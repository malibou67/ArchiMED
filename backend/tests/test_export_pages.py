"""Export ZIP des pages d'une recherche : inventaire, suivi, archive.

Deux défauts motivaient la refonte. La préparation listait tous les fichiers de tous les
registres de la collection, puis relisait les metadata de toute la collection pour chaque
registre trouvé : sur le partage, des heures pour HPC avant le premier octet, derrière un simple
spinner. Et le flux avalait les erreurs de lecture en laissant une entrée vide dans l'archive.

Les invariants tenus ici : seuls les registres des résultats sont lus ; les pages bis suivent
leur page ; l'état de l'export dit où il en est et comment il a fini ; l'archive ne contient
que des images entières, plus un `_export.txt` qui dit ce qui manque.
"""
import asyncio
import io
import json
import os
import zipfile

import pytest
from fastapi import HTTPException

import export_service
import services
from export_service import ExportCancelled, PagesExportService
from services import IndexesService, RegistresService

from conftest import build, make_collection

REGISTRES = {
    "REGA": {"REGA_1": ["chat", "noir"], "REGA_2": ["rat"]},
    "REGB": {"REGB_1": ["chat", "blanc"], "REGB_2": ["chat"]},
    "REGC": {"REGC_1": ["merle"]},
}


def scans(data_dir, reg, names):
    """Dépose des images factices, au contenu propre à chaque fichier, dans scans/<reg>/."""
    folder = data_dir / "collections" / "COL" / "scans" / reg
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(f"image {reg}/{name} ".encode() * 20)
    return folder


@pytest.fixture
def corpus(data_dir):
    """« chat » touche REGA_1 (qui a une page bis), REGB_1, et REGB_2 qui n'a pas d'image.
    REGC ne porte aucun résultat."""
    IndexesService._vocab_cache.clear()
    PagesExportService._jobs.clear()
    make_collection(data_dir, "COL", REGISTRES)
    scans(data_dir, "REGA", ["REGA_1.jpg", "REGA_1_1.jpg", "REGA_2.jpg"])
    scans(data_dir, "REGB", ["REGB_1.jpg"])
    scans(data_dir, "REGC", ["REGC_1.jpg"])
    build("idx1")
    return data_dir


def inventaire(query="chat"):
    events = list(IndexesService.resolve_result_zip_files_iter("idx1", query))
    assert events[-1]['type'] == 'files'
    return events, events[-1]


def exporter(token, query="chat"):
    PagesExportService.start(token, "idx1", query)
    return PagesExportService.prepare(token)


def archive(data):
    return zipfile.ZipFile(io.BytesIO(data))


# ── Inventaire ───────────────────────────────────────────────────────────────

def test_seuls_les_registres_des_resultats_sont_lus(corpus, monkeypatch):
    """Le cœur du gain : sur le partage, chaque registre listé coûte, et HPC en a 1 437."""
    listés = []
    scandir = os.scandir

    def espion(path):
        listés.append(os.path.basename(os.fspath(path)))
        return scandir(path)

    def interdit(*_args):
        pytest.fail("relit toute la collection, ou liste un registre fichier par fichier")

    monkeypatch.setattr(services.os, 'scandir', espion)
    monkeypatch.setattr(RegistresService, 'list_registres', staticmethod(interdit))
    monkeypatch.setattr(RegistresService, 'list_scan_pages', staticmethod(interdit))

    inventaire()
    lignes = IndexesService.resolve_result_pages("idx1", "chat")

    assert "REGC" not in listés
    assert {"REGA", "REGB"} <= set(listés)
    # L'export CSV passe par la même résolution, avec le même résultat qu'avant.
    assert [(r['page'], r['registre'], r['chemin']) for r in lignes] == [
        ("REGA_1", "REGA", os.path.join("collections", "COL", "scans", "REGA", "REGA_1.jpg")),
        ("REGB_1", "REGB", os.path.join("collections", "COL", "scans", "REGB", "REGB_1.jpg")),
        ("REGB_2", "REGB", ""),
    ]


def test_les_pages_bis_suivent_leur_page(corpus):
    _events, fin = inventaire()

    assert [(registre, path.name) for registre, path, _size in fin['files']] == [
        ("REGA", "REGA_1.jpg"), ("REGA", "REGA_1_1.jpg"), ("REGB", "REGB_1.jpg")]


def test_les_motifs_du_registre_decident_de_la_famille(corpus):
    """Les motifs sont lus dans le metadata.json du registre lui-même : `REGA_1.a.jpg` n'est une
    page bis de `REGA_1` que selon le motif extra, le repli sans motif ne l'y rattache pas."""
    scans(corpus, "REGA", ["REGA_1.a.jpg"])
    meta_file = corpus / "collections" / "COL" / "scans" / "REGA" / "metadata.json"
    sans_motif = [p.name for _r, p, _s in inventaire()[1]['files'] if p.parent.name == "REGA"]

    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    meta.update(pages_pattern="REGA_{num}.jpg",
                extra_pagination={"pattern": "REGA_{num}.{extra_page}.jpg"})
    meta_file.write_text(json.dumps(meta), encoding='utf-8')
    avec_motif = [p.name for _r, p, _s in inventaire()[1]['files'] if p.parent.name == "REGA"]

    assert set(sans_motif) == {"REGA_1.jpg", "REGA_1_1.jpg"}
    assert set(avec_motif) == {"REGA_1.jpg", "REGA_1.a.jpg"}


