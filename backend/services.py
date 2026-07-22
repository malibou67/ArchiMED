import os
import sys
import json
import re
import tempfile
import time
from typing import List, Optional, Dict, Any
from pathlib import Path
from rapidfuzz import fuzz as _fuzz

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
        """Liste toutes les collections disponibles"""
        collections_dir = CollectionsService.get_collections_dir()
        collections = []

        if not collections_dir.exists():
            return collections

        for item in collections_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                metadata_file = item / "metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                        metadata = json.load(f)
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
    def sync_collection_metadata(collection_id: str) -> Optional[Dict[str, Any]]:
        """Reconstruit la liste des registres dans le metadata.json d'une collection.

        Si le dossier n'a pas encore de metadata.json (collection déposée à la main dans
        data/collections), un squelette minimal est créé — c'est le point d'entrée voulu :
        déposer le dossier, scanner, synchroniser, puis affiner les métadonnées dans l'UI."""
        collections_dir = CollectionsService.get_collections_dir()
        collection_dir = collections_dir / collection_id

        if not collection_dir.is_dir():
            return None

        metadata_file = collection_dir / "metadata.json"
        if metadata_file.exists():
            with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                metadata = json.load(f)
        else:
            # Garde-fou : on ne transforme en collection que les dossiers qui ressemblent
            # à une collection (scans/ non vide ou ocr/ présent).
            scans = collection_dir / "scans"
            has_scans = scans.is_dir() and any(scans.iterdir())
            if not has_scans and not (collection_dir / "ocr").is_dir():
                return None
            metadata = {
                "id": f"col_{int(time.time())}_{re.sub(r'[^a-z0-9_-]', '_', collection_id.lower())}",
                "type": collection_id,
                "titre": collection_id,
                "periode": ["", ""],
                "lieu": "",
                "commentaire": "",
            }

        scans_dir = collection_dir / "scans"
        ocr_root = collection_dir / "ocr"
        ocr_root.mkdir(exist_ok=True)
        registres_summary = []
        scan_reg_names = set()  # registres réellement présents dans scans/

        if scans_dir.exists():
            for item in sorted(scans_dir.iterdir(), key=lambda x: x.name):
                if not item.is_dir():
                    continue
                scan_reg_names.add(item.name)

                reg_metadata_file = item / "metadata.json"
                if reg_metadata_file.exists():
                    with open(reg_metadata_file, 'r', encoding='utf-8-sig') as f:
                        reg_meta = json.load(f)
                    changed = False
                else:
                    reg_meta = {}
                    changed = True

                # Compter les pages images
                pages = [
                    f.name for f in item.iterdir()
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
                ]

                # Pattern, bornes et pages hors-pattern (motif du metadata réutilisé s'il existe)
                pagination = _diagnose_pagination(
                    pages,
                    known_pattern=reg_meta.get('pagination', {}).get('pattern'),
                    known_start=reg_meta.get('pagination', {}).get('start'),
                    known_end=reg_meta.get('pagination', {}).get('end'),
                )

                # Compléter les champs dérivables manquants/vides (sans écraser les saisies)
                if not reg_meta.get('id'):
                    reg_meta['id'] = item.name
                    changed = True
                if not reg_meta.get('titre'):
                    reg_meta['titre'] = item.name
                    changed = True

                periode = reg_meta.get('periode')
                periode_empty = (
                    not periode
                    or (isinstance(periode, list) and all(not str(x).strip() for x in periode))
                )
                if periode_empty:
                    year = _extract_year(item.name)
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

                if changed:
                    with open(reg_metadata_file, 'w', encoding='utf-8') as f:
                        json.dump(reg_meta, f, indent=2, ensure_ascii=False)

                # Scaffolder le dossier OCR du registre
                (ocr_root / item.name).mkdir(exist_ok=True)

                entry = {
                    'id': reg_meta.get('id', item.name),
                    'titre': reg_meta.get('titre', item.name),
                    'periode': reg_meta.get('periode', ['', '']),
                    'folder_name': item.name,
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

                # État OCR réel : comptage des XML par modèle sur le disque
                status = _count_ocr_xml(ocr_root / item.name, len(pages))
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
        for ocr_reg in sorted(ocr_root.iterdir()):
            if ocr_reg.is_dir() and ocr_reg.name not in scan_reg_names:
                col_anomalies.append(f'ocr_orphelin:{ocr_reg.name}')
        for sub in sorted(collection_dir.iterdir()):
            if not sub.is_dir() or sub.name in ('scans', 'ocr'):
                continue
            if CollectionsService._SCAN_EXCLUDE.match(sub.name):
                continue
            if any(f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS for f in sub.iterdir()):
                col_anomalies.append(f'registre_hors_scans:{sub.name}')
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

        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        metadata['folder_name'] = collection_dir.name
        return metadata

    # Dossiers à ignorer lors du scan (cachés, Python, système)
    _SCAN_EXCLUDE = re.compile(r'^(\.|__)')

    @staticmethod
    def scan_filesystem() -> Dict[str, Any]:
        """Diagnostic en lecture seule du dossier collections : présence des fichiers,
        couverture OCR par modèle, état de pagination et anomalies. N'écrit rien."""
        report: Dict[str, Any] = {"collections": [], "new_collections": 0, "new_registres": 0, "anomalies_count": 0}
        for event in CollectionsService.scan_filesystem_iter():
            if event.get('type') == 'report':
                report = event['report']
        return report

    @staticmethod
    def scan_filesystem_iter():
        """Variante en flux de scan_filesystem : émet un événement de progression par
        collection ({'type': 'progress', 'current', 'total', 'name'}) puis un événement
        final ({'type': 'report', 'report': {...}}). Lecture seule, même diagnostic."""
        collections_dir = CollectionsService.get_collections_dir()
        if not collections_dir.exists():
            yield {"type": "report", "report": {"collections": [], "new_collections": 0, "new_registres": 0, "anomalies_count": 0}}
            return

        known = CollectionsService.list_collections()
        known_folders = {c.get('folder_name') for c in known}
        known_registres = {
            c.get('folder_name'): {r['folder_name'] for r in c.get('registres') or []}
            for c in known
        }

        # Dossiers de collections à analyser : total connu d'emblée pour la progression.
        col_dirs = [
            d for d in sorted(collections_dir.iterdir())
            if d.is_dir() and not CollectionsService._SCAN_EXCLUDE.match(d.name)
        ]
        total = len(col_dirs)

        report_cols = []
        new_collections = 0
        new_registres = 0
        anomalies_count = 0

        for idx, col_dir in enumerate(col_dirs, 1):
            yield {"type": "progress", "current": idx, "total": total, "name": col_dir.name}
            is_known = col_dir.name in known_folders
            if not is_known:
                new_collections += 1

            scans_dir = col_dir / "scans"
            ocr_dir = col_dir / "ocr"
            known_regs = known_registres.get(col_dir.name, set())
            col_anomalies: List[str] = []

            # metadata.json présent mais illisible
            metadata_file = col_dir / "metadata.json"
            has_metadata = metadata_file.exists()
            if has_metadata:
                try:
                    with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                        json.load(f)
                except (json.JSONDecodeError, OSError):
                    col_anomalies.append('metadata_illisible')
                    anomalies_count += 1

            # Noms de registres réellement présents dans scans/
            scan_reg_names = set()
            regs = []
            if scans_dir.exists():
                for reg_dir in sorted(scans_dir.iterdir()):
                    if not reg_dir.is_dir():
                        continue
                    scan_reg_names.add(reg_dir.name)
                    reg_known = reg_dir.name in known_regs
                    if not reg_known:
                        new_registres += 1

                    pages = [
                        f.name for f in reg_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
                    ]
                    ocr_status = _count_ocr_xml(ocr_dir / reg_dir.name, len(pages))
                    pagination = _diagnose_pagination(pages)

                    reg_anomalies: List[str] = []
                    if not pages:
                        reg_anomalies.append('registre_vide')
                    elif pagination['pattern'] is None:
                        reg_anomalies.append('pagination_indetectable')
                    anomalies_count += len(reg_anomalies)

                    regs.append({
                        "folder_name": reg_dir.name,
                        "is_known": reg_known,
                        "has_metadata": (reg_dir / "metadata.json").exists(),
                        "has_ocr_folder": (ocr_dir / reg_dir.name).exists(),
                        "pages_count": len(pages),
                        "ocr_status": ocr_status,
                        "anomalies": reg_anomalies,
                        "pagination": pagination,
                    })

            # Registres listés dans le metadata mais absents du disque
            for missing in sorted(known_regs - scan_reg_names):
                regs.append({
                    "folder_name": missing,
                    "is_known": True,
                    "has_metadata": False,
                    "has_ocr_folder": (ocr_dir / missing).exists(),
                    "pages_count": 0,
                    "ocr_status": {},
                    "anomalies": ['registre_absent_disque'],
                    "pagination": None,
                })
                anomalies_count += 1

            # Dossiers ocr/ orphelins (sans scans/<reg> correspondant)
            if ocr_dir.exists():
                for ocr_reg in sorted(ocr_dir.iterdir()):
                    if ocr_reg.is_dir() and ocr_reg.name not in scan_reg_names:
                        col_anomalies.append(f'ocr_orphelin:{ocr_reg.name}')
                        anomalies_count += 1

            # Sous-dossiers déposés directement sous la collection (hors scans/ et ocr/)
            for sub in sorted(col_dir.iterdir()):
                if not sub.is_dir() or sub.name in ('scans', 'ocr'):
                    continue
                if CollectionsService._SCAN_EXCLUDE.match(sub.name):
                    continue
                has_images = any(
                    f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
                    for f in sub.iterdir()
                )
                if has_images:
                    col_anomalies.append(f'registre_hors_scans:{sub.name}')
                    anomalies_count += 1

            report_cols.append({
                "folder_name": col_dir.name,
                "is_known": is_known,
                "has_metadata": has_metadata,
                "has_scans_folder": scans_dir.exists(),
                "has_ocr_folder": ocr_dir.exists(),
                "anomalies": col_anomalies,
                "registres": regs,
            })

        yield {
            "type": "report",
            "report": {
                "collections": report_cols,
                "new_collections": new_collections,
                "new_registres": new_registres,
                "anomalies_count": anomalies_count,
            },
        }

    @staticmethod
    def sync_all_collections() -> List[Dict[str, Any]]:
        """Synchronise tous les dossiers de data/collections ; les dossiers sans
        metadata.json sont bootstrappés par sync_collection_metadata (s'ils ont des scans)."""
        results: List[Dict[str, Any]] = []
        for event in CollectionsService.sync_all_collections_iter():
            if event.get('type') == 'done':
                results = event['results']
        return results

    @staticmethod
    def sync_all_collections_iter():
        """Variante en flux de sync_all_collections : émet un événement de progression par
        collection ({'type': 'progress', 'current', 'total', 'name'}) puis un événement
        final ({'type': 'done', 'results': [...]})."""
        collections_dir = CollectionsService.get_collections_dir()
        if not collections_dir.exists():
            yield {"type": "done", "results": []}
            return

        col_dirs = [
            d for d in sorted(collections_dir.iterdir())
            if d.is_dir() and not CollectionsService._SCAN_EXCLUDE.match(d.name)
        ]
        total = len(col_dirs)

        results: List[Dict[str, Any]] = []
        for idx, col_dir in enumerate(col_dirs, 1):
            yield {"type": "progress", "current": idx, "total": total, "name": col_dir.name}
            result = CollectionsService.sync_collection_metadata(col_dir.name)
            if result:
                results.append(result)

        yield {"type": "done", "results": results}

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
        """Lit un metadata.json en tolérant une écriture concurrente.

        La génération réécrit ce fichier très souvent (progression) ; un lecteur qui tombe
        pendant l'écriture peut voir un contenu tronqué (`JSONDecodeError`) ou, sous Windows,
        un verrou transitoire (`PermissionError`). On réessaie brièvement plutôt que de faire
        remonter l'erreur. Les écritures étant désormais atomiques (`_save_index_meta`), un
        retry suffit largement à retomber sur un fichier complet."""
        for attempt in range(retries):
            try:
                with open(metadata_file, 'r', encoding='utf-8-sig') as f:
                    return json.load(f)
            except (json.JSONDecodeError, PermissionError, OSError):
                if attempt == retries - 1:
                    return None
                time.sleep(delay)
        return None

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

    @staticmethod
    def generate_index(index_id: str, on_progress=None,
                       should_cancel=None, should_pause=None) -> str:
        """Construit l'index multi-sources à partir des XML OCR.
        Retourne 'done' | 'cancelled' | 'paused'.

        Les pages et les registres sont **namespacés** par la clé de source ('s0::…')
        pour éviter les collisions entre collections. L'index.json final est écrit dans un
        **staging** puis basculé atomiquement (os.replace) : l'ancien index reste consultable
        pendant toute une reconstruction.

        `on_progress(processed, total, current)` rapporte l'avancement,
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

        def _set_progress(processed: int, total: int, current: Optional[str]) -> None:
            metadata.setdefault('build', {})['status'] = 'generating'
            metadata['build']['progress'] = {
                "processed": processed, "total": total, "current_registre": current,
            }

        def _cancelled() -> bool:
            return (should_cancel is not None and should_cancel()) or not index_dir.exists()

        def _paused() -> bool:
            return should_pause is not None and should_pause()

        def _report(processed: int, total: int, current: Optional[str]) -> None:
            if on_progress is not None:
                try:
                    on_progress(processed, total, current)
                except Exception:
                    pass

        # État, repris d'un checkpoint si l'indexation avait été mise en pause.
        mots_uniques: Dict[str, List[str]] = {}
        total_words = 0
        done_registres: set = set()   # clés "key::registre"
        if checkpoint_file.exists():
            try:
                with open(checkpoint_file, 'r', encoding='utf-8-sig') as f:
                    cp = json.load(f)
                mots_uniques = cp.get('words', {})
                total_words = cp.get('total_words', 0)
                done_registres = set(cp.get('done_registres', []))
            except (OSError, json.JSONDecodeError):
                pass

        def _save_checkpoint() -> None:
            # Écriture atomique (tmp + os.replace) : un crash pendant l'écriture ne peut pas
            # laisser un checkpoint tronqué (la reprise après interruption s'y appuie).
            tmp = checkpoint_file.with_name(checkpoint_file.name + '.tmp')
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump({"words": mots_uniques, "total_words": total_words,
                               "done_registres": sorted(done_registres)}, f, ensure_ascii=False)
                os.replace(tmp, checkpoint_file)
            except OSError:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

        try:
            # Première passe : lister (source, registre) et compter le total de XML.
            # Chaque tâche porte sa source pour namespacer pages et registres.
            registre_tasks: List[tuple] = []   # (src, reg_dir, xml_files, reg_key)
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
                    xml_files = list(sorted(model_dir.glob("*.xml")))
                    if not xml_files:
                        continue
                    reg_key = f"{src['key']}{SEP}{reg_dir.name}"
                    registre_tasks.append((src, reg_dir, xml_files, reg_key))
                    total_xml += len(xml_files)

            processed = sum(len(xf) for _s, _rd, xf, rk in registre_tasks if rk in done_registres)
            _set_progress(processed, total_xml, None)
            if not _save_meta():
                return 'cancelled'  # index supprimé
            _report(processed, total_xml, None)

            # Deuxième passe : indexation, registre par registre (namespacé par source).
            # Checkpoint périodique (throttlé) : permet à une indexation *interrompue* (crash,
            # fermeture) de reprendre au dernier registre terminé, comme une pause explicite.
            CHECKPOINT_THROTTLE_S = 15.0
            last_checkpoint_at = time.monotonic()
            for src, reg_dir, xml_files, reg_key in registre_tasks:
                if reg_key in done_registres:
                    continue
                if _cancelled():
                    IndexesService._discard_staging(staging_file)
                    return 'cancelled'
                if _paused():
                    _save_checkpoint()
                    return 'paused'

                label = IndexesService._source_label(reg_dir.name, src)
                _set_progress(processed, total_xml, label)
                if not _save_meta():
                    return 'cancelled'
                _report(processed, total_xml, label)

                page_prefix = f"{src['key']}{SEP}"
                for xml_file in xml_files:
                    total_words = IndexesService._process_xml(xml_file, mots_uniques, total_words, page_prefix)
                    processed += 1
                done_registres.add(reg_key)

                # Le checkpoint n'est écrit qu'entre registres complets : il ne reflète jamais un
                # registre à moitié traité (cohérent avec le saut via done_registres à la reprise).
                now = time.monotonic()
                if now - last_checkpoint_at >= CHECKPOINT_THROTTLE_S:
                    _save_checkpoint()
                    last_checkpoint_at = now

                _set_progress(processed, total_xml, label)
                if not _save_meta():
                    return 'cancelled'
                _report(processed, total_xml, label)

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
            metadata["coverage"] = {rk: len(xf) for _s, _rd, xf, rk in registre_tasks}
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
        on déduit l'état depuis `processed` (nb de XML traités) : 'done' / 'current' / 'pending'."""
        regs = task.get('index_registres') or []
        processed = task.get('processed', 0)
        current = task.get('current')
        active = task.get('status') in ('running', 'paused')
        out: List[Dict[str, Any]] = []
        cumulative = 0
        for r in regs:
            cumulative += r.get('pages', 0)
            if cumulative <= processed:
                st = 'done'
            elif active and r.get('name') == current:
                st = 'current'
            else:
                st = 'pending'
            out.append({"name": r.get('name'), "pages": r.get('pages', 0), "status": st})
        return out

    # ── Fraîcheur d'un index (OCR ajouté depuis l'indexation) ──────────
    @staticmethod
    def _current_ocr_counts(col_dir: Path, model_name: str) -> Dict[str, int]:
        """Nb de XML actuels par registre pour ce modèle (scan léger du dossier ocr)."""
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
    def compute_updates(meta: Dict[str, Any]) -> Dict[str, Any]:
        """Compare ce qui est indexé à l'OCR actuellement sur le disque.
        Retourne {new_registres, new_pages, coverage_known}.

        - Index avec `coverage` (générés avec le suivi) → détail précis (registres + pages),
          scan léger du dossier ocr uniquement.
        - Index plus anciens (sans `coverage`) → comparaison grossière par nombre de registres
          (on ne lit PAS l'index.json, qui peut peser >100 Mo) ; une reconstruction passe
          l'index en suivi précis."""
        unknown = {"new_registres": 0, "new_pages": 0, "coverage_known": True}
        if meta.get('status') != 'ready':
            return unknown

        coverage = meta.get('coverage')
        sources = meta.get('sources')

        # Multi-sources : compare l'OCR actuel de chaque source à sa couverture (clés 'key::reg').
        if sources:
            if coverage is None:
                return {"new_registres": 0, "new_pages": 0, "coverage_known": False}
            SEP = IndexesService.SOURCE_SEP
            new_registres = new_pages = 0
            for src in sources:
                col_dir = IndexesService._resolve_collection_folder(src.get('collection_id'))
                if not col_dir:
                    continue
                current = IndexesService._current_ocr_counts(col_dir, src.get('model_name', ''))
                for reg, n in current.items():
                    reg_key = f"{src['key']}{SEP}{reg}"
                    if reg_key not in coverage:
                        new_registres += 1
                    new_pages += max(0, n - coverage.get(reg_key, 0))
            return {"new_registres": new_registres, "new_pages": new_pages, "coverage_known": True}

        # ── Legacy (mono-source) ──────────────────────────────────────────────
        col_ref = meta.get('collection_folder') or meta.get('collection_id')
        col_dir = IndexesService._resolve_collection_folder(col_ref) if col_ref else None
        if not col_dir:
            return unknown

        current = IndexesService._current_ocr_counts(col_dir, meta.get('model_name', ''))

        if coverage is not None:
            new_registres = sum(1 for reg in current if reg not in coverage)
            new_pages = sum(max(0, n - coverage.get(reg, 0)) for reg, n in current.items())
            return {"new_registres": new_registres, "new_pages": new_pages, "coverage_known": True}

        # Sans couverture : signal grossier (nouveaux registres) sans lire le gros index.json.
        # Uniquement si on dispose d'un registres_count fiable, sinon on n'alarme pas
        # (un vieil index sans cette stat semblerait avoir « tous » ses registres nouveaux).
        indexed_regs = (meta.get('stats') or {}).get('registres_count')
        if not indexed_regs:
            return unknown
        new_registres = max(0, len(current) - indexed_regs)
        return {"new_registres": new_registres, "new_pages": 0, "coverage_known": False}


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
