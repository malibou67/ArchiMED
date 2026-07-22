import React, { useState, useEffect, useMemo } from 'react';
import {
  Typography,
  Box,
  Button,
  CircularProgress,
  LinearProgress,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  InputAdornment,
  MenuItem,
  TextField,
  Chip,
  IconButton,
  Skeleton,
  Table,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
  TableContainer,
  Tooltip,
  Collapse,
  Paper,
} from '@mui/material';
import {
  Clear as ClearIcon,
  Close as CloseIcon,
  Edit as EditIcon,
  FolderOpen as FolderOpenIcon,
  Search as SearchIcon,
  Sync as SyncIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  FiberNew as FiberNewIcon,
  KeyboardArrowDown as ArrowDownIcon,
  KeyboardArrowRight as ArrowRightIcon,
  ImageSearch as ImageSearchIcon,
  QueryStats as QueryStatsIcon,
  InfoOutlined as InfoIcon,
} from '@mui/icons-material';
import { useNavigate } from 'react-router-dom';
import { useTranslation, Trans } from 'react-i18next';
import type { TFunction } from 'i18next';
import { ImageViewer, FullPageViewer, makePatternRegex, sortPages, pageFamilyFromList, thumbLabel } from '../components/PageImageViewer';
import { collectionsApi } from '../api/collections';
import { registresApi } from '../api/registres';
import { transcriptionsApi } from '../api/transcriptions';
import EmptyState from '../components/EmptyState';
import Loader from '../components/Loader';
import {
  CollectionMetadata,
  CollectionUpdate,
  RegistreSummary,
  RegistreUpdate,
  CollectionScanStatus,
  ScanReport,
  ScanProgress,
  TranscriptionsSummary,
} from '../types';

// ── Helpers ──────────────────────────────────────────────────────────────────

// Normalisation pour la recherche : minuscules, sans diacritiques.
const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

