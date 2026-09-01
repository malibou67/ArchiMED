"""File d'attente du moteur de tâches : elle doit survivre à un partage qui hoquette.

Le scénario qui a motivé ces tests : deux postes, chacun avec une tâche OCR en cours et une
seconde en attente. Les secondes ne démarraient jamais — un échec de lecture *transitoire* du
fichier de tâche, au moment précis où la première se terminait, faisait conclure à `_dispatch`
que la tâche avait été supprimée à distance. Elle était retirée de la file alors que son
fichier disait toujours `queued` sur le partage, et plus rien ne repassait dessus.

Les tests couvrent donc les deux moitiés du correctif : ne jamais évincer un candidat sur un
doute (`_probe_disk`), et rattraper périodiquement ce que les événements ont laissé passer
(`_reconcile_queues`).
"""
import json

import pytest

import machine_identity
from task_service import TaskService, TaskUnavailable

from conftest import Runner, attendre, ecrire_tache, enfiler


# ── Le test de non-régression du bug signalé ──────────────────────


def test_lecture_transitoire_en_echec_ne_perd_pas_la_tache(tasks_dir, monkeypatch):
    """Un fichier momentanément illisible reporte le démarrage, il ne le condamne pas."""
    runner = Runner()
    TaskService.register('ocr', runner)

    a = enfiler('A')['id']
    assert attendre(lambda: TaskService._active.get('ocr') == a)
    b = enfiler('B')['id']
    assert TaskService._tasks[b]['status'] == 'queued'

    # Le partage hoquette : fichier présent, mais illisible. Piloté par un drapeau plutôt que
    # par `monkeypatch.undo()`, qui annulerait aussi le `DATA_DIR` posé par la fixture.
    panne = {'on': True}
    vrai_probe = TaskService._probe_disk
    monkeypatch.setattr(TaskService, '_probe_disk', staticmethod(
        lambda tid, heavy=True: (True, None) if panne['on'] else vrai_probe(tid, heavy)))
    runner.libère.set()
    assert attendre(lambda: TaskService._active.get('ocr') is None)

    # B n'a pas démarré, mais surtout : elle n'a pas été perdue.
    assert b in TaskService._tasks
    assert TaskService._tasks[b]['status'] == 'queued'
    assert TaskService._queue['ocr'] == [b]
    assert json.loads((tasks_dir / 'tasks' / f'{b}.json').read_text(encoding='utf-8'))['status'] == 'queued'

    # Le partage revient : le balayage du superviseur la démarre, sans redémarrage du backend.
    runner.libère.clear()
    panne['on'] = False
    TaskService._reconcile_queues()
    # On attend l'entrée dans le runner : `_active` est posé juste avant le démarrage du thread.
    assert attendre(lambda: runner.démarrées == [a, b])
    assert TaskService._active.get('ocr') == b
    runner.libère.set()


def test_suppression_confirmee_evince_et_enchaine(tasks_dir):
    """Fichier réellement absent : là, on évince — et le candidat suivant démarre."""
    runner = Runner()
    TaskService.register('ocr', runner)

    a = enfiler('A')['id']
    assert attendre(lambda: TaskService._active.get('ocr') == a)
    b = enfiler('B')['id']
    c = enfiler('C')['id']

    (tasks_dir / 'tasks' / f'{b}.json').unlink()
    runner.libère.set()

    # Le runner étant déjà libéré, C s'exécute et se termine aussitôt : on observe la liste des
    # démarrages, pas l'occupation instantanée de la lane.
    assert attendre(lambda: runner.démarrées == [a, c])
    assert b not in TaskService._tasks
    assert b not in TaskService._queue['ocr']


def test_partage_injoignable_ne_conclut_pas_a_une_suppression(tasks_dir, monkeypatch):
    """Windows mappe `ERROR_BAD_NETPATH` sur `ENOENT` : sans le test du dossier parent, un
    partage coupé serait pris pour une suppression et viderait la file."""
    a = enfiler('A')['id']
    monkeypatch.setattr(TaskService, '_dir',
                        staticmethod(lambda: tasks_dir / 'partage_absent'))
    assert TaskService._probe_disk(a, heavy=False) == (True, None)


def test_dispatch_ne_leve_jamais(tasks_dir, monkeypatch):
    """`_dispatch` est appelée depuis un `finally` : une exception y arrêterait la file."""
    TaskService.register('ocr', Runner())
    enfiler('A')

    def boum(tid, heavy=True):
        raise OSError("partage coupé")

    monkeypatch.setattr(TaskService, '_probe_disk', staticmethod(boum))
    TaskService._active['ocr'] = None
    TaskService._dispatch('ocr')   # ne doit pas lever


def test_execute_libere_la_lane_sur_tache_inconnue(tasks_dir):
    """Le `KeyError` d'origine laissait `_active` occupé pour toujours."""
    TaskService.register('ocr', Runner())
    TaskService._active['ocr'] = 'inexistante'
    TaskService._execute('inexistante')
    assert TaskService._active.get('ocr') is None


def test_les_mkdir_ne_levent_pas(tasks_dir, monkeypatch):
    """Un `mkdir` raté sur le NAS traversait le `finally` de `_execute`."""
    from pathlib import Path

    def refus(self, *a, **k):
        raise OSError("partage en lecture seule")

    monkeypatch.setattr(Path, 'mkdir', refus)
    assert TaskService._dir() is not None
    assert TaskService._locks_dir() is not None
    assert TaskService._control_dir() is not None


# ── Rattrapage ────────────────────────────────────────────────────────────


