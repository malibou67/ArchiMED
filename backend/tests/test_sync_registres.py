"""Synchronisation ciblée d'une poignée de registres (`CollectionsService.sync_registres`).

Ce que ces tests tiennent : le sync ciblé écrit **exactement** ce que le sync complet aurait
écrit pour les dossiers demandés, et **rien** en dehors d'eux. C'est tout l'intérêt de la
route : l'outil de copie externe l'appelle après chaque lot, sur une collection dont les
autres registres portent des métadonnées saisies à la main.
"""
import json

import pytest

from conftest import make_collection  # noqa: F401  (fixtures via conftest)
from services import CollectionNotSynced, CollectionsService


def _pages(col, reg, noms):
    """Dépose des images vides dans scans/<reg>/ (le contenu ne compte pas, seuls les noms)."""
    d = col / "scans" / reg
    d.mkdir(parents=True, exist_ok=True)
    for n in noms:
        (d / n).write_bytes(b'')
    return d


def _collection(data_dir, folder="HPC", registres=None):
    """Collection déjà synchronisée : metadata.json de collection + registres nommés."""
    col = data_dir / "collections" / folder
    (col / "scans").mkdir(parents=True, exist_ok=True)
    (col / "ocr").mkdir(parents=True, exist_ok=True)
    (col / "metadata.json").write_text(json.dumps({
        "id": "col_1", "type": folder, "titre": folder, "periode": ["1900", "1980"],
        "lieu": "Strasbourg", "commentaire": "", "registres": registres or [],
        "anomalies": ["ocr_orphelin:VIEUX-REG"],
    }), encoding='utf-8')
    return col


def _lire(col):
    return json.loads((col / "metadata.json").read_text(encoding='utf-8'))


def test_registre_neuf_sans_toucher_aux_autres(data_dir):
    """Le cas nominal : un registre copié à la main devient visible, et les entrées des autres
    registres du metadata de collection sont rendues **à l'octet près** telles qu'elles étaient."""
    voisin = {"id": "AUTRE", "folder_name": "AUTRE", "titre": "Titre saisi à la main",
              "periode": ["1888", "1889"], "pages_count": 3, "pages_pattern": "p{num}.jpg",
              "pages_start": 1, "pages_end": 3, "anomalies": ["registre_vide"]}
    col = _collection(data_dir, registres=[voisin])
    _pages(col, "AUTRE", [])  # présent sur le disque, mais hors du lot demandé
    _pages(col, "HPC-00400-00999-1979", [f"HPC_{i}.jpg" for i in (1, 2, 3)])

    result = CollectionsService.sync_registres("HPC", ["HPC-00400-00999-1979"])

    assert result['resultats'] == [
        {'folder_name': 'HPC-00400-00999-1979', 'resultat': 'cree'}]

    meta = _lire(col)
    # L'entrée du voisin n'a pas bougé, alors qu'il est lui aussi sur le disque : le sync
    # complet, lui, l'aurait recalculée (et effacé son `pages_count` de 3 pour un registre vide).
    assert voisin in meta['registres']

    neuf = next(r for r in meta['registres'] if r['folder_name'] == 'HPC-00400-00999-1979')
    assert neuf['pages_count'] == 3
    assert neuf['pages_pattern'] == 'HPC_{num}.jpg'
    assert (neuf['pages_start'], neuf['pages_end']) == (1, 3)
    assert neuf['periode'] == ['1979', '1979']   # année extraite du nom du dossier

    # metadata.json du registre et dossier ocr/ scaffoldés, comme le fait le sync complet
    reg_meta = json.loads(
        (col / "scans" / "HPC-00400-00999-1979" / "metadata.json").read_text(encoding='utf-8'))
    assert reg_meta['pagination'] == {'pattern': 'HPC_{num}.jpg', 'start': 1, 'end': 3}
    assert reg_meta['stats'] == {'total_pages': 3, 'total_files': 3}
    assert (col / "ocr" / "HPC-00400-00999-1979").is_dir()


