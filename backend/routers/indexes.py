from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from typing import List, Optional
from models import (
    IndexMetadata, IndexCreate, IndexUpdate, IndexPreviewRequest,
    WordSearchResponse, MultiSearchResponse,
)
from services import IndexesService
from stats_service import IndexStatsService
from index_runner import enqueue_index
from task_service import TaskConflict

router = APIRouter()


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
    if IndexesService.get_index(index_id) is None:
        raise HTTPException(status_code=404, detail="Index non trouvé")
    try:
        return enqueue_index(index_id=index_id, full=full)
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/available-models")
def list_available_models(collection_id: str = Query(..., description="ID ou folder_name de la collection")):
    """Liste les modèles OCR disponibles pour une collection (scan du dossier ocr/)"""
    try:
        models = IndexesService.list_available_models(collection_id)
        return models
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/updates")
def get_indexes_updates():
    """Pour chaque index prêt : nb de registres / pages OCR apparus depuis l'indexation.
    Calcul à part de la liste (peut rescanner index.json une fois) pour ne pas la ralentir."""
    try:
        result = []
        for meta in IndexesService.list_indexes():
            if meta.get('status') != 'ready':
                continue
            upd = IndexesService.compute_updates(meta)
            if upd['new_registres'] > 0 or upd['new_pages'] > 0:
                result.append({
                    "id": meta['id'],
                    "new_registres": upd['new_registres'],
                    "new_pages": upd['new_pages'],
                    "coverage_known": upd['coverage_known'],
                })
        return result
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
    download_token: Optional[str] = Query(None, description="Jeton renvoyé en cookie au démarrage du flux (loader front)"),
):
    """Exporte les images brutes (sans annotation) des pages résultats — et de leurs extra
    pages — en ZIP streamé."""
    files = IndexesService.resolve_result_zip_files(
        index_id, q, year_from=year_from, year_to=year_to, fuzzy_threshold=fuzzy_threshold,
    )
    if files is None:
        raise HTTPException(status_code=404, detail="Index non trouvé ou non encore généré")

    response = StreamingResponse(
        IndexesService.stream_pages_zip(files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{index_id}_pages.zip"'},
    )
    # Les en-têtes (dont Set-Cookie) partent une fois la préparation terminée, juste avant le
    # premier octet : le front s'en sert pour masquer le loader « Préparation de l'archive ».
    if download_token:
        response.set_cookie(
            "archimed_zip_ready", download_token,
            max_age=120, httponly=False, samesite="lax", path="/",
        )
    return response


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
