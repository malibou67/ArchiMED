from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ocr_service import OcrService, NoPagesToProcess
from task_service import TaskConflict, TaskUnavailable

router = APIRouter()


class OcrPageRef(BaseModel):
    collection: str
    registre: str
    page: str


class OcrScopeItem(BaseModel):
    collection: str
    registre: Optional[str] = None


# Filtres d'état d'un périmètre, miroir de `OcrService._SCOPE_ONLY`.
SCOPE_ONLY = ('all', 'missing', 'done')


class OcrRunRequest(BaseModel):
    seg_model_id: str
    ocr_model_id: str
    # Pages nommées une à une, et/ou registres entiers développés par le backend depuis le
    # disque. Le périmètre évite au client d'envoyer — et de fabriquer — les centaines de
    # milliers de noms d'une grosse collection.
    pages: List[OcrPageRef] = []
    scopes: Optional[List[OcrScopeItem]] = None
    scope_only: str = 'all'


class OcrMissingRequest(BaseModel):
    model: str
    scope: Optional[List[OcrScopeItem]] = None


@router.post("/run")
def run_ocr(request: OcrRunRequest):
    """Ajoute une tâche OCR à la file (gérée par le moteur de tâches générique)."""
    if not request.pages and not request.scopes:
        raise HTTPException(status_code=400, detail="Aucune page à traiter.")
    if not request.seg_model_id or not request.ocr_model_id:
        raise HTTPException(status_code=400, detail="Modèle de segmentation et modèle OCR requis.")
    if request.scope_only not in SCOPE_ONLY:
        raise HTTPException(status_code=400, detail=f"Filtre de périmètre inconnu : {request.scope_only}")
    pages = [p.model_dump() for p in request.pages]
    scopes = [s.model_dump() for s in request.scopes] if request.scopes else None
    try:
        return OcrService.enqueue(request.seg_model_id, request.ocr_model_id, pages,
                                  scopes=scopes, scope_only=request.scope_only)
    except NoPagesToProcess as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TaskConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except TaskUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/done")
def ocr_done(collection: str, registre: str, model: str):
    """Stems des pages déjà transcrites pour (collection, registre, modèle)."""
    return {"stems": OcrService.done_stems(collection, registre, model)}


@router.post("/missing")
def ocr_missing(request: OcrMissingRequest):
    """Pages sans transcription pour un modèle (périmètre optionnel)."""
    if not request.model:
        raise HTTPException(status_code=400, detail="Modèle OCR requis.")
    scope = [s.model_dump() for s in request.scope] if request.scope else None
    pages = OcrService.missing_pages(request.model, scope)
    return {"model": request.model, "count": len(pages), "pages": pages}
