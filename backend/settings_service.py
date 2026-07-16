"""Réglages applicatifs persistés (data/settings.json), modifiables depuis l'UI.

Une valeur absente / `null` signifie « valeur par défaut » (variable d'environnement ou
défaut adaptatif calculé côté OcrService). On ne stocke que les clés connues.
"""
import json
import threading
from pathlib import Path
from typing import Dict, Any

from services import DATA_DIR

ALLOWED_KEYS = {'ocr_workers', 'ocr_threads_per_worker', 'ocr_mixed_precision', 'ocr_pool_min_pages'}


class SettingsService:
    _lock = threading.Lock()

    @staticmethod
    def _file() -> Path:
        return Path(DATA_DIR) / 'settings.json'

    @staticmethod
    def get() -> Dict[str, Any]:
        """Réglages stockés (uniquement les clés connues). {} si rien n'est défini."""
        f = SettingsService._file()
        if not f.exists():
            return {}
        try:
            with open(f, 'r', encoding='utf-8-sig') as fh:
                data = json.load(fh)
            return {k: v for k, v in data.items() if k in ALLOWED_KEYS and v is not None}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def update(partial: Dict[str, Any]) -> Dict[str, Any]:
        """Fusionne des réglages. `null` sur une clé → on la retire (retour au défaut)."""
        with SettingsService._lock:
            current = SettingsService.get()
            for k, v in partial.items():
                if k not in ALLOWED_KEYS:
                    continue
                if v is None:
                    current.pop(k, None)
                else:
                    current[k] = v
            f = SettingsService._file()
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, 'w', encoding='utf-8') as fh:
                json.dump(current, fh, ensure_ascii=False, indent=2)
            return current
