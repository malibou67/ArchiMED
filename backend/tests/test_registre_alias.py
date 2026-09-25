"""Registres numérisés sous un autre nom que leur dossier.

Sur HPC, 17 registres ont des fichiers dont le nom ne commence pas par celui de leur dossier :
`HPC-373-1963_*.jpg` rangés dans `HPC-373-1973/`, par exemple. Une page n'était rattachée à son
registre que par ce préfixe : ces pages n'avaient pas de registre dans les résultats,
disparaissaient dès qu'on filtrait par période, sortaient « non rattachées » des statistiques,
en 404 dans la visionneuse, et l'export les déclarait introuvables alors qu'elles sont sur le
disque. Le motif de pagination que l'analyse de la collection a relevé donne leur vrai préfixe.

Invariants : une page rattachée par le nom de son dossier l'est toujours de la même façon ; le
plus long préfixe l'emporte ; à égalité, le dossier qui porte ce nom passe avant l'alias.
"""
import json
import os

import services
from services import IndexesService, _by_longest_prefix, _collection_page_aliases
from stats_service import IndexStatsService

from conftest import build, make_collection


def corpus(data_dir, registres, patterns, images, periodes=None):
    """Collection COL indexée : `patterns` = motifs relevés par l'analyse ({dossier: motif}),
    `images` = fichiers déposés dans scans/<dossier>/, `periodes` = {dossier: [début, fin]}."""
    IndexesService._vocab_cache.clear()
    IndexStatsService._stats_cache.clear()
    col = make_collection(data_dir, "COL", registres)
    meta_file = col / "metadata.json"
    meta = json.loads(meta_file.read_text(encoding='utf-8'))
    for reg in meta['registres']:
        if reg['folder_name'] in patterns:
            reg['pages_pattern'] = patterns[reg['folder_name']]
    meta_file.write_text(json.dumps(meta), encoding='utf-8')
    for folder, periode in (periodes or {}).items():
        reg_meta = col / "scans" / folder / "metadata.json"
        data = json.loads(reg_meta.read_text(encoding='utf-8'))
        data['periode'] = periode
        reg_meta.write_text(json.dumps(data), encoding='utf-8')
    for folder, names in images.items():
        for name in names:
            (col / "scans" / folder / name).write_bytes(f"image {folder}/{name}".encode())
    build("idx1")
    return col


def mal_nomme(data_dir):
    """REGX a été numérisé sous le nom REGY ; REGA est un registre ordinaire."""
    return corpus(
        data_dir,
        {"REGX": {"REGY_1": ["chat"], "REGY_2": ["chien"]}, "REGA": {"REGA_1": ["chat"]}},
        patterns={"REGX": "REGY_{num}.jpg", "REGA": "REGA_{num}.jpg"},
        images={"REGX": ["REGY_1.jpg", "REGY_2.jpg"], "REGA": ["REGA_1.jpg"]},
        periodes={"REGX": ["1973", "1973"]},
    )


def registres_des_pages(result):
    return {IndexesService._display_page_name(p['page_name']): p['registre'] for p in result['pages']}


def test_la_recherche_rattache_la_page_a_son_dossier(data_dir):
    mal_nomme(data_dir)

    assert registres_des_pages(IndexesService.search_words("idx1", "chat")) == {
        "REGY_1": "REGX", "REGA_1": "REGA"}


def test_le_filtre_de_periode_garde_ces_pages(data_dir):
    """REGX couvre 1973 : sans rattachement, ses pages sortaient de toute recherche filtrée."""
    mal_nomme(data_dir)

    assert registres_des_pages(IndexesService.search_words("idx1", "chat", year_from=1970)) == {
        "REGY_1": "REGX"}


def test_la_visionneuse_trouve_l_image(data_dir):
    col = mal_nomme(data_dir)
    page = next(p['page_name'] for p in IndexesService.search_words("idx1", "chat")['pages']
                if p['page_name'].endswith("REGY_1"))

    assert IndexesService.get_page_image_path("idx1", page) == col / "scans" / "REGX" / "REGY_1.jpg"


