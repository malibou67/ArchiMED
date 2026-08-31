"""Runner d'indexation, branché sur le moteur de tâches générique.

Module séparé (importe `services` ET `task_service`) pour éviter un cycle d'import :
`task_service` importe `services`, et `services` n'importe pas `task_service`.
"""
import time
from typing import Any, Dict, Optional

from app_logging import get_logger, kv
from services import IndexesService
from task_service import TaskService

log = get_logger('index')


def enqueue_index(create: Optional[Dict[str, Any]] = None, *, index_id: Optional[str] = None,
                  full: bool = False, meta: Optional[Dict[str, Any]] = None) -> dict:
    """Ajoute une tâche d'indexation à la lane 'index'.

    Deux usages :
      - **nouvel index** : `create = {name, sources:[{collection_id, model_name}]}` ;
      - **mise à jour** : `index_id` d'un index existant (ses sources/nom sont relus, ou
        `meta` si l'appelant vient déjà de les lire). Elle est incrémentale par défaut ;
        `full=True` force la réindexation de tous les registres.

    `full` voyage dans la tâche (`index_full`) plutôt que dans le metadata de l'index : les
    champs de tâche sont persistés tels quels et survivent donc à une pause, une reprise ou un
    redémarrage de l'application.

    L'index est matérialisé AVANT l'enfilage pour que le runner trouve son metadata et que la
    ligne apparaisse immédiatement ; en cas de conflit de scope, on annule la matérialisation.

    Cette fonction s'exécute **dans la requête HTTP** : elle ne doit toucher au partage que le
    strict nécessaire. La liste des registres, qui exigeait d'énumérer des dizaines de milliers
    de XML, est désormais établie par le runner (`on_scan`) : le clic répond aussitôt.
    """
    if index_id:
        if meta is None:
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

    fields = {
        'index_id': index_id,
        'index_name': name,
        'index_is_new': is_new,
        'index_full': bool(full),
        # Remplis par le runner (`on_scan`) dès la fin de sa première passe : les établir ici
        # imposait de parcourir tout le partage avant même de répondre au clic.
        'index_registres': [],
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
        'total': 0,
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
    started = time.time()
    log.info("Indexation " + kv(
        id=task['id'], index=index_id, nom=task.get('index_name'),
        mode='complète' if task.get('index_full') else 'incrémentale',
        nouveau=bool(task.get('index_is_new')),
        registres=len(task.get('index_registres') or []), pages=task.get('total'),
        sources=len(task.get('index_sources') or [])))

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
        if skipped:
            log.info("Registres réutilisés tels quels " + kv(
                index=index_id, registres=len(skipped), pages=base))

    def on_scan(registres: list, total: int) -> None:
        # La liste des registres est établie par la première passe du runner, pas à l'enfilage :
        # le détail de la tâche est donc vide pendant les premières secondes, le temps du scan.
        task['index_registres'] = registres
        task['total'] = total
        TaskService._save(task)
        log.info("Registres à traiter " + kv(
            index=index_id, registres=len(registres), pages=total))

    try:
        result = IndexesService.generate_index(
            index_id,
            on_progress=on_progress, should_cancel=should_cancel, should_pause=should_pause,
            full=bool(task.get('index_full')), on_plan=on_plan, on_scan=on_scan,
        )
    except Exception as e:
        # `generate_index` retire déjà le marqueur `build`, mais il ne peut rien faire si
        # l'échec l'a court-circuité (MemoryError, dossier disparu…). Ce filet garantit qu'un
        # index ne reste jamais affiché « en reconstruction » avec une tâche déjà terminée.
        log.error("Indexation échouée " + kv(id=task['id'], index=index_id), exc_info=True)
        try:
            IndexesService.fail_build(index_id, str(e))
        except Exception:
            pass
        raise

    log.info("Indexation terminée " + kv(
        id=task['id'], index=index_id, issue=result or 'done',
        pages=task.get('processed'), écoulé=f"{int(time.time() - started)}s"))

    if result == 'paused':
        task['status'] = 'paused'   # le checkpoint permettra la reprise
    elif result == 'cancelled':
        task['status'] = 'cancelled'
        _cleanup_cancelled(task)


def _cleanup_cancelled(task: dict) -> None:
    """Nettoyage après annulation ou échec d'une tâche d'indexation.

    La décision (supprimer / conserver) revient à `abort_build`, qui la prend sur l'état réel
    du disque. Elle reposait sur `index_is_new`, figé à l'enfilage : après un redémarrage, une
    purge d'historique ou depuis un autre poste, ce champ pouvait faire supprimer un index
    parfaitement valide."""
    iid = task.get('index_id')
    if not iid:
        return
    try:
        IndexesService.abort_build(iid)
    except Exception:
        pass


def _on_cancel_index(task: dict) -> None:
    """Annulation d'une tâche en attente : idem nettoyage (index partiel / reconstruction)."""
    _cleanup_cancelled(task)


def _on_error_index(task: dict) -> None:
    """Échec d'une tâche d'indexation *hors* du runner (conflit de verrou, runner absent).

    On retire le marqueur `build` sans rien supprimer : contrairement à une annulation, un
    échec doit rester visible — l'index concerné garde son message d'erreur."""
    iid = task.get('index_id')
    if not iid:
        return
    try:
        IndexesService.fail_build(iid, task.get('error') or "Échec de la tâche d'indexation.")
    except Exception:
        pass


def reconcile_orphan_builds() -> int:
    """Au démarrage : signale les reconstructions dont plus aucune tâche ne s'occupe.

    Un arrêt brutal, une purge d'historique ou une tâche supprimée à la main laissaient un
    index marqué « en reconstruction » sans rien pour le faire avancer. La ligne restait
    figée, et la seule action offerte devenait l'annulation. On la marque 'interrupted' :
    l'interface propose alors explicitement de reprendre ou d'abandonner.

    Retourne le nombre d'index remis en cohérence."""
    try:
        indexes = IndexesService.list_indexes()
    except Exception:
        return 0
    live = {t.get('index_id') for t in TaskService.list_tasks()
            if t.get('type') == 'index' and t.get('status') not in ('done', 'error',
                                                                    'cancelled')}
    fixed = 0
    for meta in indexes:
        build = meta.get('build')
        if not build or meta.get('id') in live or build.get('status') == 'interrupted':
            continue
        try:
            fresh = IndexesService.get_index(meta['id'])
            if not fresh or not fresh.get('build'):
                continue
            fresh['build']['status'] = 'interrupted'
            IndexesService._save_index_meta(meta['id'], fresh)
            fixed += 1
        except Exception:
            continue
    if fixed:
        log.info("Reconstructions orphelines signalées " + kv(index=fixed))
    return fixed


TaskService.register('index', run_index_task, on_cancel=_on_cancel_index,
                     on_error=_on_error_index)
