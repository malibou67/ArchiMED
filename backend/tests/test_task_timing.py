"""Comptabilité du temps de travail d'une tâche, et ce qu'elle devient à travers une pause.

Le bug d'origine : `_dispatch_next` réécrivait `started_at` à *chaque* démarrage, reprise
comprise. Or une reprise repart de son checkpoint et conserve `processed` — le temps mesuré
repartait donc de zéro pendant que le travail accompli, lui, restait entier. L'interface, qui
estime le temps restant par `(écoulé / traitées) × restantes`, en concluait un débit délirant et
annonçait « reste ~ quelques secondes » là où il restait des heures.

D'où l'invariant tenu ici : `started_at` date la première mise en route et ne bouge plus, chaque
exécution ouvre un segment (`run_started_at`) dont la durée rejoint `work_ms` à sa clôture, et le
temps passé en pause n'est jamais compté.
"""
import json
import threading
import time
from datetime import datetime, timedelta

import machine_identity
import task_service
from task_service import TaskService

from conftest import attendre, ecrire_tache, enfiler


def iso(d):
    return d.isoformat()


class RunnerPilotable:
    """Runner qui rend la main sur commande et honore le drapeau `pause`, comme le fait le vrai
    runner OCR (`OcrService._stop_requested`) : c'est lui qui pose le statut `paused`."""

    def __init__(self):
        self.entré = threading.Event()
        self.libère = threading.Event()

    def __call__(self, task):
        self.entré.set()
        self.libère.wait(timeout=5)
        self.libère.clear()
        self.entré.clear()
        if task.get('pause'):
            task['status'] = 'paused'

    def travaille(self, durée):
        """Attend l'entrée dans le runner, laisse passer `durée`, puis lui rend la main."""
        assert attendre(lambda: self.entré.is_set())
        time.sleep(durée)


# ── Le test de non-régression du bug signalé ──────────────────────────────


def test_une_reprise_ne_reecrit_pas_la_date_de_premiere_mise_en_route(tasks_dir):
    runner = RunnerPilotable()
    TaskService.register('ocr', runner)
    tid = enfiler('A')['id']
    tâche = TaskService._tasks[tid]

    runner.travaille(0.05)
    départ = tâche['started_at']
    assert départ is not None
    assert TaskService.pause(tid) == task_service.APPLIED
    runner.libère.set()
    assert attendre(lambda: tâche['status'] == 'paused')

    assert TaskService.resume(tid) == task_service.APPLIED
    assert attendre(lambda: tâche['status'] == 'running')
    assert tâche['started_at'] == départ
    runner.libère.set()
    assert attendre(lambda: tâche['status'] == 'done')
    assert tâche['started_at'] == départ


def test_le_temps_passe_en_pause_n_est_pas_compte_comme_du_travail(tasks_dir):
    """Le cœur du correctif : à la reprise, le compteur repart de sa valeur d'avant la pause —
    ni remis à zéro (l'estimation s'effondrait), ni gonflé de l'attente."""
    runner = RunnerPilotable()
    TaskService.register('ocr', runner)
    tid = enfiler('A')['id']
    tâche = TaskService._tasks[tid]

    runner.travaille(0.30)
    assert TaskService.pause(tid) == task_service.APPLIED
    runner.libère.set()
    assert attendre(lambda: tâche['status'] == 'paused')

    premier = tâche['work_ms']
    assert premier >= 250
    assert tâche['run_started_at'] is None      # segment clos : le compteur est figé

    time.sleep(0.40)                            # …et il ne bouge pas pendant la pause
    assert TaskService._work_ms(tâche) == premier

    assert TaskService.resume(tid) == task_service.APPLIED
    runner.travaille(0.20)
    # Le second segment s'ajoute au premier ; les 0,4 s de pause, elles, n'entrent nulle part.
    assert TaskService._work_ms(tâche) >= premier + 150
    assert TaskService._work_ms(tâche) < premier + 400

    runner.libère.set()
    assert attendre(lambda: tâche['status'] == 'done')
    assert tâche['run_started_at'] is None
    assert premier + 150 <= tâche['work_ms'] < premier + 400


