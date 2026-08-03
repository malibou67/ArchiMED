"""Runner d'indexation, branché sur le moteur de tâches générique.

Module séparé (importe `services` ET `task_service`) pour éviter un cycle d'import :
`task_service` importe `services`, et `services` n'importe pas `task_service`.
"""
from typing import Any, Dict, List, Optional

from services import IndexesService
from task_service import TaskService


def enqueue_index(create: Optional[Dict[str, Any]] = None, *, index_id: Optional[str] = None,
                  full: bool = False) -> dict:
    """Ajoute une tâche d'indexation à la lane 'index'.

    Deux usages :
      - **nouvel index** : `create = {name, sources:[{collection_id, model_name}]}` ;
      - **mise à jour** : `index_id` d'un index existant (ses sources/nom sont relus). Elle est
        incrémentale par défaut ; `full=True` force la réindexation de tous les registres.

    `full` voyage dans la tâche (`index_full`) plutôt que dans le metadata de l'index : les
    champs de tâche sont persistés tels quels et survivent donc à une pause, une reprise ou un
    redémarrage de l'application.

    L'index est matérialisé AVANT l'enfilage pour que le runner trouve son metadata et que la
    ligne apparaisse immédiatement ; en cas de conflit de scope, on annule la matérialisation.
    """
    if index_id:
        meta = IndexesService.get_index(index_id)
        if not meta:
            raise ValueError(f"Index introuvable : {index_id}")
        name = meta.get('name') or index_id
        sources_info = meta.get('sources')
        if not sources_info:
            # Legacy mono-source : reconstruit sur la même (collection, modèle).
            sources_info = IndexesService._resolve_sources(
                [{"collection_id": meta.get('collection_id'), "model_name": meta.get('model_name')}]
            )
        is_new = False
    else:
        if not create or not create.get('sources'):
            raise ValueError("Aucune source fournie pour l'index.")
        name = (create.get('name') or '').strip() or 'Index'
        sources_info = IndexesService._resolve_sources(create['sources'])
        index_id = IndexesService._new_index_id(name)
        is_new = True
        full = True   # rien à réutiliser pour un index qui n'existe pas encore

    registres: List[Dict[str, Any]] = IndexesService.list_sources_registres(sources_info)
    fields = {
        'index_id': index_id,
        'index_name': name,
        'index_is_new': is_new,
        'index_full': bool(full),
        'index_registres': registres,
        # Résumé compact des sources (collection + modèle) pour l'affichage des tâches
        # multi-collections/modèles ; l'ancien couple collection_id/model_name ne suffit plus.
        'index_sources': [
            {
                'collection_titre': s.get('collection_titre') or s.get('collection_folder'),
                'collection_folder': s.get('collection_folder'),
                'model_name': s.get('model_name'),
            }
            for s in sources_info
        ],
        'total': sum(r['pages'] for r in registres),
    }

    # Matérialisation avant enfilage (le thread d'exécution peut démarrer aussitôt).
    if is_new:
        IndexesService.init_index_new(index_id, name, sources_info)
    else:
        # Persiste les sources résolues (convertit au passage un ancien index mono-source).
        IndexesService.mark_rebuild(index_id, sources_info)
    try:
        return TaskService.enqueue('index', name, fields)
    except Exception:
        # Rollback de la matérialisation si l'enfilage échoue (conflit de scope, …).
        if is_new:
            try:
                IndexesService.delete_index(index_id)
            except Exception:
                pass
        else:
            try:
                IndexesService.clear_build(index_id)
            except Exception:
                pass
        raise


def run_index_task(task: dict) -> None:
    """Runner appelé par TaskService : construit l'index et rapporte la progression.
    L'index (metadata.json + index.json) reste la source pour la liste et la recherche."""
    index_id = task['index_id']

    def on_progress(processed: int, total: int, current, page=None) -> None:
        task['total'] = total
        task['processed'] = processed
        task['current'] = current      # registre en cours (détail de tâche)
        task['current_page'] = page    # page en cours de lecture
        TaskService._save_throttled(task)

    def should_cancel() -> bool:
        return bool(task.get('cancel'))

    def should_pause() -> bool:
        return bool(task.get('pause'))

    def on_plan(skipped: list, base: int = 0) -> None:
        # Registres conservés par la mise à jour incrémentale : hors de la progression (qui ne
        # décrit que le travail de ce run), ils sont affichés 'done' d'emblée dans le détail de la
        # tâche, et leurs pages annoncées à part (`index_base`).
        task['index_skipped'] = skipped
        task['index_base'] = base
        TaskService._save(task)

    result = IndexesService.generate_index(
        index_id,
        on_progress=on_progress, should_cancel=should_cancel, should_pause=should_pause,
        full=bool(task.get('index_full')), on_plan=on_plan,
    )

    if result == 'paused':
        task['status'] = 'paused'   # le checkpoint permettra la reprise
    elif result == 'cancelled':
        task['status'] = 'cancelled'
        _cleanup_cancelled(task)


def _cleanup_cancelled(task: dict) -> None:
    """Nettoyage après annulation : un nouvel index partiel est supprimé, une reconstruction
    conserve l'index précédent (on retire seulement le staging/marqueur `build`)."""
    iid = task.get('index_id')
    if not iid:
        return
    try:
        if task.get('index_is_new'):
            IndexesService.delete_index(iid)
        else:
            IndexesService.clear_build(iid)
    except Exception:
        pass


def _on_cancel_index(task: dict) -> None:
    """Annulation d'une tâche en attente : idem nettoyage (index partiel / reconstruction)."""
    _cleanup_cancelled(task)


TaskService.register('index', run_index_task, on_cancel=_on_cancel_index)
