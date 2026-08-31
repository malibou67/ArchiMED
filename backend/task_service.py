"""Moteur de tâches générique (OCR, indexation, …).

Toute tâche, quel que soit son `type`, est gérée de la même façon : file d'attente,
exécution, progression, persistance (`data/tasks/<id>.json`), reprise au démarrage.
Chaque type a sa propre **lane** (file séquentielle indépendante) → un OCR et une
indexation peuvent tourner en parallèle, mais deux OCR s'enchaînent. Le travail spécifique
à un type est délégué à un **runner** enregistré via `register(type, runner)`.

**Multi-PC (NAS partagé).** Plusieurs postes lancent le même exécutable depuis le NAS et
partagent le même dossier `data/`. Chaque tâche est estampillée avec un `machine_id`
(cf. `machine_identity`) :
  - **appartenance** : un poste ne reprend / ne contrôle que ses propres tâches en cours ;
  - **vue unifiée** : `list_tasks`/`summary` relisent le disque pour voir les tâches des
    autres postes (`_merged_tasks`) ;
  - **conflits** : deux postes ne peuvent pas produire la même sortie (OCR sur le même
    (collection, registre, modèle) ou index sur le même (collection, modèle)) → blocage à
    la création (`find_conflict`) + verrou atomique à l'exécution, auto-réparant via un
    `heartbeat` (un poste coupé ne bloque pas durablement les autres) ;
  - **contrôle à distance** : un poste ne peut pas écrire dans le fichier d'une tâche `running`
    d'un autre poste (le propriétaire l'écrase à son heartbeat suivant). Il dépose donc une
    commande dans `data/tasks/control/<id>.json`, que le propriétaire applique (cf. `_supervisor`).

Un **thread superviseur** unique par process (`_start_supervisor`) rafraîchit le heartbeat des
tâches en cours de ce poste — indépendamment de la granularité du runner, qui peut mettre plusieurs
minutes entre deux pages — et draine la boîte aux lettres de commandes.
"""
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple

from services import DATA_DIR
import machine_identity
from app_logging import get_logger, kv, log_throttled

log = get_logger('task')

TERMINAL = {'done', 'error', 'cancelled', 'interrupted'}
HISTORY_CAP = 100
# Au-delà de ce délai sans heartbeat, une tâche `running` est considérée morte (process
# tué / NAS coupé) : son verrou devient récupérable par un autre poste.
HEARTBEAT_STALE = 60.0
# Période de réveil du superviseur (heartbeat + commandes distantes). Très en deçà de
# HEARTBEAT_STALE pour garder une marge confortable sur un NAS lent.
SUPERVISOR_INTERVAL = 5.0
# Le balayage de rattrapage de la file tourne à chaque cycle du superviseur, mais il ne
# consulte le partage qu'au plus toutes les RECONCILE_DISK_INTERVAL secondes : la passe en
# mémoire est gratuite, énumérer `data/tasks/` coûte un aller-retour par fichier.
RECONCILE_DISK_INTERVAL = 30.0

# Commandes acceptées dans la boîte aux lettres inter-postes.
CONTROL_ACTIONS = ('cancel', 'pause', 'resume')

# Résultats de `cancel`/`pause`/`resume` : appliqué ici, transmis au poste propriétaire,
# ou impossible dans l'état courant.
APPLIED = 'applied'
REQUESTED = 'requested'
REFUSED = 'refused'

# Clés volatiles / lourdes exclues des réponses API.
#
# `heartbeat` n'y est PAS : c'est la seule date qui dit quand le poste propriétaire a écrit son
# avancement sur le partage. Le front en a besoin pour dater le dernier relevé d'une tâche
# distante — sans elle, une tâche qui tourne ailleurs paraît figée entre deux battements.
# `last_write` reste caché : c'est le `time.time()` brut du throttle, sans sens hors de ce process.
_HIDDEN = ('pages', 'page_states', 'index_registres', 'payload', 'cancel', 'last_write')

# Clés écrites **à part**, une fois pour toutes, dans `<id>.pages.json`.
#
# `pages` est fixé à l'enfilage et ne bouge plus, mais il pesait dans chaque écriture de la
# tâche — or `_save_throttled` écrit jusqu'à une fois par seconde pendant toute l'exécution.
# Sur une tâche de 50 000 pages c'était ~5 Mo de JSON poussés sur le partage chaque seconde,
# des heures durant, pour ne rafraîchir que trois compteurs. Le fichier chaud ne garde donc
# que l'état qui change ; `page_states` (des entiers, deux ordres de grandeur plus léger) y
# reste car c'est le point de reprise et il doit rester atomique avec les compteurs.
_SIDECAR = ('pages',)


class TaskUnavailable(Exception):
    """Levée par `enqueue` quand la tâche n'a pas pu être écrite sur le partage.

    Sans elle, l'interface annonçait une tâche créée qui n'existait que dans la mémoire de ce
    poste : invisible des autres postes, perdue au premier redémarrage, et silencieuse dans le
    journal (l'échec d'écriture y est étranglé à une entrée par minute). Elle réservait au
    passage son scope partout, au bénéfice d'une tâche fantôme."""


class TaskConflict(Exception):
    """Levée par `enqueue` quand le scope demandé est déjà occupé par un autre poste."""
    def __init__(self, scope: str, machine_label: str):
        self.scope = scope
        self.machine_label = machine_label
        super().__init__(f"« {scope} » déjà en cours sur le poste « {machine_label} ».")


