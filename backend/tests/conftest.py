"""Configuration pytest : chemin d'import du backend, isolation de DATA_DIR, corpus factices.

Le backend s'importe à plat (`from services import IndexesService`) parce qu'il est lancé depuis
son propre dossier ; les tests reproduisent ce chemin d'import.
"""
import json
import sys
import threading
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import machine_identity  # noqa: E402  (doivent suivre l'ajout au sys.path)
import services  # noqa: E402
import task_service  # noqa: E402
from task_service import TaskService  # noqa: E402

PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"


@pytest.fixture(autouse=True)
def _sequential_indexing(monkeypatch):
    """Épingle l'indexation sur son chemin séquentiel par défaut.

    Le pool de process n'exécute pas `IndexesService._process_xml` : les tests qui l'espionnent
    (empreintes incrémentales, granularité de l'annulation) décriraient sinon un chemin qui ne
    passe plus par là, et le résultat dépendrait du nombre de cœurs de la machine. Le pool a ses
    propres tests, qui lèvent explicitement cette épingle (`test_index_pool.py`)."""
    monkeypatch.setenv('ARCHIMED_INDEX_WORKERS', '1')


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isole DATA_DIR dans un dossier temporaire (relu à chaque appel dans services)."""
    monkeypatch.setattr(services, 'DATA_DIR', str(tmp_path))
    (tmp_path / "collections").mkdir()
    (tmp_path / "indexes").mkdir()
    return tmp_path


def write_page_xml(path, mots):
    """Écrit un PAGE XML minimal contenant `mots` (un Word par mot)."""
    words = "".join(
        f'<Word><Coords points="{10 * i},20 {10 * i + 8},20 {10 * i + 8},30 {10 * i},30"/>'
        f'<TextEquiv><Unicode>{m}</Unicode></TextEquiv></Word>'
        for i, m in enumerate(mots)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<PcGts xmlns="{PAGE_NS}"><Page><TextRegion><TextLine>{words}'
        f'</TextLine></TextRegion></Page></PcGts>',
        encoding='utf-8',
    )


def make_collection(data_dir, folder, registres, model="modelA"):
    """Crée une collection avec ses registres OCR (`{reg: {page: [mots]}}`) et leurs scans.

    Le metadata de collection porte l'entrée `registres[]` attendue par
    `refresh_registre_ocr_status` (match sur `folder_name`), mais **pas** encore d'`ocr_status` :
    c'est à chaque test de le publier, pour pouvoir aussi tester le cas « en retard ».
    """
    col = data_dir / "collections" / folder
    col.mkdir(parents=True, exist_ok=True)
    entries = []
    for reg, pages in registres.items():
        for page, mots in pages.items():
            write_page_xml(col / "ocr" / reg / model / f"{page}.xml", mots)
        scan_dir = col / "scans" / reg
        scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / "metadata.json").write_text(
            json.dumps({"id": reg, "titre": reg, "periode": ["1900", "1910"]}), encoding='utf-8')
        entries.append({"id": reg, "folder_name": reg, "titre": reg, "pages_count": len(pages)})

    meta_file = col / "metadata.json"
    meta = json.loads(meta_file.read_text(encoding='utf-8')) if meta_file.exists() else {
        "id": folder, "titre": folder, "registres": []}
    known = {r['folder_name'] for r in meta['registres']}
    meta['registres'].extend(e for e in entries if e['folder_name'] not in known)
    meta_file.write_text(json.dumps(meta), encoding='utf-8')
    return col


def publish_ocr_status(collection_folder):
    """Publie l'`ocr_status` de tous les registres d'une collection, comme le fait le runner OCR."""
    from services import CollectionsService
    for reg in json.loads((collection_folder / "metadata.json").read_text(encoding='utf-8'))['registres']:
        CollectionsService.refresh_registre_ocr_status(collection_folder.name, reg['folder_name'])


def read_index(data_dir, index_id):
    with open(data_dir / "indexes" / index_id / "index.json", encoding='utf-8') as f:
        return json.load(f)


def build(index_id, name="Idx", collection="COL", model="modelA", full=False, **kwargs):
    """Génère (ou met à jour) un index sur une source unique. Retourne le résultat brut."""
    from services import IndexesService
    sources = IndexesService._resolve_sources([{"collection_id": collection, "model_name": model}])
    if IndexesService.get_index(index_id) is None:
        IndexesService.init_index_new(index_id, name, sources)
    else:
        IndexesService.mark_rebuild(index_id, sources)
    return IndexesService.generate_index(index_id, full=full, **kwargs)


# ── Moteur de tâches ──────────────────────────────────────────────────────
# Partagés par les tests du moteur (file d'attente, comptabilité du temps).


@pytest.fixture
def tasks_dir(tmp_path, monkeypatch):
    """Isole `data/` et remet le moteur à zéro.

    `task_service` fait `from services import DATA_DIR` : c'est une liaison au niveau module,
    que patcher `services.DATA_DIR` (fixture `data_dir`) ne redirige pas. On patche donc les
    deux, et on repart d'un moteur vierge — ses états sont des attributs de **classe**, donc
    partagés entre tests."""
    monkeypatch.setattr(task_service, 'DATA_DIR', str(tmp_path))
    TaskService._tasks = {}
    TaskService._queue = {}
    TaskService._active = {}
    TaskService._held_locks = {}
    TaskService._file_cache = {}
    TaskService._sidecar_cache = {}
    TaskService._merged_cache = None
    TaskService._merged_cache_ts = 0.0
    TaskService._last_reconcile_disk = 0.0
    TaskService._runners = {}
    TaskService._cancel_hooks = {}
    TaskService._error_hooks = {}
    # Pas de chargement disque ni de thread superviseur : les tests pilotent tout à la main.
    TaskService._loaded = True
    TaskService._supervisor_started = True
    return tmp_path


class Runner:
    """Runner factice bloquant, pour tenir une lane occupée le temps d'un test."""

    def __init__(self):
        self.libère = threading.Event()
        self.démarrées = []
        self.entré = threading.Event()

    def __call__(self, task):
        self.démarrées.append(task['id'])
        self.entré.set()
        self.libère.wait(timeout=5)


def attendre(prédicat, timeout=5.0):
    """Attend qu'un prédicat devienne vrai (les tâches tournent dans des threads)."""
    fin = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if prédicat():
            return True
        fin.wait(0.02)
    return prédicat()


def enfiler(label='t'):
    return TaskService.enqueue('ocr', label)


def ecrire_tache(tasks_dir, tid, machine_id, status='queued'):
    d = tasks_dir / 'tasks'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{tid}.json').write_text(json.dumps({
        'id': tid, 'type': 'ocr', 'status': status, 'label': tid,
        'total': 0, 'processed': 0, 'failed': 0, 'created_at': '2026-01-01T00:00:00',
        'machine_id': machine_id, 'machine_label': machine_id,
    }), encoding='utf-8')
