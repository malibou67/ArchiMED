"""Recherche en mot entier.

Par défaut, un terme trouve tout mot qui le contient : chercher « par » remonte aussi
« parfois » et « separe », ce qui noie le mot cherché. La case « mot entier » ne garde que le
mot lui-même — et, si la ressemblance est active, ses graphies proches (fautes d'OCR), jamais
un mot qui ne fait que le contenir.
"""
from services import IndexesService

from conftest import build, make_collection

REGISTRES = {"REGA": {"REGA_1": ["par", "noir"], "REGA_2": ["parfois"], "REGA_3": ["separe"]},
             "REGB": {"REGB_1": ["syphilis"], "REGB_2": ["syphllis"], "REGB_3": ["neurosyphilis"]}}


def indexe(data_dir, index_id="idx1"):
    IndexesService._vocab_cache.clear()
    make_collection(data_dir, "COL", REGISTRES)
    build(index_id)
    return index_id


def pages(result):
    """Noms des pages trouvées, sans le préfixe de source (« s0::REGA_1 » → « REGA_1 »)."""
    return {IndexesService._display_page_name(p['page_name']) for p in result['pages']}


def test_par_defaut_un_terme_trouve_les_mots_qui_le_contiennent(data_dir):
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "par")

    assert pages(result) == {"REGA_1", "REGA_2", "REGA_3"}
    assert result['whole_word'] is False


def test_en_mot_entier_seul_le_mot_lui_meme_est_trouve(data_dir):
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "par", whole_word=True)

    assert pages(result) == {"REGA_1"}
    assert result['whole_word'] is True


def test_mot_entier_et_ressemblance_gardent_les_fautes_d_ocr(data_dir):
    """« syphllis » est une faute d'OCR de « syphilis » ; « neurosyphilis » est un autre mot."""
    index_id = indexe(data_dir)

    result = IndexesService.search_words(index_id, "syphilis", fuzzy_threshold=85, whole_word=True)

    assert pages(result) == {"REGB_1", "REGB_2"}


def test_le_flux_et_l_export_csv_suivent_le_mot_entier(data_dir):
    index_id = indexe(data_dir)

    streamed = [e for e in IndexesService.search_words_iter(index_id, "par", whole_word=True)
                if e['type'] == 'result'][0]['result']
    rows = IndexesService.resolve_result_pages(index_id, "par", whole_word=True)

    assert pages(streamed) == {"REGA_1"}
    assert [r['page'] for r in rows] == ["REGA_1"]
