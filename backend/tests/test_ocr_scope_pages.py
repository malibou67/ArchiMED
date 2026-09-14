"""Noms de pages d'un périmètre OCR : ils viennent du disque, jamais du motif de pagination.

Le bug d'origine : la page OCR régénérait les noms depuis `pages_pattern` +
`[pages_start, pages_end]` − `pages_gaps`, ce qui perd la largeur du champ numérique. Sur un
registre nommé `X_001.jpg` … `X_600.jpg`, elle demandait `X_1.jpg` … `X_99.jpg` — 99 pages
« introuvables » par registre, à chaque lancement, et 99 pages qui paraissaient éternellement
non transcrites puisque leur stem ne correspondait à aucun XML.

Ces tests tiennent les deux bouts : `scope_pages` rend les noms du disque, et la pagination du
metadata est incapable de les reconstituer (le test du remplissage mixte le démontre).
"""
import pytest

import ocr_service as ocr
from ocr_service import NoPagesToProcess, OcrService
from services import _diagnose_pagination
from task_service import TaskService


@pytest.fixture
def ocr_dir(data_dir, monkeypatch):
    """`data_dir`, mais aussi pour `ocr_service` : il fait `from services import DATA_DIR`,
    une liaison de module que patcher `services.DATA_DIR` ne redirige pas."""
    monkeypatch.setattr(ocr, 'DATA_DIR', str(data_dir))
    return data_dir


def _registre(data_dir, col, reg, noms, xml=(), modele="modelA"):
    """Dépose des scans (seuls les noms comptent) et, au besoin, leurs PAGE XML."""
    d = data_dir / "collections" / col / "scans" / reg
    d.mkdir(parents=True, exist_ok=True)
    for n in noms:
        (d / n).write_bytes(b'')
    if xml:
        o = data_dir / "collections" / col / "ocr" / reg / modele
        o.mkdir(parents=True, exist_ok=True)
        for stem in xml:
            (o / f"{stem}.xml").write_text("<PcGts/>", encoding='utf-8')
    return d


def _pages(tache):
    """Pages retenues par l'enfilage : `_HIDDEN` les masque de la vue publique."""
    return [p['page'] for p in TaskService._tasks[tache['id']]['pages']]


# ── Le cœur du bug ────────────────────────────────────────────────────────

def test_le_motif_ne_peut_pas_reconstituer_les_noms(ocr_dir):
    """Test-preuve : deux registres aux noms différents donnent la **même** pagination.

    `scope_pages` les distingue parce qu'il lit le disque ; le motif, lui, a perdu la
    largeur du champ numérique et ne peut plus choisir entre `X_80.jpg` et `X_080.jpg`."""
    noms = ["X_1.jpg", "X_80.jpg", "X_100.jpg"]
    _registre(ocr_dir, "COL", "X", noms)

    assert OcrService.scope_pages([{"collection": "COL", "registre": "X"}]) == {"COL": {"X": noms}}

    diag = _diagnose_pagination(noms)
    diag_rempli = _diagnose_pagination(["X_001.jpg", "X_080.jpg", "X_100.jpg"])
    assert diag['pattern'] == diag_rempli['pattern'] == "X_{num}.jpg"
    assert (diag['start'], diag['end']) == (diag_rempli['start'], diag_rempli['end']) == (1, 100)
    assert diag['gaps'] == diag_rempli['gaps'] == [n for n in range(2, 100) if n != 80]


def test_remplissage_mixte_dans_un_meme_registre(ocr_dir):
    """Les largeurs peuvent varier d'une page à l'autre : aucune largeur persistée ne
    marcherait, seul `iterdir()` connaît les noms."""
    noms = ["X_9.jpg", "X_79.jpg", "X_080.jpg", "X_100.jpg"]
    _registre(ocr_dir, "COL", "X", noms)
    assert OcrService.scope_pages(None)["COL"]["X"] == noms


def test_le_remplissage_ne_fait_plus_ecarter_de_pages(ocr_dir, tasks_dir, monkeypatch):
    """Un registre entièrement rempli sur 3 chiffres s'enfile sans une seule page écartée."""
    monkeypatch.setattr(ocr, 'DATA_DIR', str(ocr_dir))
    noms = [f"X_{n:03d}.jpg" for n in range(1, 121)]
    _registre(ocr_dir, "COL", "X", noms)

    tache = OcrService.enqueue("seg", "modelA", [], scopes=[{"collection": "COL", "registre": "X"}])
    assert tache['total'] == 120
    assert 'skipped_missing' not in tache


# ── Filtres d'état ────────────────────────────────────────────────────────

def test_only_missing_compare_des_stems_reels(ocr_dir):
    """La page `X_080.jpg` transcrite sort du « manquant » — avant, le stem demandé `X_80`
    ne correspondait à aucun XML et la page repartait à chaque lancement."""
    _registre(ocr_dir, "COL", "X", ["X_001.jpg", "X_080.jpg", "X_100.jpg"], xml=["X_080"])
    scope = [{"collection": "COL", "registre": "X"}]

    assert OcrService.scope_pages(scope, "modelA", 'missing') == {
        "COL": {"X": ["X_001.jpg", "X_100.jpg"]}}
    assert OcrService.scope_pages(scope, "modelA", 'done') == {"COL": {"X": ["X_080.jpg"]}}
    assert OcrService.scope_pages(scope, "modelA", 'all') == {
        "COL": {"X": ["X_001.jpg", "X_080.jpg", "X_100.jpg"]}}


