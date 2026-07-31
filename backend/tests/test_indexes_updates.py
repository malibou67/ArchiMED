"""Fraîcheur et couverture des index (`GET /api/indexes/updates`).

L'invariant central : les compteurs publiés (`ocr_status`) doivent donner exactement le même
résultat qu'un scan réel du disque tant que rien n'a bougé hors de l'application — c'est ce qui
autorise à servir le badge en quelques millisecondes au lieu de plusieurs secondes.
"""
import json
import shutil

import services
from services import CollectionsService, IndexesService

from conftest import build, make_collection, publish_ocr_status, write_page_xml


def read_meta(col):
    return json.loads((col / "metadata.json").read_text(encoding='utf-8'))


# ── ocr_counts_from_meta / ocr_counts_from_metadata ──────────────────────────

def test_ocr_counts_from_meta_filtre_le_modele_et_omet_les_zeros():
    meta = {"registres": [
        {"folder_name": "A", "ocr_status": {"m1": {"pages_done": 3}, "m2": {"pages_done": 7}}},
        {"folder_name": "B", "ocr_status": {"m2": {"pages_done": 5}}},        # pas de m1
        {"folder_name": "C", "ocr_status": {"m1": {"pages_done": 0}}},        # 0 → omis
        {"folder_name": "D"},                                                  # jamais OCRisé
        {"ocr_status": {"m1": {"pages_done": 9}}},                             # sans folder_name
    ]}
    assert CollectionsService.ocr_counts_from_meta(meta, "m1") == {"A": 3}
    assert CollectionsService.ocr_counts_from_meta(meta, "m2") == {"A": 7, "B": 5}
    assert CollectionsService.ocr_counts_from_meta(meta, "inconnu") == {}
    assert CollectionsService.ocr_counts_from_meta({}, "m1") == {}


def test_ocr_counts_from_meta_tolere_un_metadata_abime():
    meta = {"registres": [
        {"folder_name": "A", "ocr_status": {"m1": {"pages_done": "12"}}},   # chaîne → convertie
        {"folder_name": "B", "ocr_status": {"m1": {"pages_done": "beaucoup"}}},
        {"folder_name": "C", "ocr_status": "cassé"},
    ]}
    assert CollectionsService.ocr_counts_from_meta(meta, "m1") == {"A": 12}


def test_ocr_counts_from_metadata_distingue_illisible_et_vide(data_dir, tmp_path):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    assert CollectionsService.ocr_counts_from_metadata(col, "modelA") == {}   # rien de publié
    publish_ocr_status(col)
    assert CollectionsService.ocr_counts_from_metadata(col, "modelA") == {"REGA": 1}

    assert CollectionsService.ocr_counts_from_metadata(tmp_path / "absente", "modelA") is None
    (col / "metadata.json").write_text("{ pas du json", encoding='utf-8')
    assert CollectionsService.ocr_counts_from_metadata(col, "modelA") is None


def test_invariant_compteurs_publies_egaux_au_scan_reel(data_dir):
    """Le cœur de l'optimisation : publié == scanné, y compris pour les omissions à 0."""
    col = make_collection(data_dir, "COL", {
        "REGA": {"REGA_1": ["chat"], "REGA_2": ["chien"]},
        "REGB": {"REGB_1": ["rat"]},
    })
    make_collection(data_dir, "COL", {"REGC": {"REGC_1": ["souris"]}}, model="modelB")
    publish_ocr_status(col)

    for model in ("modelA", "modelB", "modelInexistant"):
        assert (CollectionsService.ocr_counts_from_metadata(col, model)
                == IndexesService._current_ocr_counts(col, model)), model


# ── Mémoïsation ──────────────────────────────────────────────────────────────

def test_counts_fn_memoise_par_couple_et_par_collection(data_dir, monkeypatch):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chien"]}}, model="modelB")
    publish_ocr_status(col)

    resolves, scans, reads = [], [], []
    orig_resolve = IndexesService._resolve_collection_folder
    orig_scan = IndexesService._current_ocr_counts
    orig_read = services._read_json_retry
    monkeypatch.setattr(IndexesService, '_resolve_collection_folder',
                        staticmethod(lambda cid: (resolves.append(cid), orig_resolve(cid))[1]))
    monkeypatch.setattr(IndexesService, '_current_ocr_counts',
                        staticmethod(lambda d, m: (scans.append((d.name, m)), orig_scan(d, m))[1]))
    monkeypatch.setattr(services, '_read_json_retry',
                        lambda p, *a, **k: (reads.append(p.name), orig_read(p, *a, **k))[1])

    fn = IndexesService._make_ocr_counts_fn()
    for _ in range(3):
        fn("COL", "modelA")
        fn("COL", "modelB")
    assert resolves == ["COL"]                       # résolution mémoïsée
    assert reads.count("metadata.json") == 1         # une seule lecture pour deux modèles

    scan_fn = IndexesService._make_ocr_counts_fn(rescan=True)
    for _ in range(3):
        scan_fn("COL", "modelA")
    assert scans == [("COL", "modelA")]              # un seul scan malgré trois appels


