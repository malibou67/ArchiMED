import base64
import csv
import io
import json
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from typing import List, Optional
from models import (
    IndexMetadata, IndexCreate, IndexUpdate, IndexUpdates, IndexPreviewRequest,
    WordSearchResponse, MultiSearchResponse, SelectionImport, SelectionSummary,
)
from services import IndexesService
from selections_service import SelectionsService, SelectionNotFound
import results_workbook
from stats_service import IndexStatsService
from export_service import PagesExportService, ExportCancelled
from index_runner import enqueue_index
from task_service import TaskConflict, TaskUnavailable

router = APIRouter()

# Jeton d'export fourni par la page (UUID, ou repli horodaté hors contexte sécurisé).
_DOWNLOAD_TOKEN = re.compile(r'^[A-Za-z0-9-]{8,64}$')


def _within(index_id: str, within: Optional[str]) -> Optional[dict]:
    """Sélection dans laquelle chercher (`{id, name, pages, steps}`), ou None pour tout l'index.
    Résolue avant toute recherche : une sélection inconnue est un vrai 404, flux compris."""
    if not within:
        return None
    try:
        summary = SelectionsService.get(index_id, within)
        return {'id': within, 'name': summary.get('name') or within,
                'pages': SelectionsService.pages(index_id, within),
                'steps': summary.get('steps') or []}
    except SelectionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


def _attachment(filename: str) -> str:
    """En-tête Content-Disposition d'un nom de fichier accentué : `filename*` en UTF-8 pour les
    navigateurs, et un repli ASCII pour les autres."""
    fallback = filename.encode('ascii', 'replace').decode('ascii').replace('?', '_').replace('"', '')
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


class _ClosingStreamingResponse(StreamingResponse):
    """StreamingResponse qui referme son générateur dès que la requête se termine.

    Quand le navigateur lâche un téléchargement, Starlette abandonne l'itération sans refermer
    le générateur : il ne l'était qu'au passage du ramasse-miettes (de 1,5 à 8 s mesurées), et
    l'export restait affiché « en cours » d'ici là ; refermé ici, l'abandon se voit en moins de
    0,1 s. Aucun thread ne l'exécute plus à ce stade (anyio attend la fin de `next()` avant
    d'honorer l'annulation) ; sur un flux épuisé ou déjà en erreur, `close()` ne fait rien."""

    def __init__(self, content, *args, **kwargs):
        super().__init__(content, *args, **kwargs)
        self._content = content

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self._content, 'close', None)
            if close is not None:
                close()


@router.get("/", response_model=List[IndexMetadata])
def list_indexes():
    """Liste tous les index disponibles"""
    try:
        return IndexesService.list_indexes()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
def generate_index(data: IndexCreate):
    """Ajoute une tâche d'indexation multi-sources à la file (moteur de tâches générique).
    Renvoie la tâche créée (suivie dans le widget / la page Tâches)."""
    if not data.sources:
        raise HTTPException(status_code=400, detail="Sélectionnez au moins une source (collection + modèle).")
    try:
        return enqueue_index({"name": data.name, "sources": [s.model_dump() for s in data.sources]})
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except TaskUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview")
def preview_index(data: IndexPreviewRequest):
    """Aperçu avant génération : registres/pages par source, totaux et avertissements."""
    try:
        return IndexesService.preview_sources([s.model_dump() for s in data.sources])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{index_id}/regenerate")
