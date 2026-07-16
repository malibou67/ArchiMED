from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from system_checks import check_requirements as run_checks
from services import check_data_storage
import machine_identity

router = APIRouter()


@router.get("/requirements")
def check_requirements():
    """Vérifie que l'environnement peut réellement exécuter l'OCR Kraken.

    Handler synchrone : FastAPI l'exécute dans un threadpool, ce qui évite de
    bloquer la boucle d'événements (et donc les requêtes parallèles) pendant les
    imports lourds torch/kraken. `use_cache=True` réutilise le résultat mémorisé.
    """
    return run_checks(use_cache=True)


@router.get("/storage")
def get_storage():
    """Santé du dossier de données (existence, écriture, sous-dossiers cruciaux).

    Sert à distinguer une installation cassée (dossier data introuvable → écran
    bloquant côté UI) d'un simple état vide. Léger (uniquement os.path)."""
    return check_data_storage()


class IdentityUpdate(BaseModel):
    machine_label: Optional[str] = None
    operator: Optional[str] = None


@router.get("/identity")
def get_identity():
    """Identité de CE poste (machine_id auto + libellé/opérateur locaux)."""
    return machine_identity.get_identity()


@router.put("/identity")
def update_identity(data: IdentityUpdate):
    """Met à jour le libellé / l'opérateur de CE poste (stockés localement, hors NAS)."""
    return machine_identity.update_identity(data.machine_label, data.operator)
