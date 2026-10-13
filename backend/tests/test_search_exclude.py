"""Mots à exclure.

La recherche garde les pages qui contiennent tous les termes ; les mots exclus en retirent
celles qui contiennent au moins l'un d'eux. Ils suivent les mêmes règles de correspondance que
les termes recherchés : sans « mot entier », exclure « noir » écarte aussi « noirci ».
"""
from services import IndexesService

from conftest import build, make_collection

REGISTRES = {"REGA": {"REGA_1": ["chat", "noir"], "REGA_2": ["chat", "blanc"],
                      "REGA_3": ["chat", "noirci"], "REGA_4": ["chat", "gris"]}}


def indexe(data_dir, index_id="idx1"):
    IndexesService._vocab_cache.clear()
    make_collection(data_dir, "COL", REGISTRES)
    build(index_id)
    return index_id


def pages(result):
    """Noms des pages trouvées, sans le préfixe de source (« s0::REGA_1 » → « REGA_1 »)."""
    return {IndexesService._display_page_name(p['page_name']) for p in result['pages']}


def test_sans_exclusion_rien_n_est_ecarte(data_dir):
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "chat")

    assert pages(result) == {"REGA_1", "REGA_2", "REGA_3", "REGA_4"}
    assert result['excluded_terms'] == []


def test_une_page_qui_contient_un_mot_exclu_est_ecartee(data_dir):
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "chat", exclude="blanc")

    assert pages(result) == {"REGA_1", "REGA_3", "REGA_4"}
    assert result['excluded_terms'] == ["blanc"]


def test_un_seul_des_mots_exclus_suffit(data_dir):
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "chat", exclude="blanc Gris")

    assert pages(result) == {"REGA_1", "REGA_3"}
    assert result['excluded_terms'] == ["blanc", "gris"]


def test_l_exclusion_suit_le_mot_entier(data_dir):
    index_id = indexe(data_dir)

    contient = IndexesService.search_words(index_id, "chat", exclude="noir")
    entier = IndexesService.search_words(index_id, "chat", exclude="noir", whole_word=True)

    assert pages(contient) == {"REGA_2", "REGA_4"}
    assert pages(entier) == {"REGA_2", "REGA_3", "REGA_4"}


def test_les_mots_vides_exclus_sont_respectes(data_dir):
    """Un mot vide est ignoré dans la requête, pas dans l'exclusion : il y est demandé."""
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "chat", exclude="et")

    assert result['excluded_terms'] == ["et"]


def test_le_flux_et_l_export_csv_suivent_l_exclusion(data_dir):
    index_id = indexe(data_dir)

    streamed = [e for e in IndexesService.search_words_iter(index_id, "chat", exclude="noir")
                if e['type'] == 'result'][0]['result']
    rows = IndexesService.resolve_result_pages(index_id, "chat", exclude="noir")

    assert pages(streamed) == {"REGA_2", "REGA_4"}
    assert sorted(r['page'] for r in rows) == ["REGA_2", "REGA_4"]