def regenerate_index(
    index_id: str,
    full: bool = Query(False, description="Reconstruction complète (réindexe tous les registres) "
                                          "au lieu d'une mise à jour incrémentale"),
):
    """Met à jour un index existant (mêmes sources) : seuls les registres nouveaux ou modifiés
    sont réindexés, sauf si `full=true`. L'index précédent reste consultable jusqu'au
    basculement atomique en fin de génération."""
    meta = IndexesService.get_index(index_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Index non trouvé")
    try:
        return enqueue_index(index_id=index_id, full=full, meta=meta)
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except TaskUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{index_id}/abort-build")
def abort_index_build(index_id: str):
    """Abandonne une génération en cours **sans détruire un index utilisable**.

    Repli de l'annulation quand plus aucune tâche ne correspond à l'index (tâche purgée,
    échouée avant d'atteindre son runner, ou perdue le temps d'un hoquet du partage). Le
    frontend appelait jusqu'ici `DELETE /{index_id}`, qui effaçait tout le dossier — l'index
    précédent, parfaitement valide, avec. Seul un index jamais terminé (pas d'index.json) est
    supprimé ici."""
    outcome = IndexesService.abort_build(index_id)
    if outcome == 'missing':
        raise HTTPException(status_code=404, detail="Index non trouvé")
    return {"index_id": index_id, "outcome": outcome}


@router.get("/available-models")
def list_available_models(collection_id: str = Query(..., description="ID ou folder_name de la collection")):
    """Liste les modèles OCR disponibles pour une collection (scan du dossier ocr/)"""
    try:
        models = IndexesService.list_available_models(collection_id)
        return models
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Nom historique : cette route renvoie désormais la couverture complète de chaque index prêt,
# pas seulement ce qui a changé. Elle doit rester déclarée AVANT `/{index_id}`, sinon FastAPI
# la capture comme un identifiant d'index.
@router.get("/updates", response_model=List[IndexUpdates])
def get_indexes_updates(
    rescan: bool = Query(False, description="Rescanner réellement les dossiers OCR au lieu de "
                                            "lire les compteurs publiés (exact mais lent)"),
):
    """Pour chaque index prêt : pages indexées / pages OCR disponibles, et ce qui reste à indexer.

    Par défaut les compteurs OCR viennent de l'`ocr_status` publié dans le metadata de chaque
    collection (quelques millisecondes) ; `rescan=true` scanne les dossiers, ce qui est exact
    même si des XML ont été déposés hors de l'application, mais peut prendre plusieurs secondes."""
    try:
        return IndexesService.list_updates(rescan)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{index_id}", response_model=IndexMetadata)
def get_index(index_id: str):
    """Récupère les métadonnées d'un index (utile pour vérifier le statut de génération)"""
    index = IndexesService.get_index(index_id)
    if not index:
        raise HTTPException(status_code=404, detail="Index non trouvé")
    return index


@router.get("/{index_id}/year-range")
def get_index_year_range(index_id: str):
    """Retourne les années min et max couverts par l'index (depuis registres_map)"""
    index_json = IndexesService.get_indexes_dir() / index_id / "index.json"
    if not index_json.exists():
        raise HTTPException(status_code=404, detail="Index non trouvé")
    import json
    with open(index_json, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    registres_map = data.get("registres_map", {}) if isinstance(data, dict) else {}
    year_values = []
    for info in registres_map.values():
        periode = info.get("periode", [])
        try:
            if periode and periode[0]:
                year_values.append(int(periode[0]))
            if len(periode) > 1 and periode[1]:
                year_values.append(int(periode[1]))
        except (ValueError, TypeError):
            pass
    if not year_values:
        raise HTTPException(status_code=404, detail="Aucune information d'année disponible")
    return {"year_min": min(year_values), "year_max": max(year_values)}


@router.get("/{index_id}/words")
def get_index_words(
    index_id: str,
    q: Optional[str] = Query(None, description="Filtre par sous-chaîne (insensible à la casse)"),
    sort: str = Query("occurrences", description="Tri : 'word', 'occurrences' ou 'pages'"),
    direction: str = Query("desc", description="Sens du tri : 'asc' ou 'desc'"),
    hide_stopwords: bool = Query(False, description="Masquer les mots-outils et lettres isolées"),
    min_occurrences: int = Query(1, ge=1, description="N'afficher que les mots vus au moins N fois"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Liste paginée du vocabulaire d'un index : mots avec occurrences et nombre de pages."""
    result = IndexesService.get_vocabulary(
        index_id, q=q, sort=sort, direction=direction,
        hide_stopwords=hide_stopwords, min_occurrences=min_occurrences,
        offset=offset, limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    return result


@router.get("/{index_id}/words/{word:path}/pages")
def get_word_pages(index_id: str, word: str):
    """Détail d'un mot : toutes les pages où il apparaît, avec coordonnées."""
    result = IndexesService.get_word_pages(index_id, word)
    if result is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    return result


@router.get("/{index_id}/export-results.csv")
def export_results_csv(
    index_id: str,
    q: str = Query(..., description="Requête (mêmes termes que la recherche)"),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100),
    whole_word: bool = Query(False),
    exclude: Optional[str] = Query(None),
    within: Optional[str] = Query(None),
):
    """Exporte les résultats d'une recherche en CSV (registre, page, occurrences, chemin)."""
    search = dict(q=q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
                  whole_word=whole_word, exclude=exclude)
    selection = _within(index_id, within)
    rows = IndexesService.resolve_result_pages(
        index_id, within=selection['pages'] if selection else None, **search)
    if rows is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    filename, media_type, content = _results_file('csv', index_id, rows, search, selection)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": _attachment(filename)})


def _results_file(fmt: str, index_id: str, rows: List[dict], search: dict,
                  selection: Optional[dict], lang: str = "fr") -> tuple:
    """Le fichier d'export d'une recherche déjà résolue en `rows` (cf.
    `IndexesService.resolve_result_pages`) : `(nom, type, octets)`. Partagé par les
    téléchargements directs et par l'export en flux."""
    if fmt == 'csv':
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["collection", "modèle", "registre", "page", "occurrences", "chemin"])
        for r in rows:
            writer.writerow([r.get("source", ""), r.get("model", ""), r["registre"], r["page"],
                             r["occurrences"], r["chemin"]])
        # BOM UTF-8 pour Excel
        return f"{index_id}_resultats.csv", "text/csv", ('﻿' + buf.getvalue()).encode('utf-8')

    # La chaîne de l'entonnoir : les recherches de la sélection où l'on cherche, puis celle-ci.
    exclude = search.get('exclude') or ''
    step = {
        'query': search['q'].strip(), 'whole_word': search.get('whole_word', False),
        'fuzzy_threshold': search.get('fuzzy_threshold'),
        'excluded_terms': [t.strip("'\"") for t in exclude.lower().split() if t.strip("'\"")],
        'year_from': search.get('year_from'), 'year_to': search.get('year_to'), 'found': len(rows),
    }
    chain = (selection['steps'] if selection else []) + [step]
    meta = IndexesService.get_index(index_id) or {}
    content = results_workbook.build_results_workbook(
        index_id, meta.get('name') or index_id, rows, chain,
        within_name=selection['name'] if selection else None, lang=lang)
    return results_workbook.export_filename(chain, lang), results_workbook.MEDIA_TYPE, content


@router.get("/{index_id}/export-results.xlsx")
def export_results_xlsx(
    index_id: str,
    q: str = Query(..., description="Requête (mêmes termes que la recherche)"),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100),
    whole_word: bool = Query(False),
    exclude: Optional[str] = Query(None),
    within: Optional[str] = Query(None),
    lang: str = Query("fr", pattern="^(fr|en)$", description="Langue du classeur (celle de l'interface)"),
):
    """Exporte les résultats d'une recherche en classeur Excel à trier : une ligne par page,
    une colonne « Garder » (oui / non ; « Keep », yes / no en anglais), un lien vers chaque
    image. Trié puis réimporté (`POST /{index_id}/selections/import`), il devient une sélection
    où chercher ensuite."""
    search = dict(q=q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
                  whole_word=whole_word, exclude=exclude)
    selection = _within(index_id, within)
    rows = IndexesService.resolve_result_pages(
        index_id, within=selection['pages'] if selection else None, **search)
    if rows is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    filename, media_type, content = _results_file('xlsx', index_id, rows, search, selection, lang)
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": _attachment(filename)})


@router.get("/{index_id}/export-results/stream")
def export_results_stream(
    index_id: str,
    fmt: str = Query(..., alias="format", pattern="^(csv|xlsx)$", description="csv ou xlsx"),
    q: str = Query(..., description="Requête (mêmes termes que la recherche)"),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100),
    whole_word: bool = Query(False),
    exclude: Optional[str] = Query(None),
    within: Optional[str] = Query(None),
    lang: str = Query("fr", pattern="^(fr|en)$"),
):
    """Le même export que `export-results.csv` / `.xlsx`, mais en flux NDJSON, pour que la
    page dise où il en est : la recherche (load/parse/scan/build), puis l'inventaire des images
    registre par registre (`resolve`, l'étape longue sur le partage), puis `write`, et enfin
    {"type": "file", "filename", "media_type", "data": <base64>}. Un téléchargement direct ne
    montrait rien pendant ce temps, ni une erreur éventuelle."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="La requête de recherche est vide")
    search = dict(q=q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
                  whole_word=whole_word, exclude=exclude)
    selection = _within(index_id, within)

    def gen():
        try:
            rows = None
            for event in IndexesService.resolve_result_pages_iter(
                    index_id, within=selection['pages'] if selection else None, **search):
                if event.get('type') == 'rows':
                    rows = event['rows']
                else:
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            if rows is None:
                yield json.dumps({"type": "error", "detail": "Index non trouvé ou non encore généré"},
                                 ensure_ascii=False) + "\n"
                return
            yield json.dumps({"type": "progress", "phase": "write", "current": 0, "total": 0}) + "\n"
            filename, media_type, content = _results_file(fmt, index_id, rows, search, selection, lang)
            yield json.dumps({"type": "file", "filename": filename, "media_type": media_type,
                              "data": base64.b64encode(content).decode('ascii')},
                             ensure_ascii=False) + "\n"
        except Exception as e:  # surface l'erreur dans le flux, sans casser la connexion
            yield json.dumps({"type": "error", "detail": str(e) or type(e).__name__},
                             ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/{index_id}/export-pages.zip")
def export_pages_zip(
    index_id: str,
    q: str = Query(..., description="Requête (mêmes termes que la recherche)"),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100),
    whole_word: bool = Query(False),
    exclude: Optional[str] = Query(None),
    within: Optional[str] = Query(None),
    download_token: Optional[str] = Query(None, description="Identifiant de l'export, pour en suivre la progression"),
):
    """Exporte les images brutes (sans annotation) des pages résultats — et de leurs extra
    pages — en ZIP streamé, avec un récapitulatif `_export.txt`.

    Le téléchargement reste natif ; `download_token` permet à la page de suivre l'export
    (`GET /{index_id}/export-pages/{token}`) et de l'annuler (`DELETE`). La préparation
    (recherche, inventaire des images) se fait ici, avant la réponse, pour que ses erreurs
    restent de vrais statuts HTTP."""
    token = download_token or uuid.uuid4().hex
    if not _DOWNLOAD_TOKEN.match(token):
        raise HTTPException(status_code=400, detail="Jeton de téléchargement invalide")
    job = PagesExportService.start(
        token, index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
        whole_word=whole_word, exclude=exclude, within=_within(index_id, within),
    )
    try:
        files = PagesExportService.prepare(job)
    except ExportCancelled:
        raise HTTPException(status_code=409, detail="Export annulé")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e) or type(e).__name__)
    if files is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")

    return _ClosingStreamingResponse(
        PagesExportService.stream(job, files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{index_id}_pages.zip"'},
    )


@router.get("/{index_id}/export-pages/{token}")
def get_pages_export(index_id: str, token: str):
    """État d'un export ZIP en cours ou récent : étape, compteurs, bilan ou erreur."""
    snapshot = PagesExportService.snapshot(token)
    if snapshot is None or snapshot['index_id'] != index_id:
        raise HTTPException(status_code=404, detail="Export inconnu")
    return snapshot


@router.delete("/{index_id}/export-pages/{token}")
def cancel_pages_export(index_id: str, token: str):
    """Demande l'annulation d'un export ZIP. Il s'arrête à l'étape ou à l'image suivante, et
    le téléchargement est coupé : le navigateur le marque en échec plutôt que de garder une
    archive tronquée."""
    snapshot = PagesExportService.snapshot(token)
    if snapshot is None or snapshot['index_id'] != index_id:
        raise HTTPException(status_code=404, detail="Export inconnu")
    return PagesExportService.request_cancel(token)


@router.get("/{index_id}/words/export")
def export_index_words(
    index_id: str,
    q: Optional[str] = Query(None),
    sort: str = Query("occurrences"),
    direction: str = Query("desc"),
    hide_stopwords: bool = Query(False),
    min_occurrences: int = Query(1, ge=1),
):
    """Exporte tout le vocabulaire (filtré/trié) en CSV téléchargeable."""
    built = IndexesService.build_vocabulary(
        index_id, q=q, sort=sort, direction=direction,
        hide_stopwords=hide_stopwords, min_occurrences=min_occurrences,
    )
    if built is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    _total_unique, entries = built

    import io, csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["mot", "occurrences", "pages"])
    for e in entries:
        writer.writerow([e["word"], e["occurrences"], e["pages"]])
    # BOM UTF-8 pour qu'Excel ouvre correctement les accents
    content = '﻿' + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{index_id}_vocabulaire.csv"'},
    )


