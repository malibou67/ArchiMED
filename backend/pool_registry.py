"""Registre des pools de process vivants, pour pouvoir les tuer à coup sûr.

`ProcessPoolExecutor.shutdown()` ne sait pas interrompre une tâche déjà commencée : il laisse
chaque worker finir la sienne (plusieurs minutes quand le GPU est chargé), et un `os._exit()`
— ce que fait « Quitter » dans la barre système — les rend carrément orphelins : ils
continuent à occuper GPU, RAM et handles longtemps après. On garde donc la main sur les pools
vivants pour pouvoir les tuer à l'annulation, à la pause et à la fermeture.

Module à part et **sans dépendance** : l'OCR (`ocr_service`) et l'indexation (`services`) y
inscrivent tous deux leurs pools, or `services` ne peut pas importer `ocr_service` (cycle
`ocr_service` → `settings_service` → `services`).
"""
import atexit
import threading

_ACTIVE_POOLS: set = set()
_POOLS_LOCK = threading.Lock()


def add(executor) -> None:
    with _POOLS_LOCK:
        _ACTIVE_POOLS.add(executor)


def discard(executor) -> None:
    with _POOLS_LOCK:
        _ACTIVE_POOLS.discard(executor)


def kill_pool(executor) -> int:
    """Tue les process d'un pool et retourne le nombre tué. Les tâches en cours sont
    abandonnées : à l'appelant de les considérer comme non faites (côté OCR l'état de la page
    reste 0 « à faire » et l'écriture des PAGE-XML est atomique ; côté indexation le registre
    n'entre pas dans `done_registres`)."""
    killed = 0
    for proc in list(getattr(executor, '_processes', {}).values()):
        try:
            if proc.is_alive():
                proc.kill()
                killed += 1
        except Exception:
            pass
    return killed


def kill_active_pools() -> int:
    """Tue tous les pools encore vivants de ce process (fermeture de l'application)."""
    with _POOLS_LOCK:
        pools = list(_ACTIVE_POOLS)
    return sum(kill_pool(ex) for ex in pools)


atexit.register(kill_active_pools)
