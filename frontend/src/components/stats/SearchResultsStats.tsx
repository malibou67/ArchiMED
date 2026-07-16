import { useMemo, useState } from 'react';
import {
  Box,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { BarChart } from '@mui/x-charts/BarChart';
import { LineChart } from '@mui/x-charts/LineChart';
import { useTranslation } from 'react-i18next';
import { indexesApi } from '../../api/indexes';
import { MultiSearchResponse } from '../../types';
import { groupByRegistre } from '../PageImageViewer';
import Loader from '../Loader';
import { StatsCard, Kpi, fmt, useStatsData } from './StatsCard';

const TOP_ROWS = 15;

// Intervalle d'années [début, fin] d'un registre (mêmes règles que le backend
// stats_service._year_span) : une seule borne → année unique, aucune → null (exclu).
function yearSpan(periode?: string[] | null): [number, number] | null {
  if (!periode) return null;
  const start = periode[0] ? parseInt(periode[0], 10) : NaN;
  const end = periode.length > 1 && periode[1] ? parseInt(periode[1], 10) : NaN;
  let s = Number.isFinite(start) ? start : null;
  let e = Number.isFinite(end) ? end : null;
  if (s == null && e == null) return null;
  if (s == null) s = e;
  if (e == null) e = s;
  return s! <= e! ? [s!, e!] : [e!, s!];
}

// Répartit un nombre de pages uniformément sur chaque année de l'intervalle.
function spread(target: Map<number, number>, [start, end]: [number, number], pages: number): void {
  const n = end - start + 1;
  const base = Math.floor(pages / n);
  const rem = pages % n;
  for (let i = 0; i < n; i++) {
    const y = start + i;
    target.set(y, (target.get(y) ?? 0) + base + (i < rem ? 1 : 0));
  }
}

// ─── Statistiques de la recherche en cours (onglet « Statistiques ») ──────────

export default function SearchResultsStats({
  indexId,
  result,
}: {
  indexId: string;
  result: MultiSearchResponse;
}) {
  const { t } = useTranslation('search');
  // Le corpus (caché côté serveur) fournit les périodes des registres et les
  // dénominateurs par décennie ; le reste se calcule depuis le résultat.
  const { data: corpus, loading: corpusLoading } = useStatsData(indexId, id => indexesApi.getCorpusStats(id));
  const [timeMode, setTimeMode] = useState<'raw' | 'per_1000'>('raw');

  const computed = useMemo(() => {
    const groups = groupByRegistre(result.pages);

    let totalOccs = 0;
    const wordCounts = new Map<string, number>();
    for (const page of result.pages) {
      for (const [word, occs] of Object.entries(page.words)) {
        totalOccs += occs.length;
        wordCounts.set(word, (wordCounts.get(word) ?? 0) + occs.length);
      }
    }
    const words = Array.from(wordCounts.entries())
      .map(([word, occurrences]) => ({ word, occurrences }))
      .sort((a, b) => b.occurrences - a.occurrences);

    const registres = groups
      .map(g => ({
        registre: g.registre,                              // nom dé-namespacé (sans préfixe 'sX::')
        collectionTitre: g.source?.collection_titre ?? null, // pour lever l'ambiguïté multi-sources
        pages: g.pages.length,
        occurrences: g.pages.reduce((s, p) => s + Object.values(p.words).reduce((ss, o) => ss + o.length, 0), 0),
      }))
      .sort((a, b) => b.pages - a.pages);

    return { groups, totalOccs, words, registres };
  }, [result]);

  const temporal = useMemo(() => {
    if (!corpus) return null;
    // Correspondance résultat → corpus. Côté corpus, `folder` est namespacé ('sX::registre')
    // en multi-sources, alors que `reg.registre` (issu de groupByRegistre) est dé-namespacé :
    // on matche donc sur (collection + registre dé-namespacé), qui est non ambigu et fournit
    // la même période quel que soit le modèle OCR. Repli sur le nom seul pour les index legacy
    // (mono-source sans collection renseignée).
    const deNs = (folder: string) => { const i = folder.indexOf('::'); return i >= 0 ? folder.slice(i + 2) : folder; };
    // Séparateur '::' : absent des noms dé-namespacés (deNs), donc pas de fausse correspondance
    // entre bornes de collection et de registre.
    const compositeKey = (collection: string | null | undefined, registre: string) => `${collection ?? ''}::${registre}`;
    const infoByComposite = new Map(corpus.registres.map(r => [compositeKey(r.collection, deNs(r.folder)), r]));
    const infoByRegistre = new Map(corpus.registres.map(r => [deNs(r.folder), r]));

    // Dénominateur : pages indexées par année (toutes les pages du corpus réparties
    // sur la période de chaque registre).
    const denominators = new Map<number, number>();
    for (const r of corpus.registres) {
      const span = yearSpan(r.periode);
      if (span) spread(denominators, span, r.pages);
    }

    // Numérateur : pages trouvées réparties sur la période de leur registre.
    const pagesFound = new Map<number, number>();
    let excluded = 0;
    for (const reg of computed.registres) {
      const info = infoByComposite.get(compositeKey(reg.collectionTitre, reg.registre))
        ?? infoByRegistre.get(reg.registre);
      const span = yearSpan(info?.periode);
      if (!span) { excluded += 1; continue; }
      spread(pagesFound, span, reg.pages);
    }

    // Axe continu : toutes les années entre la première et la dernière du corpus.
    const present = [...denominators.keys(), ...pagesFound.keys()];
    const years: number[] = [];
    if (present.length > 0) {
      const lo = Math.min(...present);
      const hi = Math.max(...present);
      for (let y = lo; y <= hi; y++) years.push(y);
    }
    return { years, pagesFound, denominators, excluded };
  }, [corpus, computed]);

  const corpusShare = corpus?.totals.total_pages
    ? (result.count / corpus.totals.total_pages) * 100
    : null;

  return (
    <StatsCard
      title={t('stats.title', { query: result.query })}
      subtitle={t('stats.subtitle')}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {/* Chiffres clés */}
        <Box sx={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <Kpi label={t('stats.kpiPagesFound')} value={fmt(result.count)} />
          <Kpi label={t('stats.kpiRegistres')} value={fmt(computed.groups.length)} />
          <Kpi label={t('stats.kpiOccurrences')} value={fmt(computed.totalOccs)} />
          <Kpi label={t('stats.kpiDistinctWords')} value={fmt(computed.words.length)} />
          <Kpi
            label={t('stats.kpiCorpusShare')}
            value={corpusShare != null ? `${corpusShare < 0.1 ? corpusShare.toFixed(2) : corpusShare.toFixed(1)} %` : '—'}
          />
        </Box>

        <Box sx={{ display: 'flex', gap: 3, flexWrap: { xs: 'wrap', lg: 'nowrap' } }}>
          {/* Par registre */}
          <Box sx={{ flex: 1, minWidth: 300 }}>
            <Typography variant="caption" fontWeight={600} color="text.secondary">
              {t('stats.pagesByRegistre')}{computed.registres.length > TOP_ROWS ? t('stats.topSuffix', { top: TOP_ROWS, total: computed.registres.length }) : ''}
            </Typography>
            <BarChart
              layout="horizontal"
              height={Math.max(200, Math.min(computed.registres.length, TOP_ROWS) * 26 + 60)}
              margin={{ left: 0 }}
              yAxis={[{ scaleType: 'band', data: computed.registres.slice(0, TOP_ROWS).map(r => r.registre), width: 130 }]}
              series={[{ data: computed.registres.slice(0, TOP_ROWS).map(r => r.pages), label: t('stats.seriesPagesFound') }]}
              hideLegend
            />
          </Box>

          {/* Par mot trouvé */}
          <Box sx={{ flex: 1, minWidth: 300 }}>
            <Typography variant="caption" fontWeight={600} color="text.secondary">
              {t('stats.occByWord')}{computed.words.length > TOP_ROWS ? t('stats.topSuffix', { top: TOP_ROWS, total: computed.words.length }) : ''}
            </Typography>
            <BarChart
              layout="horizontal"
              height={Math.max(200, Math.min(computed.words.length, TOP_ROWS) * 26 + 60)}
              margin={{ left: 0 }}
              yAxis={[{ scaleType: 'band', data: computed.words.slice(0, TOP_ROWS).map(w => w.word), width: 130 }]}
              series={[{ data: computed.words.slice(0, TOP_ROWS).map(w => w.occurrences), label: t('stats.seriesOccurrences'), color: '#7e57c2' }]}
              hideLegend
            />
          </Box>
        </Box>

        {/* Répartition temporelle */}
        <Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
            <Typography variant="caption" fontWeight={600} color="text.secondary">
              {t('stats.temporal')}
            </Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={timeMode}
              onChange={(_, v) => v && setTimeMode(v)}
              sx={{ '& .MuiToggleButton-root': { py: 0.25, px: 1, fontSize: '0.72rem', textTransform: 'none' } }}
            >
              <ToggleButton value="raw">{t('stats.raw')}</ToggleButton>
              <ToggleButton value="per_1000">{t('stats.per1000')}</ToggleButton>
            </ToggleButtonGroup>
          </Box>
          {corpusLoading ? (
            <Loader />
          ) : temporal && temporal.years.length > 0 ? (
            <>
              <LineChart
                height={280}
                margin={{ bottom: 60 }}
                xAxis={[{
                  scaleType: 'point',
                  data: temporal.years,
                  // Hauteur d'axe suffisante pour les étiquettes (sinon elles sont
                  // tronquées jusqu'à devenir invisibles avec la valeur par défaut de 25 px).
                  height: 36,
                  valueFormatter: (y: number) => `${y}`,
                  // Repères fixes sur des années rondes (au moins tous les 5 ans), montant
                  // d'un cran (5→10→25…) si la plage est large, pour rester lisible.
                  // On force les graduations (tickInterval) ET leurs étiquettes
                  // (tickLabelInterval) sur ces années : sans tickInterval, l'axe « point »
                  // réduit d'abord les graduations automatiquement et les étiquettes
                  // peuvent toutes disparaître.
                  ...(() => {
                    const step = [5, 10, 25, 50, 100].find(s => temporal.years.length / s <= 20) ?? 200;
                    const onRoundYear = (value: number) => value % step === 0;
                    return { tickInterval: onRoundYear, tickLabelInterval: onRoundYear };
                  })(),
                  tickLabelStyle: { angle: 0, textAnchor: 'middle', fontSize: 13 },
                }]}
                series={[{
                  label: timeMode === 'raw' ? t('stats.seriesPagesFound') : t('stats.seriesPagesFoundPer1000'),
                  curve: 'monotoneX',
                  color: '#26a69a',
                  showMark: false,
                  data: temporal.years.map(y => {
                    const found = temporal.pagesFound.get(y) ?? 0;
                    if (timeMode === 'raw') return found;
                    const denom = temporal.denominators.get(y);
                    return denom ? Math.round((found / denom) * 1000 * 100) / 100 : null;
                  }),
                }]}
                hideLegend
              />
              <Typography variant="caption" color="text.disabled" display="block">
                {t('stats.spreadNote')}
                {timeMode === 'per_1000' && t('stats.normalizedNote')}
                {temporal.excluded > 0 && t('stats.excludedNote', { count: temporal.excluded })}
              </Typography>
            </>
          ) : (
            <Typography variant="body2" color="text.disabled" sx={{ py: 2 }}>
              {t('stats.temporalUnavailable')}
            </Typography>
          )}
        </Box>
      </Box>
    </StatsCard>
  );
}
