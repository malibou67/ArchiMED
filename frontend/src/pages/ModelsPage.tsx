import { useState, useEffect, useMemo } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  IconButton,
  InputAdornment,
  Paper,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  alpha,
} from '@mui/material';
import {
  Add as AddIcon,
  CheckCircle as CheckCircleIcon,
  Clear as ClearIcon,
  Delete as DeleteIcon,
  Edit as EditIcon,
  Memory as MemoryIcon,
  Polyline as PolylineIcon,
  Search as SearchIcon,
  TextFields as TextFieldsIcon,
  UploadFile as UploadFileIcon,
} from '@mui/icons-material';
import { useTranslation, Trans } from 'react-i18next';
import { modelsApi } from '../api/models';
import EmptyState from '../components/EmptyState';
import Loader from '../components/Loader';
import { ModelMetadata, ModelType } from '../types';

// Normalisation pour la recherche : minuscules, sans diacritiques.
const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

// Même règle de génération d'ID que le backend (extract_mlmodel_metadata).
const slugify = (s: string) =>
  norm(s).replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '');

/** Parse une précision saisie en pourcentage ("98,4" ou "98.4").
 * Retourne { ok: true, value } (value en décimal 0-1, ou null si champ vide)
 * ou { ok: false } si la saisie est invalide. */
function parsePctInput(s: string): { ok: boolean; value?: number | null } {
  const t = s.trim();
  if (!t) return { ok: true, value: null };
  const v = parseFloat(t.replace(',', '.'));
  if (isNaN(v) || v < 0 || v > 100) return { ok: false };
  return { ok: true, value: v / 100 };
}

