import { useState, useEffect, useCallback, Fragment } from 'react';
import {
  Box,
  Typography,
  LinearProgress,
  Chip,
  Stack,
  Collapse,
  CircularProgress,
  Tooltip,
  IconButton,
  Pagination,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Alert,
} from '@mui/material';
import {
  Cancel as CancelIcon,
  Replay as ReplayIcon,
  Delete as DeleteIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  CheckCircle as CheckCircleIcon,
  ErrorOutline as ErrorOutlineIcon,
  PauseCircleOutline as PauseCircleOutlineIcon,
  RadioButtonUnchecked as PendingIcon,
  Pause as PauseIcon,
  PlayArrow as PlayArrowIcon,
} from '@mui/icons-material';
import { tasksApi, taskWorkMs, Task, TaskPreflight, TaskPagesResponse, TaskRegistresResponse, PageState } from '../api/tasks';
import { useTasks } from '../context/TasksContext';
import TaskActionOverlay from '../components/TaskActionOverlay';
import { PendingTaskAction, usePendingTaskAction } from '../components/usePendingTaskAction';
import { usePageLoading } from '../context/LoadingContext';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import i18n from '../i18n';

const currentLocale = () => (i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US');
const fmtNum = (n: number) => n.toLocaleString(currentLocale());

const TYPE_COLOR: Record<string, 'success' | 'info'> = { ocr: 'success', index: 'info' };

type ChipColor = 'default' | 'primary' | 'success' | 'error' | 'warning' | 'info';
const STATUS_COLOR: Record<string, ChipColor> = {
  running: 'primary',
  queued: 'default',
  paused: 'warning',
  done: 'success',
  error: 'error',
  cancelled: 'default',
  interrupted: 'warning',
};

const taskTitle = (t: Task, tr: TFunction) => {
  if (t.type === 'ocr') {
    const cols = (t.collections ?? []).join(', ') || '—';
    return `${cols} · ${tr('registres', { count: (t.registres ?? []).length })} · ${tr('pages', { count: t.total })}`;
  }
  return t.label;
};
const taskModel = (t: Task) => (t.type === 'ocr' ? t.ocr_model : t.model_name) || '—';

// Ce qu'un run d'indexation couvre réellement : sans cela, un total de 30 pages sur un index de
// 25 000 reste incompréhensible.
const indexModeLabel = (t: Task, tr: TFunction) =>
  t.index_is_new ? tr('indexMode.firstBuild')
    : t.index_full ? tr('indexMode.fullRebuild')
      : tr('indexMode.update');

// Sous la barre : pour l'OCR `current` est déjà la page ; pour l'indexation c'est le registre
// (collection · modèle · registre), trop long ici — on affiche la page lue, registre en infobulle.
const currentLabel = (t: Task) => (t.type === 'ocr' ? t.current : t.current_page) || '';

const fmtTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString(currentLocale(), { dateStyle: 'short', timeStyle: 'short' }) : '');

