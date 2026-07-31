"""Mise à jour incrémentale des index : helpers d'empreinte/purge et bout-en-bout.

L'invariant vérifié de bout en bout est le seul qui compte vraiment : une mise à jour
incrémentale doit produire *exactement* le même index qu'une reconstruction complète, tout en
ne relisant que les registres nouveaux ou modifiés.
"""
import json
import os

import pytest

import services
from services import IndexesService


# ── Utilitaires de corpus ────────────────────────────────────────────────────

PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"


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


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isole DATA_DIR dans un dossier temporaire (lu à chaque appel dans services)."""
    monkeypatch.setattr(services, 'DATA_DIR', str(tmp_path))
    (tmp_path / "collections").mkdir()
    (tmp_path / "indexes").mkdir()
    return tmp_path


def make_collection(data_dir, folder, registres, model="modelA"):
    """Crée une collection avec ses registres OCR (`{reg: {page: [mots]}}`) et leurs scans."""
    col = data_dir / "collections" / folder
    (col / "metadata.json").parent.mkdir(parents=True, exist_ok=True)
    (col / "metadata.json").write_text(json.dumps({"id": folder, "titre": folder}), encoding='utf-8')
    for reg, pages in registres.items():
        for page, mots in pages.items():
            write_page_xml(col / "ocr" / reg / model / f"{page}.xml", mots)
        scan_dir = col / "scans" / reg
        scan_dir.mkdir(parents=True, exist_ok=True)
        (scan_dir / "metadata.json").write_text(
            json.dumps({"id": reg, "titre": reg, "periode": ["1900", "1910"]}), encoding='utf-8')
    return col


def build(index_id, name="Idx", collection="COL", model="modelA", full=False, **kwargs):
    """Génère (ou met à jour) un index sur une source unique. Retourne le résultat brut."""
    sources = IndexesService._resolve_sources([{"collection_id": collection, "model_name": model}])
    if IndexesService.get_index(index_id) is None:
        IndexesService.init_index_new(index_id, name, sources)
    else:
        IndexesService.mark_rebuild(index_id, sources)
    return IndexesService.generate_index(index_id, full=full, **kwargs)


def read_index(data_dir, index_id):
    with open(data_dir / "indexes" / index_id / "index.json", encoding='utf-8') as f:
        return json.load(f)


def normalized(index_json):
    """Index comparable indépendamment de l'ordre d'insertion des occurrences."""
    return {mot: sorted(occs) for mot, occs in index_json['words'].items()}


def spy_process_xml(monkeypatch):
    """Enregistre le nom des XML réellement parsés. Retourne la liste, alimentée en direct."""
    parsed = []
    original = IndexesService._process_xml

    def traced(xml_path, mots_uniques, total_words, page_prefix=""):
        parsed.append(xml_path.name)
        return original(xml_path, mots_uniques, total_words, page_prefix)

    monkeypatch.setattr(IndexesService, '_process_xml', staticmethod(traced))
    return parsed


# ── _purge_registres ─────────────────────────────────────────────────────────

def test_purge_retire_les_occurrences_du_registre():
    words = {
        "chat": ["s0::A_1 - c", "s0::B_1 - c"],
        "chien": ["s0::A_2 - c"],
        "rat": ["s0::B_3 - c"],
    }
    removed = IndexesService._purge_registres(words, {"s0::A"})
    assert removed == 2
    assert words == {"chat": ["s0::B_1 - c"], "rat": ["s0::B_3 - c"]}   # 'chien' vidé → supprimé


def test_purge_ne_touche_pas_un_registre_de_nom_voisin():
    """`s0::A_B` n'est pas un registre « dans » `s0::A` — seul le préfixe complet compte."""
    words = {"mot": ["s0::A_1 - c", "s0::A_B_1 - c", "s1::A_1 - c"]}
    IndexesService._purge_registres(words, {"s0::A_B"})
    assert words["mot"] == ["s0::A_1 - c", "s1::A_1 - c"]


def test_purge_sans_cle_est_un_noop():
    words = {"mot": ["s0::A_1 - c"]}
    assert IndexesService._purge_registres(words, set()) == 0
    assert words == {"mot": ["s0::A_1 - c"]}


# ── _expand_prefix_conflicts ─────────────────────────────────────────────────

def test_expand_prefix_conflicts_ferme_les_registres_emboites():
    keys = {"s0::A", "s0::A_B", "s0::A_B_C", "s0::B"}
    assert IndexesService._expand_prefix_conflicts({"s0::A"}, keys) == {"s0::A", "s0::A_B", "s0::A_B_C"}