def test_orpheline_du_poste_est_readoptee(tasks_dir):
    """État perdu en mémoire mais fichier bien là : le balayage la récupère."""
    runner = Runner()
    TaskService.register('ocr', runner)
    ecrire_tache(tasks_dir, 'orph', machine_identity.machine_id())

    TaskService._reconcile_queues()
    assert attendre(lambda: TaskService._active.get('ocr') == 'orph')
    runner.libère.set()


def test_orpheline_d_un_autre_poste_est_ignoree(tasks_dir):
    """Une file ne se démarre que sur son propre poste : pas de vol entre postes."""
    runner = Runner()
    TaskService.register('ocr', runner)
    ecrire_tache(tasks_dir, 'ailleurs', 'un-autre-poste')

    TaskService._reconcile_queues()
    assert 'ailleurs' not in TaskService._tasks
    assert TaskService._active.get('ocr') is None
    assert runner.démarrées == []


def test_reconcile_reconstruit_une_file_vidée(tasks_dir):
    """Filet pour un état laissé par une version antérieure : la tâche est en mémoire au statut
    `queued`, mais absente de `_queue` — plus aucun événement ne l'aurait démarrée."""
    runner = Runner()
    TaskService.register('ocr', runner)
    a = enfiler('A')['id']
    assert attendre(lambda: TaskService._active.get('ocr') == a)
    b = enfiler('B')['id']

    TaskService._queue['ocr'] = []          # la file est perdue…
    runner.libère.set()
    assert attendre(lambda: TaskService._active.get('ocr') is None)
    assert TaskService._tasks[b]['status'] == 'queued'

    runner.libère.clear()
    TaskService._reconcile_queues()         # …et rattrapée
    assert attendre(lambda: TaskService._active.get('ocr') == b)
    runner.libère.set()


# ── Écritures ratées et verrous ───────────────────────────────────────────


def test_enqueue_refuse_si_le_partage_n_accepte_pas_l_ecriture(tasks_dir, monkeypatch):
    """Mieux vaut une erreur franche qu'une tâche fantôme qui réserve son scope partout."""
    TaskService.register('ocr', Runner())
    monkeypatch.setattr(TaskService, '_write_atomic',
                        staticmethod(lambda path, data: False))
    with pytest.raises(TaskUnavailable):
        enfiler('A')
    assert TaskService._tasks == {}
    assert TaskService._queue.get('ocr') == []
    assert TaskService._active.get('ocr') is None


def test_verrou_illisible_reste_tenu(tasks_dir):
    """Un fichier de tâche illisible ne doit pas rendre le verrou volable : deux postes
    lanceraient le même OCR dans le même dossier de sortie."""
    ecrire_tache(tasks_dir, 'viv', machine_identity.machine_id(), status='running')
    verrou = TaskService._lock_path('ocr__col__reg__modele')
    verrou.write_text(json.dumps({'task_id': 'viv', 'machine_id': 'un-autre-poste'}),
                      encoding='utf-8')

    (tasks_dir / 'tasks' / 'viv.json').write_text('{tronqué', encoding='utf-8')
    assert TaskService._lock_is_stale(verrou) is False

    (tasks_dir / 'tasks' / 'viv.json').unlink()
    assert TaskService._lock_is_stale(verrou) is True


# ── Hooks de fin de vie ───────────────────────────────────────────────────


def test_supprimer_une_tache_en_attente_joue_le_hook(tasks_dir):
    """Une tâche supprimée avant d'avoir tourné a pu matérialiser quelque chose à l'enfilage :
    un index marqué « en reconstruction », par exemple. Sans ce hook, plus rien ne le nettoyait
    et sa ligne restait figée pour toujours."""
    runner = Runner()
    nettoyés = []
    TaskService.register('ocr', runner, on_cancel=lambda t: nettoyés.append(t['id']))
    a = enfiler('A')                      # démarre et occupe la lane
    b = enfiler('B')                      # reste en attente
    assert attendre(lambda: runner.entré.is_set())

    assert TaskService.delete(b['id']) is True
    assert nettoyés == [b['id']]

    runner.libère.set()


def test_supprimer_une_tache_terminee_ne_joue_pas_le_hook(tasks_dir):
    """Rien à abandonner : la tâche a déjà rendu son verdict, son nettoyage est fait."""
    nettoyés = []
    TaskService.register('ocr', Runner(), on_cancel=lambda t: nettoyés.append(t['id']))
    ecrire_tache(tasks_dir, 'finie', machine_identity.machine_id(), status='done')
    TaskService.load_on_startup()

    TaskService.delete('finie')
    assert nettoyés == []


def test_echec_avant_le_runner_joue_le_hook_derreur(tasks_dir):
    """Un conflit de verrou (ou un runner absent) fait échouer la tâche **avant** son runner :
    celui-ci n'a donc rien pu nettoyer de ce que l'enfilage avait laissé en place."""
    échoués = []
    TaskService.register('ocr', Runner(), on_error=lambda t: échoués.append(t['id']))
    TaskService._runners.pop('ocr')        # plus de runner : l'exécution échoue d'emblée

    tâche = enfiler('A')

    assert attendre(lambda: échoués == [tâche['id']])
    assert TaskService._tasks[tâche['id']]['status'] == 'error'


def test_un_hook_qui_leve_ne_casse_pas_la_suppression(tasks_dir):
    """La persistance de la tâche et la lane priment sur son nettoyage."""
    runner = Runner()

    def hook_cassé(task):
        raise RuntimeError("partage injoignable")

    TaskService.register('ocr', runner, on_cancel=hook_cassé)
    enfiler('A')
    b = enfiler('B')
    assert attendre(lambda: runner.entré.is_set())

    assert TaskService.delete(b['id']) is True
    assert b['id'] not in TaskService._tasks

    runner.libère.set()