def test_la_progression_raconte_la_recherche_puis_l_inventaire(corpus):
    events, fin = inventaire()
    phases = [e['phase'] for e in events if e['type'] == 'progress']
    début = phases.index('resolve')
    resolve = [e for e in events if e.get('phase') == 'resolve']

    assert set(phases[:début]) <= {'load', 'parse', 'scan', 'build'}
    assert set(phases[début:]) == {'resolve'}
    # Un événement avant le premier registre (0), puis un par registre inventorié.
    assert [e['current'] for e in resolve] == [0, 1, 2]
    assert {e['total'] for e in resolve} == {2}
    assert resolve[0]['pages'] == 3
    assert [e.get('registre') for e in resolve[1:]] == ["REGA", "REGB"]
    assert (fin['pages'], fin['registres'], fin['missing']) == (3, 2, ["REGB_2"])
    # Les tailles viennent du listage, sans relire chaque fichier : ce sont celles du disque.
    assert all(size == path.stat().st_size for _r, path, size in fin['files'])


# ── Archive ──────────────────────────────────────────────────────────────────

def test_l_archive_contient_les_images_et_le_rapport(corpus):
    files = exporter("tok-archive")

    with archive(b"".join(PagesExportService.stream("tok-archive", files))) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == [
            "REGA/REGA_1.jpg", "REGA/REGA_1_1.jpg", "REGB/REGB_1.jpg", "_export.txt"]
        for name in zf.namelist()[:-1]:
            assert zf.read(name) == (corpus / "collections" / "COL" / "scans" / name).read_bytes()
        brut = zf.read("_export.txt")

    rapport = brut.decode('utf-8-sig')
    assert brut.startswith(b'\xef\xbb\xbf') and "\r\n" in rapport
    assert "Recherche : « chat »" in rapport
    assert "3 pages trouvées dans 2 registres." in rapport
    assert "3 images exportées (pages bis comprises)" in rapport
    assert "Images introuvables sur le disque (1) :\r\n  REGB_2" in rapport
    assert "Images illisibles au moment de l'export (0)." in rapport


def test_une_image_illisible_est_sautee_sans_laisser_d_entree(corpus, monkeypatch):
    """Avant, l'en-tête de l'entrée était écrit avant d'ouvrir l'image : une image disparue
    laissait un fichier vide dans l'archive, sans que personne le sache."""
    monkeypatch.setattr(export_service, 'READ_RETRY_DELAY', 0)
    files = exporter("tok-illisible")
    (corpus / "collections" / "COL" / "scans" / "REGA" / "REGA_1_1.jpg").unlink()

    with archive(b"".join(PagesExportService.stream("tok-illisible", files))) as zf:
        assert zf.testzip() is None
        assert "REGA/REGA_1_1.jpg" not in zf.namelist()
        rapport = zf.read("_export.txt").decode('utf-8-sig')

    assert "Images illisibles au moment de l'export (1) :\r\n  REGA/REGA_1_1.jpg" in rapport
    état = PagesExportService.snapshot("tok-illisible")
    assert état['status'] == 'done'
    assert état['unreadable'] == ["REGA/REGA_1_1.jpg"]
    assert (état['files_written'], état['files_done'], état['files_total']) == (2, 3, 3)
    assert état['bytes_done'] == état['bytes_total']


def test_un_hoquet_de_lecture_est_rattrape(corpus, monkeypatch):
    monkeypatch.setattr(export_service, 'READ_RETRY_DELAY', 0)
    files = exporter("tok-hoquet")
    ouvrir = open
    échecs = []

    def capricieux(path, *args, **kwargs):
        if str(path).endswith("REGB_1.jpg") and not échecs:
            échecs.append(path)
            raise PermissionError("partage momentanément indisponible")
        return ouvrir(path, *args, **kwargs)

    monkeypatch.setattr(export_service, 'open', capricieux, raising=False)

    with archive(b"".join(PagesExportService.stream("tok-hoquet", files))) as zf:
        assert "REGB/REGB_1.jpg" in zf.namelist()
    assert échecs
    assert PagesExportService.snapshot("tok-hoquet")['unreadable_count'] == 0


# ── État de l'export ─────────────────────────────────────────────────────────

