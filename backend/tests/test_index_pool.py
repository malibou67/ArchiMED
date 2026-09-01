"""Indexation parallèle : le pool de process doit produire exactement l'index du séquentiel.

L'invariant qui compte est l'identité **à l'octet** de `index.json` : le pool rend les pages
dans le désordre, et c'est la fusion par registre complet, dans l'ordre des fichiers, qui doit
rétablir l'ordre d'insertion du chemin séquentiel. Le reste du fichier vérifie que le pool
n'affaiblit rien des garanties existantes (annulation, pause, page illisible).

Ces tests lèvent l'épingle posée par la fixture `_sequential_indexing` de conftest.
"""
import json

from services import IndexesService

from conftest import build, make_collection

# `_nettoyer_texte` ne garde que les tokens `isalpha()` : les mots de test doivent être
# purement alphabétiques, sinon ils sont filtrés et le corpus ne distingue plus les pages.
ALPHABET = "abcdefghijklmnopqrst"

REGISTRES = {f"REG{r}": {f"REG{r}_{p}": [f"page{ALPHABET[p - 1]}", "commun", f"registre{ALPHABET[r - 1]}"]
                         for p in range(1, 21)}
             for r in range(1, 5)}   # 4 registres × 20 pages = 80 XML


def use_pool(monkeypatch, workers=2):
    """Force le chemin parallèle, quel que soit le nombre de cœurs de la machine."""
    monkeypatch.setenv('ARCHIMED_INDEX_WORKERS', str(workers))
    monkeypatch.setenv('ARCHIMED_INDEX_POOL_MIN_PAGES', '1')


def raw_index(data_dir, index_id):
    return (data_dir / "indexes" / index_id / "index.json").read_text(encoding='utf-8')


def test_index_parallele_identique_au_sequentiel(data_dir, monkeypatch):
    make_collection(data_dir, "COL", REGISTRES)

    assert build("seq") == 'done'
    sequentiel = raw_index(data_dir, "seq")

    use_pool(monkeypatch)
    assert build("par") == 'done'

    # À l'octet : même contenu ET même ordre d'insertion des occurrences.
    assert raw_index(data_dir, "par") == sequentiel

    meta_seq = json.loads((data_dir / "indexes" / "seq" / "metadata.json").read_text(encoding='utf-8'))
    meta_par = json.loads((data_dir / "indexes" / "par" / "metadata.json").read_text(encoding='utf-8'))
    assert meta_par['stats'] == meta_seq['stats']
    assert meta_par['status'] == 'ready' and meta_par['build'] is None


def test_le_pool_sert_vraiment(data_dir, monkeypatch):
    """Garde-fou des autres tests : sans lui, ils passeraient en séquentiel sans le dire."""
    make_collection(data_dir, "COL", REGISTRES)
    use_pool(monkeypatch)

    appels = []
    original = IndexesService._process_xml

    def compte(xml_path, mots, total, prefix=""):
        appels.append(xml_path.name)
        return original(xml_path, mots, total, prefix)

    monkeypatch.setattr(IndexesService, '_process_xml', staticmethod(compte))
    assert build("idx1") == 'done'
    assert appels == []   # aucune page n'est passée par le chemin séquentiel


def test_page_illisible_ignoree_sans_faire_echouer_le_run(data_dir, monkeypatch):
    col = make_collection(data_dir, "COL", REGISTRES)
    (col / "ocr" / "REG2" / "modelA" / "REG2_5.xml").write_text("<PcGts><oups", encoding='utf-8')
    use_pool(monkeypatch)

    assert build("idx1") == 'done'
    words = json.loads(raw_index(data_dir, "idx1"))['words']
    # La page cassée est perdue, ses voisines non — et son registre reste indexé.
    assert all("REG2_5" not in occ for occ in words.get("pagee", []))
    assert any("REG2_6" in occ for occ in words["pagef"])
    assert any("REG2_" in occ for occ in words["registreb"])


def test_annulation_pendant_le_pool(data_dir, monkeypatch):
    make_collection(data_dir, "COL", REGISTRES)
    use_pool(monkeypatch)

    # Premier index complet : c'est lui qui doit survivre à l'annulation de la reconstruction.
    assert build("idx1") == 'done'
    avant = raw_index(data_dir, "idx1")

    tours = []
    assert build("idx1", full=True,
                 should_cancel=lambda: bool(tours.append(1)) or len(tours) > 3) == 'cancelled'

    index_dir = data_dir / "indexes" / "idx1"
    assert not (index_dir / "index.json.tmp").exists()   # staging jeté
    assert raw_index(data_dir, "idx1") == avant           # index précédent intact


