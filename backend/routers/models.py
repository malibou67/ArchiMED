from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Body
from typing import List, Optional, Dict, Any
from models import ModelMetadata
from services import ModelsService, extract_mlmodel_metadata

router = APIRouter()

@router.get("/", response_model=List[ModelMetadata])
def list_models():
    """Liste tous les modèles OCR disponibles"""
    try:
        models = ModelsService.list_models()
        return models
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/extract-metadata")
async def extract_metadata(file: UploadFile = File(...)):
    """Extrait les métadonnées d'un fichier .mlmodel Kraken via introspection"""
    if not file.filename or not file.filename.endswith(".mlmodel"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers .mlmodel sont acceptés")
    try:
        content = await file.read()
        metadata = extract_mlmodel_metadata(content, file.filename)
        return metadata
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/", response_model=ModelMetadata)
async def create_model(
    id: str = Form(...),
    name: str = Form(...),
    type: str = Form('ocr'),
    description: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    accuracy: Optional[float] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Ajoute un nouveau modèle avec un fichier optionnel"""
    try:
        model_data = {
            "id": id,
            "name": name,
            "type": type,
            "description": description,
            "version": version,
        }
        if accuracy is not None:
            model_data["accuracy"] = accuracy

        file_content = None
        file_name = None
        if file and file.filename:
            file_content = await file.read()
            file_name = file.filename

        result = ModelsService.add_model(model_data, file_content=file_content, file_name=file_name)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{model_id}")
def delete_model(model_id: str):
    """Supprime un modèle"""
    deleted = ModelsService.delete_model(model_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Modèle non trouvé")
    return {"detail": "Modèle supprimé"}

@router.get("/{model_id}", response_model=ModelMetadata)
def get_model(model_id: str):
    """Récupère les détails d'un modèle"""
    model = ModelsService.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Modèle non trouvé")
    return model

@router.put("/{model_id}", response_model=ModelMetadata)
def update_model(model_id: str, update_data: Dict[str, Any] = Body(...)):
    """Met à jour les métadonnées d'un modèle"""
    try:
        result = ModelsService.update_model(model_id, update_data)
        if not result:
            raise HTTPException(status_code=404, detail="Modèle non trouvé")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
