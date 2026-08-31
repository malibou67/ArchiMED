import { useState, useEffect, type ReactNode } from 'react';
import {
  Box,
  Paper,
  Typography,
  Slider,
  TextField,
  Button,
  Collapse,
  Alert,
  Stack,
  Divider,
  Chip,
  CircularProgress,
  Skeleton,
  Snackbar,
  Tooltip,
  Grid,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import {
  Memory as MemoryIcon,
  Speed as SpeedIcon,
  ManageSearch as IndexIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  RestartAlt as RestartAltIcon,
  Computer as ComputerIcon,
  Translate as TranslateIcon,
  InfoOutlined as InfoIcon,
  GitHub as GitHubIcon,
  ContentCopy as CopyIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { settingsApi, SettingsResponse } from '../api/settings';
import { systemApi, SystemRequirements, MachineIdentity } from '../api/system';

// Garder en phase avec version="..." dans backend/main.py
const APP_VERSION = '1.0.0';
const GITHUB_URL = 'https://github.com/malibou67/ArchiMED';
const CITATION_BIBTEX = `@software{archimed_2026,
  author  = {Veith, Gilles and Zvenigorosky, Vincent},
  title   = {ArchiMED: An automated search engine for OCR/HTR-transcribed documents},
  year    = {2026},
  url     = {https://github.com/malibou67/ArchiMED}
}`;

const TEAM_MEMBERS = [
  { name: 'about.teamMember1', role: 'about.teamMember1Role', lab: 'about.teamMember1Lab' },
  { name: 'about.teamMember2', role: 'about.teamMember2Role', lab: 'about.teamMember2Lab' },
];

function SectionSkeleton({ lines }: { lines: number }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, height: '100%' }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <Skeleton variant="circular" width={24} height={24} />
        <Skeleton variant="text" width={160} />
      </Stack>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} variant="text" width={`${85 - (i % 3) * 15}%`} />
      ))}
    </Paper>
  );
}

function DepChip({ name, dep }: { name: string; dep: { ok: boolean; version: string | null; error?: string | null } | null }) {
  const label = dep?.version ? `${name} ${dep.version}` : name;
  const chip = (
    <Chip
      size="small"
      variant="outlined"
      color={dep?.ok ? 'success' : 'error'}
      icon={dep?.ok ? <CheckCircleIcon fontSize="small" /> : <CancelIcon fontSize="small" />}
      label={label}
    />
  );
  return dep?.error ? <Tooltip title={dep.error}>{chip}</Tooltip> : chip;
}

function InfoRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ py: 0.75 }}>
      <Typography variant="body2" color="text.secondary">{label}</Typography>
      {value}
    </Stack>
  );
}

