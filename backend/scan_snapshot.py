"""Instantané du parcours disque, partagé entre l'analyse et la synchronisation.

Les deux lisaient chacune les mêmes choses sur le NAS : la liste des images de chaque
registre, les XML par modèle, les metadata.json. Sur un partage SMB chaque `iterdir`/`stat`
est un aller-retour réseau, et c'est là que passe tout le temps — pas en CPU. Un seul
parcours dépose donc ici ses **faits bruts**, que les deux consomment ensuite.

Ce qui est stocké est volontairement brut (noms de fichiers, comptages, contenu JSON) et
jamais interprété. C'est ce qui rend le partage sûr : l'analyse re-détecte toujours le motif
de pagination depuis les noms de fichiers, la synchronisation réutilise le motif déjà
persisté. Chacune garde sa logique, appliquée aux mêmes faits — le résultat est inchangé.

Le fichier est **local au poste** (`%LOCALAPPDATA%/ArchiMED/cache/`), jamais sous `DATA_DIR` :
`data/` est partagé entre les postes, l'instantané d'une machine y serait consommé par une
autre — et l'écrire sur le NAS annulerait justement le gain recherché.

Un instantané n'est jamais nécessaire à la correction. `load()` refuse tout ce qui est
douteux (jeton inconnu, autre `DATA_DIR`, trop vieux) en rendant `None` ; l'appelant repart
alors sur un parcours disque complet, c'est-à-dire le comportement d'avant ce cache.

Ce module ne dépend de rien du projet : `data_dir` lui est passé par l'appelant plutôt
qu'importé de `services`, qui l'importe déjà (l'inverse créerait un cycle).
"""

import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger('data')

# Au-delà, on préfère reparcourir le disque : l'instantané ne sert qu'à enchaîner une
# synchronisation sur l'analyse qui vient d'avoir lieu, pas à mettre le NAS en cache.
TTL_SECONDS = 900

# Dernier instantané écrit ou lu. Évite de reparser plusieurs Mo de JSON dans le cas
# courant — analyse puis synchronisation dans le même process. Le fichier prend le relais
# après un redémarrage.
_MEM: Optional[Dict[str, Any]] = None


def new_token() -> str:
    """Jeton d'un instantané. Rendu au client avec le rapport, qui le représente ensuite
    pour dire « synchronise à partir de ce que l'analyse vient de voir »."""
    return uuid.uuid4().hex


def _cache_dir() -> Optional[Path]:
    base = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'ArchiMED' / 'cache'
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        return None


def snapshot_file() -> Optional[Path]:
    """Fichier d'instantané du poste, `None` si le dossier local est inaccessible."""
    cache_dir = _cache_dir()
    return (cache_dir / 'scan-snapshot.json') if cache_dir else None


def save(snap: Dict[str, Any]) -> Optional[Path]:
    """Persiste l'instantané (écriture atomique). Un échec n'est pas une erreur : le cache
    est facultatif, on garde au moins la copie en mémoire."""
    global _MEM
    _MEM = snap

    path = snapshot_file()
    if path is None:
        return None
    tmp = path.with_name(path.name + '.tmp')
    try:
        # Pas d'`indent` : ce fichier n'est pas fait pour être lu par un humain, et
        # l'indentation doublerait sa taille pour des centaines de milliers de noms.
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False)
        os.replace(tmp, path)
        return path
    except OSError as e:
        log.warning(f"Instantané non enregistré err={e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _read_file() -> Optional[Dict[str, Any]]:
    global _MEM
    path = snapshot_file()
    if path is None:
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            snap = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Instantané illisible err={e}")
        return None
    _MEM = snap
    return snap


def load(token: Optional[str], data_dir: str) -> Optional[Dict[str, Any]]:
    """Instantané correspondant à `token`, ou `None` s'il n'est pas utilisable tel quel.

    Refuse un jeton absent ou inconnu, un instantané pris sur un autre `data_dir` et tout
    ce qui dépasse le TTL. L'appelant doit traiter `None` comme « parcourir le disque »."""
    if not token:
        return None

    snap = _MEM
    if snap is None or snap.get('token') != token:
        snap = _read_file()
    if snap is None:
        return None

    if snap.get('token') != token:
        return None
    if str(snap.get('data_dir')) != str(data_dir):
        return None
    age = time.time() - float(snap.get('created_at') or 0)
    if age > TTL_SECONDS:
        log.info(f"Instantané périmé âge={age:.0f}s ttl={TTL_SECONDS}s")
        return None
    return snap


def update_collection(snap: Dict[str, Any], folder_name: str, entry: Dict[str, Any]) -> None:
    """Réaligne l'instantané sur ce qu'une synchronisation vient d'écrire, pour que le
    rapport puisse être rejoué ensuite sans retoucher au NAS."""
    snap.setdefault('collections', {})[folder_name] = entry
    save(snap)
