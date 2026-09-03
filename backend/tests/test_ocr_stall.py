"""Chien de garde du pool OCR : un pool qui ne rend plus rien doit être tué et rejoué.

Le cas réel (01/09/2026) : les quatre workers cessent de rendre des pages — VRAM de la carte
débordée, aucune exception levée — et la tâche reste « en cours » 40 h sans transcrire une
ligne. Rien ne pouvait la débloquer : le recyclage des workers n'a lieu qu'à la barrière de fin
de lot, or cette barrière attend précisément les pages qui ne reviennent pas.

`_run_pool_attempt` importe `ProcessPoolExecutor` au moment de l'appel : on lui substitue donc
un exécuteur factice dont on choisit combien de pages reviennent (même procédé que
`test_repli_sequentiel_si_le_pool_ne_demarre_pas` côté indexation). Tout le reste — fenêtre
glissante, `wait()`, `page_states`, kill du pool — est le vrai code.
"""
import concurrent.futures as cf
import logging
import threading
import time

import pytest

import ocr_service as ocr
from task_service import TaskService

PAGES = 10
WORKERS = 2


class FauxPool:
    """Rend les `resolues` premières pages soumises et laisse les suivantes sans réponse."""

    def __init__(self, resolues, **_kw):
        self.resolues = resolues
        self.soumises = 0
        self.shutdown_args = None

    def submit(self, fn, arg):
        fut = cf.Future()
        self.soumises += 1
        if self.soumises <= self.resolues:
            idx, col, reg, img, _model = arg
            stats = {'pid': 1000 + self.soumises % WORKERS, 'n': self.soumises, 'rss': 1400}
            # Résolue depuis un thread : le parent doit la voir arriver par `wait()`, pas la
            # trouver déjà faite — c'est l'ordonnancement du vrai pool.
            threading.Timer(0.01, fut.set_result,
                            ((idx, col, f"{reg}/{img}", True, None, stats),)).start()
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_args = (wait, cancel_futures)


@pytest.fixture
def pools(monkeypatch):
    """Fabrique de pools factices. `pools.prevoir([n, m])` : n pages rendues par le premier
    pool, m par le suivant. Les pools créés s'accumulent dans `pools.crees`."""

    class Fabrique:
        def __init__(self):
            self.crees = []

        def prevoir(self, resolues):
            suite = iter(resolues)
            monkeypatch.setattr(cf, 'ProcessPoolExecutor',
                                lambda **kw: self._nouveau(next(suite), **kw))

        def _nouveau(self, resolues, **kw):
            pool = FauxPool(resolues, **kw)
            self.crees.append(pool)
            return pool

    monkeypatch.setattr(TaskService, '_save_throttled', staticmethod(lambda task: None))
    monkeypatch.setattr(ocr, 'STALL_TIMEOUT', 1)
    monkeypatch.setattr(ocr, 'STALL_RETRIES', 2)
    return Fabrique()


def nouvelle_tache():
    task = {'id': 'test', 'total': PAGES, 'processed': 0, 'failed': 0, 'current': None,
            'errors': [], 'cancel': False, 'pause': False, 'page_states': [0] * PAGES}
    batch = [(i, 'HPC', 'HPC-1', f"p{i}.jpg", 'model_60') for i in range(PAGES)]
    by_index = {i: {'registre': 'HPC-1', 'page': f"p{i}.jpg"} for i in range(PAGES)}
    return task, batch, by_index


def lancer(task, batch, by_index, une_seule_passe=False):
    progress = {'stats': {}, 'last_log': 0.0}
    fn = (ocr.OcrService._run_pool_attempt if une_seule_passe
          else ocr.OcrService._run_pool_batch)
    return fn(task, set(), task['page_states'], by_index, batch, 'seg', 'model_60',
              'cuda', WORKERS, 1, False, lambda col, reg: None, progress)


def test_lot_normal_ne_declenche_rien(pools):
    """Garde-fou des autres cas : le chemin nominal ne doit ni rejouer ni tuer quoi que ce soit.

    `shutdown(wait=True)` est l'invariant qui compte ici : sans lui, le pool du lot suivant
    démarrerait pendant que celui-ci tient encore sa VRAM."""
    pools.prevoir([PAGES])
    task, batch, by_index = nouvelle_tache()

    assert lancer(task, batch, by_index, une_seule_passe=True) == []
    assert len(pools.crees) == 1
    assert pools.crees[0].shutdown_args == (True, True)
    assert task['processed'] == PAGES and set(task['page_states']) == {1}


def test_pool_fige_rejoue_les_pages_restantes(pools):
    """Le lot repart dans un pool neuf, et **seules** les pages figées sont resoumises : les
    pages déjà rendues ne doivent pas être retranscrites (ni recomptées)."""
    pools.prevoir([3, PAGES])
    task, batch, by_index = nouvelle_tache()

    lancer(task, batch, by_index)

    assert len(pools.crees) == 2
    # Pool figé : tué, donc `shutdown` n'attend pas — ces workers-là ne finiront jamais.
    assert pools.crees[0].shutdown_args == (False, True)
    assert pools.crees[1].soumises == PAGES - 3
    assert task['processed'] == PAGES and set(task['page_states']) == {1}


def test_pool_fige_sans_retour_remonte_lerreur(pools):
    """Après `STALL_RETRIES` reprises stériles, l'erreur remonte : `run_ocr_task` bascule alors
    en repli séquentiel plutôt que de tourner à vide. Aucune page ne doit être perdue en route
    — elles restent « à faire », donc reprises telles quelles."""
    pools.prevoir([0, 0, 0])
    task, batch, by_index = nouvelle_tache()

    with pytest.raises(RuntimeError, match="figé"):
        lancer(task, batch, by_index)

    assert len(pools.crees) == ocr.STALL_RETRIES + 1
    assert task['page_states'] == [0] * PAGES
    assert task['processed'] == 0


def test_pause_pendant_un_gel_reste_immediate(pools, monkeypatch):
    """Une pause demandée sur un pool figé ne doit pas attendre le chien de garde."""
    monkeypatch.setattr(ocr, 'STALL_TIMEOUT', 30)
    pools.prevoir([0])
    task, batch, by_index = nouvelle_tache()
    threading.Timer(0.3, lambda: task.__setitem__('pause', True)).start()

    debut = time.time()
    lancer(task, batch, by_index)

    assert time.time() - debut < 5   # et non les 30 s du chien de garde
    assert task['status'] == 'paused'
    assert len(pools.crees) == 1     # pas de reprise : l'arrêt prime


def test_la_trace_de_charge_signale_le_gel(pools, caplog, monkeypatch):
    """Sans ligne pendant un gel, le journal d'un pool figé est indiscernable d'un journal muet
    — c'est ce qui a laissé passer 40 h. La trace doit continuer, et dire depuis quand.

    Le chien de garde est réglé plus long que la cadence de trace, comme en production
    (600 s contre 60) : sinon il tire au premier tour de `wait()` et rien n'a le temps de
    s'écrire."""
    monkeypatch.setattr(ocr, 'STATS_INTERVAL', 1)
    monkeypatch.setattr(ocr, 'STALL_TIMEOUT', 3)
    pools.prevoir([0, 0, 0])
    task, batch, by_index = nouvelle_tache()

    with caplog.at_level(logging.INFO, logger='ocr'):
        with pytest.raises(RuntimeError):
            lancer(task, batch, by_index)

    charges = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Charge OCR')]
    assert charges, "aucune trace de charge pendant le gel"
    assert any('depuis=' in m for m in charges), charges
    assert any(r.getMessage().startswith('Pool figé') for r in caplog.records)
