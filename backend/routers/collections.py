import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List
from models import CollectionMetadata, CollectionCreate, CollectionUpdate, ScanReport, CollectionStatsResponse
from services import CollectionsService
from stats_service import CollectionStatsService

router = APIRouter()

@router.get("/", response_model=List[CollectionMetadata])
def list_collections():
    """Liste toutes les collections disponibles"""
    try:
        collections = CollectionsService.list_collections()
        return collections
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scan", response_model=ScanReport)
def scan_filesystem():
    """Analyse le dossier data/collections et retourne le statut de chaque collection/registre"""
    try:
        return CollectionsService.scan_filesystem()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scan/stream")
def scan_filesystem_stream():
    """Même analyse que /scan, mais en flux NDJSON : une ligne JSON de progression par
    collection ({"type":"progress","current","total","name"}) puis une ligne finale
    ({"type":"report","report":{...}}). Permet d'afficher l'avancement côté client."""
    def gen():
        try:
            for event in CollectionsService.scan_filesystem_iter():
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:  # surface l'erreur dans le flux, sans casser la connexion
            yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")

@router.post("/sync-all", response_model=List[CollectionMetadata])
def sync_all_collections():
    """Synchronise toutes les collections (reconstruit la liste des registres depuis le filesystem)"""
    try:
        return CollectionsService.sync_all_collections()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync-all/stream")
def sync_all_collections_stream():
    """Même synchronisation que /sync-all, mais en flux NDJSON : une ligne JSON de
    progression par collection ({"type":"progress","current","total","name"}) puis une
    ligne finale ({"type":"done","results":[...]})."""
    def gen():
        try:
            for event in CollectionsService.sync_all_collections_iter():
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")

@router.get("/{collection_id}/stats", response_model=CollectionStatsResponse)
def get_collection_stats(collection_id: str):
    """Statistiques d'avancement d'une collection : volume scanné, couverture OCR
    par modèle, pages par décennie, couverture transcription, détail par registre."""
    try:
        result = CollectionStatsService.get_stats(collection_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Collection non trouvée")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{collection_id}", response_model=CollectionMetadata)
def get_collection(collection_id: str):
    """Récupère une collection spécifique"""
    try:
        collection = CollectionsService.get_collection(collection_id)
        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")
        return collection
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", response_model=CollectionMetadata)
def create_collection(collection: CollectionCreate):
    """Crée une nouvelle collection"""
    try:
        # Générer un ID unique
        import time
        collection_data = collection.model_dump()
        collection_data['id'] = f"col_{int(time.time())}"

        result = CollectionsService.create_collection(collection_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{collection_id}", response_model=CollectionMetadata)
def update_collection(collection_id: str, collection: CollectionUpdate):
    """Met à jour les métadonnées d'une collection"""
    try:
        update_data = collection.model_dump(exclude_none=True)
        result = CollectionsService.update_collection(collection_id, update_data)
        if not result:
            raise HTTPException(status_code=404, detail="Collection not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{collection_id}/sync", response_model=CollectionMetadata)
def sync_collection(collection_id: str):
    """Synchronise les métadonnées d'une collection en scannant le filesystem"""
    try:
        result = CollectionsService.sync_collection_metadata(collection_id)
        if not result:
            raise HTTPException(status_code=404, detail="Collection not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{collection_id}/sync/stream")
def sync_collection_stream(collection_id: str):
    """Même synchronisation que /{collection_id}/sync, mais en flux NDJSON : une ligne
    JSON de progression par registre ({"type":"registre","reg_current","reg_total"})
    puis une ligne finale ({"type":"result","metadata":{...}})."""
    def gen():
        try:
            for event in CollectionsService.sync_collection_metadata_iter(collection_id):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