@router.get("/{index_id}/search", response_model=MultiSearchResponse)
def search_in_index(
    index_id: str,
    q: str = Query(..., description="Mot(s) à rechercher (ex: cancer du sein)"),
    year_from: Optional[int] = Query(None, description="Année de début (incluse)"),
    year_to: Optional[int] = Query(None, description="Année de fin (incluse)"),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100, description="Seuil de similarité (0-100). 100 = exact/substring, <100 = fuzzy."),
    whole_word: bool = Query(False, description="Mots entiers seulement : « par » ne trouve pas « parfois »."),
    exclude: Optional[str] = Query(None, description="Mots à exclure : une page qui en contient un est écartée."),
    within: Optional[str] = Query(None, description="Id d'une sélection : ne chercher que dans ses pages."),
):
    """Recherche un ou plusieurs termes dans l'index avec intersection des pages.
    Les paramètres year_from et year_to permettent de restreindre la recherche à une période.
    fuzzy_threshold active la recherche approximative via rapidfuzz ; whole_word écarte les
    mots qui ne font que contenir un terme ; exclude écarte les pages qui contiennent un de
    ses mots (mêmes règles de correspondance) ; within limite la recherche aux pages d'une
    sélection enregistrée."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="La requête de recherche est vide")
    selection = _within(index_id, within)
    result = IndexesService.search_words(index_id, q, year_from=year_from, year_to=year_to,
                                         fuzzy_threshold=fuzzy_threshold, whole_word=whole_word,
                                         exclude=exclude,
                                         within=selection['pages'] if selection else None)
    if result is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    return result


@router.get("/{index_id}/search/stream")
def search_in_index_stream(
    index_id: str,
    q: str = Query(..., description="Mot(s) à rechercher (ex: cancer du sein)"),
    year_from: Optional[int] = Query(None, description="Année de début (incluse)"),
    year_to: Optional[int] = Query(None, description="Année de fin (incluse)"),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100, description="Seuil de similarité (0-100). 100 = exact/substring, <100 = fuzzy."),
    whole_word: bool = Query(False, description="Mots entiers seulement : « par » ne trouve pas « parfois »."),
    exclude: Optional[str] = Query(None, description="Mots à exclure : une page qui en contient un est écartée."),
    within: Optional[str] = Query(None, description="Id d'une sélection : ne chercher que dans ses pages."),
):
    """Même recherche que /search, mais en flux NDJSON : une ligne JSON par événement de
    progression ({"type": "progress", "phase", "current", "total"}), puis le résultat
    ({"type": "result", "result": {...}}). Une recherche sur un gros index prend une minute :
    le flux permet d'afficher une barre de progression au lieu d'un spinner."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="La requête de recherche est vide")
    selection = _within(index_id, within)

    def gen():
        try:
            for event in IndexesService.search_words_iter(
                index_id, q, year_from=year_from, year_to=year_to,
                fuzzy_threshold=fuzzy_threshold, whole_word=whole_word, exclude=exclude,
                within=selection['pages'] if selection else None,
            ):
                if event.get('type') == 'result' and event.get('result') is None:
                    yield json.dumps({"type": "error", "detail": "Index non trouvé ou non encore généré"},
                                     ensure_ascii=False) + "\n"
                    return
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:  # surface l'erreur dans le flux, sans casser la connexion
            yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ── Sélections de pages (recherche en entonnoir) ─────────────────────────────

