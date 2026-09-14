"""Progression d'une recherche, et cache de l'index qu'elle partage avec le vocabulaire.

Une recherche sur un gros index dure une minute : l'essentiel part dans la relecture de
`index.json` (jusqu'à 190 Mo), le reste dans le parcours du vocabulaire. La page de recherche
n'avait qu'un spinner pour décrire cette minute. `search_words_iter` la raconte — chargement,
analyse, parcours, constitution des résultats — et passe désormais par le cache d'index déjà
utilisé par le vocabulaire et les statistiques, ce qui rend les recherches suivantes immédiates.

Les invariants tenus ici : le flux décrit ce qui se passe vraiment (pas de phase `load`
annoncée quand rien n'est relu), et `search_words` — dont dépendent les exports CSV/ZIP —
rend exactement ce que rend le flux.
"""
from services import IndexesService

from conftest import build, make_collection

REGISTRES = {"REGA": {"REGA_1": ["chat", "noir"], "REGA_2": ["rat"]},
             "REGB": {"REGB_1": ["chat", "blanc"], "REGB_2": ["merle"]}}


def indexe(data_dir, index_id="idx1"):
    """Construit un index de test en repartant d'un cache vide (état de classe, partagé)."""
    IndexesService._vocab_cache.clear()
    make_collection(data_dir, "COL", REGISTRES)
    build(index_id)
    return index_id


def phases(events):
    return [e['phase'] for e in events if e['type'] == 'progress']


def test_le_flux_raconte_le_chargement_puis_le_parcours(data_dir):
    index_id = indexe(data_dir)

    events = list(IndexesService.search_words_iter(index_id, "chat"))

    # L'ordre est celui de l'attente : lire le fichier, l'analyser, parcourir le vocabulaire,
    # assembler les pages. Les doublons de `load` (un par bloc lu) sont la progression elle-même.
    assert [p for i, p in enumerate(phases(events)) if i == 0 or p != phases(events)[i - 1]] == [
        'load', 'parse', 'scan', 'build']
    assert events[-1]['type'] == 'result'


def test_la_phase_de_chargement_compte_des_octets(data_dir):
    index_id = indexe(data_dir)

    events = list(IndexesService.search_words_iter(index_id, "chat"))
    load = [e for e in events if e.get('phase') == 'load']

    # Une barre déterminée n'a de sens que si le total est connu et l'avancée croissante.
    assert load[0]['total'] > 0
    assert [e['current'] for e in load] == sorted(e['current'] for e in load)
    assert load[-1]['current'] == load[-1]['total']


def test_la_seconde_recherche_ne_relit_pas_le_fichier(data_dir):
    """Le gain réel : c'est le parse d'index.json qui coûte la minute, pas le parcours."""
    index_id = indexe(data_dir)
    list(IndexesService.search_words_iter(index_id, "chat"))

    events = list(IndexesService.search_words_iter(index_id, "rat"))

    assert 'load' not in phases(events)
    assert 'parse' not in phases(events)
    assert phases(events)[0] == 'scan'


def test_le_flux_rend_le_meme_resultat_que_la_recherche(data_dir):
    """`search_words` draine ce flux : les exports CSV/ZIP en dépendent."""
    index_id = indexe(data_dir)

    streamed = [e for e in IndexesService.search_words_iter(index_id, "chat")
                if e['type'] == 'result'][0]['result']
    direct = IndexesService.search_words(index_id, "chat")

    assert direct == streamed
    assert direct['count'] == 2
    # Le bloc `sources` transite maintenant par le cache : sans lui, plus de registre ni de
    # collection d'origine sur les résultats.
    assert {p['registre'] for p in direct['pages']} == {"REGA", "REGB"}
    assert all(p['source']['model_name'] == "modelA" for p in direct['pages'])


def test_un_index_absent_ne_donne_pas_de_resultat(data_dir):
    IndexesService._vocab_cache.clear()

    events = list(IndexesService.search_words_iter("inconnu", "chat"))

    assert events == [{"type": "result", "result": None}]
    assert IndexesService.search_words("inconnu", "chat") is None


def test_le_vocabulaire_survit_a_une_recherche(data_dir):
    """La recherche remplit le cache sans construire `base_entries` (elle n'en a pas l'usage) :
    le vocabulaire, qui arrive derrière, doit le bâtir au lieu de servir un cache à moitié vide."""
    index_id = indexe(data_dir)
    list(IndexesService.search_words_iter(index_id, "chat"))

    total, entries = IndexesService._get_vocabulary_base(index_id)

    assert total > 0
    assert {e['word'] for e in entries} == {"chat", "noir", "rat", "blanc", "merle"}