def test_pause_puis_reprise_pendant_le_pool(data_dir, monkeypatch):
    make_collection(data_dir, "COL", REGISTRES)
    use_pool(monkeypatch)

    assert build("ref") == 'done'
    attendu = raw_index(data_dir, "ref")

    # Pause après quelques tours : au moins un registre est bouclé, les autres restent à faire.
    tours = []
    assert build("idx1",
                 should_pause=lambda: bool(tours.append(1)) or len(tours) > 3) == 'paused'

    index_dir = data_dir / "indexes" / "idx1"
    checkpoint = index_dir / "checkpoint.json"
    assert checkpoint.exists()
    cp = json.loads(checkpoint.read_text(encoding='utf-8'))
    assert len(cp['done_registres']) < len(REGISTRES)   # la pause a bien coupé au milieu

    # Reprise : l'index final est identique à celui d'un run d'une traite. Une occurrence en
    # double (registre du checkpoint rejoue) se verrait immédiatement dans la comparaison.
    assert IndexesService.generate_index("idx1") == 'done'
    assert raw_index(data_dir, "idx1") == attendu
    assert not checkpoint.exists()


def test_repli_sequentiel_si_le_pool_ne_demarre_pas(data_dir, monkeypatch):
    make_collection(data_dir, "COL", REGISTRES)
    use_pool(monkeypatch)

    assert build("ref") == 'done'
    attendu = raw_index(data_dir, "ref")

    import concurrent.futures

    def boom(*a, **k):
        raise OSError("pas de process disponible")

    monkeypatch.setattr(concurrent.futures, 'ProcessPoolExecutor', boom)
    assert build("idx1") == 'done'
    assert raw_index(data_dir, "idx1") == attendu


def test_le_checkpoint_ne_reecrit_pas_tout_lindex(data_dir, monkeypatch):
    """L'invariant qui justifie le découpage en segments : une sauvegarde coûte le registre qui
    vient d'être bouclé, pas l'index accumulé depuis le début.

    Sans lui le coût du run est quadratique — quelques Mo de JSON au cinquième registre, près de
    deux cents au soixantième — et il est payé en écritures sur le partage, depuis le thread même
    qui réalimente le pool : les workers se retrouvent à sec pendant que ça écrit."""
    import services

    make_collection(data_dir, "COL", REGISTRES)
    use_pool(monkeypatch)
    monkeypatch.setattr(services, 'CHECKPOINT_THROTTLE_S', 0.0)   # un checkpoint par registre

    # Pause une fois trois registres bouclés : `wait` peut rendre plusieurs pages d'un coup,
    # compter les tours de boucle ne dirait rien du nombre de registres réellement terminés.
    vu = {'pages': 0}
    assert build("idx1",
                 on_progress=lambda p, t, c, page=None: vu.update(pages=p),
                 should_pause=lambda: vu['pages'] >= 60) == 'paused'

    parts = sorted((data_dir / "indexes" / "idx1" / "checkpoint.parts").glob("[0-9]*.json"))
    assert len(parts) > 1   # plusieurs registres bouclés, donc plusieurs segments

    # Un registre n'apparaît que dans le segment écrit après lui : aucun segment ne réécrit ce
    # qu'un précédent avait déjà sauvegardé.
    deja_vus = set()
    for part in parts:
        segment = json.loads(part.read_text(encoding='utf-8'))
        registres = {occ.split('_')[0] for occs in segment.values() for occ in occs}
        assert registres, f"{part.name} est vide"
        assert not (registres & deja_vus), f"{part.name} réécrit {registres & deja_vus}"
        deja_vus |= registres


def test_reprise_depuis_un_checkpoint_de_lancienne_version(data_dir, monkeypatch):
    """Un checkpoint monolithique laissé par une version antérieure doit être **repris**, pas
    jeté : mettre l'application à jour ne doit pas condamner une indexation en pause à tout
    refaire. Ses occurrences sont relues telles quelles, et la suite du run les prolonge."""
    make_collection(data_dir, "COL", REGISTRES)
    use_pool(monkeypatch)

    assert build("ref") == 'done'
    attendu = raw_index(data_dir, "ref")
    mots_ref = json.loads(attendu)['words']

    # Checkpoint à l'ancien format : les occurrences en ligne, sous 'words'.
    fait = "s0::REG1"
    mots = {mot: [o for o in occs if o.startswith(f"{fait}_")] for mot, occs in mots_ref.items()}
    mots = {mot: occs for mot, occs in mots.items() if occs}
    checkpoint = data_dir / "indexes" / "idx1" / "checkpoint.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    build("idx1", should_cancel=lambda: True)   # matérialise l'index sans rien indexer
    checkpoint.write_text(json.dumps({
        "mode": "full", "words": mots,
        "total_words": sum(len(o) for o in mots.values()),
        "done_registres": [fait], "done_state": {},
    }), encoding='utf-8')

    assert IndexesService.generate_index("idx1") == 'done'
    assert raw_index(data_dir, "idx1") == attendu   # à l'octet : rien de perdu, rien en double
    assert not checkpoint.exists()