@router.get("/{index_id}/selections", response_model=List[SelectionSummary])
def list_selections(index_id: str):
    """Sélections enregistrées sur cet index, de la plus récente à la plus ancienne (sans
    leurs pages)."""
    try:
        return SelectionsService.summaries(index_id)
    except SelectionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{index_id}/selections/import", response_model=SelectionImport)
def import_selection(index_id: str, file: UploadFile = File(...)):
    """Importe un classeur exporté par `export-results.xlsx` puis trié, en français ou en
    anglais : les pages à « oui » de la colonne Garder deviennent une sélection, nommée d'après
    le fichier. Un refus est un 400 dont le `detail` porte un `code` que la page traduit
    (`unreadable`, `not_archimed`, `missing_keep_column`, `missing_id_column`, `other_index`,
    `nothing_kept`), ses `params`, et un `message` en français."""
    WorkbookError = results_workbook.WorkbookError
    try:
        if IndexesService.get_index(index_id) is None:
            raise SelectionNotFound(f"Index inconnu : {index_id}")
        content = results_workbook.read_kept_pages(file.file.read())
        if content['index_id'] and content['index_id'] != index_id:
            origin = content['index_name'] or content['index_id']
            raise WorkbookError("other_index", f"Ce fichier a été exporté depuis l'index « {origin} », "
                                "pas depuis celui-ci.", index=origin)
        if not content['pages']:
            raise WorkbookError("nothing_kept", "Aucune page n'est à « oui » dans la colonne Garder.")
        name = Path(file.filename or '').stem or "Sélection importée"
        selection = SelectionsService.create(index_id, name, content['pages'], content['steps'],
                                             source_file=file.filename)
    except SelectionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except WorkbookError as e:
        raise HTTPException(status_code=400, detail={'code': e.code, 'params': e.params,
                                                     'message': str(e)})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'selection': selection, 'kept': content['kept'], 'dropped': content['dropped'],
            'ignored': content['ignored']}


