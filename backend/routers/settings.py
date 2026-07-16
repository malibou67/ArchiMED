import os
import shutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services import DATA_DIR
from settings_service import SettingsService
import ocr_service

router = APIRouter()


class SettingsUpdate(BaseModel):
    # Clé absente → inchangée ; clé à null → retour au défaut ; valeur → définie.
    ocr_workers: Optional[int] = None
    ocr_threads_per_worker: Optional[int] = None
    ocr_mixed_precision: Optional[bool] = None
    ocr_pool_min_pages: Optional[int] = None


def _total_ram_gb() -> Optional[float]:
    """RAM physique totale en Go, sans dépendance externe. None si indéterminable."""
    try:
        if os.name == 'nt':
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return None
            total = stat.ullTotalPhys
        else:
            total = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        return round(total / 2**30, 1)
    except Exception:
        return None


def _disk_free_gb() -> Optional[float]:
    """Espace libre (Go) sur le volume du dossier de données. None si indéterminable."""
    try:
        return round(shutil.disk_usage(DATA_DIR).free / 2**30, 1)
    except OSError:
        return None


def _payload() -> dict:
    cores = os.cpu_count() or 1
    return {
        "stored": SettingsService.get(),
        "effective": {
            "ocr_workers": ocr_service.effective_workers(),
            "ocr_threads_per_worker": ocr_service.effective_threads(),
            "ocr_mixed_precision": ocr_service.effective_mixed(),
            "ocr_pool_min_pages": ocr_service.effective_pool_min_pages(),
        },
        "system": {
            "cpu_count": cores,
            "max_workers": cores,
            "recommended_workers": ocr_service.adaptive_default_workers(cores),
            "ram_total_gb": _total_ram_gb(),
            "disk_free_gb": _disk_free_gb(),
        },
    }


@router.get("")
def get_settings():
    """Réglages stockés + valeurs effectives (UI > env > défaut) + infos système."""
    return _payload()


@router.put("")
def update_settings(update: SettingsUpdate):
    """Met à jour les réglages. Une clé fournie à null revient au défaut."""
    cores = os.cpu_count() or 1
    partial = update.model_dump(exclude_unset=True)  # garde les null explicites

    if 'ocr_workers' in partial and partial['ocr_workers'] is not None:
        w = partial['ocr_workers']
        if not isinstance(w, int) or w < 1:
            raise HTTPException(status_code=400, detail="ocr_workers doit être un entier ≥ 1.")
        partial['ocr_workers'] = min(w, cores)  # borné au nb de cœurs

    if 'ocr_threads_per_worker' in partial and partial['ocr_threads_per_worker'] is not None:
        t = partial['ocr_threads_per_worker']
        if not isinstance(t, int) or t < 1:
            raise HTTPException(status_code=400, detail="ocr_threads_per_worker doit être un entier ≥ 1.")
        partial['ocr_threads_per_worker'] = min(t, 16)

    if 'ocr_pool_min_pages' in partial and partial['ocr_pool_min_pages'] is not None:
        p = partial['ocr_pool_min_pages']
        if not isinstance(p, int) or p < 1:
            raise HTTPException(status_code=400, detail="ocr_pool_min_pages doit être un entier ≥ 1.")
        partial['ocr_pool_min_pages'] = min(p, 50)

    SettingsService.update(partial)
    return _payload()
