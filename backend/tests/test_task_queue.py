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
import threading

import pytest

import machine_identity
import task_service
from task_service import TaskService, TaskUnavailable


@pytest.fixture
def tasks_dir(tmp_path, monkeypatch):
    """Isole `data/` et remet le moteur à zéro.

    `task_service` fait `from services import DATA_DIR` : c'est une liaison au niveau module,
    que patcher `services.DATA_DIR` (fixture `data_dir`) ne redirige pas. On patche donc les
    deux, et on repart d'un moteur vierge — ses états sont des attributs de **classe**, donc
    partagés entre tests."""
    monkeypatch.setattr(task_service, 'DATA_DIR', str(tmp_path))
    TaskService._tasks = {}
    TaskService._queue = {}
    TaskService._active = {}
    TaskService._held_locks = {}
    TaskService._file_cache = {}
    TaskService._sidecar_cache = {}
    TaskService._merged_cache = None
    TaskService._merged_cache_ts = 0.0
    TaskService._last_reconcile_disk = 0.0
    TaskService._runners = {}
    TaskService._cancel_hooks = {}
    # Pas de chargement disque ni de thread superviseur : les tests pilotent tout à la main.
    TaskService._loaded = True
    TaskService._supervisor_started = True
    return tmp_path


class Runner:
    """Runner factice bloquant, pour tenir une lane occupée le temps d'un test."""

    def __init__(self):
        self.libère = threading.Event()
        self.démarrées = []
        self.entré = threading.Event()

    def __call__(self, task):
        self.démarrées.append(task['id'])
        self.entré.set()
        self.libère.wait(timeout=5)


def _attendre(prédicat, timeout=5.0):
    """Attend qu'un prédicat devienne vrai (les tâches tournent dans des threads)."""
    fin = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if prédicat():
            return True
        fin.wait(0.02)
    return prédicat()


def _enfiler(label='t'):
    return TaskService.enqueue('ocr', label)


# ── Le test de non-régression du bug signalé ──────────────────────────────


def test_lecture_transitoire_en_echec_ne_perd_pas_la_tache(tasks_dir, monkeypatch):
    """Un fichier momentanément illisible reporte le démarrage, il ne le condamne pas."""
    runner = Runner()
    TaskService.register('ocr', runner)

    a = _enfiler('A')['id']
    assert _attendre(lambda: TaskService._active.get('ocr') == a)
    b = _enfiler('B')['id']
    assert TaskService._tasks[b]['status'] == 'queued'

    # Le partage hoquette : fichier présent, mais illisible. Piloté par un drapeau plutôt que
    # par `monkeypatch.undo()`, qui annulerait aussi le `DATA_DIR` posé par la fixture.
    panne = {'on': True}
    vrai_probe = TaskService._probe_disk
    monkeypatch.setattr(TaskService, '_probe_disk', staticmethod(
        lambda tid, heavy=True: (True, None) if panne['on'] else vrai_probe(tid, heavy)))
    runner.libère.set()
    assert _attendre(lambda: TaskService._active.get('ocr') is None)

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
    assert _attendre(lambda: runner.démarrées == [a, b])
    assert TaskService._active.get('ocr') == b
    runner.libère.set()


def test_suppression_confirmee_evince_et_enchaine(tasks_dir):
    """Fichier réellement absent : là, on évince — et le candidat suivant démarre."""
    runner = Runner()
    TaskService.register('ocr', runner)

    a = _enfiler('A')['id']
    assert _attendre(lambda: TaskService._active.get('ocr') == a)
    b = _enfiler('B')['id']
    c = _enfiler('C')['id']

    (tasks_dir / 'tasks' / f'{b}.json').unlink()
    runner.libère.set()

    # Le runner étant déjà libéré, C s'exécute et se termine aussitôt : on observe la liste des
    # démarrages, pas l'occupation instantanée de la lane.
    assert _attendre(lambda: runner.démarrées == [a, c])
    assert b not in TaskService._tasks
    assert b not in TaskService._queue['ocr']