def test_counts_fn_memoise_le_resultat_negatif(data_dir, monkeypatch):
    resolves = []
    orig = IndexesService._resolve_collection_folder
    monkeypatch.setattr(IndexesService, '_resolve_collection_folder',
                        staticmethod(lambda cid: (resolves.append(cid), orig(cid))[1]))
    fn = IndexesService._make_ocr_counts_fn()
    assert fn("INEXISTANTE", "modelA") is None
    assert fn("INEXISTANTE", "modelA") is None
    assert resolves == ["INEXISTANTE"]


# ── compute_updates : delta et couverture ────────────────────────────────────

def test_couverture_complete_quand_tout_est_indexe(data_dir):
    col = make_collection(data_dir, "COL", {
        "REGA": {"REGA_1": ["chat"], "REGA_2": ["chien"]},
        "REGB": {"REGB_1": ["rat"]},
    })
    publish_ocr_status(col)
    assert build("idx1") == 'done'

    upd = IndexesService.compute_updates(IndexesService.get_index("idx1"))
    assert upd["coverage_known"] is True
    assert upd["indexed_pages"] == upd["ocr_pages"] == 3
    assert upd["new_pages"] == upd["new_registres"] == upd["stale_pages"] == 0
    assert upd["rescanned"] is False
    assert [s["ocr_pages"] for s in upd["sources"]] == [3]
    assert upd["sources"][0]["resolved"] is True


def test_peremption_des_compteurs_publies(data_dir):
    """XML déposé hors de l'application : invisible par défaut, vu avec rescan."""
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    publish_ocr_status(col)
    assert build("idx1") == 'done'

    write_page_xml(col / "ocr" / "REGA" / "modelA" / "REGA_2.xml", ["hibou"])
    meta = IndexesService.get_index("idx1")

    fast = IndexesService.compute_updates(meta)
    assert fast["new_pages"] == 0 and fast["ocr_pages"] == 1      # ocr_status pas encore republié
    slow = IndexesService.compute_updates(meta, rescan=True)
    assert slow["new_pages"] == 1 and slow["ocr_pages"] == 2
    assert slow["rescanned"] is True

    publish_ocr_status(col)                                        # ce que fait le runner OCR
    assert IndexesService.compute_updates(meta)["new_pages"] == 1


def test_nouveau_registre_et_ventilation_par_source(data_dir):
    """Deux sources, un registre homonyme dans chacune : pas de fuite entre s0 et s1."""
    a = make_collection(data_dir, "A", {"REG": {"REG_1": ["chat"], "REG_2": ["chien"]}})
    b = make_collection(data_dir, "B", {"REG": {"REG_1": ["rat"]}})
    publish_ocr_status(a)
    publish_ocr_status(b)

    sources = IndexesService._resolve_sources([
        {"collection_id": "A", "model_name": "modelA"},
        {"collection_id": "B", "model_name": "modelA"},
    ])
    IndexesService.init_index_new("idx1", "Idx", sources)
    assert IndexesService.generate_index("idx1") == 'done'

    upd = IndexesService.compute_updates(IndexesService.get_index("idx1"))
    assert upd["indexed_pages"] == upd["ocr_pages"] == 3
    per_key = {s["key"]: s for s in upd["sources"]}
    assert per_key["s0"]["indexed_pages"] == 2 and per_key["s0"]["ocr_pages"] == 2
    assert per_key["s1"]["indexed_pages"] == 1 and per_key["s1"]["ocr_pages"] == 1

    write_page_xml(b / "ocr" / "AUTRE" / "modelA" / "AUTRE_1.xml", ["souris"])
    publish_ocr_status(b)   # ne publie que les registres connus du metadata : AUTRE reste ignoré
    assert IndexesService.compute_updates(IndexesService.get_index("idx1"))["new_registres"] == 0

    make_collection(data_dir, "B", {"AUTRE": {"AUTRE_1": ["souris"]}})   # déclare le registre
    publish_ocr_status(b)
    upd = IndexesService.compute_updates(IndexesService.get_index("idx1"))
    assert upd["new_registres"] == 1 and upd["new_pages"] == 1
    assert {s["key"]: s["new_registres"] for s in upd["sources"]} == {"s0": 0, "s1": 1}