def test_only_exige_un_modele_et_un_filtre_connu(ocr_dir):
    _registre(ocr_dir, "COL", "X", ["X_1.jpg"])
    with pytest.raises(ValueError):
        OcrService.scope_pages(None, None, 'missing')
    with pytest.raises(ValueError):
        OcrService.scope_pages(None, "modelA", 'inconnu')


# ── Périmètres ────────────────────────────────────────────────────────────

def test_perimetre_collection_registre_et_total(ocr_dir):
    _registre(ocr_dir, "COL", "A", ["A_1.jpg"])
    _registre(ocr_dir, "COL", "B", ["B_1.jpg"])
    _registre(ocr_dir, "AUTRE", "C", ["C_1.jpg"])

    assert set(OcrService.scope_pages([{"collection": "COL"}])["COL"]) == {"A", "B"}
    assert OcrService.scope_pages([{"collection": "COL", "registre": "A"}]) == {
        "COL": {"A": ["A_1.jpg"]}}
    assert set(OcrService.scope_pages(None)) == {"COL", "AUTRE"}
    assert set(OcrService.scope_pages([])) == {"COL", "AUTRE"}


def test_dossiers_techniques_exclus(ocr_dir):
    _registre(ocr_dir, "COL", "A", ["A_1.jpg"])
    _registre(ocr_dir, "__cache", "Z", ["Z_1.jpg"])
    assert set(OcrService.scope_pages(None)) == {"COL"}


def test_missing_pages_garde_son_contrat_a_plat(ocr_dir):
    """`/api/ocr/missing` rend toujours une liste de {collection, registre, page}."""
    _registre(ocr_dir, "COL", "X", ["X_1.jpg", "X_2.jpg"], xml=["X_1"])
    assert OcrService.missing_pages("modelA") == [
        {"collection": "COL", "registre": "X", "page": "X_2.jpg"}]


# ── Enfilage ──────────────────────────────────────────────────────────────

def test_enqueue_fusionne_perimetre_et_pages_sans_doublon(ocr_dir, tasks_dir, monkeypatch):
    monkeypatch.setattr(ocr, 'DATA_DIR', str(ocr_dir))
    _registre(ocr_dir, "COL", "X", ["X_1.jpg", "X_2.jpg", "X_3.jpg"])

    tache = OcrService.enqueue(
        "seg", "modelA",
        [{"collection": "COL", "registre": "X", "page": "X_2.jpg"}],
        scopes=[{"collection": "COL", "registre": "X"}])

    # La page nommée reste en tête (l'ordre de la liste est l'ordre de traitement) et n'est
    # pas traitée deux fois.
    assert _pages(tache) == ["X_2.jpg", "X_1.jpg", "X_3.jpg"]
    assert tache['total'] == 3


def test_enqueue_perimetre_filtre_sur_les_manquantes(ocr_dir, tasks_dir, monkeypatch):
    monkeypatch.setattr(ocr, 'DATA_DIR', str(ocr_dir))
    _registre(ocr_dir, "COL", "X", ["X_1.jpg", "X_2.jpg"], xml=["X_1"])

    tache = OcrService.enqueue("seg", "modelA", [], scopes=[{"collection": "COL"}],
                               scope_only='missing')
    assert _pages(tache) == ["X_2.jpg"]


def test_message_ne_conseille_plus_la_resynchronisation(ocr_dir, tasks_dir, monkeypatch):
    """Resynchroniser ne pouvait rien corriger : les compteurs étaient déjà justes, c'étaient
    les noms qui étaient faux."""
    monkeypatch.setattr(ocr, 'DATA_DIR', str(ocr_dir))
    _registre(ocr_dir, "COL", "X", ["X_1.jpg"])

    with pytest.raises(NoPagesToProcess) as e:
        OcrService.enqueue("seg", "modelA", [
            {"collection": "COL", "registre": "X", "page": "X_404.jpg"}])
    assert "esynchronis" not in str(e.value)


# ── Ordre de traitement ───────────────────────────────────────────────────

def test_les_pages_hors_motif_suivent_leur_page_principale(ocr_dir):
    """`X_110_1.jpg` se range après `X_110.jpg`, pas en première position.

    L'ancien tri ne lisait que le dernier nombre avant l'extension : les pages bis d'un
    registre se retrouvaient toutes en tête, et la tâche les traitait dans le désordre."""
    _registre(ocr_dir, "COL", "X",
              ["X_9.jpg", "X_10.jpg", "X_110.jpg", "X_110_1.jpg", "X_110_2.jpg", "X_111.jpg"])
    assert OcrService.scope_pages(None)["COL"]["X"] == [
        "X_9.jpg", "X_10.jpg", "X_110.jpg", "X_110_1.jpg", "X_110_2.jpg", "X_111.jpg"]