export default function SettingsPage() {
  const { t, i18n } = useTranslation(['settings', 'common']);
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [requirements, setRequirements] = useState<SystemRequirements | null>(null);
  const [loading, setLoading] = useState(true);
  const [reqLoading, setReqLoading] = useState(true);

  const [auto, setAuto] = useState(true);
  const [workers, setWorkers] = useState(4);
  const [threads, setThreads] = useState(1);
  const [poolMin, setPoolMin] = useState(3);
  const [indexWorkers, setIndexWorkers] = useState(4);
  const [indexPoolMin, setIndexPoolMin] = useState(200);
  const [advanced, setAdvanced] = useState(false);
  const [citeOpen, setCiteOpen] = useState(false);

  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [snack, setSnack] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Identité de CE poste (multi-PC) — stockée localement, hors NAS.
  const [identity, setIdentity] = useState<MachineIdentity | null>(null);
  const [machineLabel, setMachineLabel] = useState('');
  const [operator, setOperator] = useState('');
  const [savingIdentity, setSavingIdentity] = useState(false);

  const applyData = (d: SettingsResponse) => {
    setData(d);
    setAuto(d.stored.ocr_workers == null);
    // Le réglage est partagé entre postes : on l'affiche ramené à ce que CETTE machine peut
    // tenir (le backend applique de toute façon ce plafond à l'exécution).
    setWorkers(Math.min(d.stored.ocr_workers ?? d.system.recommended_workers, d.system.max_workers));
    setThreads(d.effective.ocr_threads_per_worker);
    setPoolMin(d.effective.ocr_pool_min_pages);
    setIndexWorkers(Math.min(d.effective.index_workers, d.system.cpu_count));
    setIndexPoolMin(d.effective.index_pool_min_pages);
  };

  useEffect(() => {
    settingsApi.get()
      .then(applyData)
      .catch(() => setError(t('snack.loadError')))
      .finally(() => setLoading(false));
    systemApi.getRequirements()
      .then(setRequirements)
      .catch(() => {})
      .finally(() => setReqLoading(false));
    systemApi.getIdentity()
      .then((id) => { setIdentity(id); setMachineLabel(id.machine_label); setOperator(id.operator); })
      .catch(() => {});
  }, []);

  const identityDirty = !!identity && (machineLabel !== identity.machine_label || operator !== identity.operator);

  const saveIdentity = async () => {
    try {
      setSavingIdentity(true);
      setError(null);
      const id = await systemApi.updateIdentity({ machine_label: machineLabel, operator });
      setIdentity(id);
      setMachineLabel(id.machine_label);
      setOperator(id.operator);
      setSnack(t('snack.identitySaved'));
    } catch (err: any) {
      setError(err?.response?.data?.detail || t('snack.identitySaveError'));
    } finally {
      setSavingIdentity(false);
    }
  };

  const cpu = data?.system.cpu_count ?? 1;
  // Plafond de CE poste : sur GPU c'est la VRAM qui limite, pas les cœurs.
  const maxWorkers = data?.system.max_workers ?? cpu;
  const gpuCap = data?.system.gpu_max_workers ?? null;
  const recommended = data?.system.recommended_workers ?? 1;
  const effectiveWorkers = auto ? recommended : workers;
  const freeCores = Math.max(0, cpu - effectiveWorkers);

  const dirty = !!data && (
    (auto ? data.stored.ocr_workers != null : workers !== data.effective.ocr_workers)
    || threads !== data.effective.ocr_threads_per_worker
    || poolMin !== data.effective.ocr_pool_min_pages
    || indexWorkers !== data.effective.index_workers
    || indexPoolMin !== data.effective.index_pool_min_pages
  );

  const save = async () => {
    try {
      setSaving(true);
      setError(null);
      const res = await settingsApi.update({
        ocr_workers: auto ? null : workers,
        ocr_threads_per_worker: threads,
        ocr_pool_min_pages: poolMin,
        index_workers: indexWorkers,
        index_pool_min_pages: indexPoolMin,
      });
      applyData(res);
      setSnack(t('snack.saved'));
    } catch (err: any) {
      setError(err?.response?.data?.detail || t('snack.saveError'));
    } finally {
      setSaving(false);
    }
  };

  const resetDefaults = async () => {
    try {
      setResetting(true);
      setError(null);
      const res = await settingsApi.update({
        ocr_workers: null,
        ocr_threads_per_worker: null,
        ocr_mixed_precision: null,
        ocr_pool_min_pages: null,
        index_workers: null,
        index_pool_min_pages: null,
      });
      applyData(res);
      setSnack(t('snack.defaultsRestored'));
    } catch (err: any) {
      setError(err?.response?.data?.detail || t('snack.resetError'));
    } finally {
      setResetting(false);
    }
  };

  const cancel = () => {
    if (data) applyData(data);
  };

  const copyCitation = async () => {
    try {
      await navigator.clipboard.writeText(CITATION_BIBTEX);
      setSnack(t('about.citationCopied'));
    } catch {
      /* clipboard indisponible (contexte non sécurisé) — on ignore */
    }
  };

  const cudaOk = requirements?.cuda?.ok ?? false;
  const vram = requirements?.cuda?.vram_gb;
  const ram = data?.system.ram_total_gb;
  const diskFree = data?.system.disk_free_gb;

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      {/* ── Langue de l'interface (stockée localement, par poste) ── */}
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <TranslateIcon color="action" />
          <Typography variant="subtitle1" fontWeight={600}>{t('common:language.title')}</Typography>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          {t('common:language.description')}
        </Typography>
        <ToggleButtonGroup
          exclusive
          size="small"
          color="primary"
          value={i18n.language.startsWith('fr') ? 'fr' : 'en'}
          onChange={(_, v) => { if (v) i18n.changeLanguage(v); }}
        >
          <ToggleButton value="en" sx={{ textTransform: 'none', px: 2 }}>{t('common:language.english')}</ToggleButton>
          <ToggleButton value="fr" sx={{ textTransform: 'none', px: 2 }}>{t('common:language.french')}</ToggleButton>
        </ToggleButtonGroup>
      </Paper>

      <Grid container spacing={2} alignItems="stretch">
        {/* ── Colonne principale : Performance OCR ── */}
        <Grid size={{ xs: 12, md: 7, xl: 8 }}>
          {loading ? <SectionSkeleton lines={7} /> : (
            <Paper variant="outlined" sx={{ p: 2, height: '100%' }}>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
                <SpeedIcon color="action" />
                <Typography variant="subtitle1" fontWeight={600}>{t('performance.title')}</Typography>
                <Button
                  size="small"
                  startIcon={<RestartAltIcon />}
                  onClick={resetDefaults}
                  disabled={resetting || saving}
                  sx={{ textTransform: 'none', ml: 'auto' }}
                >
                  {resetting ? t('performance.resetting') : t('performance.reset')}
                </Button>
              </Stack>

              <Typography variant="body2" sx={{ mb: 1 }}>{t('performance.parallelPages')}</Typography>
              <ToggleButtonGroup
                exclusive
                size="small"
                color="primary"
                value={auto ? 'auto' : 'manual'}
                onChange={(_, v) => { if (v != null) setAuto(v === 'auto'); }}
              >
                <ToggleButton value="auto" sx={{ textTransform: 'none', px: 2 }}>{t('performance.auto')}</ToggleButton>
                <ToggleButton value="manual" sx={{ textTransform: 'none', px: 2 }}>{t('performance.manual')}</ToggleButton>
              </ToggleButtonGroup>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1, mb: 1.5 }}>
                {auto
                  ? t('performance.autoHint', { recommended, cpu })
                  : t('performance.manualHint')}
              </Typography>

              <Collapse in={!auto} unmountOnExit>
                <Box sx={{ px: 1, pt: 1 }}>
                  <Typography variant="body2" gutterBottom>
                    {t('performance.pagesInParallel')} <strong>{workers}</strong>
                  </Typography>
                  <Slider
                    value={workers}
                    onChange={(_, v) => setWorkers(v as number)}
                    min={1}
                    max={maxWorkers}
                    step={1}
                    marks={[{ value: 1, label: '1' }, { value: recommended, label: `${recommended}` }, { value: maxWorkers, label: `${maxWorkers}` }]}
                    valueLabelDisplay="auto"
                  />
                  {gpuCap != null && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                      {t('performance.gpuCapHint', { cap: gpuCap, vram: data?.system.gpu_vram_gb ?? '?', cpu })}
                    </Typography>
                  )}
                </Box>
              </Collapse>

              <Alert severity={freeCores === 0 ? 'warning' : 'info'} variant="outlined" sx={{ mt: 1 }}>
                {freeCores === 0
                  ? t('performance.allCoresWarning')
                  : t('performance.coresBusy', { effective: effectiveWorkers, free: freeCores })}
                {t('performance.moreWorkers')}
              </Alert>

              <Button
                size="small"
                onClick={() => setAdvanced((a) => !a)}
                endIcon={advanced ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                sx={{ mt: 1.5, textTransform: 'none' }}
              >
                {t('performance.advancedOptions')}
              </Button>
              <Collapse in={advanced} unmountOnExit>
                <Divider sx={{ my: 1.5 }} />
                <Stack spacing={2}>
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                    <Box sx={{ flex: 1 }}>
                      <TextField
                        type="number"
                        size="small"
                        fullWidth
                        label={t('performance.threadsPerWorker')}
                        value={threads}
                        onChange={(e) => setThreads(Math.max(1, Math.min(16, parseInt(e.target.value || '1', 10))))}
                        inputProps={{ min: 1, max: 16 }}
                      />
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                        {t('performance.threadsHint')}
                      </Typography>
                    </Box>
                    <Box sx={{ flex: 1 }}>
                      <TextField
                        type="number"
                        size="small"
                        fullWidth
                        label={t('performance.poolThreshold')}
                        value={poolMin}
                        onChange={(e) => setPoolMin(Math.max(1, Math.min(50, parseInt(e.target.value || '1', 10))))}
                        inputProps={{ min: 1, max: 50 }}
                      />
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                        {t('performance.poolThresholdHint')}
                      </Typography>
                    </Box>
                  </Stack>
                </Stack>
              </Collapse>
            </Paper>
          )}
        </Grid>

        {/* ── Colonne latérale : Système ── */}
        <Grid size={{ xs: 12, md: 5, xl: 4 }}>
          {loading ? <SectionSkeleton lines={5} /> : (
            <Paper variant="outlined" sx={{ p: 2, height: '100%' }}>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                <MemoryIcon color="action" />
                <Typography variant="subtitle1" fontWeight={600}>{t('system.title')}</Typography>
              </Stack>

              <InfoRow label={t('system.processor')} value={<Typography variant="body2">{t('system.cores', { count: cpu })}</Typography>} />
              {ram != null && (
                <InfoRow label={t('system.ram')} value={<Typography variant="body2">{t('system.gb', { value: ram })}</Typography>} />
              )}
              {diskFree != null && (
                <InfoRow
                  label={t('system.diskFree')}
                  value={
                    <Typography variant="body2" color={diskFree < 5 ? 'warning.main' : undefined}>
                      {t('system.gb', { value: diskFree })}
                    </Typography>
                  }
                />
              )}
              <InfoRow
                label={t('system.gpu')}
                value={reqLoading ? (
                  <Chip size="small" variant="outlined" icon={<CircularProgress size={12} />} label={t('system.checking')} />
                ) : (
                  <Chip
                    size="small"
                    variant="outlined"
                    color={cudaOk ? 'success' : 'default'}
                    icon={cudaOk ? <CheckCircleIcon fontSize="small" /> : <CancelIcon fontSize="small" />}
                    label={cudaOk
                      ? `${requirements?.cuda?.device}${vram != null ? ` (${t('system.gb', { value: vram })})` : ''}`
                      : t('system.noCuda')}
                  />
                )}
              />

              {!reqLoading && requirements && (
                <>
                  <Divider sx={{ my: 1.5 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    {t('system.ocrDependencies')}
                  </Typography>
                  <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                    <DepChip name="kraken" dep={requirements.kraken} />
                    <DepChip name="torch" dep={requirements.torch} />
                    <DepChip name="torchvision" dep={requirements.torchvision} />
                  </Stack>
                </>
              )}
            </Paper>
          )}
        </Grid>
      </Grid>

      {/* ── Performance indexation ──
          Carte à part, et pas un repli de la carte OCR : l'indexation a son propre pool, qui
          lit et analyse les XML sans jamais toucher au GPU. Rangé sous « Performance OCR »,
          le réglage était introuvable. */}
      {loading ? <Box sx={{ mt: 2 }}><SectionSkeleton lines={3} /></Box> : (
        <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
            <IndexIcon color="action" />
            <Typography variant="subtitle1" fontWeight={600}>{t('indexing.title')}</Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
            {t('indexing.description')}
          </Typography>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <Box sx={{ flex: 1 }}>
              <TextField
                type="number"
                size="small"
                fullWidth
                label={t('indexing.workers')}
                value={indexWorkers}
                onChange={(e) => setIndexWorkers(Math.max(1, Math.min(cpu, parseInt(e.target.value || '1', 10))))}
                inputProps={{ min: 1, max: cpu }}
              />
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                {t('indexing.workersHint', { recommended: data?.system.recommended_index_workers ?? 1, cpu })}
              </Typography>
            </Box>
            <Box sx={{ flex: 1 }}>
              <TextField
                type="number"
                size="small"
                fullWidth
                label={t('indexing.threshold')}
                value={indexPoolMin}
                onChange={(e) => setIndexPoolMin(Math.max(1, Math.min(5000, parseInt(e.target.value || '1', 10))))}
                inputProps={{ min: 1, max: 5000 }}
              />
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                {t('indexing.thresholdHint')}
              </Typography>
            </Box>
          </Stack>
        </Paper>
      )}

      {/* ── Ce poste (identité multi-PC) ── */}
      <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <ComputerIcon color="action" />
          <Typography variant="subtitle1" fontWeight={600}>{t('machine.title')}</Typography>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1.5 }}>
          {t('machine.description')}
        </Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
          <TextField
            size="small"
            label={t('machine.name')}
            value={machineLabel}
            onChange={(e) => setMachineLabel(e.target.value)}
            placeholder={identity?.machine_id ?? ''}
            sx={{ flex: 1 }}
          />
          <TextField
            size="small"
            label={t('machine.operator')}
            value={operator}
            onChange={(e) => setOperator(e.target.value)}
            sx={{ flex: 1 }}
          />
          <Button variant="contained" onClick={saveIdentity} disabled={!identityDirty || savingIdentity}>
            {savingIdentity ? t('common:actions.saving') : t('common:actions.save')}
          </Button>
        </Stack>
        {identity && (
          <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
            {t('machine.technicalId', { id: identity.machine_id })}
          </Typography>
        )}
      </Paper>

      {/* ── À propos de l'application ── */}
      <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <InfoIcon color="action" />
          <Typography variant="subtitle1" fontWeight={600}>{t('about.title')}</Typography>
        </Stack>

        <Typography variant="body2" fontWeight={600} sx={{ mt: 1 }}>ArchiMED</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
          {t('about.tagline')}
        </Typography>

        <InfoRow label={t('about.version')} value={<Typography variant="body2">{APP_VERSION}</Typography>} />
        <InfoRow label={t('about.license')} value={<Typography variant="body2">MIT</Typography>} />
        <InfoRow label={t('about.copyright')} value={<Typography variant="body2">{t('about.copyrightValue')}</Typography>} />

        <Button
          component="a"
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          size="small"
          startIcon={<GitHubIcon />}
          sx={{ textTransform: 'none', mt: 1 }}
        >
          {t('about.github')}
        </Button>

        <Divider sx={{ my: 1.5 }} />

        <Typography variant="caption" color="text.secondary" fontWeight={600} sx={{ display: 'block' }}>
          {t('about.teamTitle')}
        </Typography>
        {TEAM_MEMBERS.map(({ name, role, lab }) => (
          <Box key={name} sx={{ mt: 0.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              {t(name)} — {t(role)}
            </Typography>
            <Typography variant="caption" color="text.disabled" sx={{ display: 'block', pl: 1 }}>
              {t(lab)}
            </Typography>
          </Box>
        ))}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
          {t('about.funding')}
        </Typography>

        <Button
          size="small"
          onClick={() => setCiteOpen((c) => !c)}
          endIcon={citeOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          sx={{ mt: 1, textTransform: 'none' }}
        >
          {t('about.citeTitle')}
        </Button>
        <Collapse in={citeOpen} unmountOnExit>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5, mb: 1 }}>
            {t('about.citeHint')}
          </Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              bgcolor: 'action.hover',
              borderRadius: 1,
              fontSize: '0.75rem',
              fontFamily: 'monospace',
              overflowX: 'auto',
              whiteSpace: 'pre',
            }}
          >
            {CITATION_BIBTEX}
          </Box>
          <Button
            size="small"
            startIcon={<CopyIcon />}
            onClick={copyCitation}
            sx={{ textTransform: 'none', mt: 1 }}
          >
            {t('about.copyCitation')}
          </Button>
        </Collapse>
      </Paper>

      {/* ── Barre d'action (Enregistrer grisé tant que rien n'est modifié) ── */}
      {!loading && (
        <Paper
          elevation={3}
          sx={{
            position: 'sticky',
            bottom: 16,
            mt: 2,
            p: 1.5,
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            zIndex: 1,
          }}
        >
          <Button variant="contained" onClick={save} disabled={!dirty || saving}>
            {saving ? t('common:actions.saving') : t('common:actions.save')}
          </Button>
          <Button onClick={cancel} disabled={!dirty || saving}>{t('common:actions.cancel')}</Button>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
            {t('actionBar.applyNote')}
          </Typography>
        </Paper>
      )}

      <Snackbar
        open={!!snack}
        autoHideDuration={2500}
        onClose={() => setSnack(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" variant="filled" onClose={() => setSnack(null)}>
          {snack}
        </Alert>
      </Snackbar>
    </Box>
  );
}