def test_expand_prefix_conflicts_jeu_vide():
    assert IndexesService._expand_prefix_conflicts(set(), {"s0::A"}) == set()


# ── _scan_registre_xml ───────────────────────────────────────────────────────

def test_scan_signature_stable_et_sensible_aux_modifications(tmp_path):
    d = tmp_path / "reg"
    write_page_xml(d / "REG_1.xml", ["alpha"])
    write_page_xml(d / "REG_2.xml", ["beta"])

    files, state = IndexesService._scan_registre_xml(d)
    assert [f.name for f in files] == ["REG_1.xml", "REG_2.xml"]   # trié par nom
    assert state["pages"] == 2
    assert IndexesService._scan_registre_xml(d)[1] == state        # stable entre deux appels

    write_page_xml(d / "REG_3.xml", ["gamma"])                     # ajout
    assert IndexesService._scan_registre_xml(d)[1]["sig"] != state["sig"]

    (d / "REG_3.xml").unlink()                                     # retour à l'identique
    assert IndexesService._scan_registre_xml(d)[1] == state

    write_page_xml(d / "REG_2.xml", ["beta", "delta"])             # taille modifiée
    assert IndexesService._scan_registre_xml(d)[1]["sig"] != state["sig"]


def test_scan_ignore_les_non_xml_et_les_dossiers_absents(tmp_path):
    d = tmp_path / "reg"
    write_page_xml(d / "REG_1.xml", ["alpha"])
    (d / "notes.txt").write_text("ignore", encoding='utf-8')
    assert IndexesService._scan_registre_xml(d)[1]["pages"] == 1
    assert IndexesService._scan_registre_xml(tmp_path / "absent") == ([], {"pages": 0, "sig": ""})


# ── _sources_signature ───────────────────────────────────────────────────────

def test_sources_signature():
    a = [{"key": "s0", "collection_id": "C1", "model_name": "m1", "collection_folder": "C1"}]
    assert IndexesService._sources_signature(a) == IndexesService._sources_signature(a)

    # Renommer le dossier/titre d'une collection n'invalide pas l'index (les noms de page ne
    # dépendent que de la clé de source et du dossier de registre).
    renamed = [{**a[0], "collection_folder": "AUTRE", "collection_titre": "Autre"}]
    assert IndexesService._sources_signature(renamed) == IndexesService._sources_signature(a)

    other_model = [{**a[0], "model_name": "m2"}]
    assert IndexesService._sources_signature(other_model) != IndexesService._sources_signature(a)

    swapped = [
        {"key": "s0", "collection_id": "C2", "model_name": "m1"},
        {"key": "s1", "collection_id": "C1", "model_name": "m1"},
    ]
    reversed_keys = [
        {"key": "s0", "collection_id": "C1", "model_name": "m1"},
        {"key": "s1", "collection_id": "C2", "model_name": "m1"},
    ]
    assert IndexesService._sources_signature(swapped) != IndexesService._sources_signature(reversed_keys)


# ── _load_incremental_state / _load_existing_words ───────────────────────────

def test_load_incremental_state_refuse_les_etats_inexploitables(tmp_path):
    (tmp_path / "index.json").write_text("{}", encoding='utf-8')
    state = {"version": IndexesService.INDEX_STATE_VERSION, "sources_sig": "abc", "registres": {}}

    assert IndexesService._load_incremental_state(tmp_path, {"index_state": state}, "abc") == {}
    assert IndexesService._load_incremental_state(tmp_path, {}, "abc") is None
    assert IndexesService._load_incremental_state(tmp_path, {"index_state": state}, "autre") is None
    assert IndexesService._load_incremental_state(
        tmp_path, {"index_state": {**state, "version": 99}}, "abc") is None

    (tmp_path / "index.json").unlink()
    assert IndexesService._load_incremental_state(tmp_path, {"index_state": state}, "abc") is None


def test_load_existing_words_refuse_un_index_incompatible(tmp_path):
    sources = [{"key": "s0", "collection_id": "C1", "model_name": "m1"}]
    f = tmp_path / "index.json"

    f.write_text("{ pas du json", encoding='utf-8')
    assert IndexesService._load_existing_words(f, sources) is None

    f.write_text(json.dumps({"chat": ["p1 - c"]}), encoding='utf-8')   # ancien format plat
    assert IndexesService._load_existing_words(f, sources) is None

    f.write_text(json.dumps({"words": {}, "sources": {"s0": {"model_name": "m2"}}}), encoding='utf-8')
    assert IndexesService._load_existing_words(f, sources) is None    # modèle divergent

    f.write_text(json.dumps({"words": {}, "sources": {"s0": {"model_name": "m1"}, "s1": {}}}), encoding='utf-8')
    assert IndexesService._load_existing_words(f, sources) is None    # sources en trop

    f.write_text(json.dumps({"words": {"chat": ["s0::A_1 - c"]},
                             "sources": {"s0": {"model_name": "m1"}}}), encoding='utf-8')
    assert IndexesService._load_existing_words(f, sources) == {"chat": ["s0::A_1 - c"]}