@router.delete("/{index_id}/selections/{sel_id}")
def delete_selection(index_id: str, sel_id: str):
    try:
        SelectionsService.delete(index_id, sel_id)
    except SelectionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"deleted": sel_id}


# ── Statistiques ──────────────────────────────────────────────────────────────

@router.get("/{index_id}/stats/corpus")
def get_corpus_stats(index_id: str, top: int = Query(20, ge=1, le=100)):
    """Statistiques du corpus : totaux, top mots, distribution des fréquences,
    détail par registre, pages par décennie."""
    result = IndexStatsService.get_corpus_stats(index_id, top=top)
    if result is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    return result


@router.get("/{index_id}/stats/term-frequency")
def get_term_frequency(
    index_id: str,
    q: str = Query(..., description="Termes séparés par des virgules ou espaces (max 5)"),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100),
):
    """Évolution temporelle de termes (style Ngram) : occurrences par décennie,
    chaque registre étant rattaché à l'année médiane de sa période."""
    terms = [t for t in q.replace(',', ' ').split() if t]
    if not terms:
        raise HTTPException(status_code=400, detail="Aucun terme fourni")
    result = IndexStatsService.get_term_frequency(index_id, terms, fuzzy_threshold=fuzzy_threshold)
    if result is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    return result


@router.get("/{index_id}/stats/quality")
def get_quality_stats(index_id: str):
    """Indicateurs de qualité OCR : mots courts (bruit), hapax, pages vides,
    registres sans période."""
    result = IndexStatsService.get_quality_stats(index_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")
    return result


@router.get("/{index_id}/page-image/{page_name}")
def get_page_image(index_id: str, page_name: str):
    """Retourne l'image d'une page identifiée par son nom (stem du fichier XML)"""
    image_path = IndexesService.get_page_image_path(index_id, page_name)
    if not image_path:
        raise HTTPException(status_code=404, detail="Image non trouvée")
    return FileResponse(str(image_path))


@router.patch("/{index_id}", response_model=IndexMetadata)
def update_index(index_id: str, data: IndexUpdate):
    """Édite un index : renomme et/ou modifie ses sources. Si les sources changent, une
    reconstruction **complète** est ré-enfilée : les clés de namespace (s0, s1, …) se décalent,
    donc rien de l'index précédent n'est réutilisable."""
    meta = IndexesService.update_index_meta(
        index_id,
        name=data.name,
        sources=[s.model_dump() for s in data.sources] if data.sources is not None else None,
    )
    if not meta:
        raise HTTPException(status_code=404, detail="Index non trouvé")
    if data.sources is not None:
        try:
            enqueue_index(index_id=index_id, full=True)
        except TaskConflict as e:
            raise HTTPException(status_code=409, detail=str(e))
        except TaskUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return IndexesService.get_index(index_id)


@router.delete("/{index_id}")
def delete_index(index_id: str):
    """Supprime un index"""
    success = IndexesService.delete_index(index_id)
    if not success:
        raise HTTPException(status_code=404, detail="Index non trouvé")
    return {"message": "Index supprimé avec succès"}
