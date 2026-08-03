import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  LinearProgress,
  IconButton,
  Button,
  CircularProgress,
  Chip,
  Tooltip,
  Divider,
} from '@mui/material';
import {
  Close as CloseIcon,
  Remove as RemoveIcon,
  CheckCircle as CheckCircleIcon,
  HourglassEmpty as HourglassEmptyIcon,
  PauseCircleOutline as PauseCircleOutlineIcon,
  PlayArrow as PlayArrowIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { useTasks } from '../context/TasksContext';
import { Task } from '../api/tasks';

const WIDTH = 320;
const Z = 1250; // au-dessus du contenu et des drawers, sous les modales (1300)

export default function TaskWidget() {
  const { t: tr, i18n } = useTranslation('common');
  const navigate = useNavigate();
  const { runningTasks, pausedTasks, queuedCount, hasActivity, widgetOpen, closeWidget, resume } = useTasks();

  const fmtNum = (n: number) => n.toLocaleString(i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US');
  const typeLabel = (task: Task) => (task.type === 'ocr' ? tr('taskWidget.typeOcr') : tr('taskWidget.typeIndexation'));
  // Ce que le run couvre : explique un total réduit aux seules pages nouvelles.
  const indexModeLabel = (task: Task) =>
    task.index_is_new ? tr('taskWidget.indexMode.firstBuild')
      : task.index_full ? tr('taskWidget.indexMode.fullRebuild')
        : tr('taskWidget.indexMode.update');
  // Pour l'OCR `current` est la page ; pour l'indexation c'est le registre (trop long ici) :
  // on affiche la page lue, le registre restant en infobulle.
  const currentLabel = (task: Task) => (task.type === 'ocr' ? task.current : task.current_page) || '';
  const [collapsed, setCollapsed] = useState(false);
  const [justFinished, setJustFinished] = useState(false);
  const [resumingIds, setResumingIds] = useState<Set<string>>(new Set());
  const prev = useRef(false);

  const handleResume = async (id: string) => {
    setResumingIds((p) => new Set(p).add(id));
    try { await resume(id); }
    finally { setResumingIds((p) => { const n = new Set(p); n.delete(id); return n; }); }
  };

  // Tâches « actives » à afficher dans le widget : en cours + en pause.
  const activeTasks = [...runningTasks, ...pausedTasks];

  useEffect(() => {
    if (prev.current && !hasActivity) {
      setJustFinished(true);
      const t = setTimeout(() => setJustFinished(false), 6000);
      prev.current = hasActivity;
      return () => clearTimeout(t);
    }
    prev.current = hasActivity;
  }, [hasActivity]);

  // Ouverture manuelle (bouton de la barre) : on déplie le widget.
  useEffect(() => { if (widgetOpen) setCollapsed(false); }, [widgetOpen]);

  // Plus aucune tâche active : on retombe dans l'état fermé.
  useEffect(() => { if (activeTasks.length === 0 && widgetOpen) closeWidget(); }, [activeTasks.length, widgetOpen, closeWidget]);

  // Visible si une tâche tourne (auto) ou si l'utilisateur a ouvert le widget alors
  // qu'une tâche est en cours/en pause. Le flash de fin reste affiché brièvement.
  const visible = hasActivity || (widgetOpen && activeTasks.length > 0);
  if (!visible && !justFinished) return null;

  const anchor = { position: 'fixed', bottom: 24, right: 24, zIndex: Z } as const;

  // ── Flash de fin ──
  if (!hasActivity && justFinished) {
    return (
      <Paper elevation={6} sx={{ ...anchor, width: WIDTH, p: 1.5, borderRadius: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <CheckCircleIcon color="success" />
        <Typography variant="body2" sx={{ mr: 'auto' }}>{tr('taskWidget.finished')}</Typography>
        <Button size="small" onClick={() => navigate('/tasks')}>{tr('taskWidget.view')}</Button>
        <IconButton size="small" onClick={() => setJustFinished(false)}><CloseIcon fontSize="small" /></IconButton>
      </Paper>
    );
  }

  const totalProgress = activeTasks.reduce((acc, t) => acc + (t.processed + t.failed), 0);
  const totalTotal = activeTasks.reduce((acc, t) => acc + t.total, 0);
  const hasRunning = runningTasks.length > 0;
  const headerLabel = activeTasks.length > 1
    ? tr('taskWidget.count', { count: activeTasks.length })
    : hasRunning ? tr('taskWidget.running') : tr('taskWidget.paused');

  // ── Pastille repliée ──
  if (collapsed) {
    return (
      <Tooltip title={tr('taskWidget.show')} arrow>
        <Paper
          elevation={6}
          onClick={() => setCollapsed(false)}
          sx={{ ...anchor, borderRadius: 5, px: 1.5, py: 1, display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer' }}
        >
          <Box sx={{ position: 'relative', display: 'inline-flex' }}>
            {hasRunning ? (
              <CircularProgress
                size={26} thickness={5}
                variant={totalTotal > 0 ? 'determinate' : 'indeterminate'}
                value={totalTotal > 0 ? (totalProgress / totalTotal) * 100 : 0}
              />
            ) : (
              <PauseCircleOutlineIcon color="warning" />
            )}
          </Box>
          <Typography variant="body2" fontWeight={600}>
            {activeTasks.length > 1 ? tr('taskWidget.count', { count: activeTasks.length }) : `${fmtNum(totalProgress)}/${fmtNum(totalTotal)}`}
          </Typography>
        </Paper>
      </Tooltip>
    );
  }

  // ── Carte étendue ──
  return (
    <Paper elevation={6} sx={{ ...anchor, width: WIDTH, borderRadius: 2, overflow: 'hidden' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 1, bgcolor: 'primary.main', color: 'primary.contrastText' }}>
        {hasRunning
          ? <CircularProgress size={18} thickness={5} color="inherit" />
          : <PauseCircleOutlineIcon fontSize="small" />}
        <Typography variant="subtitle2" sx={{ mr: 'auto', fontWeight: 700 }}>
          {headerLabel}
        </Typography>
        <Tooltip title={tr('taskWidget.reduce')} arrow>
          <IconButton size="small" onClick={() => setCollapsed(true)} sx={{ color: 'inherit' }}>
            <RemoveIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      <Box sx={{ p: 1.5 }}>
        {activeTasks.map((t, i) => {
          const done = t.processed + t.failed;
          const pct = t.total > 0 ? (done / t.total) * 100 : 0;
          const paused = t.status === 'paused';
          const resuming = resumingIds.has(t.id);
          const owned = t.owned !== false;
          return (
            <Box key={t.id} sx={{ mb: i < activeTasks.length - 1 ? 1.5 : 0 }}>
              {i > 0 && <Divider sx={{ mb: 1.5 }} />}
              {/* Chips de statut sur leur propre ligne */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5, flexWrap: 'wrap' }}>
                <Chip size="small" label={typeLabel(t)} color={t.type === 'ocr' ? 'success' : 'info'} variant="outlined" sx={{ height: 20 }} />
                {t.type === 'index' && (
                  <Chip size="small" label={indexModeLabel(t)} variant="outlined" sx={{ height: 20 }} />
                )}
                {paused && <Chip size="small" label={tr('taskWidget.pausedChip')} color="warning" sx={{ height: 20 }} />}
                {!owned && t.machine_label && (
                  <Chip size="small" label={t.machine_label} variant="outlined" sx={{ height: 20 }} />
                )}
              </Box>
              {/* Libellé complet : peut passer sur deux lignes plutôt que d'être tronqué */}
              <Typography
                variant="body2"
                fontWeight={600}
                title={t.label}
                sx={{ mb: 0.5, lineHeight: 1.3, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
              >
                {t.label}
              </Typography>
              <LinearProgress
                variant={paused || t.total > 0 ? 'determinate' : 'indeterminate'}
                value={pct}
                color={paused ? 'warning' : 'primary'}
                sx={{ mt: 0.5, mb: 0.5, height: 6, borderRadius: 3 }}
              />
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }} title={t.current ?? ''}>
                  {fmtNum(done)} / {fmtNum(t.total)}
                  {!paused && currentLabel(t) ? ` · ${currentLabel(t)}` : ''}
                  {t.failed > 0 ? ` · ${tr('taskWidget.failures', { count: t.failed })}` : ''}
                </Typography>
                {paused && owned && (
                  <Button
                    size="small"
                    variant="contained"
                    color="warning"
                    disableElevation
                    disabled={resuming}
                    startIcon={resuming ? <CircularProgress size={14} color="inherit" /> : <PlayArrowIcon />}
                    onClick={() => handleResume(t.id)}
                    sx={{ flexShrink: 0 }}
                  >
                    {tr('taskWidget.resume')}
                  </Button>
                )}
              </Box>
              {/* Les pages déjà indexées sortent de la barre : sans cette mention, un total
                  réduit aux seules pages nouvelles surprend. */}
              {(t.index_base ?? 0) > 0 && (
                <Typography variant="caption" color="text.disabled" sx={{ display: 'block' }}>
                  {tr('taskWidget.keptPages', { count: t.index_base!, val: fmtNum(t.index_base!) })}
                </Typography>
              )}
            </Box>
          );
        })}

        {queuedCount > 0 && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1 }}>
            <HourglassEmptyIcon fontSize="inherit" sx={{ color: 'text.secondary' }} />
            <Typography variant="caption" color="text.secondary">
              {tr('taskWidget.queued', { count: queuedCount })}
            </Typography>
          </Box>
        )}

        <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
          <Button size="small" onClick={() => navigate('/tasks')}>{tr('taskWidget.viewAll')}</Button>
        </Box>
      </Box>
    </Paper>
  );
}
