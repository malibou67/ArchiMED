import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
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
  Menu,
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
  MoreVert as MoreVertIcon,
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
  IndexUpdates,
  CollectionMetadata,
} from '../types';
import { Task, TaskControlResult } from '../api/tasks';
import { useTasks } from '../context/TasksContext';
import TaskActionOverlay from '../components/TaskActionOverlay';
import { usePendingTaskAction } from '../components/usePendingTaskAction';
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

// Ce qui compte pendant une indexation : la page en train d'être lue et le décompte x / y.
// Le registre (collection · modèle · registre) ne tient pas sur la ligne et n'apprend rien de
// plus à chaque rafraîchissement : il reste dans l'infobulle.
function IndexProgress({ progress, rebuild, locale }: { progress?: IndexProgressType; rebuild?: boolean; locale: string }) {
  const { t } = useTranslation(['indexes', 'common']);
  const p = progress;
  // Étape préparatoire annoncée par le serveur (parcours des registres, relecture de l'index
  // existant) : elle n'a pas encore de décompte, mais dure — la nommer vaut mieux qu'une barre
  // muette. Les étapes de **fin** portent le même champ mais arrivent avec des compteurs
  // complets : elles passent par la branche déterminée plus bas, qui garde la barre pleine.
  if (p?.phase && !p.total) {
    return (
      <Box>
        <LinearProgress variant="indeterminate" color={rebuild ? 'info' : 'warning'} sx={{ height: 5, borderRadius: 3, mb: 0.5 }} />
        <Typography variant="caption" color="text.disabled">
          {t(`common:indexPhase.${p.phase}`)}
        </Typography>
      </Box>
    );
  }
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
    const counts = { done: p.processed.toLocaleString(locale), total: p.total.toLocaleString(locale) };
    return (
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <LinearProgress variant="determinate" value={pct} color={rebuild ? 'info' : 'warning'} sx={{ flex: 1, height: 5, borderRadius: 3 }} />
          <Typography variant="caption" fontWeight={600} color={rebuild ? 'info.dark' : 'warning.dark'} sx={{ minWidth: 32 }}>{pct} %</Typography>
        </Box>
        <Tooltip title={p.current_registre ?? ''} arrow disableHoverListener={!p.current_registre}>
          <Typography variant="caption" color="text.disabled" noWrap sx={{ display: 'block', maxWidth: 260 }}>
            {/* Une étape de fin remplace la page lue : il n'y en a plus, et c'est elle qu'on attend. */}
            {p.phase
              ? `${t('progress.pages', counts)} · ${t(`common:indexPhase.${p.phase}`)}`
              : p.current_page
                ? t('progress.pageCount', { page: p.current_page, ...counts })
                : t('progress.pages', counts)}
          </Typography>
        </Tooltip>
      </Box>
    );
  }
  return <LinearProgress variant="indeterminate" color={rebuild ? 'info' : 'warning'} sx={{ height: 5, borderRadius: 3 }} />;
}

