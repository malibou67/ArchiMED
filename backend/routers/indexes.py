import json
import re
import uuid

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from typing import List, Optional
from models import (
    IndexMetadata, IndexCreate, IndexUpdate, IndexUpdates, IndexPreviewRequest,
    WordSearchResponse, MultiSearchResponse,
)
from services import IndexesService
from stats_service import IndexStatsService
from export_service import PagesExportService, ExportCancelled
from index_runner import enqueue_index
from task_service import TaskConflict, TaskUnavailable

router = APIRouter()

# Jeton d'export fourni par la page (UUID, ou repli horodaté hors contexte sécurisé).
_DOWNLOAD_TOKEN = re.compile(r'^[A-Za-z0-9-]{8,64}$')


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
):
    """Exporte les résultats d'une recherche en CSV (registre, page, occurrences, chemin)."""
    rows = IndexesService.resolve_result_pages(
        index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")

    import io, csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["collection", "modèle", "registre", "page", "occurrences", "chemin"])
    for r in rows:
        writer.writerow([r.get("source", ""), r.get("model", ""), r["registre"], r["page"], r["occurrences"], r["chemin"]])
    content = '﻿' + buf.getvalue()  # BOM UTF-8 pour Excel
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{index_id}_resultats.csv"'},
    )


@router.get("/{index_id}/export-pages.zip")
def export_pages_zip(
    index_id: str,
    q: str = Query(..., description="Requête (mêmes termes que la recherche)"),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    fuzzy_threshold: Optional[int] = Query(None, ge=0, le=100),
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
    PagesExportService.start(
        token, index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
    )
    try:
        files = PagesExportService.prepare(token)
    except ExportCancelled:
        raise HTTPException(status_code=409, detail="Export annulé")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e) or type(e).__name__)
    if files is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")

    return _ClosingStreamingResponse(
        PagesExportService.stream(token, files),
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
):
    """Recherche un ou plusieurs termes dans l'index avec intersection des pages.
    Les paramètres year_from et year_to permettent de restreindre la recherche à une période.
    fuzzy_threshold active la recherche approximative via rapidfuzz."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="La requête de recherche est vide")
    result = IndexesService.search_words(index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold)
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
):
    """Même recherche que /search, mais en flux NDJSON : une ligne JSON par événement de
    progression ({"type": "progress", "phase", "current", "total"}), puis le résultat
    ({"type": "result", "result": {...}}). Une recherche sur un gros index prend une minute :
    le flux permet d'afficher une barre de progression au lieu d'un spinner."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="La requête de recherche est vide")

    def gen():
        try:
            for event in IndexesService.search_words_iter(
                index_id, q, year_from=year_from, year_to=year_to,
                fuzzy_threshold=fuzzy_threshold,
            ):
                if event.get('type') == 'result' and event.get('result') is None:
                    yield json.dumps({"type": "error", "detail": "Index non trouvé ou non encore généré"},
                                     ensure_ascii=False) + "\n"
                    return
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:  # surface l'erreur dans le flux, sans casser la connexion
            yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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