# ── task_registres ───────────────────────────────────────────────────────────

def test_task_registres_marque_les_registres_conserves():
    """Les registres sautés sont 'done' même s'ils ne sont pas en tête de liste."""
    task = {
        'status': 'running',
        'processed': 10,          # = les 10 pages de R1 et R3, conservées
        'current': 'R2',
        'index_registres': [{"name": "R1", "pages": 5}, {"name": "R2", "pages": 7},
                            {"name": "R3", "pages": 5}],
        'index_skipped': ["R1", "R3"],
    }
    assert IndexesService.task_registres(task) == [
        {"name": "R1", "pages": 5, "status": "done"},
        {"name": "R2", "pages": 7, "status": "current"},
        {"name": "R3", "pages": 5, "status": "done"},
    ]


# ── Bout en bout ─────────────────────────────────────────────────────────────

def test_mise_a_jour_incrementale_ne_relit_que_les_registres_modifies(data_dir, monkeypatch):
    col = make_collection(data_dir, "COL", {
        "REGA": {"REGA_1": ["chat", "chien"], "REGA_2": ["rat"]},
        "REGB": {"REGB_1": ["souris"]},
    })

    assert build("idx1") == 'done'
    meta = IndexesService.get_index("idx1")
    assert meta["index_state"]["version"] == IndexesService.INDEX_STATE_VERSION
    assert set(meta["index_state"]["registres"]) == {"s0::REGA", "s0::REGB"}
    assert meta["coverage"] == {"s0::REGA": 2, "s0::REGB": 1}

    # Nouvelle page dans REGB uniquement.
    write_page_xml(col / "ocr" / "REGB" / "modelA" / "REGB_2.xml", ["hibou"])

    parsed = spy_process_xml(monkeypatch)
    assert build("idx1") == 'done'
    assert parsed == ["REGB_1.xml", "REGB_2.xml"]   # REGA n'a pas été relu

    incremental = read_index(data_dir, "idx1")
    assert "hibou" in incremental['words']
    assert "chat" in incremental['words']            # les pages conservées sont toujours là

    stats_incr = IndexesService.get_index("idx1")["stats"]
    coverage_incr = IndexesService.get_index("idx1")["coverage"]

    # Contrôle : une reconstruction complète du même état doit donner le même résultat.
    assert build("idx1", full=True) == 'done'
    assert normalized(read_index(data_dir, "idx1")) == normalized(incremental)
    assert IndexesService.get_index("idx1")["stats"] == stats_incr
    assert IndexesService.get_index("idx1")["coverage"] == coverage_incr == {"s0::REGA": 2, "s0::REGB": 2}


def test_page_supprimee_disparait_de_l_index(data_dir):
    col = make_collection(data_dir, "COL", {
        "REGA": {"REGA_1": ["chat"], "REGA_2": ["chien"]},
        "REGB": {"REGB_1": ["souris"]},
    })
    assert build("idx1") == 'done'
    assert "chien" in read_index(data_dir, "idx1")['words']

    (col / "ocr" / "REGA" / "modelA" / "REGA_2.xml").unlink()
    os.utime(col / "ocr" / "REGA" / "modelA")
    assert build("idx1") == 'done'

    words = read_index(data_dir, "idx1")['words']
    assert "chien" not in words          # la page purgée n'a pas été réintroduite
    assert "chat" in words
    assert IndexesService.get_index("idx1")["coverage"] == {"s0::REGA": 1, "s0::REGB": 1}


def test_registre_supprime_disparait_de_l_index(data_dir):
    import shutil

    col = make_collection(data_dir, "COL", {
        "REGA": {"REGA_1": ["chat"]},
        "REGB": {"REGB_1": ["souris"]},
    })
    assert build("idx1") == 'done'

    shutil.rmtree(col / "ocr" / "REGB")
    assert build("idx1") == 'done'

    meta = IndexesService.get_index("idx1")
    assert "souris" not in read_index(data_dir, "idx1")['words']
    assert set(meta["index_state"]["registres"]) == {"s0::REGA"}
    assert meta["stats"]["registres_count"] == 1


