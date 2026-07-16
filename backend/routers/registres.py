from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from typing import List
from pathlib import Path
import os
from models import RegistreMetadata, RegistreCreate, RegistreUpdate
from services import RegistresService, DATA_DIR

router = APIRouter()

@router.get("/", response_model=List[RegistreMetadata])
def list_all_registres():
    """Liste tous les registres de toutes les collections"""
    try:
        # Pour simplifier, on récupère les registres de toutes les collections
        from services import CollectionsService
        all_registres = []
        collections = CollectionsService.list_collections()
        for col in collections:
            col_id = col.get('folder_name', col.get('type'))
            registres = RegistresService.list_registres(col_id)
            all_registres.extend(registres)
        return all_registres
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{collection_id}", response_model=List[RegistreMetadata])
def list_registres(collection_id: str):
    """Liste tous les registres d'une collection"""
    try:
        registres = RegistresService.list_registres(collection_id)
        return registres
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{collection_id}/{registre_id}", response_model=RegistreMetadata)
def get_registre(collection_id: str, registre_id: str):
    """Récupère un registre spécifique"""
    try:
        registre = RegistresService.get_registre(collection_id, registre_id)
        if not registre:
            raise HTTPException(status_code=404, detail="Registre not found")
        return registre
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{collection_id}/{registre_id}/pages", response_model=List[str])
def list_scan_pages(collection_id: str, registre_id: str):
    """Liste les pages scannées d'un registre"""
    try:
        pages = RegistresService.list_scan_pages(collection_id, registre_id)
        return pages
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{collection_id}/{registre_id}/transcriptions")
def list_page_transcriptions(collection_id: str, registre_id: str):
    """Modèles ayant transcrit chaque page d'un registre (stem image -> [modèles])"""
    try:
        return RegistresService.list_page_transcriptions(collection_id, registre_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{collection_id}/{registre_id}/pages/{filename}")
def get_scan_page(collection_id: str, registre_id: str, filename: str):
    """Sert une image de scan d'un registre"""
    file_path = Path(DATA_DIR) / "collections" / collection_id / "scans" / registre_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(file_path)

@router.put("/{collection_id}/{registre_id}", response_model=RegistreMetadata)
def update_registre(collection_id: str, registre_id: str, update: RegistreUpdate):
    """Met à jour les métadonnées d'un registre"""
    try:
        update_data = {k: v for k, v in update.model_dump().items() if v is not None}
        result = RegistresService.update_registre(collection_id, registre_id, update_data)
        if not result:
            raise HTTPException(status_code=404, detail="Registre not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{collection_id}", response_model=RegistreMetadata)
def create_registre(collection_id: str, registre: RegistreCreate):
    """Crée un nouveau registre dans une collection"""
    try:
        import time
        registre_data = registre.model_dump()
        registre_data['id'] = f"reg_{collection_id}_{int(time.time())}"

        result = RegistresService.create_registre(collection_id, registre_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