def test_l_etat_suit_l_export_du_debut_a_la_fin(corpus):
    PagesExportService.start("tok-etat", "idx1", "chat")
    assert PagesExportService.snapshot("tok-etat")['status'] == 'preparing'

    files = PagesExportService.prepare("tok-etat")
    prêt = PagesExportService.snapshot("tok-etat")
    assert (prêt['status'], prêt['phase']) == ('preparing', 'resolve')
    assert (prêt['pages'], prêt['registres'], prêt['files_total'], prêt['missing_count']) == (3, 2, 3, 1)
    assert prêt['missing'] == ["REGB_2"]
    assert prêt['bytes_total'] == sum(size for _r, _p, size in files)

    flux = PagesExportService.stream("tok-etat", files)
    next(flux)
    envoi = PagesExportService.snapshot("tok-etat")
    assert (envoi['status'], envoi['phase']) == ('streaming', 'zip')
    # Une image ne compte qu'une fois son morceau parti, quand le suivant est demandé.
    assert envoi['files_done'] == 0

    list(flux)
    fin = PagesExportService.snapshot("tok-etat")
    assert fin['status'] == 'done'
    assert fin['files_done'] == fin['files_written'] == fin['files_total'] == 3
    assert fin['bytes_done'] == fin['bytes_total'] == fin['bytes_written']
    assert fin['idle_s'] == 0


def test_l_annulation_coupe_le_flux(corpus):
    """Le flux doit lever, pas s'arrêter proprement : une fin propre ferait passer une archive
    tronquée pour complète auprès du navigateur."""
    files = exporter("tok-annule")
    flux = PagesExportService.stream("tok-annule", files)
    next(flux)

    PagesExportService.request_cancel("tok-annule")

    with pytest.raises(ExportCancelled):
        next(flux)
    état = PagesExportService.snapshot("tok-annule")
    assert (état['status'], état['cancel_reason']) == ('cancelled', 'user')


def test_l_annulation_pendant_la_preparation(corpus):
    PagesExportService.start("tok-annule-tot", "idx1", "chat")
    PagesExportService.request_cancel("tok-annule-tot")

    with pytest.raises(ExportCancelled):
        PagesExportService.prepare("tok-annule-tot")
    état = PagesExportService.snapshot("tok-annule-tot")
    assert (état['status'], état['cancel_reason']) == ('cancelled', 'user')


def test_un_flux_lache_par_le_navigateur(corpus):
    """Téléchargement annulé ou onglet fermé : Starlette abandonne le flux, qui est refermé."""
    files = exporter("tok-client")
    flux = PagesExportService.stream("tok-client", files)
    next(flux)

    flux.close()

    état = PagesExportService.snapshot("tok-client")
    assert (état['status'], état['cancel_reason']) == ('cancelled', 'client')


def test_un_index_absent_met_l_export_en_erreur(data_dir):
    IndexesService._vocab_cache.clear()
    PagesExportService.start("tok-absent", "inconnu", "chat")

    assert PagesExportService.prepare("tok-absent") is None
    assert PagesExportService.snapshot("tok-absent")['status'] == 'error'


# ── Routes ───────────────────────────────────────────────────────────────────

def test_les_routes_livrent_et_suivent_l_export(corpus):
    """Sans httpx, pas de TestClient : on appelle les fonctions de route, et on vide la
    réponse streamée comme le ferait Starlette."""
    from routers import indexes as routes
    paramètres = dict(q="chat", year_from=None, year_to=None, fuzzy_threshold=None)

    with pytest.raises(HTTPException) as refus:
        routes.export_pages_zip("idx1", download_token="pas un jeton", **paramètres)
    assert refus.value.status_code == 400
    with pytest.raises(HTTPException) as inconnu:
        routes.get_pages_export("idx1", "tok-jamais-vu")
    assert inconnu.value.status_code == 404

    réponse = routes.export_pages_zip("idx1", download_token="tok-route", **paramètres)

    async def vider():
        return b"".join([morceau async for morceau in réponse.body_iterator])

    with archive(asyncio.run(vider())) as zf:
        assert zf.testzip() is None
        assert "_export.txt" in zf.namelist()
    assert routes.get_pages_export("idx1", "tok-route")['status'] == 'done'
    # Un jeton ne se consulte que sous l'index qui l'a produit.
    with pytest.raises(HTTPException) as ailleurs:
        routes.get_pages_export("autre", "tok-route")
    assert ailleurs.value.status_code == 404
    with pytest.raises(HTTPException) as absent:
        routes.export_pages_zip("inconnu", download_token="tok-route-absent", **paramètres)
    assert absent.value.status_code == 404


def test_un_telechargement_abandonne_se_voit_aussitot(corpus):
    """Starlette abandonne l'itération sans refermer le générateur : sans fermeture explicite,
    l'abandon ne se voyait qu'au passage du ramasse-miettes, et l'export restait « en cours »."""
    from routers import indexes as routes
    réponse = routes.export_pages_zip(
        "idx1", q="chat", year_from=None, year_to=None, fuzzy_threshold=None,
        download_token="tok-abandon")

    async def navigateur_qui_lache():
        lâché = asyncio.Event()

        async def receive():
            await lâché.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                lâché.set()   # le navigateur abandonne après le premier morceau

        await réponse({"type": "http"}, receive, send)
        # Relevé avant la fin de la boucle, qui refermerait de toute façon les générateurs.
        return PagesExportService.snapshot("tok-abandon")

    état = asyncio.run(navigateur_qui_lache())
    assert (état['status'], état['cancel_reason']) == ('cancelled', 'client')