// Part de l'OCR disponible réellement présente dans l'index. Porte à elle seule ce qui reste à
// indexer : le nombre de pages manquantes se lit dans le ratio, un badge séparé ne ferait que
// répéter la même information. `onIndex` (fourni quand il y a du nouveau) la rend cliquable.
// Composant local : les trois autres indicateurs de couverture de l'app (CollectionsPage,
// QualityCard, CollectionStatsDashboard) sont eux aussi locaux — les factoriser est un chantier
// à part. On en reprend en revanche la règle du « pas de 100 % trompeur par arrondi ».
function IndexCoverage({ updates, locale, onIndex }: {
  updates?: IndexUpdates;
  locale: string;
  onIndex?: () => void;
}) {
  const { t } = useTranslation('indexes');
  if (!updates) return null;
  const fmtN = (n: number) => n.toLocaleString(locale);

  if (!updates.coverage_known) {
    return (
      <Tooltip title={t('list.coverageUnknownTooltip')} arrow>
        <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 0.25, cursor: 'help' }}>
          {t('list.coverageUnknown')}
        </Typography>
      </Tooltip>
    );
  }

  const ocr = updates.ocr_pages ?? 0;
  const indexed = updates.indexed_pages ?? 0;
  if (!ocr) return null;   // aucune page OCR connue : la ligne mots/registres suffit

  const complete = indexed >= ocr;
  const pct = complete ? 100 : Math.min(99, Math.round((indexed / ocr) * 100));

  const tooltip = (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
      <Typography variant="caption" fontWeight={700}>{t('list.coverageTooltipTitle')}</Typography>
      {updates.sources.map((s, i) => {
        const src = s.collection_titre || s.collection_folder;
        if (!s.resolved) {
          return <span key={i}>{t('list.coverageSourceMissing', { source: src, model: s.model_name })}</span>;
        }
        const sOcr = s.ocr_pages ?? 0;
        const sIdx = s.indexed_pages ?? 0;
        const sPct = sIdx >= sOcr ? 100 : sOcr > 0 ? Math.min(99, Math.round((sIdx / sOcr) * 100)) : 0;
        return (
          <span key={i}>
            {t('list.coverageSource', { source: src, model: s.model_name, val: fmtN(sIdx), total: fmtN(sOcr), pct: sPct })}
          </span>
        );
      })}
      {updates.stale_pages > 0 && (
        <span>{t('list.coverageStale', { count: updates.stale_pages, val: fmtN(updates.stale_pages) })}</span>
      )}
      {onIndex && (
        <Typography variant="caption" fontWeight={600} sx={{ mt: 0.5 }}>
          {[
            updates.new_pages > 0 && t('list.newPages', { count: updates.new_pages, val: fmtN(updates.new_pages) }),
            updates.new_registres > 0 && t('list.newRegistres', { count: updates.new_registres }),
          ].filter(Boolean).join(' · ')} {t('list.toIndex')} — {t('list.coverageClickHint')}
        </Typography>
      )}
      <Typography variant="caption" color="text.disabled" sx={{ mt: 0.5 }}>
        {t(updates.rescanned ? 'list.coverageFreshnessScanned' : 'list.coverageFreshness')}
      </Typography>
    </Box>
  );

  return (
    <Tooltip title={tooltip} arrow>
      <Box
        onClick={onIndex}
        sx={{ mt: 0.25, cursor: onIndex ? 'pointer' : 'help' }}
      >
        <Typography variant="caption" color={onIndex ? 'warning.dark' : 'text.secondary'} fontWeight={onIndex ? 600 : 400}>
          {t('list.coverage', { val: fmtN(indexed), total: fmtN(ocr), pct })}
        </Typography>
        {/* Pas de barre quand tout est indexé : une barre pleine sur chaque ligne n'est que du
            bruit, et son absence rend un index incomplet immédiatement repérable. */}
        {!complete && (
          <LinearProgress
            variant="determinate"
            value={pct}
            color={onIndex ? 'warning' : 'primary'}
            sx={{ height: 4, borderRadius: 2, mt: 0.25 }}
          />
        )}
      </Box>
    </Tooltip>
  );
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
  const { t, i18n } = useTranslation(['indexes', 'common', 'tasks']);
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

  // Menu « autres actions » d'une ligne + confirmation de reconstruction complète (opération
  // longue : elle relit toutes les pages, contrairement à la mise à jour).
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [menuIndexId, setMenuIndexId] = useState<string | null>(null);
  const [fullRebuildTarget, setFullRebuildTarget] = useState<IndexMetadata | null>(null);

  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderMode, setBuilderMode] = useState<'create' | 'edit'>('create');
  const [editing, setEditing] = useState<IndexMetadata | null>(null);

  const navigate = useNavigate();

  const [regeneratingIds, setRegeneratingIds] = useState<Set<string>>(new Set());
  const [pausingIds, setPausingIds] = useState<Set<string>>(new Set());
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());
  const [updatesById, setUpdatesById] = useState<Record<string, IndexUpdates>>({});
  const [refreshing, setRefreshing] = useState(false);

  const { runningTasks, pausedTasks, interruptedTasks, queuedTasks, hasActivity, cancel: cancelTask, pause: pauseTask, resume: resumeTask, refresh: refreshTasks } = useTasks();

  // Index dont une tâche d'indexation s'occupe encore, quel que soit son état. Un index marqué
  // « en reconstruction » qui n'en fait pas partie est orphelin : plus rien ne le fera avancer.
  const liveIndexTaskIds = useMemo(
    () => new Set(
      [...runningTasks, ...queuedTasks, ...pausedTasks, ...interruptedTasks]
        .filter(t => t.type === 'index' && t.index_id)
        .map(t => t.index_id as string),
    ),
    [runningTasks, queuedTasks, pausedTasks, interruptedTasks],
  );

  // Overlay bloquant tant que l'action cliquée n'a pas pris effet : la pause d'une indexation
  // attend la fin du registre en cours, ce qui peut prendre un moment.
  const {
    pending, overlayOpen, start: startAction, update: updateAction,
    done: doneAction, hide: hideAction,
  } = usePendingTaskAction();

  useEffect(() => {
    if (!pending) return;
    const suivi = (liste: Task[]) => liste.some(t => t.type === 'index' && t.index_id === pending.id);
    const abouti =
      pending.kind === 'pause' ? !suivi(runningTasks)
        : pending.kind === 'resume' ? !suivi(pausedTasks) && !suivi(interruptedTasks)
          : pending.kind === 'cancel' ? !liveIndexTaskIds.has(pending.id)
            // 'delete' et 'start' : rien à observer, résolues à la main au retour de l'appel.
            : false;
    if (abouti) doneAction();
  }, [pending, runningTasks, pausedTasks, interruptedTasks, liveIndexTaskIds, doneAction]);

  // `rescan` : relire réellement les dossiers OCR au lieu des compteurs publiés. Réservé à une
  // action explicite de l'utilisateur (bouton Actualiser), car c'est plusieurs secondes.
  const loadUpdates = useCallback(async (rescan = false) => {
    try {
      const ups = await indexesApi.getUpdates(rescan);
      setUpdatesById(Object.fromEntries(ups.map(u => [u.id, u])));
    } catch {
      /* non bloquant : pas d'info de couverture */
    }
  }, []);

  const loadIndexes = useCallback(async (rescan = false) => {
    try {
      setIndexes(await indexesApi.getAll());
      loadUpdates(rescan);
    } catch {
      setError(t('errors.load'));
    }
  }, [loadUpdates]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([indexesApi.getAll().then(setIndexes), loadUpdates(true)]);
    } catch {
      setError(t('errors.load'));
    } finally {
      setRefreshing(false);
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

  // La dernière tâche vient de se terminer : l'intervalle ci-dessus s'arrête sans avoir relu les
  // index, qui porteraient encore leur `build` — la ligne resterait affichée « En attente »
  // jusqu'à un rechargement manuel. Un dernier chargement suffit : le serveur écrit
  // status/stats de l'index avant de passer la tâche à 'done'.
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !hasActivity) loadIndexes();
    wasActive.current = hasActivity;
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

  // `full` : réindexe tous les registres au lieu des seuls nouveaux/modifiés.
  //
  // La ligne bascule en « démarrage » AVANT l'appel : l'enfilage écrit sur le partage et la
  // réponse peut tarder plusieurs secondes, pendant lesquelles le clic semblait sans effet.
  // Le marqueur ne retombe qu'une fois la liste relue — l'index porte alors son `build` (ou
  // a déjà fini), donc la ligne ne repasse jamais par un « Prêt » fugace.
  const handleRegenerate = async (index: IndexMetadata, full = false) => {
    setRegeneratingIds(prev => new Set([...prev, index.id]));
    // Même raison que le marqueur de ligne, en plus visible : l'enfilage écrit sur le partage et
    // peut tarder. L'attente est ici celle de l'appel — la tâche n'existe pas encore, il n'y a rien
    // à observer —, d'où le `done()` du `finally`. Ce bouton sert aussi de « reprendre » à une
    // reconstruction orpheline, dont plus aucune tâche ne s'occupe.
    startAction({
      kind: 'start', id: index.id,
      title: t('overlay.queueing'), detail: t('overlay.queueingDetail'),
    });
    setError(null); setSuccess(null);
    try {
      await indexesApi.regenerate(index.id, { full });
      setSuccess(t(full ? 'success.fullRebuildQueued' : 'success.updateQueued'));
      await Promise.all([refreshTasks(), loadIndexes()]);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? t('errors.rebuild'));
    } finally {
      doneAction();
      setRegeneratingIds(prev => { const s = new Set(prev); s.delete(index.id); return s; });
    }
  };

  // Tâche d'un autre poste : la commande lui a seulement été transmise, elle ne prendra effet
  // qu'à son prochain relevé (~5 s). L'overlay doit le dire, sinon l'attente est incompréhensible.
  const noterPosteDistant = (result: TaskControlResult) => {
    if (result.requested) updateAction({ machine: result.machine_label || t('tasks:otherMachine') });
  };

  const handleCancelGeneration = async (indexId: string) => {
    setCancellingIds(prev => new Set(prev).add(indexId));   // retour visuel immédiat
    startAction({ kind: 'cancel', id: indexId, taskType: 'index' });
    // Une tâche a été priée de s'arrêter : l'arrêt est coopératif et prend plusieurs secondes,
    // le retour visuel doit donc tenir jusqu'à ce qu'elle disparaisse (useEffect plus bas).
    let attendLaTache = false;
    try {
      // Une indexation (en cours, en attente, en pause ou interrompue) = une tâche dont
      // index_id == cet index → on l'annule (le nettoyage index partiel/build est fait côté serveur).
      const task = [...runningTasks, ...queuedTasks, ...pausedTasks, ...interruptedTasks]
        .find(t => t.type === 'index' && t.index_id === indexId);
      if (task) {
        noterPosteDistant(await cancelTask(task.id));
        attendLaTache = true;
      } else {
        // Aucune tâche associée : reconstruction orpheline (tâche purgée, échouée avant son
        // runner, ou perdue le temps d'un hoquet du partage). `abortBuild` retire le marqueur
        // en CONSERVANT l'index précédent ; c'est `delete` — qui effaçait tout le dossier —
        // qui était appelé ici, et une annulation faisait donc perdre l'index.
        await indexesApi.abortBuild(indexId);
      }
      await loadIndexes();
      setSuccess(t('success.cancelled'));
    } catch {
      setError(t('errors.cancel'));
      attendLaTache = false;
    } finally {
      // Sans tâche à attendre (repli `abortBuild`, ou échec), rien ne viendra fermer l'overlay ni
      // retirer le marqueur de ligne : on s'en charge ici.
      if (!attendLaTache) {
        doneAction();
        setCancellingIds(prev => { const s = new Set(prev); s.delete(indexId); return s; });
      }
    }
  };

  const handlePauseGeneration = async (indexId: string) => {
    const task = runningTasks.find(t => t.type === 'index' && t.index_id === indexId);
    if (!task) return;
    setPausingIds(prev => new Set(prev).add(indexId));   // retour visuel immédiat
    startAction({ kind: 'pause', id: indexId, taskType: 'index' });
    try {
      noterPosteDistant(await pauseTask(task.id));
      setSuccess(t('success.pausing'));
    } catch {
      doneAction();
      setError(t('errors.pause'));
    }
  };

  const handleResumeGeneration = async (indexId: string) => {
    const task = pausedTasks.find(t => t.type === 'index' && t.index_id === indexId)
      ?? interruptedTasks.find(t => t.type === 'index' && t.index_id === indexId);
    if (!task) return;
    startAction({ kind: 'resume', id: indexId, taskType: 'index' });
    try {
      noterPosteDistant(await resumeTask(task.id));
      await loadIndexes();
      setSuccess(t('success.resumed'));
    } catch {
      doneAction();
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

  // « Annulation… » tant que la tâche n'a pas disparu : l'annulation est coopérative et prend
  // plusieurs secondes. Rendre la main aussitôt faisait recliquer l'utilisateur, et le second
  // clic — la tâche ayant alors disparu — partait sur le repli.
  useEffect(() => {
    setCancellingIds(prev => {
      if (!prev.size) return prev;
      const next = new Set([...prev].filter(id => liveIndexTaskIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [liveIndexTaskIds]);

  const handleDeleteClick = (indexId: string) => { setIndexToDelete(indexId); setDeleteDialogOpen(true); };

  const closeRowMenu = () => { setMenuAnchor(null); setMenuIndexId(null); };

  const handleFullRebuildConfirm = async () => {
    const target = fullRebuildTarget;
    setFullRebuildTarget(null);
    if (target) await handleRegenerate(target, true);
  };

  const handleDeleteConfirm = async () => {
    if (!indexToDelete) return;
    // Suppression d'un index, pas d'une tâche : l'overlay porte ses propres libellés, et son issue
    // est celle de l'appel — rien à observer dans l'état de la page, d'où le `done()` explicite.
    startAction({
      kind: 'delete', id: indexToDelete,
      title: t('overlay.deletingIndex'), detail: t('overlay.deletingIndexDetail'),
    });
    try {
      await indexesApi.delete(indexToDelete);
      setIndexes(prev => prev.filter(i => i.id !== indexToDelete));
      setSuccess(t('success.deleted'));
    } catch {
      setError(t('errors.delete'));
    } finally {
      doneAction();
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

        {/* Seule action qui relit réellement les dossiers OCR : ailleurs on se fie aux compteurs
            publiés, qui peuvent ignorer des XML déposés hors de l'application. */}
        <Tooltip title={t('list.refreshTooltip')} arrow>
          <Button
            variant="outlined"
            startIcon={refreshing ? <CircularProgress size={16} /> : <RefreshIcon />}
            onClick={handleRefresh}
            disabled={refreshing}
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
                  // Reconstruction = un build par-dessus un index déjà consultable. Une
                  // première génération porte elle aussi un marqueur `build`
                  // (`init_index_new`), mais garde `status: 'generating'` — sans ce garde-fou
                  // la ligne annonçait « Reconstruction… » dès le tout premier index.
                  const rebuild = !!index.build && index.status !== 'generating';
                  const isRegenerating = regeneratingIds.has(index.id);
                  // « Démarrage… » : le POST d'enfilage est parti, la tâche n'existe pas encore.
                  const isGenerating = index.status === 'generating' || rebuild || isRegenerating;
                  const progress = index.build?.progress ?? index.progress;
                  const pausedTask = pausedTasks.find(t => t.type === 'index' && t.index_id === index.id);
                  const isPaused = !!pausedTask;
                  const interruptedTask = interruptedTasks.find(t => t.type === 'index' && t.index_id === index.id);
                  // Reconstruction orpheline : le marqueur `build` est là mais plus aucune tâche
                  // ne s'en occupe (arrêt brutal, tâche supprimée, échec avant son runner). La
                  // ligne restait alors figée sur « Reconstruction… » indéfiniment, sans que rien
                  // ne la rafraîchisse — on l'annonce pour ce qu'elle est : à reprendre ou à
                  // abandonner. `isRegenerating` l'exclut : la tâche n'a simplement pas encore
                  // eu le temps d'apparaître.
                  const isOrphanBuild = rebuild && !isRegenerating && !liveIndexTaskIds.has(index.id);
                  const isInterrupted = isGenerating && !isPaused && (!!interruptedTask || isOrphanBuild);
                  // En file d'attente : matérialisé (generating/build) et une tâche l'attend
                  // réellement. Le déduire de l'absence de toute tâche ferait passer un
                  // instantané périmé (indexation terminée, liste pas encore relue) pour une
                  // attente.
                  const isQueued = isGenerating && queuedTasks.some(t => t.type === 'index' && t.index_id === index.id);
                  const actionsAlwaysVisible = isGenerating || isRegenerating;
                  const labels = sourcesLabels(index, collections);
                  // Une entrée existe désormais pour chaque index prêt (elle porte la couverture) :
                  // le chip d'alerte ne doit s'afficher que s'il y a réellement du nouveau.
                  const updates = updatesById[index.id];
                  const hasNew = !!updates && (updates.new_pages > 0 || updates.new_registres > 0);
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
                            <IndexProgress progress={progress} rebuild={rebuild} locale={locale} />
                          )
                        ) : index.stats ? (
                          <Box>
                            <Typography variant="body2" fontWeight={500}>
                              {t('list.wordsCount', { count: index.stats.total_unique_words, val: index.stats.total_unique_words.toLocaleString(locale) })}
                            </Typography>
                            <Typography variant="caption" color="text.disabled">
                              {t('list.registresCount', { count: index.stats.registres_count })} · {t('list.occAbbr', { val: index.stats.total_word_occurrences.toLocaleString(locale) })}
                            </Typography>
                            <IndexCoverage
                              updates={updates}
                              locale={locale}
                              // `isRegenerating` : sans ce garde-fou un double-clic envoyait deux
                              // enfilages, et le 409 du second remplaçait le message de succès
                              // par une erreur rouge.
                              onIndex={hasNew && !isRegenerating ? () => handleRegenerate(index) : undefined}
                            />
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
                                  {/* Reconstruction orpheline : plus de tâche à reprendre, on en
                                      réenfile une — elle repartira du checkpoint laissé sur place. */}
                                  <IconButton
                                    size="small"
                                    color="primary"
                                    onClick={() => (isOrphanBuild ? handleRegenerate(index) : handleResumeGeneration(index.id))}
                                  >
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
                                <Tooltip title={isOrphanBuild ? t('actions.abandonBuild') : t('actions.cancelGeneration')} arrow>
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
                              <Tooltip title={hasNew ? t('actions.update') : t('actions.updateGeneric')} arrow>
                                <span>
                                  <IconButton size="small" color={hasNew ? 'warning' : 'primary'} onClick={() => handleRegenerate(index)} disabled={isRegenerating}>
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
                              <Tooltip title={t('actions.more')} arrow>
                                <span>
                                  <IconButton
                                    size="small"
                                    onClick={e => { setMenuAnchor(e.currentTarget); setMenuIndexId(index.id); }}
                                    disabled={isRegenerating}
                                  >
                                    <MoreVertIcon fontSize="small" />
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

      {/* ── Menu : autres actions d'une ligne ───────────────── */}
      <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={closeRowMenu}>
        <MenuItem
          onClick={() => {
            setFullRebuildTarget(indexes.find(i => i.id === menuIndexId) ?? null);
            closeRowMenu();
          }}
        >
          <SyncIcon fontSize="small" sx={{ mr: 1.5 }} />
          {t('actions.fullRebuild')}
        </MenuItem>
      </Menu>

      {/* ── Dialog : Reconstruction complète ────────────────── */}
      <Dialog open={Boolean(fullRebuildTarget)} onClose={() => setFullRebuildTarget(null)} maxWidth="sm" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ pb: 0.5 }}>{t('fullRebuildDialog.title')}</DialogTitle>
        <DialogContent>
          <Typography sx={{ mt: 1 }}>
            <Trans t={t} i18nKey="fullRebuildDialog.confirm" components={{ strong: <strong /> }}
              values={{ name: fullRebuildTarget?.name ?? fullRebuildTarget?.id }} />
          </Typography>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          <Button onClick={() => setFullRebuildTarget(null)}>{t('common:actions.cancel')}</Button>
          <Button onClick={handleFullRebuildConfirm} color="warning" variant="contained" disableElevation>{t('fullRebuildDialog.cta')}</Button>
        </DialogActions>
      </Dialog>

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

      <TaskActionOverlay pending={pending} open={overlayOpen} onHide={hideAction} />
    </Box>
  );
}
