import {
  Alert,
  Box,
  Chip,
  LinearProgress,
  Tooltip,
  Typography,
} from '@mui/material';
import { InfoOutlined as InfoOutlinedIcon } from '@mui/icons-material';
import { PieChart } from '@mui/x-charts/PieChart';
import { useTranslation, Trans } from 'react-i18next';
import { QualityStatsResponse } from '../../types';
import { StatsCard, fmt } from './StatsCard';

const PAGE_TEXT_COLOR = '#66bb6a';
const PAGE_EMPTY_COLOR = '#ef5350';

function RatioRow({ label, detail, ratio, help }: { label: string; detail: string; ratio: number; help?: string }) {
  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
          <Typography variant="body2">{label}</Typography>
          {help && (
            <Tooltip arrow title={help}>
              <InfoOutlinedIcon sx={{ fontSize: 15, color: 'text.disabled', cursor: 'help' }} />
            </Tooltip>
          )}
        </Box>
        <Typography variant="caption" fontWeight={600} color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
          {(ratio * 100).toFixed(1)} % · {detail}
        </Typography>
      </Box>
      <LinearProgress
        variant="determinate"
        value={Math.min(100, ratio * 100)}
        color={ratio > 0.5 ? 'warning' : 'primary'}
        sx={{ height: 6, borderRadius: 3, mt: 0.5 }}
      />
    </Box>
  );
}

// Élément de légende : pastille colorée + libellé + valeur.
function LegendRow({ color, label, value }: { color: string; label: string; value: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <Box sx={{ width: 12, height: 12, borderRadius: '3px', bgcolor: color, flexShrink: 0 }} />
      <Typography variant="body2" sx={{ flex: 1 }}>{label}</Typography>
      <Typography variant="body2" fontWeight={700}>{value}</Typography>
    </Box>
  );
}

export default function QualityCard({ data, error }: { data: QualityStatsResponse | null; error?: string | null }) {
  const { t } = useTranslation('stats');
  return (
    <StatsCard
      title={t('quality.title')}
      subtitle={t('quality.subtitle')}
      error={error}
    >
      {data && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <RatioRow
            label={t('quality.shortWords')}
            detail={t('quality.shortWordsDetail', { words: fmt(data.short_words.unique), occ: fmt(data.short_words.occurrences) })}
            ratio={data.short_words.ratio_unique}
            help={t('quality.shortWordsHelp')}
          />
          <RatioRow
            label={t('quality.hapax')}
            detail={t('quality.hapaxDetail', { words: fmt(data.hapax.unique) })}
            ratio={data.hapax.ratio_unique}
            help={t('quality.hapaxHelp')}
          />
          <Box sx={{ mt: -1 }}>
            <Typography variant="caption" color="text.disabled" display="block">
              <Trans t={t} i18nKey="quality.hapaxNote" components={{ strong: <strong /> }}
                values={{ words: fmt(data.hapax.unique), pct: (data.hapax.ratio_unique * 100).toFixed(1) }} />
            </Typography>
            <Typography variant="caption" color="text.disabled" display="block">
              {t('quality.hapaxNote2')}
            </Typography>
          </Box>

          {data.pages.coverage_known && data.pages.ocr_total != null ? (
            <Box>
              <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
                <PieChart
                  height={170}
                  width={170}
                  hideLegend
                  series={[{
                    innerRadius: 45,
                    data: [
                      { id: 0, value: data.pages.indexed, label: t('quality.pagesWithText'), color: PAGE_TEXT_COLOR },
                      { id: 1, value: data.pages.empty ?? 0, label: t('quality.pagesWithoutText'), color: PAGE_EMPTY_COLOR },
                    ],
                  }]}
                />
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 240, flex: 1 }}>
                  <LegendRow color={PAGE_TEXT_COLOR} label={t('quality.pagesWithText')} value={fmt(data.pages.indexed)} />
                  <LegendRow color={PAGE_EMPTY_COLOR} label={t('quality.pagesWithoutText')} value={fmt(data.pages.empty)} />
                  <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                    {t('quality.onOcrPages', { total: fmt(data.pages.ocr_total) })}
                  </Typography>
                </Box>
              </Box>
              <Typography variant="caption" color="text.disabled" display="block" sx={{ mt: 1 }}>
                <Trans t={t} i18nKey="quality.pagesNote" components={{ strong: <strong /> }} />
              </Typography>
            </Box>
          ) : (
            <Alert severity="info">
              {t('quality.coverageUnknown')}
            </Alert>
          )}

          {data.registres_without_periode.length > 0 && (
            <Box>
              <Typography variant="caption" fontWeight={600} color="text.secondary" display="block" sx={{ mb: 0.5 }}>
                {t('quality.registresNoPeriod')}
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                {data.registres_without_periode.map(f => (
                  <Chip key={f} label={f} size="small" variant="outlined" sx={{ fontFamily: 'monospace' }} />
                ))}
              </Box>
            </Box>
          )}
        </Box>
      )}
    </StatsCard>
  );
}