// Anomalies d'un registre dérivables des métadonnées du listing (sans relire le disque).
// Les codes correspondent à ceux du scan, rendus par anomalyInfo/AnomalyChips. Les anomalies
// d'écart disque/metadata (orphelins, absents…) restent du ressort du dialog de scan.
function registreAnomalies(r: RegistreSummary): string[] {
  if (r.pages_count === 0) return ['registre_vide'];
  if (!r.pages_pattern) return ['pagination_indetectable'];
  return [];
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusIcon({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Tooltip title={label} arrow>
      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center' }}>
        {ok
          ? <CheckCircleIcon fontSize="small" color="success" />
          : <CancelIcon fontSize="small" color="error" />}
      </Box>
    </Tooltip>
  );
}

// Libellé FR + sévérité + explication détaillée d'un code d'anomalie (le code peut
// porter un argument après ':'). Le détail est affiché au survol du badge.
function anomalyInfo(code: string, t: TFunction): { label: string; color: 'error' | 'warning'; detail: string } {
  const [kind, arg] = code.split(':');
  switch (kind) {
    case 'metadata_illisible':
      return { label: t('anomaly.metadataUnreadableLabel'), color: 'error', detail: t('anomaly.metadataUnreadableDetail') };
    case 'registre_absent_disque':
      return { label: t('anomaly.absentDiskLabel'), color: 'error', detail: t('anomaly.absentDiskDetail') };
    case 'ocr_orphelin':
      return { label: arg ? t('anomaly.ocrOrphanArg', { arg }) : t('anomaly.ocrOrphan'), color: 'error', detail: t('anomaly.ocrOrphanDetail', { arg: arg ?? '?' }) };
    case 'registre_hors_scans':
      return { label: arg ? t('anomaly.outsideScansArg', { arg }) : t('anomaly.outsideScans'), color: 'error', detail: t('anomaly.outsideScansDetail', { arg: arg ?? '?' }) };
    case 'registre_vide':
      return { label: t('anomaly.emptyLabel'), color: 'warning', detail: t('anomaly.emptyDetail') };
    case 'pagination_indetectable':
      return { label: t('anomaly.paginationLabel'), color: 'warning', detail: t('anomaly.paginationDetail') };
    default:
      return { label: code, color: 'warning', detail: code };
  }
}

function AnomalyChips({ anomalies }: { anomalies: string[] }) {
  const { t } = useTranslation('collections');
  if (!anomalies.length) return null;
  return (
    <Box sx={{ display: 'inline-flex', flexWrap: 'wrap', gap: 0.5, ml: 1, verticalAlign: 'middle' }}>
      {anomalies.map((code) => {
        const { label, color, detail } = anomalyInfo(code, t);
        return (
          <Tooltip key={code} title={detail} arrow>
            <Chip label={label} size="small" color={color} variant="outlined" sx={{ height: 18, fontSize: '0.65rem' }} />
          </Tooltip>
        );
      })}
    </Box>
  );
}

// Chips « N trous » / « N doublons » de pagination, avec la liste des numéros en tooltip.
// Réutilisé sur la ligne du registre (listing) et dans le rapport de scan.
function PaginationChips({ gaps, duplicates }: { gaps?: number[]; duplicates?: number[] }) {
  const { t } = useTranslation('collections');
  if (!gaps?.length && !duplicates?.length) return null;
  return (
    <Box component="span" sx={{ display: 'inline-flex', flexWrap: 'wrap', gap: 0.5, alignItems: 'center', verticalAlign: 'middle' }}>
      {gaps && gaps.length > 0 && (
        <Tooltip title={t('pagination.missingNumbers', { list: gaps.join(', ') })} arrow>
          <Chip label={t('pagination.gaps', { count: gaps.length })} size="small" color="warning" variant="outlined" sx={{ height: 18, fontSize: '0.65rem' }} />
        </Tooltip>
      )}
      {duplicates && duplicates.length > 0 && (
        <Tooltip title={t('pagination.duplicateNumbers', { list: duplicates.join(', ') })} arrow>
          <Chip label={t('pagination.duplicates', { count: duplicates.length })} size="small" color="warning" variant="outlined" sx={{ height: 18, fontSize: '0.65rem' }} />
        </Tooltip>
      )}
    </Box>
  );
}

// Panneau de progression (barre + libellé « <action> de « <nom> »… n/total »),
// partagé entre l'analyse et la synchronisation.
function ProgressPanel({ progress, action }: { progress: ScanProgress | null; action: string }) {
  const { t } = useTranslation('collections');
  return (
    <Box sx={{ py: 4, px: 1 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 1, gap: 2 }}>
        <Typography color="text.secondary" noWrap>
          {progress ? t('progress.of', { action, name: progress.name }) : t('progress.inProgress', { action })}
        </Typography>
        {progress && (
          <Typography color="text.secondary" sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
            {progress.current} / {progress.total}
          </Typography>
        )}
      </Box>
      {progress && progress.total > 0 ? (
        <LinearProgress variant="determinate" value={Math.round((progress.current / progress.total) * 100)} />
      ) : (
        <LinearProgress />
      )}
      {progress && progress.reg_total != null && progress.reg_total > 0 && (
        <Box sx={{ mt: 2 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 1, gap: 2 }}>
            <Typography variant="body2" color="text.secondary" noWrap>
              {t('progress.registres')}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
              {progress.reg_current ?? 0} / {progress.reg_total}
            </Typography>
          </Box>
          <LinearProgress variant="determinate" value={Math.round(((progress.reg_current ?? 0) / progress.reg_total) * 100)} />
        </Box>
      )}
    </Box>
  );
}

function ScanCollectionRow({ col, onSync, syncing }: {
  col: CollectionScanStatus;
  onSync: (folderName: string) => void;
  syncing: boolean;
}) {
  const { t } = useTranslation('collections');
  const [open, setOpen] = useState(!col.is_known || col.anomalies.length > 0);

  return (
    <>
      <TableRow
        sx={{
          bgcolor: col.anomalies.length > 0 ? 'error.50' : col.is_known ? 'transparent' : 'warning.50',
          cursor: col.registres.length > 0 ? 'pointer' : 'default',
          '&:hover': { bgcolor: 'action.hover' },
        }}
        onClick={() => col.registres.length > 0 && setOpen((o) => !o)}
      >
        <TableCell sx={{ width: 32, py: 0.5 }}>
          {col.registres.length > 0
            ? (open ? <ArrowDownIcon fontSize="small" /> : <ArrowRightIcon fontSize="small" />)
            : null}
        </TableCell>
        <TableCell sx={{ fontWeight: 600 }}>
          {col.folder_name}
          {!col.is_known && (
            <Chip label={t('new')} size="small" color="warning" sx={{ ml: 1, height: 18, fontSize: '0.65rem' }} />
          )}
          <AnomalyChips anomalies={col.anomalies} />
        </TableCell>
        <TableCell align="center">
          <StatusIcon ok={col.has_metadata} label={col.has_metadata ? t('status.metadataPresent') : t('status.metadataMissing')} />
        </TableCell>
        <TableCell align="center">
          <StatusIcon ok={col.has_scans_folder} label={col.has_scans_folder ? t('status.scansPresent') : t('status.scansMissing')} />
        </TableCell>
        <TableCell align="center">
          <StatusIcon ok={col.has_ocr_folder} label={col.has_ocr_folder ? t('status.ocrPresent') : t('status.ocrMissing')} />
        </TableCell>
        <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.8rem', whiteSpace: 'nowrap' }}>
          {t('registresCount', { count: col.registres.length })}
          <Tooltip title={t('syncCollection')} arrow>
            <span>
              <IconButton
                size="small"
                disabled={syncing}
                onClick={(e) => { e.stopPropagation(); onSync(col.folder_name); }}
                sx={{ ml: 0.5 }}
              >
                {syncing ? <CircularProgress size={14} /> : <SyncIcon fontSize="small" />}
              </IconButton>
            </span>
          </Tooltip>
        </TableCell>
      </TableRow>

      <TableRow>
        <TableCell colSpan={6} sx={{ p: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Table size="small" sx={{ bgcolor: 'action.hover' }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ pl: 6, fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('regTable.registre')}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('regTable.pages')}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('regTable.ocr')}</TableCell>
                  <TableCell sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('regTable.paginationAnomalies')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {col.registres.map((reg) => {
                  const ocrCounts = Object.keys(reg.ocr_status).length
                    ? Object.fromEntries(Object.entries(reg.ocr_status).map(([m, s]) => [m, s.pages_done]))
                    : null;
                  const gaps = reg.pagination?.gaps ?? [];
                  const dups = reg.pagination?.duplicates ?? [];
                  return (
                    <TableRow
                      key={reg.folder_name}
                      sx={{ bgcolor: reg.anomalies.length > 0 ? 'error.50' : reg.is_known ? 'transparent' : 'warning.50' }}
                    >
                      <TableCell sx={{ pl: 6 }}>
                        {reg.folder_name}
                        {!reg.is_known && (
                          <Chip label={t('new')} size="small" color="warning" sx={{ ml: 1, height: 18, fontSize: '0.65rem' }} />
                        )}
                      </TableCell>
                      <TableCell align="right" sx={{ fontSize: '0.8rem', color: 'text.secondary' }}>
                        {reg.pages_count.toLocaleString('fr-FR')}
                      </TableCell>
                      <TableCell align="right">
                        <CoverageCell counts={ocrCounts} pageCount={reg.pages_count} loading={false} />
                      </TableCell>
                      <TableCell>
                        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
                          <PaginationChips gaps={gaps} duplicates={dups} />
                          <AnomalyChips anomalies={reg.anomalies} />
                        </Box>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

function CoverageCell({ counts, pageCount, loading, compact = false }: {
  counts: Record<string, number> | null;
  pageCount: number;
  loading: boolean;
  // En mode compact : un seul badge « Transcrit » avec le détail des modèles au survol
  // (comme les lignes de pages), au lieu d'un badge par modèle.
  compact?: boolean;
}) {
  const { t, i18n } = useTranslation('collections');
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const fmtN = (n: number) => n.toLocaleString(locale);
  const pagesStr = fmtN(pageCount);

  if (loading) {
    return <Skeleton variant="rounded" width={110} height={22} sx={{ ml: 'auto' }} />;
  }

  const entries = counts ? Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)) : [];

  if (entries.length === 0) {
    if (compact) {
      return (
        <Tooltip title={t('coverage.notTranscribedTooltip', { total: pagesStr })} arrow>
          <Chip
            size="small"
            variant="outlined"
            color="error"
            label={t('coverage.notTranscribed')}
            sx={{ cursor: 'default' }}
          />
        </Tooltip>
      );
    }
    return (
      <Tooltip title={t('coverage.zeroTooltip', { total: pagesStr })} arrow>
        <Chip
          size="small"
          variant="outlined"
          label="0 %"
          sx={{ cursor: 'default' }}
        />
      </Tooltip>
    );
  }

  if (compact) {
    // Pourcentage affiché : meilleure couverture parmi les modèles. Le détail
    // par modèle reste accessible au survol.
    const best = entries.reduce((max, [, count]) => Math.max(max, count), 0);
    const bestComplete = best >= pageCount;
    const bestPct = bestComplete ? 100
      : pageCount > 0 ? Math.min(99, Math.round((best / pageCount) * 100)) : 0;
    const tooltip = (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
        {entries.map(([m, count]) => {
          const complete = count >= pageCount;
          const pct = complete ? 100
            : pageCount > 0 ? Math.min(99, Math.round((count / pageCount) * 100)) : 0;
          return (
            <span key={m}>{t('coverage.perModel', { model: m, pct, count: fmtN(count), total: pagesStr })}</span>
          );
        })}
      </Box>
    );
    return (
      <Tooltip title={tooltip} arrow>
        <Chip
          size="small"
          variant="outlined"
          color={bestComplete ? 'success' : 'warning'}
          label={t('coverage.transcribedPct', { pct: bestPct })}
          sx={{ cursor: 'default' }}
        />
      </Tooltip>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 0.5 }}>
      {entries.map(([m, count]) => {
        const complete = count >= pageCount;
        // Pas de « 100 % » trompeur par arrondi : plafonné à 99 % tant que ce n'est pas complet.
        const pct = complete ? 100
          : pageCount > 0 ? Math.min(99, Math.round((count / pageCount) * 100)) : 0;
        const remaining = Math.max(0, pageCount - count);
        const tooltip = complete
          ? t('coverage.completeTooltip', { total: pagesStr })
          : t('coverage.partialTooltip', { count: fmtN(count), total: pagesStr, remaining: fmtN(remaining) });
        return (
          <Tooltip key={m} title={tooltip} arrow>
            <Chip
              size="small"
              variant="outlined"
              color={count === 0 ? 'default' : complete ? 'success' : 'warning'}
              label={t('coverage.modelPct', { model: m, pct })}
              sx={{ cursor: 'default' }}
            />
          </Tooltip>
        );
      })}
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function CollectionsPage() {
  const { t } = useTranslation(['collections', 'common']);
  const navigate = useNavigate();
  const [collections, setCollections] = useState<CollectionMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedCollectionId, setExpandedCollectionId] = useState<string | null>(null);

  // Filtres de la liste
  const [typeFilter, setTypeFilter] = useState('all');
  const [searchFilter, setSearchFilter] = useState('');

  // Synchronisation d'une collection (id en cours, ou null)
  const [syncingId, setSyncingId] = useState<string | null>(null);

  const [openEditDialog, setOpenEditDialog] = useState(false);
  const [editingCollection, setEditingCollection] = useState<CollectionMetadata | null>(null);

  // Dialog d'aide (structure du dossier data, formats, fonctionnement)
  const [infoOpen, setInfoOpen] = useState(false);

  // Scan
  const [scanOpen, setScanOpen] = useState(false);
  const [scanReport, setScanReport] = useState<ScanReport | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState<ScanProgress | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncProgress, setSyncProgress] = useState<ScanProgress | null>(null);
  const [scanOnlyIssues, setScanOnlyIssues] = useState(false);
  const [scanSyncingFolder, setScanSyncingFolder] = useState<string | null>(null);

  // Registre expansion + page cache
  const [expandedRegistreKey, setExpandedRegistreKey] = useState<string | null>(null);
  const [registrePagesCache, setRegistrePagesCache] = useState<Map<string, string[]>>(new Map());
  // Transcriptions par page : cacheKey -> { stem du fichier image -> [modèles] }
  const [registreOcrCache, setRegistreOcrCache] = useState<Map<string, Record<string, string[]>>>(new Map());
  const [loadingRegistrePages, setLoadingRegistrePages] = useState<string | null>(null);

  // Visionneuse de pages (dialog rapide + mode pleine page)
  interface RegistreViewer {
    collectionId: string;
    regKey: string;
    pages: string[];
    index: number;
    mainPattern?: string;
    extraPattern?: string;
  }
  const [viewer, setViewer] = useState<RegistreViewer | null>(null);
  const [fullViewer, setFullViewer] = useState<RegistreViewer | null>(null);

  // Famille d'une page : la page principale et ses pages « extra » (même numéro
  // dans le motif), affichée en miniatures dans la visionneuse.
  const pageFamily = (v: RegistreViewer, page: string): string[] =>
    pageFamilyFromList(v.pages, page, v.mainPattern, v.extraPattern);

  // Edit registre dialog
  const [savingRegistre, setSavingRegistre] = useState(false);
  const [editingRegistre, setEditingRegistre] = useState<{
    collectionId: string;
    registreId: string;
    titre: string;
    periodeDebut: string;
    periodeFin: string;
    paginationPattern: string;
    paginationStart: string;
    paginationEnd: string;
    extraPattern: string;
  } | null>(null);

  // Transcriptions
  const [summary, setSummary] = useState<TranscriptionsSummary[]>([]);
  const [transcLoading, setTranscLoading] = useState(true);

  useEffect(() => {
    loadCollections();
    transcriptionsApi.getSummary()
      .then(setSummary)
      .catch(() => {})
      .finally(() => setTranscLoading(false));
  }, []);

  const loadCollections = async () => {
    try {
      setLoading(true);
      const data = await collectionsApi.getAll();
      setCollections(data);
      setError(null);
    } catch (err) {
      setError(t('errors.loadCollections'));
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleOpenEdit = (collection: CollectionMetadata) => {
    setEditingCollection({ ...collection });
    setOpenEditDialog(true);
  };

  const handleEditCollection = async () => {
    if (!editingCollection) return;
    try {
      const update: CollectionUpdate = {
        type: editingCollection.type,
        titre: editingCollection.titre,
        periode: editingCollection.periode,
        lieu: editingCollection.lieu,
        commentaire: editingCollection.commentaire,
      };
      await collectionsApi.update(editingCollection.folder_name || editingCollection.id, update);
      setOpenEditDialog(false);
      setEditingCollection(null);
      loadCollections();
    } catch (err) {
      setError(t('errors.editCollection'));
      console.error(err);
    }
  };

  const handleOpenEditRegistre = (
    collectionId: string,
    registre: {
      id: string;
      folder_name: string;
      titre: string;
      periode: string[];
      pages_pattern?: string;
      pages_start?: number;
      pages_end?: number;
      extra_pagination?: { pattern?: string };
    }
  ) => {
    setEditingRegistre({
      collectionId,
      registreId: registre.folder_name || registre.id,
      titre: registre.titre,
      periodeDebut: registre.periode[0] ?? '',
      periodeFin: registre.periode[1] ?? '',
      paginationPattern: registre.pages_pattern ?? '',
      paginationStart: registre.pages_start != null ? String(registre.pages_start) : '',
      paginationEnd: registre.pages_end != null ? String(registre.pages_end) : '',
      extraPattern: registre.extra_pagination?.pattern ?? '',
    });
  };

  const handleSaveRegistre = async () => {
    if (!editingRegistre) return;
    setSavingRegistre(true);
    try {
      const update: RegistreUpdate = {
        titre: editingRegistre.titre,
        periode: [editingRegistre.periodeDebut, editingRegistre.periodeFin],
        pagination: {
          pattern: editingRegistre.paginationPattern || undefined,
          start: editingRegistre.paginationStart !== '' ? Number(editingRegistre.paginationStart) : undefined,
          end: editingRegistre.paginationEnd !== '' ? Number(editingRegistre.paginationEnd) : undefined,
        },
        extra_pagination: editingRegistre.extraPattern ? {
          pattern: editingRegistre.extraPattern,
        } : undefined,
      };
      await registresApi.update(editingRegistre.collectionId, editingRegistre.registreId, update);
      setEditingRegistre(null);
      loadCollections();
    } catch (err) {
      setError(t('errors.editRegistre'));
      console.error(err);
    } finally {
      setSavingRegistre(false);
    }
  };

  const handleToggleRegistre = async (collectionId: string, regKey: string) => {
    const cacheKey = `${collectionId}|${regKey}`;

    if (expandedRegistreKey === cacheKey) {
      setExpandedRegistreKey(null);
      return;
    }

    setExpandedRegistreKey(cacheKey);

    if (!registrePagesCache.has(cacheKey)) {
      setLoadingRegistrePages(cacheKey);
      try {
        const [data, ocr] = await Promise.all([
          registresApi.getPages(collectionId, regKey),
          registresApi.getPageTranscriptions(collectionId, regKey),
        ]);
        setRegistrePagesCache(prev => new Map(prev).set(cacheKey, data));
        setRegistreOcrCache(prev => new Map(prev).set(cacheKey, ocr));
      } catch (err) {
        console.error('Error loading pages:', err);
        setRegistrePagesCache(prev => new Map(prev).set(cacheKey, []));
        setRegistreOcrCache(prev => new Map(prev).set(cacheKey, {}));
      } finally {
        setLoadingRegistrePages(null);
      }
    }
  };

  const handleScan = async () => {
    setScanning(true);
    setScanReport(null);
    setScanProgress(null);
    setScanOpen(true);
    try {
      const report = await collectionsApi.scanStream(setScanProgress);
      setScanReport(report);
    } catch (err) {
      setError(t('errors.scan'));
      setScanOpen(false);
      console.error(err);
    } finally {
      setScanning(false);
      setScanProgress(null);
    }
  };

  const handleSyncAll = async () => {
    setSyncing(true);
    setSyncProgress(null);
    try {
      await collectionsApi.syncAllStream(setSyncProgress);
      await loadCollections();
      // Rafraîchit la couverture de transcription affichée dans le tableau.
      setTranscLoading(true);
      try {
        setSummary(await transcriptionsApi.getSummary());
      } finally {
        setTranscLoading(false);
      }
      // Synchronisation terminée : on ferme le dialog sans relancer de scan.
      setScanOpen(false);
      setScanReport(null);
    } catch (err) {
      setError(t('errors.sync'));
      console.error(err);
    } finally {
      setSyncing(false);
      setSyncProgress(null);
    }
  };

  // Synchronise une seule collection depuis le rapport de scan, puis rafraîchit le rapport.
  // Passe par le flux NDJSON : le ProgressPanel affiche l'avancement par registre (état
  // syncing/syncProgress partagé avec « Synchroniser tout »).
  const handleSyncFromScan = async (folderName: string) => {
    setSyncing(true);
    setSyncProgress({ current: 1, total: 1, name: folderName });
    setScanSyncingFolder(folderName);
    try {
      await collectionsApi.syncStream(folderName, setSyncProgress);
      await loadCollections();
      const report = await collectionsApi.scan();
      setScanReport(report);
    } catch (err) {
      setError(t('errors.syncCollection'));
      console.error(err);
    } finally {
      setSyncing(false);
      setSyncProgress(null);
      setScanSyncingFolder(null);
    }
  };

  const handleSyncCollection = async (collection: CollectionMetadata) => {
    const id = collection.folder_name || collection.id;
    setSyncingId(id);
    try {
      await collectionsApi.sync(id);
      await loadCollections();
      setTranscLoading(true);
      try {
        setSummary(await transcriptionsApi.getSummary());
      } finally {
        setTranscLoading(false);
      }
    } catch (err) {
      setError(t('errors.syncCollection'));
      console.error(err);
    } finally {
      setSyncingId(null);
    }
  };

  const summaryMap = new Map(summary.map(s => [s.collection_id, s]));

  // ── Liste filtrée : type + recherche (collections et registres, sans diacritiques) ──
  const searchActive = searchFilter.trim().length > 0;
  const visibleCollections = useMemo(() => {
    const q = norm(searchFilter.trim());
    const result: { collection: CollectionMetadata; regs: RegistreSummary[] }[] = [];
    for (const c of collections) {
      if (typeFilter !== 'all' && c.type !== typeFilter) continue;
      const regs = c.registres || [];
      if (!q || norm(`${c.titre} ${c.folder_name ?? ''} ${c.lieu ?? ''}`).includes(q)) {
        result.push({ collection: c, regs });
        continue;
      }
      const matched = regs.filter(r => norm(`${r.titre} ${r.folder_name}`).includes(q));
      if (matched.length > 0) result.push({ collection: c, regs: matched });
    }
    return result;
  }, [collections, typeFilter, searchFilter]);

  const collectionTypes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of collections) counts.set(c.type, (counts.get(c.type) || 0) + 1);
    return [...counts.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [collections]);

  // On garde le loader tant que collections ET couverture ne sont pas prêtes,
  // sinon le tableau s'affiche puis change de hauteur quand les taux arrivent.
  if (loading || transcLoading) return <Loader message={t('loading')} minHeight="60vh" />;

  // Vue pleine page d'un registre — le composant reste monté, donc l'état
  // (expansion, caches de pages) est préservé au retour.
  if (fullViewer) {
    return (
      <FullPageViewer
        entries={fullViewer.pages.map(p => ({ pageName: p, boxes: [], wordSummary: '' }))}
        startIndex={fullViewer.index}
        getImageUrl={(p) => registresApi.getPageUrl(fullViewer.collectionId, fullViewer.regKey, p)}
        getRelated={(p) => pageFamily(fullViewer, p)}
        getThumbLabel={(p) => thumbLabel(p, fullViewer.mainPattern, fullViewer.extraPattern)}
        backLabel={t('backToCollections')}
        onBack={() => setFullViewer(null)}
      />
    );
  }

  return (
    <Box>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* ── Barre d'outils : filtres + actions ── */}
      <Box sx={{ mb: 2, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        {collections.length > 0 && (
          <>
            <TextField
              select
              size="small"
              label={t('toolbar.type')}
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              sx={{ minWidth: 160 }}
            >
              <MenuItem value="all">{t('toolbar.all', { count: collections.length })}</MenuItem>
              {collectionTypes.map(([type, count]) => (
                <MenuItem key={type} value={type}>{type} ({count})</MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              placeholder={t('toolbar.searchPlaceholder')}
              value={searchFilter}
              onChange={e => setSearchFilter(e.target.value)}
              sx={{ width: 320 }}
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
          </>
        )}
        <Button
          variant="outlined"
          startIcon={<InfoIcon />}
          onClick={() => setInfoOpen(true)}
          sx={{ ml: 'auto' }}
        >
          {t('toolbar.info')}
        </Button>
        <Button
          variant="contained"
          disableElevation
          startIcon={scanning ? <CircularProgress size={16} color="inherit" /> : <SearchIcon />}
          onClick={handleScan}
          disabled={scanning}
        >
          {t('toolbar.scan')}
        </Button>
      </Box>

      {collections.length === 0 ? (
        <EmptyState
          icon={<FolderOpenIcon />}
          title={t('empty.title')}
          description={t('empty.description')}
          action={{ label: t('empty.action'), onClick: handleScan }}
        />
      ) : visibleCollections.length === 0 ? (
        <Box sx={{ textAlign: 'center', mt: 10, color: 'text.disabled' }}>
          <FolderOpenIcon sx={{ fontSize: 56, opacity: 0.2, mb: 1 }} />
          <Typography variant="body2" color="text.disabled">
            {t('noMatch')}
          </Typography>
        </Box>
      ) : (
      <Paper elevation={0} sx={{ border: '1.5px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: 'grey.50' }}>
                <TableCell sx={{ width: 36 }} />
                {['table.type', 'table.title', 'table.period', 'table.place'].map(h => (
                  <TableCell key={h} sx={{ fontWeight: 700, fontSize: '0.7rem', letterSpacing: '0.05em', color: 'text.secondary', textTransform: 'uppercase' }}>{t(h)}</TableCell>
                ))}
                {['table.registres', 'table.transcriptions', 'table.actions'].map(h => (
                  <TableCell key={h} align="right" sx={{ fontWeight: 700, fontSize: '0.7rem', letterSpacing: '0.05em', color: 'text.secondary', textTransform: 'uppercase' }}>{t(h)}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {visibleCollections.map(({ collection, regs }) => {
                const allRegs = collection.registres || [];
                const isExpanded = searchActive ? regs.length > 0 : expandedCollectionId === collection.id;
                const colKey = collection.folder_name || collection.type;
                const colSummary = summaryMap.get(colKey);
                const colSyncing = syncingId === (collection.folder_name || collection.id);
                // Anomalies persistées au dernier sync (niveau collection).
                const colAnomalies = collection.anomalies ?? [];

                return (
                  <React.Fragment key={collection.id}>
                    <TableRow
                      hover
                      sx={{
                        cursor: regs.length > 0 ? 'pointer' : 'default',
                        '&:hover .row-actions': { opacity: 1 },
                      }}
                      onClick={() => regs.length > 0 && setExpandedCollectionId(isExpanded ? null : collection.id)}
                    >
                      <TableCell sx={{ py: 0.5 }}>
                        {regs.length > 0
                          ? (isExpanded ? <ArrowDownIcon fontSize="small" /> : <ArrowRightIcon fontSize="small" />)
                          : null}
                      </TableCell>
                      <TableCell>
                        <Chip label={collection.type} size="small" variant="outlined" sx={{ fontWeight: 500 }} />
                      </TableCell>
                      <TableCell sx={{ fontWeight: 500 }}>
                        {collection.titre}
                        <AnomalyChips anomalies={colAnomalies} />
                      </TableCell>
                      <TableCell sx={{ color: 'text.secondary', fontSize: '0.85rem', whiteSpace: 'nowrap' }}>
                        {collection.periode.join(' – ')}
                      </TableCell>
                      <TableCell sx={{ color: 'text.secondary', fontSize: '0.85rem' }}>{collection.lieu}</TableCell>
                      <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.85rem' }}>
                        {allRegs.length}
                      </TableCell>
                      <TableCell align="right">
                        <CoverageCell
                          counts={colSummary?.totals ?? null}
                          pageCount={allRegs.reduce((s, r) => s + r.pages_count, 0)}
                          loading={transcLoading}
                          compact
                        />
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        <Box
                          className="row-actions"
                          sx={{ display: 'inline-flex', gap: 0.5, opacity: colSyncing ? 1 : 0, transition: 'opacity 0.15s' }}
                        >
                          <Tooltip title={t('rowActions.viewStats')} arrow>
                            <IconButton
                              size="small"
                              onClick={(e) => { e.stopPropagation(); navigate(`/collections/${collection.id}/stats`); }}
                            >
                              <QueryStatsIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          <Tooltip title={t('rowActions.syncMetadata')} arrow>
                            <span>
                              <IconButton
                                size="small"
                                disabled={colSyncing || syncingId !== null}
                                onClick={(e) => { e.stopPropagation(); handleSyncCollection(collection); }}
                              >
                                {colSyncing
                                  ? <CircularProgress size={16} />
                                  : <SyncIcon fontSize="small" />}
                              </IconButton>
                            </span>
                          </Tooltip>
                          <Tooltip title={t('rowActions.edit')} arrow>
                            <IconButton
                              size="small"
                              color="primary"
                              onClick={(e) => { e.stopPropagation(); handleOpenEdit(collection); }}
                            >
                              <EditIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </Box>
                      </TableCell>
                    </TableRow>

                    <TableRow>
                      <TableCell colSpan={8} sx={{ p: 0, border: 0 }}>
                        <Collapse in={isExpanded} unmountOnExit>
                          <Table size="small" sx={{ bgcolor: 'grey.50' }}>
                            <TableHead>
                              <TableRow>
                                <TableCell sx={{ width: 32 }} />
                                <TableCell sx={{ pl: 4, fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('subTable.registreTitle')}</TableCell>
                                <TableCell sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('subTable.period')}</TableCell>
                                <TableCell align="right" sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('subTable.transcriptionsPages')}</TableCell>
                                <TableCell align="right" />
                              </TableRow>
                            </TableHead>
                            <TableBody>
                              {regs.map((registre) => {
                                const regKey = registre.folder_name || registre.id;
                                const cacheKey = `${colKey}|${regKey}`;
                                const regIsExpanded = expandedRegistreKey === cacheKey;
                                const regPages = registrePagesCache.get(cacheKey);
                                const regOcr = registreOcrCache.get(cacheKey) ?? {};
                                const regLoading = loadingRegistrePages === cacheKey;
                                const regSummary = colSummary?.registres.find(r => r.registre_id === regKey);
                                // Anomalies/trous/doublons lus depuis le metadata persisté au dernier sync.
                                // (fallback sur la dérivation client pour les collections pas encore resynchronisées).
                                const regAnomalies_ = registre.anomalies ?? registreAnomalies(registre);
                                const regGaps = registre.pages_gaps;
                                const regDups = registre.pages_duplicates;
                                const extraRx = registre.extra_pagination?.pattern
                                  ? makePatternRegex(registre.extra_pagination.pattern, false)
                                  : null;
                                const sorted = regPages
                                  ? sortPages(regPages, registre.pages_pattern, registre.extra_pagination?.pattern)
                                  : [];

                                // Diagnostic de pagination recalculé en direct sur les pages chargées
                                // (toujours exact, indépendant du badge persisté). Numéro de chaque page
                                // principale, doublons, et trous dans l'intervalle [start, end].
                                const mainRx = registre.pages_pattern
                                  ? makePatternRegex(registre.pages_pattern, true)
                                  : null;
                                const numOf = (p: string): number | null => {
                                  const m = mainRx?.exec(p);
                                  return m ? parseInt(m[1]) : null;
                                };
                                const numCounts = new Map<number, number>();
                                for (const p of sorted) {
                                  const n = numOf(p);
                                  if (n != null) numCounts.set(n, (numCounts.get(n) ?? 0) + 1);
                                }
                                const gaps: number[] = [];
                                if (mainRx && numCounts.size > 0) {
                                  const present = [...numCounts.keys()];
                                  const lo = registre.pages_start ?? Math.min(...present);
                                  const hi = registre.pages_end ?? Math.max(...present);
                                  for (let n = lo; n <= hi; n++) if (!numCounts.has(n)) gaps.push(n);
                                }
                                // Largeur de remplissage par zéros détectée sur les pages existantes,
                                // pour reconstruire le nom exact d'une page manquante (ex. « page_0232.jpg »).
                                let numPadWidth = 0;
                                if (mainRx) {
                                  for (const p of sorted) {
                                    const m = mainRx.exec(p);
                                    if (m && m[1].length > String(parseInt(m[1], 10)).length) {
                                      numPadWidth = Math.max(numPadWidth, m[1].length);
                                    }
                                  }
                                }
                                const missingPageName = (num: number): string => {
                                  const pat = registre.pages_pattern;
                                  if (!pat) return `n° ${num}`;
                                  const numStr = numPadWidth > 0 ? String(num).padStart(numPadWidth, '0') : String(num);
                                  return pat.replace('{num}', numStr);
                                };
                                // Fusion pages réelles + lignes fantômes des trous, dans l'ordre des numéros.
                                type PageRow = { kind: 'page'; page: string } | { kind: 'gap'; num: number };
                                const pageRows: PageRow[] = [];
                                let gi = 0;
                                for (const page of sorted) {
                                  const n = numOf(page);
                                  if (n != null) while (gi < gaps.length && gaps[gi] < n) pageRows.push({ kind: 'gap', num: gaps[gi++] });
                                  pageRows.push({ kind: 'page', page });
                                }
                                while (gi < gaps.length) pageRows.push({ kind: 'gap', num: gaps[gi++] });

                                return (
                                  <React.Fragment key={registre.id}>
                                    <TableRow
                                      hover
                                      sx={{ cursor: 'pointer', '&:hover .row-actions': { opacity: 1 } }}
                                      onClick={() => handleToggleRegistre(colKey, regKey)}
                                    >
                                      <TableCell sx={{ width: 32, py: 0.5 }}>
                                        {regIsExpanded
                                          ? <ArrowDownIcon fontSize="small" />
                                          : <ArrowRightIcon fontSize="small" />}
                                      </TableCell>
                                      <TableCell sx={{ pl: 4 }}>
                                        {registre.titre}
                                        <AnomalyChips anomalies={regAnomalies_} />
                                        <Box component="span" sx={{ ml: 1 }}>
                                          <PaginationChips gaps={regGaps} duplicates={regDups} />
                                        </Box>
                                      </TableCell>
                                      <TableCell sx={{ color: 'text.secondary', fontSize: '0.85rem', whiteSpace: 'nowrap' }}>
                                        {registre.periode.join(' – ')}
                                      </TableCell>
                                      <TableCell align="right">
                                        <CoverageCell
                                          counts={regSummary?.counts ?? null}
                                          pageCount={registre.pages_count}
                                          loading={transcLoading}
                                          compact
                                        />
                                      </TableCell>
                                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                                        <Box className="row-actions" sx={{ display: 'inline-flex', opacity: 0, transition: 'opacity 0.15s' }}>
                                          <Tooltip title={t('rowActions.editRegistre')} arrow>
                                            <IconButton
                                              size="small"
                                              color="primary"
                                              onClick={(e) => {
                                                e.stopPropagation();
                                                handleOpenEditRegistre(colKey, registre);
                                              }}
                                            >
                                              <EditIcon fontSize="small" />
                                            </IconButton>
                                          </Tooltip>
                                        </Box>
                                      </TableCell>
                                    </TableRow>

                                    <TableRow>
                                      <TableCell colSpan={5} sx={{ p: 0, border: 0 }}>
                                        <Collapse in={regIsExpanded} unmountOnExit>
                                          {regLoading ? (
                                            <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
                                              <CircularProgress size={20} />
                                            </Box>
                                          ) : (
                                            <Table size="small" sx={{ bgcolor: 'grey.100' }}>
                                              <TableBody>
                                                {sorted.length === 0 ? (
                                                  <TableRow>
                                                    <TableCell colSpan={3} sx={{ textAlign: 'center', color: 'text.disabled', fontSize: '0.78rem', py: 2 }}>
                                                      {t('pageRow.noPages')}
                                                    </TableCell>
                                                  </TableRow>
                                                ) : pageRows.map((item) => {
                                                  if (item.kind === 'gap') {
                                                    return (
                                                      <TableRow key={`gap-${item.num}`} sx={{ bgcolor: 'warning.50' }}>
                                                        <TableCell sx={{ pl: 8, py: 0.25, fontSize: '0.78rem', color: 'warning.dark', fontStyle: 'italic' }}>
                                                          {t('pageRow.missingPage', { name: missingPageName(item.num) })}
                                                        </TableCell>
                                                        <TableCell sx={{ py: 0.25, width: 52 }} />
                                                        <TableCell sx={{ py: 0.25, pr: 1, width: 40 }} />
                                                      </TableRow>
                                                    );
                                                  }
                                                  const page = item.page;
                                                  const isExtra = !!extraRx?.test(page);
                                                  const n = numOf(page);
                                                  const isDuplicate = n != null && (numCounts.get(n) ?? 0) > 1;
                                                  const ocrModels = regOcr[page.replace(/\.[^.]+$/, '')] ?? [];
                                                  const isTranscribed = ocrModels.length > 0;
                                                  const openPage = () => setViewer({
                                                    collectionId: colKey,
                                                    regKey,
                                                    pages: sorted,
                                                    index: sorted.indexOf(page),
                                                    mainPattern: registre.pages_pattern,
                                                    extraPattern: registre.extra_pagination?.pattern,
                                                  });
                                                  return (
                                                    <TableRow
                                                      key={page}
                                                      hover
                                                      sx={{ cursor: 'pointer', ...(isDuplicate ? { bgcolor: 'warning.50' } : {}) }}
                                                      onClick={openPage}
                                                    >
                                                      <TableCell sx={{
                                                        pl: isExtra ? 12 : 8,
                                                        py: 0.25,
                                                        fontFamily: 'monospace',
                                                        fontSize: '0.78rem',
                                                        fontWeight: 500,
                                                        color: isExtra ? 'text.disabled' : 'text.primary',
                                                        fontStyle: isExtra ? 'italic' : 'normal',
                                                      }}>
                                                        {page}
                                                      </TableCell>
                                                      <TableCell sx={{ py: 0.25 }}>
                                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
                                                          <Tooltip
                                                            title={isTranscribed
                                                              ? t('pageRow.transcribedByTooltip', { models: ocrModels.join(', ') })
                                                              : t('pageRow.notTranscribedTooltip')}
                                                            arrow
                                                          >
                                                            <Chip
                                                              label={isTranscribed
                                                                ? (ocrModels.length > 1 ? t('pageRow.transcribedMulti', { count: ocrModels.length }) : t('pageRow.transcribed'))
                                                                : t('pageRow.notTranscribed')}
                                                              size="small"
                                                              color={isTranscribed ? 'success' : 'error'}
                                                              variant="outlined"
                                                              sx={{ height: 18, fontSize: '0.65rem' }}
                                                            />
                                                          </Tooltip>
                                                          {isExtra && (
                                                            <Chip
                                                              label={t('pageRow.extra')}
                                                              size="small"
                                                              color="info"
                                                              variant="outlined"
                                                              sx={{ height: 18, fontSize: '0.65rem' }}
                                                            />
                                                          )}
                                                          {isDuplicate && (
                                                            <Chip
                                                              label={t('pageRow.duplicate')}
                                                              size="small"
                                                              color="warning"
                                                              variant="outlined"
                                                              sx={{ height: 18, fontSize: '0.65rem' }}
                                                            />
                                                          )}
                                                        </Box>
                                                      </TableCell>
                                                      <TableCell align="right" sx={{ py: 0.25, pr: 1, width: 40 }}>
                                                        <Tooltip title={t('rowActions.showPage')} arrow>
                                                          <IconButton
                                                            size="small"
                                                            color="primary"
                                                            onClick={(e) => { e.stopPropagation(); openPage(); }}
                                                          >
                                                            <ImageSearchIcon fontSize="small" />
                                                          </IconButton>
                                                        </Tooltip>
                                                      </TableCell>
                                                    </TableRow>
                                                  );
                                                })}
                                              </TableBody>
                                            </Table>
                                          )}
                                        </Collapse>
                                      </TableCell>
                                    </TableRow>
                                  </React.Fragment>
                                );
                              })}
                            </TableBody>
                          </Table>
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </React.Fragment>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
      )}

      {/* ── Dialog Modifier Registre ── */}
      <Dialog open={editingRegistre !== null} onClose={() => setEditingRegistre(null)} maxWidth="sm" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ pb: 0.5 }}>
          {t('editReg.title')}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t('editReg.subtitle')}
          </Typography>
        </DialogTitle>
        <DialogContent>
          {editingRegistre && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2 }}>
              <TextField
                label={t('editReg.titleField')}
                required
                fullWidth
                value={editingRegistre.titre}
                onChange={(e) => setEditingRegistre({ ...editingRegistre, titre: e.target.value })}
              />
              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField
                  label={t('editReg.periodStart')}
                  required
                  fullWidth
                  value={editingRegistre.periodeDebut}
                  onChange={(e) => setEditingRegistre({ ...editingRegistre, periodeDebut: e.target.value })}
                />
                <TextField
                  label={t('editReg.periodEnd')}
                  required
                  fullWidth
                  value={editingRegistre.periodeFin}
                  onChange={(e) => setEditingRegistre({ ...editingRegistre, periodeFin: e.target.value })}
                />
              </Box>
              <Typography variant="subtitle2" color="text.secondary" sx={{ mt: 1 }}>
                {t('editReg.pagination')}
              </Typography>
              <TextField
                label={t('editReg.filePattern')}
                fullWidth
                value={editingRegistre.paginationPattern}
                onChange={(e) => setEditingRegistre({ ...editingRegistre, paginationPattern: e.target.value })}
                helperText={t('editReg.filePatternHelper')}
              />
              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField
                  label={t('editReg.startPage')}
                  type="number"
                  fullWidth
                  value={editingRegistre.paginationStart}
                  onChange={(e) => setEditingRegistre({ ...editingRegistre, paginationStart: e.target.value })}
                />
                <TextField
                  label={t('editReg.endPage')}
                  type="number"
                  fullWidth
                  value={editingRegistre.paginationEnd}
                  onChange={(e) => setEditingRegistre({ ...editingRegistre, paginationEnd: e.target.value })}
                />
              </Box>
              <Typography variant="subtitle2" color="text.secondary" sx={{ mt: 1 }}>
                {t('editReg.extraPages')}
              </Typography>
              <TextField
                label={t('editReg.extraPattern')}
                fullWidth
                value={editingRegistre.extraPattern}
                onChange={(e) => setEditingRegistre({ ...editingRegistre, extraPattern: e.target.value })}
                helperText={t('editReg.extraPatternHelper')}
              />
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          <Button onClick={() => setEditingRegistre(null)} disabled={savingRegistre}>{t('common:actions.cancel')}</Button>
          <Button onClick={handleSaveRegistre} variant="contained" disableElevation disabled={savingRegistre}
            startIcon={savingRegistre ? <CircularProgress size={16} color="inherit" /> : undefined}>
            {savingRegistre ? t('common:actions.saving') : t('common:actions.save')}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Dialog Informations (aide) ── */}
      <Dialog open={infoOpen} onClose={() => setInfoOpen(false)} maxWidth="md" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', pb: 0.5 }}>
          <Box>
            {t('info.title')}
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {t('info.subtitle')}
            </Typography>
          </Box>
          <IconButton onClick={() => setInfoOpen(false)} size="small"><CloseIcon /></IconButton>
        </DialogTitle>
        <DialogContent dividers>
          {/* Où déposer les fichiers */}
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            {t('info.whereTitle')}
          </Typography>
          <Typography variant="body2" sx={{ mb: 1.5 }}>
            <Trans t={t} i18nKey="info.whereText" components={{ code: <code />, strong: <strong /> }} />
          </Typography>
          <Box
            component="pre"
            sx={{
              bgcolor: 'grey.50',
              border: '1px solid',
              borderColor: 'divider',
              borderRadius: 2,
              p: 2,
              m: 0,
              mb: 3,
              fontSize: '0.78rem',
              lineHeight: 1.6,
              overflowX: 'auto',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            }}
          >{t('info.treeBlock')}</Box>

          {/* Noms de fichiers & formats */}
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            {t('info.namingTitle')}
          </Typography>
          <Box component="ul" sx={{ pl: 2.5, m: 0, mb: 3, '& li': { mb: 1, fontSize: '0.875rem' } }}>
            <li>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap' }}>
                <span>{t('info.formatsLabel')}</span>
                {['.jpg', '.jpeg', '.png', '.tif', '.tiff'].map((ext) => (
                  <Chip key={ext} label={ext} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                ))}
              </Box>
            </li>
            <li><Trans t={t} i18nKey="info.namingItem1" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.namingItem2" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.namingItem3" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.namingItem4" components={{ code: <code />, strong: <strong /> }} /></li>
          </Box>

          {/* Règles de nommage à respecter */}
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            {t('info.rulesTitle')}
          </Typography>
          <Box component="ol" sx={{ pl: 2.5, m: 0, mb: 3, '& li': { mb: 1, fontSize: '0.875rem' } }}>
            <li><Trans t={t} i18nKey="info.rule1" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.rule2" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.rule3" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.rule4" components={{ code: <code />, strong: <strong /> }} /></li>
            <li style={{ color: 'var(--mui-palette-warning-dark)' }}>
              <Trans t={t} i18nKey="info.rule5" components={{ code: <code />, strong: <strong /> }} />
            </li>
          </Box>

          {/* Étapes */}
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            {t('info.howTitle')}
          </Typography>
          <Box component="ol" sx={{ pl: 2.5, m: 0, mb: 3, '& li': { mb: 1, fontSize: '0.875rem' } }}>
            <li><Trans t={t} i18nKey="info.how1" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.how2" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.how3" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.how4" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.how5" components={{ code: <code />, strong: <strong /> }} /></li>
          </Box>

          {/* Anomalies */}
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
            {t('info.anomaliesTitle')}
          </Typography>
          <Box component="ul" sx={{ pl: 2.5, m: 0, '& li': { mb: 0.75, fontSize: '0.875rem' } }}>
            <li><Trans t={t} i18nKey="info.anomalyItem1" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.anomalyItem2" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.anomalyItem3" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.anomalyItem4" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.anomalyItem5" components={{ code: <code />, strong: <strong /> }} /></li>
            <li><Trans t={t} i18nKey="info.anomalyItem6" components={{ code: <code />, strong: <strong /> }} /></li>
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          <Button onClick={() => setInfoOpen(false)}>{t('common:actions.close')}</Button>
        </DialogActions>
      </Dialog>

      {/* ── Dialog Scan ── */}
      <Dialog open={scanOpen} onClose={() => setScanOpen(false)} maxWidth="md" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', pb: 0.5 }}>
          <Box>
            {t('scan.title')}
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {t('scan.subtitle')}
            </Typography>
          </Box>
          <IconButton onClick={() => setScanOpen(false)} size="small"><CloseIcon /></IconButton>
        </DialogTitle>
        <DialogContent dividers>
          {(scanning || syncing) ? (
            <ProgressPanel
              progress={syncing ? syncProgress : scanProgress}
              action={syncing ? t('progress.sync') : t('progress.analyze')}
            />
          ) : scanReport && (
            <>
              <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap' }}>
                <Chip
                  label={t('scan.collectionsCount', { count: scanReport.collections.length })}
                  color="default"
                  variant="outlined"
                />
                {scanReport.new_collections > 0 && (
                  <Chip
                    icon={<FiberNewIcon />}
                    label={t('scan.newCount', { count: scanReport.new_collections })}
                    color="warning"
                    variant="outlined"
                  />
                )}
                {scanReport.new_registres > 0 && (
                  <Chip
                    icon={<FiberNewIcon />}
                    label={t('scan.newRegistresCount', { count: scanReport.new_registres })}
                    color="warning"
                    variant="outlined"
                  />
                )}
                {scanReport.anomalies_count > 0 && (
                  <Chip
                    label={t('scan.anomaliesCount', { count: scanReport.anomalies_count })}
                    color="error"
                    variant="outlined"
                  />
                )}
                {scanReport.new_collections === 0 && scanReport.new_registres === 0 && scanReport.anomalies_count === 0 && (
                  <Chip icon={<CheckCircleIcon />} label={t('scan.allSynced')} color="success" variant="outlined" />
                )}
                <Box sx={{ flexGrow: 1 }} />
                <Tooltip title={t('scan.onlyIssuesTooltip')} arrow>
                  <Chip
                    label={t('scan.onlyIssuesChip')}
                    color={scanOnlyIssues ? 'primary' : 'default'}
                    variant={scanOnlyIssues ? 'filled' : 'outlined'}
                    onClick={() => setScanOnlyIssues((v) => !v)}
                  />
                </Tooltip>
              </Box>

              {(() => {
                const hasIssue = (col: CollectionScanStatus) =>
                  !col.is_known || col.anomalies.length > 0 ||
                  col.registres.some((r) => !r.is_known || r.anomalies.length > 0);
                const rows = scanOnlyIssues ? scanReport.collections.filter(hasIssue) : scanReport.collections;
                if (rows.length === 0) {
                  return (
                    <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
                      {t('scan.noCollectionsFilter')}
                    </Typography>
                  );
                }
                return (
                  <Table size="small">
                    <TableHead>
                      <TableRow sx={{ bgcolor: 'action.hover' }}>
                        <TableCell sx={{ width: 32 }} />
                        <TableCell sx={{ fontWeight: 600 }}>{t('scan.colHeader')}</TableCell>
                        <TableCell align="center" sx={{ fontWeight: 600 }}>metadata.json</TableCell>
                        <TableCell align="center" sx={{ fontWeight: 600 }}>scans/</TableCell>
                        <TableCell align="center" sx={{ fontWeight: 600 }}>ocr/</TableCell>
                        <TableCell align="right" sx={{ fontWeight: 600 }}>{t('scan.registresHeader')}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {rows.map((col) => (
                        <ScanCollectionRow
                          key={col.folder_name}
                          col={col}
                          onSync={handleSyncFromScan}
                          syncing={scanSyncingFolder === col.folder_name}
                        />
                      ))}
                    </TableBody>
                  </Table>
                );
              })()}
            </>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          <Button onClick={() => setScanOpen(false)}>{t('common:actions.close')}</Button>
          <Button
            variant="contained"
            disableElevation
            startIcon={syncing ? <CircularProgress size={16} color="inherit" /> : <SyncIcon />}
            onClick={handleSyncAll}
            disabled={syncing || scanning || !scanReport}
          >
            {t('scan.syncAll')}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Dialog Modifier Collection ── */}
      <Dialog open={openEditDialog} onClose={() => setOpenEditDialog(false)} maxWidth="sm" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ pb: 0.5 }}>
          {t('editCol.title')}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t('editCol.subtitle')}
          </Typography>
        </DialogTitle>
        <DialogContent>
          {editingCollection && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2 }}>
              <TextField
                label={t('editCol.type')}
                required
                value={editingCollection.type}
                onChange={(e) => setEditingCollection({ ...editingCollection, type: e.target.value })}
              />
              <TextField
                label={t('editCol.titleField')}
                required
                value={editingCollection.titre}
                onChange={(e) => setEditingCollection({ ...editingCollection, titre: e.target.value })}
              />
              <Box sx={{ display: 'flex', gap: 2 }}>
                <TextField
                  label={t('editCol.periodStart')}
                  required
                  value={editingCollection.periode[0]}
                  onChange={(e) => setEditingCollection({ ...editingCollection, periode: [e.target.value, editingCollection.periode[1]] })}
                />
                <TextField
                  label={t('editCol.periodEnd')}
                  required
                  value={editingCollection.periode[1]}
                  onChange={(e) => setEditingCollection({ ...editingCollection, periode: [editingCollection.periode[0], e.target.value] })}
                />
              </Box>
              <TextField
                label={t('editCol.place')}
                required
                value={editingCollection.lieu}
                onChange={(e) => setEditingCollection({ ...editingCollection, lieu: e.target.value })}
              />
              <TextField
                label={t('editCol.comment')}
                multiline
                rows={3}
                value={editingCollection.commentaire || ''}
                onChange={(e) => setEditingCollection({ ...editingCollection, commentaire: e.target.value })}
              />
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          <Button onClick={() => setOpenEditDialog(false)}>{t('common:actions.cancel')}</Button>
          <Button onClick={handleEditCollection} variant="contained" disableElevation>{t('common:actions.save')}</Button>
        </DialogActions>
      </Dialog>
      {/* ── Visionneuse de page (aperçu rapide, même composant que la recherche) ── */}
      {viewer && (
        <ImageViewer
          open
          onClose={() => setViewer(null)}
          entry={{ pageName: viewer.pages[viewer.index], boxes: [], wordSummary: '' }}
          index={viewer.index}
          total={viewer.pages.length}
          onPrev={() => setViewer(v => v && v.index > 0 ? { ...v, index: v.index - 1 } : v)}
          onNext={() => setViewer(v => v && v.index < v.pages.length - 1 ? { ...v, index: v.index + 1 } : v)}
          getImageUrl={(p) => registresApi.getPageUrl(viewer.collectionId, viewer.regKey, p)}
          getRelated={(p) => pageFamily(viewer, p)}
          getThumbLabel={(p) => thumbLabel(p, viewer.mainPattern, viewer.extraPattern)}
          onSelectPage={(p) => setViewer(v => {
            if (!v) return v;
            const i = v.pages.indexOf(p);
            return i >= 0 ? { ...v, index: i } : v;
          })}
          onExpand={() => { setFullViewer(viewer); setViewer(null); }}
        />
      )}
    </Box>
  );
}