def test_le_debit_mesure_reste_celui_du_travail_reel(tasks_dir):
    """L'estimation de l'interface est `(écoulé / traitées) × restantes` : elle n'est juste que
    si le temps mesuré couvre le même travail que `processed`. On vérifie donc le débit, seule
    grandeur qui compte pour l'affichage — avant le correctif il était multiplié par ~10 après
    une reprise, et le « reste ~ » divisé d'autant."""
    runner = RunnerPilotable()
    TaskService.register('ocr', runner)
    tid = enfiler('A')['id']
    tâche = TaskService._tasks[tid]
    tâche['total'] = 100

    runner.travaille(0.30)
    tâche['processed'] = 50                     # moitié du travail, en ~0,3 s
    débit_avant = tâche['processed'] / TaskService._work_ms(tâche)
    assert TaskService.pause(tid) == task_service.APPLIED
    runner.libère.set()
    assert attendre(lambda: tâche['status'] == 'paused')

    time.sleep(0.30)
    assert TaskService.resume(tid) == task_service.APPLIED
    runner.travaille(0.05)

    # Le débit mesuré juste après la reprise reste du même ordre : le travail des runs précédents
    # est toujours en face du temps qui l'a produit.
    débit_après = tâche['processed'] / TaskService._work_ms(tâche)
    assert débit_après <= débit_avant * 1.5
    runner.libère.set()


# ── Arrêts qui ne passent pas par la fin normale du runner ────────────────


def test_une_interruption_scelle_le_segment_sur_le_dernier_releve(tasks_dir):
    """Poste éteint brutalement : le segment s'arrête au dernier heartbeat. Le sceller sur
    « maintenant » compterait comme travail toute la durée d'extinction du poste."""
    t0 = datetime.now() - timedelta(hours=3)
    ecrire_tache(tasks_dir, 'brute', machine_identity.machine_id(), status='running')
    chemin = tasks_dir / 'tasks' / 'brute.json'
    disque = json.loads(chemin.read_text(encoding='utf-8'))
    disque.update({'started_at': iso(t0), 'run_started_at': iso(t0), 'work_ms': 0,
                   'heartbeat': iso(t0 + timedelta(minutes=20))})
    chemin.write_text(json.dumps(disque), encoding='utf-8')

    TaskService._loaded = False
    TaskService.load_on_startup()

    tâche = TaskService._tasks['brute']
    assert tâche['status'] == 'interrupted'
    assert tâche['run_started_at'] is None
    assert tâche['work_ms'] == 20 * 60 * 1000


def test_annuler_une_orpheline_scelle_son_segment(tasks_dir):
    """Tâche d'un poste éteint, annulée depuis un autre : son segment était resté ouvert."""
    t0 = datetime.now() - timedelta(hours=2)
    tâche = {'id': 'orph', 'type': 'ocr', 'status': 'interrupted', 'started_at': iso(t0),
             'run_started_at': iso(t0), 'work_ms': 0,
             'heartbeat': iso(t0 + timedelta(minutes=5))}
    TaskService._finalize_cancel(tâche)

    assert tâche['status'] == 'cancelled'
    assert tâche['run_started_at'] is None
    assert tâche['work_ms'] == 5 * 60 * 1000


# ── Robustesse du calcul ──────────────────────────────────────────────────


def test_work_ms_tolere_les_taches_des_versions_anterieures():
    """Une tâche déjà sur le partage n'a ni `work_ms` ni `run_started_at` : on répond 0 plutôt
    que de lever — c'est le front qui replie alors sur l'ancien calcul."""
    assert TaskService._work_ms({'started_at': iso(datetime.now())}) == 0
    assert TaskService._elapsed({'started_at': iso(datetime.now())}) == '0s'
    assert TaskService._elapsed({'started_at': None}) is None


def test_work_ms_ignore_une_date_illisible_ou_a_venir():
    """Horloge d'un autre poste en avance, champ corrompu : le cumul déjà acquis fait foi, et le
    compteur ne recule jamais."""
    assert TaskService._work_ms({'run_started_at': 'n importe quoi', 'work_ms': 500}) == 500
    futur = iso(datetime.now() + timedelta(hours=1))
    assert TaskService._work_ms({'run_started_at': futur, 'work_ms': 500}) == 500
