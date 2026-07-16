from fastapi import APIRouter, HTTPException, Query

from task_service import TaskService
from ocr_service import OcrService
from services import IndexesService

router = APIRouter()


@router.get("/summary")
def get_summary():
    """Résumé léger (tâches en cours + nb en attente) pour l'indicateur/widget global."""
    return TaskService.summary()


@router.get("")
def list_tasks():
    """Toutes les tâches (tous types) : en cours, en attente, puis historique."""
    return TaskService.list_tasks()


@router.get("/{task_id}")
def get_task(task_id: str):
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    return TaskService._public(task, full=True)


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str):
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    cancelled = TaskService.cancel(task_id)
    return {"cancelled": cancelled, "task": TaskService._public(TaskService.get(task_id), full=True)}


@router.post("/{task_id}/pause")
def pause_task(task_id: str):
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    paused = TaskService.pause(task_id)
    return {"paused": paused, "task": TaskService._public(TaskService.get(task_id), full=True)}


@router.post("/{task_id}/resume")
def resume_task(task_id: str):
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    resumed = TaskService.resume(task_id)
    return {"resumed": resumed, "task": TaskService._public(TaskService.get(task_id), full=True)}


@router.delete("/{task_id}")
def delete_task(task_id: str):
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    if not TaskService.delete(task_id):
        raise HTTPException(status_code=409, detail="Impossible de supprimer une tâche en cours.")
    return {"deleted": True}


@router.get("/{task_id}/pages")
def get_task_pages(
    task_id: str,
    status: str = Query('all', pattern='^(all|todo|done|failed)$'),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Détail par page (tâches OCR uniquement). Vide pour les autres types."""
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    if task.get('type') != 'ocr':
        return {"total": 0, "offset": offset, "limit": limit,
                "counts": {"all": 0, "done": 0, "failed": 0, "todo": 0}, "items": []}
    return OcrService.pages_slice(task, status, offset, limit)


@router.get("/{task_id}/registres")
def get_task_registres(task_id: str):
    """État par registre (tâches d'indexation). Vide pour les autres types."""
    task = TaskService.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable.")
    if task.get('type') != 'index':
        return {"counts": {"all": 0, "done": 0, "current": 0, "pending": 0}, "items": []}
    items = IndexesService.task_registres(task)
    counts = {
        "all": len(items),
        "done": sum(1 for r in items if r['status'] == 'done'),
        "current": sum(1 for r in items if r['status'] == 'current'),
        "pending": sum(1 for r in items if r['status'] == 'pending'),
    }
    return {"counts": counts, "items": items}
