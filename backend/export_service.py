"""Suivi des exports ZIP des pages d'une recherche.

L'archive part en téléchargement natif : le navigateur l'écrit sur disque au fil de l'eau, sans
limite de taille, mais la page ne voit rien passer. Pour qu'elle puisse dire où en est l'export,
et distinguer un export qui avance d'un export bloqué ou d'un serveur tombé, chaque export est
suivi ici, en mémoire, sous le jeton que la page a joint à sa requête. La page interroge cet
état toutes les secondes environ, du clic jusqu'au dernier octet.

Déroulé : `start` enregistre l'export, `prepare` fait la recherche puis l'inventaire des images
(dans la route, avant la réponse : ses erreurs restent de vrais statuts HTTP), `stream` produit
le ZIP que Starlette relit morceau par morceau. `snapshot` sert la page, `request_cancel` son
bouton Annuler.
"""
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_logging import get_logger, kv
from services import IndexesService

log = get_logger('export')

TERMINAL = {'done', 'error', 'cancelled'}
# Un export terminé reste consultable une heure : la page affiche son bilan jusqu'à ce qu'on la
# ferme. Au-delà de six heures sans nouvelles, un export est oublié quel que soit son état.
KEEP_FINISHED = 3600.0
KEEP_IDLE = 6 * 3600.0
# Introuvables / illisibles renvoyés à la page (le rapport de l'archive, lui, les liste tous).
LIST_CAP = 50
# Pause avant de retenter la lecture d'une image : de quoi laisser passer un hoquet du partage.
READ_RETRY_DELAY = 0.5
REPORT_NAME = '_export.txt'


class ExportCancelled(Exception):
    """Levée dans la préparation ou le flux quand l'utilisateur a demandé l'annulation."""


class _Buffer:
    """Sortie du ZipFile : accumule ce qu'il écrit, le flux la vide à chaque morceau envoyé."""

    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data += b
        return len(b)

    def flush(self):
        pass


def _read_image(path: Path) -> Optional[bytes]:
    """Contenu d'une image, lu d'un bloc ; None si elle reste illisible à la seconde tentative.

    Lire l'image **avant** d'ouvrir son entrée ZIP évite d'y laisser un fichier vide ou tronqué :
    une fois l'en-tête local écrit, une erreur de lecture ne peut plus être défaite."""
    for attempt in (1, 2):
        try:
            with open(path, 'rb') as f:
                return f.read()
        except OSError:
            if attempt == 1:
                time.sleep(READ_RETRY_DELAY)
    return None


def _unique_arcname(seen: Dict[str, int], arc: str) -> str:
    """Nom d'entrée unique dans l'archive (`nom_1.ext`, `nom_2.ext`… pour les doublons)."""
    if arc in seen:
        seen[arc] += 1
        stem, dot, ext = arc.rpartition('.')
        return f"{stem}_{seen[arc]}{dot}{ext}" if dot else f"{arc}_{seen[arc]}"
    seen[arc] = 0
    return arc


