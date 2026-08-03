"""Moteur de tâches générique (OCR, indexation, …).

Toute tâche, quel que soit son `type`, est gérée de la même façon : file d'attente,
exécution, progression, persistance (`data/tasks/<id>.json`), reprise au démarrage.
Chaque type a sa propre **lane** (file séquentielle indépendante) → un OCR et une
indexation peuvent tourner en parallèle, mais deux OCR s'enchaînent. Le travail spécifique
à un type est délégué à un **runner** enregistré via `register(type, runner)`.

**Multi-PC (NAS partagé).** Plusieurs postes lancent le même exécutable depuis le NAS et
partagent le même dossier `data/`. Chaque tâche est estampillée avec un `machine_id`
(cf. `machine_identity`) :
  - **appartenance** : un poste ne reprend / ne contrôle que ses propres tâches en cours ;
  - **vue unifiée** : `list_tasks`/`summary` relisent le disque pour voir les tâches des
    autres postes (`_merged_tasks`) ;
  - **conflits** : deux postes ne peuvent pas produire la même sortie (OCR sur le même
    (collection, registre, modèle) ou index sur le même (collection, modèle)) → blocage à
    la création (`find_conflict`) + verrou atomique à l'exécution, auto-réparant via un
    `heartbeat` (un poste coupé ne bloque pas durablement les autres) ;
  - **contrôle à distance** : un poste ne peut pas écrire dans le fichier d'une tâche `running`
    d'un autre poste (le propriétaire l'écrase à son heartbeat suivant). Il dépose donc une
    commande dans `data/tasks/control/<id>.json`, que le propriétaire applique (cf. `_supervisor`).

Un **thread superviseur** unique par process (`_start_supervisor`) rafraîchit le heartbeat des
tâches en cours de ce poste — indépendamment de la granularité du runner, qui peut mettre plusieurs
minutes entre deux pages — et draine la boîte aux lettres de commandes.
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple

from services import DATA_DIR
import machine_identity

TERMINAL = {'done', 'error', 'cancelled', 'interrupted'}
HISTORY_CAP = 100
# Au-delà de ce délai sans heartbeat, une tâche `running` est considérée morte (process
# tué / NAS coupé) : son verrou devient récupérable par un autre poste.
HEARTBEAT_STALE = 60.0
# Période de réveil du superviseur (heartbeat + commandes distantes). Très en deçà de
# HEARTBEAT_STALE pour garder une marge confortable sur un NAS lent.
SUPERVISOR_INTERVAL = 5.0

# Commandes acceptées dans la boîte aux lettres inter-postes.
CONTROL_ACTIONS = ('cancel', 'pause', 'resume')

# Résultats de `cancel`/`pause`/`resume` : appliqué ici, transmis au poste propriétaire,
# ou impossible dans l'état courant.
APPLIED = 'applied'
REQUESTED = 'requested'
REFUSED = 'refused'

# Clés volatiles / lourdes exclues des réponses API.
_HIDDEN = ('pages', 'page_states', 'index_registres', 'payload', 'cancel', 'last_write', 'heartbeat')


class TaskConflict(Exception):
    """Levée par `enqueue` quand le scope demandé est déjà occupé par un autre poste."""
    def __init__(self, scope: str, machine_label: str):
        self.scope = scope
        self.machine_label = machine_label
        super().__init__(f"« {scope} » déjà en cours sur le poste « {machine_label} ».")


class TaskService:
    _tasks: Dict[str, Dict[str, Any]] = {}
    _queue: Dict[str, List[str]] = {}        # type -> ids en attente (ordre)
    _active: Dict[str, Optional[str]] = {}   # type -> id en cours (ou None)
    _lock = threading.RLock()
    _loaded = False

    _runners: Dict[str, Callable[[Dict[str, Any]], None]] = {}
    _cancel_hooks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

    # Verrous de scope détenus par ce process, par task_id (non persistés).
    _held_locks: Dict[str, List[Path]] = {}

    # Sérialise les écritures de fichiers de tâche (thread runner + superviseur).
    _io_lock = threading.Lock()
    _supervisor_started = False

    # Cache court de la lecture disque (vue fusionnée) pour ne pas marteler le NAS.
    _merged_cache: Optional[Dict[str, Dict[str, Any]]] = None
    _merged_cache_ts: float = 0.0
    _MERGED_TTL = 1.0

    # ── Enregistrement des runners ────────────────────────────────────
    @staticmethod
    def register(task_type: str, runner: Callable[[Dict[str, Any]], None],
                 on_cancel: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        TaskService._runners[task_type] = runner
        if on_cancel is not None:
            TaskService._cancel_hooks[task_type] = on_cancel

    # ── Persistance ───────────────────────────────────────────────────
    @staticmethod
    def _dir() -> Path:
        d = Path(DATA_DIR) / "tasks"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _file(task_id: str) -> Path:
        return TaskService._dir() / f"{task_id}.json"

    @staticmethod
    def _save(task: Dict[str, Any]) -> None:
        """Écriture **atomique** (tmp + os.replace) du fichier de tâche.

        Le fichier est relu en boucle par les autres postes (`_merged_tasks`, `_lock_is_stale`) :
        une écriture en place exposerait un JSON tronqué, et un `JSONDecodeError` fait passer un
        verrou bien vivant pour obsolète — donc volable. Même motif que
        `IndexesService._save_index_meta`. Le tmp est nommé par thread pour que le superviseur et
        le runner ne se marchent pas dessus."""
        data = {k: v for k, v in task.items() if k not in ('cancel', 'last_write')}
        path = TaskService._file(task['id'])
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with TaskService._io_lock:
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, path)
            except OSError:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _save_throttled(task: Dict[str, Any]) -> None:
        """Écriture limitée à ~1/s pendant l'exécution (épargne le disque). Rafraîchit le
        `heartbeat` : preuve de vie pour les autres postes."""
        now = time.time()
        if now - task.get('last_write', 0) >= 1.0:
            task['last_write'] = now
            task['heartbeat'] = datetime.now().isoformat()
            TaskService._save(task)

    @staticmethod
    def _load_disk(task_id: str) -> Optional[Dict[str, Any]]:
        """Relit l'état d'une tâche depuis le disque (peut appartenir à un autre poste)."""
        f = TaskService._file(task_id)
        if not f.exists():
            return None
        try:
            with open(f, 'r', encoding='utf-8-sig') as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    # ── Appartenance / vivacité (multi-PC) ────────────────────────────
    @staticmethod
    def _is_owned(task: Dict[str, Any]) -> bool:
        """Vrai si la tâche a été créée sur ce poste. Les tâches héritées (sans `machine_id`,
        antérieures au multi-PC) sont considérées locales pour rester contrôlables."""
        mid = task.get('machine_id')
        return (mid is None) or (mid == machine_identity.machine_id())

    @staticmethod
    def _is_stale_running(task: Dict[str, Any]) -> bool:
        """Vrai si la tâche est `running` mais que son heartbeat est périmé → process mort."""
        if task.get('status') != 'running':
            return False
        hb = task.get('heartbeat') or task.get('started_at')
        if not hb:
            return False  # pas d'info → prudence : on ne la déclare pas morte
        try:
            dt = datetime.fromisoformat(hb)
        except ValueError:
            return False
        return (datetime.now() - dt).total_seconds() >= HEARTBEAT_STALE

    @staticmethod
    def _merged_tasks() -> Dict[str, Dict[str, Any]]:
        """Vue unifiée tous postes : état disque (frais pour les autres postes) + tâches en
        mémoire de CE poste (plus fraîches que le disque throttlé). Cache 1 s."""
        TaskService._ensure_loaded()
        now = time.time()
        if TaskService._merged_cache is not None and now - TaskService._merged_cache_ts < TaskService._MERGED_TTL:
            base = dict(TaskService._merged_cache)
        else:
            base = {}
            for f in TaskService._dir().glob("*.json"):
                try:
                    with open(f, 'r', encoding='utf-8-sig') as fh:
                        t = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                tid = t.get('id')
                if tid:
                    base[tid] = t
            TaskService._merged_cache = dict(base)
            TaskService._merged_cache_ts = now
        with TaskService._lock:
            for tid, t in TaskService._tasks.items():
                if TaskService._is_owned(t):
                    base[tid] = t
        return base

    @staticmethod
    def _invalidate_merged() -> None:
        TaskService._merged_cache = None

    # ── Projection API ────────────────────────────────────────────────
    @staticmethod
    def _public(task: Dict[str, Any], *, full: bool = False) -> Dict[str, Any]:
        out = {k: v for k, v in task.items() if k not in _HIDDEN}
        out['owned'] = TaskService._is_owned(task)
        if not full:
            errs = task.get('errors') or []
            out['errors'] = errs[:5]
            out['errors_truncated'] = max(0, len(errs) - 5)
        return out

    # ── Accès ─────────────────────────────────────────────────────────
    @staticmethod
    def get(task_id: str) -> Optional[Dict[str, Any]]:
        """Tâche par id : version mémoire si on la possède, sinon état disque (autre poste)."""
        TaskService._ensure_loaded()
        t = TaskService._tasks.get(task_id)
        if t is not None and TaskService._is_owned(t):
            return t
        disk = TaskService._load_disk(task_id)
        return disk if disk is not None else t

    @staticmethod
    def list_tasks() -> List[Dict[str, Any]]:
        """En cours (tous types/postes), puis en attente, puis terminés (récents d'abord)."""
        merged = list(TaskService._merged_tasks().values())
        running = [t for t in merged if t.get('status') == 'running']
        queued = [t for t in merged if t.get('status') == 'queued']
        paused = [t for t in merged if t.get('status') == 'paused']
        finished = [t for t in merged if t.get('status') in TERMINAL]
        running.sort(key=lambda t: t.get('started_at') or '')
        queued.sort(key=lambda t: t.get('created_at') or '')
        paused.sort(key=lambda t: t.get('created_at') or '')
        finished.sort(key=lambda t: t.get('finished_at') or '', reverse=True)
        return [TaskService._public(t) for t in running + queued + paused + finished]

    @staticmethod
    def summary() -> Dict[str, Any]:
        """Résumé léger (tous postes) : tâches en cours + en pause + interrompues + nb en attente."""
        merged = list(TaskService._merged_tasks().values())
        running = [TaskService._public(t) for t in merged if t.get('status') == 'running']
        paused = [TaskService._public(t) for t in merged if t.get('status') == 'paused']
        interrupted = [TaskService._public(t) for t in merged if t.get('status') == 'interrupted']
        queued_tasks = [TaskService._public(t) for t in merged if t.get('status') == 'queued']
        # has_activity ignore les tâches en pause/interrompues : on ne relance pas le polling pour elles.
        return {'running': running, 'paused': paused, 'interrupted': interrupted,
                'queued': len(queued_tasks), 'queued_tasks': queued_tasks,
                'has_activity': bool(running or queued_tasks)}

    # ── Conflits de scope (multi-PC) ──────────────────────────────────
    @staticmethod
    def _scope_keys(task: Dict[str, Any]) -> List[str]:
        """Clés de scope d'une tâche (unité de conflit). OCR = (collection, registre, modèle) ;
        index = (collection, modèle)."""
        ttype = task.get('type')
        if ttype == 'ocr':
            model = task.get('ocr_model') or ''
            scopes = task.get('scopes')
            if scopes:
                return sorted({f"ocr__{c}__{r}__{model}" for c, r in scopes})
            # Repli pour les tâches enfilées avant l'ajout de `scopes` (JSON encore présents sur
            # le partage, ou poste pas à jour) : croiser `collections` × `registres`. Sur-approxime
            # (verrouille des couples non traités), mais ne laisse jamais passer un vrai conflit.
            cols = task.get('collections') or []
            regs = task.get('registres') or []
            return sorted({f"ocr__{c}__{r}__{model}" for c in cols for r in regs})
        if ttype == 'index':
            # L'unité de conflit d'un index est sa sortie = son id (multi-sources).
            iid = task.get('index_id')
            if iid:
                return [f"index__{iid}"]
            # Legacy (anciennes tâches mono-source sans index_id).
            return [f"index__{task.get('collection_id')}__{task.get('model_name')}"]
        return []

    @staticmethod
    def _human_scope(key: str) -> str:
        parts = key.split('__')
        if parts[0] == 'ocr' and len(parts) >= 4:
            return f"{parts[1]} / {parts[2]} (modèle {parts[3]})"
        if parts[0] == 'index' and len(parts) >= 3:
            return f"index {parts[1]} (modèle {parts[2]})"
        if parts[0] == 'index' and len(parts) == 2:
            return f"index {parts[1]}"
        return key

    @staticmethod
    def active_scopes(task_type: str) -> Dict[str, str]:
        """{scope_key: machine_label} des scopes occupés par les tâches non terminales et
        **vivantes** de ce type, tous postes confondus."""
        out: Dict[str, str] = {}
        for t in TaskService._merged_tasks().values():
            if t.get('type') != task_type:
                continue
            st = t.get('status')
            if st in TERMINAL:
                continue
            if st == 'running' and TaskService._is_stale_running(t):
                continue  # morte → n'occupe plus son scope
            label = t.get('machine_label') or t.get('machine_id') or '?'
            for key in TaskService._scope_keys(t):
                out.setdefault(key, label)
        return out

    @staticmethod
    def find_conflict(task_type: str, fields: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """(scope_lisible, machine_label) si un scope demandé est déjà occupé, sinon None."""
        active = TaskService.active_scopes(task_type)
        pseudo = {'type': task_type, **fields}
        for key in TaskService._scope_keys(pseudo):
            if key in active:
                return (TaskService._human_scope(key), active[key])
        return None

    # ── Verrous de scope (filet anti-course à l'exécution) ────────────
    @staticmethod
    def _locks_dir() -> Path:
        d = Path(DATA_DIR) / "locks"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _lock_path(key: str) -> Path:
        return TaskService._locks_dir() / (re.sub(r'[^A-Za-z0-9_.-]', '_', key) + '.lock')

    @staticmethod
    def _try_create_lock(path: Path, task_id: str) -> bool:
        try:
            with open(path, 'x', encoding='utf-8') as f:
                json.dump({'task_id': task_id, 'machine_id': machine_identity.machine_id()}, f)
            return True
        except FileExistsError:
            return False
        except OSError:
            return False

    @staticmethod
    def _lock_is_stale(path: Path) -> bool:
        """Un verrou est obsolète si sa tâche est terminée, absente ou morte (heartbeat périmé)."""
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                info = json.load(f)
        except (OSError, json.JSONDecodeError):
            return True
        tid = info.get('task_id')
        if not tid:
            return True
        disk = TaskService._load_disk(tid)
        if disk is None:
            return True
        st = disk.get('status')
        if st in TERMINAL:
            return True
        return st == 'running' and TaskService._is_stale_running(disk)

    @staticmethod
    def _claim_locks(task: Dict[str, Any]) -> Optional[str]:
        """Réserve atomiquement les verrous de scope. Retourne la clé en conflit (détenteur
        vivant) si échec, sinon None (récupère au passage les verrous obsolètes)."""
        held: List[Path] = []
        for key in TaskService._scope_keys(task):
            path = TaskService._lock_path(key)
            if not TaskService._try_create_lock(path, task['id']):
                if TaskService._lock_is_stale(path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    if not TaskService._try_create_lock(path, task['id']):
                        TaskService._free_paths(held)
                        return key
                else:
                    TaskService._free_paths(held)
                    return key
            held.append(path)
        TaskService._held_locks[task['id']] = held
        return None

    @staticmethod
    def _free_paths(paths: List[Path]) -> None:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _release_locks(task: Dict[str, Any]) -> None:
        """Libère les verrous de la tâche. Utilise les chemins détenus en mémoire ; en repli
        (reprise/redémarrage), ne supprime que les verrous estampillés à cette tâche."""
        held = TaskService._held_locks.pop(task.get('id'), None)
        if held is not None:
            TaskService._free_paths(held)
            return
        for key in TaskService._scope_keys(task):
            path = TaskService._lock_path(key)
            try:
                with open(path, 'r', encoding='utf-8-sig') as f:
                    info = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if info.get('task_id') == task.get('id'):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    # ── Boîte aux lettres de commandes (contrôle inter-postes) ────────
    @staticmethod
    def _control_dir() -> Path:
        d = TaskService._dir() / "control"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _control_file(task_id: str) -> Path:
        return TaskService._control_dir() / f"{task_id}.json"

    @staticmethod
    def _request_control(task_id: str, action: str) -> bool:
        """Dépose une commande à destination du poste propriétaire d'une tâche.
        On n'écrit **jamais** dans le fichier d'une tâche en cours d'un autre poste : il est
        réécrit chaque seconde par son propriétaire, qui écraserait notre modification."""
        if action not in CONTROL_ACTIONS:
            return False
        ident = machine_identity.get_identity()
        payload = {'action': action, 'from': ident['machine_label'],
                   'from_id': ident['machine_id'], 'at': datetime.now().isoformat()}
        path = TaskService._control_file(task_id)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _drain_control() -> None:
        """Applique les commandes qui nous sont destinées, puis retire leur marqueur.

        Couvre tous les statuts : `running` (cancel/pause coopératifs via les drapeaux lus par le
        runner) mais aussi `queued`/`paused`/`interrupted`, qui n'ont pas de thread d'exécution."""
        for f in TaskService._control_dir().glob("*.json"):
            task_id = f.stem
            try:
                with open(f, 'r', encoding='utf-8-sig') as fh:
                    cmd = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            action = cmd.get('action')
            with TaskService._lock:
                task = TaskService._tasks.get(task_id)
                mine = task is not None and TaskService._is_owned(task)
            # Marqueur sans destinataire ici (autre poste, tâche supprimée) : on le laisse à
            # son propriétaire. Il sera purgé avec la tâche (`_unlink_task`).
            if not mine:
                continue
            # `cancel`/`pause`/`resume` valident eux-mêmes le statut : une commande devenue
            # caduque (tâche déjà terminée) est simplement sans effet.
            try:
                if action == 'cancel':
                    TaskService.cancel(task_id)
                elif action == 'pause':
                    TaskService.pause(task_id)
                elif action == 'resume':
                    TaskService.resume(task_id)
            except Exception:
                pass
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    # ── Superviseur (heartbeat + commandes distantes) ─────────────────
    @staticmethod
    def _supervisor() -> None:
        """Boucle de fond : preuve de vie des tâches en cours de CE poste + commandes reçues.

        Le heartbeat ne peut pas dépendre de `_save_throttled` : celui-ci n'est appelé qu'entre
        deux pages OCR (parfois > 60 s sur CPU) et jamais pendant le préflight (import torch,
        chargement des modèles depuis le NAS). Sans ce thread, les autres postes déclarent la
        tâche morte, libèrent son scope et volent son verrou → même OCR lancé deux fois."""
        while True:
            time.sleep(SUPERVISOR_INTERVAL)
            try:
                with TaskService._lock:
                    running = [t for t in TaskService._tasks.values()
                               if t.get('status') == 'running' and TaskService._is_owned(t)]
                for task in running:
                    task['heartbeat'] = datetime.now().isoformat()
                    task['last_write'] = time.time()
                    TaskService._save(task)
                if running:
                    TaskService._invalidate_merged()
                TaskService._drain_control()
            except Exception:
                pass  # un superviseur ne doit jamais mourir

    @staticmethod
    def _start_supervisor() -> None:
        with TaskService._lock:
            if TaskService._supervisor_started:
                return
            TaskService._supervisor_started = True
        threading.Thread(target=TaskService._supervisor, daemon=True).start()

    # ── Cycle de vie ──────────────────────────────────────────────────
    @staticmethod
    def enqueue(task_type: str, label: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Crée une tâche `queued` et démarre la lane de ce type si elle est libre.
        Lève `TaskConflict` si le scope demandé est déjà occupé par un autre poste."""
        TaskService._ensure_loaded()
        fields = fields or {}
        conflict = TaskService.find_conflict(task_type, fields)
        if conflict:
            raise TaskConflict(conflict[0], conflict[1])
        ident = machine_identity.get_identity()
        task_id = uuid.uuid4().hex[:12]
        task: Dict[str, Any] = {
            'id': task_id,
            'type': task_type,
            'status': 'queued',
            'label': label,
            'total': 0,
            'processed': 0,
            'failed': 0,
            'current': None,
            'created_at': datetime.now().isoformat(),
            'started_at': None,
            'finished_at': None,
            'error': None,
            'errors': [],
            'cancel': False,
            'machine_id': ident['machine_id'],
            'machine_label': ident['machine_label'],
            'operator': ident['operator'],
        }
        task.update(fields)
        with TaskService._lock:
            TaskService._tasks[task_id] = task
            TaskService._queue.setdefault(task_type, []).append(task_id)
            TaskService._save(task)
            TaskService._invalidate_merged()
            TaskService._prune_history()
        TaskService._dispatch(task_type)
        return TaskService._public(task, full=True)

    @staticmethod
    def _dispatch(task_type: str) -> None:
        """Démarre la prochaine tâche en attente de ce type si la lane est libre. Relit le
        disque avant de lancer : une tâche annulée/supprimée à distance (autre poste) est
        ignorée."""
        with TaskService._lock:
            if TaskService._active.get(task_type) is not None:
                return
            q = TaskService._queue.get(task_type, [])
            tid = None
            while q:
                cand = q[0]
                t = TaskService._tasks.get(cand)
                if not t or t['status'] != 'queued':
                    q.pop(0)
                    continue
                disk = TaskService._load_disk(cand)
                if disk is None:
                    q.pop(0)
                    TaskService._tasks.pop(cand, None)   # supprimée à distance
                    continue
                if disk.get('status') != 'queued':
                    q.pop(0)
                    t['status'] = disk.get('status')     # annulée à distance
                    continue
                tid = q.pop(0)
                break
            if tid is None:
                return
            t = TaskService._tasks[tid]
            TaskService._active[task_type] = tid
            t['status'] = 'running'
            t['started_at'] = datetime.now().isoformat()
            t['heartbeat'] = datetime.now().isoformat()
            TaskService._save(t)
            TaskService._invalidate_merged()

        threading.Thread(target=TaskService._execute, args=(tid,), daemon=True).start()

    @staticmethod
    def _execute(task_id: str) -> None:
        task = TaskService._tasks[task_id]
        ttype = task['type']
        try:
            # Filet anti-course : si un autre poste a démarré le même scope entre la création
            # et ici, on échoue proprement au lieu de corrompre la sortie partagée.
            conflict = TaskService._claim_locks(task)
            if conflict is not None:
                raise RuntimeError(f"« {TaskService._human_scope(conflict)} » verrouillé par un autre poste.")
            runner = TaskService._runners.get(ttype)
            if runner is None:
                raise RuntimeError(f"Aucun runner enregistré pour le type « {ttype} »")
            runner(task)
            if task['status'] == 'running':
                task['status'] = 'done'
        except Exception as e:
            task['status'] = 'error'
            task['error'] = str(e)
        finally:
            TaskService._release_locks(task)
            task['pause'] = False
            if task['status'] in TERMINAL:
                task['finished_at'] = datetime.now().isoformat()
            task['current'] = None
            TaskService._save(task)
            with TaskService._lock:
                TaskService._active[ttype] = None
                TaskService._invalidate_merged()
            TaskService._dispatch(ttype)

    @staticmethod
    def cancel(task_id: str) -> str:
        """APPLIED si l'annulation est prise en charge ici, REQUESTED si elle est transmise au
        poste propriétaire, REFUSED si l'état ne le permet pas."""
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] == 'running':
                    task['cancel'] = True          # le runner s'arrêtera proprement
                    return APPLIED
                # 'interrupted' est terminal mais reste annulable : on le finalise comme une tâche
                # en file/pause (retrait de file no-op + nettoyage index partiel via le hook).
                if task['status'] in ('queued', 'paused', 'interrupted'):
                    q = TaskService._queue.get(task['type'])
                    if q and task_id in q:
                        q.remove(task_id)
                    TaskService._finalize_cancel(task)
                    return APPLIED
                return REFUSED

        # Tâche d'un autre poste.
        disk = TaskService._load_disk(task_id)
        if not disk or disk.get('status') in ('done', 'error', 'cancelled'):
            return REFUSED
        # Le propriétaire doit appliquer l'annulation lui-même : lui seul peut arrêter son runner,
        # et sa copie mémoire ferait autorité sur ce qu'on écrirait ici.
        TaskService._request_control(task_id, 'cancel')
        if disk.get('status') == 'running':
            return REQUESTED
        # Non active (queued / paused / interrupted) : on finalise aussi directement, pour qu'une
        # orpheline d'un poste éteint — qui ne lira jamais la commande — soit tout de même nettoyée.
        TaskService._finalize_cancel(disk)
        return APPLIED

    @staticmethod
    def pause(task_id: str) -> str:
        """Met en pause une tâche en cours : directement si elle est à nous, via une commande
        déposée pour le poste propriétaire sinon."""
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] == 'running':
                    task['pause'] = True
                    return APPLIED
                return REFUSED
        disk = TaskService._load_disk(task_id)
        if not disk or disk.get('status') != 'running':
            return REFUSED
        return REQUESTED if TaskService._request_control(task_id, 'pause') else REFUSED

    @staticmethod
    def resume(task_id: str) -> str:
        """Reprend une tâche en pause ou interrompue. Les deux repartent de leur dernier
        checkpoint (au dernier registre / à la dernière page terminée) : la pause l'écrit à
        l'arrêt, l'interruption s'appuie sur l'état persisté pendant le traitement.

        Une tâche d'un autre poste ne peut être reprise que par lui (c'est sa file et son
        thread d'exécution) : on lui transmet la demande."""
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] not in ('paused', 'interrupted'):
                    return REFUSED
                task['pause'] = False
                task['status'] = 'queued'
                task['finished_at'] = None   # 'interrupted' est terminal : on le réactive
                task['error'] = None
                TaskService._queue.setdefault(task['type'], []).append(task_id)
                TaskService._save(task)
                TaskService._invalidate_merged()
                ttype = task['type']
                dispatch = True
            else:
                dispatch = False
        if dispatch:
            TaskService._dispatch(ttype)
            return APPLIED
        disk = TaskService._load_disk(task_id)
        if not disk or disk.get('status') not in ('paused', 'interrupted'):
            return REFUSED
        return REQUESTED if TaskService._request_control(task_id, 'resume') else REFUSED

    @staticmethod
    def _finalize_cancel(task: Dict[str, Any]) -> None:
        """Passe une tâche en `cancelled`, persiste, libère ses verrous et joue le hook."""
        task['status'] = 'cancelled'
        task['finished_at'] = datetime.now().isoformat()
        TaskService._save(task)
        TaskService._release_locks(task)
        TaskService._invalidate_merged()
        hook = TaskService._cancel_hooks.get(task.get('type'))
        if hook:
            try:
                hook(task)
            except Exception:
                pass

    @staticmethod
    def delete(task_id: str) -> bool:
        """Supprime une tâche non en cours. Autorisé depuis n'importe quel poste pour les
        tâches d'un autre poste (nettoyage d'orpheline), sauf si elle tourne réellement."""
        TaskService._ensure_loaded()
        with TaskService._lock:
            task = TaskService._tasks.get(task_id)
            if task is not None and TaskService._is_owned(task):
                if task['status'] == 'running':
                    return False
                q = TaskService._queue.get(task['type'])
                if q and task_id in q:
                    q.remove(task_id)
                TaskService._tasks.pop(task_id, None)
                TaskService._release_locks(task)
                TaskService._invalidate_merged()
                TaskService._unlink_task(task_id)
                return True
        # Tâche d'un autre poste.
        disk = TaskService._load_disk(task_id)
        if disk is not None and disk.get('status') == 'running' and not TaskService._is_stale_running(disk):
            return False  # vraiment en cours ailleurs
        with TaskService._lock:
            TaskService._tasks.pop(task_id, None)
        if disk is not None:
            TaskService._release_locks(disk)
        TaskService._invalidate_merged()
        TaskService._unlink_task(task_id)
        return True

    @staticmethod
    def _unlink_task(task_id: str) -> None:
        """Supprime le fichier de la tâche et la commande éventuellement en attente pour elle
        (sinon un marqueur orphelin survivrait à la tâche qu'il visait)."""
        for path in (TaskService._file(task_id), TaskService._control_file(task_id)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _prune_history() -> None:
        """Borne l'historique des tâches **possédées** (n'efface pas celles des autres postes)."""
        finished = [t for t in TaskService._tasks.values()
                    if t['status'] in TERMINAL and TaskService._is_owned(t)]
        if len(finished) <= HISTORY_CAP:
            return
        finished.sort(key=lambda t: t.get('finished_at') or '')
        for task in finished[:len(finished) - HISTORY_CAP]:
            TaskService._tasks.pop(task['id'], None)
            TaskService._unlink_task(task['id'])

    # ── Reprise au démarrage ──────────────────────────────────────────
    @staticmethod
    def _ensure_loaded() -> None:
        if not TaskService._loaded:
            TaskService.load_on_startup()

    @staticmethod
    def _migrate_legacy_ocr_jobs(tasks_dir: Path) -> None:
        """Ingestion unique des anciens jobs OCR (data/ocr_jobs) → data/tasks (type='ocr')."""
        legacy = Path(DATA_DIR) / "ocr_jobs"
        if not legacy.exists():
            return
        for f in legacy.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8-sig') as fh:
                    data = json.load(fh)
                data.setdefault('type', 'ocr')
                dest = tasks_dir / f.name
                if not dest.exists():
                    with open(dest, 'w', encoding='utf-8') as out:
                        json.dump(data, out, ensure_ascii=False)
                f.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                continue
        try:
            legacy.rmdir()
        except OSError:
            pass

    @staticmethod
    def load_on_startup() -> None:
        with TaskService._lock:
            if TaskService._loaded:
                return
            TaskService._loaded = True
            tasks_dir = TaskService._dir()
            TaskService._migrate_legacy_ocr_jobs(tasks_dir)

            for f in tasks_dir.glob("*.json"):
                try:
                    with open(f, 'r', encoding='utf-8-sig') as fh:
                        task = json.load(fh)
                except (OSError, json.JSONDecodeError):
                    continue
                task.setdefault('cancel', False)
                task.setdefault('type', 'ocr')
                # Ne marquer interrompue QUE nos propres tâches : une tâche `running` d'un autre
                # poste tourne peut-être réellement (sa vivacité est jugée via le heartbeat).
                if task.get('status') == 'running' and TaskService._is_owned(task):
                    task['status'] = 'interrupted'   # le process précédent est mort
                    task['current'] = None
                    if not task.get('finished_at'):
                        task['finished_at'] = datetime.now().isoformat()
                    TaskService._save(task)
                    TaskService._release_locks(task)
                TaskService._tasks[task['id']] = task

            # File propre au poste : on ne ré-enfile que NOS tâches en attente.
            queued = [t for t in TaskService._tasks.values()
                      if t.get('status') == 'queued' and TaskService._is_owned(t)]
            queued.sort(key=lambda t: t.get('created_at') or '')
            for t in queued:
                TaskService._queue.setdefault(t['type'], []).append(t['id'])

        for ttype in list(TaskService._queue.keys()):
            TaskService._dispatch(ttype)

        TaskService._start_supervisor()