def test_index_sans_etat_est_reconstruit_integralement(data_dir, monkeypatch):
    """Cas du premier passage après déploiement : pas d'`index_state` ⇒ tout est relu."""
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}, "REGB": {"REGB_1": ["rat"]}})
    assert build("idx1") == 'done'

    meta = IndexesService.get_index("idx1")
    meta.pop("index_state")
    IndexesService._save_index_meta("idx1", meta)

    parsed = spy_process_xml(monkeypatch)
    assert build("idx1") == 'done'
    assert sorted(parsed) == ["REGA_1.xml", "REGB_1.xml"]
    assert IndexesService.get_index("idx1")["index_state"]["registres"].keys() == {"s0::REGA", "s0::REGB"}


def test_reconstruction_complete_ignore_un_checkpoint_incremental(data_dir, monkeypatch):
    """Une reconstruction complète ne doit pas hériter des registres « déjà faits » d'une mise
    à jour incrémentale interrompue : elle serait silencieusement dégradée."""
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}, "REGB": {"REGB_1": ["rat"]}})
    assert build("idx1") == 'done'

    cp = data_dir / "indexes" / "idx1" / "checkpoint.json"
    cp.write_text(json.dumps({
        "mode": "incremental", "words": {}, "total_words": 0,
        "done_registres": ["s0::REGA", "s0::REGB"], "done_state": {},
    }), encoding='utf-8')

    parsed = spy_process_xml(monkeypatch)
    assert build("idx1", full=True) == 'done'
    assert sorted(parsed) == ["REGA_1.xml", "REGB_1.xml"]


def test_reprise_purge_les_registres_disparus_pendant_la_pause(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}, "REGB": {"REGB_1": ["rat"]}})
    assert build("idx1") == 'done'

    import shutil
    shutil.rmtree(col / "ocr" / "REGB")

    # Checkpoint d'une indexation interrompue qui avait traité les deux registres.
    cp = data_dir / "indexes" / "idx1" / "checkpoint.json"
    cp.write_text(json.dumps({
        "mode": "full",
        "words": {"chat": ["s0::REGA_1 - c"], "rat": ["s0::REGB_1 - c"]},
        "total_words": 2,
        "done_registres": ["s0::REGA", "s0::REGB"],
        "done_state": {"s0::REGA": {"pages": 1, "sig": "x"}, "s0::REGB": {"pages": 1, "sig": "y"}},
    }), encoding='utf-8')

    assert build("idx1") == 'done'
    meta = IndexesService.get_index("idx1")
    assert "rat" not in read_index(data_dir, "idx1")['words']
    assert meta["stats"]["registres_count"] == 1
    assert meta["stats"]["total_word_occurrences"] == 1
    assert set(meta["index_state"]["registres"]) == {"s0::REGA"}


def test_on_plan_annonce_les_registres_conserves(data_dir):
    col = make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}, "REGB": {"REGB_1": ["rat"]}})
    assert build("idx1") == 'done'
    write_page_xml(col / "ocr" / "REGB" / "modelA" / "REGB_2.xml", ["hibou"])

    skipped = []
    assert build("idx1", on_plan=skipped.extend) == 'done'
    assert skipped == ["COL · modelA · REGA"]


def test_progression_demarre_aux_pages_conservees(data_dir):
    col = make_collection(data_dir, "COL", {
        "REGA": {f"REGA_{i}": ["chat"] for i in range(1, 6)},
        "REGB": {"REGB_1": ["rat"]},
    })
    assert build("idx1") == 'done'
    write_page_xml(col / "ocr" / "REGB" / "modelA" / "REGB_2.xml", ["hibou"])

    reports = []
    assert build("idx1", on_progress=lambda p, tot, cur: reports.append((p, tot))) == 'done'
    assert reports[0] == (5, 7)        # les 5 pages de REGA sont acquises d'emblée
    assert reports[-1] == (7, 7)


def test_changement_de_modele_ocr_force_une_reconstruction(data_dir):
    """Changer de modèle OCR invalide l'index existant : les pages viennent d'un autre corpus."""
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chat"]}})
    make_collection(data_dir, "COL", {"REGA": {"REGA_1": ["chien"]}}, model="modelB")

    assert build("idx1", model="modelA") == 'done'
    assert build("idx1", model="modelB") == 'done'

    words = read_index(data_dir, "idx1")['words']
    assert "chien" in words and "chat" not in words
