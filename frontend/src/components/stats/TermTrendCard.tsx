import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  InputAdornment,
  Slider,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { Timeline as TimelineIcon } from '@mui/icons-material';
import { LineChart } from '@mui/x-charts/LineChart';
import { useTranslation } from 'react-i18next';
import { indexesApi } from '../../api/indexes';
import { TermFrequencyResponse } from '../../types';
import Loader from '../Loader';
import { StatsCard } from './StatsCard';

const MAX_TERMS = 5;
const DEFAULT_TERMS = 'cancer tuberculose';

export default function TermTrendCard({ indexId }: { indexId: string }) {
  const { t } = useTranslation('stats');
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TermFrequencyResponse | null>(null);
  const [mode, setMode] = useState<'occurrences' | 'per_1000_pages'>('occurrences');
  const [fuzzyThreshold, setFuzzyThreshold] = useState(100);
  const autoTracedFor = useRef<string | null>(null);

  const terms = input.replace(/,/g, ' ').split(/\s+/).filter(Boolean).slice(0, MAX_TERMS);

  const trace = async (e?: React.FormEvent, termsOverride?: string[]) => {
    e?.preventDefault();
    const tracedTerms = termsOverride ?? terms;
    if (!indexId || tracedTerms.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const data = await indexesApi.getTermFrequency(indexId, tracedTerms, fuzzyThreshold);
      setResult(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? t('trend.error'));
    } finally {
      setLoading(false);
    }
  };

  // Trace automatiquement les termes par défaut au premier affichage de l'index
  useEffect(() => {
    if (!indexId || autoTracedFor.current === indexId) return;
    autoTracedFor.current = indexId;
    trace(undefined, DEFAULT_TERMS.split(/\s+/));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indexId]);

  const hasData = result && result.decades.length > 0;

  return (
    <StatsCard
      title={t('trend.title')}
      subtitle={t('trend.subtitle')}
      error={error}
    >
      <Box component="form" onSubmit={trace} sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
        <TextField
          size="small"
          placeholder={t('trend.placeholder')}
          value={input}
          onChange={e => setInput(e.target.value)}
          sx={{ flex: 1, minWidth: 240 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <TimelineIcon sx={{ color: 'text.disabled', fontSize: 20 }} />
              </InputAdornment>
            ),
          }}
          helperText={t('trend.helper', { max: MAX_TERMS })}
        />
        <Button
          type="submit"
          variant="contained"
          disableElevation
          disabled={terms.length === 0 || loading}
          startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <TimelineIcon />}
          sx={{ mb: 2.5 }}
        >
          {t('trend.trace')}
        </Button>
        {/* Ressemblance (recherche approximative) — comme dans la page Recherche */}
        <Box sx={{ width: { xs: '100%', sm: 220 }, mb: 2.5 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.72rem' }}>{t('trend.similarity')}</Typography>
            <Typography variant="caption" fontWeight={600} sx={{ fontSize: '0.72rem', color: fuzzyThreshold < 100 ? 'warning.main' : 'text.secondary' }}>
              {fuzzyThreshold === 100 ? t('trend.exact') : `${fuzzyThreshold}%`}
            </Typography>
          </Box>
          <Slider
            value={fuzzyThreshold}
            onChange={(_, v) => setFuzzyThreshold(v as number)}
            min={50}
            max={100}
            step={5}
            size="small"
            sx={{ py: 0.5, color: fuzzyThreshold < 100 ? 'warning.main' : 'primary.main' }}
          />
        </Box>
        {result && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={mode}
            onChange={(_, v) => v && setMode(v)}
            sx={{ mb: 2.5 }}
          >
            <ToggleButton value="per_1000_pages">{t('trend.per1000')}</ToggleButton>
            <ToggleButton value="occurrences">{t('trend.rawOccurrences')}</ToggleButton>
          </ToggleButtonGroup>
        )}
      </Box>

      {!result && !loading && (
        <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
          {t('trend.prompt')}
        </Typography>
      )}

      {loading && <Loader />}

      {result && !loading && !hasData && (
        <Alert severity="info">
          {t('trend.noData')}
        </Alert>
      )}

      {hasData && !loading && (
        <Box>
          <Box sx={{ display: 'flex', gap: 0.5, mb: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            {result.terms.map(term => <Chip key={term} label={term} size="small" />)}
            {result.fuzzy_threshold != null && result.fuzzy_threshold < 100 && (
              <Chip label={t('trend.similarityChip', { value: result.fuzzy_threshold })} size="small" color="warning" variant="outlined" />
            )}
          </Box>
          <LineChart
            height={320}
            xAxis={[{
              scaleType: 'point',
              data: result.decades,
              valueFormatter: (d: number) => `${d}`,
              tickLabelInterval: (value: string | number) => Number(value) % 5 === 0,
            }]}
            series={result.series.map(s => ({
              label: s.term,
              curve: 'monotoneX' as const,
              data: s.points.map(p => mode === 'occurrences' ? p.occurrences : p.per_1000_pages),
            }))}
          />
          <Typography variant="caption" color="text.disabled" display="block">
            {t('trend.note')}
            {mode === 'per_1000_pages' && t('trend.normalizedNote')}
            {result.excluded_registres > 0 && t('trend.excluded', { count: result.excluded_registres })}
          </Typography>
        </Box>
      )}
    </StatsCard>
  );
}
