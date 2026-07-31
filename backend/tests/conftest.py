"""Configuration pytest : rend les modules de `backend/` importables tels quels.

Le backend s'importe à plat (`from services import IndexesService`) parce qu'il est lancé
depuis son propre dossier ; les tests reproduisent ce chemin d'import.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
