"""Sélections de pages : garder une partie des résultats d'une recherche, puis chercher dedans.

Une recherche repartait toujours de tout l'index. Une sélection fige les pages gardées d'une
recherche — triées dans Excel, pas toutes (voir `test_results_workbook`) — et la recherche
suivante peut se faire dedans, en entonnoir. Les invariants tenus ici : une sélection rend
exactement les pages enregistrées ; une recherche dans une sélection n'en sort jamais, quels que
soient ses autres filtres ; la chaîne des étapes est recopiée, donc indépendante des sélections
intermédiaires ; un id ne désigne jamais autre chose qu'un fichier de sélection.
"""
import json

import pytest
from fastapi import HTTPException

from selections_service import SelectionNotFound, SelectionsService
from services import IndexesService

from conftest import build, make_collection

REGISTRES = {"REGA": {"REGA_1": ["chat", "noir"], "REGA_2": ["chat", "blanc"],
                      "REGA_3": ["chat", "noirci"]},
             "REGB": {"REGB_1": ["chat", "gris"], "REGB_2": ["merle"]}}
INCONNUE = "sel_20990101000000_000000"


@pytest.fixture
def corpus(data_dir):
    IndexesService._vocab_cache.clear()
    make_collection(data_dir, "COL", REGISTRES)
    build("idx1")
    return data_dir


def page(nom):
    """Nom d'une page tel que la recherche le rend (préfixe de source compris)."""
    return next(p['page_name'] for p in IndexesService.search_words("idx1", "chat")['pages']
                if IndexesService._display_page_name(p['page_name']) == nom)


def noms(pages):
    return {IndexesService._display_page_name(p) for p in pages}


def trouvees(result):
    return noms(p['page_name'] for p in result['pages'])


def etape(query="chat", found=4, **kw):
    return {"query": query, "whole_word": False, "fuzzy_threshold": None, "excluded_terms": [],
            "year_from": None, "year_to": None, "found": found, **kw}


def enregistrer(nom, *pages, steps=None):
    return SelectionsService.create("idx1", nom, [page(p) for p in pages],
                                    [etape()] if steps is None else steps)


# ── Enregistrer, lister, supprimer ───────────────────────────────────────────

def test_une_selection_rend_exactement_les_pages_gardees(corpus):
    sel = enregistrer("chats sauf gris", "REGA_1", "REGA_2", "REGA_1")

    assert sel['count'] == 2                      # doublon écarté
    assert sel['steps'] == [{**etape(), 'kept': 2}]
    assert sel['stale'] is False
    assert noms(SelectionsService.pages("idx1", sel['id'])) == {"REGA_1", "REGA_2"}


def test_la_liste_va_de_la_plus_recente_a_la_plus_ancienne_sans_les_pages(corpus):
    ancienne = enregistrer("ancienne", "REGA_1")
    recente = enregistrer("récente", "REGA_2")

    liste = SelectionsService.summaries("idx1")

    assert [s['id'] for s in liste] == [recente['id'], ancienne['id']]
    assert all('pages' not in s for s in liste)


def test_supprimer_retire_le_resume_et_les_pages(corpus):
    sel = enregistrer("à jeter", "REGA_1")

    SelectionsService.delete("idx1", sel['id'])

    assert SelectionsService.summaries("idx1") == []
    assert list((corpus / "indexes" / "idx1" / "selections").iterdir()) == []
    with pytest.raises(SelectionNotFound):
        SelectionsService.delete("idx1", sel['id'])


def test_un_id_ne_designe_qu_un_fichier_de_selection(corpus):
    enregistrer("vraie", "REGA_1")

    for faux in ("../metadata", "..\\metadata", "sel_x", INCONNUE):
        with pytest.raises(SelectionNotFound):
            SelectionsService.pages("idx1", faux)


def test_rien_a_enregistrer_est_refuse(corpus):
    with pytest.raises(ValueError):
        SelectionsService.create("idx1", "vide", [], [etape()])
    with pytest.raises(ValueError):
        SelectionsService.create("idx1", "  ", [page("REGA_1")], [etape()])
    with pytest.raises(SelectionNotFound):
        SelectionsService.create("inconnu", "x", ["p"], [etape()])