def test_partage_injoignable_ne_conclut_pas_a_une_suppression(tasks_dir, monkeypatch):
    """Windows mappe `ERROR_BAD_NETPATH` sur `ENOENT` : sans le test du dossier parent, un
    partage coupé serait pris pour une suppression et viderait la file."""
    a = _enfiler('A')['id']
    monkeypatch.setattr(TaskService, '_dir',
                        staticmethod(lambda: tasks_dir / 'partage_absent'))
    assert TaskService._probe_disk(a, heavy=False) == (True, None)


def test_dispatch_ne_leve_jamais(tasks_dir, monkeypatch):
    """`_dispatch` est appelée depuis un `finally` : une exception y arrêterait la file."""
    TaskService.register('ocr', Runner())
    _enfiler('A')

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


def _ecrire_tache(tasks_dir, tid, machine_id, status='queued'):
    d = tasks_dir / 'tasks'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{tid}.json').write_text(json.dumps({
        'id': tid, 'type': 'ocr', 'status': status, 'label': tid,
        'total': 0, 'processed': 0, 'failed': 0, 'created_at': '2026-01-01T00:00:00',
        'machine_id': machine_id, 'machine_label': machine_id,
    }), encoding='utf-8')


def test_orpheline_du_poste_est_readoptee(tasks_dir):
    """État perdu en mémoire mais fichier bien là : le balayage la récupère."""
    runner = Runner()
    TaskService.register('ocr', runner)
    _ecrire_tache(tasks_dir, 'orph', machine_identity.machine_id())

    TaskService._reconcile_queues()
    assert _attendre(lambda: TaskService._active.get('ocr') == 'orph')
    runner.libère.set()


def test_orpheline_d_un_autre_poste_est_ignoree(tasks_dir):
    """Une file ne se démarre que sur son propre poste : pas de vol entre postes."""
    runner = Runner()
    TaskService.register('ocr', runner)
    _ecrire_tache(tasks_dir, 'ailleurs', 'un-autre-poste')

    TaskService._reconcile_queues()
    assert 'ailleurs' not in TaskService._tasks
    assert TaskService._active.get('ocr') is None
    assert runner.démarrées == []


def test_reconcile_reconstruit_une_file_vidée(tasks_dir):
    """Filet pour un état laissé par une version antérieure : la tâche est en mémoire au statut
    `queued`, mais absente de `_queue` — plus aucun événement ne l'aurait démarrée."""
    runner = Runner()
    TaskService.register('ocr', runner)
    a = _enfiler('A')['id']
    assert _attendre(lambda: TaskService._active.get('ocr') == a)
    b = _enfiler('B')['id']

    TaskService._queue['ocr'] = []          # la file est perdue…
    runner.libère.set()
    assert _attendre(lambda: TaskService._active.get('ocr') is None)
    assert TaskService._tasks[b]['status'] == 'queued'

    runner.libère.clear()
    TaskService._reconcile_queues()         # …et rattrapée
    assert _attendre(lambda: TaskService._active.get('ocr') == b)
    runner.libère.set()


# ── Écritures ratées et verrous ───────────────────────────────────────────


def test_enqueue_refuse_si_le_partage_n_accepte_pas_l_ecriture(tasks_dir, monkeypatch):
    """Mieux vaut une erreur franche qu'une tâche fantôme qui réserve son scope partout."""
    TaskService.register('ocr', Runner())
    monkeypatch.setattr(TaskService, '_write_atomic',
                        staticmethod(lambda path, data: False))
    with pytest.raises(TaskUnavailable):
        _enfiler('A')
    assert TaskService._tasks == {}
    assert TaskService._queue.get('ocr') == []
    assert TaskService._active.get('ocr') is None


def test_verrou_illisible_reste_tenu(tasks_dir):
    """Un fichier de tâche illisible ne doit pas rendre le verrou volable : deux postes
    lanceraient le même OCR dans le même dossier de sortie."""
    _ecrire_tache(tasks_dir, 'viv', machine_identity.machine_id(), status='running')
    verrou = TaskService._lock_path('ocr__col__reg__modele')
    verrou.write_text(json.dumps({'task_id': 'viv', 'machine_id': 'un-autre-poste'}),
                      encoding='utf-8')

    (tasks_dir / 'tasks' / 'viv.json').write_text('{tronqué', encoding='utf-8')
    assert TaskService._lock_is_stale(verrou) is False

    (tasks_dir / 'tasks' / 'viv.json').unlink()
    assert TaskService._lock_is_stale(verrou) is True
