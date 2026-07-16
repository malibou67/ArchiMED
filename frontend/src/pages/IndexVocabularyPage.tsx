import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputAdornment,
  MenuItem,
  Pagination,
  Paper,
  Select,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  ChevronRight as ChevronRightIcon,
  Download as DownloadIcon,
  InfoOutlined as InfoOutlinedIcon,
  MenuBook as MenuBookIcon,
  QueryStats as QueryStatsIcon,
  Search as SearchIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { indexesApi } from '../api/indexes';
import { IndexMetadata, VocabularyEntry } from '../types';
import IndexStatsDashboard from '../components/stats/IndexStatsDashboard';
import { usePageHeader } from '../context/HeaderContext';

const VOCAB_PAGE_SIZE = 50;

export default function IndexVocabularyPage() {
  const { t } = useTranslation('search');
  const { indexId = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Onglet courant (?tab=stats) : persistant au retour de navigation.
  const tab = searchParams.get('tab') === 'stats' ? 'stats' : 'vocab';
  const setTab = (value: string) => {
    const p = new URLSearchParams(searchParams);
    if (value === 'stats') p.set('tab', 'stats'); else p.delete('tab');
    setSearchParams(p, { replace: true });
  };

  const [index, setIndex] = useState<IndexMetadata | null>(null);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [sort, setSort] = useState<'word' | 'occurrences' | 'pages'>('occurrences');
  const [direction, setDirection] = useState<'asc' | 'desc'>('desc');
  const [hideStopwords, setHideStopwords] = useState(true);
  const [minOccurrences, setMinOccurrences] = useState(1);
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<VocabularyEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    indexesApi.get(indexId).then(setIndex).catch(() => setIndex(null));
  }, [indexId]);

  // Debounce du champ de filtre
  useEffect(() => {
    const id = setTimeout(() => { setDebouncedQuery(query); setOffset(0); }, 300);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    indexesApi.getWords(indexId, {
      q: debouncedQuery || undefined, sort, direction,
      hide_stopwords: hideStopwords, min_occurrences: minOccurrences,
      offset, limit: VOCAB_PAGE_SIZE,
    })
      .then(data => { if (!cancelled) { setItems(data.items); setTotal(data.total); } })
      .catch(() => { if (!cancelled) setError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [indexId, debouncedQuery, sort, direction, hideStopwords, minOccurrences, offset]);

  const handleSort = useCallback((col: 'word' | 'occurrences' | 'pages') => {
    if (sort === col) {
      setDirection(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSort(col);
      setDirection(col === 'word' ? 'asc' : 'desc');
    }
    setOffset(0);
  }, [sort]);

  const pageCount = Math.ceil(total / VOCAB_PAGE_SIZE);
  const currentPage = Math.floor(offset / VOCAB_PAGE_SIZE) + 1;
  const stats = index?.stats;

  // En-tête affiché dans la barre bleue (titre + bouton retour).
  usePageHeader(
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
      <Tooltip title={t('backToIndexes')} arrow>
        <IconButton color="inherit" edge="start" onClick={() => navigate('/indexes')} aria-label={t('backToIndexes')}>
          <ArrowBackIcon />
        </IconButton>
      </Tooltip>
      <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.4)', my: 1 }} />
      <MenuBookIcon sx={{ opacity: 0.9, flexShrink: 0 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="h6" noWrap sx={{ fontFamily: 'monospace', fontSize: '1.05rem', lineHeight: 1.2, fontWeight: 600 }}>
          {indexId}
        </Typography>
        {stats && (
          <Typography variant="caption" noWrap sx={{ display: 'block', opacity: 0.85, lineHeight: 1.2 }}>
            {t('uniqueWordsCount', { count: stats.total_unique_words })} · {t('occurrencesCount', { count: stats.total_word_occurrences })} · {t('registresCount', { count: stats.registres_count })}
          </Typography>
        )}
      </Box>
    </Box>,
    [indexId, stats],
  );

  const exportUrl = indexesApi.getWordsExportUrl(indexId, {
    q: debouncedQuery || undefined, sort, direction,
    hide_stopwords: hideStopwords, min_occurrences: minOccurrences,
  });

  const sortableHead = (col: 'word' | 'occurrences' | 'pages', label: string, align: 'left' | 'right') => (
    <TableCell align={align} sortDirection={sort === col ? direction : false} sx={{ fontWeight: 600 }}>
      <TableSortLabel
        active={sort === col}
        direction={sort === col ? direction : (col === 'word' ? 'asc' : 'desc')}
        onClick={() => handleSort(col)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  );

  return (
    <Box>
      {/* ── Onglets : vocabulaire · statistiques ─────────────── */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2.5, borderBottom: 1, borderColor: 'divider', minHeight: 42 }}>
        <Tab value="vocab" label={t('vocab.tab')} icon={<MenuBookIcon fontSize="small" />} iconPosition="start" sx={{ minHeight: 42 }} />
        <Tab value="stats" label={t('tabs.stats')} icon={<QueryStatsIcon fontSize="small" />} iconPosition="start" sx={{ minHeight: 42 }} />
      </Tabs>

      {tab === 'stats' ? (
        <IndexStatsDashboard indexId={indexId} />
      ) : (
      <>
      {/* ── Filtre + export ─────────────────────────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1.5, flexWrap: 'wrap' }}>
        <TextField
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t('vocab.filterPlaceholder')}
          size="small"
          sx={{ flex: 1, minWidth: 220 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ color: 'text.disabled', fontSize: 20 }} />
              </InputAdornment>
            ),
          }}
        />
        <Tooltip title={t('vocab.exportTooltip')}>
          <Button component="a" href={exportUrl} download size="small" variant="outlined" startIcon={<DownloadIcon />} sx={{ flexShrink: 0 }}>
            CSV
          </Button>
        </Tooltip>
      </Box>

      {/* ── Options de filtrage du bruit ────────────────────── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <FormControlLabel
            control={<Checkbox size="small" checked={hideStopwords} onChange={e => { setHideStopwords(e.target.checked); setOffset(0); }} />}
            label={<Typography variant="body2">{t('vocab.hideStopwords')}</Typography>}
            sx={{ mr: 0.5 }}
          />
          <Tooltip
            arrow
            title={
              <Box sx={{ py: 0.5 }}>
                <Typography variant="caption" sx={{ display: 'block', fontWeight: 600, mb: 0.5 }}>
                  {t('vocab.hiddenExamplesTitle')}
                </Typography>
                <Typography variant="caption" sx={{ display: 'block', mb: 0.5 }}>
                  <strong>{t('vocab.functionWordsLabel')}</strong> {t('vocab.functionWordsList')}
                </Typography>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  <strong>{t('vocab.isolatedLettersLabel')}</strong> {t('vocab.isolatedLettersText')}
                </Typography>
              </Box>
            }
          >
            <InfoOutlinedIcon sx={{ fontSize: 18, color: 'text.disabled', cursor: 'help' }} />
          </Tooltip>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="body2" color="text.secondary">{t('vocab.minOccurrences')}</Typography>
          <FormControl size="small">
            <Select
              value={minOccurrences}
              onChange={e => { setMinOccurrences(Number(e.target.value)); setOffset(0); }}
              sx={{ '& .MuiSelect-select': { py: 0.5 } }}
            >
              {[1, 2, 3, 5, 10, 20].map(n => (
                <MenuItem key={n} value={n}>{n === 1 ? t('vocab.all') : n}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
      </Box>

      {error ? (
        <Alert severity="error">{t('vocab.loadError')}</Alert>
      ) : loading ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2, minHeight: 320 }}>
          <CircularProgress />
          <Typography variant="body2" color="text.secondary">{t('vocab.loading')}</Typography>
        </Box>
      ) : (
        <Paper elevation={0} sx={{ border: '1.5px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: 'grey.50' }}>
                  {sortableHead('word', t('table.word'), 'left')}
                  {sortableHead('occurrences', t('table.occurrences'), 'right')}
                  {sortableHead('pages', t('table.pages'), 'right')}
                  <TableCell sx={{ width: 44 }} />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} sx={{ textAlign: 'center', color: 'text.disabled', py: 4 }}>
                      {debouncedQuery ? t('vocab.noMatch') : t('vocab.empty')}
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map(item => (
                    <TableRow
                      key={item.word}
                      hover
                      sx={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/indexes/${indexId}/words/${encodeURIComponent(item.word)}`)}
                    >
                      <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.85rem', fontWeight: 500 }}>{item.word}</TableCell>
                      <TableCell align="right" sx={{ color: 'text.secondary' }}>{item.occurrences.toLocaleString('fr-FR')}</TableCell>
                      <TableCell align="right" sx={{ color: 'text.secondary' }}>{item.pages.toLocaleString('fr-FR')}</TableCell>
                      <TableCell sx={{ width: 44, color: 'text.disabled' }}><ChevronRightIcon fontSize="small" /></TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      {!loading && !error && total > 0 && (
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', pt: 2, flexWrap: 'wrap', gap: 1 }}>
          <Typography variant="caption" color="text.secondary">
            {t('vocab.wordsCount', { count: total })}{debouncedQuery || hideStopwords || minOccurrences > 1 ? ` ${t('vocab.filtered')}` : ''}
          </Typography>
          {pageCount > 1 && (
            <Pagination
              count={pageCount}
              page={currentPage}
              onChange={(_, p) => { setOffset((p - 1) * VOCAB_PAGE_SIZE); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
              color="primary"
              shape="rounded"
              size="small"
              siblingCount={1}
            />
          )}
        </Box>
      )}
      </>
      )}
    </Box>
  );
}
