"""Identité légère du poste pour un déploiement multi-PC.

Contexte : l'exécutable est hébergé sur un NAS et lancé depuis chaque PC. Chaque poste
exécute donc son **propre** backend, mais tous écrivent dans le **même** dossier `data/`
(partagé sur le NAS). Pour distinguer les tâches d'un poste à l'autre, chaque tâche est
estampillée avec un `machine_id`.

Le `machine_id` est dérivé du nom Windows du poste (`COMPUTERNAME`) → stable et unique sur
le LAN, sans configuration. Le libellé lisible (`machine_label`) et le nom de l'opérateur
sont **stockés localement sur chaque PC** (`%LOCALAPPDATA%\\ArchiMED\\identity.json`) et
JAMAIS dans `data/` qui est partagé entre tous les postes.
"""
import json
import os
import socket
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional

_lock = threading.Lock()


def machine_id() -> str:
    """Identifiant stable du poste = nom Windows (COMPUTERNAME), repli sur le hostname."""
    raw = os.environ.get('COMPUTERNAME') or socket.gethostname() or 'poste-inconnu'
    return raw.strip().lower()


def _identity_file() -> Path:
    """Fichier d'identité LOCAL au poste (hors NAS)."""
    base = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    return Path(base) / 'ArchiMED' / 'identity.json'


def get_identity() -> Dict[str, str]:
    """{machine_id, machine_label, operator}. Libellé/opérateur lus du fichier local
    (défauts si absent : label = machine_id, operator = '')."""
    mid = machine_id()
    label, operator = mid, ''
    f = _identity_file()
    if f.exists():
        try:
            with open(f, 'r', encoding='utf-8-sig') as fh:
                data = json.load(fh)
            label = (data.get('machine_label') or '').strip() or mid
            operator = (data.get('operator') or '').strip()
        except (OSError, json.JSONDecodeError):
            pass
    return {'machine_id': mid, 'machine_label': label, 'operator': operator}


def update_identity(machine_label: Optional[str] = None, operator: Optional[str] = None) -> Dict[str, str]:
    """Met à jour le libellé et/ou l'opérateur dans le fichier LOCAL au poste."""
    with _lock:
        current = get_identity()
        if machine_label is not None:
            current['machine_label'] = machine_label.strip() or current['machine_id']
        if operator is not None:
            current['operator'] = operator.strip()
        f = _identity_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, 'w', encoding='utf-8') as fh:
            json.dump({'machine_label': current['machine_label'], 'operator': current['operator']},
                      fh, ensure_ascii=False, indent=2)
        return current
