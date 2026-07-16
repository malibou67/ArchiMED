"""Vérification de l'environnement OCR (torch, torchvision, kraken, CUDA).

Utilisée à deux endroits :
- le endpoint GET /api/system/requirements (affichage informatif côté UI) ;
- le préflight de `run_ocr_task` (vérité au moment de l'exécution, journalisée dans la tâche).
"""
from typing import Any, Dict, Optional

# Résultat mémorisé du dernier check complet (les imports torch/kraken ne changent
# pas pendant la vie du process). Utilisé uniquement quand use_cache=True.
_cached: Optional[Dict[str, Any]] = None


def _pkg_version(name: str):
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version(name)
    except Exception:
        return None


def check_requirements(use_cache: bool = False) -> Dict[str, Any]:
    """Vérifie que l'environnement peut réellement exécuter l'OCR Kraken.

    On ne se contente pas de « import kraken » (qui réussit même si le pipeline
    est cassé) : on tente d'importer les modules réellement utilisés pour la
    segmentation et la reconnaissance, ainsi que torchvision dont dépend Kraken.

    Retourne {"kraken": ..., "torch": ..., "torchvision": ..., "cuda": ...},
    chaque entrée portant au moins {"ok": bool}.

    use_cache=True renvoie le résultat mémorisé s'il existe (affichage UI). Le
    préflight OCR appelle avec use_cache=False pour obtenir la vérité au moment
    de l'exécution.
    """
    global _cached
    if use_cache and _cached is not None:
        return _cached

    result = {"kraken": None, "torch": None, "torchvision": None, "cuda": None}

    # PyTorch (requis) + CUDA (optionnel)
    try:
        import torch
        v = _pkg_version("torch") or getattr(torch, "__version__", None)
        result["torch"] = {"ok": True, "version": v}
        if torch.cuda.is_available():
            try:
                vram_gb = round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1)
            except Exception:
                vram_gb = None
            result["cuda"] = {"ok": True, "device": torch.cuda.get_device_name(0), "vram_gb": vram_gb}
        else:
            result["cuda"] = {"ok": False, "device": None, "vram_gb": None}
    except ImportError:
        result["torch"] = {"ok": False, "version": None}
        result["cuda"] = {"ok": False, "device": None, "vram_gb": None}

    # torchvision (requis par Kraken) — c'est ici que se voient les mismatch torch/torchvision
    tv_ok = False
    try:
        import torchvision  # noqa: F401
        result["torchvision"] = {"ok": True, "version": _pkg_version("torchvision"), "error": None}
        tv_ok = True
    except Exception as e:
        result["torchvision"] = {
            "ok": False,
            "version": _pkg_version("torchvision"),
            "error": str(e) or e.__class__.__name__,
        }

    # Kraken : présence du package PUIS importabilité réelle du pipeline OCR
    kraken_version = _pkg_version("kraken")
    try:
        import kraken  # noqa: F401
        kraken_version = kraken_version or getattr(kraken, "__version__", None)
    except ImportError:
        result["kraken"] = {"ok": False, "version": None,
                             "error": "Le package « kraken » n'est pas installé."}
        _cached = result
        return result

    # Le package est là : on vérifie que la segmentation et la reconnaissance s'importent
    try:
        from kraken import blla, rpred  # noqa: F401
        from kraken.lib import models  # noqa: F401
        result["kraken"] = {"ok": True, "version": kraken_version, "error": None}
    except Exception as e:
        msg = str(e) or e.__class__.__name__
        if not tv_ok:
            msg = ("Le pipeline Kraken ne peut pas démarrer à cause de torchvision "
                   f"(incompatible avec PyTorch). Détail : {msg}")
        result["kraken"] = {"ok": False, "version": kraken_version, "error": msg}

    _cached = result
    return result
