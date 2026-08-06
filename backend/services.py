import copy
import os
import sys
import json
import logging
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Any
from pathlib import Path
from rapidfuzz import fuzz as _fuzz

import scan_snapshot

# `logging` de la stdlib, pas `app_logging` : celui-ci importe DATA_DIR d'ici, l'inverse
# créerait un cycle. Le handler est posé sur la racine par `app_logging.setup()`.
log = logging.getLogger('data')

def _get_base_dir() -> str:
    if getattr(sys, 'frozen', False):
        # Exécution via PyInstaller : données à côté de l'exe
        return os.path.dirname(sys.executable)
    # En dev : remonter d'un niveau (backend/ → racine du projet)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.getenv("DATA_DIR", os.path.join(_get_base_dir(), "data"))

# Extensions d'images reconnues comme pages de scan
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}


def check_data_storage() -> Dict[str, Any]:
    """Santé du dossier de données racine et de ses sous-dossiers cruciaux.

    Seul `exists=False` (dossier data racine introuvable) traduit une installation
    cassée — c'est le signal qui déclenche l'écran bloquant côté UI. Les sous-dossiers
    manquants à l'intérieur d'un data/ existant sont bénins : ils sont recréés à la
    première écriture et correspondent à un état vide normal.
    """
    data_path = Path(DATA_DIR)
    exists = data_path.is_dir()
    return {
        "data_dir": str(data_path),
        "exists": exists,
        "writable": os.access(DATA_DIR, os.W_OK) if exists else False,
        "subdirs": {
            "collections": (data_path / "collections").is_dir(),
            "models": (data_path / "models").is_dir(),
            "indexes": (data_path / "indexes").is_dir(),
        },
    }


def _read_json_probe(path: Path, retries: int = 3, delay: float = 0.04):
    """`(présent, contenu)` — même lecture tolérante que `_read_json_retry`, mais en
    distinguant le fichier **absent** (cas normal, rien à signaler) du fichier **présent
    mais illisible** (anomalie à remonter). Un simple `None` confond les deux, alors que
    l'analyse doit précisément savoir les séparer pour publier `metadata_illisible`.

    Rend aussi une lecture unique suffisante là où il fallait sinon un `exists()` puis un
    `open()` — un aller-retour SMB de moins par fichier."""
    for attempt in range(retries):
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                return True, json.load(f)
        except FileNotFoundError:
            return False, None   # absence normale (metadata pas encore écrit) — rien à réessayer
        except (json.JSONDecodeError, PermissionError, OSError) as e:
            if attempt == retries - 1:
                # L'appelant interprète `None` comme « pas de metadata » et publie des
                # compteurs vides : sans cette trace, un NAS qui décroche se manifeste par des
                # chiffres qui s'effondrent sans la moindre erreur nulle part.
                log.warning(f"Lecture JSON abandonnée fichier={path.name} err={e}")
                return True, None
            log.debug(f"Relecture JSON fichier={path.name} tentative={attempt + 1} err={e}")
            time.sleep(delay)
    return True, None


def _read_json_retry(path: Path, retries: int = 3, delay: float = 0.04) -> Optional[Dict[str, Any]]:
    """Lit un JSON en tolérant une écriture concurrente. `None` si illisible.

    Les metadata sont réécrits très souvent (progression d'indexation, `ocr_status` publié
    registre par registre) et relus en boucle, y compris par les autres postes du NAS. Les
    écritures sont atomiques (tmp + `os.replace`), donc jamais tronquées, mais sous Windows un
    lecteur peut tomber sur un `PermissionError` transitoire au moment exact du remplacement :
    on réessaie brièvement plutôt que de faire remonter l'erreur."""
    return _read_json_probe(path, retries, delay)[1]


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Écrit un JSON de façon atomique (tmp + `os.replace`).

    Ces fichiers sont relus en boucle par les autres postes du NAS : une écriture en place
    les expose à lire un contenu tronqué, ce que `_read_json_retry` ne peut pas rattraper
    puisque le fichier reste tronqué."""
    tmp = path.with_name(path.name + '.tmp')
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _count_ocr_xml(reg_ocr_dir: Path, pages_total: int) -> Dict[str, Dict[str, int]]:
    """État OCR d'un registre : nombre de XML par modèle dans ocr/<registre>/<modele>/.
    Helper partagé entre le scan (diagnostic) et la synchronisation (écriture du metadata)."""
    status: Dict[str, Dict[str, int]] = {}
    if not reg_ocr_dir.exists():
        return status
    for model_dir in reg_ocr_dir.iterdir():
        if model_dir.is_dir():
            n = sum(1 for f in model_dir.iterdir() if f.suffix == '.xml')
            if n:
                status[model_dir.name] = {'pages_done': n, 'pages_total': pages_total}
    return status


def _diagnose_pagination(
    pages: List[str],
    known_pattern: Optional[str] = None,
    known_start: Optional[int] = None,
    known_end: Optional[int] = None,
) -> Dict[str, Any]:
    """Diagnostique la pagination d'un registre à partir des noms d'images.

    Détecte (ou réutilise) un motif `prefix{num}ext`, calcule les bornes, les trous dans
    la numérotation, les doublons et les pages hors-motif. Si un motif est fourni
    (depuis le metadata), il est conservé tel quel ; sinon il est déduit des fichiers."""
    result: Dict[str, Any] = {
        'pattern': known_pattern,
        'start': known_start,
        'end': known_end,
        'gaps': [],
        'duplicates': [],
        'extra_pages': [],
    }
    if not pages:
        return result

    nums: List[int] = []
    if known_pattern:
        before, after = known_pattern.split('{num}', 1)
        main_re = re.compile(r'^' + re.escape(before) + r'(\d+)' + re.escape(after) + r'$')
        for p in pages:
            m = main_re.match(p)
            if m:
                nums.append(int(m.group(1)))
            else:
                result['extra_pages'].append(p)
    else:
        sample = sorted(pages)[0]
        match = re.match(r'^(.+?)(\d+)(\.[^.]+)$', sample)
        if match:
            prefix, _, ext = match.groups()
            result['pattern'] = f"{prefix}{{num}}{ext}"
            for p in pages:
                m = re.match(r'^(.+?)(\d+)(\.[^.]+)$', p)
                if m and m.group(1) == prefix and m.group(3) == ext:
                    nums.append(int(m.group(2)))
                else:
                    result['extra_pages'].append(p)
        # Pas de motif déductible : pattern reste None (anomalie côté appelant)

    if nums:
        if known_pattern is None:
            result['start'] = min(nums)
            result['end'] = max(nums)
        seen: set = set()
        dups: set = set()
        for n in nums:
            if n in seen:
                dups.add(n)
            seen.add(n)
        result['duplicates'] = sorted(dups)
        lo, hi = min(nums), max(nums)
        present = set(nums)
        result['gaps'] = [n for n in range(lo, hi + 1) if n not in present]

    result['extra_pages'].sort(key=lambda n: [int(x) for x in re.findall(r'\d+', n)])
    return result


def _extract_year(name: str) -> Optional[str]:
    """Première année plausible (19xx/20xx) trouvée dans un nom de dossier, sinon None."""
    m = re.search(r'(?:19|20)\d{2}', name)
    return m.group(0) if m else None


def _guess_extra_pagination(extra_pages: List[str], main_pattern: str) -> Optional[str]:
    """Devine un motif pour les pages hors-pattern à partir du motif principal `prefix{num}ext`.
    Ex. main `X_{num}.jpg` + page `X_5_2.jpg` → `X_{num}_{extra_page}.jpg`. None si rien d'exploitable."""
    if '{num}' not in main_pattern:
        return None
    before, after = main_pattern.split('{num}', 1)
    for p in sorted(extra_pages):
        if not p.startswith(before) or (after and not p.endswith(after)):
            continue
        core = p[len(before):len(p) - len(after)] if after else p[len(before):]
        m = re.match(r'^(\d+)(\D+)(.+)$', core)
        if m:
            sep = m.group(2)
            return f"{before}{{num}}{sep}{{extra_page}}{after}"
    return None


def _pattern_to_regex(pattern: str) -> "re.Pattern":
    """Convertit un motif de pagination (`prefix{num}sep{extra_page}ext`) en regex capturante :
    `{num}`→(\\d+), `{extra_page}`→(.+?), le reste échappé. Calqué sur makePatternRegex (frontend)."""
    out: List[str] = []
    for part in re.split(r'(\{num\}|\{extra_page\})', pattern):
        if part == '{num}':
            out.append(r'(\d+)')
        elif part == '{extra_page}':
            out.append(r'(.+?)')
        else:
            out.append(re.escape(part))
    return re.compile('^' + ''.join(out) + '$')


def _page_key(name: str, main_pattern: Optional[str], extra_pattern: Optional[str]) -> Optional[str]:
    """Clé de regroupement d'une page = son numéro principal. Avec motifs : capture `{num}` du
    motif principal, sinon du motif extra. Sans motif : repli `<base>_<num>` (l'extra suit par
    `-` ou `_`). Calqué sur pageFamilyFromList (frontend), pour reconstituer côté serveur la
    même famille (page principale + extras) que la visionneuse."""
    if main_pattern or extra_pattern:
        if main_pattern:
            m = _pattern_to_regex(main_pattern).match(name)
            if m:
                return m.group(1)
        if extra_pattern:
            e = _pattern_to_regex(extra_pattern).match(name)
            if e:
                return e.group(1)
        return None
    m = re.match(r'^(.+?)_(\d+)(?:[-_].+)?(?:\.[^.]+)?$', name)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _parse_model_accuracy(raw: Any) -> Optional[float]:
    """Précision d'un user_metadata Kraken : scalaire, liste de scalaires, ou liste de
    paires [étape, précision] (kraken ≥ 4) — on garde le meilleur checkpoint."""
    try:
        if isinstance(raw, (int, float, str)):
            return float(raw)
        if isinstance(raw, (list, tuple)) and raw:
            values = []
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    values.append(float(item[1]))
                elif isinstance(item, (int, float)):
                    values.append(float(item))
            if values:
                return max(values)
    except (ValueError, TypeError):
        pass
    return None


def _parse_model_type(raw: Any) -> Optional[str]:
    """Mappe le model_type Kraken ('recognition'/'segmentation', str ou liste) vers nos types."""
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    if raw == 'recognition':
        return 'ocr'
    if raw == 'segmentation':
        return 'segmentation'
    return None


