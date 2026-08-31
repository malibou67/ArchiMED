"""Runner OCR Kraken (segmentation + reconnaissance → PAGE XML).

L'OCR est un **type de tâche** géré par le moteur générique `TaskService` (file, persistance,
reprise). Ce module fournit le runner `run_ocr_task` (pipeline multiprocessing) + le détail
par page (`pages_slice`/`_page_status`), et délègue la file/le cycle de vie à TaskService.
"""
import ctypes
import os
import threading
import time
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Dict, Any, Optional, Tuple

import pool_registry
from app_logging import get_logger, kv
from services import DATA_DIR, ModelsService, CollectionsService, RegistresService
from settings_service import SettingsService
from system_checks import check_requirements
from task_service import TaskService

log = get_logger('ocr')

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}

# Sous ce nombre de pages, on reste en séquentiel (le démarrage du pool ne vaut pas le coup).
POOL_MIN_PAGES = 3


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Précision mixte (autocast + TF32) par défaut (env). Mesuré sans effet sur ce modèle (goulot
# = post-traitement CPU). Surchargée par le réglage UI s'il est défini.
MIXED_PRECISION = os.getenv('OCR_MIXED_PRECISION', '0').strip().lower() in ('1', 'true', 'yes', 'on')

# Période de la trace « Charge OCR » (empreinte mémoire des workers), en secondes. 0 = muet.
STATS_INTERVAL = _env_int('OCR_STATS_INTERVAL', 60)

# ── Usure des workers sur les tâches longues ──────────────────────────────────────
# Un worker qui enchaîne des milliers de pages ne rend jamais ce qu'il a pris : l'allocateur
# CUDA de PyTorch garde ses blocs (il ne les rend au pilote que sur `empty_cache()`) et les
# fragmente au fil de scans de tailles inégales. Quand la VRAM sature, le pilote NVIDIA déborde
# en mémoire système : le GPU affiche 100 % mais le débit s'effondre et **toute la machine**
# devient lente. D'où deux garde-fous, l'un radical et l'autre continu :
#   - renouveler les workers toutes les N pages : le seul moyen sûr de tout rendre, y compris
#     la fragmentation du tas Python que `gc` ne récupère pas ;
#   - purger le cache CUDA périodiquement, pour que les workers ne s'affament pas entre deux
#     recyclages.
# Le recyclage coûte un rechargement des modèles (~1-3 s) : à 100 pages de plusieurs secondes,
# moins de 1 % du temps de la tâche. 0 désactive.
MAX_TASKS_PER_CHILD = 100
EMPTY_CACHE_EVERY = _env_int('OCR_EMPTY_CACHE_EVERY', 20)

# Pages en échec détaillées gardées dans la tâche. La liste est resérialisée à **chaque**
# écriture : sans plafond, une panne durable (partage coupé en pleine tâche) la fait grossir
# page après page et rend chaque sauvegarde plus lente que la précédente. Le compte exact reste
# dans `failed`, et le journal garde le détail de toutes les pages, pas seulement des 200
# premières.
ERRORS_KEPT = 200


# Budget VRAM par worker CUDA (Go) et marge réservée (bureau + pics de segmentation).
#
# Ce ne sont ni les poids (22 Mo) ni le contexte CUDA (20 Mo) qui coûtent, mais les
# activations de la segmentation, proportionnelles à la surface de l'image : 1,7 Go par
# worker mesuré sur des scans de 25 Mpx (RTX 3060 12 Go). Un worker de trop et le pilote
# NVIDIA déborde en mémoire système — le GPU reste à 100 % mais le débit est divisé par 10.
#
# Benchmark sur 12 pages de 25 Mpx (12 s/page en séquentiel) :
#   W=4 → 4,63 s/page (×2,8), 8,0 Go   W=6 → 4,18 s/page (×3,1), 11,3 Go
#   W=8 → effondrement : 48 s/page, VRAM saturée
# Au-delà de ~4 workers la latence par page monte (12,2 → 16,9 s) : le gain sature alors
# que le risque de saturation grandit. Le défaut vise donc ~80 % de la VRAM, pas 100 %.
# Des scans plus grands consomment davantage : baisser le budget si besoin.
GPU_VRAM_PER_WORKER_GB = 2.0
GPU_VRAM_RESERVE_GB = 1.5


# ── Réglages effectifs : UI (settings.json) > variable d'env > défaut ──────────────
def adaptive_default_workers(cores: int) -> int:
    """Laisse au moins la moitié des cœurs libres, plafonné à 4 (machine utilisable)."""
    return max(1, min(4, cores // 2))


def gpu_max_workers(vram_gb: Optional[float]) -> int:
    """Workers CUDA tenables dans la VRAM de la carte. 1 si la VRAM est inconnue (prudence).

    Sur GPU c'est la VRAM, pas le nombre de cœurs, qui borne le parallélisme : chaque worker
    a son propre contexte CUDA et sa copie des modèles. Le plafond est donc calculé **à
    l'exécution, sur chaque poste** — `settings.json` est partagé entre des machines aux GPU
    différents (cf. multi-PC dans task_service)."""
    if not vram_gb:
        return 1
    per_worker = max(0.5, _env_float('OCR_GPU_VRAM_PER_WORKER_GB', GPU_VRAM_PER_WORKER_GB))
    reserve = max(0.0, _env_float('OCR_GPU_VRAM_RESERVE_GB', GPU_VRAM_RESERVE_GB))
    return max(1, int((vram_gb - reserve) // per_worker))


def effective_workers() -> int:
    cores = os.cpu_count() or 1
    stored = SettingsService.get().get('ocr_workers')
    if stored is not None:
        try:
            return max(1, min(int(stored), cores))
        except (TypeError, ValueError):
            pass
    raw = os.getenv('OCR_WORKERS')
    if raw and raw.strip():
        try:
            return max(1, min(int(raw), cores))
        except (TypeError, ValueError):
            pass
    return adaptive_default_workers(cores)


def effective_threads() -> int:
    stored = SettingsService.get().get('ocr_threads_per_worker')
    if stored is not None:
        try:
            return max(1, int(stored))
        except (TypeError, ValueError):
            pass
    return max(1, _env_int('OCR_THREADS_PER_WORKER', 1))


def effective_mixed() -> bool:
    stored = SettingsService.get().get('ocr_mixed_precision')
    if stored is not None:
        return bool(stored)
    return MIXED_PRECISION


def effective_max_tasks_per_child() -> int:
    """Pages traitées par un worker avant recyclage. 0 = jamais (comportement d'avant)."""
    stored = SettingsService.get().get('ocr_max_tasks_per_child')
    if stored is not None:
        try:
            return max(0, int(stored))
        except (TypeError, ValueError):
            pass
    return max(0, _env_int('OCR_MAX_TASKS_PER_CHILD', MAX_TASKS_PER_CHILD))


def effective_pool_min_pages() -> int:
    stored = SettingsService.get().get('ocr_pool_min_pages')
    if stored is not None:
        try:
            return max(1, min(int(stored), 50))
        except (TypeError, ValueError):
            pass
    return max(1, min(_env_int('OCR_POOL_MIN_PAGES', POOL_MIN_PAGES), 50))


# ── Arrêt des pools ───────────────────────────────────────────────────────────────
# Le registre vit dans `pool_registry` : l'indexation y inscrit aussi ses pools, et elle ne
# peut pas importer ce module-ci (cycle via settings_service → services). Réexportés sous
# leurs anciens noms pour les appelants d'ici.
_kill_pool = pool_registry.kill_pool
kill_active_pools = pool_registry.kill_active_pools


# ── Écriture des PAGE-XML ─────────────────────────────────────────────────────────
XML_WRITE_ATTEMPTS = 6
XML_WRITE_BASE_DELAY = 0.1   # 0,1 → 1,6 s : ~3,1 s d'attente cumulée au pire


def _write_xml_atomic(out_path: Path, xml: str) -> None:
    """Écrit un PAGE-XML en deux temps (fichier temporaire + `os.replace`), avec réessais.

    L'atomicité évite qu'un worker tué en pleine écriture (annulation) laisse un XML tronqué,
    que `done_stems` compterait comme une page transcrite. Les réessais couvrent l'autre
    risque, propre aux partages réseau : antivirus, indexeur Windows ou simple lecteur tiennent
    le fichier ouvert quelques centaines de ms, et l'opération échoue alors en WinError 32
    (« utilisé par un autre processus ») — sans réessai la page passerait en échec pour rien.
    Même motif que `IndexesService._save_index_meta`, avec un backoff plus long (le partage
    réseau est plus lent que le disque local).

    Le nom du temporaire est propre au process/thread (comme `TaskService._save`) : deux
    écrivains concurrents (repli séquentiel après un pool tué, relance d'un autre poste) ne
    se disputent pas le même `.tmp`. Son suffixe `.tmp` le rend invisible pour les compteurs,
    qui ne retiennent que les fichiers `.xml`.
    """
    tmp_path = out_path.with_name(f"{out_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    delay = XML_WRITE_BASE_DELAY
    for attempt in range(XML_WRITE_ATTEMPTS):
        try:
            tmp_path.write_text(xml, encoding='utf-8')
            os.replace(tmp_path, out_path)
            return
        except OSError as e:
            # `PermissionError` (WinError 32/5) mais aussi les coupures SMB transitoires, qui
            # remontent en OSError nu : on réessaie dans les deux cas, l'attente perdue sur une
            # erreur définitive (disque plein) est négligeable devant le coût d'une page.
            if attempt == 0:
                log.warning("Écriture XML différée " + kv(fichier=out_path.name, err=e))
            if attempt == XML_WRITE_ATTEMPTS - 1:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            time.sleep(delay)
            delay *= 2


# ── Mesure de l'empreinte mémoire (diagnostic des tâches longues) ─────────────────
# Sur une tâche de plusieurs dizaines de milliers de pages, ce qui ralentit la machine n'est
# pas le débit de l'OCR mais la mémoire que les workers ne rendent jamais. Ces mesures sont
# la seule façon de voir la dérive : elles ne doivent donc jamais faire échouer une page.
class _MemoryCounters(ctypes.Structure):
    """`PROCESS_MEMORY_COUNTERS` de psapi. On passe par ctypes plutôt que d'ajouter `psutil` :
    une dépendance de plus dans le build gelé pour une ligne de journal ne se justifie pas."""
    _fields_ = [('cb', ctypes.c_ulong),
                ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t)]


def _bind_rss_probe():
    """Lie les deux appels psapi une fois pour toutes, ou rend `None` si l'OS ne les offre pas.

    Les signatures sont **obligatoires** : sans `restype`, `GetCurrentProcess()` (qui rend le
    pseudo-handle -1) repasse par un `int` 32 bits et l'appel échoue en ERROR_INVALID_HANDLE."""
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        return kernel32.GetCurrentProcess, psapi.GetProcessMemoryInfo
    except (AttributeError, OSError):
        return None


_RSS_PROBE = _bind_rss_probe()


def _process_rss_mb() -> Optional[int]:
    """Mémoire résidente du process courant, en Mo. `None` hors Windows ou en cas d'échec."""
    if _RSS_PROBE is None:
        return None
    current_process, memory_info = _RSS_PROBE
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(_MemoryCounters)
        if not memory_info(current_process(), ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize // (1024 * 1024))
    except Exception:
        return None


# ── Workers multiprocessing (fonctions au niveau module → picklables sous Windows) ──
_W_SEG = None
_W_OCR = None
_W_DEVICE = 'cpu'
_W_MIXED = False
_W_PAGES = 0        # pages traitées par CE worker (chaque process a sa copie)


def _release_worker_memory() -> None:
    """Rend périodiquement au pilote les blocs que l'allocateur CUDA garde en cache.

    PyTorch ne libère jamais ces blocs de lui-même : il les recycle. C'est efficace pour un
    process court, ruineux pour quatre workers qui se partagent une carte pendant des heures —
    chacun tient son plus haut pic pour toujours. Le recyclage des workers reste le vrai
    remède ; ceci évite juste qu'ils s'affament entre deux recyclages. `empty_cache` synchronise
    le device, d'où l'espacement plutôt qu'un appel à chaque page."""
    if _W_DEVICE != 'cuda' or EMPTY_CACHE_EVERY <= 0 or _W_PAGES % EMPTY_CACHE_EVERY:
        return
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


def _worker_stats() -> Dict[str, Any]:
    """Empreinte du worker, jointe au résultat de chaque page pour que le parent la journalise.

    `memory_reserved` est ce que PyTorch tient auprès du pilote, `memory_allocated` ce qu'il en
    utilise vraiment : c'est l'écart entre les deux, et la montée du premier, qui trahissent le
    cache CUDA qui enfle. Les deux sont des lectures de compteurs, sans synchronisation GPU."""
    stats: Dict[str, Any] = {'pid': os.getpid(), 'n': _W_PAGES, 'rss': _process_rss_mb()}
    if _W_DEVICE == 'cuda':
        try:
            import torch
            stats['vram_res'] = int(torch.cuda.memory_reserved() // (1024 * 1024))
            stats['vram_alloc'] = int(torch.cuda.memory_allocated() // (1024 * 1024))
        except Exception:
            pass
    return stats


def _ocr_worker_init(seg_model_id: str, ocr_model_id: str, device: str, threads: int, mixed: bool) -> None:
    """Initialise un process worker : bride les threads (anti-sur-souscription) puis charge
    les modèles une fois. Les variables d'env de threads doivent être posées AVANT torch."""
    import os as _os
    for _v in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        _os.environ[_v] = str(threads)
    import torch
    try:
        torch.set_num_threads(max(1, threads))
    except Exception:
        pass
    if mixed and device == 'cuda':
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    from kraken.lib import vgsl, models as kmodels
    global _W_SEG, _W_OCR, _W_DEVICE, _W_MIXED
    _W_SEG = vgsl.TorchVGSLModel.load_model(str(OcrService._resolve_model_path(seg_model_id)))
    _W_OCR = kmodels.load_any(str(OcrService._resolve_model_path(ocr_model_id)), device=device)
    _W_DEVICE = device
    _W_MIXED = mixed


def _ocr_worker_page(task):
    """Traite une page dans un worker : segmentation → reconnaissance → PAGE XML écrit.

    Retourne `(index, collection, libellé, ok, erreur, stats)` — les stats servent la trace
    « Charge OCR » côté parent et sont jointes aux deux issues, échec compris : un worker qui
    dérive échoue souvent *avant* de rendre sa page."""
    global _W_PAGES
    idx, col, reg, img_name, ocr_model_id = task
    label = f"{reg}/{img_name}"
    _W_PAGES += 1
    im = None
    try:
        from PIL import Image
        from kraken import blla, rpred, serialization
        collections_root = Path(DATA_DIR) / "collections"
        img_path = collections_root / col / "scans" / reg / img_name
        if not img_path.exists():
            raise FileNotFoundError(f"Image introuvable : {img_path}")
        out_dir = collections_root / col / "ocr" / reg / ocr_model_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (Path(img_name).stem + ".xml")
        # `convert` détache une nouvelle image : sans le `with`, le handle du fichier source
        # ne se referme qu'au passage du compteur de références — sur un partage SMB c'est un
        # handle réseau tenu ouvert pour rien, des dizaines de milliers de fois.
        with Image.open(img_path) as src:
            im = src.convert('RGB')
        bounds = blla.segment(im, model=_W_SEG, device=_W_DEVICE, autocast=_W_MIXED)
        preds = list(rpred.rpred(_W_OCR, im, bounds))
        results = dataclasses.replace(bounds, lines=preds, imagename=str(img_path))
        xml = serialization.serialize(results=results, image_size=im.size, template='pagexml')
        _write_xml_atomic(out_path, xml)
        return (idx, col, label, True, None, _worker_stats())
    except Exception as e:
        return (idx, col, label, False, str(e), _worker_stats())
    finally:
        if im is not None:
            im.close()
        _release_worker_memory()


def _log_worker_load(task, worker_stats: Dict[int, Dict[str, Any]], in_flight: int,
                     last_log: float) -> float:
    """Trace périodique de l'empreinte des workers. Retourne la date de la dernière écriture.

    C'est la courbe qui dit si un worker dérive : VRAM réservée qui monte sans jamais redescendre
    → le pilote NVIDIA déborde en mémoire système et c'est toute la machine qui ralentit, pas
    seulement l'OCR. Une ligne par minute au plus : le journal tourne à 5 Mo, on ne le remplit
    pas avec de la métrique."""
    if STATS_INTERVAL <= 0:
        return last_log
    now = time.time()
    if now - last_log < STATS_INTERVAL:
        return last_log
    detail = ' | '.join(
        f"pid={st['pid']} n={st['n']}"
        + (f" rss={st['rss']}Mo" if st.get('rss') is not None else '')
        + (f" vram={st['vram_res']}/{st['vram_alloc']}Mo" if 'vram_res' in st else '')
        for st in sorted(worker_stats.values(), key=lambda st: st['pid']))
    log.info("Charge OCR " + kv(
        id=task['id'],
        restantes=task['total'] - task['processed'] - task['failed'],
        en_vol=in_flight, workers=detail))
    return now


def _record_error(task: Dict[str, Any], label: str, err: Any) -> None:
    """Note une page en échec dans la tâche, sans laisser la liste enfler indéfiniment."""
    errors = task['errors']
    if len(errors) < ERRORS_KEPT:
        errors.append({'page': label, 'error': str(err) if err is not None else None})


class NoPagesToProcess(Exception):
    """Aucune page enfilable : toutes les images demandées sont absentes du disque."""


class OcrService:
    # Modèles chargés une seule fois et gardés chauds (réutilisés entre les jobs).
    _model_cache: Dict[str, Any] = {}
    _model_lock = threading.Lock()

    # ── Enfilage (délégué au moteur générique) ────────────────────────
    @staticmethod
    def enqueue(seg_model_id: str, ocr_model_id: str, pages: List[Dict[str, str]],
                retry_of: Optional[str] = None) -> Dict[str, Any]:
        """Ajoute une tâche OCR à la lane 'ocr', après avoir écarté les pages sans image.

        La liste est construite côté client à partir de la pagination détectée : elle peut
        contenir des pages qui n'existent pas sur le disque (trou de pagination, fichier
        déplacé depuis la dernière synchronisation). Les enfiler ne produirait que des échecs,
        on les retire ici — c'est le seul endroit qui voie la vérité du disque."""
        kept, skipped = OcrService.filter_existing_pages(pages)
        if not kept:
            raise NoPagesToProcess(
                f"Aucune page à traiter : les {len(pages)} image(s) demandée(s) sont "
                "introuvables sur le disque. Resynchronisez la collection."
            )
        if skipped:
            log.warning("Pages écartées, image introuvable " + kv(
                nombre=len(skipped), exemple=f"{skipped[0]['registre']}/{skipped[0]['page']}"))
        # Collections/registres dérivés des pages **retenues** : un registre entièrement
        # fantôme ne doit rien verrouiller ni apparaître dans le récapitulatif.
        collections = sorted({p['collection'] for p in kept})
        registres = sorted({p['registre'] for p in kept})
        # Les couples réellement couverts — unité de verrou. Les deux listes ci-dessus, aplaties
        # séparément, perdent l'appariement : les recroiser verrouillerait des registres qu'aucune
        # page ne concerne (sélection multi-collections). On les garde pour le label et le repli.
        scopes = sorted({(p['collection'], p['registre']) for p in kept})
        label = ', '.join(collections) if collections else 'OCR'
        fields: Dict[str, Any] = {
            'total': len(kept),
            'seg_model': seg_model_id,
            'ocr_model': ocr_model_id,
            'collections': collections,
            'registres': registres,
            'scopes': [[c, r] for c, r in scopes],
            'pages': kept,
        }
        if skipped:
            # Le compte seul : le JSON de tâche est relu en boucle par les autres postes.
            fields['skipped_missing'] = len(skipped)
        if retry_of:
            fields['retry_of'] = retry_of
        return TaskService.enqueue('ocr', label, fields)

    # ── Détail par page (utilisé par /api/tasks/{id}/pages) ───────────
    @staticmethod
    def _page_codes(task: Dict[str, Any]) -> List[str]:
        """Statut de chaque page, par position, sans construire un objet par page.

        En parallèle les pages se terminent dans le désordre, donc on s'appuie sur `page_states`
        (aligné sur `pages`) : 0=à faire, 1=faite, 2=échec, 3=en cours. Repli sur une déduction
        par ordre si `page_states` est absent (tâches antérieures à ce champ)."""
        pages = task.get('pages') or []
        states = task.get('page_states')
        if isinstance(states, list) and len(states) == len(pages):
            names = {0: 'pending', 1: 'done', 2: 'failed', 3: 'current'}
            return [names.get(st, 'pending') for st in states]

        failed_labels = {e.get('page') for e in (task.get('errors') or [])}
        attempted = task.get('processed', 0) + task.get('failed', 0)
        running = task.get('status') == 'running'
        current = task.get('current')
        codes: List[str] = []
        for i, p in enumerate(pages):
            label = f"{p['registre']}/{p['page']}"
            if i < attempted:
                codes.append('failed' if label in failed_labels else 'done')
            elif running and (label == current or i == attempted):
                codes.append('current')
            else:
                codes.append('pending')
        return codes

    @staticmethod
    def _page_item(pages: List[Dict[str, str]], codes: List[str],
                   error_by_label: Dict[str, Any], i: int) -> Dict[str, Any]:
        """Ligne d'affichage d'une page."""
        label = f"{pages[i]['registre']}/{pages[i]['page']}"
        item: Dict[str, Any] = {'page': label, 'status': codes[i]}
        if codes[i] == 'failed':
            item['error'] = error_by_label.get(label)
        return item

    @staticmethod
    def _page_status(task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """État de chaque page, en entier. Réservé aux usages qui ont besoin de tout
        (`failed_pages`) : pour l'affichage paginé, passer par `pages_slice`."""
        pages = task.get('pages') or []
        codes = OcrService._page_codes(task)
        error_by_label = {e.get('page'): e.get('error') for e in (task.get('errors') or [])}
        return [OcrService._page_item(pages, codes, error_by_label, i) for i in range(len(pages))]

    # Statuts retenus par chaque filtre de l'API (`all` = pas de filtre).
    _FILTERS = {'done': ('done',), 'failed': ('failed',), 'todo': ('pending', 'current')}

    @staticmethod
    def pages_slice(task: Dict[str, Any], status_filter: str = 'all', offset: int = 0, limit: int = 100) -> Dict[str, Any]:
        """Tranche paginée de l'état des pages d'une tâche OCR.

        Le panneau est resondé toutes les 2 secondes tant qu'il est déplié, dans le process qui
        fait tourner l'OCR : on ne construit donc que les cent lignes demandées. Les comptes se
        lisent directement sur les codes, et le filtre travaille sur des index."""
        pages = task.get('pages') or []
        codes = OcrService._page_codes(task)
        counts = {
            'all': len(codes),
            'done': codes.count('done'),
            'failed': codes.count('failed'),
            'todo': codes.count('pending') + codes.count('current'),
        }
        wanted = OcrService._FILTERS.get(status_filter)
        indices: Any = range(len(codes)) if wanted is None else [
            i for i, code in enumerate(codes) if code in wanted]
        offset = max(0, offset)
        limit = max(1, min(limit, 500))
        error_by_label = {e.get('page'): e.get('error') for e in (task.get('errors') or [])}
        return {
            'total': len(indices),
            'offset': offset,
            'limit': limit,
            'counts': counts,
            'items': [OcrService._page_item(pages, codes, error_by_label, i)
                      for i in indices[offset:offset + limit]],
        }

    # ── Relance des pages en échec ────────────────────────────────────
    @staticmethod
    def failed_pages(task: Dict[str, Any]) -> List[Dict[str, str]]:
        """Pages en échec d'une tâche OCR.

        On indexe `task['pages']` **par position** via `_page_status` — jamais par le libellé
        « registre/page », qui perd la collection et n'est donc pas réversible. Passer par
        `_page_status` couvre au passage les tâches anciennes sans `page_states` (repli)."""
        pages = task.get('pages') or []
        return [pages[i] for i, s in enumerate(OcrService._page_status(task))
                if s['status'] == 'failed']

    @staticmethod
    def retry_failed(task: Dict[str, Any]) -> Dict[str, Any]:
        """Réenfile une **nouvelle** tâche OCR limitée aux pages en échec, mêmes modèles.

        La tâche d'origine n'est pas touchée : elle peut appartenir à un autre poste (on
        n'écrit jamais dans le fichier d'un autre poste), et son historique d'échecs reste
        consultable. Les pages dont l'image a disparu entre-temps sont écartées par
        `enqueue`, qui lève `NoPagesToProcess` s'il ne reste rien."""
        failed = OcrService.failed_pages(task)
        if not failed:
            raise NoPagesToProcess("Aucune page en échec à relancer.")
        # Un modèle supprimé depuis la tâche d'origine donne un refus immédiat, plutôt qu'une
        # tâche qui part pour échouer au préflight.
        for kind, model_id in (('de segmentation', task.get('seg_model')), ('OCR', task.get('ocr_model'))):
            if not model_id or OcrService._resolve_model_path(model_id) is None:
                raise NoPagesToProcess(
                    f"Le modèle {kind} « {model_id} » de cette tâche est introuvable : "
                    "relancez depuis la page OCR avec un modèle disponible."
                )
        return OcrService.enqueue(task['seg_model'], task['ocr_model'], failed,
                                  retry_of=task.get('id'))

    # ── État de transcription (pages faites / manquantes) ─────────────
    @staticmethod
    def done_stems(collection: str, registre: str, model_id: str) -> List[str]:
        """Stems (nom sans extension) des PAGE XML déjà produits pour (collection, registre, modèle).
        Même règle que l'écriture (Path(img).stem) : deux images qui ne diffèrent que par
        l'extension partagent donc le même stem."""
        d = Path(DATA_DIR) / "collections" / collection / "ocr" / registre / model_id
        if not d.is_dir():
            return []
        return sorted(f.stem for f in d.iterdir() if f.suffix == '.xml')

    @staticmethod
    def filter_existing_pages(pages: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Sépare les pages demandées en (retenues, écartées) selon l'existence de l'image.

        Un seul listing par (collection, registre) : sur un partage réseau, lister un dossier
        une fois coûte bien moins cher qu'un `stat` par page — sauf pour une poignée de pages,
        où c'est l'inverse (cas « je relance deux pages »). Sur le chemin par listing, le nom
        retenu est celui du disque : Windows ouvrirait `Page_1.jpg` demandé en `page_1.jpg`,
        mais le XML produit prendrait le stem demandé et fausserait les compteurs."""
        by_registre: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        for p in pages:
            by_registre.setdefault((p['collection'], p['registre']), []).append(p)

        kept: List[Dict[str, str]] = []
        skipped: List[Dict[str, str]] = []
        scans_root = Path(DATA_DIR) / "collections"
        for (col, reg), group in by_registre.items():
            if len(group) <= 4:
                reg_dir = scans_root / col / "scans" / reg
                for p in group:
                    (kept if (reg_dir / p['page']).is_file() else skipped).append(p)
                continue
            by_lower = {n.lower(): n for n in RegistresService.list_scan_pages(col, reg)}
            for p in group:
                real = by_lower.get(p['page'].lower())
                if real is None:
                    skipped.append(p)
                else:
                    kept.append(p if real == p['page'] else {**p, 'page': real})
        return kept, skipped

    @staticmethod
    def missing_pages(model_id: str, scope: Optional[List[Dict[str, Optional[str]]]] = None) -> List[Dict[str, str]]:
        """Pages (fichiers image des scans) sans transcription pour ce modèle.
        `scope` : liste optionnelle de {'collection': ..., 'registre': optionnel} ;
        None ou vide = toutes les collections."""
        collections_root = Path(DATA_DIR) / "collections"
        if not collections_root.exists():
            return []

        # collection -> set de registres demandés (None = tous les registres)
        wanted: Optional[Dict[str, Optional[set]]] = None
        if scope:
            wanted = {}
            for item in scope:
                col = item.get('collection')
                if not col:
                    continue
                reg = item.get('registre')
                if not reg:
                    wanted[col] = None  # toute la collection
                elif col not in wanted:
                    wanted[col] = {reg}
                elif wanted[col] is not None:
                    wanted[col].add(reg)

        result: List[Dict[str, str]] = []
        for col_dir in sorted(collections_root.iterdir()):
            if not col_dir.is_dir() or CollectionsService._SCAN_EXCLUDE.match(col_dir.name):
                continue
            if wanted is not None and col_dir.name not in wanted:
                continue
            scans_dir = col_dir / "scans"
            if not scans_dir.exists():
                continue
            regs_filter = wanted.get(col_dir.name) if wanted is not None else None
            for reg_dir in sorted(scans_dir.iterdir()):
                if not reg_dir.is_dir():
                    continue
                if regs_filter is not None and reg_dir.name not in regs_filter:
                    continue
                done = set(OcrService.done_stems(col_dir.name, reg_dir.name, model_id))
                for img_name in RegistresService.list_scan_pages(col_dir.name, reg_dir.name):
                    if Path(img_name).stem not in done:
                        result.append({'collection': col_dir.name, 'registre': reg_dir.name, 'page': img_name})
        return result

    # ── Résolution / cache des modèles ────────────────────────────────
    @staticmethod
    def _resolve_model_path(model_id: str) -> Optional[Path]:
        """Résout le fichier .mlmodel d'un modèle (dossier des modèles, repli file_path)."""
        models_dir = ModelsService.get_models_dir()
        candidate = models_dir / f"{model_id}.mlmodel"
        if candidate.exists():
            return candidate
        model = ModelsService.get_model(model_id)
        if model and model.get('file_path'):
            p = Path(model['file_path'])
            if not p.is_absolute():
                p = Path(DATA_DIR).parent / p
            if p.exists():
                return p
        return None

    @staticmethod
    def _get_seg_net(model_id: str):
        key = f"seg:{model_id}"
        with OcrService._model_lock:
            if key not in OcrService._model_cache:
                path = OcrService._resolve_model_path(model_id)
                if path is None:
                    raise RuntimeError(f"Fichier du modèle de segmentation introuvable : {model_id}")
                from kraken.lib import vgsl
                OcrService._model_cache[key] = vgsl.TorchVGSLModel.load_model(str(path))
            return OcrService._model_cache[key]

    @staticmethod
    def _get_ocr_net(model_id: str, device: str):
        key = f"ocr:{model_id}:{device}"
        with OcrService._model_lock:
            if key not in OcrService._model_cache:
                path = OcrService._resolve_model_path(model_id)
                if path is None:
                    raise RuntimeError(f"Fichier du modèle OCR introuvable : {model_id}")
                from kraken.lib import models as kmodels
                OcrService._model_cache[key] = kmodels.load_any(str(path), device=device)
            return OcrService._model_cache[key]

    # ── Traitement ────────────────────────────────────────────────────
    @staticmethod
    def _stop_requested(task) -> bool:
        """Arrêt coopératif demandé ? Positionne le statut final et retourne True.
        L'annulation prime sur la pause (elle est définitive)."""
        if task['cancel']:
            task['status'] = 'cancelled'
            return True
        if task.get('pause'):
            task['status'] = 'paused'   # page_states sert de checkpoint pour la reprise
            return True
        return False

    @staticmethod
    def _process_inline(task, touched, todo, seg_model_id, ocr_model_id, device, mixed,
                        on_page_done) -> None:
        """Traitement séquentiel (W=1 ou petit job) : modèles gardés chauds en cache.
        `todo` est une liste de couples (index dans task['pages'], page).
        `touched` collecte les couples (collection, registre) réellement transcrits."""
        import torch
        from PIL import Image
        from kraken import blla, rpred, serialization

        use_amp = bool(mixed)
        if use_amp:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

        seg_net = OcrService._get_seg_net(seg_model_id)
        ocr_net = OcrService._get_ocr_net(ocr_model_id, device)
        states = task['page_states']
        collections_root = Path(DATA_DIR) / "collections"

        for i, page in todo:
            if OcrService._stop_requested(task):
                break   # states[i] vaut encore 0 : la page reste à faire pour la reprise
            col = page['collection']; reg = page['registre']; img_name = page['page']
            label = f"{reg}/{img_name}"
            task['current'] = label
            states[i] = 3  # en cours
            try:
                img_path = collections_root / col / "scans" / reg / img_name
                if not img_path.exists():
                    raise FileNotFoundError(f"Image introuvable : {img_path}")
                out_dir = collections_root / col / "ocr" / reg / ocr_model_id
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / (Path(img_name).stem + ".xml")

                im = Image.open(img_path).convert('RGB')
                bounds = blla.segment(im, model=seg_net, device=device, autocast=use_amp)
                preds = list(rpred.rpred(ocr_net, im, bounds))
                results = dataclasses.replace(bounds, lines=preds, imagename=str(img_path))
                xml = serialization.serialize(results=results, image_size=im.size, template='pagexml')
                _write_xml_atomic(out_path, xml)

                states[i] = 1
                touched.add((col, reg))
                task['processed'] += 1
            except Exception as e:
                states[i] = 2
                task['failed'] += 1
                _record_error(task, label, e)
                # `errors` est plafonné et disparaît avec la tâche purgée : le journal est la
                # seule trace durable des pages en échec.
                log.warning("Page en échec " + kv(registre=reg, page=img_name, err=e))
            on_page_done(col, reg)
            TaskService._save_throttled(task)

    @staticmethod
    def _process_pool(task, touched, todo, seg_model_id, ocr_model_id, device, workers,
                      threads, mixed, max_tasks_per_child, on_page_done) -> None:
        """Traitement parallèle : un pool de process traite les pages en concurrence
        (le post-traitement CPU, goulot réel, s'étale sur les cœurs). Les pages se terminent
        dans le désordre → on note l'état par index dans page_states.

        Le pool est **renouvelé** tous les `max_tasks_per_child` pages par worker : c'est ce qui
        empêche une tâche de plusieurs dizaines de milliers de pages de ralentir la machine
        entière (voir `MAX_TASKS_PER_CHILD`). Voir `_run_pool_batch` pour le pourquoi de ce
        découpage plutôt que du paramètre standard."""
        states = task['page_states']
        by_index = {i: p for i, p in todo}
        tasks = [(i, p['collection'], p['registre'], p['page'], ocr_model_id) for i, p in todo]
        # `last_log` est partagé entre les lots (la trace garde sa cadence d'une ligne par
        # minute), mais les mesures elles-mêmes sont remises à zéro à chaque pool : les pids du
        # lot précédent n'existent plus, et les cumuler ferait grossir la ligne sans fin.
        progress: Dict[str, Any] = {'stats': {}, 'last_log': time.time()}

        per_pool = max(1, max_tasks_per_child * workers) if max_tasks_per_child > 0 else len(tasks)
        for start in range(0, len(tasks), per_pool):
            if OcrService._stop_requested(task):
                break
            OcrService._run_pool_batch(
                task, touched, states, by_index, tasks[start:start + per_pool],
                seg_model_id, ocr_model_id, device, workers, threads, mixed,
                on_page_done, progress)

    @staticmethod
    def _run_pool_batch(task, touched, states, by_index, batch, seg_model_id, ocr_model_id,
                        device, workers, threads, mixed, on_page_done, progress) -> None:
        """Traite un lot dans un pool neuf, puis rend les process — donc leur VRAM et leur tas.

        On renouvelle le pool nous-mêmes au lieu de passer `max_tasks_per_child` à
        `ProcessPoolExecutor` : ce paramètre **bloque le pool**. Quand un worker atteint son
        quota et sort, `_adjust_process_count()` commence par consommer un jeton du sémaphore
        des workers libres — jeton posté par chaque page déjà rendue — et repart sans avoir
        engendré le remplaçant. Après quelques pages, le pool n'a plus un seul process et
        l'attente est éternelle. Reproduit ici sur 8 pages : les deux workers sortent à leur
        quota, `restantes=4 en_vol=4`, plus rien ne bouge.

        Le prix de ce découpage est une barrière par lot (les traînards finissent pendant que
        les autres attendent) plus un rechargement des modèles : quelques secondes par lot de
        plusieurs centaines de pages."""
        from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait

        progress['stats'] = {}   # workers du lot précédent : ces process n'existent plus
        executor = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_ocr_worker_init,
            initargs=(seg_model_id, ocr_model_id, device, threads, bool(mixed)),
        )
        pool_registry.add(executor)
        # Soumission par fenêtre glissante plutôt qu'en bloc : `wait()` est O(n) sur les futures
        # en vol (il les trie et prend le verrou de chacune) et on l'appelle chaque seconde —
        # avec 50 000 pages soumises d'emblée, c'est du CPU brûlé à ne rien faire, et 50 000
        # `_WorkItem` retenus dans l'exécuteur. Quelques pages d'avance par worker suffisent à ce
        # qu'aucun ne se retrouve à sec. La reprise, elle, reste pilotée par `page_states`.
        remaining = iter(batch)
        window = max(8, workers * 4)

        def _fill(in_flight: set) -> None:
            """Complète la fenêtre. `wait` rebinde l'ensemble à chaque tour, d'où le paramètre."""
            while len(in_flight) < window:
                try:
                    in_flight.add(executor.submit(_ocr_worker_page, next(remaining)))
                except StopIteration:
                    return

        try:
            pending: set = set()
            _fill(pending)
            # Attente par tranches d'une seconde plutôt que `as_completed` : sinon la demande
            # d'arrêt n'est vue qu'au retour d'une page, soit une minute ou plus par gros scan.
            while pending:
                if OcrService._stop_requested(task):
                    break
                done, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in done:
                    idx, col, label, ok, err, stats = fut.result()
                    progress['stats'][stats['pid']] = stats
                    if ok:
                        states[idx] = 1
                        touched.add((col, by_index[idx]['registre']))
                        task['processed'] += 1
                    else:
                        states[idx] = 2
                        task['failed'] += 1
                        _record_error(task, label, err)
                        log.warning("Page en échec " + kv(
                            registre=by_index[idx]['registre'], page=by_index[idx]['page'], err=err))
                    task['current'] = label
                    on_page_done(col, by_index[idx]['registre'])
                if done:
                    _fill(pending)
                    TaskService._save_throttled(task)
                    progress['last_log'] = _log_worker_load(
                        task, progress['stats'], len(pending), progress['last_log'])
        finally:
            # `shutdown` annule les pages en file mais laisse les workers finir celle qu'ils
            # ont commencée : sur un arrêt demandé on tue, sinon « Annuler » laisse le GPU
            # occupé pendant des minutes (et la tâche suivante démarre par-dessus).
            stopping = OcrService._stop_requested(task)
            if stopping:
                killed = _kill_pool(executor)
                if killed:
                    log.info("Arrêt demandé " + kv(workers_interrompus=killed))
            pool_registry.discard(executor)
            try:
                # Lot terminé normalement : on **attend** la sortie des workers, sinon le pool
                # du lot suivant démarrerait pendant que l'ancien tient encore sa VRAM — deux
                # jeux de workers à la fois, exactement la saturation qu'on cherche à éviter.
                executor.shutdown(wait=not stopping, cancel_futures=True)
            except Exception:
                pass  # pool déjà tué : rien à attendre


def _plan_todo(task: Dict[str, Any], pages: List[Dict[str, str]]) -> List:
    """Prépare une exécution (première ou reprise après pause/interruption) et retourne les pages
    restant à traiter, sous forme de couples (index dans `pages`, page).

    `page_states` (0=à faire, 1=faite, 2=échec, 3=en cours) est persisté avec la tâche : il tient
    lieu de checkpoint, les PAGE-XML déjà produits n'ont pas à être refaits. Les pages restées
    « en cours » (process tué au milieu) repassent à faire, et les compteurs sont **recalculés**
    depuis les états plutôt qu'accumulés — sinon une reprise double-compterait."""
    states = task.get('page_states')
    if not isinstance(states, list) or len(states) != len(pages):
        states = [0] * len(pages)
    else:
        states = [0 if s == 3 else s for s in states]
    task['page_states'] = states
    task['processed'] = sum(1 for s in states if s == 1)
    task['failed'] = sum(1 for s in states if s == 2)
    return [(i, p) for i, p in enumerate(pages) if states[i] == 0]


def _registre_reporter(task: Dict[str, Any], todo: List) -> Callable[[str, str], None]:
    """Callback `on_page_done(collection, registre)` qui publie l'état d'un registre dès que
    **toutes** ses pages de cette exécution ont été tentées, au lieu d'attendre la fin de la tâche.

    Rafraîchit son `ocr_status` dans le metadata.json de la collection (mise à jour ciblée, pas la
    resynchronisation complète) et l'ajoute à `registres_done`, que le frontend surveille pour
    recharger les compteurs."""
    remaining: Dict[tuple, int] = {}
    for _, p in todo:
        key = (p['collection'], p['registre'])
        remaining[key] = remaining.get(key, 0) + 1
    planned = dict(remaining)
    started = time.time()
    done: List[str] = task.setdefault('registres_done', [])

    def on_page_done(collection: str, registre: str) -> None:
        key = (collection, registre)
        left = remaining.get(key)
        if left is None:
            return
        left -= 1
        remaining[key] = left
        if left > 0:
            return
        remaining.pop(key, None)
        try:
            CollectionsService.refresh_registre_ocr_status(collection, registre)
        except Exception as e:
            log.warning("Publication de l'ocr_status en échec " + kv(
                collection=collection, registre=registre, err=e))
        label = f"{collection}/{registre}"
        if label not in done:
            done.append(label)
        # Un résumé par registre, jamais par page : un gros registre produirait des milliers
        # de lignes et ferait tourner la rotation avant même la fin de la tâche. La durée est
        # celle écoulée depuis le début de l'exécution — en mode pool les pages de plusieurs
        # registres s'entrelacent, une durée « par registre » n'aurait pas de sens. L'écart
        # entre deux lignes donne la cadence.
        log.info("Registre terminé " + kv(
            collection=collection, registre=registre, pages=planned[key],
            écoulé=f"{int(time.time() - started)}s"))

    return on_page_done


def run_ocr_task(task: Dict[str, Any]) -> None:
    """Runner OCR appelé par TaskService. Met à jour la tâche en place ; ne fixe pas le statut
    final 'done'/'error' (géré par le moteur) mais peut passer 'cancelled' ou 'paused'."""
    pages = task.get('pages') or []
    seg_model_id = task['seg_model']
    ocr_model_id = task['ocr_model']
    touched: set = set()   # couples (collection, registre) réellement transcrits
    try:
        todo = _plan_todo(task, pages)
        on_page_done = _registre_reporter(task, todo)

        # Préflight : on vérifie l'environnement au moment de l'exécution (la vérif faite
        # côté page peut être périmée) et on journalise le résultat dans la tâche.
        pre = check_requirements()
        device = 'cuda' if (pre.get('cuda') or {}).get('ok') else 'cpu'
        requested = effective_workers()
        workers = requested
        cap_reason = None
        if device == 'cuda':
            # Le réglage est partagé entre postes : c'est ici, sur la machine qui exécute,
            # qu'on le ramène à ce que sa carte peut tenir.
            cap = gpu_max_workers((pre.get('cuda') or {}).get('vram_gb'))
            if requested > cap:
                workers = cap
                cap_reason = 'gpu_vram'
        task['preflight'] = {
            **pre,
            'device': device,
            'workers': workers,
            'threads': effective_threads(),
            'mixed_precision': effective_mixed() and device == 'cuda',
            'max_tasks_per_child': effective_max_tasks_per_child(),
            'checked_at': datetime.now().isoformat(),
        }
        if cap_reason:
            task['preflight']['workers_requested'] = requested
            task['preflight']['workers_cap_reason'] = cap_reason
        TaskService._save(task)
        # Photo de l'environnement d'exécution : elle ne vit sinon que dans la tâche, donc
        # disparaît à la purge, alors que c'est la première chose qu'on veut relire quand un
        # poste transcrit dix fois plus lentement qu'un autre.
        log.info("Préflight " + kv(
            id=task['id'], device=device, vram=(pre.get('cuda') or {}).get('vram_gb'),
            workers=workers, workers_demandés=requested if cap_reason else None,
            bridage=cap_reason, threads=task['preflight']['threads'],
            précision_mixte=task['preflight']['mixed_precision'],
            recyclage=task['preflight']['max_tasks_per_child'] or 'aucun',
            kraken=(pre.get('kraken') or {}).get('version'),
            torch=(pre.get('torch') or {}).get('version')))

        missing = [label for key, label in
                   (('torch', 'PyTorch'), ('torchvision', 'torchvision'), ('kraken', 'Kraken'))
                   if not (pre.get(key) or {}).get('ok')]
        if missing:
            details = '; '.join(
                e for e in ((pre.get(k) or {}).get('error') for k in ('torch', 'torchvision', 'kraken')) if e
            )
            raise RuntimeError(
                f"Environnement OCR incomplet : {', '.join(missing)} indisponible(s). "
                + (f"Détail : {details}" if details
                   else "Vérifiez l'installation Python (pip install torch torchvision kraken).")
            )

        if OcrService._resolve_model_path(seg_model_id) is None:
            raise RuntimeError(f"Fichier du modèle de segmentation introuvable : {seg_model_id}")
        if OcrService._resolve_model_path(ocr_model_id) is None:
            raise RuntimeError(f"Fichier du modèle OCR introuvable : {ocr_model_id}")

        workers = task['preflight']['workers']
        threads = task['preflight']['threads']
        mixed = task['preflight']['mixed_precision']
        max_tasks_per_child = task['preflight']['max_tasks_per_child']

        if workers <= 1 or len(todo) < effective_pool_min_pages():
            task['preflight']['mode'] = 'sequential'
            OcrService._process_inline(task, touched, todo, seg_model_id, ocr_model_id,
                                       device, mixed, on_page_done)
        else:
            task['preflight']['mode'] = 'parallel'
            try:
                OcrService._process_pool(task, touched, todo, seg_model_id, ocr_model_id,
                                         device, workers, threads, mixed,
                                         max_tasks_per_child, on_page_done)
            except Exception as pool_err:
                if task['cancel']:
                    raise
                # Repli séquentiel : on repart de l'état réellement atteint par le pool (pages
                # déjà écrites conservées), pas de zéro — sinon une reprise referait tout.
                task['status'] = 'running'
                todo = _plan_todo(task, pages)
                on_page_done = _registre_reporter(task, todo)
                # Tracé dans le préflight : la dégradation est visible dans l'UI, sinon la
                # tâche affiche W>1 alors qu'elle tourne sur un seul cœur.
                task['preflight']['mode'] = 'sequential'
                task['preflight']['pool_fallback'] = str(pool_err)[:300]
                TaskService._save(task)
                log.warning("Pool indisponible, repli séquentiel " + kv(id=task['id'], err=pool_err))
                OcrService._process_inline(task, touched, todo, seg_model_id, ocr_model_id,
                                           device, mixed, on_page_done)
    finally:
        # Surtout pas de `sync_collection_metadata` ici : il relit tous les registres de la
        # collection sur le NAS (≈16 min pour 383 registres) alors qu'un OCR ne change que
        # les XML — la tâche resterait « en cours » tout ce temps, dernière page écrite.
        # Le reporter a déjà publié l'ocr_status de chaque registre terminé ; il ne reste
        # que ceux laissés à moitié par une annulation, une pause ou une erreur.
        deja_publies = set(task.get('registres_done') or [])
        for col, reg in touched:
            if f"{col}/{reg}" in deja_publies:
                continue
            try:
                CollectionsService.refresh_registre_ocr_status(col, reg)
            except Exception:
                pass


TaskService.register('ocr', run_ocr_task)
