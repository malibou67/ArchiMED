"""Journal d'exploitation, un fichier par poste.

Contexte : l'exécutable est hébergé sur un NAS et lancé depuis plusieurs PC qui partagent le
même dossier `data/`. Un journal unique serait donc écrit simultanément par tous les postes.
Chaque poste écrit ici dans **son propre** fichier, `data/logs/<machine_id>/archimed.log` :
un seul écrivain par fichier (aucun entrelacement, aucune bagarre sur le renommage de
rotation), mais tous les journaux restent lisibles depuis n'importe quel poste — c'est ce qui
permet de diagnostiquer un poste sans aller physiquement le chercher.

Repli sur `%LOCALAPPDATA%\\ArchiMED\\logs\\` si `data/` est injoignable : un NAS coupé est
précisément le moment où l'on a besoin du journal.

Format : texte lisible + champs `clé=valeur` (cf. `kv`), ouvrable au Notepad depuis le NAS.
Les horodatages portent leur décalage horaire, contrairement aux `datetime.now()` naïfs du
reste du code : les fichiers de plusieurs postes sont destinés à être lus côte à côte.

**Confidentialité.** Les registres traités sont des archives hospitalières. Le journal ne doit
JAMAIS contenir de texte transcrit ni de nom de patient — uniquement des identifiants :
registre, numéro de page, nom de fichier, modèle.
"""
import logging
import multiprocessing
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import machine_identity
from services import DATA_DIR

# 5 Mo × 5 archives ≈ 25 Mo par poste. C'est toute la stratégie de nettoyage : la rotation
# borne le disque, il n'y a pas de purge par âge à faire tourner.
MAX_BYTES = 5_000_000
BACKUP_COUNT = 5

# Intervalle minimal entre deux entrées identiques émises par une boucle de fond (`log_throttled`).
THROTTLE_INTERVAL = 60.0

_configured = False
_log_dir: Optional[Path] = None
_throttle_state: Dict[str, float] = {}


class _Formatter(logging.Formatter):
    """`2026-08-03T14:22:31+02:00 INFO  ocr     message`.

    `%(asctime)s` par défaut n'expose pas le décalage horaire ; on reformate l'heure depuis
    `record.created` pour que les journaux de deux postes soient comparables.

    Les loggers d'uvicorn (`uvicorn.error`, `uvicorn.access`) sont raccourcis en `uvicorn` :
    sans cela ils débordent la colonne des noms et décalent le message d'une ligne sur deux."""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec='seconds')

    def format(self, record: logging.LogRecord) -> str:
        record.name = record.name.split('.')[0]
        return super().format(record)


def kv(**fields: Any) -> str:
    """Formate des champs en `clé=valeur`, dans l'ordre donné.

    Les valeurs vides (`None`, chaîne vide) sont omises plutôt que loguées comme `clé=None` :
    un champ non renseigné n'apprend rien. Les valeurs contenant un espace sont mises entre
    guillemets pour rester délimitables à l'œil comme à la regex."""
    parts = []
    for key, value in fields.items():
        if value is None or value == '':
            continue
        text = ('oui' if value else 'non') if isinstance(value, bool) else str(value)
        if ' ' in text or '"' in text:
            text = '"' + text.replace('"', "'") + '"'
        parts.append(f"{key}={text}")
    return ' '.join(parts)


def get_logger(name: str) -> logging.Logger:
    """Journal nommé. Noms courts et stables : `app`, `task`, `ocr`, `index`, `data`."""
    return logging.getLogger(name)


class StreamToLogger:
    """Adaptateur `sys.stdout` / `sys.stderr` → journal.

    Dans le build sans console, `sys.stdout` vaut `None` et uvicorn plante en appelant
    `isatty()` : la redirection est obligatoire, pas cosmétique. La faire passer par le
    journal (plutôt que vers un fichier séparé) capture au passage les tracebacks des threads
    et les impressions des bibliothèques tierces."""

    encoding = 'utf-8'

    def __init__(self, name: str, level: int):
        self._log = logging.getLogger(name)
        self._level = level
        self._buffer = ''

    def write(self, text: str) -> int:
        # Les tracebacks arrivent morceau par morceau : on n'émet qu'à la ligne complète,
        # sinon un seul message serait éclaté en dizaines d'entrées.
        self._buffer += text
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            if line.strip():
                self._log.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._log.log(self._level, self._buffer.strip())
        self._buffer = ''

    def isatty(self) -> bool:
        return False


