import {
  Alert,
  Box,
  Chip,
  Divider,
  LinearProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  RemoveCircleOutline as RemoveCircleOutlineIcon,
  WarningAmber as WarningAmberIcon,
} from '@mui/icons-material';
import { BarChart } from '@mui/x-charts/BarChart';
import { useTranslation } from 'react-i18next';
import { collectionsApi } from '../../api/collections';
import { CollectionRegistreStat } from '../../types';
import Loader from '../Loader';
import { StatsCard, Kpi, fmt, useStatsData } from './StatsCard';

const pct = (c: number): string => `${Math.round(c * 100)} %`;

// ─── Chip d'état de transcription d'un registre (détail par modèle au survol) ──
function RegistreTranscriptionChip({ reg }: { reg: CollectionRegistreStat }) {
  const { t } = useTranslation('stats');
  const ocrEntries = Object.entries(reg.ocr);
  const coverage = ocrEntries.reduce((max, [, o]) => Math.max(max, o.coverage), 0);

  const chip = ocrEntries.length === 0 || coverage <= 0
    ? { label: t('transcriptionChip.notTranscribed'), color: 'default' as const, icon: <RemoveCircleOutlineIcon fontSize="small" /> }
    : coverage >= 0.999
      ? { label: t('transcriptionChip.transcribed'), color: 'success' as const, icon: <CheckCircleIcon fontSize="small" /> }
      : { label: t('transcriptionChip.partial', { pct: pct(coverage) }), color: 'warning' as const, icon: <WarningAmberIcon fontSize="small" /> };

  const tooltip = ocrEntries.length === 0 ? (
    <Typography variant="caption">{t('transcriptionChip.noOcr')}</Typography>
  ) : (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, py: 0.5 }}>
      {ocrEntries.map(([model, o]) => {
        const files = reg.transcriptions[model] ?? 0;
        return (
          <Box key={model}>
            <Typography variant="caption" sx={{ fontFamily: 'monospace', fontWeight: 600, display: 'block' }}>
              {model}
            </Typography>
            <Typography variant="caption" sx={{ display: 'block' }}>
              {t('transcriptionChip.ocrLine', { done: fmt(o.pages_done), total: fmt(o.pages_total), pct: pct(o.coverage) })}
            </Typography>
            {files > 0 && (
              <Typography variant="caption" sx={{ display: 'block' }}>
                {t('filesTranscribed', { count: files })}
              </Typography>
            )}
          </Box>
        );
      })}
    </Box>
  );

  return (
    <Tooltip title={tooltip} arrow placement="left">
      <Chip size="small" variant="outlined" color={chip.color} icon={chip.icon} label={chip.label} />
    </Tooltip>
  );
}