def test_pattern_fige_au_premier_sync(data_dir):
    """`pattern`, `start` et `end` sont fixés à la première synchronisation et **jamais**
    recalculés — même règle que le sync complet, sur laquelle reposent les listes de pages
    déjà enfilées en OCR. Une page ajoutée ne doit donc déplacer que `pages_count`."""
    col = _collection(data_dir)
    _pages(col, "REG-1950", ["a_1.jpg", "a_2.jpg"])
    CollectionsService.sync_registres("HPC", ["REG-1950"])

    reg_file = col / "scans" / "REG-1950" / "metadata.json"
    fige = json.loads(reg_file.read_text(encoding='utf-8'))['pagination']
    assert fige == {'pattern': 'a_{num}.jpg', 'start': 1, 'end': 2}

    # Nouvelles pages, sous un préfixe différent qui aurait donné un autre motif à froid
    _pages(col, "REG-1950", ["b_7.jpg", "b_8.jpg"])
    result = CollectionsService.sync_registres("HPC", ["REG-1950"])

    assert json.loads(reg_file.read_text(encoding='utf-8'))['pagination'] == fige
    entry = next(r for r in _lire(col)['registres'] if r['folder_name'] == 'REG-1950')
    assert entry['pages_pattern'] == 'a_{num}.jpg'
    assert (entry['pages_start'], entry['pages_end']) == (1, 2)
    assert entry['pages_count'] == 4
    assert sorted(entry['extra_pages']) == ['b_7.jpg', 'b_8.jpg']
    assert result['resultats'][0]['resultat'] == 'cree'


def test_sans_changement_aucune_ecriture(data_dir):
    """Rappeler la route sur un lot déjà synchronisé ne réécrit rien : l'outil de copie peut
    rejouer un lot sans faire travailler le NAS (et sans invalider les mtime que les autres
    postes surveillent)."""
    col = _collection(data_dir)
    _pages(col, "REG-1960", ["p_1.jpg"])
    CollectionsService.sync_registres("HPC", ["REG-1960"])

    mtimes = {f: f.stat().st_mtime_ns for f in
              (col / "metadata.json", col / "scans" / "REG-1960" / "metadata.json")}
    result = CollectionsService.sync_registres("HPC", ["REG-1960"])

    assert result['resultats'] == [{'folder_name': 'REG-1960', 'resultat': 'inchange'}]
    assert {f: f.stat().st_mtime_ns for f in mtimes} == mtimes


def test_saisie_manuelle_preservee(data_dir):
    """Un champ déjà renseigné n'est jamais écrasé : seuls les champs vides sont complétés."""
    col = _collection(data_dir)
    _pages(col, "REG-1970", ["p_1.jpg", "p_2.jpg"])
    (col / "scans" / "REG-1970" / "metadata.json").write_text(json.dumps({
        "id": "REG-1970", "titre": "Registre des entrées", "periode": ["1971", "1975"],
        "stats": {"total_pages": 999, "total_files": 999},
    }), encoding='utf-8')

    CollectionsService.sync_registres("HPC", ["REG-1970"])

    reg_meta = json.loads(
        (col / "scans" / "REG-1970" / "metadata.json").read_text(encoding='utf-8'))
    assert reg_meta['titre'] == "Registre des entrées"
    assert reg_meta['periode'] == ["1971", "1975"]
    # `stats` n'est écrit que s'il est absent, même faux : c'est un compteur de référence saisi.
    assert reg_meta['stats'] == {"total_pages": 999, "total_files": 999}


def test_dossier_introuvable_nfait_pas_echouer_le_lot(data_dir):
    """Un dossier absent est signalé et les suivants sont quand même synchronisés : l'outil
    copie par lots, un nom erroné ne doit pas perdre le travail des autres."""
    col = _collection(data_dir)
    _pages(col, "REG-1930", ["p_1.jpg"])

    result = CollectionsService.sync_registres("HPC", ["ABSENT", "REG-1930"])

    assert result['resultats'] == [
        {'folder_name': 'ABSENT', 'resultat': 'introuvable'},
        {'folder_name': 'REG-1930', 'resultat': 'cree'},
    ]
    assert [r['folder_name'] for r in _lire(col)['registres']] == ['REG-1930']