def test_l_export_et_le_csv_trouvent_l_image(data_dir):
    mal_nomme(data_dir)

    fin = list(IndexesService.resolve_result_zip_files_iter("idx1", "chat"))[-1]
    chemins = {r['page']: r['chemin'] for r in IndexesService.resolve_result_pages("idx1", "chat")}

    assert fin['missing'] == []
    assert sorted(f"{registre}/{path.name}" for registre, path, _size in fin['files']) == [
        "REGA/REGA_1.jpg", "REGX/REGY_1.jpg"]
    assert chemins["REGY_1"] == os.path.join("collections", "COL", "scans", "REGX", "REGY_1.jpg")


def test_les_statistiques_les_rattachent(data_dir):
    mal_nomme(data_dir)

    stats = IndexStatsService.get_corpus_stats("idx1")

    assert {r['folder'].split(IndexesService.SOURCE_SEP)[-1] for r in stats['registres']} == {"REGA", "REGX"}


def test_le_plus_long_prefixe_l_emporte(data_dir):
    """Le cas de HPC-565-1968 : ses fichiers s'appellent `HPC-561-1968_0_*`, et HPC-561-1968
    existe aussi. Le préfixe `…_0_`, plus long, désigne le bon dossier sans rien prendre à
    l'autre."""
    col = corpus(
        data_dir,
        {"REGZ": {"REGA_0_1": ["chat"]}, "REGA": {"REGA_1": ["chat"]}},
        patterns={"REGZ": "REGA_0_{num}.jpg", "REGA": "REGA_{num}.jpg"},
        images={"REGZ": ["REGA_0_1.jpg"], "REGA": ["REGA_1.jpg"]},
    )

    assert registres_des_pages(IndexesService.search_words("idx1", "chat")) == {
        "REGA_0_1": "REGZ", "REGA_1": "REGA"}
    fin = list(IndexesService.resolve_result_zip_files_iter("idx1", "chat"))[-1]
    assert fin['missing'] == []
    assert {path for _r, path, _s in fin['files']} == {
        col / "scans" / "REGZ" / "REGA_0_1.jpg", col / "scans" / "REGA" / "REGA_1.jpg"}


def test_a_egalite_le_dossier_qui_porte_le_nom_passe_avant():
    """Cinq registres HPC portent le nom d'un autre registre existant : l'index a confondu leurs
    pages avec celles de ce registre, et plus rien ne les distingue. Rien ne change pour eux."""
    assert _by_longest_prefix([("REGA_", "REGB", True), ("REGA_", "REGA", False),
                               ("REGA_0_", "REGZ", True)]) == [
        ("REGA_0_", "REGZ"), ("REGA_", "REGA"), ("REGA_", "REGB")]


def test_un_prefixe_revendique_deux_fois_est_ecarte(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"registres": [
        {"folder_name": "R1", "pages_pattern": "DOUBLE_{num}.jpg"},
        {"folder_name": "R2", "pages_pattern": "DOUBLE_{num}.jpg"},
        {"folder_name": "R3", "pages_pattern": "AUTRE_{num}.jpg",
         "extra_pagination": {"pattern": "AUTRE_{num}_{extra_page}.jpg"}},
        {"folder_name": "R4", "pages_pattern": "R4_{num}.jpg"},
    ]}), encoding='utf-8')

    assert _collection_page_aliases(tmp_path) == {"AUTRE_": "R3"}


def test_une_correction_des_donnees_est_prise_en_compte(tmp_path):
    """Les alias sont gardés en mémoire, mais relus dès que le metadata de la collection change."""
    meta_file = tmp_path / "metadata.json"
    meta_file.write_text(json.dumps({"registres": [
        {"folder_name": "R1", "pages_pattern": "FAUX_{num}.jpg"}]}), encoding='utf-8')
    assert _collection_page_aliases(tmp_path) == {"FAUX_": "R1"}

    meta_file.write_text(json.dumps({"registres": [
        {"folder_name": "R1", "pages_pattern": "R1_{num}.jpg"}]}), encoding='utf-8')
    os.utime(meta_file, (1, 1))   # mtime différent même sur un système de fichiers grossier

    assert _collection_page_aliases(tmp_path) == {}
    assert str(meta_file) in services._page_alias_cache