def _resolve_dir() -> Optional[Path]:
    """`data/logs/<poste>/`, avec repli local si le NAS est injoignable."""
    candidates = [
        Path(DATA_DIR) / 'logs' / machine_identity.machine_id(),
        Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'ArchiMED' / 'logs',
    ]
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    return None


def setup() -> Optional[Path]:
    """Installe le journal du poste. Idempotent. Retourne le fichier écrit, `None` si aucun.

    **Ne fait rien dans un process fils.** Les workers du pool OCR réexécutent l'exécutable
    (cf. l'en-tête de `main.py`) : s'ils installaient ce handler, plusieurs process écriraient
    le même fichier et se disputeraient le renommage de rotation — fatal sous Windows/SMB.
    Leurs `get_logger()` de niveau module restent inertes (aucun handler → `lastResort` →
    stderr, déjà redirigé vers `os.devnull`)."""
    global _configured, _log_dir
    if _configured:
        return _log_dir / 'archimed.log' if _log_dir else None
    if multiprocessing.current_process().name != 'MainProcess':
        return None
    _configured = True

    # Colonne de niveau tenue en 5 caractères.
    logging.addLevelName(logging.WARNING, 'WARN')
    logging.addLevelName(logging.CRITICAL, 'CRIT')

    directory = _resolve_dir()
    if directory is None:
        return None
    _log_dir = directory
    path = directory / 'archimed.log'

    handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding='utf-8', delay=True
    )
    # Colonne des noms dimensionnée sur le plus long (`uvicorn`) : au-delà, le message se
    # décale et la lecture en colonnes est perdue.
    handler.setFormatter(_Formatter('%(asctime)s %(levelname)-5s %(name)-7s %(message)s'))

    root = logging.getLogger()
    root.setLevel(_level_from_env())
    root.addHandler(handler)
    _install_excepthooks()
    return path


def _install_excepthooks() -> None:
    """Journalise les exceptions qui ne sont rattrapées par personne.

    Sans cela une indexation tuée par un `MemoryError` — l'index tient plusieurs centaines de
    Mo en RAM — ne laissait aucune trace : le thread mourait en silence et l'index restait
    affiché « en reconstruction » sans la moindre explication dans le journal."""
    crash_log = logging.getLogger('crash')

    def _thread_hook(args) -> None:
        # Un thread arrêté par `Thread._stop()` remonte ici sans exception : rien à dire.
        if args.exc_type is SystemExit:
            return
        crash_log.critical(
            "Exception non rattrapée dans le thread « %s »",
            getattr(args.thread, 'name', '?'),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def _main_hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            _previous_main(exc_type, exc_value, exc_tb)
            return
        crash_log.critical("Exception non rattrapée",
                           exc_info=(exc_type, exc_value, exc_tb))
        _previous_main(exc_type, exc_value, exc_tb)

    _previous_main = sys.excepthook
    threading.excepthook = _thread_hook
    sys.excepthook = _main_hook


def _level_from_env() -> int:
    """Niveau lu dans `LOG_LEVEL`. Une valeur farfelue ne doit pas empêcher de démarrer."""
    raw = (os.getenv('LOG_LEVEL') or 'INFO').strip().upper()
    level = logging.getLevelName(raw)
    return level if isinstance(level, int) else logging.INFO


def log_throttled(log: logging.Logger, level: int, key: str, message: str, **info: Any) -> None:
    """Journalise en limitant à une entrée par minute pour une même `key`.

    Réservé aux boucles de fond (superviseur, drainage des commandes) qui se réveillent toutes
    les quelques secondes : une panne durable y remplirait le fichier en quelques minutes et
    ferait disparaître le contexte utile par rotation."""
    now = time.monotonic()   # insensible aux corrections d'horloge, contrairement à `now()`
    last = _throttle_state.get(key, 0.0)
    if now - last < THROTTLE_INTERVAL:
        return
    _throttle_state[key] = now
    log.log(level, message, **info)


def redirect_std_streams() -> None:
    """Envoie `sys.stdout`/`sys.stderr` vers le journal (build sans console)."""
    sys.stdout = StreamToLogger('stdout', logging.INFO)
    sys.stderr = StreamToLogger('stderr', logging.ERROR)