const fmtDuration = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h} h ${String(m).padStart(2, '0')} min`;
  if (m) return `${m} min ${String(sec).padStart(2, '0')} s`;
  return `${sec} s`;
};

const isRunning = (t: Task) => t.status === 'running';
const isQueued = (t: Task) => t.status === 'queued';
const isPaused = (t: Task) => t.status === 'paused';
// `interrupted` est terminal (le poste s'est arrêté en pleine tâche) mais reste **reprenable** :
// le backend accepte `resume` et `cancel` dessus au même titre qu'une pause, en repartant du
// dernier checkpoint. C'est le cas d'une indexation coupée par un redémarrage du backend.
const isInterrupted = (t: Task) => t.status === 'interrupted';
const isFinished = (t: Task) => ['done', 'error', 'cancelled', 'interrupted'].includes(t.status);
// Statuts d'où l'on peut relancer le travail là où il s'était arrêté.
const isResumable = (t: Task) => isPaused(t) || isInterrupted(t);

// L'overlay d'une action se ferme sur l'effet observé, jamais sur un délai : pause et arrêt sont
// coopératifs, leur latence est celle de la page ou du registre en cours. Une tâche disparue de la
// liste (supprimée ici ou ailleurs) compte comme aboutie, sans quoi l'overlay resterait ouvert.
const actionAboutie = (p: PendingTaskAction, tasks: Task[]): boolean => {
  const t = tasks.find((x) => x.id === p.id);
  if (!t) return true;
  switch (p.kind) {
    case 'pause': return !isRunning(t);
    // `queued` suffit : la reprise a pris, la tâche attend seulement son tour dans la file.
    case 'resume': return !isResumable(t);
    case 'cancel': return isFinished(t);
    // 'delete' et 'start' : rien à observer, l'appelant referme au retour de l'appel.
    default: return false;
  }
};
// `owned` absent (tâche héritée) → considérée locale.
const isOwned = (t: Task) => t.owned !== false;

const PAGE_SIZE = 50;

// Cadence à laquelle un autre poste écrit son avancement sur le partage
// (backend : `task_service.SUPERVISOR_INTERVAL`). Notre polling à 2 s n'y change rien : c'est
// cette période-là qui borne la fraîcheur d'une tâche distante.
const REMOTE_REPORT_INTERVAL_MS = 5000;
// Un relevé peut arriver en retard (NAS lent, page OCR longue) sans que rien n'aille mal :
// on ne s'alarme qu'au-delà de deux périodes et un peu.
const REMOTE_LATE_MS = 12000;
// Au-delà, le backend déclare la tâche morte et son verrou récupérable
// (`task_service.HEARTBEAT_STALE`).
const REMOTE_STALE_MS = 60000;

const HEADER_CELL = {
  fontWeight: 700,
  fontSize: '0.7rem',
  letterSpacing: '0.05em',
  color: 'text.secondary',
  textTransform: 'uppercase',
} as const;

// ── Fraîcheur des données d'une tâche tournant sur un autre poste ──
//
// Une tâche distante n'est pas suivie en direct : son poste écrit son avancement sur le partage
// par battements. Entre deux, la ligne ne bouge pas et on croit qu'il ne se passe rien.
// L'anneau se remplit sur la période de relevé attendue — c'est lui qui donne le signal « ça
// tourne » — et le texte donne l'âge **réel** du dernier battement, ce qui dénonce aussi un
// poste qui s'est tu au lieu d'égrener une seconde mensongère.
function RemoteHeartbeat({ heartbeat, now }: { heartbeat: string; now: number }) {
  const { t: tr } = useTranslation('tasks');
  const parsed = Date.parse(heartbeat);
  if (Number.isNaN(parsed)) return null;
  // `max(0, …)` : l'horloge de l'autre poste peut être un peu en avance sur la nôtre.
  const age = Math.max(0, now - parsed);
  const stale = age >= REMOTE_STALE_MS;
  const late = age >= REMOTE_LATE_MS;
  const next = Math.ceil(Math.max(0, REMOTE_REPORT_INTERVAL_MS - age) / 1000);

  const label = stale ? tr('remote.unreachable', { age: fmtDuration(age) })
    : late ? tr('remote.silent', { age: fmtDuration(age) })
      : `${tr('remote.reported', { age: fmtDuration(age) })} · ${next > 0 ? tr('remote.next', { sec: next }) : tr('remote.nextSoon')}`;

  return (
    <Tooltip title={tr('remote.tooltip')} arrow>
      <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, mt: 0.25, cursor: 'default' }}>
        <CircularProgress
          size={11}
          thickness={7}
          variant="determinate"
          value={Math.min(100, (age / REMOTE_REPORT_INTERVAL_MS) * 100)}
          color={stale ? 'error' : late ? 'warning' : 'primary'}
        />
        <Typography variant="caption" sx={{ fontSize: '0.65rem', color: stale ? 'error.main' : late ? 'warning.main' : 'text.secondary' }}>
          {label}
        </Typography>
      </Box>
    </Tooltip>
  );
}

// ── Environnement détecté au préflight d'une tâche OCR ──
function PreflightChips({ preflight }: { preflight: TaskPreflight }) {
  const { t: tr } = useTranslation('tasks');
  const chips: { label: string; color: ChipColor; tooltip: string }[] = [];
  if (preflight.kraken) {
    chips.push(preflight.kraken.ok
      ? { label: `Kraken ${preflight.kraken.version ?? ''}`.trim(), color: 'success', tooltip: tr('preflight.krakenOk') }
      : { label: tr('preflight.krakenAbsent'), color: 'error', tooltip: preflight.kraken.error || tr('preflight.krakenNotFound') });
  }
  if (preflight.torch) {
    chips.push(preflight.torch.ok
      ? { label: `PyTorch ${preflight.torch.version ?? ''}`.trim(), color: 'success', tooltip: tr('preflight.torchOk') }
      : { label: tr('preflight.torchAbsent'), color: 'error', tooltip: tr('preflight.torchNotFound') });
  }
  chips.push(preflight.device === 'cuda'
    ? { label: `CUDA — ${preflight.cuda?.device ?? 'GPU'}`, color: 'success', tooltip: tr('preflight.cudaTooltip') }
    : { label: tr('preflight.cpuLabel'), color: 'warning', tooltip: tr('preflight.cpuTooltip') });
  // Pool de process indisponible → la tâche a tourné sur un seul cœur : afficher le repli
  // plutôt que les workers demandés, qui n'ont pas servi.
  const workersTooltip = `${tr('preflight.workersCount', { count: preflight.workers })}, ${tr('preflight.threadsPerWorker', { count: preflight.threads })}${preflight.mixed_precision ? tr('preflight.mixedSuffix') : ''}`;
  if (preflight.pool_fallback) {
    chips.push({
      label: tr('preflight.sequentialFallback'),
      color: 'warning',
      tooltip: `${tr('preflight.sequentialFallbackTooltip', { count: preflight.workers })} — ${preflight.pool_fallback}`,
    });
  } else if (preflight.workers_cap_reason === 'gpu_vram') {
    // Réglage ramené à ce que la VRAM de ce poste peut tenir : le dire, sinon l'écart
    // entre le réglage et l'exécution est invisible.
    chips.push({
      label: `W${preflight.workers} · T${preflight.threads}`,
      color: 'warning',
      tooltip: `${tr('preflight.workersCapped', {
        requested: preflight.workers_requested ?? preflight.workers,
        applied: preflight.workers,
        vram: preflight.cuda?.vram_gb ?? '?',
      })} — ${workersTooltip}`,
    });
  } else {
    chips.push({
      label: `W${preflight.workers} · T${preflight.threads}`,
      color: 'default',
      tooltip: workersTooltip,
    });
  }

  return (
    <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
      <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center' }}>
        {tr('preflight.label')}
      </Typography>
      {chips.map((c, i) => (
        <Tooltip key={i} title={c.tooltip} arrow>
          <Chip size="small" variant="outlined" color={c.color} label={c.label} sx={{ cursor: 'default' }} />
        </Tooltip>
      ))}
    </Stack>
  );
}

// ── Liste paginée des pages d'une tâche OCR (panneau dépliable) ──
function TaskPagesPanel({ taskId, live }: { taskId: string; live: boolean }) {
  const { t: tr } = useTranslation('tasks');
  const [filter, setFilter] = useState<'all' | 'todo' | 'done' | 'failed'>('all');
  const [page, setPage] = useState(1);
  const [data, setData] = useState<TaskPagesResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try { setData(await tasksApi.getPages(taskId, filter, (page - 1) * PAGE_SIZE, PAGE_SIZE)); }
    catch { /* réessai */ } finally { setLoading(false); }
  }, [taskId, filter, page]);

  useEffect(() => {
    load();
    if (!live) return;
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [load, live]);

  useEffect(() => { setPage(1); }, [filter]);
  const counts = data?.counts;
  const pageCount = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  useEffect(() => { if (page > pageCount) setPage(pageCount); }, [page, pageCount]);

  const filterChip = (key: typeof filter, label: string, n: number | undefined) => (
    <Chip size="small" label={`${label} ${fmtNum(n ?? 0)}`} onClick={() => setFilter(key)}
      color={filter === key ? 'primary' : 'default'} variant={filter === key ? 'filled' : 'outlined'} />
  );

  const start = data && data.total > 0 ? (page - 1) * PAGE_SIZE + 1 : 0;
  const end = data ? Math.min(page * PAGE_SIZE, data.total) : 0;

  return (
    <Box>
      <Stack direction="row" spacing={0.5} sx={{ mb: 1, flexWrap: 'wrap', gap: 0.5 }}>
        {filterChip('all', tr('pagesPanel.all'), counts?.all)}
        {filterChip('todo', tr('pagesPanel.todo'), counts?.todo)}
        {filterChip('done', tr('pagesPanel.done'), counts?.done)}
        {filterChip('failed', tr('pagesPanel.failed'), counts?.failed)}
      </Stack>
      {loading && !data ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 1 }}>
          <CircularProgress size={14} /><Typography variant="caption" color="text.secondary">{tr('pagesPanel.loading')}</Typography>
        </Box>
      ) : !data || data.items.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ pl: 0.5 }}>{tr('pagesPanel.noPages')}</Typography>
      ) : (
        <>
          <Paper variant="outlined" sx={{ borderRadius: 1.5, overflow: 'hidden' }}>
            <Box sx={{ maxHeight: 280, overflow: 'auto', '& > div': { borderBottom: 1, borderColor: 'divider' }, '& > div:last-of-type': { borderBottom: 0 } }}>
              {data.items.map((p) => <PageRow key={p.page} page={p.page} status={p.status} error={p.error} />)}
            </Box>
          </Paper>
          <Box sx={{ display: 'flex', alignItems: 'center', mt: 1, gap: 1, flexWrap: 'wrap' }}>
            <Typography variant="caption" color="text.secondary" sx={{ mr: 'auto' }}>
              {tr('pagesPanel.range', { start: fmtNum(start), end: fmtNum(end), total: fmtNum(data.total) })}
            </Typography>
            {pageCount > 1 && (
              <Pagination size="small" count={pageCount} page={Math.min(page, pageCount)} onChange={(_, v) => setPage(v)} siblingCount={0} />
            )}
          </Box>
        </>
      )}
    </Box>
  );
}

function PageRow({ page, status, error }: { page: string; status: PageState; error?: string | null }) {
  const icon =
    status === 'done' ? <CheckCircleIcon fontSize="small" color="success" />
      : status === 'failed' ? <ErrorOutlineIcon fontSize="small" color="error" />
        : status === 'current' ? <CircularProgress size={14} />
          : <PendingIcon fontSize="small" sx={{ color: 'text.disabled' }} />;
  const row = (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.5, '&:hover': { bgcolor: 'action.hover' } }}>
      <Box sx={{ width: 18, display: 'flex', justifyContent: 'center' }}>{icon}</Box>
      <Typography variant="caption" sx={{ fontFamily: 'monospace' }} noWrap>{page}</Typography>
    </Box>
  );
  return error ? <Tooltip title={error} arrow placement="left">{row}</Tooltip> : row;
}

// ── Trace par registre d'une tâche d'indexation ──
function TaskRegistresPanel({ taskId, live }: { taskId: string; live: boolean }) {
  const { t: tr } = useTranslation('tasks');
  const [data, setData] = useState<TaskRegistresResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try { setData(await tasksApi.getRegistres(taskId)); }
    catch { /* réessai */ } finally { setLoading(false); }
  }, [taskId]);

  useEffect(() => {
    load();
    if (!live) return;
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [load, live]);

  if (loading && !data) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, p: 1 }}>
        <CircularProgress size={14} /><Typography variant="caption" color="text.secondary">{tr('registresPanel.loading')}</Typography>
      </Box>
    );
  }
  if (!data || data.items.length === 0) {
    return <Typography variant="caption" color="text.secondary" sx={{ pl: 0.5 }}>{tr('registresPanel.noRegistres')}</Typography>;
  }
  const c = data.counts;
  return (
    <Box>
      <Stack direction="row" spacing={0.5} sx={{ mb: 1, flexWrap: 'wrap', gap: 0.5 }}>
        <Chip size="small" color="success" variant="outlined" label={tr('registresPanel.indexed', { done: fmtNum(c.done), all: fmtNum(c.all) })} />
        {c.current > 0 && <Chip size="small" color="primary" variant="outlined" label={tr('registresPanel.current')} />}
        {c.pending > 0 && <Chip size="small" variant="outlined" label={tr('registresPanel.pending', { count: c.pending })} />}
      </Stack>
      <Paper variant="outlined" sx={{ borderRadius: 1.5, overflow: 'hidden' }}>
        <Box sx={{ maxHeight: 280, overflow: 'auto', '& > div': { borderBottom: 1, borderColor: 'divider' }, '& > div:last-of-type': { borderBottom: 0 } }}>
          {data.items.map((r) => {
            const icon =
              r.status === 'done' ? <CheckCircleIcon fontSize="small" color="success" />
                : r.status === 'current' ? <CircularProgress size={14} />
                  : <PendingIcon fontSize="small" sx={{ color: 'text.disabled' }} />;
            return (
              <Box key={r.name} sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.5, '&:hover': { bgcolor: 'action.hover' } }}>
                <Box sx={{ width: 18, display: 'flex', justifyContent: 'center' }}>{icon}</Box>
                <Typography variant="caption" sx={{ fontFamily: 'monospace', mr: 'auto' }} noWrap>{r.name}</Typography>
                <Typography variant="caption" color="text.secondary">{tr('pages', { count: r.pages })}</Typography>
              </Box>
            );
          })}
        </Box>
      </Paper>
    </Box>
  );
}

export default function TasksPage() {
  const { t: tr } = useTranslation(['tasks', 'common']);
  const { cancel, pause, resume, remove, hasActivity, refresh: refreshSummary } = useTasks();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  usePageLoading(tr('loading'), loading);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [pausingIds, setPausingIds] = useState<Set<string>>(new Set());
  // Commandes transmises à un autre poste : l'effet arrive à son prochain relevé (~5 s).
  const [requestedIds, setRequestedIds] = useState<Map<string, string>>(new Map());
  const [now, setNow] = useState(Date.now());
  const [machineFilter, setMachineFilter] = useState<string | null>(null);
  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set());
  // La relance crée une nouvelle tâche sans toucher à la tâche source, dont les pages restent
  // en échec : sans cette trace le bouton resterait actif et inviterait à relancer en boucle.
  const [retriedIds, setRetriedIds] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);
  // Overlay bloquant tant que l'action cliquée n'a pas pris effet (pause et arrêt sont coopératifs).
  const {
    pending, overlayOpen, start: startAction, update: updateAction, done: doneAction, hide: hideAction,
  } = usePendingTaskAction();

  const refresh = useCallback(async () => {
    try { setTasks(await tasksApi.list()); }
    catch { /* réessai */ } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    refresh();
    if (!hasActivity) return;
    const timer = setInterval(refresh, 2000);
    return () => clearInterval(timer);
  }, [refresh, hasActivity]);

  useEffect(() => {
    if (!hasActivity) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [hasActivity]);

  // « Mise en pause… » tant que la tâche est encore en cours (pause coopérative).
  useEffect(() => {
    const runningIds = new Set(tasks.filter(isRunning).map((t) => t.id));
    setPausingIds((prev) => {
      const next = new Set([...prev].filter((id) => runningIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [tasks]);

  // L'overlay se ferme dès que l'action cliquée a produit son effet observable.
  useEffect(() => {
    if (pending && actionAboutie(pending, tasks)) doneAction();
  }, [pending, tasks, doneAction]);

  // « Demande envoyée » jusqu'à ce que le poste destinataire l'ait appliquée (statut changé).
  useEffect(() => {
    const stillRunning = new Set(tasks.filter(isRunning).map((t) => t.id));
    setRequestedIds((prev) => {
      const next = new Map([...prev].filter(([id]) => stillRunning.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [tasks]);

  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Une commande visant un autre poste revient `requested` : on l'affiche en attente au lieu de
  // laisser croire que rien ne s'est passé.
  const noteRequest = (id: string, result: { requested: boolean; machine_label: string | null }) => {
    if (!result.requested) return;
    setRequestedIds((p) => new Map(p).set(id, result.machine_label || tr('otherMachine')));
  };

  // Les quatre actions suivent le même scénario : ouvrir l'overlay avant l'appel (l'effet n'est pas
  // immédiat, cf. TaskActionOverlay), puis le laisser se fermer sur le statut. Le `catch` est
  // indispensable : un overlay qui survivrait à une erreur réseau piégerait l'interface.
  const runAction = async (
    action: PendingTaskAction,
    appel: () => Promise<{ requested: boolean; machine_label: string | null } | void>,
  ) => {
    startAction(action);
    setActionError(null);
    try {
      const result = await appel();
      if (result) {
        noteRequest(action.id, result);
        if (result.requested) updateAction({ machine: result.machine_label || tr('otherMachine') });
      }
      await refresh();
      // La suppression n'a pas d'état intermédiaire à observer, et le backend peut la refuser sans
      // lever (tâche devenue active) : son issue est celle de l'appel, on ferme donc ici plutôt que
      // d'attendre une disparition qui pourrait ne jamais venir.
      if (action.kind === 'delete') doneAction();
    } catch {
      doneAction();
      setActionError(tr('actionError'));
    }
  };

  const handleCancel = (t: Task) =>
    runAction({ kind: 'cancel', id: t.id, taskType: t.type }, () => cancel(t.id));
  const handlePause = (t: Task) => {
    setPausingIds((p) => new Set(p).add(t.id));
    return runAction({ kind: 'pause', id: t.id, taskType: t.type }, () => pause(t.id));
  };
  const handleResume = (t: Task) =>
    runAction({ kind: 'resume', id: t.id, taskType: t.type }, () => resume(t.id));
  const handleDelete = (t: Task) =>
    runAction({ kind: 'delete', id: t.id, taskType: t.type }, () => remove(t.id));

  // Réenfile les pages en échec dans une NOUVELLE tâche (la tâche source n'est pas modifiée :
  // elle peut appartenir à un autre poste). `refreshSummary` réveille le polling et le widget.
  const handleRetry = async (id: string) => {
    setRetryingIds((p) => new Set(p).add(id));
    setActionError(null);
    try {
      await tasksApi.retryFailed(id);
      setRetriedIds((p) => new Set(p).add(id));
      await refreshSummary();
      await refresh();
    } catch (e: any) {
      setActionError(e?.response?.data?.detail || tr('retryError'));
    } finally {
      setRetryingIds((p) => { const n = new Set(p); n.delete(id); return n; });
    }
  };

  if (loading) return null;  // overlay global (usePageLoading) pendant le chargement initial

  if (tasks.length === 0) {
    return (
      <Box sx={{ textAlign: 'center', mt: 10, color: 'text.disabled' }}>
        <PauseCircleOutlineIcon sx={{ fontSize: 56, opacity: 0.2, mb: 1 }} />
        <Typography variant="body2" color="text.disabled">
          {tr('empty')}
        </Typography>
      </Box>
    );
  }

  // Postes distincts présents (pour le filtre multi-PC).
  const machines = Array.from(new Set(tasks.map((t) => t.machine_label).filter((m): m is string => !!m))).sort();
  // Toutes les tâches dans un seul tableau, les plus récentes en premier (filtré par poste).
  const sorted = [...tasks]
    .filter((t) => machineFilter === null || t.machine_label === machineFilter)
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

  return (
    <Box>
      {machines.length > 1 && (
        <Stack direction="row" spacing={0.5} sx={{ mb: 2, flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>{tr('machine')}</Typography>
          <Chip size="small" label={tr('machineAll')} onClick={() => setMachineFilter(null)}
            color={machineFilter === null ? 'primary' : 'default'} variant={machineFilter === null ? 'filled' : 'outlined'} />
          {machines.map((m) => (
            <Chip key={m} size="small" label={m} onClick={() => setMachineFilter(m)}
              color={machineFilter === m ? 'primary' : 'default'} variant={machineFilter === m ? 'filled' : 'outlined'} />
          ))}
        </Stack>
      )}
      {actionError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>{actionError}</Alert>
      )}
    <Paper elevation={0} sx={{ border: '1.5px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow sx={{ bgcolor: 'grey.50' }}>
            <TableCell sx={{ ...HEADER_CELL, width: 88 }}>{tr('table.type')}</TableCell>
            <TableCell sx={HEADER_CELL}>{tr('table.task')}</TableCell>
            <TableCell sx={{ ...HEADER_CELL, width: 150 }}>{tr('table.machine')}</TableCell>
            <TableCell sx={{ ...HEADER_CELL, width: 116 }}>{tr('table.status')}</TableCell>
            <TableCell sx={{ ...HEADER_CELL, width: 280 }}>{tr('table.progress')}</TableCell>
            <TableCell sx={{ ...HEADER_CELL, width: 180 }}>{tr('table.time')}</TableCell>
            <TableCell align="right" sx={{ ...HEADER_CELL, width: 185 }}>{tr('table.actions')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {sorted.map((t) => {
            const sc = { label: tr(`status.${t.status}`, { defaultValue: t.status }), color: STATUS_COLOR[t.status] ?? 'default' as const };
            const done = t.processed + t.failed;
            const remaining = t.total - done;
            const pct = t.total > 0 ? (done / t.total) * 100 : 0;
            // Temps de travail cumulé, pause exclue : c'est la bonne base d'estimation, elle se
            // rapporte au même travail que `done`.
            const elapsed = taskWorkMs(t, now);
            const eta = isRunning(t) && elapsed != null && done > 0 && remaining > 0 ? (elapsed / done) * remaining : null;
            const isOcr = t.type === 'ocr';
            const isOpen = expanded.has(t.id);
            const pausing = pausingIds.has(t.id);
            const owned = isOwned(t);
            const requested = requestedIds.get(t.id);
            const machineName = t.machine_label || tr('otherMachine');

            return (
              <Fragment key={t.id}>
                <TableRow hover sx={{ '& td': { borderBottom: isOpen ? 'none' : undefined, verticalAlign: 'top' } }}>
                  {/* Type */}
                  <TableCell>
                    <Chip size="small" label={tr(`type.${t.type}`, { defaultValue: t.type })} color={TYPE_COLOR[t.type]} variant="outlined" />
                  </TableCell>

                  {/* Tâche */}
                  <TableCell>
                    <Typography variant="body2" fontWeight={500}>{taskTitle(t, tr)}</Typography>
                    {t.type === 'index' && (t.index_sources?.length ?? 0) > 0 ? (
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 0.5, maxWidth: 440 }}>
                        <Chip size="small" variant="outlined" color="info" label={indexModeLabel(t, tr)} sx={{ height: 20 }} />
                        {t.index_sources!.slice(0, 4).map((s, i) => {
                          const label = `${s.collection_titre || s.collection_folder || '—'} · ${s.model_name || '—'}`;
                          return (
                            <Tooltip key={i} title={label} arrow>
                              <Chip size="small" variant="outlined" label={label} sx={{ height: 20, maxWidth: 240 }} />
                            </Tooltip>
                          );
                        })}
                        {t.index_sources!.length > 4 && (
                          <Chip size="small" variant="outlined" label={`+${t.index_sources!.length - 4}`} sx={{ height: 20 }} />
                        )}
                      </Box>
                    ) : (
                      <Typography variant="caption" color="text.secondary">{tr('modelLabel', { model: taskModel(t) })}</Typography>
                    )}
                    {t.status === 'error' && t.error && (
                      <Typography variant="caption" color="error" sx={{ display: 'block' }} noWrap title={t.error}>{t.error}</Typography>
                    )}
                  </TableCell>

                  {/* Poste */}
                  <TableCell>
                    <Typography variant="body2" noWrap title={t.machine_label}>{t.machine_label || '—'}</Typography>
                    {t.operator && (
                      <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>{t.operator}</Typography>
                    )}
                    {owned && (
                      <Chip size="small" label={tr('thisMachine')} color="primary" variant="outlined" sx={{ height: 18, fontSize: '0.65rem', mt: 0.25 }} />
                    )}
                    {/* Seulement `running` : le heartbeat n'est rafraîchi que pour les tâches en
                        cours du poste propriétaire. Sur une tâche distante en attente ou en pause,
                        ce poste n'écrit rien — un décompte y monterait sans fin et crierait à la
                        panne là où il n'y a, par construction, rien à rafraîchir. */}
                    {!owned && isRunning(t) && t.heartbeat && <RemoteHeartbeat heartbeat={t.heartbeat} now={now} />}
                  </TableCell>

                  {/* Statut */}
                  <TableCell>
                    <Chip size="small" label={sc.label} color={sc.color} variant="outlined" />
                  </TableCell>

                  {/* Progression */}
                  <TableCell>
                    {isRunning(t) || isPaused(t) ? (
                      <Box>
                        <LinearProgress
                          variant={t.total > 0 ? 'determinate' : 'indeterminate'}
                          value={pct}
                          color={isPaused(t) ? 'warning' : 'primary'}
                          sx={{ height: 6, borderRadius: 3, mb: 0.5 }}
                        />
                        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }} title={t.current ?? ''}>
                          {fmtNum(done)} / {fmtNum(t.total)}
                          {/* Étape de fin d'indexation : elle arrive barre pleine, il n'y a plus
                              de page en cours, et c'est elle qu'on attend — parfois des minutes. */}
                          {t.index_phase ? ` · ${tr(`common:indexPhase.${t.index_phase}`)}`
                            : currentLabel(t) ? ` · ${currentLabel(t)}` : ''}
                          {t.failed > 0 ? ` · ${tr('failures', { count: t.failed })}` : ''}
                        </Typography>
                        {/* Les pages déjà indexées sortent de la barre : sans cette mention, un
                            total réduit aux seules pages nouvelles surprend. */}
                        {(t.index_base ?? 0) > 0 && (
                          <Typography variant="caption" color="text.disabled" noWrap sx={{ display: 'block' }}>
                            {tr('keptPages', { count: t.index_base!, val: fmtNum(t.index_base!) })}
                          </Typography>
                        )}
                      </Box>
                    ) : isQueued(t) ? (
                      <Typography variant="caption" color="text.secondary">
                        {owned ? tr('queuedOwned') : tr('queuedOther', { machine: t.machine_label || tr('otherMachine') })}
                      </Typography>
                    ) : (
                      <Typography variant="caption" color="text.secondary">
                        {tr('pagesProcessed', { count: t.processed })}
                        {t.failed > 0 ? ` · ${tr('failures', { count: t.failed })}` : ''}
                      </Typography>
                    )}
                  </TableCell>

                  {/* Temps */}
                  <TableCell>
                    {/* En pause aussi : le temps écoulé est désormais figé et juste, il dit
                        combien de travail a déjà été fourni. */}
                    {(isRunning(t) || isPaused(t)) && elapsed != null ? (
                      <Typography variant="caption" color="text.secondary">
                        {tr('elapsed', { dur: fmtDuration(elapsed) })}{eta != null ? tr('remaining', { dur: fmtDuration(eta) }) : ''}
                      </Typography>
                    ) : isFinished(t) && elapsed != null ? (
                      <Typography variant="caption" color="text.secondary">{tr('duration', { dur: fmtDuration(elapsed) })}</Typography>
                    ) : null}
                    <Typography variant="caption" color="text.disabled" sx={{ display: 'block' }}>
                      {fmtTime(t.finished_at || t.started_at || t.created_at)}
                    </Typography>
                  </TableCell>

                  {/* Actions */}
                  <TableCell align="right">
                    <Stack direction="row" spacing={0.25} justifyContent="flex-end">
                      {/* Commande transmise à un autre poste : en attente de son relevé (~5 s). */}
                      {requested && (
                        <Tooltip title={tr('actions.requestSent', { machine: requested })}>
                          <span><IconButton size="small" disabled><CircularProgress size={16} /></IconButton></span>
                        </Tooltip>
                      )}
                      {/* Pause : OCR et indexation repartent l'une comme l'autre de leur checkpoint.
                          Sur une tâche d'un autre poste, la commande lui est transmise. */}
                      {!requested && isRunning(t) && (
                        pausing ? (
                          <Tooltip title={tr('actions.pausing')}><span><IconButton size="small" disabled><CircularProgress size={16} /></IconButton></span></Tooltip>
                        ) : (
                          <Tooltip title={owned ? tr('actions.pause') : tr('actions.pauseOther', { machine: machineName })}>
                            <IconButton size="small" color="primary" onClick={() => handlePause(t)}><PauseIcon fontSize="small" /></IconButton>
                          </Tooltip>
                        )
                      )}
                      {/* Reprise : une tâche interrompue par un arrêt du poste se relance comme une
                          pause, depuis son checkpoint. Sans ce bouton ici, une indexation coupée par
                          un redémarrage du backend n'était reprenable que depuis la page Index. */}
                      {!requested && isResumable(t) && (
                        <Tooltip title={owned ? tr('actions.resume') : tr('actions.resumeOther', { machine: machineName })}>
                          <IconButton size="small" color="primary" onClick={() => handleResume(t)}><PlayArrowIcon fontSize="small" /></IconButton>
                        </Tooltip>
                      )}
                      {!requested && (isRunning(t) || isQueued(t) || isResumable(t)) && (
                        <Tooltip title={owned ? tr('actions.cancel') : tr('actions.cancelOther', { machine: machineName })}>
                          <IconButton size="small" color="error" onClick={() => handleCancel(t)}><CancelIcon fontSize="small" /></IconButton>
                        </Tooltip>
                      )}
                      {/* Relance des échecs : une reprise ne refait que les pages « à faire », jamais
                          celles en échec — c'est le seul moyen de les rattraper sans les rechercher
                          une par une. Autorisé aussi sur la tâche d'un autre poste (qui peut être
                          éteint) : la nouvelle tâche nous appartient, les conflits de périmètre
                          restent arbitrés à l'enfilage. */}
                      {isOcr && t.failed > 0 && ['done', 'error', 'cancelled'].includes(t.status) && (
                        <Tooltip title={retriedIds.has(t.id) ? tr('actions.retryDone') : tr('actions.retryFailed', { count: t.failed })}>
                          <span>
                            <IconButton
                              size="small"
                              color="warning"
                              disabled={retryingIds.has(t.id) || retriedIds.has(t.id)}
                              onClick={() => handleRetry(t.id)}
                            >
                              {retryingIds.has(t.id) ? <CircularProgress size={16} /> : <ReplayIcon fontSize="small" />}
                            </IconButton>
                          </span>
                        </Tooltip>
                      )}
                      {/* Suppression : tâche terminée (n'importe quel poste) ou orpheline en attente/pause d'un autre poste. */}
                      {(isFinished(t) || (!owned && (isQueued(t) || isPaused(t)))) && (
                        <Tooltip title={owned ? tr('actions.delete') : tr('actions.deleteOther')}>
                          <IconButton size="small" onClick={() => handleDelete(t)} sx={{ color: 'text.secondary' }}><DeleteIcon fontSize="small" /></IconButton>
                        </Tooltip>
                      )}
                      <Tooltip title={isOcr ? tr('actions.viewPages') : tr('actions.viewRegistres')}>
                        <IconButton size="small" onClick={() => toggleExpand(t.id)}>{isOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}</IconButton>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                </TableRow>

                <TableRow>
                  <TableCell colSpan={7} sx={{ p: 0, borderBottom: isOpen ? undefined : 'none' }}>
                    <Collapse in={isOpen} unmountOnExit>
                      <Box sx={{ px: 2, py: 1.5, bgcolor: 'grey.50', borderTop: 1, borderColor: 'divider' }}>
                        {isOcr && t.preflight && <PreflightChips preflight={t.preflight} />}
                        {isOcr
                          ? <TaskPagesPanel taskId={t.id} live={isRunning(t)} />
                          : <TaskRegistresPanel taskId={t.id} live={isRunning(t)} />}
                      </Box>
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
    </Paper>

    <TaskActionOverlay pending={pending} open={overlayOpen} onHide={hideAction} />
    </Box>
  );
}