# ── L'entonnoir ──────────────────────────────────────────────────────────────

def test_la_chaine_est_recopiee_et_sa_derniere_etape_compte_les_pages_gardees(corpus):
    chaine = [etape("chat", found=4, kept=3), etape("noir", found=2)]

    sel = enregistrer("chats › noir", "REGA_1", "REGA_3", steps=chaine)

    assert [(s['query'], s['found'], s['kept']) for s in sel['steps']] == [
        ("chat", 4, 3), ("noir", 2, 2)]
    assert chaine[-1].get('kept') is None         # la chaîne reçue n'est pas modifiée
    # Un classeur sans feuille d'étapes : une sélection sans chaîne, mais valide.
    assert enregistrer("sans chaîne", "REGA_1", steps=[])['steps'] == []


def test_une_recherche_dans_une_selection_n_en_sort_pas(corpus):
    sel = enregistrer("A1 A3 B1", "REGA_1", "REGA_3", "REGB_1")
    dedans = SelectionsService.pages("idx1", sel['id'])

    assert trouvees(IndexesService.search_words("idx1", "chat", within=dedans)) == {
        "REGA_1", "REGA_3", "REGB_1"}
    # Avec ses autres filtres : « noir » sans mot entier exclut aussi « noirci ».
    assert trouvees(IndexesService.search_words("idx1", "chat", exclude="noir",
                                                within=dedans)) == {"REGB_1"}
    assert trouvees(IndexesService.search_words("idx1", "chat", exclude="noir", whole_word=True,
                                                within=dedans)) == {"REGA_3", "REGB_1"}
    # Le flux et l'export CSV suivent.
    flux = [e for e in IndexesService.search_words_iter("idx1", "noir", within=dedans)
            if e['type'] == 'result'][0]['result']
    assert trouvees(flux) == {"REGA_1", "REGA_3"}
    assert {r['page'] for r in IndexesService.resolve_result_pages(
        "idx1", "gris", within=dedans)} == {"REGB_1"}


def test_une_selection_d_avant_un_changement_de_sources_est_signalee(corpus):
    enregistrer("avant", "REGA_1")
    meta_file = corpus / "indexes" / "idx1" / "metadata.json"
    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    meta['sources'][0]['model_name'] = "autre_modele"
    meta_file.write_text(json.dumps(meta), encoding='utf-8')

    assert SelectionsService.summaries("idx1")[0]['stale'] is True


# ── Routes ───────────────────────────────────────────────────────────────────

FILTRES = dict(year_from=None, year_to=None, fuzzy_threshold=None, whole_word=False, exclude=None)


def test_les_routes_listent_cherchent_dedans_et_suppriment(corpus):
    from routers import indexes as routes
    sel = enregistrer("chats noirs", "REGA_1")

    assert [s['id'] for s in routes.list_selections("idx1")] == [sel['id']]
    found = routes.search_in_index("idx1", q="chat", within=sel['id'], **FILTRES)
    assert trouvees(found) == {"REGA_1"}
    assert routes.delete_selection("idx1", sel['id']) == {"deleted": sel['id']}
    with pytest.raises(HTTPException) as absent:
        routes.list_selections("inconnu")
    assert absent.value.status_code == 404


def test_une_selection_inconnue_est_un_404_partout(corpus):
    from routers import indexes as routes
    appels = [
        lambda: routes.search_in_index("idx1", q="chat", within=INCONNUE, **FILTRES),
        # Le flux résout la sélection avant d'ouvrir le générateur : un vrai statut HTTP.
        lambda: routes.search_in_index_stream("idx1", q="chat", within=INCONNUE, **FILTRES),
        lambda: routes.export_results_csv("idx1", q="chat", within=INCONNUE, **FILTRES),
        lambda: routes.export_results_xlsx("idx1", q="chat", within=INCONNUE, lang="fr", **FILTRES),
        lambda: routes.export_pages_zip("idx1", q="chat", within=INCONNUE,
                                        download_token="tok-inconnue", **FILTRES),
        lambda: routes.delete_selection("idx1", INCONNUE),
    ]
    for appel in appels:
        with pytest.raises(HTTPException) as refus:
            appel()
        assert refus.value.status_code == 404