class TaskService:
    _tasks: Dict[str, Dict[str, Any]] = {}
    _queue: Dict[str, List[str]] = {}        # type -> ids en attente (ordre)
    _active: Dict[str, Optional[str]] = {}   # type -> id en cours (ou None)
    _lock = threading.RLock()
    _loaded = False

    _runners: Dict[str, Callable[[Dict[str, Any]], None]] = {}
    _cancel_hooks: Dict[str, Callable[[Dict[str, Any]], None]] = {}
    # Joué quand une tâche finit en 'error'. Nécessaire pour les échecs survenus **avant** le
    # runner (conflit de verrou, runner absent) : celui-ci n'ayant jamais tourné, il ne peut
    # pas nettoyer ce qu'il avait laissé en place — un index resté « en reconstruction », par
    # exemple, que plus aucune tâche ne fait avancer.
    _error_hooks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

    # Verrous de scope détenus par ce process, par task_id (non persistés).
    _held_locks: Dict[str, List[Path]] = {}

    # Sérialise les écritures de fichiers de tâche (thread runner + superviseur).
    _io_lock = threading.Lock()
    _supervisor_started = False

    # Cache court de la lecture disque (vue fusionnée) pour ne pas marteler le NAS.
    _merged_cache: Optional[Dict[str, Dict[str, Any]]] = None
    _merged_cache_ts: float = 0.0
    _MERGED_TTL = 1.0

    # Contenu des sidecars déjà lus, par id → (mtime, taille, valeurs). Le détail par page est
    # interrogé toutes les 2 s tant que le panneau est déplié, y compris pour une tâche d'un
    # autre poste (qui passe par le disque) : sans ce cache on relirait des Mo à chaque sondage.
    _sidecar_cache: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}
    # Le panneau n'affiche qu'une tâche à la fois : garder plus que les dernières consultées
    # ferait de ce cache le nouveau gouffre mémoire, à la place de celui qu'on vient de boucher.
    _SIDECAR_CACHE_MAX = 4

    # Fichiers de tâche déjà analysés, par nom → (mtime, taille, contenu). Le TTL d'une seconde
    # ci-dessus dit *quand* relire le dossier, celui-ci dit *quoi* y relire : sans lui, les deux
    # sondages du frontend reparsaient tout l'historique — jusqu'à `HISTORY_CAP` tâches — toutes
    # les deux secondes, dans le process qui fait tourner l'OCR.
    _file_cache: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}

    # Horodatage (monotone) du dernier balayage ayant relu le partage (`_adopt_orphan_queued`).
    _last_reconcile_disk: float = 0.0

    # ── Enregistrement des runners ────────────────────────────────────
    @staticmethod
    def register(task_type: str, runner: Callable[[Dict[str, Any]], None],
                 on_cancel: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_error: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        TaskService._runners[task_type] = runner
        if on_cancel is not None:
            TaskService._cancel_hooks[task_type] = on_cancel
        if on_error is not None:
            TaskService._error_hooks[task_type] = on_error

    @staticmethod
    def _run_hook(hooks: Dict[str, Callable[[Dict[str, Any]], None]], task: Dict[str, Any]) -> None:
        """Joue un hook de fin de vie. Il ne doit jamais faire échouer l'appelant : la lane et
        la persistance de la tâche priment sur son nettoyage."""
        hook = hooks.get(task.get('type'))
        if hook is None:
            return
        try:
            hook(task)
        except Exception:
            log.error("Échec du hook de tâche " + kv(type=task.get('type'), id=task.get('id')),
                      exc_info=True)

    # ── Persistance ───────────────────────────────────────────────────
    @staticmethod
    def _ensure_dir(d: Path) -> Path:
        """Crée le dossier si possible, sans jamais faire échouer l'appelant.

        Ce `mkdir` est un confort de premier démarrage, rejoué à chaque accès. Sur un partage
        momentanément injoignable il lève `OSError` — remontée telle quelle, elle traversait le
        `finally` de `_execute` et laissait la file à l'arrêt jusqu'au redémarrage du poste. Les
        lectures et écritures qui suivent ont chacune leur garde et leur repli : c'est à elles
        de constater l'indisponibilité, pas à ce `mkdir`."""
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return d

    @staticmethod
    def _dir() -> Path:
        return TaskService._ensure_dir(Path(DATA_DIR) / "tasks")

    @staticmethod
    def _file(task_id: str) -> Path:
        return TaskService._dir() / f"{task_id}.json"

    @staticmethod
    def _sidecar_file(task_id: str) -> Path:
        return TaskService._dir() / f"{task_id}.pages.json"

    @staticmethod
    def _write_atomic(path: Path, data: Dict[str, Any]) -> bool:
        """Écrit un JSON de tâche de façon **atomique** (tmp + `os.replace`). `False` si l'écriture
        a échoué (partage injoignable) — l'appelant décide s'il journalise.

        Le fichier est relu en boucle par les autres postes (`_merged_tasks`, `_lock_is_stale`) :
        une écriture en place exposerait un JSON tronqué, et un `JSONDecodeError` fait passer un
        verrou bien vivant pour obsolète — donc volable. Même motif que
        `IndexesService._save_index_meta`. Le tmp est nommé par thread pour que le superviseur et
        le runner ne se marchent pas dessus."""
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except (OSError, TypeError, ValueError):
            # `TypeError`/`ValueError` : un champ non sérialisable ajouté par un runner. Laisser
            # remonter l'exception depuis le `finally` de `_execute` bloquerait la lane.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _read_sidecar(task_id: str) -> Dict[str, Any]:
        """Clés lourdes d'une tâche, depuis `<id>.pages.json`. Vide si le fichier n'existe pas
        (tâche d'avant la séparation : ses `pages` sont restées dans le fichier principal)."""
        path = TaskService._sidecar_file(task_id)
        try:
            stat = path.stat()
        except OSError:
            TaskService._sidecar_cache.pop(task_id, None)
            return {}
        cached = TaskService._sidecar_cache.get(task_id)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        try:
            with open(path, 'r', encoding='utf-8-sig') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
        TaskService._sidecar_cache[task_id] = (stat.st_mtime_ns, stat.st_size, data)
        while len(TaskService._sidecar_cache) > TaskService._SIDECAR_CACHE_MAX:
            TaskService._sidecar_cache.pop(next(iter(TaskService._sidecar_cache)))
        return data

    @staticmethod
    def _save(task: Dict[str, Any]) -> bool:
        """Persiste la tâche : l'état changeant dans `<id>.json`, les clés `_SIDECAR` à part.
        Retourne le succès de l'écriture du fichier principal (le sidecar, figé, n'est pas
        critique) — seul `enqueue` s'en sert, les autres appelants n'ont rien à en faire.

        Le sidecar n'est écrit que s'il manque : son contenu est figé à l'enfilage, le réécrire
        à chaque battement ne ferait que recopier les mêmes mégaoctets sur le partage."""
        task_id = task['id']
        data = {k: v for k, v in task.items()
                if k not in ('cancel', 'last_write') and k not in _SIDECAR}
        heavy = {k: task[k] for k in _SIDECAR if k in task}
        with TaskService._io_lock:
            if heavy and not TaskService._sidecar_file(task_id).exists():
                TaskService._write_atomic(TaskService._sidecar_file(task_id), heavy)
            # L'horloge de modification de Windows n'avance que par pas de ~15 ms : deux
            # écritures rapprochées de même taille sont indiscernables pour `_read_task_file`.
            # Nos propres écritures, elles, sont connues — on invalide plutôt que de parier.
            TaskService._file_cache.pop(f"{task_id}.json", None)
            if not TaskService._write_atomic(TaskService._file(task_id), data):
                # Appelé chaque seconde par le superviseur : un NAS qui décroche produirait
                # une entrée par seconde, d'où l'étranglement.
                log_throttled(log, logging.WARNING, f"save:{task_id}",
                              "Échec d'écriture du fichier de tâche " + kv(id=task_id))
                return False
        return True

    @staticmethod
    def _save_throttled(task: Dict[str, Any]) -> None:
        """Écriture limitée à ~1/s pendant l'exécution (épargne le disque). Rafraîchit le
        `heartbeat` : preuve de vie pour les autres postes."""
        now = time.time()
        if now - task.get('last_write', 0) >= 1.0:
            task['last_write'] = now
            task['heartbeat'] = datetime.now().isoformat()
            TaskService._save(task)

    @staticmethod
    def _probe_disk(task_id: str, heavy: bool = True) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """`(présent, contenu)` — l'état d'une tâche sur le disque (elle peut appartenir à un
        autre poste), en distinguant le fichier **absent** (tâche réellement supprimée, ici ou
        depuis un autre poste) du fichier **présent mais illisible** (partage qui hoquette,
        écriture concurrente, JSON tronqué). Même convention que `services._read_json_probe`.

        `_dispatch` et `_lock_is_stale` ne peuvent pas se contenter d'un `None` : le premier en
        déduisait une suppression et retirait le candidat de la file — définitivement, puisque
        rien ne repassait jamais dessus ; le second en déduisait une tâche morte et rendait son
        verrou volable. Un aller-retour SMB manqué suffisait donc à condamner une tâche en
        attente, ou à lancer le même OCR sur deux postes.

        `heavy=False` laisse les clés lourdes de côté : vérifier un statut ou la vivacité d'un
        verrou n'a que faire de la liste des pages, et la relire tirerait des mégaoctets du
        partage à chaque passage du superviseur. Les tâches d'avant la séparation les portent
        encore en ligne : le sidecar absent laisse alors la valeur du fichier principal intacte."""
        f = TaskService._file(task_id)
        try:
            with open(f, 'r', encoding='utf-8-sig') as fh:
                task = json.load(fh)
        except FileNotFoundError:
            # Windows mappe aussi `ERROR_BAD_NETPATH` sur `ENOENT` : un partage injoignable est
            # indiscernable d'un fichier effacé, sauf à demander si son dossier, lui, répond.
            # Un `stat` de plus, sur le seul chemin rare.
            return (False, None) if TaskService._dir().is_dir() else (True, None)
        except (OSError, json.JSONDecodeError):
            return (True, None)
        if heavy:
            task.update(TaskService._read_sidecar(task_id))
        return (True, task)

    @staticmethod
    def _load_disk(task_id: str, heavy: bool = True) -> Optional[Dict[str, Any]]:
        """Relit l'état d'une tâche depuis le disque. `None` si elle est absente **ou**
        illisible : les appelants qui doivent séparer les deux cas passent par `_probe_disk`."""
        return TaskService._probe_disk(task_id, heavy)[1]

    # ── Appartenance / vivacité (multi-PC) ────────────────────────────
    @staticmethod
    def _is_owned(task: Dict[str, Any]) -> bool:
        """Vrai si la tâche a été créée sur ce poste. Les tâches héritées (sans `machine_id`,
        antérieures au multi-PC) sont considérées locales pour rester contrôlables."""
        mid = task.get('machine_id')
        return (mid is None) or (mid == machine_identity.machine_id())

    @staticmethod
    def _is_stale_running(task: Dict[str, Any]) -> bool:
        """Vrai si la tâche est `running` mais que son heartbeat est périmé → process mort."""
        if task.get('status') != 'running':
            return False
        hb = task.get('heartbeat') or task.get('started_at')
        if not hb:
            return False  # pas d'info → prudence : on ne la déclare pas morte
        try:
            dt = datetime.fromisoformat(hb)
        except ValueError:
            return False
        return (datetime.now() - dt).total_seconds() >= HEARTBEAT_STALE

    @staticmethod
    def _merged_tasks() -> Dict[str, Dict[str, Any]]:
        """Vue unifiée tous postes : état disque (frais pour les autres postes) + tâches en
        mémoire de CE poste (plus fraîches que le disque throttlé). Cache 1 s."""
        TaskService._ensure_loaded()
        now = time.time()
        if TaskService._merged_cache is not None and now - TaskService._merged_cache_ts < TaskService._MERGED_TTL:
            base = dict(TaskService._merged_cache)
        else:
            base = {}
            seen = set()
            for f in TaskService._dir().glob("*.json"):
                if f.name.endswith('.pages.json'):
                    continue   # rien ici n'a besoin des pages : `_HIDDEN` les écarte de l'API
                seen.add(f.name)
                t = TaskService._read_task_file(f)
                if t is None:
                    continue
                tid = t.get('id')
                if tid:
                    base[tid] = t
            # Une tâche supprimée depuis un autre poste ne repassera plus jamais par `stat` :
            # sans ce balayage son contenu resterait au chaud jusqu'à l'arrêt du serveur.
            for name in list(TaskService._file_cache):
                if name not in seen:
                    del TaskService._file_cache[name]
            TaskService._merged_cache = dict(base)
            TaskService._merged_cache_ts = now
        with TaskService._lock:
            for tid, t in TaskService._tasks.items():
                if TaskService._is_owned(t):
                    base[tid] = t
        return base

    @staticmethod
    def _read_task_file(path: Path) -> Optional[Dict[str, Any]]:
        """Contenu d'un fichier de tâche, réanalysé seulement s'il a changé depuis la dernière
        lecture. `os.stat` coûte un aller-retour, `json.load` en coûte des milliers."""
        try:
            stat = path.stat()
        except OSError:
            TaskService._file_cache.pop(path.name, None)
            return None
        cached = TaskService._file_cache.get(path.name)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        try:
            with open(path, 'r', encoding='utf-8-sig') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        TaskService._file_cache[path.name] = (stat.st_mtime_ns, stat.st_size, data)
        return data

    @staticmethod
    def _invalidate_merged() -> None:
        TaskService._merged_cache = None

    # ── Projection API ────────────────────────────────────────────────
    @staticmethod
    def _public(task: Dict[str, Any], *, full: bool = False) -> Dict[str, Any]:
        out = {k: v for k, v in task.items() if k not in _HIDDEN}
        out['owned'] = TaskService._is_owned(task)
        if not full:
            errs = task.get('errors') or []
            out['errors'] = errs[:5]
            # Le nombre d'échecs fait foi, pas la longueur de la liste : celle-ci est plafonnée
            # à la source (`ocr_service.ERRORS_KEPT`) et sous-estimerait une tâche très ratée.
            out['errors_truncated'] = max(0, max(task.get('failed') or 0, len(errs)) - 5)
        return out

    # ── Accès ─────────────────────────────────────────────────────────
    @staticmethod
    def get(task_id: str) -> Optional[Dict[str, Any]]:
        """Tâche par id : version mémoire si on la possède, sinon état disque (autre poste)."""
        TaskService._ensure_loaded()
        t = TaskService._tasks.get(task_id)
        if t is not None and TaskService._is_owned(t):
            return t
        disk = TaskService._load_disk(task_id)
        return disk if disk is not None else t

    @staticmethod
    def list_tasks() -> List[Dict[str, Any]]:
        """En cours (tous types/postes), puis en attente, puis terminés (récents d'abord)."""
        merged = list(TaskService._merged_tasks().values())
        running = [t for t in merged if t.get('status') == 'running']
        queued = [t for t in merged if t.get('status') == 'queued']
        paused = [t for t in merged if t.get('status') == 'paused']
        finished = [t for t in merged if t.get('status') in TERMINAL]
        running.sort(key=lambda t: t.get('started_at') or '')
        queued.sort(key=lambda t: t.get('created_at') or '')
        paused.sort(key=lambda t: t.get('created_at') or '')
        finished.sort(key=lambda t: t.get('finished_at') or '', reverse=True)
        return [TaskService._public(t) for t in running + queued + paused + finished]

    @staticmethod
    def summary() -> Dict[str, Any]:
        """Résumé léger (tous postes) : tâches en cours + en pause + interrompues + nb en attente."""
        merged = list(TaskService._merged_tasks().values())
        running = [TaskService._public(t) for t in merged if t.get('status') == 'running']
        paused = [TaskService._public(t) for t in merged if t.get('status') == 'paused']
        interrupted = [TaskService._public(t) for t in merged if t.get('status') == 'interrupted']
        queued_tasks = [TaskService._public(t) for t in merged if t.get('status') == 'queued']
        # has_activity ignore les tâches en pause/interrompues : on ne relance pas le polling pour elles.
        return {'running': running, 'paused': paused, 'interrupted': interrupted,
                'queued': len(queued_tasks), 'queued_tasks': queued_tasks,
                'has_activity': bool(running or queued_tasks)}

    # ── Conflits de scope (multi-PC) ──────────────────────────────────
    @staticmethod
    def _scope_keys(task: Dict[str, Any]) -> List[str]:
        """Clés de scope d'une tâche (unité de conflit). OCR = (collection, registre, modèle) ;
        index = (collection, modèle)."""
        ttype = task.get('type')
        if ttype == 'ocr':
            model = task.get('ocr_model') or ''
            scopes = task.get('scopes')
            if scopes:
                return sorted({f"ocr__{c}__{r}__{model}" for c, r in scopes})
            # Repli pour les tâches enfilées avant l'ajout de `scopes` (JSON encore présents sur
            # le partage, ou poste pas à jour) : croiser `collections` × `registres`. Sur-approxime
            # (verrouille des couples non traités), mais ne laisse jamais passer un vrai conflit.
            cols = task.get('collections') or []
            regs = task.get('registres') or []
            return sorted({f"ocr__{c}__{r}__{model}" for c in cols for r in regs})
        if ttype == 'index':
            # L'unité de conflit d'un index est sa sortie = son id (multi-sources).
            iid = task.get('index_id')
            if iid:
                return [f"index__{iid}"]
            # Legacy (anciennes tâches mono-source sans index_id).
            return [f"index__{task.get('collection_id')}__{task.get('model_name')}"]
        return []

    @staticmethod
    def _human_scope(key: str) -> str:
        parts = key.split('__')
        if parts[0] == 'ocr' and len(parts) >= 4:
            return f"{parts[1]} / {parts[2]} (modèle {parts[3]})"
        if parts[0] == 'index' and len(parts) >= 3:
            return f"index {parts[1]} (modèle {parts[2]})"
        if parts[0] == 'index' and len(parts) == 2:
            return f"index {parts[1]}"
        return key

    @staticmethod
    def active_scopes(task_type: str) -> Dict[str, str]:
        """{scope_key: machine_label} des scopes occupés par les tâches non terminales et
        **vivantes** de ce type, tous postes confondus."""
        out: Dict[str, str] = {}
        for t in TaskService._merged_tasks().values():
            if t.get('type') != task_type:
                continue
            st = t.get('status')
            if st in TERMINAL:
                continue
            if st == 'running' and TaskService._is_stale_running(t):
                continue  # morte → n'occupe plus son scope
            label = t.get('machine_label') or t.get('machine_id') or '?'
            for key in TaskService._scope_keys(t):
                out.setdefault(key, label)
        return out

    @staticmethod
    def find_conflict(task_type: str, fields: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """(scope_lisible, machine_label) si un scope demandé est déjà occupé, sinon None."""
        active = TaskService.active_scopes(task_type)
        pseudo = {'type': task_type, **fields}
        for key in TaskService._scope_keys(pseudo):
            if key in active:
                return (TaskService._human_scope(key), active[key])
        return None

    # ── Verrous de scope (filet anti-course à l'exécution) ────────────
    @staticmethod
    def _locks_dir() -> Path:
        return TaskService._ensure_dir(Path(DATA_DIR) / "locks")

    @staticmethod
    def _lock_path(key: str) -> Path:
        return TaskService._locks_dir() / (re.sub(r'[^A-Za-z0-9_.-]', '_', key) + '.lock')

    @staticmethod
    def _try_create_lock(path: Path, task_id: str) -> bool:
        try:
            with open(path, 'x', encoding='utf-8') as f:
                json.dump({'task_id': task_id, 'machine_id': machine_identity.machine_id()}, f)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    @staticmethod
    def _lock_owner(path: Path) -> Dict[str, Any]:
        """Contenu d'un fichier de verrou ({'task_id', 'machine_id'}), `{}` s'il est illisible."""
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                info = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return info if isinstance(info, dict) else {}

    @staticmethod
    def _lock_is_stale(path: Path) -> bool:
        """Un verrou est obsolète si sa tâche est terminée, absente ou morte (heartbeat périmé)."""
        info = TaskService._lock_owner(path)
        tid = info.get('task_id')
        if not tid:
            return True
        présent, disk = TaskService._probe_disk(tid, heavy=False)
        if disk is None:
            # Fichier illisible ≠ tâche disparue. Déclarer le verrou obsolète sur un simple
            # hoquet du partage le rendait volable : deux postes lançaient alors le même OCR
            # dans le même dossier de sortie. Dans le doute le verrou est tenu — au pire il
            # faudra attendre un `HEARTBEAT_STALE` de plus, une fois le fichier relisible.
            return not présent
        st = disk.get('status')
        if st in TERMINAL:
            return True
        return st == 'running' and TaskService._is_stale_running(disk)

    @staticmethod
    def _claim_locks(task: Dict[str, Any]) -> Optional[str]:
        """Réserve atomiquement les verrous de scope. Retourne la clé en conflit (détenteur
        vivant) si échec, sinon None (récupère au passage les verrous obsolètes)."""
        held: List[Path] = []
        for key in TaskService._scope_keys(task):
            path = TaskService._lock_path(key)
            if not TaskService._try_create_lock(path, task['id']):
                if TaskService._lock_is_stale(path):
                    previous = TaskService._lock_owner(path)
                    log.warning("Verrou périmé récupéré " + kv(
                        scope=TaskService._human_scope(key), id=task['id'],
                        ancienne_tâche=previous.get('task_id'),
                        ancien_poste=previous.get('machine_id')))
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    if not TaskService._try_create_lock(path, task['id']):
                        TaskService._free_paths(held)
                        log.warning("Verrou repris par un autre poste " + kv(
                            scope=TaskService._human_scope(key), id=task['id']))
                        return key
                else:
                    TaskService._free_paths(held)
                    log.warning("Verrou détenu ailleurs " + kv(
                        scope=TaskService._human_scope(key), id=task['id'],
                        détenteur=TaskService._lock_owner(path).get('machine_id')))
                    return key
            held.append(path)
        TaskService._held_locks[task['id']] = held
        return None

    @staticmethod
    def _free_paths(paths: List[Path]) -> None:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _release_locks(task: Dict[str, Any]) -> None:
        """Libère les verrous de la tâche. Utilise les chemins détenus en mémoire ; en repli
        (reprise/redémarrage), ne supprime que les verrous estampillés à cette tâche."""
        held = TaskService._held_locks.pop(task.get('id'), None)
        if held is not None:
            TaskService._free_paths(held)
            return
        for key in TaskService._scope_keys(task):
            path = TaskService._lock_path(key)
            if TaskService._lock_owner(path).get('task_id') == task.get('id'):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    # ── Boîte aux lettres de commandes (contrôle inter-postes) ────────
    @staticmethod
    def _control_dir() -> Path:
        return TaskService._ensure_dir(TaskService._dir() / "control")

    @staticmethod
    def _control_file(task_id: str) -> Path:
        return TaskService._control_dir() / f"{task_id}.json"

    @staticmethod
    def _request_control(task_id: str, action: str) -> bool:
        """Dépose une commande à destination du poste propriétaire d'une tâche.
        On n'écrit **jamais** dans le fichier d'une tâche en cours d'un autre poste : il est
        réécrit chaque seconde par son propriétaire, qui écraserait notre modification."""
        if action not in CONTROL_ACTIONS:
            return False
        ident = machine_identity.get_identity()
        payload = {'action': action, 'from': ident['machine_label'],
                   'from_id': ident['machine_id'], 'at': datetime.now().isoformat()}
        path = TaskService._control_file(task_id)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
            log.info("Commande envoyée " + kv(action=action, id=task_id))
            return True
        except OSError as e:
            log.warning("Échec d'envoi de commande " + kv(action=action, id=task_id, err=e))
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _drain_control() -> None:
        """Applique les commandes qui nous sont destinées, puis retire leur marqueur.

        Couvre tous les statuts : `running` (cancel/pause coopératifs via les drapeaux lus par le
        runner) mais aussi `queued`/`paused`/`interrupted`, qui n'ont pas de thread d'exécution."""
        for f in TaskService._control_dir().glob("*.json"):
            task_id = f.stem
            try:
                with open(f, 'r', encoding='utf-8-sig') as fh:
                    cmd = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            action = cmd.get('action')
            with TaskService._lock:
                task = TaskService._tasks.get(task_id)
                mine = task is not None and TaskService._is_owned(task)
            # Marqueur sans destinataire ici (autre poste, tâche supprimée) : on le laisse à
            # son propriétaire. Il sera purgé avec la tâche (`_unlink_task`).
            if not mine:
                continue
            log.info("Commande reçue " + kv(action=action, id=task_id, de=cmd.get('from')))
            # `cancel`/`pause`/`resume` valident eux-mêmes le statut : une commande devenue
            # caduque (tâche déjà terminée) est simplement sans effet.
            try:
                if action == 'cancel':
                    TaskService.cancel(task_id)
                elif action == 'pause':
                    TaskService.pause(task_id)
                elif action == 'resume':
                    TaskService.resume(task_id)
            except Exception:
                log.error("Échec d'application de la commande "
                          + kv(action=action, id=task_id, de=cmd.get('from')), exc_info=True)
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    # ── Superviseur (heartbeat + commandes distantes) ─────────────────
    @staticmethod
    def _supervisor() -> None:
        """Boucle de fond : preuve de vie des tâches en cours de CE poste, commandes reçues,
        et rattrapage de la file (`_reconcile_queues`).

        Le heartbeat ne peut pas dépendre de `_save_throttled` : celui-ci n'est appelé qu'entre
        deux pages OCR (parfois > 60 s sur CPU) et jamais pendant le préflight (import torch,
        chargement des modèles depuis le NAS). Sans ce thread, les autres postes déclarent la
        tâche morte, libèrent son scope et volent son verrou → même OCR lancé deux fois."""
        while True:
            time.sleep(SUPERVISOR_INTERVAL)
            try:
                with TaskService._lock:
                    running = [t for t in TaskService._tasks.values()
                               if t.get('status') == 'running' and TaskService._is_owned(t)]
                for task in running:
                    task['heartbeat'] = datetime.now().isoformat()
                    task['last_write'] = time.time()
                    TaskService._save(task)
                if running:
                    TaskService._invalidate_merged()
                TaskService._drain_control()
                TaskService._reconcile_queues()
            except Exception:
                # Un superviseur ne doit jamais mourir — mais il ne doit pas non plus remplir
                # le journal à raison d'une entrée toutes les 5 s si la panne est durable.
                log_throttled(log, logging.ERROR, 'supervisor',
                              "Échec du cycle superviseur", exc_info=True)

    @staticmethod
    def _reconcile_queues() -> None:
        """Rattrapage périodique des files d'attente. **Seul mécanisme non événementiel.**

        `_dispatch` n'est appelée que par quatre événements : création, fin d'exécution, reprise,
        démarrage du poste. Si l'un d'eux passe à travers — fichier momentanément illisible sur
        le partage, écriture perdue, exception inattendue — plus personne ne démarre la tâche en
        attente : elle reste « en attente de démarrage » indéfiniment et garde son scope réservé
        pour tous les postes, jusqu'au redémarrage du backend. Ce balayage est le filet.

        Coût : une passe en mémoire à chaque cycle. Le partage n'est consulté que si une lane est
        libre alors que sa file est vide — c'est-à-dire seulement quand notre vue en mémoire
        n'explique plus rien — et au plus toutes les `RECONCILE_DISK_INTERVAL` secondes."""
        à_lancer: List[str] = []
        mémoire_muette = False
        with TaskService._lock:
            # Les runners enregistrés font foi sur les types qui existent : `_tasks` et `_queue`
            # sont justement ce dont on se méfie ici, et un poste dont la mémoire a été vidée
            # n'aurait plus aucun type à balayer — donc aucune chance de réadopter ses orphelines.
            types = set(TaskService._runners)
            types.update(t.get('type') for t in TaskService._tasks.values() if t.get('type'))
            types.update(TaskService._queue)
            types.update(TaskService._active)
            for ttype in types:
                if TaskService._active.get(ttype) is not None:
                    continue
                # File reconstruite depuis la source qui fait foi : nos tâches `queued`. L'ordre
                # déjà en place est conservé (FIFO), les manquantes sont ajoutées par ancienneté
                # — même règle qu'au démarrage (`load_on_startup`).
                attendues = {tid for tid, t in TaskService._tasks.items()
                             if t.get('type') == ttype and t.get('status') == 'queued'
                             and TaskService._is_owned(t)}
                avant = TaskService._queue.get(ttype) or []
                ids = [tid for tid in avant if tid in attendues]
                ids += sorted(attendues.difference(ids),
                              key=lambda tid: TaskService._tasks[tid].get('created_at') or '')
                if ids != avant:
                    TaskService._queue[ttype] = ids
                    log.warning("File d'attente reconstruite " + kv(
                        type=ttype, avant=len(avant), après=len(ids)))
                if ids:
                    à_lancer.append(ttype)
                else:
                    mémoire_muette = True
        if mémoire_muette:
            # Hors du verrou : lit le partage.
            à_lancer.extend(TaskService._adopt_orphan_queued())
        for ttype in dict.fromkeys(à_lancer):
            TaskService._dispatch(ttype)

    @staticmethod
    def _adopt_orphan_queued() -> List[str]:
        """Réintègre les tâches `queued` de CE poste présentes sur le partage mais absentes de
        notre mémoire (état perdu par un incident, ou par une version antérieure du moteur).
        Retourne les types à relancer.

        On ne touche **jamais** aux tâches d'un autre poste : sa file n'est démarrée que par lui.
        Une tâche réadoptée repasse par `_claim_locks` comme n'importe quelle autre — si son
        scope a été pris entre-temps, elle échouera proprement au lieu de doubler une sortie."""
        maintenant = time.monotonic()
        if maintenant - TaskService._last_reconcile_disk < RECONCILE_DISK_INTERVAL:
            return []
        TaskService._last_reconcile_disk = maintenant
        types: List[str] = []
        # `_merged_tasks` a son cache d'une seconde, et `_read_task_file` ne réanalyse que les
        # fichiers dont la taille ou la date a bougé : rien de neuf n'est relu ici.
        merged = TaskService._merged_tasks()
        with TaskService._lock:
            for tid, vue in merged.items():
                if vue.get('status') != 'queued' or not TaskService._is_owned(vue):
                    continue
                ttype = vue.get('type')
                if not ttype or tid in TaskService._tasks:
                    continue
                task = TaskService._load_disk(tid)   # avec ses pages : c'est le point de reprise
                if task is None or task.get('status') != 'queued':
                    continue
                task.setdefault('cancel', False)
                TaskService._tasks[tid] = task
                TaskService._queue.setdefault(ttype, []).append(tid)
                types.append(ttype)
                log.warning("Tâche en attente réadoptée depuis le partage " + kv(
                    type=ttype, id=tid, label=task.get('label')))
        return types

    @staticmethod
    def _start_supervisor() -> None:
        with TaskService._lock:
            if TaskService._supervisor_started:
                return
            TaskService._supervisor_started = True
        threading.Thread(target=TaskService._supervisor, daemon=True).start()

    # ── Cycle de vie ──────────────────────────────────────────────────
    @staticmethod
    def enqueue(task_type: str, label: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Crée une tâche `queued` et démarre la lane de ce type si elle est libre.

        Lève `TaskConflict` si le scope demandé est déjà occupé par un autre poste, et
        `TaskUnavailable` si le partage n'a pas accepté l'écriture de la tâche."""
        TaskService._ensure_loaded()
        fields = fields or {}
        conflict = TaskService.find_conflict(task_type, fields)
        if conflict:
            log.warning("Création refusée " + kv(type=task_type, label=label,
                                                 scope=conflict[0], poste=conflict[1]))
            raise TaskConflict(conflict[0], conflict[1])
        ident = machine_identity.get_identity()
        task_id = uuid.uuid4().hex[:12]
        task: Dict[str, Any] = {
            'id': task_id,
            'type': task_type,
            'status': 'queued',
            'label': label,
            'total': 0,
            'processed': 0,
            'failed': 0,
            'current': None,
            'created_at': datetime.now().isoformat(),
            'started_at': None,
            'finished_at': None,
            'error': None,
            'errors': [],
            'cancel': False,
            'machine_id': ident['machine_id'],
            'machine_label': ident['machine_label'],
            'operator': ident['operator'],
        }
        task.update(fields)
        with TaskService._lock:
            TaskService._tasks[task_id] = task
            TaskService._queue.setdefault(task_type, []).append(task_id)
            enregistrée = TaskService._save(task)
            TaskService._invalidate_merged()
            if not enregistrée:
                # Une tâche que le partage n'a pas acceptée n'existe pour personne d'autre : la
                # garder en file ferait réserver son scope sur tous les postes au bénéfice d'un
                # fantôme, et l'interface annoncerait une création qui n'a pas eu lieu.
                TaskService._tasks.pop(task_id, None)
                TaskService._queue[task_type].remove(task_id)
                log.error("Création abandonnée : écriture impossible " + kv(
                    type=task_type, id=task_id, label=label))
                raise TaskUnavailable(
                    "Le dossier partagé est injoignable : la tâche n'a pas pu être enregistrée.")
            TaskService._prune_history()
        log.info("Tâche créée " + kv(type=task_type, id=task_id, label=label))
        TaskService._dispatch(task_type)
        return TaskService._public(task, full=True)

    @staticmethod
    def _dispatch(task_type: str) -> None:
        """Démarre la prochaine tâche en attente de ce type si la lane est libre.

        **Ne lève jamais** : elle est appelée depuis le `finally` de `_execute`, où la moindre
        exception (partage injoignable, JSON récalcitrant) laissait la file à l'arrêt jusqu'au
        redémarrage du poste. Le balayage du superviseur réessaiera au cycle suivant."""
        try:
            TaskService._dispatch_next(task_type)
        except Exception:
            log_throttled(log, logging.ERROR, f'dispatch:{task_type}',
                          "Échec du démarrage de la file " + kv(type=task_type), exc_info=True)

    @staticmethod
    def _dispatch_next(task_type: str) -> None:
        """Relit le disque avant de lancer : une tâche annulée ou supprimée depuis un autre
        poste est écartée. Mais un candidat n'est **jamais** évincé sur un simple échec de
        lecture : on ne sait alors rien de son sort, et l'évincer le condamnait pour de bon —
        son fichier restait `queued` sur le partage, l'interface affichait « en attente » sans
        fin, et le scope demeurait réservé sur tous les postes. On laisse donc le candidat en
        tête de file et on rend la main : `_reconcile_queues` réessaiera dans les secondes qui
        suivent."""
        with TaskService._lock:
            if TaskService._active.get(task_type) is not None:
                return
            q = TaskService._queue.get(task_type, [])
            tid = None
            while q:
                cand = q[0]
                t = TaskService._tasks.get(cand)
                if not t or t['status'] != 'queued':
                    q.pop(0)
                    continue
                présent, disk = TaskService._probe_disk(cand, heavy=False)
                if disk is None and présent:
                    log_throttled(log, logging.WARNING, f'illisible:{cand}',
                                  "Fichier de tâche illisible, démarrage reporté " + kv(
                                      type=task_type, id=cand, label=t.get('label')))
                    return
                if disk is None:
                    q.pop(0)
                    TaskService._tasks.pop(cand, None)
                    log.info("Tâche supprimée à distance, retirée de la file " + kv(
                        type=task_type, id=cand, label=t.get('label')))
                    continue
                if disk.get('status') != 'queued':
                    q.pop(0)
                    t['status'] = disk.get('status')     # annulée / reprise à distance
                    log.info("Tâche non démarrée, statut changé à distance " + kv(
                        type=task_type, id=cand, statut=disk.get('status')))
                    continue
                tid = q.pop(0)
                break
            if tid is None:
                return
            t = TaskService._tasks[tid]
            TaskService._active[task_type] = tid
            t['status'] = 'running'
            t['started_at'] = datetime.now().isoformat()
            t['heartbeat'] = t['started_at']
            TaskService._save(t)
            TaskService._invalidate_merged()
            log.info("Démarrage " + kv(type=task_type, id=tid, label=t.get('label')))
            # Démarrage sous le verrou : le bloc y fait déjà les E/S du `_save`, et sortir du
            # verrou rouvrait une fenêtre où la lane est prise sans que personne ne l'exécute.
            try:
                threading.Thread(target=TaskService._execute, args=(tid,), daemon=True).start()
            except Exception:
                TaskService._active[task_type] = None
                t['status'] = 'queued'
                t['started_at'] = None
                q.insert(0, tid)
                TaskService._save(t)
                raise

    @staticmethod
    def _elapsed(task: Dict[str, Any]) -> Optional[str]:
        """Durée depuis `started_at`, en « 1h04m12s » / « 40m13s » / « 12s » (pour le journal)."""
        started = task.get('started_at')
        if not started:
            return None
        try:
            delta = int((datetime.now() - datetime.fromisoformat(started)).total_seconds())
        except ValueError:
            return None
        if delta < 0:
            return None
        hours, rest = divmod(delta, 3600)
        minutes, seconds = divmod(rest, 60)
        if hours:
            return f"{hours}h{minutes:02d}m{seconds:02d}s"
        if minutes:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"

    @staticmethod
    def _release_lane(task_type: str, task_id: str) -> None:
        """Rend la lane et enchaîne sur la tâche suivante. Ne libère que si elle nous appartient
        encore : le balayage du superviseur a pu la dégripper et démarrer autre chose."""
        with TaskService._lock:
            if TaskService._active.get(task_type) == task_id:
                TaskService._active[task_type] = None
            TaskService._invalidate_merged()
        TaskService._dispatch(task_type)

    @staticmethod
    def _execute(task_id: str) -> None:
        """Exécute la tâche puis libère la lane — **quoi qu'il arrive**.

        La lecture de la tâche était hors du `try` : un `KeyError` y laissait `_active` occupé
        pour de bon, et toutes les tâches suivantes de ce type restaient « en attente de
        démarrage » indéfiniment, scope réservé compris. Tout ce qui suit la prise de la lane
        est donc gardé."""
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            ttype = (task or {}).get('type') or next(
                (k for k, v in TaskService._active.items() if v == task_id), None)
        if task is None:
            log.error("Tâche introuvable au démarrage, lane libérée " + kv(id=task_id, type=ttype))
            if ttype:
                TaskService._release_lane(ttype, task_id)
            return
        try:
            # Filet anti-course : si un autre poste a démarré le même scope entre la création
            # et ici, on échoue proprement au lieu de corrompre la sortie partagée.
            conflict = TaskService._claim_locks(task)
            if conflict is not None:
                raise RuntimeError(f"« {TaskService._human_scope(conflict)} » verrouillé par un autre poste.")
            runner = TaskService._runners.get(ttype)
            if runner is None:
                raise RuntimeError(f"Aucun runner enregistré pour le type « {ttype} »")
            runner(task)
            if task['status'] == 'running':
                task['status'] = 'done'
        except Exception as e:
            task['status'] = 'error'
            task['error'] = str(e)
            # Certains échecs précèdent le runner (verrou pris par un autre poste, runner
            # absent) : il n'a alors rien pu nettoyer de ce que l'enfilage avait matérialisé.
            TaskService._run_hook(TaskService._error_hooks, task)
        finally:
            try:
                TaskService._release_locks(task)
                task['pause'] = False
                summary = kv(type=ttype, id=task_id, statut=task['status'],
                             durée=TaskService._elapsed(task), traitées=task.get('processed'),
                             échecs=task.get('failed'), err=task.get('error'))
                log.log(logging.ERROR if task['status'] == 'error' else logging.INFO,
                        "Fin " + summary)
                if task['status'] in TERMINAL:
                    task['finished_at'] = datetime.now().isoformat()
                task['current'] = None
                TaskService._save(task)
            except Exception:
                log.error("Échec de la finalisation " + kv(type=ttype, id=task_id), exc_info=True)
            finally:
                # Hors du `try` ci-dessus : une lane jamais rendue bloque toutes les tâches
                # suivantes de ce type, ce qui est bien pire qu'une finalisation ratée.
                TaskService._release_lane(ttype, task_id)

    @staticmethod
    def _logged(action: str, task_id: str, result: str) -> str:
        """Journalise l'issue d'une action de contrôle et la retourne telle quelle.

        `cancel`/`pause`/`resume` ont chacune plusieurs sorties (appliquée ici, transmise au
        poste propriétaire, refusée) : les envelopper évite d'instrumenter chaque `return`."""
        log.info("Action " + kv(action=action, id=task_id, résultat=result))
        return result

    @staticmethod
    def cancel(task_id: str) -> str:
        """APPLIED si l'annulation est prise en charge ici, REQUESTED si elle est transmise au
        poste propriétaire, REFUSED si l'état ne le permet pas."""
        return TaskService._logged('cancel', task_id, TaskService._cancel(task_id))

    @staticmethod
    def pause(task_id: str) -> str:
        """Met en pause une tâche en cours : directement si elle est à nous, via une commande
        déposée pour le poste propriétaire sinon."""
        return TaskService._logged('pause', task_id, TaskService._pause(task_id))

    @staticmethod
    def resume(task_id: str) -> str:
        """Reprend une tâche en pause ou interrompue, ici ou sur le poste propriétaire."""
        return TaskService._logged('resume', task_id, TaskService._resume(task_id))

    @staticmethod
    def _cancel(task_id: str) -> str:
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] == 'running':
                    task['cancel'] = True          # le runner s'arrêtera proprement
                    return APPLIED
                # 'interrupted' est terminal mais reste annulable : on le finalise comme une tâche
                # en file/pause (retrait de file no-op + nettoyage index partiel via le hook).
                if task['status'] in ('queued', 'paused', 'interrupted'):
                    q = TaskService._queue.get(task['type'])
                    if q and task_id in q:
                        q.remove(task_id)
                    TaskService._finalize_cancel(task)
                    return APPLIED
                return REFUSED

        # Tâche d'un autre poste.
        disk = TaskService._load_disk(task_id)
        if not disk or disk.get('status') in ('done', 'error', 'cancelled'):
            return REFUSED
        # Le propriétaire doit appliquer l'annulation lui-même : lui seul peut arrêter son runner,
        # et sa copie mémoire ferait autorité sur ce qu'on écrirait ici.
        TaskService._request_control(task_id, 'cancel')
        if disk.get('status') == 'running':
            return REQUESTED
        # Non active (queued / paused / interrupted) : on finalise aussi directement, pour qu'une
        # orpheline d'un poste éteint — qui ne lira jamais la commande — soit tout de même nettoyée.
        TaskService._finalize_cancel(disk)
        return APPLIED

    @staticmethod
    def _pause(task_id: str) -> str:
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] == 'running':
                    task['pause'] = True
                    return APPLIED
                return REFUSED
        disk = TaskService._load_disk(task_id)
        if not disk or disk.get('status') != 'running':
            return REFUSED
        return REQUESTED if TaskService._request_control(task_id, 'pause') else REFUSED

    @staticmethod
    def _resume(task_id: str) -> str:
        """Les deux statuts repartent de leur dernier checkpoint (au dernier registre / à la
        dernière page terminée) : la pause l'écrit à l'arrêt, l'interruption s'appuie sur
        l'état persisté pendant le traitement.

        Une tâche d'un autre poste ne peut être reprise que par lui (c'est sa file et son
        thread d'exécution) : on lui transmet la demande."""
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] not in ('paused', 'interrupted'):
                    return REFUSED
                task['pause'] = False
                task['status'] = 'queued'
                task['finished_at'] = None   # 'interrupted' est terminal : on le réactive
                task['error'] = None
                TaskService._queue.setdefault(task['type'], []).append(task_id)
                TaskService._save(task)
                TaskService._invalidate_merged()
                ttype = task['type']
                dispatch = True
            else:
                dispatch = False
        if dispatch:
            TaskService._dispatch(ttype)
            return APPLIED
        disk = TaskService._load_disk(task_id)
        if not disk or disk.get('status') not in ('paused', 'interrupted'):
            return REFUSED
        return REQUESTED if TaskService._request_control(task_id, 'resume') else REFUSED

    @staticmethod
    def _finalize_cancel(task: Dict[str, Any]) -> None:
        """Passe une tâche en `cancelled`, persiste, libère ses verrous et joue le hook."""
        task['status'] = 'cancelled'
        task['finished_at'] = datetime.now().isoformat()
        TaskService._save(task)
        TaskService._release_locks(task)
        TaskService._invalidate_merged()
        TaskService._run_hook(TaskService._cancel_hooks, task)

    @staticmethod
    def delete(task_id: str) -> bool:
        """Supprime une tâche non en cours. Autorisé depuis n'importe quel poste pour les
        tâches d'un autre poste (nettoyage d'orpheline), sauf si elle tourne réellement.

        Supprimer une tâche **non terminée** (en attente, en pause, interrompue) joue son hook
        d'annulation : elle avait pu matérialiser quelque chose à l'enfilage — un index marqué
        « en reconstruction », par exemple — que plus rien ne viendrait alors nettoyer."""
        TaskService._ensure_loaded()
        # 'interrupted' est dans TERMINAL mais reste reprenable : la supprimer, c'est bien
        # abandonner un travail en cours, donc jouer le hook.
        ABANDONED = ('queued', 'paused', 'interrupted')
        removed: Optional[Dict[str, Any]] = None   # tâche abandonnée, dont le hook reste à jouer

        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            owned = task is not None and TaskService._is_owned(task)
            if owned:
                if task['status'] == 'running':
                    return False
                q = TaskService._queue.get(task['type'])
                if q and task_id in q:
                    q.remove(task_id)
                TaskService._tasks.pop(task_id, None)
                TaskService._release_locks(task)
                TaskService._invalidate_merged()
                TaskService._unlink_task(task_id)
                if task['status'] in ABANDONED:
                    removed = task

        if not owned:
            # Tâche d'un autre poste.
            disk = TaskService._load_disk(task_id)
            if disk is not None and disk.get('status') == 'running' and not TaskService._is_stale_running(disk):
                return False  # vraiment en cours ailleurs
            with TaskService._lock:
                TaskService._tasks.pop(task_id, None)
            if disk is not None:
                TaskService._release_locks(disk)
                if disk.get('status') in ABANDONED:
                    removed = disk
            TaskService._invalidate_merged()
            TaskService._unlink_task(task_id)

        if removed is not None:
            # Hors du verrou : le hook écrit sur le partage, qui peut être lent.
            TaskService._run_hook(TaskService._cancel_hooks, removed)
        return True

    @staticmethod
    def _unlink_task(task_id: str) -> None:
        """Supprime les fichiers de la tâche (état + clés lourdes) et la commande éventuellement
        en attente pour elle (sinon un marqueur orphelin survivrait à la tâche qu'il visait)."""
        for path in (TaskService._file(task_id), TaskService._sidecar_file(task_id),
                     TaskService._control_file(task_id)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        TaskService._sidecar_cache.pop(task_id, None)
        TaskService._file_cache.pop(f"{task_id}.json", None)

    @staticmethod
    def _prune_history() -> None:
        """Borne l'historique des tâches **possédées** (n'efface pas celles des autres postes)."""
        finished = [t for t in TaskService._tasks.values()
                    if t['status'] in TERMINAL and TaskService._is_owned(t)]
        if len(finished) <= HISTORY_CAP:
            return
        finished.sort(key=lambda t: t.get('finished_at') or '')
        stale = finished[:len(finished) - HISTORY_CAP]
        for task in stale:
            TaskService._tasks.pop(task['id'], None)
            TaskService._unlink_task(task['id'])
        # Le journal est désormais la seule trace de ces tâches : il survit à la purge.
        log.info("Historique purgé " + kv(supprimées=len(stale), plafond=HISTORY_CAP))

    # ── Reprise au démarrage ──────────────────────────────────────────
    @staticmethod
    def _ensure_loaded() -> None:
        if not TaskService._loaded:
            TaskService.load_on_startup()

    @staticmethod
    def _migrate_legacy_ocr_jobs(tasks_dir: Path) -> None:
        """Ingestion unique des anciens jobs OCR (data/ocr_jobs) → data/tasks (type='ocr')."""
        legacy = Path(DATA_DIR) / "ocr_jobs"
        if not legacy.exists():
            return
        for f in legacy.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8-sig') as fh:
                    data = json.load(fh)
                data.setdefault('type', 'ocr')
                dest = tasks_dir / f.name
                if not dest.exists():
                    with open(dest, 'w', encoding='utf-8') as out:
                        json.dump(data, out, ensure_ascii=False)
                f.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                continue
        try:
            legacy.rmdir()
        except OSError:
            pass

    @staticmethod
    def load_on_startup() -> None:
        with TaskService._lock:
            if TaskService._loaded:
                return
            TaskService._loaded = True
            tasks_dir = TaskService._dir()
            TaskService._migrate_legacy_ocr_jobs(tasks_dir)

            for f in tasks_dir.glob("*.json"):
                if f.name.endswith('.pages.json'):
                    continue   # sidecar : recollé avec sa tâche, jamais chargé pour lui-même
                try:
                    with open(f, 'r', encoding='utf-8-sig') as fh:
                        task = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                if task.get('id'):
                    # Sans quoi une tâche reprise après redémarrage repartirait sans ses pages.
                    task.update(TaskService._read_sidecar(task['id']))
                task.setdefault('cancel', False)
                task.setdefault('type', 'ocr')
                # Ne marquer interrompue QUE nos propres tâches : une tâche `running` d'un autre
                # poste tourne peut-être réellement (sa vivacité est jugée via le heartbeat).
                if task.get('status') == 'running' and TaskService._is_owned(task):
                    log.warning("Tâche interrompue par un arrêt du poste " + kv(
                        type=task.get('type'), id=task.get('id'), label=task.get('label'),
                        traitées=task.get('processed'), total=task.get('total')))
                    task['status'] = 'interrupted'   # le process précédent est mort
                    task['current'] = None
                    if not task.get('finished_at'):
                        task['finished_at'] = datetime.now().isoformat()
                    TaskService._save(task)
                    TaskService._release_locks(task)
                TaskService._tasks[task['id']] = task

            # File propre au poste : on ne ré-enfile que NOS tâches en attente.
            queued = [t for t in TaskService._tasks.values()
                      if t.get('status') == 'queued' and TaskService._is_owned(t)]
            queued.sort(key=lambda t: t.get('created_at') or '')
            for t in queued:
                TaskService._queue.setdefault(t['type'], []).append(t['id'])

        for ttype in list(TaskService._queue.keys()):
            TaskService._dispatch(ttype)

        TaskService._start_supervisor()
