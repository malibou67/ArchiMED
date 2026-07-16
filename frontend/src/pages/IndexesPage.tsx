import { useEffect, useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputAdornment,
  LinearProgress,
  ListItemText,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import {
  Add as AddIcon,
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  Clear as ClearIcon,
  Delete as DeleteIcon,
  Edit as EditIcon,
  Error as ErrorIcon,
  HourglassEmpty as HourglassIcon,
  Layers as LayersIcon,
  Search as SearchIcon,
  Pause as PauseIcon,
  PlayArrow as PlayArrowIcon,
  Refresh as RefreshIcon,
  Schedule as ScheduleIcon,
  Storage as StorageIcon,
  Sync as SyncIcon,
  Visibility as VisibilityIcon,
  WarningAmber as WarningIcon,
} from '@mui/icons-material';
import { useTranslation, Trans } from 'react-i18next';
import { indexesApi } from '../api/indexes';
import { collectionsApi } from '../api/collections';
import {
  IndexMetadata,
  IndexProgress as IndexProgressType,
  IndexSource,
  IndexPreview,
  CollectionMetadata,
} from '../types';
import { useTasks } from '../context/TasksContext';
import EmptyState from '../components/EmptyState';
import Loader from '../components/Loader';

// Normalisation pour la recherche : minuscules, sans diacritiques.
const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

// Ligne du constructeur : une collection + un ou plusieurs modèles OCR sélectionnés.
type BuilderRow = { collection_id: string; models: string[] };

const groupSources = (srcs: IndexSource[]): BuilderRow[] => {
  const map = new Map<string, string[]>();
  for (const s of srcs) {
    const arr = map.get(s.collection_id) ?? [];
    arr.push(s.model_name);
    map.set(s.collection_id, arr);
  }
  return [...map.entries()].map(([collection_id, models]) => ({ collection_id, models }));
};

const rowsToSources = (rows: BuilderRow[]): IndexSource[] =>
  rows.flatMap(r => r.models.map(m => ({ collection_id: r.collection_id, model_name: m })));

// Libellé des sources d'un index (multi-sources ou legacy) pour l'affichage.
const sourcesLabels = (index: IndexMetadata, collections: CollectionMetadata[] = []): { label: string; sub: string }[] => {
  if (index.sources && index.sources.length) {
    return index.sources.map(s => ({
      label: s.collection_titre || s.collection_folder,
      sub: s.model_name,
    }));
  }
  if (index.collection_id || index.model_name) {
    const col = collections.find(c => c.id === index.collection_id || c.folder_name === index.collection_id);
    return [{ label: col?.titre ?? index.collection_id ?? '', sub: index.model_name ?? '' }];
  }
  return [];
};

function StatusChip({ status }: { status: IndexMetadata['status'] }) {
  const { t } = useTranslation('indexes');
  if (status === 'ready')
    return <Chip icon={<CheckCircleIcon sx={{ fontSize: '14px !important' }} />} label={t('status.ready')} color="success" size="small" variant="outlined" sx={{ fontWeight: 500 }} />;
  if (status === 'generating')
    return <Chip icon={<HourglassIcon sx={{ fontSize: '14px !important' }} />} label={t('status.generating')} color="warning" size="small" variant="outlined" sx={{ fontWeight: 500 }} />;
  return <Chip icon={<ErrorIcon sx={{ fontSize: '14px !important' }} />} label={t('status.error')} color="error" size="small" variant="outlined" sx={{ fontWeight: 500 }} />;
}

function IndexProgress({ progress, rebuild }: { progress?: IndexProgressType; rebuild?: boolean }) {
  const { t } = useTranslation('indexes');
  const p = progress;
  if (!p || (p.total === 0 && !p.current_registre)) {
    return (
      <Box>
        <LinearProgress variant="indeterminate" color={rebuild ? 'info' : 'warning'} sx={{ height: 5, borderRadius: 3, mb: 0.5 }} />
        <Typography variant="caption" color="text.disabled">
          {rebuild ? t('progress.rebuilding') : t('progress.starting')}
        </Typography>
      </Box>
    );
  }
  if (p.total > 0) {
    const pct = Math.round((p.processed / p.total) * 100);
    return (
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <LinearProgress variant="determinate" value={pct} color={rebuild ? 'info' : 'warning'} sx={{ flex: 1, height: 5, borderRadius: 3 }} />
          <Typography variant="caption" fontWeight={600} color={rebuild ? 'info.dark' : 'warning.dark'} sx={{ minWidth: 32 }}>{pct} %</Typography>
        </Box>
        <Tooltip title={p.current_registre ?? ''} arrow disableHoverListener={!p.current_registre}>
          <Typography variant="caption" color="text.disabled" noWrap sx={{ display: 'block', maxWidth: 260 }}>
            {p.current_registre ?? '…'}
          </Typography>
        </Tooltip>
      </Box>
    );
  }
  return <LinearProgress variant="indeterminate" color={rebuild ? 'info' : 'warning'} sx={{ height: 5, borderRadius: 3 }} />;
}

const HEADER_CELL = {
  fontWeight: 700,
  fontSize: '0.7rem',
  letterSpacing: '0.05em',
  color: 'text.secondary',
  textTransform: 'uppercase',
} as const;

// ── Dialog constructeur d'index (création + édition) ─────────────────────────
function IndexBuilderDialog({
  open, mode, collections, initialName, initialSources, submitting, onClose, onSubmit,
}: {
  open: boolean;
  mode: 'create' | 'edit';
  collections: CollectionMetadata[];
  initialName: string;
  initialSources: IndexSource[];
  submitting: boolean;
  onClose: () => void;
  onSubmit: (name: string, sources: IndexSource[]) => void;
}) {
  const { t } = useTranslation(['indexes', 'common']);
  const [rows, setRows] = useState<BuilderRow[]>([]);
  const [name, setName] = useState('');
  const [nameEdited, setNameEdited] = useState(false);
  const [modelsByCollection, setModelsByCollection] = useState<Record<string, string[]>>({});
  const [loadingModels, setLoadingModels] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<IndexPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const collectionTitre = useCallback(
    (id: string) => collections.find(c => c.id === id)?.titre ?? id,
    [collections],
  );

  // (Ré)initialise l'état à l'ouverture.
  useEffect(() => {
    if (!open) return;
    const initRows = initialSources.length ? groupSources(initialSources) : [{ collection_id: '', models: [] }];
    setRows(initRows);
    setName(initialName);
    setNameEdited(mode === 'edit');
    setPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const ensureModels = useCallback(async (collectionId: string) => {
    if (!collectionId || modelsByCollection[collectionId]) return;
    setLoadingModels(prev => ({ ...prev, [collectionId]: true }));
    try {
      const models = await indexesApi.getAvailableModels(collectionId);
      setModelsByCollection(prev => ({ ...prev, [collectionId]: models }));
    } catch {
      setModelsByCollection(prev => ({ ...prev, [collectionId]: [] }));
    } finally {
      setLoadingModels(prev => ({ ...prev, [collectionId]: false }));
    }
  }, [modelsByCollection]);

  // Précharge les modèles des collections déjà présentes (édition).
  useEffect(() => {
    if (!open) return;
    rows.forEach(r => { if (r.collection_id) ensureModels(r.collection_id); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rows.length]);

  const sources = useMemo(() => rowsToSources(rows), [rows]);

  // Nom auto-suggéré tant que l'utilisateur ne l'a pas édité.
  useEffect(() => {
    if (nameEdited) return;
    const withModels = rows.filter(r => r.collection_id && r.models.length);
    const titres = [...new Set(withModels.map(r => collectionTitre(r.collection_id)))];
    let suggestion = '';
    if (titres.length === 1) {
      const only = withModels.filter(r => collectionTitre(r.collection_id) === titres[0]);
      const models = [...new Set(only.flatMap(r => r.models))];
      suggestion = models.length === 1 ? `${titres[0]} · ${models[0]}` : titres[0];
    } else if (titres.length > 1) {
      suggestion = titres.length <= 2 ? titres.join(' + ') : `${titres[0]} +${titres.length - 1}`;
    }
    setName(suggestion);
  }, [rows, nameEdited, collectionTitre]);

  // Aperçu (débounce) dès qu'il y a au moins une source complète.
  useEffect(() => {
    if (!open || sources.length === 0) { setPreview(null); return; }
    setPreviewLoading(true);
    const key = JSON.stringify(sources);
    const timer = setTimeout(async () => {
      try {
        const p = await indexesApi.preview(sources);
        setPreview(prev => (JSON.stringify(sources) === key ? p : prev));
      } catch {
        /* aperçu non bloquant */
      } finally {
        setPreviewLoading(false);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [open, sources]);

  const setRow = (i: number, patch: Partial<BuilderRow>) =>
    setRows(prev => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const removeRow = (i: number) => setRows(prev => prev.filter((_, idx) => idx !== i));
  const addRow = () => setRows(prev => [...prev, { collection_id: '', models: [] }]);

  const canSubmit = sources.length > 0 && name.trim().length > 0 && !submitting;

  return (
    <Dialog open={open} onClose={() => !submitting && onClose()} fullWidth maxWidth="md" slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
      <DialogTitle sx={{ pb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              width: 38, height: 38, borderRadius: 2, flexShrink: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              bgcolor: (t) => alpha(t.palette.primary.main, 0.12), color: 'primary.main',
            }}
          >
            <LayersIcon fontSize="small" />
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="h6" fontWeight={700} lineHeight={1.25}>
              {mode === 'edit' ? t('builder.editTitle') : t('builder.createTitle')}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('builder.subtitle')}
            </Typography>
          </Box>
        </Box>
      </DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2 }}>
          <TextField
            label={t('builder.nameLabel')}
            size="small"
            fullWidth
            value={name}
            onChange={e => { setName(e.target.value); setNameEdited(true); }}
            placeholder={t('builder.namePlaceholder')}
          />

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography variant="caption" fontWeight={700} sx={{ letterSpacing: '0.05em' }} color="text.secondary">
              {t('builder.sources')}
            </Typography>
            <Divider sx={{ flex: 1 }} />
          </Box>

          <Stack spacing={1.5}>
            {rows.map((row, i) => {
              const models = modelsByCollection[row.collection_id] ?? [];
              const modelsLoading = !!loadingModels[row.collection_id];
              return (
                <Paper
                  key={i}
                  variant="outlined"
                  sx={{
                    p: 2, borderRadius: 2,
                    transition: 'border-color .15s, box-shadow .15s',
                    '&:hover': { borderColor: 'primary.main', boxShadow: (t) => `0 0 0 1px ${alpha(t.palette.primary.main, 0.25)}` },
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5 }}>
                    <Box
                      sx={{
                        width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        bgcolor: (t) => alpha(t.palette.primary.main, 0.12), color: 'primary.main',
                        fontSize: 13, fontWeight: 700,
                      }}
                    >
                      {i + 1}
                    </Box>
                    <FormControl sx={{ flex: 1, minWidth: 0 }} size="small">
                      <Select
                        value={row.collection_id}
                        displayEmpty
                        onChange={e => { setRow(i, { collection_id: e.target.value, models: [] }); ensureModels(e.target.value); }}
                        renderValue={val =>
                          val
                            ? <Typography variant="body2" fontWeight={600} noWrap>{collectionTitre(val)}</Typography>
                            : <Typography variant="body2" color="text.disabled">{t('builder.chooseCollection')}</Typography>
                        }
                      >
                        {collections.map(col => (
                          <MenuItem key={col.id} value={col.id}>
                            <Typography variant="body2">{col.titre}</Typography>
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <Tooltip title={t('builder.removeSource')} arrow>
                      <span>
                        <IconButton size="small" onClick={() => removeRow(i)} disabled={rows.length === 1}>
                          <ClearIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  </Box>

                  <Box sx={{ pl: { xs: 0, sm: 5 } }}>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                      {t('builder.ocrModels')}
                    </Typography>
                    <FormControl fullWidth size="small" disabled={!row.collection_id || modelsLoading}>
                      <Select
                        multiple
                        value={row.models}
                        onChange={e => setRow(i, { models: typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value })}
                        displayEmpty
                        renderValue={(selected) =>
                          modelsLoading ? (
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <CircularProgress size={14} /><Typography variant="body2" color="text.disabled">{t('common:loading.default')}</Typography>
                            </Box>
                          ) : selected.length ? (
                            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                              {selected.map(m => <Chip key={m} label={m} size="small" color="primary" variant="outlined" sx={{ height: 22 }} />)}
                            </Box>
                          ) : (
                            <Typography variant="body2" color="text.disabled">
                              {row.collection_id ? (models.length ? t('builder.selectModels') : t('builder.noModels')) : t('builder.chooseCollectionFirst')}
                            </Typography>
                          )
                        }
                      >
                        {models.map(m => (
                          <MenuItem key={m} value={m} dense>
                            <Checkbox size="small" checked={row.models.includes(m)} />
                            <ListItemText primary={<Typography variant="body2">{m}</Typography>} />
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </Box>
                </Paper>
              );
            })}
          </Stack>

          <Button
            fullWidth
            variant="outlined"
            startIcon={<AddIcon />}
            onClick={addRow}
            sx={{
              borderStyle: 'dashed', borderWidth: 1.5, py: 1.1, color: 'text.secondary', borderColor: 'divider',
              '&:hover': { borderStyle: 'dashed', borderWidth: 1.5, borderColor: 'primary.main', bgcolor: 'action.hover', color: 'primary.main' },
            }}
          >
            {t('builder.addCollection')}
          </Button>

          {/* Aperçu de ce qui sera indexé */}
          {sources.length > 0 && (
            <Paper
              variant="outlined"
              sx={{
                p: 2, borderRadius: 2,
                bgcolor: (t) => alpha(t.palette.primary.main, 0.05),
                borderColor: (t) => alpha(t.palette.primary.main, 0.2),
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: preview ? 1.25 : 0 }}>
                <LayersIcon fontSize="small" color="primary" />
                <Typography variant="subtitle2" fontWeight={700}>{t('builder.preview')}</Typography>
                {previewLoading && <CircularProgress size={14} sx={{ ml: 0.5 }} />}
              </Box>
              {preview ? (
                <>
                  <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                    <Chip size="small" variant="outlined" label={t('builder.sourcesCount', { count: preview.totals.sources })} />
                    <Chip size="small" variant="outlined" label={t('builder.registresCount', { count: preview.totals.registres, val: preview.totals.registres.toLocaleString('fr-FR') })} />
                    <Chip size="small" color="primary" label={t('builder.pagesToIndex', { count: preview.totals.pages, val: preview.totals.pages.toLocaleString('fr-FR') })} />
                  </Box>
                  {preview.warnings.map((w, idx) => (
                    <Alert key={idx} severity="warning" sx={{ mt: 1, py: 0 }}>{w}</Alert>
                  ))}
                </>
              ) : (
                <Typography variant="body2" color="text.disabled">{t('builder.computingPreview')}</Typography>
              )}
            </Paper>
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
        <Button onClick={onClose} disabled={submitting}>{t('common:actions.cancel')}</Button>
        <Button
          variant="contained"
          disableElevation
          startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : (mode === 'edit' ? <SyncIcon /> : <AddIcon />)}
          onClick={() => onSubmit(name.trim(), sources)}
          disabled={!canSubmit}
        >
          {submitting ? t('common:actions.saving') : (mode === 'edit' ? t('builder.saveRebuild') : t('builder.generate'))}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function IndexesPage() {
  const { t, i18n } = useTranslation(['indexes', 'common']);
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const [indexes, setIndexes] = useState<IndexMetadata[]>([]);
  const [collections, setCollections] = useState<CollectionMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [searchFilter, setSearchFilter] = useState('');

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [indexToDelete, setIndexToDelete] = useState<string | null>(null);

  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderMode, setBuilderMode] = useState<'create' | 'edit'>('create');
  const [editing, setEditing] = useState<IndexMetadata | null>(null);

  const navigate = useNavigate();

  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set());
  const [pausingIds, setPausingIds] = useState<Set<string>>(new Set());
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());
  const [updatesById, setUpdatesById] = useState<Record<string, { new_registres: number; new_pages: number }>>({});

  const { runningTasks, pausedTasks, interruptedTasks, queuedTasks, hasActivity, cancel: cancelTask, pause: pauseTask, resume: resumeTask, refresh: refreshTasks } = useTasks();

  const loadUpdates = useCallback(async () => {
    try {
      const ups = await indexesApi.getUpdates();
      setUpdatesById(Object.fromEntries(ups.map(u => [u.id, u])));
    } catch {
      /* non bloquant : pas d'info de fraîcheur */
    }
  }, []);

  const loadIndexes = useCallback(async () => {
    try {
      setIndexes(await indexesApi.getAll());
      loadUpdates();
    } catch {
      setError(t('errors.load'));
    }
  }, [loadUpdates]);

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      try {
        const [cols] = await Promise.all([collectionsApi.getAll(), loadIndexes()]);
        setCollections(cols);
      } finally {
        setLoading(false);
      }
    };
    init();
  }, [loadIndexes]);

  // Tant qu'une tâche tourne, rafraîchir la liste (indexations qui démarrent / se terminent).
  useEffect(() => {
    if (!hasActivity) return;
    const id = setInterval(loadIndexes, 2000);
    return () => clearInterval(id);
  }, [hasActivity, loadIndexes]);

  const openCreate = () => { setBuilderMode('create'); setEditing(null); setBuilderOpen(true); };
  const openEdit = (index: IndexMetadata) => { setBuilderMode('edit'); setEditing(index); setBuilderOpen(true); };

  const handleBuilderSubmit = async (name: string, sources: IndexSource[]) => {
    setSubmitting(true); setError(null); setSuccess(null);
    try {
      if (builderMode === 'edit' && editing) {
        await indexesApi.update(editing.id, { name, sources });
        setSuccess(t('success.updated'));
      } else {
        await indexesApi.generate({ name, sources });
        setSuccess(t('success.queued'));
      }
      await refreshTasks();
      await loadIndexes();
      setBuilderOpen(false);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? t('errors.generate'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRegenerate = async (index: IndexMetadata) => {
    setRegeneratingIds(prev => new Set([...prev, index.id]));
    setError(null); setSuccess(null);
    try {
      await indexesApi.regenerate(index.id);
      await refreshTasks();
      await loadIndexes();
      setSuccess(t('success.rebuildQueued'));
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? t('errors.rebuild'));
    } finally {
      setRegeneratingIds(prev => { const s = new Set(prev); s.delete(index.id); return s; });
    }
  };

  const handleCancelGeneration = async (indexId: string) => {
    setCancellingIds(prev => new Set(prev).add(indexId));   // retour visuel immédiat
    try {
      // Une indexation (en cours, en attente, en pause ou interrompue) = une tâche dont
      // index_id == cet index → on l'annule (le nettoyage index partiel/build est fait côté serveur).
      const task = [...runningTasks, ...queuedTasks, ...pausedTasks, ...interruptedTasks]
        .find(t => t.type === 'index' && t.index_id === indexId);
      if (task) {
        await cancelTask(task.id);
      } else {
        await indexesApi.delete(indexId);  // repli si aucune tâche associée
      }
      await loadIndexes();
      setSuccess(t('success.cancelled'));
    } catch {
      setError(t('errors.cancel'));
    } finally {
      setCancellingIds(prev => { const s = new Set(prev); s.delete(indexId); return s; });
    }
  };

  const handlePauseGeneration = async (indexId: string) => {
    const task = runningTasks.find(t => t.type === 'index' && t.index_id === indexId);
    if (!task) return;
    setPausingIds(prev => new Set(prev).add(indexId));   // retour visuel immédiat
    try {
      await pauseTask(task.id);
      setSuccess(t('success.pausing'));
    } catch {
      setError(t('errors.pause'));
    }
  };

  const handleResumeGeneration = async (indexId: string) => {
    const task = pausedTasks.find(t => t.type === 'index' && t.index_id === indexId)
      ?? interruptedTasks.find(t => t.type === 'index' && t.index_id === indexId);
    if (!task) return;
    try {
      await resumeTask(task.id);
      await loadIndexes();
      setSuccess(t('success.resumed'));
    } catch {
      setError(t('errors.resume'));
    }
  };

  // « Mise en pause… » tant que la tâche d'indexation tourne encore (pause coopérative).
  useEffect(() => {
    const runningIndexIds = new Set(
      runningTasks.filter(t => t.type === 'index' && t.index_id).map(t => t.index_id as string)
    );
    setPausingIds(prev => {
      const next = new Set([...prev].filter(id => runningIndexIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [runningTasks]);

  const handleDeleteClick = (indexId: string) => { setIndexToDelete(indexId); setDeleteDialogOpen(true); };

  const handleDeleteConfirm = async () => {
    if (!indexToDelete) return;
    try {
      await indexesApi.delete(indexToDelete);
      setIndexes(prev => prev.filter(i => i.id !== indexToDelete));
      setSuccess(t('success.deleted'));
    } catch {
      setError(t('errors.delete'));
    } finally {
      setDeleteDialogOpen(false);
      setIndexToDelete(null);
    }
  };

  // ── Liste filtrée : recherche sur le nom, les collections et les modèles ──
  const visibleIndexes = useMemo(() => {
    const q = norm(searchFilter.trim());
    if (!q) return indexes;
    return indexes.filter(idx => {
      const parts = [idx.name ?? '', idx.id, ...sourcesLabels(idx, collections).flatMap(s => [s.label, s.sub])];
      return norm(parts.join(' ')).includes(q);
    });
  }, [indexes, collections, searchFilter]);

  const editingSources: IndexSource[] = editing?.sources
    ? editing.sources.map(s => ({ collection_id: s.collection_id, model_name: s.model_name }))
    : (editing && (editing.collection_id || editing.model_name)
        ? [{ collection_id: editing.collection_id ?? '', model_name: editing.model_name ?? '' }]
        : []);

  if (loading) return <Loader message={t('loading')} minHeight="60vh" />;

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
      {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

      {/* ── Barre d'outils : titre + recherche + actions ── */}
      <Box sx={{ mb: 2, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h6" fontWeight={700} sx={{ display: 'flex', alignItems: 'baseline', gap: 1 }}>
          {t('list.title')}
          {indexes.length > 0 && (
            <Typography component="span" variant="subtitle1" color="text.secondary" fontWeight={500}>
              ({indexes.length})
            </Typography>
          )}
        </Typography>

        {indexes.length > 0 && (
          <TextField
            size="small"
            placeholder={t('list.searchPlaceholder')}
            value={searchFilter}
            onChange={e => setSearchFilter(e.target.value)}
            sx={{ width: 340, ml: 'auto' }}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
                endAdornment: searchFilter ? (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={() => setSearchFilter('')} edge="end">
                      <ClearIcon fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ) : undefined,
              },
            }}
          />
        )}

        <Tooltip title={t('list.refreshTooltip')} arrow>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={loadIndexes}
            sx={indexes.length > 0 ? undefined : { ml: 'auto' }}
          >
            {t('list.refresh')}
          </Button>
        </Tooltip>
        <Button
          variant="contained"
          disableElevation
          startIcon={<AddIcon />}
          onClick={openCreate}
        >
          {t('builder.createTitle')}
        </Button>
      </Box>

      {indexes.length === 0 ? (
        <EmptyState
          icon={<StorageIcon />}
          title={t('list.emptyTitle')}
          description={t('list.emptyDesc')}
          action={{ label: t('builder.createTitle'), onClick: openCreate }}
        />
      ) : visibleIndexes.length === 0 ? (
        <Box sx={{ textAlign: 'center', mt: 10, color: 'text.disabled' }}>
          <StorageIcon sx={{ fontSize: 56, opacity: 0.2, mb: 1 }} />
          <Typography variant="body2" color="text.disabled">
            {t('list.noMatch')}
          </Typography>
        </Box>
      ) : (
        <Paper elevation={0} sx={{ border: '1.5px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: 'grey.50' }}>
                  <TableCell sx={HEADER_CELL}>{t('table.index')}</TableCell>
                  <TableCell sx={HEADER_CELL}>{t('table.composition')}</TableCell>
                  <TableCell sx={HEADER_CELL}>{t('table.wordsProgress')}</TableCell>
                  <TableCell sx={HEADER_CELL}>{t('table.createdAt')}</TableCell>
                  <TableCell sx={HEADER_CELL}>{t('table.status')}</TableCell>
                  <TableCell align="right" sx={HEADER_CELL} />
                </TableRow>
              </TableHead>
              <TableBody>
                {visibleIndexes.map((index) => {
                  const rebuild = !!index.build;                       // reconstruction (index encore consultable)
                  const isGenerating = index.status === 'generating' || rebuild;
                  const progress = index.build?.progress ?? index.progress;
                  const pausedTask = pausedTasks.find(t => t.type === 'index' && t.index_id === index.id);
                  const isPaused = !!pausedTask;
                  const interruptedTask = interruptedTasks.find(t => t.type === 'index' && t.index_id === index.id);
                  const isInterrupted = isGenerating && !isPaused && !!interruptedTask;
                  const isRunning = runningTasks.some(t => t.type === 'index' && t.index_id === index.id);
                  // En file d'attente : matérialisé (generating/build) mais aucune tâche encore active pour lui.
                  const isQueued = isGenerating && !isRunning && !isPaused && !isInterrupted;
                  const isRegenerating = regeneratingIds.has(index.id);
                  const actionsAlwaysVisible = isGenerating || isRegenerating;
                  const labels = sourcesLabels(index, collections);
                  return (
                    <TableRow
                      key={index.id}
                      hover={!isGenerating}
                      sx={{ '&:hover .row-actions': { opacity: 1 } }}
                    >
                      {/* Nom de l'index */}
                      <TableCell sx={{ maxWidth: 220 }}>
                        <Typography variant="body2" fontWeight={600} noWrap title={index.name ?? index.id}>
                          {index.name ?? index.id}
                        </Typography>
                        <Typography variant="caption" color="text.disabled" sx={{ fontFamily: 'monospace' }} noWrap>
                          {index.id}
                        </Typography>
                      </TableCell>

                      {/* Composition (collections · modèles) */}
                      <TableCell sx={{ maxWidth: 280 }}>
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                          {labels.slice(0, 4).map((s, i) => (
                            <Tooltip key={i} title={`${s.label} · ${s.sub}`} arrow>
                              <Chip
                                size="small"
                                variant="outlined"
                                label={<Typography variant="caption" noWrap sx={{ maxWidth: 180 }}>{s.label} · {s.sub}</Typography>}
                                sx={{ maxWidth: 200 }}
                              />
                            </Tooltip>
                          ))}
                          {labels.length > 4 && (
                            <Chip size="small" variant="outlined" label={`+${labels.length - 4}`} />
                          )}
                        </Box>
                      </TableCell>

                      {/* Mots / Progression */}
                      <TableCell sx={{ minWidth: 200 }}>
                        {isGenerating ? (
                          isQueued ? (
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, color: 'text.disabled' }}>
                              <ScheduleIcon sx={{ fontSize: 16 }} />
                              <Typography variant="caption">{t('progress.queued')}</Typography>
                            </Box>
                          ) : (
                            <IndexProgress progress={progress} rebuild={rebuild} />
                          )
                        ) : index.stats ? (
                          <Box>
                            <Typography variant="body2" fontWeight={500}>
                              {t('list.wordsCount', { count: index.stats.total_unique_words, val: index.stats.total_unique_words.toLocaleString(locale) })}
                            </Typography>
                            <Typography variant="caption" color="text.disabled">
                              {t('list.registresCount', { count: index.stats.registres_count })} · {t('list.occAbbr', { val: index.stats.total_word_occurrences.toLocaleString(locale) })}
                            </Typography>
                            {updatesById[index.id] && (
                              <Tooltip title="De nouvelles transcriptions OCR sont disponibles depuis l'indexation. Cliquez pour reconstruire l'index et les inclure." arrow>
                                <Chip
                                  size="small"
                                  color="warning"
                                  variant="outlined"
                                  icon={<SyncIcon sx={{ fontSize: '13px !important' }} />}
                                  onClick={() => handleRegenerate(index)}
                                  label={(() => {
                                    const u = updatesById[index.id];
                                    const parts = [];
                                    if (u.new_pages > 0) parts.push(t('list.newPages', { count: u.new_pages, val: u.new_pages.toLocaleString(locale) }));
                                    if (u.new_registres > 0) parts.push(t('list.newRegistres', { count: u.new_registres }));
                                    return `${parts.join(' · ')} ${t('list.toIndex')}`;
                                  })()}
                                  sx={{ mt: 0.5, cursor: 'pointer', fontWeight: 500 }}
                                />
                              </Tooltip>
                            )}
                          </Box>
                        ) : (
                          <Typography variant="body2" color="text.disabled">—</Typography>
                        )}
                      </TableCell>

                      {/* Date */}
                      <TableCell sx={{ color: 'text.secondary', fontSize: '0.8rem', whiteSpace: 'nowrap' }}>
                        {index.created_at
                          ? new Date(index.created_at).toLocaleString(locale, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
                          : '—'}
                      </TableCell>

                      {/* Statut */}
                      <TableCell>
                        {isPaused ? (
                          <Chip icon={<PauseIcon sx={{ fontSize: '14px !important' }} />} label={t('status.paused')} color="info" size="small" variant="outlined" sx={{ fontWeight: 500 }} />
                        ) : isInterrupted ? (
                          <Chip icon={<WarningIcon sx={{ fontSize: '14px !important' }} />} label={t('status.interrupted')} color="warning" size="small" variant="outlined" sx={{ fontWeight: 500 }} />
                        ) : isQueued ? (
                          <Chip icon={<ScheduleIcon sx={{ fontSize: '14px !important' }} />} label={t('status.waiting')} color="default" size="small" variant="outlined" sx={{ fontWeight: 500 }} />
                        ) : rebuild ? (
                          <Chip icon={<SyncIcon sx={{ fontSize: '14px !important' }} />} label={t('status.rebuilding')} color="info" size="small" variant="outlined" sx={{ fontWeight: 500 }} />
                        ) : (
                          <StatusChip status={index.status} />
                        )}
                      </TableCell>

                      {/* Actions */}
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        <Box
                          className={actionsAlwaysVisible ? undefined : 'row-actions'}
                          sx={{ display: 'inline-flex', gap: 0.5, justifyContent: 'flex-end', opacity: actionsAlwaysVisible ? 1 : 0, transition: 'opacity 0.15s' }}
                        >
                          {isGenerating ? (
                            <>
                              {/* Un index en reconstruction reste consultable */}
                              {rebuild && index.status === 'ready' && (
                                <Tooltip title={t('actions.viewCurrent')} arrow>
                                  <IconButton size="small" onClick={() => navigate(`/indexes/${index.id}`)}>
                                    <VisibilityIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              )}
                              {isPaused || isInterrupted ? (
                                <Tooltip title={isInterrupted ? t('actions.resumeInterrupted') : t('actions.resume')} arrow>
                                  <IconButton size="small" color="primary" onClick={() => handleResumeGeneration(index.id)}>
                                    <PlayArrowIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              ) : pausingIds.has(index.id) ? (
                                <Tooltip title={t('actions.pausing')} arrow>
                                  <span><IconButton size="small" disabled><CircularProgress size={16} /></IconButton></span>
                                </Tooltip>
                              ) : runningTasks.some(t => t.type === 'index' && t.index_id === index.id) && (
                                <Tooltip title={t('actions.pause')} arrow>
                                  <IconButton size="small" color="primary" onClick={() => handlePauseGeneration(index.id)}>
                                    <PauseIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              )}
                              {cancellingIds.has(index.id) ? (
                                <Tooltip title={t('actions.cancelling')} arrow>
                                  <span><IconButton size="small" disabled><CircularProgress size={16} /></IconButton></span>
                                </Tooltip>
                              ) : (
                                <Tooltip title={t('actions.cancelGeneration')} arrow>
                                  <IconButton size="small" color="warning" onClick={() => handleCancelGeneration(index.id)}>
                                    <CancelIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              )}
                            </>
                          ) : (
                            <>
                              {index.status === 'ready' && (
                                <Tooltip title={t('actions.view')} arrow>
                                  <IconButton size="small" onClick={() => navigate(`/indexes/${index.id}`)}>
                                    <VisibilityIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              )}
                              <Tooltip title={t('actions.edit')} arrow>
                                <span>
                                  <IconButton size="small" onClick={() => openEdit(index)} disabled={isRegenerating}>
                                    <EditIcon fontSize="small" />
                                  </IconButton>
                                </span>
                              </Tooltip>
                              <Tooltip title={updatesById[index.id] ? t('actions.update') : t('actions.rebuild')} arrow>
                                <span>
                                  <IconButton size="small" color={updatesById[index.id] ? 'warning' : 'primary'} onClick={() => handleRegenerate(index)} disabled={isRegenerating}>
                                    {isRegenerating ? <CircularProgress size={16} /> : <SyncIcon fontSize="small" />}
                                  </IconButton>
                                </span>
                              </Tooltip>
                              <Tooltip title={t('actions.delete')} arrow>
                                <span>
                                  <IconButton size="small" color="error" onClick={() => handleDeleteClick(index.id)} disabled={isRegenerating}>
                                    <DeleteIcon fontSize="small" />
                                  </IconButton>
                                </span>
                              </Tooltip>
                            </>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      {/* ── Dialog : Constructeur (création / édition) ─────────── */}
      <IndexBuilderDialog
        open={builderOpen}
        mode={builderMode}
        collections={collections}
        initialName={builderMode === 'edit' ? (editing?.name ?? '') : ''}
        initialSources={builderMode === 'edit' ? editingSources : []}
        submitting={submitting}
        onClose={() => setBuilderOpen(false)}
        onSubmit={handleBuilderSubmit}
      />

      {/* ── Dialog : Supprimer ──────────────────────────────── */}
      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)} maxWidth="sm" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ pb: 0.5 }}>{t('deleteDialog.title')}</DialogTitle>
        <DialogContent>
          <Typography sx={{ mt: 1 }}>
            <Trans t={t} i18nKey="deleteDialog.confirm" components={{ strong: <strong /> }}
              values={{ name: indexes.find(i => i.id === indexToDelete)?.name ?? indexToDelete }} />
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          <Button onClick={() => setDeleteDialogOpen(false)}>{t('common:actions.cancel')}</Button>
          <Button onClick={handleDeleteConfirm} color="error" variant="contained" disableElevation>{t('actions.delete')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