def _fmt_size(n: int) -> str:
    """Taille lisible, à la française : « 655 Mo », « 1,2 Go »."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} Go".replace('.', ',')
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} Mo"
    return f"{round(n / 1024)} Ko"


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count <= 1 else plural}"


class PagesExportService:
    _jobs: Dict[str, Dict[str, Any]] = {}
    # Le flux avance dans les threads de Starlette, la page interroge depuis d'autres.
    _lock = threading.Lock()

    # ── Cycle de vie ─────────────────────────────────────────────────────
    @classmethod
    def start(cls, token: str, index_id: str, query: str,
              year_from: Optional[int] = None, year_to: Optional[int] = None,
              fuzzy_threshold: Optional[int] = None) -> None:
        """Enregistre un export avant tout travail : la page peut l'interroger dès le clic."""
        meta = IndexesService.get_index(index_id) or {}
        now = time.monotonic()
        job = {
            'token': token, 'index_id': index_id, 'index_name': meta.get('name') or index_id,
            'query': query, 'year_from': year_from, 'year_to': year_to,
            'fuzzy_threshold': fuzzy_threshold,
            'status': 'preparing', 'phase': None, 'current': 0, 'total': 0, 'item': None,
            'pages': None, 'registres': None,
            'files_total': 0, 'files_done': 0, 'bytes_total': 0, 'bytes_done': 0,
            'files_written': 0, 'bytes_written': 0,
            'missing': [], 'unreadable': [],
            'error': None, 'cancel_reason': None, 'cancel_requested': False,
            'created': datetime.now(),
            'started': now, 'touched': now, 'zip_started': None, 'finished': None,
        }
        with cls._lock:
            cls._purge(now)
            cls._jobs[token] = job
        log.info("Export ZIP demandé " + kv(index=index_id, requête=query, jeton=token))

    @classmethod
    def prepare(cls, token: str) -> Optional[List[tuple]]:
        """Recherche puis inventaire des images, état tenu à jour à chaque étape.

        Renvoie les fichiers `(registre, Path, taille)`, ou None si l'index est absent (l'export
        passe alors en erreur). Lève `ExportCancelled` si l'utilisateur annule entre deux étapes."""
        job = cls._job(token)
        try:
            outcome = None
            for event in IndexesService.resolve_result_zip_files_iter(
                job['index_id'], job['query'], year_from=job['year_from'],
                year_to=job['year_to'], fuzzy_threshold=job['fuzzy_threshold'],
            ):
                cls._check_cancel(token)
                if event.get('type') == 'files':
                    outcome = event
                    continue
                fields = {'phase': event['phase'], 'current': event['current'],
                          'total': event['total'], 'item': event.get('registre')}
                if event['phase'] == 'resolve':
                    fields['registres'] = event['total']
                    if 'pages' in event:
                        fields['pages'] = event['pages']
                cls._update(token, **fields)
            if outcome is None or outcome['files'] is None:
                cls._finish(token, 'error', error="Index non trouvé ou non encore généré")
                log.warning("Export ZIP impossible : index absent " + kv(jeton=token))
                return None
        except ExportCancelled:
            cls._finish(token, 'cancelled', cancel_reason='user')
            log.info("Export ZIP annulé pendant la préparation " + kv(jeton=token))
            raise
        except Exception as e:
            cls._finish(token, 'error', error=str(e) or type(e).__name__)
            log.error("Échec de la préparation de l'export ZIP " + kv(jeton=token), exc_info=True)
            raise

        files = outcome['files']
        total_bytes = sum(size for _registre, _path, size in files)
        cls._update(token, pages=outcome['pages'], registres=outcome['registres'],
                    missing=outcome['missing'], files_total=len(files), bytes_total=total_bytes,
                    item=None)
        log.info("Export ZIP prêt " + kv(
            jeton=token, pages=outcome['pages'], registres=outcome['registres'],
            images=len(files), taille=_fmt_size(total_bytes),
            introuvables=len(outcome['missing']) or None,
            préparation=f"{time.monotonic() - job['started']:.1f}s"))
        return files

    @classmethod
    def stream(cls, token: str, files: List[tuple]):
        """Le ZIP lui-même, non compressé (les scans le sont déjà), morceau par morceau :
        une image par morceau, puis le rapport `_export.txt` et le répertoire central.

        Une annulation demandée par la page lève `ExportCancelled` **dans** le flux : la
        connexion est coupée et le navigateur marque le téléchargement en échec, au lieu de
        garder une archive tronquée qu'il croirait complète. Un flux refermé avant la fin
        (`GeneratorExit`) veut dire que le navigateur a lâché le téléchargement."""
        buf = _Buffer()
        seen: Dict[str, int] = {}
        cls._update(token, status='streaming', phase='zip', current=0, total=len(files),
                    zip_started=time.monotonic())
        try:
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
                for registre, path, size in files:
                    cls._check_cancel(token)
                    arc = _unique_arcname(seen, f"{registre}/{path.name}" if registre else path.name)
                    cls._update(token, item=arc)
                    data = _read_image(path)
                    if data is None:
                        log.warning("Image illisible, absente de l'export " + kv(jeton=token, fichier=path))
                        cls._advance(token, size, unreadable=arc)
                        continue
                    zf.writestr(arc, data)
                    written = len(data)
                    del data
                    chunk = bytes(buf.data)
                    buf.data.clear()
                    yield chunk
                    # Starlette ne redemande un morceau qu'une fois le précédent confié au socket :
                    # ce compte suit donc ce qui est réellement parti vers le navigateur.
                    cls._advance(token, size, written=written)
                cls._check_cancel(token)
                cls._update(token, item=REPORT_NAME)
                zf.writestr(REPORT_NAME, cls._report(token))
            if buf.data:
                yield bytes(buf.data)
                buf.data.clear()
        except ExportCancelled:
            cls._finish(token, 'cancelled', cancel_reason='user')
            cls._log_end(token, "Export ZIP annulé par l'utilisateur")
            raise
        except GeneratorExit:
            cls._finish(token, 'cancelled', cancel_reason='client')
            cls._log_end(token, "Export ZIP interrompu côté navigateur")
            raise
        except Exception as e:
            cls._finish(token, 'error', error=str(e) or type(e).__name__)
            log.error("Échec de l'export ZIP " + kv(jeton=token), exc_info=True)
            raise
        else:
            cls._finish(token, 'done', item=None)
            cls._log_end(token, "Export ZIP terminé")

    # ── Consultation / commande depuis la page ───────────────────────────
    @classmethod
    def snapshot(cls, token: str) -> Optional[Dict[str, Any]]:
        """État d'un export pour la page, None s'il est inconnu (jamais vu, oublié, ou serveur
        redémarré entre-temps)."""
        with cls._lock:
            job = cls._jobs.get(token)
            if job is None:
                return None
            now = time.monotonic()
            end = job['finished'] or now
            return {
                'token': job['token'], 'index_id': job['index_id'], 'query': job['query'],
                'status': job['status'], 'phase': job['phase'],
                'current': job['current'], 'total': job['total'], 'item': job['item'],
                'pages': job['pages'], 'registres': job['registres'],
                'files_total': job['files_total'], 'files_done': job['files_done'],
                'bytes_total': job['bytes_total'], 'bytes_done': job['bytes_done'],
                'files_written': job['files_written'], 'bytes_written': job['bytes_written'],
                'missing': job['missing'][:LIST_CAP], 'missing_count': len(job['missing']),
                'unreadable': job['unreadable'][:LIST_CAP], 'unreadable_count': len(job['unreadable']),
                'error': job['error'], 'cancel_reason': job['cancel_reason'],
                'cancel_requested': job['cancel_requested'],
                'elapsed_s': round(end - job['started'], 1),
                # Temps écoulé depuis la dernière avancée : ce qui distingue un export lent
                # d'un export bloqué (partage qui ne répond plus sur un fichier, par exemple).
                'idle_s': 0.0 if job['finished'] else round(now - job['touched'], 1),
                'zip_elapsed_s': round(end - job['zip_started'], 1) if job['zip_started'] else 0.0,
            }

    @classmethod
    def request_cancel(cls, token: str) -> Optional[Dict[str, Any]]:
        """Demande l'annulation : elle prend effet entre deux étapes ou deux images."""
        with cls._lock:
            job = cls._jobs.get(token)
            if job is None:
                return None
            if job['status'] not in TERMINAL:
                job['cancel_requested'] = True
        log.info("Annulation de l'export ZIP demandée " + kv(jeton=token))
        return cls.snapshot(token)

    # ── Interne ──────────────────────────────────────────────────────────
    @classmethod
    def _job(cls, token: str) -> Dict[str, Any]:
        with cls._lock:
            return cls._jobs[token]

    @classmethod
    def _update(cls, token: str, **fields) -> None:
        with cls._lock:
            job = cls._jobs.get(token)
            if job is not None:
                job.update(fields)
                job['touched'] = time.monotonic()

    @classmethod
    def _advance(cls, token: str, size: int, written: Optional[int] = None,
                 unreadable: Optional[str] = None) -> None:
        """Une image traitée : envoyée (`written` octets), ou sautée car illisible. La barre
        avance des tailles inventoriées dans les deux cas, pour finir exactement à 100 %."""
        with cls._lock:
            job = cls._jobs.get(token)
            if job is None:
                return
            job['files_done'] += 1
            job['bytes_done'] += size
            job['current'] = job['files_done']
            if written is not None:
                job['files_written'] += 1
                job['bytes_written'] += written
            if unreadable is not None:
                job['unreadable'].append(unreadable)
            job['touched'] = time.monotonic()

    @classmethod
    def _check_cancel(cls, token: str) -> None:
        with cls._lock:
            job = cls._jobs.get(token)
            if job is not None and job['cancel_requested']:
                raise ExportCancelled()

    @classmethod
    def _finish(cls, token: str, status: str, **fields) -> None:
        with cls._lock:
            job = cls._jobs.get(token)
            if job is None or job['status'] in TERMINAL:
                return
            job.update(fields)
            job['status'] = status
            job['finished'] = job['touched'] = time.monotonic()

    @classmethod
    def _purge(cls, now: float) -> None:
        """Oublie les exports terminés depuis longtemps (appelé sous le verrou)."""
        stale = [t for t, job in cls._jobs.items()
                 if (job['finished'] is not None and now - job['finished'] > KEEP_FINISHED)
                 or now - job['touched'] > KEEP_IDLE]
        for t in stale:
            del cls._jobs[t]

    @classmethod
    def _log_end(cls, token: str, message: str) -> None:
        snap = cls.snapshot(token)
        if snap is None:
            return
        log.info(message + " " + kv(
            jeton=token, images=f"{snap['files_written']}/{snap['files_total']}",
            taille=_fmt_size(snap['bytes_written']), durée=f"{snap['elapsed_s']}s",
            illisibles=snap['unreadable_count'] or None))

    @classmethod
    def _report(cls, token: str) -> bytes:
        """`_export.txt` : de quoi comprendre l'archive une fois partagée, et savoir ce qui y
        manque. En français, comme les en-têtes de l'export CSV ; UTF-8 avec BOM et fins de
        ligne CRLF, pour que le Bloc-notes l'ouvre correctement."""
        with cls._lock:
            job = dict(cls._jobs[token])
            job['missing'] = list(job['missing'])
            job['unreadable'] = list(job['unreadable'])
        index = job['index_name']
        if index != job['index_id']:
            index = f"{index} ({job['index_id']})"
        lines = [f"Export ArchiMED du {job['created']:%d/%m/%Y à %H:%M}", "",
                 f"Index : {index}", f"Recherche : « {job['query']} »"]
        if job['year_from'] is not None or job['year_to'] is not None:
            lines.append(f"Période : {job['year_from'] or '…'} – {job['year_to'] or '…'}")
        if job['fuzzy_threshold'] is not None and job['fuzzy_threshold'] < 100:
            lines.append(f"Ressemblance : ≥ {job['fuzzy_threshold']} %")
        lines += [
            "",
            f"{_plural(job['pages'] or 0, 'page trouvée', 'pages trouvées')} dans "
            f"{_plural(job['registres'] or 0, 'registre', 'registres')}.",
            f"{_plural(job['files_written'], 'image exportée', 'images exportées')} "
            f"(pages bis comprises), {_fmt_size(job['bytes_written'])}.",
            "Les images sont rangées par registre : <registre>/<image>.",
        ]
        for title, names in (("Images introuvables sur le disque", job['missing']),
                             ("Images illisibles au moment de l'export", job['unreadable'])):
            lines += ["", f"{title} ({len(names)})" + (" :" if names else ".")]
            lines += [f"  {name}" for name in names]
        return ('﻿' + '\r\n'.join(lines) + '\r\n').encode('utf-8')