def test_anomalies_de_collection_laissees_telles_quelles(data_dir):
    """Le sync ciblé ne relit pas toute l'arborescence : il ne peut pas recalculer
    `ocr_orphelin` / `registre_hors_scans`, et ne doit surtout pas les effacer au passage.
    La réponse le dit (`anomalies_recalculees`)."""
    col = _collection(data_dir)
    _pages(col, "REG-1940", ["p_1.jpg"])

    result = CollectionsService.sync_registres("HPC", ["REG-1940"])

    assert result['anomalies_recalculees'] is False
    assert result['anomalies'] == ["ocr_orphelin:VIEUX-REG"]
    assert _lire(col)['anomalies'] == ["ocr_orphelin:VIEUX-REG"]


def test_entree_inseree_dans_lordre_du_disque(data_dir):
    """`registres[]` reste trié par nom de dossier, comme le publie le sync complet : sinon le
    registre fraîchement copié sauterait au bas du tableau jusqu'au prochain sync complet."""
    col = _collection(data_dir, registres=[
        {"id": "A", "folder_name": "A-1900", "titre": "A", "periode": ["", ""]},
        {"id": "C", "folder_name": "C-1900", "titre": "C", "periode": ["", ""]},
    ])
    _pages(col, "B-1900", ["p_1.jpg"])

    CollectionsService.sync_registres("HPC", ["B-1900"])

    assert [r['folder_name'] for r in _lire(col)['registres']] == ['A-1900', 'B-1900', 'C-1900']


def test_doublons_dans_le_lot_traites_une_fois(data_dir):
    """L'outil peut répéter un dossier dans son lot : il n'est sondé et rendu qu'une fois."""
    col = _collection(data_dir)
    _pages(col, "REG-1920", ["p_1.jpg"])

    result = CollectionsService.sync_registres("HPC", ["REG-1920", "REG-1920"])

    assert result['resultats'] == [{'folder_name': 'REG-1920', 'resultat': 'cree'}]


def test_metadata_illisible_ne_repose_pas_un_squelette(data_dir):
    """Un metadata.json présent mais corrompu est une anomalie, pas une absence : on lève
    plutôt que d'écrire un squelette neuf par-dessus des métadonnées saisies à la main."""
    col = _collection(data_dir)
    _pages(col, "REG-1910", ["p_1.jpg"])
    reg_file = col / "scans" / "REG-1910" / "metadata.json"
    reg_file.write_text("{ ceci n'est pas du JSON", encoding='utf-8')

    with pytest.raises(ValueError, match="illisible"):
        CollectionsService.sync_registres("HPC", ["REG-1910"])
    assert reg_file.read_text(encoding='utf-8') == "{ ceci n'est pas du JSON"


def test_collection_jamais_synchronisee_refusee(data_dir):
    """Amorcer une collection (squelette, période déduite, anomalies) demande de voir toute
    l'arborescence : c'est le travail du sync complet, le sync ciblé refuse."""
    col = data_dir / "collections" / "NEUVE"
    (col / "scans" / "REG-1900").mkdir(parents=True)
    (col / "scans" / "REG-1900" / "p_1.jpg").write_bytes(b'')

    with pytest.raises(CollectionNotSynced):
        CollectionsService.sync_registres("NEUVE", ["REG-1900"])
    assert not (col / "metadata.json").exists()


def test_collection_absente(data_dir):
    assert CollectionsService.sync_registres("INEXISTANTE", ["REG"]) is None


def test_meme_resultat_que_le_sync_complet(data_dir):
    """Le filet de sécurité de tout le reste : sur les mêmes dossiers, le sync ciblé et le sync
    complet doivent produire des `registres[]` identiques. Les deux passent par
    `_sync_registre_entry` ; ce test le vérifie de bout en bout, y compris trous, doublons et
    pages hors-motif."""
    pages = ["s_1.jpg", "s_2.jpg", "s_4.jpg", "s_4.png", "s_4_bis.jpg"]

    cible = _collection(data_dir, folder="CIBLE")
    _pages(cible, "REG-1955", pages)
    CollectionsService.sync_registres("CIBLE", ["REG-1955"])

    complet = _collection(data_dir, folder="COMPLET")
    _pages(complet, "REG-1955", pages)
    CollectionsService.sync_collection_metadata("COMPLET")

    assert _lire(cible)['registres'] == _lire(complet)['registres']
    assert (json.loads((cible / "scans" / "REG-1955" / "metadata.json").read_text(encoding='utf-8'))
            == json.loads((complet / "scans" / "REG-1955" / "metadata.json").read_text(encoding='utf-8')))
