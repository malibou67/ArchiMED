import { Box, Typography } from '@mui/material';
import { BarChart } from '@mui/x-charts/BarChart';
import { useTranslation } from 'react-i18next';
import { CorpusStatsResponse } from '../../types';
import { StatsCard } from './StatsCard';

const CHART_HEIGHT = 360;

// Style commun des panneaux encadrés (graphiques) : bordure outlined.
const PANEL_SX = { p: 2, borderRadius: 2, border: '1px solid', borderColor: 'divider' } as const;

// Données par année, mais on n'étiquette l'axe X que tous les 5 ans (années multiples de 5).
export const YEAR_TICK_LABELS = (value: string | number) => Number(value) % 5 === 0;

// ─── Graphiques du corpus : grille 2×2 de BarChart (pleine largeur) ───────────

export default function CorpusStatsCard({ data, error }: { data: CorpusStatsResponse | null; error?: string | null }) {
  const { t } = useTranslation('stats');
  return (
    <StatsCard error={error} sx={{ border: 'none', borderRadius: 0, p: 0 }}>
      {data && (
        <Box
          sx={{
            display: 'grid',
            gap: 3,
            gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)' },
          }}
        >
          <Box sx={{ ...PANEL_SX, minWidth: 0 }}>
            <Typography variant="caption" fontWeight={600} color="text.secondary">
              {t('corpus.topWords', { count: data.top_words.length })}
            </Typography>
            <BarChart
              layout="horizontal"
              height={CHART_HEIGHT}
              margin={{ left: 0 }}
              yAxis={[{ scaleType: 'band', data: data.top_words.map(w => w.word), width: 110 }]}
              series={[{ data: data.top_words.map(w => w.occurrences), label: t('corpus.seriesOccurrences') }]}
              hideLegend
            />
          </Box>
          <Box sx={{ ...PANEL_SX, minWidth: 0 }}>
            <Typography variant="caption" fontWeight={600} color="text.secondary">
              {t('corpus.freqDist')}
            </Typography>
            <BarChart
              height={CHART_HEIGHT}
              xAxis={[{ scaleType: 'band', data: data.frequency_distribution.map(b => b.bucket) }]}
              series={[{ data: data.frequency_distribution.map(b => b.words), label: t('corpus.seriesUniqueWords'), color: '#7e57c2' }]}
              hideLegend
            />
          </Box>
          {data.pages_by_decade.length > 0 && (
            <Box sx={{ ...PANEL_SX, minWidth: 0 }}>
              <Typography variant="caption" fontWeight={600} color="text.secondary">
                {t('corpus.pagesByYear')}
              </Typography>
              <BarChart
                height={CHART_HEIGHT}
                xAxis={[{ scaleType: 'band', data: data.pages_by_decade.map(d => `${d.decade}`), tickLabelInterval: YEAR_TICK_LABELS }]}
                series={[{ data: data.pages_by_decade.map(d => d.pages), label: t('corpus.seriesPages'), color: '#26a69a' }]}
                hideLegend
              />
            </Box>
          )}
          {data.pages_by_decade.length > 0 && (
            <Box sx={{ ...PANEL_SX, minWidth: 0 }}>
              <Typography variant="caption" fontWeight={600} color="text.secondary">
                {t('corpus.registresByYear')}
              </Typography>
              <BarChart
                height={CHART_HEIGHT}
                xAxis={[{ scaleType: 'band', data: data.pages_by_decade.map(d => `${d.decade}`), tickLabelInterval: YEAR_TICK_LABELS }]}
                series={[{ data: data.pages_by_decade.map(d => d.registres), label: t('corpus.seriesRegistres'), color: '#ef6c00' }]}
                hideLegend
              />
            </Box>
          )}
        </Box>
      )}
    </StatsCard>
  );
}
