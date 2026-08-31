"""Abandon d'une génération d'index : ce qui doit survivre, et ce qui doit disparaître.

L'invariant central : **annuler ou échouer ne détruit jamais un index utilisable**. Un index
déjà construit reste consultable ; seule la reconstruction en cours est abandonnée. Corollaire
tout aussi important : le marqueur `build` ne doit jamais survivre à l'échec — un index resté
« en reconstruction » sans tâche pour le faire avancer figeait sa ligne indéfiniment, et la
seule action alors offerte (annuler) partait sur le repli destructeur.
"""
import json

import pytest

from services import IndexesService

from conftest import build, make_collection, read_index


def index_dir(data_dir, index_id):
    return data_dir / "indexes" / index_id


def meta_of(data_dir, index_id):
    with open(index_dir(data_dir, index_id) / "metadata.json", encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture
def built_index(data_dir):
    """Un index construit et prêt, puis remis en reconstruction (marqueur `build` posé)."""
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat", "rat"]}})
    assert build("idx1") == 'done'
    sources = IndexesService._resolve_sources([{"collection_id": "COL", "model_name": "modelA"}])
    IndexesService.mark_rebuild("idx1", sources)
    return data_dir


# ── abort_build ──────────────────────────────────────────────────────────────

def test_abort_conserve_un_index_deja_construit(built_index):
    """Le cas qui faisait perdre des index : annuler une *reconstruction* ne doit toucher qu'au
    marqueur, pas à l'index.json précédent, qui reste parfaitement valide."""
    data_dir = built_index
    before = read_index(data_dir, "idx1")
    (index_dir(data_dir, "idx1") / "index.json.tmp").write_text("{}", encoding='utf-8')
    (index_dir(data_dir, "idx1") / "checkpoint.json").write_text("{}", encoding='utf-8')

    assert IndexesService.abort_build("idx1") == 'cleared'

    assert read_index(data_dir, "idx1") == before
    assert meta_of(data_dir, "idx1").get('build') is None
    assert not (index_dir(data_dir, "idx1") / "index.json.tmp").exists()
    assert not (index_dir(data_dir, "idx1") / "checkpoint.json").exists()


def test_abort_supprime_un_index_jamais_termine(data_dir):
    """Un index dont la toute première génération est abandonnée n'a rien à conserver."""
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    sources = IndexesService._resolve_sources([{"collection_id": "COL", "model_name": "modelA"}])
    IndexesService.init_index_new("idx_neuf", "Neuf", sources)

    assert IndexesService.abort_build("idx_neuf") == 'deleted'
    assert not index_dir(data_dir, "idx_neuf").exists()


def test_abort_sur_index_inconnu(data_dir):
    assert IndexesService.abort_build("fantome") == 'missing'


# ── fail_build ───────────────────────────────────────────────────────────────

def test_fail_build_retire_le_marqueur_et_garde_lindex(built_index):
    data_dir = built_index
    before = read_index(data_dir, "idx1")

    IndexesService.fail_build("idx1", "disque plein")

    meta = meta_of(data_dir, "idx1")
    assert meta['build'] is None
    assert meta['error'] == "disque plein"
    assert meta['status'] == 'ready'          # l'index reste consultable
    assert read_index(data_dir, "idx1") == before


def test_fail_build_marque_en_erreur_une_premiere_generation(data_dir):
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    sources = IndexesService._resolve_sources([{"collection_id": "COL", "model_name": "modelA"}])
    IndexesService.init_index_new("idx_neuf", "Neuf", sources)

    IndexesService.fail_build("idx_neuf", "boum")

    meta = meta_of(data_dir, "idx_neuf")
    assert meta['status'] == 'error'
    assert meta['build'] is None


# ── generate_index : échecs et annulations ───────────────────────────────────

def test_echec_dans_la_preparation_retire_le_marqueur(built_index):
    """L'exception qui laissait un index « en reconstruction » pour toujours : elle survenait
    dans la préparation (lecture du checkpoint), hors du bloc protégé, qui n'attrape que
    `OSError` et `JSONDecodeError`. Un checkpoint corrompu en octets lève, lui, une
    `UnicodeDecodeError` — désormais couverte comme tout le reste du corps."""
    data_dir = built_index
    before = read_index(data_dir, "idx1")
    (index_dir(data_dir, "idx1") / "checkpoint.json").write_bytes(b'\xff\xfe pas de l\x92utf-8')

    with pytest.raises(UnicodeDecodeError):
        IndexesService.generate_index("idx1")

    meta = meta_of(data_dir, "idx1")
    assert meta['build'] is None            # la ligne ne reste pas figée sur « Reconstruction… »
    assert meta['status'] == 'ready'
    assert read_index(data_dir, "idx1") == before


def test_echec_en_cours_de_run_conserve_lindex_precedent(built_index, monkeypatch):
    data_dir = built_index
    before = read_index(data_dir, "idx1")

    def boom(*a, **k):
        raise RuntimeError("lecture réseau interrompue")

    monkeypatch.setattr(IndexesService, '_process_xml', staticmethod(boom))
    with pytest.raises(RuntimeError):
        build("idx1", full=True)

    meta = meta_of(data_dir, "idx1")
    assert meta['build'] is None
    assert meta['status'] == 'ready'
    assert read_index(data_dir, "idx1") == before
    assert not (index_dir(data_dir, "idx1") / "index.json.tmp").exists()


def test_ecriture_du_metadata_en_echec_nannule_pas_le_run(data_dir, monkeypatch):
    """Une écriture de progression ratée (hoquet du partage, antivirus qui tient le fichier)
    était interprétée comme la disparition de l'index — donc une annulation, donc la
    suppression pure et simple d'un index neuf."""
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"], "REGA_2": ["rat"]}})
    sources = IndexesService._resolve_sources([{"collection_id": "COL", "model_name": "modelA"}])
    IndexesService.init_index_new("idx1", "Idx", sources)   # metadata écrit avant la panne
    monkeypatch.setattr(IndexesService, '_save_index_meta', staticmethod(lambda *a, **k: False))

    assert IndexesService.generate_index("idx1") == 'done'
    assert index_dir(data_dir, "idx1").exists()
    assert sorted(read_index(data_dir, "idx1")['words']) == ['chat', 'rat']


def test_annulation_en_cours_de_registre(data_dir, monkeypatch):
    """L'annulation est écoutée à l'intérieur d'un registre, pas seulement à ses bornes : un
    registre de plusieurs centaines de pages faisait sinon patienter une minute ou plus, bouton
    grisé — et l'utilisateur recliquait."""
    pages = {f"REGA_{i}": ["chat"] for i in range(1, 420)}
    make_collection(data_dir, "COL", {"REGA": pages})

    lus = []
    original = IndexesService._process_xml

    def compte(xml_path, mots, total, prefix=""):
        lus.append(xml_path.name)
        return original(xml_path, mots, total, prefix)

    monkeypatch.setattr(IndexesService, '_process_xml', staticmethod(compte))
    assert build("idx1", should_cancel=lambda: len(lus) >= 200) == 'cancelled'

    # Sans le contrôle interne, le registre entier (419 pages) aurait été lu jusqu'au bout.
    assert len(lus) < 419
    assert not (index_dir(data_dir, "idx1") / "index.json").exists()


# ── _process_xml : les pages perdues sont comptées ───────────────────────────

def test_xml_illisible_signale_au_lieu_detre_avale(data_dir, tmp_path):
    mots = {}
    bad = tmp_path / "casse.xml"
    bad.write_text("<PcGts>pas fermé", encoding='utf-8')
    total, ok = IndexesService._process_xml(bad, mots, 0)
    assert ok is False
    assert total == 0
    assert mots == {}


# ── Réconciliation au démarrage ──────────────────────────────────────────────

def test_reconcile_signale_une_reconstruction_orpheline(built_index, monkeypatch):
    """Un arrêt brutal (ou une tâche purgée) laisse un index « en reconstruction » que plus
    rien ne fait avancer. Au démarrage on l'annonce pour ce qu'il est, afin que l'interface
    propose de reprendre ou d'abandonner — au lieu d'une barre qui tourne dans le vide."""
    import task_service
    from task_service import TaskService

    data_dir = built_index
    monkeypatch.setattr(task_service, 'DATA_DIR', str(data_dir))
    monkeypatch.setattr(TaskService, '_tasks', {})
    monkeypatch.setattr(TaskService, '_merged_cache', None)
    monkeypatch.setattr(TaskService, '_merged_cache_ts', 0.0)
    monkeypatch.setattr(TaskService, '_loaded', True)

    import index_runner
    assert index_runner.reconcile_orphan_builds() == 1
    assert meta_of(data_dir, "idx1")['build']['status'] == 'interrupted'

    # Idempotent : un second passage ne resignale rien.
    assert index_runner.reconcile_orphan_builds() == 0


def test_reconcile_laisse_tranquille_une_reconstruction_suivie(built_index, monkeypatch):
    """Une tâche vivante s'en occupe : ne rien toucher."""
    import task_service
    from task_service import TaskService

    data_dir = built_index
    monkeypatch.setattr(task_service, 'DATA_DIR', str(data_dir))
    monkeypatch.setattr(TaskService, '_tasks', {
        'tid': {'id': 'tid', 'type': 'index', 'index_id': 'idx1', 'status': 'running'},
    })
    monkeypatch.setattr(TaskService, '_merged_cache', None)
    monkeypatch.setattr(TaskService, '_merged_cache_ts', 0.0)
    monkeypatch.setattr(TaskService, '_loaded', True)

    import index_runner
    assert index_runner.reconcile_orphan_builds() == 0
    assert meta_of(data_dir, "idx1")['build']['status'] == 'generating'