export default function CollectionStatsDashboard({ collectionId }: { collectionId: string }) {
  const { t } = useTranslation('stats');
  const { data, error } = useStatsData(collectionId, id => collectionsApi.getStats(id));

  // Loader commun : une seule indication de chargement avant d'afficher toutes les cartes.
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!data) return <Loader />;

  // Union des modèles OCR + transcription (généralement identiques), ordre OCR d'abord.
  const ocrMap = new Map((data?.ocr_by_model ?? []).map(m => [m.model, m]));
  const transMap = new Map((data?.transcriptions_by_model ?? []).map(m => [m.model, m.files]));
  const models = [
    ...(data?.ocr_by_model ?? []).map(m => m.model),
    ...(data?.transcriptions_by_model ?? []).map(m => m.model).filter(m => !ocrMap.has(m)),
  ];
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
      {/* ── Bandeau de chiffres clés ───────────────────────────────────────────── */}
      <StatsCard title={t('overview.title')}>
        {data && (
          <Box sx={{ display: 'flex', gap: { xs: 3, md: 5 }, flexWrap: 'wrap', alignItems: 'center' }}>
            <Kpi label={t('overview.registres')} value={fmt(data.totals.registres_count)} />
            <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
            <Kpi label={t('overview.pagesScanned')} value={fmt(data.totals.pages_total)} />
            <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
            <Kpi
              label={t('overview.periodCovered')}
              value={data.totals.year_min != null && data.totals.year_max != null
                ? `${data.totals.year_min} – ${data.totals.year_max}`
                : '—'}
            />
            <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
            <Kpi label={t('overview.ocrModels')} value={fmt(data.totals.ocr_models_count)} />
            <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', md: 'block' } }} />
            <Kpi label={t('overview.filesTranscribed')} value={fmt(data.transcriptions_grand_total)} />
          </Box>
        )}
      </StatsCard>

      {/* ── Rangée : avancement par modèle (40%) · pages par année (60%) ───────── */}
      <Box sx={{ display: 'flex', gap: 2.5, flexWrap: { xs: 'wrap', lg: 'nowrap' }, alignItems: 'stretch' }}>
        <StatsCard
          title={t('byModel.title')}
          subtitle={t('byModel.subtitle')}
          sx={{ flex: 2, minWidth: 280 }}
        >
          {data && (
            models.length > 0 ? (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {models.map(m => {
                  const o = ocrMap.get(m);
                  const files = transMap.get(m) ?? 0;
                  return (
                    <Box key={m}>
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 0.5 }}>
                        <Typography variant="body2" sx={{ fontFamily: 'monospace', fontWeight: 600 }}>{m}</Typography>
                        {o && (
                          <Typography variant="body2" fontWeight={700} color={o.coverage >= 1 ? 'success.main' : 'text.primary'}>
                            {pct(o.coverage)}
                          </Typography>
                        )}
                      </Box>
                      <LinearProgress
                        variant="determinate"
                        value={o ? Math.min(100, o.coverage * 100) : 0}
                        color={o && o.coverage >= 1 ? 'success' : 'primary'}
                        sx={{ height: 8, borderRadius: 4 }}
                      />
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.5 }}>
                        <Typography variant="caption" color="text.secondary">
                          {o ? t('byModel.ocrLine', { done: fmt(o.pages_done), total: fmt(o.pages_total) }) : t('byModel.ocrNone')}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('filesTranscribed', { count: files })}
                        </Typography>
                      </Box>
                    </Box>
                  );
                })}
              </Box>
            ) : (
              <Typography variant="body2" color="text.disabled">{t('byModel.noData')}</Typography>
            )
          )}
        </StatsCard>

        <StatsCard
          title={t('byYear.title')}
          subtitle={t('byYear.subtitle')}
          sx={{ flex: 3, minWidth: 320 }}
        >
          {data && (
            data.pages_by_year.length > 0 ? (
              <>
                <BarChart
                  height={300}
                  margin={{ bottom: 60 }}
                  xAxis={[{
                    scaleType: 'band',
                    data: data.pages_by_year.map(d => `${d.year}`),
                    // Repères fixes sur des années rondes (au moins tous les 5 ans), montant
                    // d'un cran (5→10→25…) si la plage est large. On force les graduations
                    // (tickInterval) ET leurs étiquettes (tickLabelInterval) sur ces années :
                    // sans tickInterval, l'axe réduit d'abord les graduations automatiquement
                    // et les étiquettes peuvent toutes disparaître.
                    ...(() => {
                      const step = [5, 10, 25, 50, 100].find(s => data.pages_by_year.length / s <= 20) ?? 200;
                      const onRoundYear = (value: string) => Number(value) % step === 0;
                      return { tickInterval: onRoundYear, tickLabelInterval: onRoundYear };
                    })(),
                    // Hauteur d'axe suffisante pour les étiquettes (sinon elles sont
                    // tronquées jusqu'à devenir invisibles avec la valeur par défaut de 25 px).
                    height: 36,
                    tickLabelStyle: { angle: 0, textAnchor: 'middle', fontSize: 13 },
                  }]}
                  series={[{ data: data.pages_by_year.map(d => d.pages), label: t('byYear.seriesPages'), color: '#42a5f5' }]}
                  hideLegend
                />
                {data.registres_without_periode.length > 0 && (
                  <Typography variant="caption" color="text.secondary">
                    {t('byYear.excluded', { count: data.registres_without_periode.length })}
                  </Typography>
                )}
              </>
            ) : (
              <Typography variant="body2" color="text.disabled">{t('byYear.noPeriod')}</Typography>
            )
          )}
        </StatsCard>
      </Box>

      {/* ── Détail par registre ───────────────────────────────────────────────── */}
      <StatsCard
        title={t('detail.title')}
        subtitle={t('detail.subtitle')}
      >
        {data && (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: 'grey.50' }}>
                  <TableCell sx={{ fontWeight: 600 }}>{t('detail.registre')}</TableCell>
                  <TableCell sx={{ fontWeight: 600 }}>{t('detail.period')}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600 }}>{t('detail.pages')}</TableCell>
                  <TableCell align="center" sx={{ fontWeight: 600 }}>{t('detail.transcription')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.registres.map(reg => (
                  <TableRow key={reg.folder_name} hover>
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{reg.folder_name}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.8rem' }}>
                      {reg.periode && (reg.periode[0] || reg.periode[1])
                        ? `${reg.periode[0] || '…'} – ${reg.periode[1] || '…'}`
                        : '—'}
                    </TableCell>
                    <TableCell align="right">{fmt(reg.pages_count)}</TableCell>
                    <TableCell align="center">
                      <RegistreTranscriptionChip reg={reg} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </StatsCard>
    </Box>
  );
}
