"""Étapes nommées d'une indexation : ce qui se passe quand il n'y a rien à compter.

Une indexation passe l'essentiel de son temps sur des pages, et le décompte suffit à décrire
l'attente. Mais elle commence et surtout **finit** par des étapes qui n'ont pas de compteur
propre : le parcours des registres, la relecture de l'index existant, puis — barre déjà pleine —
la lecture des metadata de registre, l'écriture de l'index sur le partage et le décompte des
pages indexées. Sur un gros index cette fin dure des minutes pendant lesquelles la ligne
affichait « x / x » sans bouger, ce qui se lit comme un blocage.

L'invariant que ce fichier tient : les étapes **préparatoires** ne remontent pas de compteurs à
la tâche (les leurs sont nuls, et les publier remettrait barre et ETA à zéro), tandis que les
étapes de **fin** en remontent — leurs compteurs sont réels et complets, et c'est ce qui garde
la barre pleine pendant qu'elles durent.
"""
import index_runner
from services import IndexesService
from task_service import TaskService

from conftest import build, make_collection, write_page_xml

# 2 registres × 2 pages : le total tient dans les assertions sans rien y ajouter.
REGISTRES = {"REGA": {"REGA_1": ["chat"], "REGA_2": ["rat"]},
             "REGB": {"REGB_1": ["hibou"], "REGB_2": ["merle"]}}
PAGES = 4


def journal(data_dir, index_id="idx1", **kwargs):
    """Construit un index en enregistrant étapes et avancements dans un seul fil chronologique.

    Les deux voyagent séparément (`on_phase` nomme, `on_progress` compte) mais c'est leur
    entrelacement qui décrit ce que voit l'utilisateur : les mêler ici permet de vérifier ce qui
    a été remonté *pour* une étape donnée."""
    events = []
    build(index_id,
          on_phase=lambda ph: events.append(('phase', ph)),
          on_progress=lambda p, t, c, page=None: events.append(('progress', p, t)),
          **kwargs)
    return events


def test_les_etapes_de_fin_sont_annoncees(data_dir):
    make_collection(data_dir, "COL", REGISTRES)
    events = journal(data_dir)

    # L'ordre du run : on annonce le parcours, on indexe, puis les trois étapes de fin.
    assert [e[1] for e in events if e[0] == 'phase' and e[1]] == [
        'scanning', 'registres', 'writing', 'counting']


def test_les_etapes_de_fin_gardent_la_barre_pleine(data_dir):
    """Le point de la correction : la fin d'indexation garde des compteurs complets.

    Sans cela la page Tâches n'apprenait rien de ces minutes-là — `_publish` coupait la remontée
    dès qu'une phase était posée, sans distinguer celles qui ont des compteurs de celles qui
    n'en ont pas."""
    make_collection(data_dir, "COL", REGISTRES)
    events = journal(data_dir)

    for phase in ('registres', 'writing', 'counting'):
        i = events.index(('phase', phase))
        assert events[i + 1] == ('progress', PAGES, PAGES), f"{phase} sans compteurs complets"


def test_les_etapes_preparatoires_ne_remontent_pas_de_compteurs(data_dir):
    """La garde d'origine, qui doit survivre : publier les (0, 0) d'une préparation remettait
    la barre et le « reste ~ » de la tâche à zéro à chaque jalon."""
    col = make_collection(data_dir, "COL", REGISTRES)
    assert build("idx1") == 'done'          # un index existant : la reprise passe par 'loading'
    # Une page de plus, sinon la mise à jour n'a rien à faire et publie légitimement (0, 0) —
    # elle ne dirait alors rien des préparations, qui sont ce qu'on teste ici.
    write_page_xml(col / "ocr" / "REGB" / "modelA" / "REGB_3.xml", ["pinson"])

    events = journal(data_dir)
    assert ('phase', 'loading') in events   # sinon le test ne prouve rien
    assert not [e for e in events if e[0] == 'progress' and e[2] == 0]

    # Et une préparation n'est jamais suivie d'un avancement : c'est bien elle qui est muette.
    for phase in ('scanning', 'loading'):
        i = events.index(('phase', phase))
        assert events[i + 1][0] == 'phase'


def test_letape_nest_annoncee_quau_changement(data_dir):
    """`_publish` est appelé à chaque page. Réannoncer la même étape réécrirait le fichier de
    tâche sur le partage, chaque seconde, pour redire la même chose."""
    make_collection(data_dir, "COL", REGISTRES)
    phases = [e[1] for e in journal(data_dir) if e[0] == 'phase']
    assert len(phases) == len(set(phases))


def test_le_runner_efface_letape_a_la_fin(data_dir, tasks_dir, monkeypatch):
    """Aucune ligne terminée ne doit garder « Écriture du fichier d'index… » : le libellé
    décrit une attente en cours, et la tâche n'attend plus rien."""
    make_collection(data_dir, "COL", REGISTRES)
    sources = IndexesService._resolve_sources([{"collection_id": "COL", "model_name": "modelA"}])
    IndexesService.init_index_new("idx1", "Idx", sources)

    vus = []
    original = TaskService._save
    def espion(task):
        vus.append(task.get('index_phase'))
        return original(task)
    monkeypatch.setattr(TaskService, '_save', espion)

    task = {'id': 't1', 'type': 'index', 'status': 'running', 'index_id': 'idx1',
            'index_name': 'Idx', 'index_full': True, 'index_registres': [],
            'total': 0, 'processed': 0}
    index_runner.run_index_task(task)

    assert 'writing' in vus            # l'étape a bien été portée jusqu'à la tâche
    assert task.get('index_phase') is None
