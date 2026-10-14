"""Sélections de pages : le résultat d'une recherche, trié dans Excel puis réimporté.

Une recherche repart sinon toujours de tout l'index. Ses résultats s'exportent en classeur avec
une colonne « Garder » ; une fois trié, le classeur réimporté devient une sélection. Elle fige
les pages gardées — pas la requête : c'est un jeu de données établi — et une recherche suivante
peut se faire dedans (`within`), puis être exportée, triée et réimportée à son tour, en
entonnoir.

Rangées avec leur index, dans `data/indexes/<index>/selections/` : partagées par tous les postes
qui utilisent ces données, et emportées par la suppression de l'index. Deux fichiers par
sélection, pour que la liste reste légère sur le partage même quand une sélection compte des
milliers de pages : `<id>.json` (le résumé) et `<id>.pages.json` (les pages). Les pages sont
écrites d'abord : un résumé présent est une sélection complète.
"""
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app_logging import get_logger, kv
from services import IndexesService, _read_json_retry, _write_json_atomic

log = get_logger('selections')

_ID = re.compile(r'^sel_\d{14}_[0-9a-f]{6}$')
_PAGES_SUFFIX = '.pages.json'


class SelectionNotFound(LookupError):
    """Index ou sélection inconnus."""


def _sources_sig(meta: Dict[str, Any]) -> str:
    """Empreinte des sources de l'index : si elle change, les clés s0/s1/… des noms de page
    ont pu glisser, et les pages d'une sélection ne plus désigner les mêmes."""
    sources = meta.get('sources') or [{"key": None, "collection_id": meta.get('collection_id'),
                                       "model_name": meta.get('model_name')}]
    return IndexesService._sources_signature(sources)


class SelectionsService:

    @staticmethod
    def _dir(index_id: str) -> Path:
        return IndexesService.get_indexes_dir() / index_id / "selections"

    @staticmethod
    def _index_meta(index_id: str) -> Dict[str, Any]:
        meta = IndexesService.get_index(index_id)
        if not meta:
            raise SelectionNotFound(f"Index inconnu : {index_id}")
        return meta

    @staticmethod
    def _summary(index_id: str, sel_id: str) -> Optional[Dict[str, Any]]:
        if not _ID.match(sel_id or ''):
            return None
        path = SelectionsService._dir(index_id) / f"{sel_id}.json"
        return _read_json_retry(path) if path.exists() else None

    @staticmethod
    def summaries(index_id: str) -> List[Dict[str, Any]]:
        """Résumés des sélections de l'index, de la plus récente à la plus ancienne. `stale` dit
        celles enregistrées avant un changement des sources de l'index."""
        sig = _sources_sig(SelectionsService._index_meta(index_id))
        folder = SelectionsService._dir(index_id)
        if not folder.is_dir():
            return []
        out = []
        for path in folder.glob('sel_*.json'):
            if path.name.endswith(_PAGES_SUFFIX):
                continue
            summary = _read_json_retry(path)
            if summary and _ID.match(summary.get('id') or ''):
                out.append({**summary, 'stale': summary.get('sources_sig') != sig})
        out.sort(key=lambda s: s.get('created_at') or '', reverse=True)
        return out

    @staticmethod
    def get(index_id: str, sel_id: str) -> Dict[str, Any]:
        SelectionsService._index_meta(index_id)
        summary = SelectionsService._summary(index_id, sel_id)
        if summary is None:
            raise SelectionNotFound(f"Sélection inconnue : {sel_id}")
        return summary

    @staticmethod
    def pages(index_id: str, sel_id: str) -> Set[str]:
        """Pages d'une sélection, pour restreindre une recherche (`within`)."""
        SelectionsService.get(index_id, sel_id)
        data = _read_json_retry(SelectionsService._dir(index_id) / f"{sel_id}{_PAGES_SUFFIX}")
        if data is None:
            raise SelectionNotFound(f"Pages de la sélection illisibles : {sel_id}")
        return set(data.get('pages') or [])

    @staticmethod
    def create(index_id: str, name: str, pages: List[str], steps: List[Dict[str, Any]],
               source_file: Optional[str] = None) -> Dict[str, Any]:
        """Enregistre les pages gardées. `steps` est la chaîne des recherches dont elles
        viennent, depuis tout l'index (recopiée, pour rester lisible même si les sélections
        intermédiaires sont supprimées) ; la dernière étape reçoit le nombre de pages gardées."""
        meta = SelectionsService._index_meta(index_id)
        name = (name or '').strip()
        if not name:
            raise ValueError("Le nom de la sélection est vide")
        pages = list(dict.fromkeys(p for p in pages if isinstance(p, str) and p))
        if not pages:
            raise ValueError("Aucune page à enregistrer")

        steps = [dict(s) for s in steps]
        if steps:
            steps[-1]['kept'] = len(pages)
        now = datetime.now()
        sel_id = f"sel_{now:%Y%m%d%H%M%S}_{secrets.token_hex(3)}"
        summary = {
            'id': sel_id,
            'name': name,
            # À la microseconde : la liste est triée dessus, et deux enregistrements peuvent tomber
            # dans la même seconde.
            'created_at': now.isoformat(),
            'count': len(pages),
            'source_file': source_file,
            'steps': steps,
            'sources_sig': _sources_sig(meta),
        }
        folder = SelectionsService._dir(index_id)
        folder.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(folder / f"{sel_id}{_PAGES_SUFFIX}", {'pages': pages})
        _write_json_atomic(folder / f"{sel_id}.json", summary)
        log.info("Sélection enregistrée " + kv(index=index_id, id=sel_id, nom=name, pages=len(pages)))
        return {**summary, 'stale': False}

    @staticmethod
    def delete(index_id: str, sel_id: str) -> None:
        SelectionsService.get(index_id, sel_id)
        folder = SelectionsService._dir(index_id)
        # Le résumé d'abord : sans lui, la sélection n'existe plus, même si les pages restaient.
        (folder / f"{sel_id}.json").unlink(missing_ok=True)
        (folder / f"{sel_id}{_PAGES_SUFFIX}").unlink(missing_ok=True)
        log.info("Sélection supprimée " + kv(index=index_id, id=sel_id))