def test_pages_ocr_supprimees_comptees_comme_perimees(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"], "REGA_2": ["chien"]}})
    publish_ocr_status(col)
    assert build("idx1") == 'done'

    (col / "ocr" / "REGA" / "modelA" / "REGA_2.xml").unlink()
    publish_ocr_status(col)

    upd = IndexesService.compute_updates(IndexesService.get_index("idx1"))
    assert upd["indexed_pages"] == 2 and upd["ocr_pages"] == 1
    assert upd["stale_pages"] == 1 and upd["new_pages"] == 0     # jamais de delta négatif


def test_collection_introuvable(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    publish_ocr_status(col)
    assert build("idx1") == 'done'

    shutil.rmtree(col)
    upd = IndexesService.compute_updates(IndexesService.get_index("idx1"))
    assert upd["sources"][0]["resolved"] is False
    assert upd["sources"][0]["ocr_pages"] is None
    assert upd["ocr_pages"] is None
    # Une source introuvable ne doit pas faire passer ses pages pour des pages disparues.
    assert upd["stale_pages"] == 0


# ── Index legacy (mono-source, clés de couverture nues) ──────────────────────

def test_legacy_avec_coverage(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"], "REGA_2": ["chien"]}})
    publish_ocr_status(col)
    meta = {"id": "old", "status": "ready", "collection_id": "COL", "collection_folder": "COL",
            "model_name": "modelA", "coverage": {"REGA": 2},
            "stats": {"registres_count": 1}}

    upd = IndexesService.compute_updates(meta)
    assert upd["coverage_known"] is True
    assert upd["indexed_pages"] == upd["ocr_pages"] == 2
    assert upd["new_pages"] == 0
    assert upd["sources"][0]["key"] is None and upd["sources"][0]["indexed_pages"] == 2

    write_page_xml(col / "ocr" / "REGB" / "modelA" / "REGB_1.xml", ["rat"])
    make_collection(data_dir, "COL", {"REGB": {"REGB_1": ["rat"]}})
    publish_ocr_status(col)
    upd = IndexesService.compute_updates(meta)
    assert upd["new_registres"] == 1 and upd["new_pages"] == 1


def test_legacy_sans_coverage(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}, "REGB": {"REGB_1": ["rat"]}})
    publish_ocr_status(col)
    base = {"id": "old", "status": "ready", "collection_id": "COL", "collection_folder": "COL",
            "model_name": "modelA"}

    upd = IndexesService.compute_updates({**base, "stats": {"registres_count": 1}})
    assert upd["coverage_known"] is False
    assert upd["indexed_pages"] is None
    assert upd["new_registres"] == 1 and upd["new_pages"] == 0

    # Sans registres_count fiable, on n'alarme pas : tout paraîtrait neuf.
    assert IndexesService.compute_updates(base)["new_registres"] == 0


def test_multi_sources_sans_coverage(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    publish_ocr_status(col)
    sources = IndexesService._resolve_sources([{"collection_id": "COL", "model_name": "modelA"}])
    upd = IndexesService.compute_updates({"id": "x", "status": "ready", "sources": sources})
    assert upd["coverage_known"] is False
    assert upd["indexed_pages"] is None and upd["ocr_pages"] is None


# ── list_updates ─────────────────────────────────────────────────────────────

def test_list_updates_couvre_tous_les_index_prets(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    publish_ocr_status(col)
    assert build("idx1") == 'done'
    assert build("idx2", name="Idx2") == 'done'

    ids = [u["id"] for u in IndexesService.list_updates()]
    assert sorted(ids) == ["idx1", "idx2"]           # y compris ceux sans rien de nouveau

    IndexesService.mark_rebuild("idx2")              # build en cours : toujours 'ready'
    assert len(IndexesService.list_updates()) == 2
    meta = IndexesService.get_index("idx2")
    meta["status"] = "generating"
    IndexesService._save_index_meta("idx2", meta)
    assert [u["id"] for u in IndexesService.list_updates()] == ["idx1"]


def test_list_updates_partage_le_comptage_entre_index(data_dir, monkeypatch):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    publish_ocr_status(col)
    assert build("idx1") == 'done'
    assert build("idx2", name="Idx2") == 'done'

    scans = []
    orig = IndexesService._current_ocr_counts
    monkeypatch.setattr(IndexesService, '_current_ocr_counts',
                        staticmethod(lambda d, m: (scans.append((d.name, m)), orig(d, m))[1]))
    IndexesService.list_updates(rescan=True)
    assert scans == [("COL", "modelA")]   # deux index, un seul scan


def test_list_updates_identique_dans_les_deux_modes(data_dir):
    """Tant que rien n'a bougé hors de l'application, publié et scanné coïncident."""
    a = make_collection(data_dir, "A", {"REGA": {"REGA_1": ["chat"], "REGA_2": ["rat"]}})
    b = make_collection(data_dir, "B", {"REGB": {"REGB_1": ["souris"]}})
    publish_ocr_status(a)
    publish_ocr_status(b)
    sources = IndexesService._resolve_sources([
        {"collection_id": "A", "model_name": "modelA"},
        {"collection_id": "B", "model_name": "modelA"},
    ])
    IndexesService.init_index_new("idx1", "Idx", sources)
    assert IndexesService.generate_index("idx1") == 'done'

    strip = lambda ups: [{k: v for k, v in u.items() if k != 'rescanned'} for u in ups]
    assert strip(IndexesService.list_updates()) == strip(IndexesService.list_updates(rescan=True))
