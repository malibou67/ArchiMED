from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from models import TranscriptionInfo
from services import TranscriptionsService

router = APIRouter()

@router.get("/", response_model=List[TranscriptionInfo])
def list_transcriptions(
    collection_id: Optional[str] = Query(None, description="Filtrer par collection"),
    registre_id: Optional[str] = Query(None, description="Filtrer par registre")
):
    """Liste toutes les transcriptions, avec filtres optionnels"""
    try:
        transcriptions = TranscriptionsService.list_transcriptions(collection_id, registre_id)
        return transcriptions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/summary")
def get_transcriptions_summary():
    """Résumé agrégé par collection / registre / modèle — rapide, sans lecture individuelle des fichiers"""
    try:
        return TranscriptionsService.get_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats")
def get_transcriptions_stats():
    """Récupère des statistiques sur les transcriptions"""
    try:
        transcriptions = TranscriptionsService.list_transcriptions()

        stats = {
            "total": len(transcriptions),
            "by_collection": {},
            "by_model": {}
        }

        for trans in transcriptions:
            col_id = trans["collection_id"]
            model_name = trans["model_name"]

            stats["by_collection"][col_id] = stats["by_collection"].get(col_id, 0) + 1
            stats["by_model"][model_name] = stats["by_model"].get(model_name, 0) + 1

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