def extract_mlmodel_metadata(file_content: bytes, file_name: str) -> Dict[str, Any]:
    """Extrait les métadonnées d'un fichier .mlmodel Kraken OCR"""
    stem = Path(file_name).stem
    metadata: Dict[str, Any] = {
        "id": re.sub(r'[^a-z0-9_-]', '_', stem.lower()),
        "name": stem,
    }

    try:
        from kraken.lib.vgsl import TorchVGSLModel

        with tempfile.NamedTemporaryFile(suffix=".mlmodel", delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            nn = TorchVGSLModel.load_model(tmp_path)
            if getattr(nn, 'name', None):
                metadata["name"] = nn.name
            model_type = _parse_model_type(getattr(nn, 'model_type', None))
            if model_type:
                metadata["type"] = model_type
            if getattr(nn, 'user_metadata', None):
                um = nn.user_metadata
                if um.get('description'):
                    metadata["description"] = um['description']
                if um.get('version'):
                    metadata["version"] = um['version']
                if um.get('author'):
                    metadata["author"] = um['author']
                accuracy = _parse_model_accuracy(um.get('accuracy'))
                if accuracy is not None:
                    metadata["accuracy"] = accuracy
        finally:
            os.unlink(tmp_path)
    except Exception:
        # Fallback : extraire la version depuis le nom de fichier
        version_match = re.search(r'[_-]v?(\d+(?:\.\d+)*)', stem, re.IGNORECASE)
        if version_match:
            metadata["version"] = version_match.group(1)

    return metadata

class ModelsService:
    @staticmethod
    def get_models_dir():
        return Path(DATA_DIR) / "models"

    @staticmethod
    def list_models() -> List[Dict[str, Any]]:
        """Liste tous les modèles .mlmodel disponibles dans le dossier models"""
        models_dir = ModelsService.get_models_dir()
        models = []

        if not models_dir.exists():
            return models

        for item in models_dir.iterdir():
            if not item.is_file():
                continue

            if item.suffix == '.mlmodel':
                model_data = {
                    "id": item.stem,
                    "name": item.stem,
                    "file_path": str(item),
                }
                # Charger les métadonnées associées si elles existent
                metadata_file = models_dir / f"{item.stem}_metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                        metadata = json.load(f)
                        model_data.update(metadata)
                models.append(model_data)

        return models

    @staticmethod
    def add_model(model_data: Dict[str, Any], file_content: Optional[bytes] = None, file_name: Optional[str] = None) -> Dict[str, Any]:
        """Ajoute un nouveau modèle avec ses métadonnées directement dans models/"""
        models_dir = ModelsService.get_models_dir()
        models_dir.mkdir(parents=True, exist_ok=True)

        if file_content and file_name:
            file_path = models_dir / file_name
            with open(file_path, 'wb') as f:
                f.write(file_content)
            model_data["file_path"] = str(file_path)

        model_id = model_data.get("id", Path(file_name).stem if file_name else "unknown")
        metadata_file = models_dir / f"{model_id}_metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(model_data, f, indent=2, ensure_ascii=False)

        return model_data

    @staticmethod
    def delete_model(model_id: str) -> bool:
        """Supprime un modèle et son fichier .mlmodel associé"""
        models_dir = ModelsService.get_models_dir()
        metadata_file = models_dir / f"{model_id}_metadata.json"
        mlmodel_file = models_dir / f"{model_id}.mlmodel"

        if not metadata_file.exists() and not mlmodel_file.exists():
            return False

        if metadata_file.exists():
            metadata_file.unlink()
        if mlmodel_file.exists():
            mlmodel_file.unlink()

        return True

    @staticmethod
    def get_model(model_id: str) -> Optional[Dict[str, Any]]:
        """Récupère un modèle par son ID"""
        models = ModelsService.list_models()
        for m in models:
            if m.get('id') == model_id:
                return m
        return None

    @staticmethod
    def update_model(model_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Met à jour les métadonnées d'un modèle existant"""
        models_dir = ModelsService.get_models_dir()
        metadata_file = models_dir / f"{model_id}_metadata.json"

        if not metadata_file.exists():
            return None

        with open(metadata_file, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)

        for key, value in update_data.items():
            if key != 'id' and value is not None:
                metadata[key] = value

        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        return metadata

class CollectionsService:
    @staticmethod
    def get_collections_dir():
        return Path(DATA_DIR) / "collections"

    @staticmethod
    def list_collections() -> List[Dict[str, Any]]:
        """Liste toutes les collections disponibles.

        Une collection dont le metadata.json est illisible est ignorée plutôt que de faire
        échouer tout le listing : un seul fichier abîmé rendait auparavant l'application
        entièrement muette, alors que l'analyse sait signaler le cas (`metadata_illisible`)."""
        collections_dir = CollectionsService.get_collections_dir()
        collections = []

        if not collections_dir.exists():
            return collections

        for item in collections_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                metadata = _read_json_retry(item / "metadata.json")
                if metadata is not None:
                    metadata['folder_name'] = item.name
                    collections.append(metadata)

        return collections

    @staticmethod
    def get_collection(collection_id: str) -> Optional[Dict[str, Any]]:
        """Récupère une collection spécifique"""
        collections = CollectionsService.list_collections()
        for col in collections:
            if col.get('id') == collection_id or col.get('folder_name') == collection_id:
                return col
        return None

    @staticmethod
    def create_collection(collection_data: Dict[str, Any]) -> Dict[str, Any]:
        """Crée une nouvelle collection"""
        collections_dir = CollectionsService.get_collections_dir()
        collections_dir.mkdir(parents=True, exist_ok=True)

        folder_name = collection_data.get("type")
        collection_dir = collections_dir / folder_name
        collection_dir.mkdir(exist_ok=True)

        # Créer les sous-dossiers
        (collection_dir / "scans").mkdir(exist_ok=True)
        (collection_dir / "ocr").mkdir(exist_ok=True)

        # Sauvegarder les métadonnées
        metadata_file = collection_dir / "metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(collection_data, f, indent=2, ensure_ascii=False)

        collection_data['folder_name'] = folder_name
        return collection_data

    @staticmethod
    def update_collection(collection_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Met à jour les métadonnées d'une collection"""
        collections_dir = CollectionsService.get_collections_dir()

        # Trouver le dossier de la collection
        target_dir = None
        for item in collections_dir.iterdir():
            if item.is_dir() and (item.name == collection_id):
                target_dir = item
                break

        if not target_dir:
            return None

        metadata_file = target_dir / "metadata.json"
        if not metadata_file.exists():
            return None

        with open(metadata_file, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)

        # Mettre à jour uniquement les champs fournis
        for key, value in update_data.items():
            if key not in ('id', 'folder_name'):
                metadata[key] = value

        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        metadata['folder_name'] = target_dir.name
        return metadata

    @staticmethod
    def refresh_registre_ocr_status(collection_id: str, registre_folder: str) -> bool:
        """Recalcule l'`ocr_status` d'un **seul** registre dans le metadata.json de sa collection.

        Version ciblée de `sync_collection_metadata`, qui reconstruit tout (pagination, anomalies,
        tous les registres) et coûte trop cher pour être appelée en cours d'OCR. Ici on se contente
        de recompter les XML : le runner OCR peut donc publier l'avancement registre par registre
        au lieu d'attendre la fin de la tâche.

        Écriture atomique : le fichier est relu en boucle par les autres postes du NAS.
        Retourne False (sans lever) si la collection ou le registre est introuvable."""
        collection_dir = CollectionsService.get_collections_dir() / collection_id
        metadata_file = collection_dir / "metadata.json"
        if not metadata_file.exists():
            return False
        try:
            with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                metadata = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False

        entry = next((r for r in metadata.get('registres') or []
                      if r.get('folder_name') == registre_folder), None)
        if entry is None:
            return False

        pages_total = entry.get('pages_count')
        if not pages_total:
            pages_total = len(RegistresService.list_scan_pages(collection_id, registre_folder))
        status = _count_ocr_xml(collection_dir / "ocr" / registre_folder, pages_total)
        if status:
            entry['ocr_status'] = status
        else:
            entry.pop('ocr_status', None)

        tmp = metadata_file.with_name(metadata_file.name + '.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            os.replace(tmp, metadata_file)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    @staticmethod
    def ocr_counts_from_meta(metadata: Dict[str, Any], model_name: str) -> Dict[str, int]:
        """`{registre_folder: pages_done}` pour ce modèle, à partir d'un metadata de collection
        **déjà lu**. Fonction pure : permet de ne lire le fichier qu'une fois quand plusieurs
        modèles de la même collection sont interrogés.

        Un registre sans page pour ce modèle est **omis**, exactement comme `_count_ocr_xml`
        (`if n:`) et `IndexesService._current_ocr_counts` : les deux sources restent ainsi
        isomorphes, ce dont dépend le comptage des nouveaux registres (`reg not in coverage`)."""
        counts: Dict[str, int] = {}
        for reg in metadata.get('registres') or []:
            folder = reg.get('folder_name')
            if not folder:
                continue
            try:
                n = int(((reg.get('ocr_status') or {}).get(model_name) or {}).get('pages_done') or 0)
            except (AttributeError, TypeError, ValueError):
                continue   # metadata édité à la main : structure ou valeur inattendue
            if n:
                counts[folder] = n
        return counts

    @staticmethod
    def ocr_counts_from_metadata(col_dir: Path, model_name: str) -> Optional[Dict[str, int]]:
        """Comptage OCR **rapide** d'une collection : lecture du seul metadata.json (~0,5 ms),
        contre jusqu'à 1,4 s de scan disque pour `IndexesService._current_ocr_counts`.

        Les compteurs viennent de l'`ocr_status` **publié** par l'application : le runner OCR le
        réécrit registre par registre (`refresh_registre_ocr_status`), et une synchronisation de
        collection le reconstruit intégralement. Il peut donc être en retard si des XML sont
        copiés ou supprimés hors de l'application, si un processus OCR est tué avant son
        nettoyage final, ou si un registre présent dans `ocr/` ne figure pas encore dans
        `registres[]`. La conséquence est **cosmétique** : la génération d'un index ne consulte
        jamais l'`ocr_status`, sa première passe scanne réellement les dossiers — une mise à jour
        n'omet donc jamais de pages, quoi qu'ait affiché le badge de fraîcheur.

        Retourne `None` si le metadata est introuvable ou illisible — à distinguer de `{}`, qui
        signifie « collection lue, aucune page OCR pour ce modèle »."""
        metadata = _read_json_retry(col_dir / "metadata.json")
        if metadata is None:
            return None
        return CollectionsService.ocr_counts_from_meta(metadata, model_name)

    @staticmethod
    def sync_collection_metadata(collection_id: str,
                                 snap_entry: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Wrapper non-streaming de sync_collection_metadata_iter : draine l'itérateur et
        renvoie le metadata reconstruit (ou None si le dossier n'est pas une collection)."""
        result = None
        for event in CollectionsService.sync_collection_metadata_iter(collection_id, snap_entry):
            if event.get('type') == 'result':
                result = event['metadata']
        return result

    @staticmethod
    def sync_collection_metadata_iter(collection_id: str,
                                      snap_entry: Optional[Dict[str, Any]] = None):
        """Variante en flux de sync_collection_metadata : reconstruit la liste des
        registres dans le metadata.json d'une collection, en émettant un événement de
        progression par registre ({'type': 'registre', 'reg_current', 'reg_total'}) puis
        un événement final ({'type': 'result', 'metadata': {...}} ; rien n'est yieldé si
        le dossier n'est pas une collection).

        Si le dossier n'a pas encore de metadata.json (collection déposée à la main dans
        data/collections), un squelette minimal est créé — c'est le point d'entrée voulu :
        déposer le dossier, scanner, synchroniser, puis affiner les métadonnées dans l'UI.

        `snap_entry` : les faits que l'analyse vient de lire (cf. `scan_snapshot`). Fourni,
        il supprime toute lecture du NAS — il ne reste que les écritures. À `None`, la
        fonction sonde le disque elle-même via le même `_probe_collection_iter` : c'est le
        chemin de référence, celui sur lequel on retombe dès qu'un instantané est refusé."""
        collections_dir = CollectionsService.get_collections_dir()
        collection_dir = collections_dir / collection_id

        if not collection_dir.is_dir():
            return None

        # Le sondage émet déjà la progression par registre : on ne la réémet dans la boucle
        # de traitement que s'il n'a pas eu lieu, pour que le compteur défile une seule fois.
        emit_progress = snap_entry is not None
        if snap_entry is None:
            snap_entry = yield from CollectionsService._probe_collection_iter(collection_dir)

        metadata_file = collection_dir / "metadata.json"
        scans_dir = collection_dir / "scans"
        ocr_root = collection_dir / "ocr"

        previous_metadata = snap_entry.get('metadata')
        metadata = copy.deepcopy(previous_metadata)
        if metadata is None:
            if snap_entry.get('metadata_present'):
                # Présent mais illisible : surtout pas de squelette neuf par-dessus, ce
                # serait effacer des métadonnées saisies à la main.
                raise ValueError(f"metadata.json illisible : {collection_id}")
            # Garde-fou : on ne transforme en collection que les dossiers qui ressemblent
            # à une collection (scans/ non vide ou ocr/ présent).
            if not snap_entry.get('scans_non_empty') and not snap_entry.get('has_ocr_folder'):
                return None
            metadata = {
                "id": f"col_{int(time.time())}_{re.sub(r'[^a-z0-9_-]', '_', collection_id.lower())}",
                "type": collection_id,
                "titre": collection_id,
                "periode": ["", ""],
                "lieu": "",
                "commentaire": "",
            }

        ocr_dirs = set(snap_entry.get('ocr_dirs') or [])
        if not snap_entry.get('has_ocr_folder'):
            ocr_root.mkdir(exist_ok=True)
        registres_summary = []
        registres = snap_entry.get('registres') or {}
        scan_reg_names = set(registres)  # registres réellement présents dans scans/
        reg_total = len(registres)
        created_ocr_dirs = []

        for j, (reg_name, reg) in enumerate(registres.items(), 1):
            if emit_progress:
                yield {"type": "registre", "reg_current": j, "reg_total": reg_total}

            reg_metadata_file = scans_dir / reg_name / "metadata.json"
            reg_meta = copy.deepcopy(reg.get('metadata'))
            if reg_meta is None:
                if reg.get('metadata_present'):
                    raise ValueError(f"metadata.json illisible : {collection_id}/{reg_name}")
                reg_meta = {}
                changed = True
            else:
                changed = False

            pages = reg.get('pages') or []

            # Pattern, bornes et pages hors-pattern (motif du metadata réutilisé s'il existe)
            pagination = _diagnose_pagination(
                pages,
                known_pattern=reg_meta.get('pagination', {}).get('pattern'),
                known_start=reg_meta.get('pagination', {}).get('start'),
                known_end=reg_meta.get('pagination', {}).get('end'),
            )

            # Compléter les champs dérivables manquants/vides (sans écraser les saisies)
            if not reg_meta.get('id'):
                reg_meta['id'] = reg_name
                changed = True
            if not reg_meta.get('titre'):
                reg_meta['titre'] = reg_name
                changed = True

            periode = reg_meta.get('periode')
            periode_empty = (
                not periode
                or (isinstance(periode, list) and all(not str(x).strip() for x in periode))
            )
            if periode_empty:
                year = _extract_year(reg_name)
                reg_meta['periode'] = [year, year] if year else ['', '']
                changed = True

            if pagination['pattern'] and not reg_meta.get('pagination', {}).get('pattern'):
                reg_meta['pagination'] = {
                    'pattern': pagination['pattern'],
                    'start': pagination['start'],
                    'end': pagination['end'],
                }
                changed = True

            if (pagination['extra_pages'] and pagination['pattern']
                    and not reg_meta.get('extra_pagination', {}).get('pattern')):
                guess = _guess_extra_pagination(pagination['extra_pages'], pagination['pattern'])
                if guess:
                    reg_meta['extra_pagination'] = {'pattern': guess}
                    changed = True

            if 'stats' not in reg_meta:
                reg_meta['stats'] = {
                    'total_pages': len(pages) - len(pagination['extra_pages']),
                    'total_files': len(pages),
                }
                changed = True

            # `changed` dit qu'un champ a été complété ; encore faut-il que le résultat
            # diffère de ce qui est déjà sur le disque. Plusieurs compléments recalculent à
            # l'identique — une période indéterminable revaut `['', '']` à chaque passage —
            # et refaisaient écrire le fichier à chaque synchronisation pour rien.
            if changed and reg_meta != reg.get('metadata'):
                _write_json_atomic(reg_metadata_file, reg_meta)
                # L'instantané reste vrai après l'écriture : le rapport peut être rejoué
                # ensuite sans retoucher au NAS.
                reg['metadata'] = copy.deepcopy(reg_meta)
                reg['metadata_present'] = True

            # Scaffolder le dossier OCR du registre
            if reg_name not in ocr_dirs:
                (ocr_root / reg_name).mkdir(exist_ok=True)
                created_ocr_dirs.append(reg_name)

            entry = {
                'id': reg_meta.get('id', reg_name),
                'titre': reg_meta.get('titre', reg_name),
                'periode': reg_meta.get('periode', ['', '']),
                'folder_name': reg_name,
                'pages_count': len(pages),
                'pages_pattern': pagination['pattern'],
                'pages_start': pagination['start'],
                'pages_end': pagination['end'],
            }
            if pagination['extra_pages']:
                entry['extra_pages'] = pagination['extra_pages']
            if reg_meta.get('extra_pagination'):
                entry['extra_pagination'] = reg_meta['extra_pagination']
            # Trous et doublons de pagination : persistés pour le badge du listing
            # (le détail page-par-page est recalculé côté client au dépliage).
            if pagination['gaps']:
                entry['pages_gaps'] = pagination['gaps']
            if pagination['duplicates']:
                entry['pages_duplicates'] = pagination['duplicates']

            # Anomalies du registre persistées pour le badge du listing (mêmes
            # codes que le scan), afin de les afficher sans relire le disque.
            reg_anomalies: List[str] = []
            if not pages:
                reg_anomalies.append('registre_vide')
            elif pagination['pattern'] is None:
                reg_anomalies.append('pagination_indetectable')
            if reg_anomalies:
                entry['anomalies'] = reg_anomalies

            # État OCR réel : comptage des XML par modèle relevé lors du parcours
            status = reg.get('ocr_status') or {}
            if status:
                entry['ocr_status'] = status

            registres_summary.append(entry)

        metadata['registres'] = registres_summary

        # Anomalies de niveau collection persistées pour le tableau (mêmes codes que le
        # scan), afin que le listing les affiche sans relire le disque. Les anomalies de
        # niveau registre (vide, pagination indétectable) restent dérivables côté client
        # depuis pages_count/pages_pattern ; registre_absent_disque est réconcilié ici même
        # (la liste est reconstruite depuis le disque).
        col_anomalies: List[str] = []
        for ocr_reg in sorted(ocr_dirs):
            if ocr_reg not in scan_reg_names:
                col_anomalies.append(f'ocr_orphelin:{ocr_reg}')
        for sub in snap_entry.get('stray_dirs_with_images') or []:
            col_anomalies.append(f'registre_hors_scans:{sub}')
        metadata['anomalies'] = col_anomalies

        # Période de la collection déduite des registres si encore vide
        col_periode = metadata.get('periode')
        col_periode_empty = (
            not col_periode
            or (isinstance(col_periode, list) and all(not str(x).strip() for x in col_periode))
        )
        if col_periode_empty:
            years = [
                str(y) for r in registres_summary for y in (r.get('periode') or [])
                if str(y).strip()
            ]
            if years:
                metadata['periode'] = [min(years), max(years)]

        # Réécrit seulement si le contenu bouge. Une collection déjà à jour ne coûte alors
        # plus aucune écriture sur le NAS — le cas de loin le plus fréquent.
        if metadata != previous_metadata:
            _write_json_atomic(metadata_file, metadata)

        snap_entry['metadata'] = copy.deepcopy(metadata)
        snap_entry['metadata_present'] = True
        if created_ocr_dirs:
            snap_entry['ocr_dirs'] = sorted(ocr_dirs | set(created_ocr_dirs))
            snap_entry['has_ocr_folder'] = True

        metadata['folder_name'] = collection_dir.name
        yield {"type": "result", "metadata": metadata}

    # Dossiers à ignorer lors du scan (cachés, Python, système)
    _SCAN_EXCLUDE = re.compile(r'^(\.|__)')

    # Nombre de registres sondés en parallèle. Le parcours n'est pas limité par le CPU mais
    # par les allers-retours SMB : `iterdir`/`stat` relâchent le GIL, donc quelques threads
    # suffisent à masquer la latence réseau. Réglable si le NAS n'aime pas la concurrence.
    _WALK_WORKERS = max(1, int(os.getenv('ARCHIMED_SCAN_WORKERS', '8')))

    @staticmethod
    def _probe_registre(reg_dir: Path, ocr_reg_dir: Optional[Path]) -> Dict[str, Any]:
        """Faits bruts d'un registre, tels qu'ils sont sur le disque. Aucune interprétation
        ici : l'analyse et la synchronisation en tirent chacune leurs propres conclusions."""
        pages = [
            f.name for f in reg_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
        metadata_present, metadata = _read_json_probe(reg_dir / "metadata.json")
        return {
            'pages': pages,
            'metadata': metadata,
            'metadata_present': metadata_present,
            # Dossier OCR absent : inutile d'aller le demander au NAS pour s'entendre dire non.
            'ocr_status': _count_ocr_xml(ocr_reg_dir, len(pages)) if ocr_reg_dir else {},
        }

    @staticmethod
    def _probe_collection_iter(col_dir: Path):
        """Faits bruts d'une collection, lus du disque. Émet {'type': 'registre'} au fil du
        sondage et **retourne** l'entrée d'instantané (`entry = yield from ...`).

        Seul endroit qui lit l'arborescence d'une collection : l'analyse s'en sert pour bâtir
        l'instantané, la synchronisation comme repli quand elle n'en reçoit pas. Deux
        chemins de lecture divergents seraient la façon la plus sûre de faire dire deux
        choses différentes aux deux boutons."""
        scans_dir = col_dir / "scans"
        ocr_dir = col_dir / "ocr"
        metadata_present, metadata = _read_json_probe(col_dir / "metadata.json")

        # Listé avant les registres : le `has_ocr_folder` de chacun s'en déduit, ce qui
        # épargne un `stat` par registre.
        has_ocr_folder = ocr_dir.is_dir()
        ocr_dirs = (
            [d.name for d in sorted(ocr_dir.iterdir()) if d.is_dir()]
            if has_ocr_folder else []
        )
        ocr_names = set(ocr_dirs)

        entry: Dict[str, Any] = {
            'metadata': metadata,
            'metadata_present': metadata_present,
            'has_scans_folder': scans_dir.is_dir(),
            'has_ocr_folder': has_ocr_folder,
            'ocr_dirs': ocr_dirs,
            'stray_dirs_with_images': [],
            # Distinct de `registres` : un scans/ ne contenant que des fichiers en vrac est
            # non vide sans avoir le moindre registre. C'est ce qui autorise l'amorçage.
            'scans_non_empty': False,
            'registres': {},
        }

        if entry['has_scans_folder']:
            scans_entries = sorted(scans_dir.iterdir())
            entry['scans_non_empty'] = bool(scans_entries)
            reg_dirs = [d for d in scans_entries if d.is_dir()]
            reg_total = len(reg_dirs)
            registres: Dict[str, Any] = {}
            if reg_dirs:
                with ThreadPoolExecutor(max_workers=CollectionsService._WALK_WORKERS) as pool:
                    pending = {
                        pool.submit(
                            CollectionsService._probe_registre,
                            d,
                            (ocr_dir / d.name) if d.name in ocr_names else None,
                        ): d.name
                        for d in reg_dirs
                    }
                    for done, future in enumerate(as_completed(pending), 1):
                        # Compteur d'arrivée, pas indice de soumission : les registres se
                        # terminent dans le désordre, la progression doit rester monotone.
                        yield {"type": "registre", "reg_current": done, "reg_total": reg_total}
                        registres[pending[future]] = future.result()
            # Remis dans l'ordre du disque : la sortie ne doit pas dépendre de l'ordre
            # d'arrivée des threads.
            entry['registres'] = {name: registres[name] for name in sorted(registres)}

        for sub in sorted(col_dir.iterdir()):
            if not sub.is_dir() or sub.name in ('scans', 'ocr'):
                continue
            if CollectionsService._SCAN_EXCLUDE.match(sub.name):
                continue
            if any(f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS for f in sub.iterdir()):
                entry['stray_dirs_with_images'].append(sub.name)

        return entry

    @staticmethod
    def _walk_iter():
        """**L'unique parcours disque.** Émet la progression ({'type': 'progress'} par
        collection, {'type': 'registre'} par registre) puis {'type': 'snapshot'}.

        Regroupe ce que l'analyse et la synchronisation lisaient chacune de leur côté, pour
        que l'enchaînement « Analyser » puis « Synchroniser tout » ne touche le NAS qu'une
        fois. Cf. `scan_snapshot` pour la forme et la durée de vie du résultat."""
        started = time.time()
        collections_dir = CollectionsService.get_collections_dir()
        snap: Dict[str, Any] = {
            'token': scan_snapshot.new_token(),
            'created_at': started,
            'data_dir': str(DATA_DIR),
            'collections': {},
        }
        if not collections_dir.exists():
            yield {"type": "snapshot", "snapshot": snap}
            return

        col_dirs = [
            d for d in sorted(collections_dir.iterdir())
            if d.is_dir() and not CollectionsService._SCAN_EXCLUDE.match(d.name)
        ]
        total = len(col_dirs)
        reg_count = 0

        for idx, col_dir in enumerate(col_dirs, 1):
            yield {"type": "progress", "current": idx, "total": total, "name": col_dir.name}
            entry = yield from CollectionsService._probe_collection_iter(col_dir)
            reg_count += len(entry['registres'])
            snap['collections'][col_dir.name] = entry

        log.info(
            f"Parcours disque terminé collections={total} registres={reg_count} "
            f"threads={CollectionsService._WALK_WORKERS} durée={time.time() - started:.1f}s"
        )
        yield {"type": "snapshot", "snapshot": snap}

    @staticmethod
    def build_report(snap: Dict[str, Any]) -> Dict[str, Any]:
        """Rapport de diagnostic déduit de l'instantané. **Fonction pure, aucune I/O.**

        Ce qui permet de rejouer le rapport après une synchronisation sans retoucher au NAS.
        La référence « ce qu'on connaissait déjà » est le metadata.json de chaque collection
        tel que le parcours l'a lu."""
        report_cols = []
        new_collections = 0
        new_registres = 0
        anomalies_count = 0

        for folder_name, entry in snap.get('collections', {}).items():
            metadata = entry.get('metadata')
            is_known = metadata is not None
            if not is_known:
                new_collections += 1

            known_regs = {
                r.get('folder_name') for r in (metadata or {}).get('registres') or []
                if r.get('folder_name')
            }
            ocr_names = set(entry.get('ocr_dirs') or [])
            col_anomalies: List[str] = []

            # metadata.json présent mais illisible
            if entry.get('metadata_present') and metadata is None:
                col_anomalies.append('metadata_illisible')
                anomalies_count += 1

            scan_reg_names = set()
            regs = []
            for reg_name, reg in (entry.get('registres') or {}).items():
                scan_reg_names.add(reg_name)
                reg_known = reg_name in known_regs
                if not reg_known:
                    new_registres += 1

                pages = reg.get('pages') or []
                # Détection fraîche depuis les noms de fichiers : l'analyse dit ce qu'il y a
                # sur le disque, là où la synchronisation respecte le motif déjà enregistré.
                pagination = _diagnose_pagination(pages)

                reg_anomalies: List[str] = []
                if not pages:
                    reg_anomalies.append('registre_vide')
                elif pagination['pattern'] is None:
                    reg_anomalies.append('pagination_indetectable')
                anomalies_count += len(reg_anomalies)

                regs.append({
                    "folder_name": reg_name,
                    "is_known": reg_known,
                    "has_metadata": bool(reg.get('metadata_present')),
                    "has_ocr_folder": reg_name in ocr_names,
                    "pages_count": len(pages),
                    "ocr_status": reg.get('ocr_status') or {},
                    "anomalies": reg_anomalies,
                    "pagination": pagination,
                })

            # Registres listés dans le metadata mais absents du disque
            for missing in sorted(known_regs - scan_reg_names):
                regs.append({
                    "folder_name": missing,
                    "is_known": True,
                    "has_metadata": False,
                    "has_ocr_folder": missing in ocr_names,
                    "pages_count": 0,
                    "ocr_status": {},
                    "anomalies": ['registre_absent_disque'],
                    "pagination": None,
                })
                anomalies_count += 1

            # Dossiers ocr/ orphelins (sans scans/<reg> correspondant)
            for ocr_reg in entry.get('ocr_dirs') or []:
                if ocr_reg not in scan_reg_names:
                    col_anomalies.append(f'ocr_orphelin:{ocr_reg}')
                    anomalies_count += 1

            # Sous-dossiers déposés directement sous la collection (hors scans/ et ocr/)
            for sub in entry.get('stray_dirs_with_images') or []:
                col_anomalies.append(f'registre_hors_scans:{sub}')
                anomalies_count += 1

            report_cols.append({
                "folder_name": folder_name,
                "is_known": is_known,
                "has_metadata": bool(entry.get('metadata_present')),
                "has_scans_folder": bool(entry.get('has_scans_folder')),
                "has_ocr_folder": bool(entry.get('has_ocr_folder')),
                "anomalies": col_anomalies,
                "registres": regs,
            })

        return {
            "collections": report_cols,
            "new_collections": new_collections,
            "new_registres": new_registres,
            "anomalies_count": anomalies_count,
            # Rendu au client, qui le représente à la synchronisation pour qu'elle reparte
            # de ce parcours-ci au lieu d'en refaire un.
            "token": snap.get('token', ''),
        }

    @staticmethod
    def scan_filesystem() -> Dict[str, Any]:
        """Diagnostic en lecture seule du dossier collections : présence des fichiers,
        couverture OCR par modèle, état de pagination et anomalies. N'écrit rien sur le NAS
        (l'instantané du parcours, lui, est déposé en cache local)."""
        report: Dict[str, Any] = {"collections": [], "new_collections": 0, "new_registres": 0,
                                  "anomalies_count": 0, "token": ""}
        for event in CollectionsService.scan_filesystem_iter():
            if event.get('type') == 'report':
                report = event['report']
        return report

    @staticmethod
    def scan_filesystem_iter():
        """Variante en flux de scan_filesystem : émet un événement de progression par
        collection ({'type': 'progress', 'current', 'total', 'name'}) puis un événement
        final ({'type': 'report', 'report': {...}}). Lecture seule, même diagnostic."""
        snap = None
        for event in CollectionsService._walk_iter():
            if event.get('type') == 'snapshot':
                snap = event['snapshot']
            else:
                yield event
        scan_snapshot.save(snap)
        yield {"type": "report", "report": CollectionsService.build_report(snap)}

    @staticmethod
    def sync_all_collections(token: Optional[str] = None) -> List[Dict[str, Any]]:
        """Synchronise tous les dossiers de data/collections ; les dossiers sans
        metadata.json sont bootstrappés par sync_collection_metadata (s'ils ont des scans)."""
        results: List[Dict[str, Any]] = []
        for event in CollectionsService.sync_all_collections_iter(token):
            if event.get('type') == 'done':
                results = event['results']
        return results

    @staticmethod
    def sync_all_collections_iter(token: Optional[str] = None):
        """Variante en flux de sync_all_collections : émet un événement de progression par
        collection ({'type': 'progress', 'current', 'total', 'name'}) puis un événement
        final ({'type': 'done', 'results': [...]}).

        `token` est celui rendu par l'analyse. S'il désigne un instantané encore valable, la
        synchronisation repart de ce que l'analyse a déjà lu et ne fait plus que des
        écritures. Sinon elle reparcourt le disque, exactement comme avant l'instantané."""
        started = time.time()
        collections_dir = CollectionsService.get_collections_dir()
        if not collections_dir.exists():
            yield {"type": "done", "results": []}
            return

        snap = scan_snapshot.load(token, DATA_DIR)
        log.info(f"Synchronisation démarrée instantané={'réutilisé' if snap else 'reconstruit'}")

        col_dirs = [
            d for d in sorted(collections_dir.iterdir())
            if d.is_dir() and not CollectionsService._SCAN_EXCLUDE.match(d.name)
        ]
        total = len(col_dirs)

        results: List[Dict[str, Any]] = []
        for idx, col_dir in enumerate(col_dirs, 1):
            yield {"type": "progress", "current": idx, "total": total, "name": col_dir.name}
            # Une collection apparue depuis l'analyse n'est pas dans l'instantané : elle sera
            # sondée à la volée, sans invalider le reste.
            snap_entry = (snap.get('collections') or {}).get(col_dir.name) if snap else None
            # Relaie les événements 'registre' de la collection et capture son 'result'.
            for event in CollectionsService.sync_collection_metadata_iter(col_dir.name, snap_entry):
                if event.get('type') == 'result':
                    if event['metadata']:
                        results.append(event['metadata'])
                else:
                    yield event

        # L'instantané a été réaligné sur ce qui vient d'être écrit : on le persiste pour que
        # le rapport puisse être rejoué ensuite sans retoucher au NAS.
        if snap:
            scan_snapshot.save(snap)

        log.info(f"Synchronisation terminée collections={total} durée={time.time() - started:.1f}s")
        yield {"type": "done", "results": results}

    @staticmethod
    def sync_collection_with_token_iter(collection_id: str, token: Optional[str] = None):
        """Synchronise **une** collection en repartant de l'instantané `token` s'il est
        encore valable, puis le réaligne sur ce qui vient d'être écrit — de quoi rejouer
        ensuite le rapport d'analyse sans relire le NAS."""
        snap = scan_snapshot.load(token, DATA_DIR)
        snap_entry = (snap.get('collections') or {}).get(collection_id) if snap else None
        for event in CollectionsService.sync_collection_metadata_iter(collection_id, snap_entry):
            yield event
        if snap and snap_entry is not None:
            scan_snapshot.save(snap)

    @staticmethod
    def report_from_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
        """Rapport d'analyse rejoué depuis l'instantané, **sans aucun accès au NAS**.
        `None` si l'instantané n'est plus utilisable : l'appelant doit relancer une analyse."""
        snap = scan_snapshot.load(token, DATA_DIR)
        return CollectionsService.build_report(snap) if snap else None

class RegistresService:
    @staticmethod
    def list_registres(collection_id: str) -> List[Dict[str, Any]]:
        """Liste tous les registres d'une collection"""
        collections_dir = Path(DATA_DIR) / "collections" / collection_id / "scans"
        registres = []

        if not collections_dir.exists():
            return registres

        for item in collections_dir.iterdir():
            if item.is_dir():
                metadata_file = item / "metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                        metadata = json.load(f)
                        metadata['folder_name'] = item.name
                        metadata['collection_id'] = collection_id
                        registres.append(metadata)

        return registres

    @staticmethod
    def get_registre(collection_id: str, registre_id: str) -> Optional[Dict[str, Any]]:
        """Récupère un registre spécifique"""
        registres = RegistresService.list_registres(collection_id)
        for reg in registres:
            if reg.get('id') == registre_id or reg.get('folder_name') == registre_id:
                return reg
        return None

    @staticmethod
    def list_scan_pages(collection_id: str, registre_folder: str) -> List[str]:
        """Liste les fichiers image (scans) d'un registre, triés par numéro de page"""
        scans_dir = Path(DATA_DIR) / "collections" / collection_id / "scans" / registre_folder
        if not scans_dir.exists():
            return []

        image_extensions = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}
        pages = [
            f.name for f in scans_dir.iterdir()
            if f.is_file() and f.suffix.lower() in image_extensions
        ]

        def sort_key(name: str):
            match = re.search(r'(\d+)\.[^.]+$', name)
            return int(match.group(1)) if match else 0

        pages.sort(key=sort_key)
        return pages

    @staticmethod
    def list_page_transcriptions(collection_id: str, registre_folder: str) -> Dict[str, List[str]]:
        """Pour chaque page transcrite, liste des modèles ayant produit un XML.

        Renvoie un dict {stem du fichier image -> [modèles]}, où le stem est le nom de
        base (sans extension) partagé entre l'image scan et son XML PAGE-XML."""
        ocr_dir = Path(DATA_DIR) / "collections" / collection_id / "ocr" / registre_folder
        result: Dict[str, List[str]] = {}
        if not ocr_dir.exists():
            return result
        for model_dir in sorted(ocr_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            for f in model_dir.iterdir():
                if f.suffix == '.xml':
                    result.setdefault(f.stem, []).append(model_dir.name)
        return result

    @staticmethod
    def create_registre(collection_id: str, registre_data: Dict[str, Any]) -> Dict[str, Any]:
        """Crée un nouveau registre"""
        scans_dir = Path(DATA_DIR) / "collections" / collection_id / "scans"
        scans_dir.mkdir(parents=True, exist_ok=True)

        folder_name = registre_data.get("id", "").replace("reg_", "")
        registre_dir = scans_dir / folder_name
        registre_dir.mkdir(exist_ok=True)

        metadata_file = registre_dir / "metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(registre_data, f, indent=2, ensure_ascii=False)

        registre_data['folder_name'] = folder_name
        registre_data['collection_id'] = collection_id
        return registre_data

    @staticmethod
    def update_registre(collection_id: str, registre_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Met à jour les métadonnées d'un registre"""
        scans_dir = Path(DATA_DIR) / "collections" / collection_id / "scans"
        if not scans_dir.exists():
            return None

        registre_dir = None
        current_meta: Dict[str, Any] = {}
        for item in scans_dir.iterdir():
            if not item.is_dir():
                continue
            metadata_file = item / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                    meta = json.load(f)
                if meta.get('id') == registre_id or item.name == registre_id:
                    registre_dir = item
                    current_meta = meta
                    break
            elif item.name == registre_id:
                # Dossier sans metadata.json : on crée à la volée
                registre_dir = item
                current_meta = {'id': item.name}
                break

        if not registre_dir:
            return None

        current_meta.update({k: v for k, v in update_data.items() if v is not None})

        metadata_file = registre_dir / "metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(current_meta, f, indent=2, ensure_ascii=False)

        # Répercuter les changements dans le metadata.json de la collection
        CollectionsService.sync_collection_metadata(collection_id)

        current_meta['folder_name'] = registre_dir.name
        current_meta['collection_id'] = collection_id
        return current_meta

class IndexesService:
    @staticmethod
    def get_indexes_dir():
        return Path(DATA_DIR) / "indexes"

    @staticmethod
    def _read_index_meta(metadata_file: Path, retries: int = 3, delay: float = 0.04) -> Optional[Dict[str, Any]]:
        """Lit un metadata.json d'index en tolérant une écriture concurrente (cf.
        `_read_json_retry`) : la génération le réécrit à chaque registre pour la progression."""
        return _read_json_retry(metadata_file, retries, delay)

    @staticmethod
    def list_indexes() -> List[Dict[str, Any]]:
        indexes_dir = IndexesService.get_indexes_dir()
        indexes = []
        if not indexes_dir.exists():
            return indexes
        for item in indexes_dir.iterdir():
            if item.is_dir():
                metadata_file = item / "metadata.json"
                if metadata_file.exists():
                    meta = IndexesService._read_index_meta(metadata_file)
                    # Un index momentanément illisible (écriture en cours) est ignoré plutôt
                    # que de faire échouer toute la liste (sinon HTTP 500 pendant la génération).
                    if meta is not None:
                        indexes.append(meta)
        return indexes

    @staticmethod
    def get_index(index_id: str) -> Optional[Dict[str, Any]]:
        metadata_file = IndexesService.get_indexes_dir() / index_id / "metadata.json"
        if not metadata_file.exists():
            return None
        return IndexesService._read_index_meta(metadata_file)

    @staticmethod
    def _resolve_collection_folder(collection_id: str) -> Optional[Path]:
        """Résout l'id ou le folder_name d'une collection vers son chemin réel."""
        collections_dir = Path(DATA_DIR) / "collections"
        # Accès direct (si collection_id == folder_name)
        direct = collections_dir / collection_id
        if direct.is_dir():
            return direct
        # Recherche par id dans les metadata.json
        for item in collections_dir.iterdir():
            if not item.is_dir() or item.name.startswith('.'):
                continue
            metadata_file = item / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                    meta = json.load(f)
                if meta.get('id') == collection_id:
                    return item
        return None

    @staticmethod
    def list_available_models(collection_id: str) -> List[str]:
        """Liste les modèles OCR disponibles pour une collection en scannant le dossier ocr/."""
        col_dir = IndexesService._resolve_collection_folder(collection_id)
        if col_dir is None:
            return []
        ocr_dir = col_dir / "ocr"
        if not ocr_dir.exists():
            return []
        models: set = set()
        for reg_dir in ocr_dir.iterdir():
            if reg_dir.is_dir():
                for model_dir in reg_dir.iterdir():
                    if model_dir.is_dir():
                        models.add(model_dir.name)
        return sorted(models)

    @staticmethod
    def preview_sources(sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aperçu avant génération : registres/pages par source, totaux et avertissements
        (sources identiques, ou même registre couvert par plusieurs modèles → pages
        comptées pour chacun)."""
        resolved = IndexesService._resolve_sources(sources)
        per_source: List[Dict[str, Any]] = []
        pair_counts: Dict[tuple, int] = {}      # (collection_id, model) identiques
        registre_cover: Dict[tuple, int] = {}   # (collection_folder, registre) → nb de modèles
        total_registres = total_pages = 0
        for src in resolved:
            regs = IndexesService.list_sources_registres([src])
            pages = sum(r['pages'] for r in regs)
            total_registres += len(regs)
            total_pages += pages
            pair_counts[(src['collection_id'], src['model_name'])] = \
                pair_counts.get((src['collection_id'], src['model_name']), 0) + 1
            for r in regs:
                pair = (src['collection_folder'], r['registre'])
                registre_cover[pair] = registre_cover.get(pair, 0) + 1
            per_source.append({
                "collection_id": src['collection_id'],
                "collection_titre": src['collection_titre'],
                "collection_folder": src['collection_folder'],
                "model_name": src['model_name'],
                "registres": len(regs),
                "pages": pages,
            })
        warnings: List[str] = []
        if any(c > 1 for c in pair_counts.values()):
            warnings.append("Des sources identiques (même collection et modèle) sont sélectionnées plusieurs fois.")
        dup_registres = sum(1 for c in registre_cover.values() if c > 1)
        if dup_registres:
            warnings.append(
                f"{dup_registres} registre(s) sont couverts par plusieurs modèles : "
                "leurs pages seront indexées (et comptées) pour chaque modèle.")
        return {
            "sources": per_source,
            "totals": {"registres": total_registres, "pages": total_pages, "sources": len(resolved)},
            "duplicate_registres": dup_registres,
            "warnings": warnings,
        }

    # Séparateur de namespace des pages/registres par source (URL- & JSON-safe).
    SOURCE_SEP = "::"

    # Version du bloc `index_state` (metadata.json) qui permet la mise à jour incrémentale.
    # Un index dont l'état porte une autre version est reconstruit intégralement.
    INDEX_STATE_VERSION = 1

    @staticmethod
    def _slugify(name: str) -> str:
        """Slug ASCII sûr pour un nom de fichier/dossier. 'index' si vide."""
        import unicodedata
        s = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode('ascii')
        s = re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower()
        return s or 'index'

    @staticmethod
    def _new_index_id(name: str) -> str:
        """Id interne unique et horodaté : idx_<AAAAMMJJhhmmss>_<slug>."""
        from datetime import datetime
        base = f"idx_{datetime.now().strftime('%Y%m%d%H%M%S')}_{IndexesService._slugify(name)}"
        idx_dir = IndexesService.get_indexes_dir()
        candidate, i = base, 2
        while (idx_dir / candidate).exists():
            candidate = f"{base}-{i}"
            i += 1
        return candidate

    @staticmethod
    def _collection_titre(col_dir: Optional[Path], fallback: str) -> Optional[str]:
        if not col_dir:
            return None
        mf = col_dir / "metadata.json"
        if mf.exists():
            try:
                with open(mf, 'r', encoding='utf-8-sig') as f:
                    return json.load(f).get('titre') or fallback
            except (OSError, json.JSONDecodeError):
                pass
        return fallback

    @staticmethod
    def _resolve_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """[{collection_id, model_name}] → [{key, collection_id, collection_folder,
        collection_titre, model_name}] (clé de namespace s0, s1, …)."""
        out: List[Dict[str, Any]] = []
        for i, s in enumerate(sources):
            cid = s['collection_id']
            col_dir = IndexesService._resolve_collection_folder(cid)
            folder = col_dir.name if col_dir else cid
            out.append({
                "key": f"s{i}",
                "collection_id": cid,
                "collection_folder": folder,
                "collection_titre": IndexesService._collection_titre(col_dir, folder),
                "model_name": s['model_name'],
            })
        return out

    @staticmethod
    def _source_label(reg_folder: str, src: Dict[str, Any]) -> str:
        """Libellé lisible d'un registre pour la trace de tâche / progression."""
        col = src.get('collection_titre') or src.get('collection_folder')
        return f"{col} · {src['model_name']} · {reg_folder}"

    @staticmethod
    def list_sources_registres(sources_info: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Registres à indexer (tous sources), dans l'ordre de traitement, avec nb de XML.
        `name` = libellé lisible ; `key`/`registre` servent au namespacing."""
        out: List[Dict[str, Any]] = []
        for src in sources_info:
            col_dir = IndexesService._resolve_collection_folder(src['collection_id'])
            if not col_dir:
                continue
            ocr_dir = col_dir / "ocr"
            if not ocr_dir.exists():
                continue
            for reg_dir in sorted(ocr_dir.iterdir()):
                if not reg_dir.is_dir():
                    continue
                model_dir = reg_dir / src['model_name']
                if not model_dir.exists():
                    continue
                n = sum(1 for f in model_dir.iterdir() if f.suffix == '.xml')
                if n:
                    out.append({
                        "name": IndexesService._source_label(reg_dir.name, src),
                        "key": src['key'],
                        "registre": reg_dir.name,
                        "pages": n,
                    })
        return out

    @staticmethod
    def init_index_new(index_id: str, name: str, sources_info: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Matérialise un nouvel index (dossier + metadata) avec statut 'generating'."""
        from datetime import datetime
        index_dir = IndexesService.get_indexes_dir() / index_id
        index_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": index_id,
            "name": name,
            "sources": sources_info,
            "status": "generating",
            "created_at": datetime.now().isoformat(),
            "stats": None,
            "build": {"status": "generating", "progress": None},
        }
        IndexesService._save_index_meta(index_id, metadata)
        return metadata

    @staticmethod
    def _save_index_meta(index_id: str, metadata: Dict[str, Any]) -> bool:
        """Écrit metadata.json **atomiquement** (tmp + os.replace).

        La progression est réécrite très souvent pendant la génération, pendant que la liste
        (`list_indexes`) relit ces fichiers toutes les 2 s : une écriture en place (open 'w')
        exposerait un fichier tronqué. Le motif tmp + `os.replace` garantit qu'un lecteur voit
        toujours l'ancien OU le nouveau fichier complet (cf. `_save_checkpoint`)."""
        metadata_file = IndexesService.get_indexes_dir() / index_id / "metadata.json"
        tmp = metadata_file.with_name(metadata_file.name + '.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            # os.replace est atomique sur le même volume. Sous Windows, il peut lever
            # PermissionError si un lecteur tient la cible ouverte au même instant : on
            # réessaie brièvement (fenêtre très courte, jamais de contenu partiel).
            for attempt in range(3):
                try:
                    os.replace(tmp, metadata_file)
                    return True
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.04)
            return True
        except (FileNotFoundError, OSError):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def update_index_meta(index_id: str, *, name: Optional[str] = None,
                          sources: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
        """Édition d'un index : renomme et/ou remplace ses sources (résolues)."""
        meta = IndexesService.get_index(index_id)
        if not meta:
            return None
        if name is not None:
            meta['name'] = name
        if sources is not None:
            meta['sources'] = IndexesService._resolve_sources(sources)
            meta.pop('collection_id', None)   # quitte le format legacy
            meta.pop('model_name', None)
        IndexesService._save_index_meta(index_id, meta)
        return meta

    @staticmethod
    def mark_rebuild(index_id: str, sources_info: Optional[List[Dict[str, Any]]] = None) -> None:
        """Marque un index comme en cours de reconstruction sans toucher à l'index existant
        (il reste 'ready' et consultable jusqu'au basculement atomique).

        `sources_info` est persisté dans le metadata pour que la génération dispose des
        sources — indispensable notamment pour convertir un ancien index mono-source
        (sans `sources`) au format multi-sources."""
        meta = IndexesService.get_index(index_id)
        if not meta:
            return
        if sources_info is not None:
            meta['sources'] = sources_info
            meta.pop('collection_id', None)   # quitte le format legacy
            meta.pop('model_name', None)
        meta['build'] = {"status": "generating", "progress": None}
        IndexesService._save_index_meta(index_id, meta)

    @staticmethod
    def clear_build(index_id: str) -> None:
        """Abandonne une reconstruction : retire le marqueur `build`, le staging et le
        checkpoint. L'index précédent (index.json) reste intact."""
        meta = IndexesService.get_index(index_id)
        if meta is not None:
            meta.pop('build', None)
            IndexesService._save_index_meta(index_id, meta)
        index_dir = IndexesService.get_indexes_dir() / index_id
        for f in ("index.json.tmp", "checkpoint.json"):
            try:
                (index_dir / f).unlink(missing_ok=True)
            except OSError:
                pass

    # ── Mise à jour incrémentale : empreintes et purge ────────────────────────
    @staticmethod
    def _scan_registre_xml(model_dir: Path) -> tuple:
        """Liste les XML d'un registre et calcule son empreinte.
        Retourne `(xml_files triés, {"pages": n, "sig": "<hex>"})`.

        Un seul `os.scandir` : sous Windows, `DirEntry.stat()` est servi par l'entrée de
        répertoire elle-même, donc lister 40 000 fichiers avec leur taille/mtime ne coûte pas
        plus cher que de les lister — y compris sur un partage réseau, où un `stat()` par
        fichier serait rédhibitoire.

        Le filtre est insensible à la casse comme l'était `Path.glob("*.xml")` sous Windows :
        un `.XML` deviendrait invisible avec une comparaison stricte."""
        import hashlib
        entries: List[tuple] = []   # (nom, taille, mtime)
        try:
            with os.scandir(model_dir) as it:
                for e in it:
                    if not e.name.lower().endswith('.xml'):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    entries.append((e.name, st.st_size, int(st.st_mtime)))
        except OSError:
            return [], {"pages": 0, "sig": ""}

        entries.sort()   # tri par nom : même ordre que l'ancien sorted(glob("*.xml"))
        h = hashlib.blake2b(digest_size=8)
        for name, size, mtime in entries:
            h.update(f"{name}|{size}|{mtime}\n".encode('utf-8'))
        xml_files = [model_dir / name for name, _s, _m in entries]
        return xml_files, {"pages": len(entries), "sig": h.hexdigest()}

    @staticmethod
    def _sources_signature(sources_info: List[Dict[str, Any]]) -> str:
        """Empreinte du mapping `clé de source → (collection, modèle)`.

        Toute édition des sources décale les clés s0/s1/… et invalide donc les préfixes de page
        (`s0::…`) de l'index existant : l'incrémental doit être refusé dans ce cas.
        Volontairement indépendante de `collection_folder`/`collection_titre` : renommer une
        collection ne change ni les noms de page ni les dossiers de registres.

        Un `hash()` intégré ne conviendrait pas : il est randomisé par PYTHONHASHSEED et donc
        instable d'un lancement à l'autre."""
        import hashlib
        h = hashlib.blake2b(digest_size=8)
        for src in sources_info:
            h.update(f"{src.get('key')}|{src.get('collection_id')}|{src.get('model_name')}\n".encode('utf-8'))
        return h.hexdigest()

    @staticmethod
    def _load_incremental_state(index_dir: Path, metadata: Dict[str, Any],
                                sources_sig: str) -> Optional[Dict[str, Dict]]:
        """État des registres du build précédent, ou `None` si l'incrémental est impossible
        (→ reconstruction complète).

        Refusé si : pas d'`index.json`, pas d'`index_state` (index construit avant l'arrivée du
        suivi incrémental, dont tous les index legacy mono-source), version d'état inconnue, ou
        sources différentes de celles de l'index existant."""
        if not (index_dir / "index.json").exists():
            return None
        state = metadata.get('index_state')
        if not isinstance(state, dict):
            return None
        if state.get('version') != IndexesService.INDEX_STATE_VERSION:
            return None
        if state.get('sources_sig') != sources_sig:
            return None
        registres = state.get('registres')
        if not isinstance(registres, dict):
            return None
        return registres

    @staticmethod
    def _load_existing_words(index_file: Path,
                             sources_info: List[Dict[str, Any]]) -> Optional[Dict[str, List[str]]]:
        """Charge le dictionnaire de mots de l'index existant pour repartir de lui.
        Retourne `None` si le fichier est inutilisable (→ reconstruction complète).

        Second filet après `_load_incremental_state` : on vérifie que le bloc `sources` du
        fichier décrit bien les mêmes sources, ce qui écarte un index au format legacy (pages
        sans préfixe `sX::`, non purgeables par préfixe de registre) ou désynchronisé de son
        metadata."""
        try:
            with open(index_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, MemoryError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        words = data.get('words')
        if not isinstance(words, dict):
            return None   # ancien format « dict plat » : pas de bloc sources exploitable
        block = data.get('sources')
        if not isinstance(block, dict):
            return None
        if set(block.keys()) != {s['key'] for s in sources_info}:
            return None
        for src in sources_info:
            if block[src['key']].get('model_name') != src['model_name']:
                return None
        data = None   # libère le wrapper ; seul `words` (volumineux) est conservé
        return words

    @staticmethod
    def _expand_prefix_conflicts(remove_keys: set, all_keys) -> set:
        """Étend un jeu de registres à purger aux registres dont la clé en est un dérivé.

        La purge s'appuie sur le préfixe `<reg_key>_` des noms de page. Si `s0::A` est purgé et
        que `s0::A_B` existe, les pages `s0::A_B_12` seraient effacées alors que `s0::A_B`
        resterait marqué « déjà indexé » — perte de mots silencieuse. On purge donc aussi les
        registres emboîtés (et, transitivement, ceux qui s'emboîtent dans eux), comme le reste du
        code qui résout une page vers son registre par le plus long préfixe."""
        keys = list(all_keys)
        out = set(remove_keys)
        pending = list(out)
        while pending:
            rk = pending.pop()
            for k in keys:
                if k not in out and k.startswith(rk + '_'):
                    out.add(k)
                    pending.append(k)
        return out

    @staticmethod
    def _purge_registres(words: Dict[str, List[str]], reg_keys) -> int:
        """Retire des occurrences tous les mots appartenant aux registres donnés.
        Retourne le nombre d'occurrences retirées.

        Une page du registre `sX::REG` s'appelle toujours `sX::REG_<n>` : un unique
        `str.startswith(tuple)` (boucle en C) par occurrence suffit. Les mots dont la liste se
        vide sont supprimés pour que `total_unique_words` reste exact."""
        prefixes = tuple(f"{rk}_" for rk in reg_keys)
        if not prefixes:
            return 0
        removed = 0
        empties: List[str] = []
        for word, occs in words.items():   # mutation des valeurs seulement : taille constante
            kept = [o for o in occs if not o.startswith(prefixes)]
            if len(kept) != len(occs):
                removed += len(occs) - len(kept)
                if kept:
                    words[word] = kept
                else:
                    empties.append(word)
        for word in empties:
            del words[word]
        return removed

    @staticmethod
    def generate_index(index_id: str, on_progress=None,
                       should_cancel=None, should_pause=None,
                       *, full: bool = False, on_plan=None) -> str:
        """Construit l'index multi-sources à partir des XML OCR.
        Retourne 'done' | 'cancelled' | 'paused'.

        Les pages et les registres sont **namespacés** par la clé de source ('s0::…')
        pour éviter les collisions entre collections. L'index.json final est écrit dans un
        **staging** puis basculé atomiquement (os.replace) : l'ancien index reste consultable
        pendant toute une reconstruction.

        Par défaut la génération est **incrémentale** : l'index existant est rechargé, les
        registres nouveaux ou modifiés (empreinte `index_state`) en sont purgés puis réindexés,
        et les autres sont conservés tels quels. `full=True` réindexe tout (échappatoire quand
        un XML a été modifié sans changer ni sa taille ni sa date). Comme `index.json` n'est
        remplacé qu'à la toute fin, ce préchargement est idempotent : un run interrompu repart
        proprement du même point de départ.

        ⚠️ Un registre absent du disque est considéré comme supprimé et purgé de l'index. Si une
        collection est momentanément indisponible (partage réseau déconnecté), ses pages sont
        donc retirées — comme le ferait une reconstruction complète.

        `on_progress(processed, total, current, page)` rapporte l'avancement (registre courant et
        page en cours de lecture),
        `on_plan(skipped_labels, base_pages)` annonce les registres conservés et leurs pages, hors
        de la progression publiée (qui ne décrit que le travail de ce run),
        `should_cancel()` arrête définitivement, `should_pause()` met en pause via checkpoint."""
        SEP = IndexesService.SOURCE_SEP
        index_dir = IndexesService.get_indexes_dir() / index_id
        metadata_file = index_dir / "metadata.json"
        staging_file = index_dir / "index.json.tmp"
        checkpoint_file = index_dir / "checkpoint.json"
        final_file = index_dir / "index.json"

        with open(metadata_file, 'r', encoding='utf-8-sig') as f:
            metadata = json.load(f)

        # Reconstruction (index déjà prêt) vs première génération : détermine si l'on peut
        # conserver l'index précédent en cas d'échec/annulation.
        is_new = not final_file.exists()
        sources_info: List[Dict[str, Any]] = metadata.get('sources') or []

        def _save_meta() -> bool:
            return IndexesService._save_index_meta(index_id, metadata)

        def _set_progress(processed: int, total: int, current: Optional[str],
                          page: Optional[str] = None) -> None:
            metadata.setdefault('build', {})['status'] = 'generating'
            metadata['build']['progress'] = {
                "processed": processed, "total": total, "current_registre": current,
                "current_page": page,
            }

        def _cancelled() -> bool:
            return (should_cancel is not None and should_cancel()) or not index_dir.exists()

        def _paused() -> bool:
            return should_pause is not None and should_pause()

        def _report(processed: int, total: int, current: Optional[str],
                    page: Optional[str] = None) -> None:
            if on_progress is not None:
                try:
                    on_progress(processed, total, current, page)
                except Exception:
                    pass

        last_publish = 0.0
        # Pages acquises hors de ce run (registres conservés par la mise à jour incrémentale).
        # Elles sortent de la progression publiée : une mise à jour de 30 pages sur un index de
        # 25 000 doit afficher « 12 / 30 », pas « 24 982 / 25 000 » — barre déjà pleine et ETA
        # absurde. Les appelants raisonnent en compteurs absolus, `_publish` retranche la base.
        base = 0

        def _publish(processed: int, total: int, current: Optional[str],
                     page: Optional[str] = None, *, force: bool = False) -> bool:
            """Publie l'avancement (metadata.json + tâche). Retourne False si l'index a disparu,
            ce que les appelants traitent comme une annulation.

            Throttlé à ~1/s : la boucle interne appelle à chaque page, et `_save_meta` réécrit
            tout le metadata (la liste, elle, ne le relit que toutes les 2 s). `force=True` aux
            bornes de registre, où la publication doit être exacte."""
            nonlocal last_publish
            now = time.monotonic()
            if not force and now - last_publish < 1.0:
                return True
            last_publish = now
            _set_progress(processed - base, total - base, current, page)
            if not _save_meta():
                return False
            _report(processed - base, total - base, current, page)
            return True

        # État, repris d'un checkpoint si l'indexation avait été mise en pause.
        mots_uniques: Dict[str, List[str]] = {}
        total_words = 0
        done_registres: set = set()          # clés "key::registre"
        done_state: Dict[str, Dict] = {}     # empreintes des registres réellement indexés
        resumed = False
        mode = 'full'
        cp_skipped: Optional[set] = None     # base du run d'origine, à la reprise
        if checkpoint_file.exists():
            try:
                with open(checkpoint_file, 'r', encoding='utf-8-sig') as f:
                    cp = json.load(f)
                # Un checkpoint sans 'mode' vient d'une version antérieure : traité comme 'full'.
                cp_mode = cp.get('mode', 'full')
                # Une reconstruction complète explicite ne doit pas hériter des registres qu'une
                # mise à jour incrémentale interrompue avait marqués « déjà indexés » : elle serait
                # silencieusement dégradée en incrémental (mark_rebuild ne purge pas le checkpoint).
                if not (full and cp_mode == 'incremental'):
                    mots_uniques = cp.get('words', {})
                    total_words = cp.get('total_words', 0)
                    done_registres = set(cp.get('done_registres', []))
                    done_state = cp.get('done_state', {}) or {}
                    # Base du run d'origine : la reprise doit repartir de la même barre de
                    # progression (même total), pas d'un total rétréci au travail restant.
                    # Absente d'un checkpoint hérité → on retombe sur `done_registres`.
                    if cp.get('skipped') is not None:
                        cp_skipped = set(cp['skipped'])
                    resumed = True
                    mode = cp_mode
            except (OSError, json.JSONDecodeError):
                pass   # checkpoint illisible : on repart de zéro (reconstruction complète)

        base_registres: set = set()   # registres conservés : la base de progression de ce run

        def _save_checkpoint() -> None:
            # Écriture atomique (tmp + os.replace) : un crash pendant l'écriture ne peut pas
            # laisser un checkpoint tronqué (la reprise après interruption s'y appuie).
            tmp = checkpoint_file.with_name(checkpoint_file.name + '.tmp')
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump({"mode": mode, "words": mots_uniques, "total_words": total_words,
                               "done_registres": sorted(done_registres),
                               "skipped": sorted(base_registres),
                               "done_state": done_state}, f, ensure_ascii=False)
                os.replace(tmp, checkpoint_file)
            except OSError:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

        try:
            # Première passe : lister (source, registre) et compter le total de XML.
            # Chaque tâche porte sa source pour namespacer pages et registres. L'empreinte de
            # chaque registre est relevée ici, au moment où ses fichiers sont figés : elle décrit
            # donc exactement ce qui sera indexé, même si des XML arrivent pendant le build.
            sources_sig = IndexesService._sources_signature(sources_info)
            registre_tasks: List[tuple] = []   # (src, reg_dir, xml_files, reg_key, reg_state)
            current_state: Dict[str, Dict] = {}
            total_xml = 0
            for src in sources_info:
                col_dir = IndexesService._resolve_collection_folder(src['collection_id'])
                if not col_dir:
                    continue
                ocr_dir = col_dir / "ocr"
                if not ocr_dir.exists():
                    continue
                for reg_dir in sorted(ocr_dir.iterdir()):
                    if not reg_dir.is_dir():
                        continue
                    model_dir = reg_dir / src['model_name']
                    if not model_dir.exists():
                        continue
                    xml_files, reg_state = IndexesService._scan_registre_xml(model_dir)
                    if not xml_files:
                        continue
                    reg_key = f"{src['key']}{SEP}{reg_dir.name}"
                    registre_tasks.append((src, reg_dir, xml_files, reg_key, reg_state))
                    current_state[reg_key] = reg_state
                    total_xml += len(xml_files)

            if resumed:
                # Un registre a pu disparaître du disque pendant la pause : ses pages resteraient
                # dans l'index alors qu'il n'est plus listé (registres_count sur-compterait).
                orphans = set(done_registres) - set(current_state)
                if orphans:
                    to_drop = IndexesService._expand_prefix_conflicts(orphans, done_registres)
                    IndexesService._purge_registres(mots_uniques, to_drop)
                    done_registres -= to_drop
                    done_state = {rk: st for rk, st in done_state.items() if rk in done_registres}
                    total_words = sum(len(v) for v in mots_uniques.values())

            elif not full:
                # Mise à jour incrémentale : on repart de l'index existant, dont on ne purge que
                # les registres nouveaux/modifiés/supprimés — les autres sont marqués « déjà
                # indexés » et sautés par la deuxième passe, exactement comme à une reprise.
                previous = IndexesService._load_incremental_state(index_dir, metadata, sources_sig)
                if previous is not None:
                    stale = {rk for rk, st in current_state.items()
                             if previous.get(rk, {}).get('sig') != st['sig']}
                    deleted = set(previous) - set(current_state)
                    to_remove = IndexesService._expand_prefix_conflicts(
                        stale | deleted, current_state.keys())
                    words = IndexesService._load_existing_words(final_file, sources_info)
                    if words is not None:
                        mots_uniques = words
                        IndexesService._purge_registres(mots_uniques, to_remove)
                        # `total_words` compte une unité par occurrence stockée (_process_xml) :
                        # il se recalcule donc exactement depuis le dictionnaire purgé.
                        total_words = sum(len(v) for v in mots_uniques.values())
                        done_registres = {rk for rk in current_state if rk not in to_remove}
                        done_state = {rk: previous[rk] for rk in done_registres}
                        mode = 'incremental'
                    # words is None → index illisible/incompatible : reconstruction complète.

            # Base de la progression : les registres conservés, hors du travail de ce run. À une
            # reprise, c'est celle du run d'origine (checkpoint) : les registres traités *pendant*
            # ce run restent dans la barre, sinon elle repartirait de zéro à chaque reprise.
            base_registres = set(done_registres) if cp_skipped is None else (cp_skipped & done_registres)
            base = sum(len(xf) for _s, _rd, xf, rk, _st in registre_tasks if rk in base_registres)

            if on_plan is not None:
                try:
                    on_plan([IndexesService._source_label(rd.name, s)
                             for s, rd, _xf, rk, _st in registre_tasks if rk in base_registres],
                            base)
                except Exception:
                    pass

            processed = sum(len(xf) for _s, _rd, xf, rk, _st in registre_tasks if rk in done_registres)
            if not _publish(processed, total_xml, None, force=True):
                return 'cancelled'  # index supprimé

            # Deuxième passe : indexation, registre par registre (namespacé par source).
            # Checkpoint périodique (throttlé) : permet à une indexation *interrompue* (crash,
            # fermeture) de reprendre au dernier registre terminé, comme une pause explicite.
            # En mise à jour incrémentale il pèse d'emblée le poids de l'index entier : on
            # l'espace, et on ne l'écrit que si un registre de plus a été traité depuis le dernier
            # (sinon il n'apporte rien — le préchargement se refait en quelques secondes).
            CHECKPOINT_THROTTLE_S = 60.0
            last_checkpoint_at = time.monotonic()
            last_checkpoint_done = len(done_registres)
            for src, reg_dir, xml_files, reg_key, reg_state in registre_tasks:
                if reg_key in done_registres:
                    continue
                if _cancelled():
                    IndexesService._discard_staging(staging_file)
                    return 'cancelled'
                if _paused():
                    _save_checkpoint()
                    return 'paused'

                label = IndexesService._source_label(reg_dir.name, src)
                if not _publish(processed, total_xml, label, force=True):
                    return 'cancelled'

                page_prefix = f"{src['key']}{SEP}"
                for xml_file in xml_files:
                    total_words = IndexesService._process_xml(xml_file, mots_uniques, total_words, page_prefix)
                    processed += 1
                    # Page par page (throttlé) : sans cela un gros registre fige la progression
                    # pendant toute sa durée.
                    if not _publish(processed, total_xml, label, xml_file.stem):
                        return 'cancelled'
                done_registres.add(reg_key)
                done_state[reg_key] = reg_state

                # Le checkpoint n'est écrit qu'entre registres complets : il ne reflète jamais un
                # registre à moitié traité (cohérent avec le saut via done_registres à la reprise).
                now = time.monotonic()
                if (now - last_checkpoint_at >= CHECKPOINT_THROTTLE_S
                        and len(done_registres) > last_checkpoint_done):
                    _save_checkpoint()
                    last_checkpoint_at = now
                    last_checkpoint_done = len(done_registres)

                if not _publish(processed, total_xml, label, force=True):
                    return 'cancelled'

            if _cancelled():
                IndexesService._discard_staging(staging_file)
                return 'cancelled'

            # Carte registre → période (namespacée par source) + bloc sources auto-suffisant.
            registres_map: Dict[str, Dict] = {}
            sources_block: Dict[str, Dict] = {}
            for src in sources_info:
                sources_block[src['key']] = {
                    "collection_folder": src['collection_folder'],
                    "collection_titre": src.get('collection_titre'),
                    "model_name": src['model_name'],
                }
                col_dir = IndexesService._resolve_collection_folder(src['collection_id'])
                scans_dir = (col_dir / "scans") if col_dir else None
                if not scans_dir or not scans_dir.exists():
                    continue
                for reg_dir in scans_dir.iterdir():
                    if not reg_dir.is_dir():
                        continue
                    meta_file = reg_dir / "metadata.json"
                    if not meta_file.exists():
                        continue
                    with open(meta_file, 'r', encoding='utf-8-sig') as f:
                        reg_meta = json.load(f)
                    registres_map[f"{src['key']}{SEP}{reg_dir.name}"] = {
                        "id": reg_meta.get("id", reg_dir.name),
                        "titre": reg_meta.get("titre", reg_dir.name),
                        "periode": reg_meta.get("periode", ["", ""]),
                        "source_key": src['key'],
                        "collection_titre": src.get('collection_titre'),
                    }

            # Écriture atomique : staging puis bascule (l'ancien index reste valide jusqu'ici).
            with open(staging_file, 'w', encoding='utf-8') as f:
                json.dump({"words": mots_uniques, "registres_map": registres_map,
                           "sources": sources_block}, f, ensure_ascii=False)
            os.replace(staging_file, final_file)

            year_values = []
            for info in registres_map.values():
                periode = info.get("periode", [])
                try:
                    if periode and periode[0]:
                        year_values.append(int(periode[0]))
                    if len(periode) > 1 and periode[1]:
                        year_values.append(int(periode[1]))
                except (ValueError, TypeError):
                    pass

            # Pages distinctes réellement indexées (présentes dans au moins un mot).
            indexed_pages = set()
            for occs in mots_uniques.values():
                for occ in occs:
                    indexed_pages.add(occ.split(' - ')[0])

            metadata["status"] = "ready"
            metadata["progress"] = None
            metadata["build"] = None
            metadata["stats"] = {
                "total_unique_words": len(mots_uniques),
                "total_word_occurrences": total_words,
                "total_pages": len(indexed_pages),
                "registres_count": len(done_registres),
                "year_min": min(year_values) if year_values else None,
                "year_max": max(year_values) if year_values else None,
            }
            # `index_state` décrit ce qui est réellement dans l'index (empreinte relevée au
            # moment où chaque registre a été traité), et fonde la prochaine mise à jour
            # incrémentale. `coverage` en est dérivé : il alimente le badge de fraîcheur et le
            # panneau Qualité OCR, et reste donc cohérent par construction.
            for rk in done_registres:
                done_state.setdefault(rk, current_state[rk])   # filet : checkpoint sans done_state
            metadata["index_state"] = {
                "version": IndexesService.INDEX_STATE_VERSION,
                "sources_sig": sources_sig,
                "registres": done_state,
            }
            metadata["coverage"] = {rk: st.get("pages", 0) for rk, st in done_state.items()}
            _save_meta()
            try:
                checkpoint_file.unlink(missing_ok=True)  # plus de reprise nécessaire
            except OSError:
                pass
            return 'done'

        except Exception as e:
            IndexesService._discard_staging(staging_file)
            if not index_dir.exists():
                return 'cancelled'  # le dossier a été supprimé
            # Reconstruction échouée : on conserve l'index précédent (toujours 'ready').
            metadata["status"] = "error" if is_new else metadata.get("status", "ready")
            metadata["progress"] = None
            metadata["build"] = None
            metadata["error"] = str(e)
            _save_meta()
            raise

    @staticmethod
    def _discard_staging(staging_file: Path) -> None:
        try:
            staging_file.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _extraire_rectangle(coords: str) -> str:
        points = [tuple(map(int, c.split(','))) for c in coords.split()]
        x_min = min(p[0] for p in points)
        x_max = max(p[0] for p in points)
        y_min = min(p[1] for p in points)
        y_max = max(p[1] for p in points)
        return f"({x_min}, {y_min}), ({x_max}, {y_max})"

    @staticmethod
    def _nettoyer_texte(texte: str) -> List[str]:
        for char in ['&quot', '?', '¬', ':', '(', ')', ',', '.', '_', ';', '█', '/', '+', '*', '--']:
            texte = texte.replace(char, ' ')
        texte = texte.replace('  ', ' ').replace("&#x27", "'").lower()
        return [mot.strip('-') for mot in texte.split() if mot.isalpha()]

    @staticmethod
    def _process_xml(xml_path: Path, mots_uniques: Dict, total_words: int, page_prefix: str = "") -> int:
        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            ns = {'ns': 'http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15'}
            page_name = f"{page_prefix}{xml_path.stem}"
            for ligne in root.findall(".//ns:TextLine", ns):
                for mot_element in ligne.findall(".//ns:Word", ns):
                    texte_unicode = mot_element.find("ns:TextEquiv/ns:Unicode", ns)
                    if texte_unicode is not None and texte_unicode.text:
                        coords_elem = mot_element.find("ns:Coords", ns)
                        mot_coords = IndexesService._extraire_rectangle(coords_elem.attrib['points']) if coords_elem is not None else ""
                        for mot in IndexesService._nettoyer_texte(texte_unicode.text):
                            total_words += 1
                            mots_uniques.setdefault(mot, []).append(f"{page_name} - {mot_coords}")
        except Exception:
            pass
        return total_words

    STOP_WORDS = {
        'au', 'aux', 'avec', 'ce', 'ces', 'cet', 'cette', 'd', 'dans', 'de',
        'des', 'du', 'en', 'est', 'et', 'l', 'la', 'le', 'les', 'leur', 'leurs',
        'lui', 'ma', 'mais', 'me', 'mes', 'moi', 'mon', 'ni', 'nos', 'notre',
        'ou', 'par', 'pas', 'pour', 'qu', 'que', 'qui', 'sa', 'sans', 'se',
        'ses', 'si', 'son', 'sur', 'ta', 'te', 'tes', 'toi', 'ton', 'tu',
        'un', 'une', 'vos', 'votre', 'vous', 'y',
    }

    @staticmethod
    def _get_registre_folder(page_name: str, sorted_folders: List[str]) -> Optional[str]:
        """Retourne la clé de registre (namespacée 'sX::folder' en multi-sources) d'une page."""
        for folder in sorted_folders:
            if page_name.startswith(folder + '_'):
                return folder
        return None

    @staticmethod
    def _page_source_registre(page_name: str, sorted_folders: List[str],
                              sources_block: Dict[str, Dict]) -> tuple:
        """(source_label|None, registre_folder|None) d'une page. La source est déduite du
        préfixe de namespace ('sX::…') via le bloc `sources` de l'index."""
        SEP = IndexesService.SOURCE_SEP
        folder = IndexesService._get_registre_folder(page_name, sorted_folders)
        key = None
        registre = folder
        if folder and SEP in folder:
            key, registre = folder.split(SEP, 1)
        elif SEP in page_name:
            key = page_name.split(SEP, 1)[0]
        source = None
        if key and key in sources_block:
            sb = sources_block[key]
            source = {
                "collection_titre": sb.get("collection_titre"),
                "collection_folder": sb.get("collection_folder"),
                "model_name": sb.get("model_name"),
            }
        return source, registre

    @staticmethod
    def _term_stem(term: str, fuzzy_threshold: Optional[int]) -> str:
        """Racine commune pour le matching de préfixe : longueur pilotée par le seuil
        (plus bas = racine plus courte = plus de variantes morphologiques).
        Min 3 caractères pour limiter le bruit. Vide si le fuzzy est inactif."""
        if fuzzy_threshold is None or fuzzy_threshold >= 100:
            return ""
        stem_len = int(len(term) * fuzzy_threshold / 100)
        if 3 <= stem_len < len(term):
            return term[:stem_len]
        return ""

    @staticmethod
    def _term_matches(term: str, word: str, fuzzy_threshold: Optional[int], stem: str) -> bool:
        """Un mot correspond s'il : contient le terme (neurosyphilis ⊃ syphilis),
        partage la racine (syphilis → syphilitique), ou est proche (faute OCR)."""
        if term in word:
            return True
        if fuzzy_threshold is not None and fuzzy_threshold < 100:
            return (bool(stem) and word.startswith(stem)) or _fuzz.ratio(term, word) >= fuzzy_threshold
        return False

    @staticmethod
    def search_words(
        index_id: str,
        query: str,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
        fuzzy_threshold: Optional[int] = None,
    ) -> Optional[Dict]:
        """Recherche multi-termes avec intersection des pages.
        Retourne uniquement les pages où TOUS les termes sont présents.
        Si year_from/year_to sont fournis, filtre par période de registre."""
        index_file = IndexesService.get_indexes_dir() / index_id / "index.json"
        if not index_file.exists():
            return None

        with open(index_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        # Compatibilité : ancien format (dict plat) vs nouveau format ({"words": ..., "registres_map": ...})
        if "words" in data and isinstance(data["words"], dict):
            index = data["words"]
            registres_map: Dict[str, Dict] = data.get("registres_map", {})
            sources_block: Dict[str, Dict] = data.get("sources", {})
        else:
            index = data
            registres_map = {}
            sources_block = {}

        query_clean = query.strip().lower()
        raw_terms = [t.strip("'\"") for t in query_clean.split() if t.strip("'\"")]
        terms = [t for t in raw_terms if t not in IndexesService.STOP_WORDS]
        if not terms:
            terms = raw_terms

        # Folders triés par longueur décroissante pour le matching de préfixe
        sorted_folders = sorted(registres_map.keys(), key=len, reverse=True)

        # Déterminer les folders autorisés par le filtre d'années
        allowed_folders: Optional[set] = None
        if (year_from is not None or year_to is not None) and registres_map:
            allowed_folders = set()
            for folder, info in registres_map.items():
                periode = info.get("periode", ["", ""])
                try:
                    reg_start = int(periode[0]) if periode and periode[0] else 0
                    reg_end = int(periode[1]) if len(periode) > 1 and periode[1] else 9999
                    if (year_from is None or reg_end >= year_from) and (year_to is None or reg_start <= year_to):
                        allowed_folders.add(folder)
                except (ValueError, TypeError):
                    allowed_folders.add(folder)

        # Pour chaque terme : {page_name: {word: [occ_strings]}}
        term_page_data: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
        for term in terms:
            term_page_data[term] = {}
            stem = IndexesService._term_stem(term, fuzzy_threshold)
            for word, occs in index.items():
                if not IndexesService._term_matches(term, word, fuzzy_threshold, stem):
                    continue
                for occ in occs:
                    page = occ.split(' - ')[0]
                    if allowed_folders is not None:
                        folder = IndexesService._get_registre_folder(page, sorted_folders)
                        if folder not in allowed_folders:
                            continue
                    term_page_data[term].setdefault(page, {}).setdefault(word, []).append(occ)

        if not terms:
            return {"query": query, "terms": terms, "pages": [], "count": 0,
                    "year_from": year_from, "year_to": year_to}

        # Intersection des pages (toutes présentes dans chaque terme)
        page_sets = [set(term_page_data[t].keys()) for t in terms]
        common_pages = page_sets[0]
        for s in page_sets[1:]:
            common_pages &= s

        # Construire les résultats par page, triés par registre puis numéro de page
        def sort_key(page_name: str):
            folder = IndexesService._get_registre_folder(page_name, sorted_folders)
            info = registres_map.get(folder, {}) if folder else {}
            periode = info.get("periode", ["0", "0"])
            try:
                year = int(periode[0]) if periode and periode[0] else 0
            except (ValueError, TypeError):
                year = 0
            suffix = page_name.rsplit('_', 1)[-1]
            page_num = int(suffix) if suffix.isdigit() else 0
            return (year, folder or "", page_num)

        pages = []
        for page_name in sorted(common_pages, key=sort_key):
            words_on_page: Dict[str, List[str]] = {}
            for term in terms:
                for word, occs in term_page_data[term].get(page_name, {}).items():
                    words_on_page[word] = occs
            source, registre = IndexesService._page_source_registre(
                page_name, sorted_folders, sources_block)
            pages.append({"page_name": page_name, "words": words_on_page,
                          "source": source, "registre": registre})

        return {
            "query": query,
            "terms": terms,
            "pages": pages,
            "count": len(pages),
            "year_from": year_from,
            "year_to": year_to,
            "fuzzy_threshold": fuzzy_threshold,
        }

    # Cache de l'index : index_id -> (mtime, total_unique, base_entries, words, registres_map).
    # base_entries = liste non filtrée {word, occurrences, pages}, pré-triée par mot.
    # words = dict mot -> ["page - coords", ...] (sert au détail des pages d'un mot).
    # registres_map = folder -> {id, titre, periode} (sert aux statistiques).
    # Invalidé automatiquement quand index.json change (comparaison de mtime).
    _vocab_cache: Dict[str, tuple] = {}

    @staticmethod
    def _load_index(index_id: str) -> Optional[tuple]:
        """Retourne (total_unique, base_entries, words, registres_map) depuis le cache
        si index.json est inchangé, sinon (re)parse le fichier et met le cache à jour."""
        index_file = IndexesService.get_indexes_dir() / index_id / "index.json"
        if not index_file.exists():
            IndexesService._vocab_cache.pop(index_id, None)
            return None

        mtime = index_file.stat().st_mtime
        cached = IndexesService._vocab_cache.get(index_id)
        if cached and cached[0] == mtime:
            return cached[1], cached[2], cached[3], cached[4]

        with open(index_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        # Compatibilité : ancien format (dict plat) vs nouveau format ({"words": ...})
        if isinstance(data, dict) and "words" in data and isinstance(data["words"], dict):
            words = data["words"]
            registres_map = data.get("registres_map", {})
        else:
            words = data
            registres_map = {}

        base_entries = [
            {
                "word": word,
                "occurrences": len(occs),
                "pages": len({occ.split(' - ')[0] for occ in occs}),
            }
            for word, occs in words.items()
        ]
        # Pré-tri par mot : sert de tri secondaire stable pour les autres tris.
        base_entries.sort(key=lambda e: e["word"])

        total_unique = len(words)
        IndexesService._vocab_cache[index_id] = (mtime, total_unique, base_entries, words, registres_map)
        return total_unique, base_entries, words, registres_map

    @staticmethod
    def get_word_pages(index_id: str, word: str) -> Optional[Dict]:
        """Détail d'un mot exact : pages où il apparaît, avec coordonnées.
        Retourne None si l'index est absent ; un résultat à 0 page si le mot est inconnu."""
        loaded = IndexesService._load_index(index_id)
        if loaded is None:
            return None
        _total_unique, _base, words, _registres_map = loaded

        occs = words.get(word, [])
        # Regrouper les occurrences par page : page -> liste de "coords"
        pages_map: Dict[str, List[str]] = {}
        for occ in occs:
            parts = occ.split(' - ', 1)
            page = parts[0]
            coords = parts[1] if len(parts) > 1 else ""
            pages_map.setdefault(page, []).append(coords)

        def natural_key(page_name: str):
            # Tri naturel : sépare segments texte / numériques pour ordonner correctement.
            return [int(t) if t.isdigit() else t for t in re.findall(r'\d+|\D+', page_name)]

        pages = [
            {"page_name": page, "occurrences": pages_map[page], "count": len(pages_map[page])}
            for page in sorted(pages_map, key=natural_key)
        ]
        return {
            "word": word,
            "page_count": len(pages),
            "total_occurrences": len(occs),
            "pages": pages,
        }

    @staticmethod
    def _get_vocabulary_base(index_id: str) -> Optional[tuple]:
        """Retourne (total_unique_words, base_entries) — voir _load_index."""
        loaded = IndexesService._load_index(index_id)
        if loaded is None:
            return None
        total_unique, base_entries, _words, _registres_map = loaded
        return total_unique, base_entries

    @staticmethod
    def build_vocabulary(
        index_id: str,
        q: Optional[str] = None,
        sort: str = "occurrences",
        direction: str = "desc",
        hide_stopwords: bool = False,
        min_occurrences: int = 1,
    ) -> Optional[tuple]:
        """Construit la liste complète, filtrée et triée, du vocabulaire d'un index.
        Retourne (total_unique_words, entries) ou None si l'index.json est absent.
        `hide_stopwords` écarte les mots vides français et les lettres isolées."""
        base = IndexesService._get_vocabulary_base(index_id)
        if base is None:
            return None
        total_unique, base_entries = base

        # Copie de travail (ne pas muter la liste mise en cache, déjà triée par mot).
        entries = list(base_entries)

        if hide_stopwords:
            entries = [
                e for e in entries
                if len(e["word"]) > 1 and e["word"].lower() not in IndexesService.STOP_WORDS
            ]

        if min_occurrences > 1:
            entries = [e for e in entries if e["occurrences"] >= min_occurrences]

        if q:
            q_clean = q.strip().lower()
            if q_clean:
                entries = [e for e in entries if q_clean in e["word"].lower()]

        reverse = direction == "desc"
        # `entries` est déjà trié par mot (ordre alphabétique) → tri secondaire stable.
        if sort == "occurrences":
            entries.sort(key=lambda e: e["occurrences"], reverse=reverse)
        elif sort == "pages":
            entries.sort(key=lambda e: e["pages"], reverse=reverse)
        elif sort == "word" and reverse:
            entries.reverse()

        return total_unique, entries

    @staticmethod
    def get_vocabulary(
        index_id: str,
        q: Optional[str] = None,
        sort: str = "occurrences",
        direction: str = "desc",
        hide_stopwords: bool = False,
        min_occurrences: int = 1,
        offset: int = 0,
        limit: int = 100,
    ) -> Optional[Dict]:
        """Page de vocabulaire (liste paginée). Retourne None si l'index est absent."""
        built = IndexesService.build_vocabulary(
            index_id, q=q, sort=sort, direction=direction,
            hide_stopwords=hide_stopwords, min_occurrences=min_occurrences,
        )
        if built is None:
            return None
        total_unique_words, entries = built
        total = len(entries)
        page = entries[offset:offset + limit] if limit > 0 else entries[offset:]
        return {
            "total": total,
            "total_unique_words": total_unique_words,
            "items": page,
        }

    @staticmethod
    def _page_collection_dir(meta: Dict[str, Any], page_name: str) -> tuple:
        """(col_dir, page_reelle) pour une page. En multi-sources, le préfixe 'sX::' désigne
        la source (→ sa collection) et le reste est le nom réel du fichier scan. En legacy,
        la page n'a pas de préfixe → collection unique de l'index."""
        SEP = IndexesService.SOURCE_SEP
        sources = meta.get('sources')
        if sources and SEP in page_name:
            key, rest = page_name.split(SEP, 1)
            src = next((s for s in sources if s.get('key') == key), None)
            if not src:
                return None, page_name
            ref = src.get('collection_id') or src.get('collection_folder')
            return IndexesService._resolve_collection_folder(ref), rest
        col_ref = meta.get('collection_folder') or meta.get('collection_id')
        col_dir = IndexesService._resolve_collection_folder(col_ref) if col_ref else None
        return col_dir, page_name

    @staticmethod
    def get_page_image_path(index_id: str, page_name: str) -> Optional[Path]:
        """Résout le nom de page vers le fichier image correspondant.
        Cherche le dossier registre dont le nom est un préfixe du nom de page."""
        meta = IndexesService.get_index(index_id)
        if not meta:
            return None
        col_dir, page = IndexesService._page_collection_dir(meta, page_name)
        if not col_dir:
            return None
        scans_dir = col_dir / "scans"
        if not scans_dir.exists():
            return None
        # Trier par longueur décroissante pour matcher le préfixe le plus long en premier
        registre_dirs = sorted(
            (d for d in scans_dir.iterdir() if d.is_dir()),
            key=lambda d: len(d.name), reverse=True
        )
        for registre_dir in registre_dirs:
            if page.startswith(registre_dir.name + '_'):
                for ext in ('.jpg', '.jpeg', '.png', '.tif', '.tiff'):
                    p = registre_dir / f"{page}{ext}"
                    if p.exists():
                        return p
        return None

    @staticmethod
    def _resolve_pages_files(index_id: str, page_names: List[str]) -> Dict[str, tuple]:
        """Résout en masse une liste de noms de pages vers (Path, registre_folder).
        Scanne chaque collection une seule fois (efficace pour des milliers de pages,
        y compris quand les pages proviennent de plusieurs collections)."""
        meta = IndexesService.get_index(index_id)
        if not meta:
            return {}

        image_exts = ('.jpg', '.jpeg', '.png', '.tif', '.tiff')
        # Cache par collection : (scans_dir, reg_names triés desc, reg_files).
        col_cache: Dict[str, tuple] = {}

        def _col_info(col_dir: Path) -> tuple:
            ck = str(col_dir)
            if ck in col_cache:
                return col_cache[ck]
            scans_dir = col_dir / "scans"
            if not scans_dir.exists():
                col_cache[ck] = (None, [], {})
                return col_cache[ck]
            reg_dirs = sorted((d for d in scans_dir.iterdir() if d.is_dir()),
                              key=lambda d: len(d.name), reverse=True)
            reg_files = {d.name: {f.name for f in d.iterdir() if f.is_file()} for d in reg_dirs}
            col_cache[ck] = (scans_dir, [d.name for d in reg_dirs], reg_files)
            return col_cache[ck]

        result: Dict[str, tuple] = {}
        for name in page_names:
            col_dir, real = IndexesService._page_collection_dir(meta, name)
            path = None
            registre = None
            if col_dir:
                scans_dir, reg_names, reg_files = _col_info(col_dir)
                if scans_dir is not None:
                    registre = next((r for r in reg_names if real.startswith(r + '_')), None)
                    if registre:
                        files = reg_files[registre]
                        for ext in image_exts:
                            fn = f"{real}{ext}"
                            if fn in files:
                                path = scans_dir / registre / fn
                                break
            result[name] = (path, registre)
        return result

    @staticmethod
    def resolve_result_pages(
        index_id: str, q: str,
        year_from: Optional[int] = None, year_to: Optional[int] = None,
        fuzzy_threshold: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Liste ordonnée des pages d'une recherche, avec registre, occurrences et chemin
        du fichier image (relatif à DATA_DIR). Retourne None si l'index est absent."""
        result = IndexesService.search_words(
            index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
        )
        if result is None:
            return None
        pages = result.get("pages", [])
        files = IndexesService._resolve_pages_files(index_id, [p["page_name"] for p in pages])

        SEP = IndexesService.SOURCE_SEP
        rows: List[Dict[str, Any]] = []
        for p in pages:
            name = p["page_name"]
            occ = sum(len(v) for v in p.get("words", {}).values())
            path, registre = files.get(name, (None, None))
            chemin = os.path.relpath(str(path), DATA_DIR) if path else ""
            src = p.get("source") or {}
            # Nom de page affiché : sans le préfixe technique de source.
            display = name.split(SEP, 1)[1] if SEP in name else name
            rows.append({
                "page": display,
                "registre": registre or p.get("registre") or "",
                "source": src.get("collection_titre") or src.get("collection_folder") or "",
                "model": src.get("model_name") or "",
                "occurrences": occ,
                "path": path,
                "chemin": chemin,
            })
        return rows

    @staticmethod
    def resolve_result_zip_files(
        index_id: str, q: str,
        year_from: Optional[int] = None, year_to: Optional[int] = None,
        fuzzy_threshold: Optional[int] = None,
    ) -> Optional[List[tuple]]:
        """Fichiers (registre, Path) à inclure dans le ZIP d'une recherche : les pages résultats
        ET leurs extra pages (même registre, même numéro principal), pour coller à ce qu'affiche
        la visionneuse. Retourne None si l'index est absent."""
        result = IndexesService.search_words(
            index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
        )
        if result is None:
            return None
        pages = result.get("pages", [])
        resolved = IndexesService._resolve_pages_files(index_id, [p["page_name"] for p in pages])

        # Cache par registre : (dossier scans/<registre>, {fichier -> famille principale+extras}).
        reg_cache: Dict[tuple, tuple] = {}
        out: List[tuple] = []
        seen: set = set()
        for _name, (path, registre) in resolved.items():
            if not path or not registre:
                continue
            collection_id = path.parents[2].name  # …/<collection_id>/scans/<registre>/<fichier>
            ck = (collection_id, registre)
            if ck not in reg_cache:
                reg = RegistresService.get_registre(collection_id, registre) or {}
                main_pattern = reg.get('pages_pattern')
                extra_pattern = (reg.get('extra_pagination') or {}).get('pattern')
                files = RegistresService.list_scan_pages(collection_id, registre)
                groups: Dict[str, List[str]] = {}
                keyed: Dict[str, Optional[str]] = {}
                for f in files:
                    k = _page_key(f, main_pattern, extra_pattern)
                    keyed[f] = k
                    if k is not None:
                        groups.setdefault(k, []).append(f)
                fam_index = {f: (groups[k] if k is not None else [f]) for f, k in keyed.items()}
                reg_cache[ck] = (path.parent, fam_index)
            scans_reg_dir, fam_index = reg_cache[ck]
            for fn in fam_index.get(path.name, [path.name]):
                fp = scans_reg_dir / fn
                if fp not in seen:
                    seen.add(fp)
                    out.append((registre, fp))
        return out

    @staticmethod
    def stream_pages_zip(files: List[tuple]):
        """Génère un ZIP (non compressé, streamé) des fichiers bruts.
        `files` = liste de (registre, Path). Mémoire constante même pour des milliers de pages."""
        import zipfile

        class _Buffer:
            def __init__(self):
                self.data = bytearray()
            def write(self, b):
                self.data += b
                return len(b)
            def flush(self):
                pass

        buf = _Buffer()
        seen: Dict[str, int] = {}
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
            for registre, path in files:
                if not path:
                    continue
                arc = f"{registre}/{path.name}" if registre else path.name
                # Éviter les doublons de noms dans l'archive.
                if arc in seen:
                    seen[arc] += 1
                    stem, dot, ext = arc.rpartition('.')
                    arc = f"{stem}_{seen[arc]}{dot}{ext}" if dot else f"{arc}_{seen[arc]}"
                else:
                    seen[arc] = 0
                try:
                    with zf.open(arc, 'w') as dest, open(path, 'rb') as src:
                        while True:
                            chunk = src.read(1 << 16)
                            if not chunk:
                                break
                            dest.write(chunk)
                            if buf.data:
                                yield bytes(buf.data)
                                buf.data.clear()
                except OSError:
                    continue
                if buf.data:
                    yield bytes(buf.data)
                    buf.data.clear()
        if buf.data:
            yield bytes(buf.data)
            buf.data.clear()

    @staticmethod
    def delete_index(index_id: str) -> bool:
        import shutil
        index_dir = IndexesService.get_indexes_dir() / index_id
        if not index_dir.exists():
            return False
        shutil.rmtree(index_dir)
        IndexesService._vocab_cache.pop(index_id, None)
        return True

    # ── Registres d'une tâche d'indexation (trace par registre) ───────
    @staticmethod
    def list_index_registres(collection_id: str, model_name: str) -> List[Dict[str, Any]]:
        """Registres à indexer pour ce modèle, dans l'ordre de traitement (= sorted),
        avec leur nombre de XML. Calculé à l'enfilage pour tracer l'état par registre."""
        col_dir = IndexesService._resolve_collection_folder(collection_id)
        out: List[Dict[str, Any]] = []
        if not col_dir:
            return out
        ocr_dir = col_dir / "ocr"
        if not ocr_dir.exists():
            return out
        for reg_dir in sorted(ocr_dir.iterdir()):
            if not reg_dir.is_dir():
                continue
            model_dir = reg_dir / model_name
            if not model_dir.exists():
                continue
            n = sum(1 for f in model_dir.iterdir() if f.suffix == '.xml')
            if n:
                out.append({"name": reg_dir.name, "pages": n})
        return out

    @staticmethod
    def task_registres(task: Dict[str, Any]) -> List[Dict[str, Any]]:
        """État de chaque registre d'une tâche d'indexation. L'indexation étant séquentielle,
        on déduit l'état depuis `processed` (nb de XML traités) : 'done' / 'current' / 'pending'.

        En mise à jour incrémentale, les registres conservés (`index_skipped`) ne sont pas traités
        dans l'ordre et sortent de `processed`, qui ne compte que le travail de ce run : ils sont
        'done' d'emblée, et le cumul ne déroule que les registres réellement réindexés."""
        regs = task.get('index_registres') or []
        processed = task.get('processed', 0)
        current = task.get('current')
        active = task.get('status') in ('running', 'paused')
        skipped = set(task.get('index_skipped') or [])
        out: List[Dict[str, Any]] = []
        cumulative = 0
        for r in regs:
            if r.get('name') in skipped:
                out.append({"name": r.get('name'), "pages": r.get('pages', 0), "status": 'done'})
                continue
            cumulative += r.get('pages', 0)
            if cumulative <= processed:
                st = 'done'
            elif active and r.get('name') == current:
                st = 'current'
            else:
                st = 'pending'
            out.append({"name": r.get('name'), "pages": r.get('pages', 0), "status": st})
        return out

    # ── Fraîcheur et couverture d'un index ─────────────────────────────
    @staticmethod
    def _current_ocr_counts(col_dir: Path, model_name: str) -> Dict[str, int]:
        """Nb de XML actuels par registre pour ce modèle, par **scan réel** du dossier ocr.

        Source **autoritaire** mais coûteuse (un listing + deux `stat()` par registre, jusqu'à
        1,4 s sur 25 000 XML à froid, davantage sur un partage réseau). Pour l'affichage on lui
        préfère `CollectionsService.ocr_counts_from_metadata`, qui lit les compteurs publiés."""
        counts: Dict[str, int] = {}
        ocr_dir = col_dir / "ocr"
        if not ocr_dir.exists():
            return counts
        for reg_dir in ocr_dir.iterdir():
            if not reg_dir.is_dir():
                continue
            model_dir = reg_dir / model_name
            if model_dir.exists():
                n = sum(1 for f in model_dir.iterdir() if f.suffix == '.xml')
                if n:
                    counts[reg_dir.name] = n
        return counts

    @staticmethod
    def _make_ocr_counts_fn(rescan: bool = False):
        """Fabrique la fonction de comptage OCR d'**une** requête :
        `(collection_id, model_name) -> {registre: pages} | None` (None = collection
        introuvable ou illisible, à distinguer de `{}` = aucune page pour ce modèle).

        `rescan=False` (défaut) lit les compteurs publiés dans le metadata de la collection
        (~0,5 ms) ; `rescan=True` scanne réellement les dossiers OCR (autoritaire, lent).

        Trois mémoïsations, car une même liste d'index interroge plusieurs fois les mêmes
        couples : la résolution de collection (qui peut ouvrir tous les metadata quand l'id
        diffère du nom de dossier), le metadata lu (une seule lecture même pour deux modèles
        d'une même collection) et le comptage lui-même.

        Volontairement **borné à la requête** : un cache inter-requêtes empêcherait un poste de
        voir ce qu'un autre poste vient de publier dans le metadata partagé. Le dict retourné
        est partagé entre appels d'un même couple — aucun appelant ne doit le muter."""
        dir_cache: Dict[str, Optional[Path]] = {}
        meta_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        counts_cache: Dict[tuple, Optional[Dict[str, int]]] = {}

        def counts_fn(collection_id: Optional[str], model_name: str) -> Optional[Dict[str, int]]:
            key = (collection_id, model_name)
            if key in counts_cache:
                return counts_cache[key]
            if collection_id not in dir_cache:   # mémoïser aussi le résultat négatif (None)
                dir_cache[collection_id] = (
                    IndexesService._resolve_collection_folder(collection_id) if collection_id else None)
            col_dir = dir_cache[collection_id]
            if col_dir is None:
                counts_cache[key] = None
                return None
            if rescan:
                result = IndexesService._current_ocr_counts(col_dir, model_name)
            else:
                ck = str(col_dir)
                if ck not in meta_cache:
                    meta_cache[ck] = _read_json_retry(col_dir / "metadata.json")
                meta = meta_cache[ck]
                result = (None if meta is None
                          else CollectionsService.ocr_counts_from_meta(meta, model_name))
            counts_cache[key] = result
            return result

        return counts_fn

    @staticmethod
    def _blank_updates(rescan: bool = False, coverage_known: bool = True) -> Dict[str, Any]:
        """Réponse « rien à signaler » : ni delta, ni couverture exploitable."""
        return {"new_registres": 0, "new_pages": 0, "coverage_known": coverage_known,
                "indexed_pages": None, "ocr_pages": None, "stale_pages": 0,
                "rescanned": rescan, "sources": []}

    @staticmethod
    def compute_updates(meta: Dict[str, Any], *, rescan: bool = False,
                        counts_fn=None) -> Dict[str, Any]:
        """Compare ce qui est indexé à l'OCR disponible : delta **et** couverture.

        - Index avec `coverage` (générés avec le suivi) → détail précis, par source.
        - Index plus anciens (sans `coverage`) → `coverage_known: False`, et pour un index
          mono-source un signal grossier par nombre de registres (on ne lit PAS l'index.json,
          qui peut peser >100 Mo) ; une mise à jour passe l'index en suivi précis.

        `counts_fn` permet de partager les mémoïsations sur toute une liste d'index (cf.
        `list_updates`) ; il doit alors correspondre au `rescan` passé, qui n'est plus utilisé
        que pour renseigner `rescanned`."""
        if meta.get('status') != 'ready':
            return IndexesService._blank_updates(rescan)
        if counts_fn is None:
            counts_fn = IndexesService._make_ocr_counts_fn(rescan)

        SEP = IndexesService.SOURCE_SEP
        coverage = meta.get('coverage')
        sources = meta.get('sources')
        legacy = not sources

        if legacy:
            # Ancien index mono-source : ses clés de couverture sont des noms de registres nus.
            col_ref = meta.get('collection_folder') or meta.get('collection_id')
            if not col_ref:
                return IndexesService._blank_updates(rescan)
            sources = [{"key": None, "collection_id": col_ref, "collection_folder": col_ref,
                        "collection_titre": None, "model_name": meta.get('model_name') or ''}]
        elif coverage is None:
            # Multi-sources sans couverture : rien de comparable, et inutile de scanner.
            return IndexesService._blank_updates(rescan, coverage_known=False)

        per_source: List[Dict[str, Any]] = []
        new_registres = new_pages = 0
        total_ocr = total_registres = 0
        any_resolved = False
        all_resolved = True

        for src in sources:
            counts = counts_fn(src.get('collection_id'), src.get('model_name') or '')
            entry = {
                "key": src.get('key'),
                "collection_folder": src.get('collection_folder') or src.get('collection_id') or '',
                "collection_titre": src.get('collection_titre'),
                "model_name": src.get('model_name') or '',
                "resolved": counts is not None,
                "indexed_pages": None, "ocr_pages": None,
                "new_registres": 0, "new_pages": 0,
            }
            if counts is None:
                all_resolved = False
            else:
                any_resolved = True
                entry["ocr_pages"] = sum(counts.values())
                total_ocr += entry["ocr_pages"]
                total_registres += len(counts)
                if coverage is not None:
                    prefix = None if src['key'] is None else f"{src['key']}{SEP}"
                    entry["indexed_pages"] = (
                        sum(coverage.values()) if prefix is None
                        else sum(v for k, v in coverage.items() if k.startswith(prefix)))
                    for reg, n in counts.items():
                        reg_key = reg if prefix is None else f"{prefix}{reg}"
                        if reg_key not in coverage:
                            entry["new_registres"] += 1
                        entry["new_pages"] += max(0, n - coverage.get(reg_key, 0))
                    new_registres += entry["new_registres"]
                    new_pages += entry["new_pages"]
            per_source.append(entry)

        if legacy and not any_resolved:
            return IndexesService._blank_updates(rescan)

        if coverage is None:
            # Legacy sans couverture : signal grossier par nombre de registres, et uniquement si
            # `registres_count` est fiable — sinon un vieil index semblerait tout avoir de neuf.
            indexed_regs = (meta.get('stats') or {}).get('registres_count')
            if not indexed_regs:
                return IndexesService._blank_updates(rescan)
            out = IndexesService._blank_updates(rescan, coverage_known=False)
            out["new_registres"] = max(0, total_registres - indexed_regs)
            out["ocr_pages"] = total_ocr
            out["sources"] = per_source
            return out

        indexed_pages = sum(coverage.values())
        ocr_pages = total_ocr if any_resolved else None
        # `stale_pages` n'a de sens que si tout l'OCR a pu être compté : une source introuvable
        # ferait passer ses pages indexées pour des pages disparues.
        stale = max(0, indexed_pages - total_ocr) if all_resolved else 0
        return {"new_registres": new_registres, "new_pages": new_pages, "coverage_known": True,
                "indexed_pages": indexed_pages, "ocr_pages": ocr_pages, "stale_pages": stale,
                "rescanned": rescan, "sources": per_source}

    @staticmethod
    def list_updates(rescan: bool = False) -> List[Dict[str, Any]]:
        """Fraîcheur et couverture de **chaque** index prêt.

        Un seul `counts_fn` pour toute la liste : un couple (collection, modèle) partagé par
        plusieurs index n'est compté qu'une fois."""
        counts_fn = IndexesService._make_ocr_counts_fn(rescan)
        out: List[Dict[str, Any]] = []
        for meta in IndexesService.list_indexes():
            if meta.get('status') != 'ready':
                continue
            out.append({"id": meta['id'],
                        **IndexesService.compute_updates(meta, rescan=rescan, counts_fn=counts_fn)})
        return out


class TranscriptionsService:
    @staticmethod
    def get_summary() -> List[Dict[str, Any]]:
        """Résumé agrégé : nb de fichiers XML par collection / registre / modèle.
        Ne fait aucun stat() individuel — juste des listdir, donc très rapide."""
        collections_dir = Path(DATA_DIR) / "collections"
        result = []

        if not collections_dir.exists():
            return result

        for col_dir in sorted(collections_dir.iterdir()):
            if not col_dir.is_dir() or col_dir.name.startswith('.'):
                continue
            ocr_dir = col_dir / "ocr"
            if not ocr_dir.exists():
                continue

            # Collecter tous les modèles présents dans cette collection
            all_models: set = set()
            registres_data = []

            for reg_dir in sorted(ocr_dir.iterdir()):
                if not reg_dir.is_dir():
                    continue
                counts: Dict[str, int] = {}
                for model_dir in reg_dir.iterdir():
                    if model_dir.is_dir():
                        # Compter les XML sans stat() individuel
                        n = sum(1 for f in model_dir.iterdir() if f.suffix == '.xml')
                        counts[model_dir.name] = n
                        all_models.add(model_dir.name)
                if counts:
                    registres_data.append({"registre_id": reg_dir.name, "counts": counts})

            models = sorted(all_models)
            totals = {m: sum(r["counts"].get(m, 0) for r in registres_data) for m in models}

            result.append({
                "collection_id": col_dir.name,
                "models": models,
                "registres": registres_data,
                "totals": totals,
                "grand_total": sum(totals.values()),
            })

        return result


    @staticmethod
    def list_transcriptions(collection_id: Optional[str] = None, registre_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liste toutes les transcriptions, optionnellement filtrées par collection et/ou registre"""
        transcriptions = []
        collections_dir = Path(DATA_DIR) / "collections"

        if not collections_dir.exists():
            return transcriptions

        # Déterminer quelles collections explorer
        if collection_id:
            collections_to_scan = [collections_dir / collection_id]
        else:
            collections_to_scan = [d for d in collections_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]

        for col_dir in collections_to_scan:
            if not col_dir.exists():
                continue

            ocr_dir = col_dir / "ocr"
            if not ocr_dir.exists():
                continue

            # Déterminer quels registres explorer
            if registre_id:
                registres_to_scan = [ocr_dir / registre_id]
            else:
                registres_to_scan = [d for d in ocr_dir.iterdir() if d.is_dir()]

            for reg_dir in registres_to_scan:
                if not reg_dir.exists():
                    continue

                # Explorer les modèles
                for model_dir in reg_dir.iterdir():
                    if model_dir.is_dir():
                        model_name = model_dir.name

                        # Compter les fichiers XML
                        xml_files = list(model_dir.glob("*.xml"))
                        for xml_file in xml_files:
                            transcriptions.append({
                                "collection_id": col_dir.name,
                                "registre_id": reg_dir.name,
                                "model_name": model_name,
                                "file_name": xml_file.name,
                                "file_path": str(xml_file),
                                "has_content": xml_file.stat().st_size > 0
                            })

        return transcriptions
