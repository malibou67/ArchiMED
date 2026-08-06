import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, Optional
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

@router.get("/scan/report", response_model=ScanReport)
def scan_report(token: str):
    """Rejoue le rapport d'analyse à partir de l'instantané `token`, **sans toucher au NAS**.

    Sert à rafraîchir le rapport après avoir synchronisé une collection depuis le dialogue :
    inutile de reparcourir tout le partage pour refléter ce qu'on vient d'écrire. 409 si
    l'instantané n'est plus utilisable, auquel cas le client relance une analyse complète."""
    report = CollectionsService.report_from_token(token)
    if report is None:
        raise HTTPException(status_code=409, detail="Instantané d'analyse expiré")
    return report

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
def sync_all_collections(token: Optional[str] = None):
    """Synchronise toutes les collections (reconstruit la liste des registres depuis le filesystem)"""
    try:
        return CollectionsService.sync_all_collections(token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync-all/stream")
def sync_all_collections_stream(token: Optional[str] = None):
    """Même synchronisation que /sync-all, mais en flux NDJSON : une ligne JSON de
    progression par collection ({"type":"progress","current","total","name"}) puis une
    ligne finale ({"type":"done","results":[...],"summary":[...]}).

    `token` est celui rendu par l'analyse : s'il désigne un instantané encore valable, la
    synchronisation ne relit pas le NAS et se contente d'écrire."""
    def gen():
        try:
            for event in CollectionsService.sync_all_collections_iter(token):
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
def sync_collection_stream(collection_id: str, token: Optional[str] = None):
    """Même synchronisation que /{collection_id}/sync, mais en flux NDJSON : une ligne
    JSON de progression par registre ({"type":"registre","reg_current","reg_total"})
    puis une ligne finale ({"type":"result","metadata":{...}}).

    `token` est celui rendu par l'analyse : s'il désigne un instantané encore valable, la
    synchronisation repart de ce qui a déjà été lu."""
    def gen():
        try:
            for event in CollectionsService.sync_collection_with_token_iter(collection_id, token):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "detail": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")