export default function ModelsPage() {
  const { t } = useTranslation(['ocr', 'common']);
  const [models, setModels] = useState<ModelMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filtres de la liste
  const [typeFilter, setTypeFilter] = useState<'all' | ModelType>('all');
  const [searchFilter, setSearchFilter] = useState('');

  // Add dialog
  const [openDialog, setOpenDialog] = useState(false);
  const [newModel, setNewModel] = useState<Partial<ModelMetadata>>({ id: '', name: '', type: 'ocr', description: '', version: '' });
  const [accuracyPct, setAccuracyPct] = useState(''); // saisie en % (texte), convertie au submit
  const [idTouched, setIdTouched] = useState(false);  // l'utilisateur a édité l'ID → on arrête de le générer
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Detail dialog
  const [detailModel, setDetailModel] = useState<ModelMetadata | null>(null);

  // Edit dialog
  const [editTarget, setEditTarget] = useState<ModelMetadata | null>(null);
  const [editData, setEditData] = useState<Partial<ModelMetadata>>({});
  const [editAccuracyPct, setEditAccuracyPct] = useState('');
  const [editError, setEditError] = useState<string | null>(null);

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<ModelMetadata | null>(null);

  useEffect(() => { loadModels(); }, []);

  const loadModels = async () => {
    try {
      setLoading(true);
      const data = await modelsApi.getAll();
      setModels(data);
      setError(null);
    } catch (err) {
      setError(t('errors.loadModels'));
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleFileSelect = async (file: File | null) => {
    setSelectedFile(file);
    if (!file) return;
    setExtracting(true);
    try {
      const metadata = await modelsApi.extractMetadata(file);
      setNewModel(prev => ({
        ...prev,
        id: metadata.id || prev.id || '',
        name: metadata.name || prev.name || '',
        description: metadata.description || prev.description || '',
        version: metadata.version || prev.version || '',
        type: (metadata.type as ModelType) || prev.type || 'ocr',
      }));
      if (metadata.accuracy != null) {
        setAccuracyPct((metadata.accuracy * 100).toFixed(1));
      }
    } catch {
      const stem = file.name.replace(/\.mlmodel$/, '');
      setNewModel(prev => ({ ...prev, id: prev.id || slugify(stem), name: prev.name || stem }));
    } finally {
      setExtracting(false);
    }
  };

  const resetAddDialog = () => {
    setOpenDialog(false);
    setAddError(null);
    setNewModel({ id: '', name: '', type: 'ocr', description: '', version: '' });
    setAccuracyPct('');
    setIdTouched(false);
    setSelectedFile(null);
  };

  // Tous les champs sont requis à l'ajout (fichier compris).
  const addAccuracyParsed = parsePctInput(accuracyPct);
  const addFormValid = !extracting
    && !!selectedFile
    && !!newModel.name?.trim()
    && !!newModel.id?.trim()
    && !!newModel.description?.trim()
    && !!newModel.version?.trim()
    && accuracyPct.trim() !== ''
    && addAccuracyParsed.ok;

  const handleAddModel = async () => {
    setAddError(null);
    if (!addFormValid) { setAddError(t('modelsPage.allFieldsRequiredFile')); return; }
    if (models.some(m => m.name === newModel.name)) { setAddError(t('modelsPage.nameExists', { name: newModel.name })); return; }
    if (models.some(m => m.id === newModel.id)) { setAddError(t('modelsPage.idExists', { id: newModel.id })); return; }
    try {
      await modelsApi.create({ ...newModel, accuracy: addAccuracyParsed.value ?? undefined }, selectedFile);
      resetAddDialog();
      loadModels();
    } catch (err) {
      setAddError(t('modelsPage.addError'));
      console.error(err);
    }
  };

  const openEdit = (model: ModelMetadata) => {
    setEditData({ name: model.name, type: model.type, description: model.description || '', version: model.version || '' });
    setEditAccuracyPct(model.accuracy != null ? (model.accuracy * 100).toFixed(1) : '');
    setEditError(null);
    setEditTarget(model);
  };

  const handleSaveEdit = async () => {
    if (!editTarget) return;
    const accuracy = parsePctInput(editAccuracyPct);
    if (!accuracy.ok) { setEditError(t('modelsPage.accuracyRange')); return; }
    try {
      await modelsApi.update(editTarget.id, { ...editData, accuracy: accuracy.value });
      setEditTarget(null);
      loadModels();
    } catch (err) {
      setError(t('modelsPage.updateError'));
      console.error(err);
    }
  };

  // ── Liste filtrée (type + recherche) ──
  const filteredModels = useMemo(() => {
    const q = norm(searchFilter.trim());
    return models.filter(m => {
      if (typeFilter !== 'all' && m.type !== typeFilter) return false;
      if (q && !norm(`${m.name} ${m.id} ${m.description ?? ''}`).includes(q)) return false;
      return true;
    });
  }, [models, typeFilter, searchFilter]);

  const ocrCount = models.filter(m => m.type === 'ocr').length;
  const segCount = models.filter(m => m.type === 'segmentation').length;

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await modelsApi.delete(deleteTarget.id);
      setDeleteTarget(null);
      loadModels();
    } catch (err) {
      setError(t('modelsPage.deleteError'));
      console.error(err);
    }
  };

  if (loading) return <Loader message={t('loading.models')} minHeight="60vh" />;

  return (
    // pt : compense la remontée (mt négatif) du conteneur OCR pour redonner de l'air
    // en haut de cette page (la page de lancement, elle, reste plus resserrée).
    <Box sx={{ pt: 3 }}>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      {/* ── Filtres + ajout ─────────────────────────────────── */}
      <Box sx={{ mb: 2, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        {models.length > 0 && (
          <>
            <ToggleButtonGroup
              value={typeFilter}
              exclusive
              onChange={(_e, val: 'all' | ModelType | null) => { if (val) setTypeFilter(val); }}
              size="small"
            >
              <ToggleButton value="all">{t('modelsPage.filterAll', { count: models.length })}</ToggleButton>
              <ToggleButton value="ocr">{t('modelsPage.filterOcr', { count: ocrCount })}</ToggleButton>
              <ToggleButton value="segmentation">{t('modelsPage.filterSeg', { count: segCount })}</ToggleButton>
            </ToggleButtonGroup>
            <TextField
              size="small"
              placeholder={t('modelsPage.searchPlaceholder')}
              value={searchFilter}
              onChange={e => setSearchFilter(e.target.value)}
              sx={{ width: 280 }}
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
          variant="contained"
          disableElevation
          startIcon={<AddIcon />}
          onClick={() => setOpenDialog(true)}
          sx={{ flexShrink: 0, ml: 'auto' }}
        >
          {t('modelsPage.addModel')}
        </Button>
      </Box>

      {/* ── Tableau ─────────────────────────────────────────── */}
      {models.length === 0 ? (
        <EmptyState
          icon={<MemoryIcon />}
          title={t('modelsPage.emptyTitle')}
          description={t('modelsPage.emptyDesc')}
          action={{ label: t('modelsPage.addModel'), onClick: () => setOpenDialog(true) }}
        />
      ) : filteredModels.length === 0 ? (
        <Box sx={{ textAlign: 'center', mt: 10, color: 'text.disabled' }}>
          <MemoryIcon sx={{ fontSize: 56, opacity: 0.2, mb: 1 }} />
          <Typography variant="body2" color="text.disabled">
            {t('modelsPage.noMatch')}
          </Typography>
        </Box>
      ) : (
        <Paper elevation={0} sx={{ border: '1.5px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
          {/* En-têtes de colonnes */}
          <Box sx={{
            display: 'flex', alignItems: 'center', gap: 2,
            px: 2.5, py: 1,
            bgcolor: 'grey.50',
            borderBottom: '1.5px solid', borderColor: 'divider',
          }}>
            <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ minWidth: 220, flexShrink: 0, letterSpacing: '0.05em' }}>{t('modelsPage.colName')}</Typography>
            <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ minWidth: 120, flexShrink: 0, letterSpacing: '0.05em' }}>{t('modelsPage.colType')}</Typography>
            <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ flex: 1, letterSpacing: '0.05em' }}>{t('modelsPage.colDescription')}</Typography>
            <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ minWidth: 72, flexShrink: 0, textAlign: 'right', letterSpacing: '0.05em' }}>{t('modelsPage.colVersion')}</Typography>
            <Typography variant="caption" color="text.secondary" fontWeight={700} sx={{ minWidth: 80, flexShrink: 0, textAlign: 'right', letterSpacing: '0.05em' }}>{t('modelsPage.colAccuracy')}</Typography>
            <Box sx={{ width: 72, flexShrink: 0 }} />
          </Box>

          {/* Lignes */}
          {filteredModels.map((model, i) => (
            <Box
              key={model.id}
              onClick={() => setDetailModel(model)}
              sx={{
                display: 'flex', alignItems: 'center', gap: 2,
                px: 2.5, py: 1.5,
                borderTop: i > 0 ? '1px solid' : 'none',
                borderColor: 'divider',
                cursor: 'pointer',
                '&:hover': { bgcolor: 'action.hover', '& .row-actions': { opacity: 1 } },
              }}
            >
              <Box sx={{ minWidth: 220, flexShrink: 0 }}>
                <Typography variant="body2" fontWeight={500}>{model.name}</Typography>
                <Typography variant="caption" color="text.disabled" sx={{ fontFamily: 'monospace', fontSize: '0.7rem' }}>{model.id}</Typography>
              </Box>

              <Box sx={{ minWidth: 120, flexShrink: 0 }}>
                <Chip
                  label={model.type === 'segmentation' ? t('modelsPage.typeSeg') : t('modelsPage.typeOcr')}
                  size="small"
                  color={model.type === 'segmentation' ? 'secondary' : 'primary'}
                  variant="outlined"
                  sx={{ fontWeight: 500 }}
                />
              </Box>

              <Typography variant="body2" color="text.secondary" sx={{
                flex: 1,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {model.description || <span style={{ opacity: 0.4 }}>—</span>}
              </Typography>

              <Typography variant="body2" color="text.secondary" sx={{ minWidth: 72, flexShrink: 0, textAlign: 'right', fontFamily: 'monospace', fontSize: '0.8rem' }}>
                {model.version || '—'}
              </Typography>

              <Typography variant="body2" fontWeight={model.accuracy != null ? 500 : 400} color={model.accuracy != null ? 'success.main' : 'text.disabled'} sx={{ minWidth: 80, flexShrink: 0, textAlign: 'right' }}>
                {model.accuracy != null ? `${(model.accuracy * 100).toFixed(1)} %` : '—'}
              </Typography>

              <Box className="row-actions" sx={{ display: 'flex', gap: 0.5, opacity: 0, transition: 'opacity 0.15s', flexShrink: 0, width: 72, justifyContent: 'flex-end' }}>
                <Tooltip title={t('modelsPage.edit')}>
                  <IconButton size="small" color="primary" onClick={e => { e.stopPropagation(); openEdit(model); }}>
                    <EditIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title={t('modelsPage.delete')}>
                  <IconButton size="small" color="error" onClick={e => { e.stopPropagation(); setDeleteTarget(model); }}>
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>
          ))}
        </Paper>
      )}

      {/* ── Dialog : Ajouter ────────────────────────────────── */}
      <Dialog open={openDialog} onClose={resetAddDialog} maxWidth="sm" fullWidth slotProps={{ paper: { sx: { borderRadius: 3 } } }}>
        <DialogTitle sx={{ pb: 0.5 }}>
          {t('modelsPage.addModel')}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t('modelsPage.addSubtitle')}
          </Typography>
        </DialogTitle>
        <DialogContent>
          {addError && <Alert severity="error" sx={{ mb: 2, mt: 1 }} onClose={() => setAddError(null)}>{addError}</Alert>}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>

            {/* Zone de dépôt du fichier */}
            <Box
              component="label"
              onDragOver={e => e.preventDefault()}
              onDrop={e => {
                e.preventDefault();
                const f = e.dataTransfer.files?.[0];
                if (f && f.name.endsWith('.mlmodel')) handleFileSelect(f);
              }}
              sx={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                gap: 0.5, p: 2.5, borderRadius: 2, cursor: 'pointer', textAlign: 'center',
                border: '2px dashed',
                borderColor: selectedFile ? 'success.main' : 'divider',
                bgcolor: (theme) => selectedFile ? alpha(theme.palette.success.main, 0.06) : 'action.hover',
                transition: 'border-color 0.15s, background-color 0.15s',
                '&:hover': { borderColor: 'primary.main' },
              }}
            >
              <input type="file" accept=".mlmodel" hidden onChange={e => handleFileSelect(e.target.files?.[0] || null)} />
              {extracting ? (
                <>
                  <CircularProgress size={28} />
                  <Typography variant="body2" color="text.secondary">{t('modelsPage.extracting')}</Typography>
                </>
              ) : selectedFile ? (
                <>
                  <CheckCircleIcon color="success" sx={{ fontSize: 32 }} />
                  <Typography variant="body2" fontWeight={600}>{selectedFile.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {t('modelsPage.fileSize', { size: (selectedFile.size / 1024 / 1024).toFixed(1) })}
                  </Typography>
                </>
              ) : (
                <>
                  <UploadFileIcon sx={{ fontSize: 32, color: 'text.disabled' }} />
                  <Typography variant="body2" fontWeight={500}>{t('modelsPage.dropHere')}</Typography>
                  <Typography variant="caption" color="text.secondary">{t('modelsPage.orBrowse')}</Typography>
                </>
              )}
            </Box>

            {/* Champs affichés une fois le fichier choisi : l'extraction préremplit, on complète */}
            <Collapse in={!!selectedFile && !extracting}>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <ToggleButtonGroup
                  value={newModel.type || 'ocr'}
                  exclusive
                  onChange={(_e, val: ModelType | null) => { if (val) setNewModel({ ...newModel, type: val }); }}
                  size="small"
                  fullWidth
                >
                  <ToggleButton value="ocr"><TextFieldsIcon fontSize="small" sx={{ mr: 1 }} />{t('modelsPage.typeOcr')}</ToggleButton>
                  <ToggleButton value="segmentation"><PolylineIcon fontSize="small" sx={{ mr: 1 }} />{t('modelsPage.typeSeg')}</ToggleButton>
                </ToggleButtonGroup>

                <TextField
                  label={t('modelsPage.name')}
                  required
                  value={newModel.name}
                  onChange={e => setNewModel(prev => ({
                    ...prev,
                    name: e.target.value,
                    id: idTouched ? prev.id : slugify(e.target.value),
                  }))}
                  size="small"
                />
                <TextField
                  label="ID"
                  required
                  value={newModel.id}
                  onChange={e => { setIdTouched(true); setNewModel({ ...newModel, id: e.target.value }); }}
                  helperText={t('modelsPage.idHelper')}
                  size="small"
                />
                <TextField
                  label={t('modelsPage.description')}
                  required
                  multiline
                  rows={2}
                  value={newModel.description}
                  onChange={e => setNewModel({ ...newModel, description: e.target.value })}
                  size="small"
                />
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <TextField
                    label={t('modelsPage.version')}
                    required
                    value={newModel.version}
                    onChange={e => setNewModel({ ...newModel, version: e.target.value })}
                    size="small"
                    sx={{ flex: 1 }}
                  />
                  <TextField
                    label={t('modelsPage.accuracy')}
                    required
                    value={accuracyPct}
                    onChange={e => setAccuracyPct(e.target.value)}
                    error={!addAccuracyParsed.ok}
                    helperText={!addAccuracyParsed.ok ? t('modelsPage.accuracyError') : t('modelsPage.accuracyExample')}
                    size="small"
                    sx={{ flex: 1 }}
                  />
                </Box>
              </Box>
            </Collapse>
          </Box>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5, pt: 1 }}>
          {selectedFile && !addFormValid && !extracting && (
            <Typography variant="caption" color="text.secondary" sx={{ mr: 'auto' }}>
              {t('modelsPage.allFieldsRequired')}
            </Typography>
          )}
          <Button onClick={resetAddDialog}>{t('common:actions.cancel')}</Button>
          <Button onClick={handleAddModel} variant="contained" disableElevation startIcon={<AddIcon />} disabled={!addFormValid}>
            {t('modelsPage.add')}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Dialog : Détails ────────────────────────────────── */}
      <Dialog open={!!detailModel} onClose={() => setDetailModel(null)} maxWidth="sm" fullWidth>
        {detailModel && (
          <>
            <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Box>
                {detailModel.name}
                <Chip
                  label={detailModel.type === 'segmentation' ? t('modelsPage.typeSeg') : t('modelsPage.typeOcr')}
                  size="small"
                  color={detailModel.type === 'segmentation' ? 'secondary' : 'primary'}
                  variant="outlined"
                  sx={{ ml: 1.5, fontWeight: 500 }}
                />
              </Box>
            </DialogTitle>
            <DialogContent>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
                {[
                  { label: 'ID', value: detailModel.id, mono: true },
                  { label: t('modelsPage.version'), value: detailModel.version },
                  { label: t('modelsPage.description'), value: detailModel.description },
                  { label: t('modelsPage.accuracyLabel'), value: detailModel.accuracy != null ? `${(detailModel.accuracy * 100).toFixed(1)} %` : undefined },
                  { label: t('modelsPage.file'), value: detailModel.file_path, mono: true },
                  { label: t('modelsPage.createdAt'), value: detailModel.created_at },
                ].filter(r => r.value).map(row => (
                  <Box key={row.label} sx={{ display: 'flex', gap: 2 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ minWidth: 90, flexShrink: 0, pt: 0.25 }}>{row.label}</Typography>
                    <Typography variant="body2" sx={{ wordBreak: 'break-all', fontFamily: row.mono ? 'monospace' : undefined }}>{row.value}</Typography>
                  </Box>
                ))}
              </Box>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setDetailModel(null)}>{t('common:actions.close')}</Button>
              <Button onClick={() => { setDetailModel(null); openEdit(detailModel); }} variant="outlined" startIcon={<EditIcon />}>{t('modelsPage.edit')}</Button>
            </DialogActions>
          </>
        )}
      </Dialog>

      {/* ── Dialog : Modifier ───────────────────────────────── */}
      <Dialog open={!!editTarget} onClose={() => setEditTarget(null)} maxWidth="sm" fullWidth>
        {editTarget && (
          <>
            <DialogTitle>{t('modelsPage.editTitle', { name: editTarget.name })}</DialogTitle>
            <DialogContent>
              {editError && <Alert severity="error" sx={{ mb: 2, mt: 1 }} onClose={() => setEditError(null)}>{editError}</Alert>}
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
                <Box>
                  <Typography variant="body2" sx={{ mb: 1 }}>{t('modelsPage.modelType')}</Typography>
                  <ToggleButtonGroup
                    value={editData.type || 'ocr'}
                    exclusive
                    onChange={(_e, val: ModelType | null) => { if (val) setEditData({ ...editData, type: val }); }}
                    size="small"
                    fullWidth
                  >
                    <ToggleButton value="ocr">{t('modelsPage.typeOcr')}</ToggleButton>
                    <ToggleButton value="segmentation">{t('modelsPage.typeSeg')}</ToggleButton>
                  </ToggleButtonGroup>
                </Box>
                <TextField label={t('modelsPage.name')} value={editData.name || ''} onChange={e => setEditData({ ...editData, name: e.target.value })} size="small" />
                <TextField label={t('modelsPage.description')} multiline rows={3} value={editData.description || ''} onChange={e => setEditData({ ...editData, description: e.target.value })} size="small" />
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <TextField label={t('modelsPage.version')} value={editData.version || ''} onChange={e => setEditData({ ...editData, version: e.target.value })} size="small" sx={{ flex: 1 }} />
                  <TextField
                    label={t('modelsPage.accuracy')}
                    value={editAccuracyPct}
                    onChange={e => setEditAccuracyPct(e.target.value)}
                    error={!parsePctInput(editAccuracyPct).ok}
                    helperText={t('modelsPage.accuracyEmptyHelper')}
                    size="small"
                    sx={{ flex: 1 }}
                  />
                </Box>
              </Box>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setEditTarget(null)}>{t('common:actions.cancel')}</Button>
              <Button onClick={handleSaveEdit} variant="contained" disableElevation>{t('common:actions.save')}</Button>
            </DialogActions>
          </>
        )}
      </Dialog>

      {/* ── Dialog : Supprimer ──────────────────────────────── */}
      <Dialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>{t('modelsPage.deleteTitle')}</DialogTitle>
        <DialogContent>
          <Typography>
            <Trans t={t} i18nKey="modelsPage.deleteConfirm" components={{ strong: <strong /> }} values={{ name: deleteTarget?.name }} />
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>{t('common:actions.cancel')}</Button>
          <Button onClick={handleDelete} color="error" variant="contained" disableElevation>{t('modelsPage.delete')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
