import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Backdrop,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  FormControl,
  FormHelperText,
  IconButton,
  InputAdornment,
  InputLabel,
  Menu,
  MenuItem,
  Pagination,
  Paper,
  Select,
  Slider,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  ArrowDropDown as ArrowDropDownIcon,
  DateRange as DateRangeIcon,
  Download as DownloadIcon,
  FilterList as FilterListIcon,
  ImageSearch as ImageSearchIcon,
  KeyboardArrowDown as ArrowDownIcon,
  KeyboardArrowRight as ArrowRightIcon,
  QueryStats as QueryStatsIcon,
  Search as SearchIcon,
  TableRows as TableRowsIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { indexesApi } from '../api/indexes';
import { collectionsApi } from '../api/collections';
import { registresApi } from '../api/registres';
import { usePageLoading } from '../context/LoadingContext';
import { IndexMetadata, IndexStats, MultiSearchResponse, RegistreSummary } from '../types';
import { useSearchParams } from 'react-router-dom';
import {
  ViewerEntry,
  groupByRegistre,
  displayPageName,
  pageToViewerEntry,
  pageFamilyFromList,
  thumbLabel,
  renderAnnotatedBlob,
  ImageViewer,
  FullPageViewer,
} from '../components/PageImageViewer';
import SearchResultsStats from '../components/stats/SearchResultsStats';
import EmptyState from '../components/EmptyState';

// ─── SearchPage ───────────────────────────────────────────────────────────────

export default function SearchPage() {
  const { t, i18n } = useTranslation('search');
  const fmtN = (n: number) => n.toLocaleString(i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US');
  const indexStatsLabel = (stats: IndexStats): string => {
    const parts = [t('indexStats.words', { count: stats.total_unique_words, val: fmtN(stats.total_unique_words) })];
    if (stats.total_pages != null) parts.push(t('indexStats.pages', { count: stats.total_pages, val: fmtN(stats.total_pages) }));
    if (stats.registres_count != null) parts.push(t('indexStats.registres', { count: stats.registres_count, val: fmtN(stats.registres_count) }));
    return parts.join(' · ');
  };
  const [searchParams, setSearchParams] = useSearchParams();
  const [indexes, setIndexes] = useState<IndexMetadata[]>([]);
  const [loadingIndexes, setLoadingIndexes] = useState(true);
  usePageLoading(t('loadingIndexes'), loadingIndexes);
  const [selectedIndex, setSelectedIndex] = useState('');
  const [query, setQuery] = useState('');
  const [yearBounds, setYearBounds] = useState<[number, number] | null>(null);
  const [yearRange, setYearRange] = useState<[number, number] | null>(null);
  const [loadingYearRange, setLoadingYearRange] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<MultiSearchResponse | null>(null);
  const [viewerList, setViewerList] = useState<ViewerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [loadingPage, setLoadingPage] = useState<string | null>(null);
  const [regPage, setRegPage] = useState(1);
  const [expandedRegistres, setExpandedRegistres] = useState<Set<string>>(new Set());
  const [selectedWords, setSelectedWords] = useState<Set<string>>(new Set());
  const [fuzzyThreshold, setFuzzyThreshold] = useState(100);
  const [exportAnchor, setExportAnchor] = useState<null | HTMLElement>(null);
  const [preparingZip, setPreparingZip] = useState(false);  // overlay pendant la préparation du ZIP
  const REG_PER_PAGE = 10;

  // Pages liées (famille : page principale + extras) pour la visionneuse de recherche.
  // Reprend le rendu de CollectionsPage en s'appuyant sur la liste complète du registre,
  // y compris des pages extra qui ne sont pas des résultats.
  const [collectionKey, setCollectionKey] = useState('');           // folder_name de la collection
  const [registres, setRegistres] = useState<RegistreSummary[]>([]); // registres (motifs + dossiers)
  const regPagesRef = useRef<Map<string, string[]>>(new Map());      // folder -> noms de fichiers
  const [familyMap, setFamilyMap] = useState<Map<string, string[]>>(new Map()); // stem -> famille (stems)
  const [displayedEntry, setDisplayedEntry] = useState<ViewerEntry | null>(null); // override d'aperçu (page extra)
  const getRelated = (p: string) => familyMap.get(p) ?? [p];
  // Boîtes du mot recherché pour une page liée (uniquement pour les pages qui sont
  // des résultats) — sert à surligner le mot sur les miniatures du rail.
  const getBoxes = (p: string) => viewerList.find(e => e.pageName === p)?.boxes ?? [];

  // Famille (stems) d'une page : son registre est résolu par préfixe, puis la liste
  // complète du registre (mise en cache) sert à reconstituer principale + extras.
  const computeFamily = useCallback(async (stem: string): Promise<string[]> => {
    if (!collectionKey || registres.length === 0) return [stem];
    const stripExt = (f: string) => f.replace(/\.[^.]+$/, '');
    const folders = registres
      .map(r => r.folder_name)
      .filter((f): f is string => !!f)
      .sort((a, b) => b.length - a.length);  // préfixe le plus long d'abord
    const folder = folders.find(f => stem.startsWith(f + '_'));
    if (!folder) return [stem];
    const registre = registres.find(r => r.folder_name === folder)!;
    let files = regPagesRef.current.get(folder);
    if (!files) {
      try { files = await registresApi.getPages(collectionKey, folder); }
      catch { files = []; }
      regPagesRef.current.set(folder, files);
    }
    const filename = files.find(f => stripExt(f) === stem);
    if (!filename) return [stem];
    return pageFamilyFromList(
      files, filename, registre.pages_pattern, registre.extra_pagination?.pattern,
    ).map(stripExt);
  }, [collectionKey, registres]);

  // Mémorise une famille : chaque membre (principale ET extras) pointe vers la famille
  // complète, pour que la bande latérale reste affichée même sur une page extra.
  const mergeFamily = useCallback((family: string[]) => {
    if (family.length <= 1) return;
    setFamilyMap(prev => {
      const next = new Map(prev);
      for (const member of family) next.set(member, family);
      return next;
    });
  }, []);

  // Libellé d'une miniature : « num » pour une page principale, « num - extra » pour une
  // extra, via les motifs du registre résolu par préfixe.
  const getThumbLabel = (p: string) => {
    const reg = registres
      .filter(r => r.folder_name)
      .sort((a, b) => b.folder_name.length - a.folder_name.length)
      .find(r => p.startsWith(r.folder_name + '_'));
    return thumbLabel(p, reg?.pages_pattern, reg?.extra_pagination?.pattern);
  };

  // Précharge les images (principale + miniatures) ; résout même en cas d'erreur.
  const preloadImages = (pages: string[]) =>
    Promise.all(pages.map(p => new Promise<void>(resolve => {
      const img = new window.Image();
      img.onload = () => resolve();
      img.onerror = () => resolve();
      img.src = indexesApi.getPageImageUrl(selectedIndex, p);
    })));

  useEffect(() => {
    indexesApi.getAll()
      .then(data => {
        const ready = data.filter(i => i.status === 'ready');
        setIndexes(ready);
        if (ready.length > 0) setSelectedIndex(ready[0].id);
      })
      .catch(() => setError(t('errors.loadIndexes')))
      .finally(() => setLoadingIndexes(false));
  }, []);

  useEffect(() => {
    if (!selectedIndex) return;
    setYearBounds(null);
    setYearRange(null);
    const stats = indexes.find(i => i.id === selectedIndex)?.stats;
    if (stats?.year_min != null && stats?.year_max != null) {
      setYearBounds([stats.year_min, stats.year_max]);
      setYearRange([stats.year_min, stats.year_max]);
      return;
    }
    // Fallback pour les index générés avant l'ajout de year_min/year_max aux stats.
    setLoadingYearRange(true);
    indexesApi.getYearRange(selectedIndex)
      .then(range => {
        if (range) {
          setYearBounds([range.year_min, range.year_max]);
          setYearRange([range.year_min, range.year_max]);
        }
      })
      .finally(() => setLoadingYearRange(false));
  }, [selectedIndex, indexes]);

  // Charge les registres de la collection de l'index (dossiers + motifs de pagination),
  // nécessaires pour reconstituer la famille d'une page (principale + extras).
  useEffect(() => {
    setCollectionKey('');
    setRegistres([]);
    setFamilyMap(new Map());
    regPagesRef.current = new Map();
    const colId = indexes.find(i => i.id === selectedIndex)?.collection_id;
    if (!colId) return;
    let cancelled = false;
    collectionsApi.getById(colId)
      .then(col => {
        if (cancelled) return;
        setCollectionKey(col.folder_name || colId);
        setRegistres(col.registres ?? []);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [selectedIndex, indexes]);

  // Quand la visionneuse est active, construit la famille (stems) de chaque page-résultat
  // à partir de la liste complète de son registre. Chaque membre (principale ET extras)
  // pointe vers la famille complète, pour que la bande latérale reste affichée même en
  // visualisant une page extra.
  const viewerActive = viewerOpen || searchParams.get('view') !== null;
  useEffect(() => {
    if (!viewerActive || viewerList.length === 0 || !collectionKey || registres.length === 0) return;
    let cancelled = false;
    (async () => {
      const map = new Map<string, string[]>();
      for (const { pageName } of viewerList) {
        if (cancelled) return;
        if (map.has(pageName)) continue;
        const family = await computeFamily(pageName);
        if (family.length > 1) for (const member of family) map.set(member, family);
      }
      if (!cancelled && map.size > 0) setFamilyMap(map);
    })();
    return () => { cancelled = true; };
  }, [viewerActive, viewerList, collectionKey, registres, computeFamily]);

  // Onglets sous le bloc recherche (visibles après une recherche) : Résultats / Statistiques.
  const [resultTab, setResultTab] = useState<'results' | 'stats'>('results');

  const handleSearch = async (e?: React.FormEvent, queryOverride?: string) => {
    e?.preventDefault();
    const q = (queryOverride ?? query).trim();
    if (!selectedIndex || !q) return;
    setSearching(true);
    setError(null);
    setSearchResult(null);
    setViewerList([]);
    setRegPage(1);
    setExpandedRegistres(new Set());
    setSelectedWords(new Set());
    try {
      const yFrom = (yearRange && yearBounds && yearRange[0] !== yearBounds[0]) ? yearRange[0] : undefined;
      const yTo = (yearRange && yearBounds && yearRange[1] !== yearBounds[1]) ? yearRange[1] : undefined;
      const data = await indexesApi.search(selectedIndex, q, yFrom, yTo, fuzzyThreshold);
      setSearchResult(data);
      setViewerList(data.pages.map(pageToViewerEntry));
      setResultTab('results');
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? t('errors.search'));
    } finally {
      setSearching(false);
    }
  };

  const toggleWord = (word: string) => {
    setSelectedWords(prev => {
      const next = new Set(prev);
      if (next.has(word)) next.delete(word); else next.add(word);
      return next;
    });
    setRegPage(1);
    setExpandedRegistres(new Set());
  };

  const downloadPage = async (pageName: string) => {
    const url = indexesApi.getPageImageUrl(selectedIndex, pageName);
    const entry = viewerList.find(e => e.pageName === pageName);
    const blob = await renderAnnotatedBlob(url, entry?.boxes ?? []);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = pageName.replace(/\.[^.]+$/, '') + '.png';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  };

  // Attend le cookie posé par le backend au démarrage du flux ZIP (= fin de la préparation
  // serveur), avec un filet de sécurité `timeoutMs` pour ne jamais laisser l'overlay bloqué.
  const waitForZipStart = (token: string, timeoutMs: number): Promise<void> =>
    new Promise(resolve => {
      const start = Date.now();
      const id = window.setInterval(() => {
        const ready = document.cookie.split('; ').some(c => c === `archimed_zip_ready=${token}`);
        if (ready || Date.now() - start > timeoutMs) {
          window.clearInterval(id);
          if (ready) document.cookie = 'archimed_zip_ready=; Max-Age=0; path=/';
          resolve();
        }
      }, 400);
    });

  // Export ZIP piloté en JS (plutôt qu'un simple <a href>) afin d'afficher un loader pendant la
  // préparation serveur. Le téléchargement reste streamé sur disque (ancre native déclenchée
  // ici) : aucune limite de taille, l'archive ne transite pas par la mémoire du navigateur.
  const handleExportZip = () => {
    if (!searchResult) return;
    setExportAnchor(null);
    // randomUUID n'existe qu'en contexte sécurisé (localhost ok) ; repli sinon.
    const token = window.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const url = indexesApi.getResultsExportUrl(selectedIndex, 'zip', {
      q: searchResult.query, year_from: searchResult.year_from, year_to: searchResult.year_to,
      fuzzy_threshold: searchResult.fuzzy_threshold, download_token: token,
    });
    setPreparingZip(true);
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    waitForZipStart(token, 15 * 60_000).finally(() => setPreparingZip(false));
  };


  const toggleRegistre = (registre: string) => {
    setExpandedRegistres(prev => {
      const next = new Set(prev);
      if (next.has(registre)) next.delete(registre);
      else next.add(registre);
      return next;
    });
  };

  const openViewer = async (pageName: string) => {
    if (loadingPage) return;
    const idx = viewerList.findIndex(e => e.pageName === pageName);
    const entry = viewerList[idx >= 0 ? idx : 0];
    setLoadingPage(pageName);
    // Résout la famille puis précharge l'image principale ET les miniatures : la
    // visionneuse ne s'ouvre qu'une fois tout chargé (pas de pop-in des extras).
    const family = await computeFamily(entry.pageName);
    mergeFamily(family);
    await preloadImages(family);
    setLoadingPage(null);
    setDisplayedEntry(null);
    setViewerIndex(idx >= 0 ? idx : 0);
    setViewerOpen(true);
  };

  const handlePrev = useCallback(() => { setDisplayedEntry(null); setViewerIndex(i => Math.max(0, i - 1)); }, []);
  const handleNext = useCallback(() => { setDisplayedEntry(null); setViewerIndex(i => Math.min(viewerList.length - 1, i + 1)); }, [viewerList.length]);

  if (loadingIndexes) return null;  // overlay global (usePageLoading) pendant le chargement initial

  const selectedStats = indexes.find(i => i.id === selectedIndex)?.stats;

  // Vue pleine page d'un résultat (pilotée par ?view=<index>) : le composant reste monté,
  // donc les résultats sont préservés au retour.
  const viewParam = searchParams.get('view');
  if (viewParam !== null && viewerList.length > 0) {
    const startIndex = Math.min(Math.max(0, Number(viewParam) || 0), viewerList.length - 1);
    return (
      <FullPageViewer
        entries={viewerList}
        startIndex={startIndex}
        getImageUrl={(p) => indexesApi.getPageImageUrl(selectedIndex, p)}
        getRelated={getRelated}
        getEntry={(p) => ({ pageName: p, boxes: [], wordSummary: '' })}
        getThumbLabel={getThumbLabel}
        getBoxes={getBoxes}
        backLabel={t('backToSearch')}
        onBack={() => { const p = new URLSearchParams(searchParams); p.delete('view'); setSearchParams(p); }}
      />
    );
  }

  return (
    <Box>

      {indexes.length === 0 ? (
        <EmptyState
          icon={<SearchIcon />}
          title={t('empty.title')}
          description={t('empty.description')}
          action={{ label: t('empty.action'), path: '/indexes' }}
        />
      ) : (
        <>
          {/* ── Panneau de recherche unifié ──────────── */}
          <Paper
            component="form"
            onSubmit={handleSearch}
            variant="outlined"
            sx={{ borderRadius: 2, p: 2.5, mb: 3 }}
          >
            {/* Ligne principale : index · champ · bouton (alignés à la même hauteur) */}
            <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start', flexWrap: { xs: 'wrap', md: 'nowrap' } }}>
              <FormControl sx={{ width: { xs: '100%', md: 260 }, flexShrink: 0 }}>
                <InputLabel id="search-index-label">{t('index')}</InputLabel>
                <Select
                  labelId="search-index-label"
                  label={t('index')}
                  value={selectedIndex}
                  onChange={e => { setSelectedIndex(e.target.value); setSearchResult(null); setViewerList([]); setYearBounds(null); setYearRange(null); }}
                  renderValue={val => {
                    const idx = indexes.find(i => i.id === val);
                    return <Typography variant="body2" fontWeight={600} noWrap>{idx?.name ?? val}</Typography>;
                  }}
                >
                  {indexes.map(idx => (
                    <MenuItem key={idx.id} value={idx.id}>
                      <Box>
                        <Typography variant="body2" fontWeight={500}>{idx.name ?? idx.id}</Typography>
                        {idx.stats && (
                          <Typography variant="caption" color="text.secondary">{indexStatsLabel(idx.stats)}</Typography>
                        )}
                      </Box>
                    </MenuItem>
                  ))}
                </Select>
                {selectedStats && <FormHelperText sx={{ mx: 0 }}>{indexStatsLabel(selectedStats)}</FormHelperText>}
              </FormControl>

              <TextField
                fullWidth
                placeholder={t('queryPlaceholder')}
                value={query}
                onChange={e => setQuery(e.target.value)}
                autoFocus
                sx={{ flex: 1 }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon sx={{ color: 'text.disabled', fontSize: 20 }} />
                    </InputAdornment>
                  ),
                }}
              />

              <Button
                type="submit"
                variant="contained"
                disableElevation
                disabled={!selectedIndex || !query.trim() || searching}
                startIcon={searching ? <CircularProgress size={16} color="inherit" /> : <SearchIcon />}
                sx={{ height: 56, px: 3.5, flexShrink: 0, width: { xs: '100%', md: 'auto' } }}
              >
                {searching ? t('searching') : t('searchButton')}
              </Button>
            </Box>

            <Divider sx={{ my: 2 }} />

            {/* Filtres compacts (largeurs contraintes, alignés à gauche) */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: { xs: 2, md: 4 }, flexWrap: 'wrap' }}>
              <Typography variant="caption" sx={{ fontWeight: 700, color: 'text.secondary', letterSpacing: '0.05em', flexShrink: 0 }}>
                {t('filters')}
              </Typography>

              {/* Période */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: { xs: '100%', sm: 300 } }}>
                <DateRangeIcon sx={{ color: 'text.disabled', fontSize: 18, flexShrink: 0 }} />
                {loadingYearRange ? (
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <CircularProgress size={14} thickness={5} />
                    <Typography variant="caption" color="text.disabled">{t('loadingPeriod')}</Typography>
                  </Box>
                ) : yearBounds && yearRange ? (
                  <Box sx={{ flex: 1 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.72rem' }}>{t('period')}</Typography>
                      <Typography variant="caption" fontWeight={600} color="text.secondary" sx={{ fontSize: '0.72rem' }}>{yearRange[0]} – {yearRange[1]}</Typography>
                    </Box>
                    <Slider
                      value={yearRange}
                      onChange={(_, v) => setYearRange(v as [number, number])}
                      min={yearBounds[0]}
                      max={yearBounds[1]}
                      valueLabelDisplay="auto"
                      size="small"
                      sx={{ py: 0.5 }}
                    />
                  </Box>
                ) : (
                  <Typography variant="caption" color="text.disabled">{t('periodUnavailable')}</Typography>
                )}
              </Box>

              {/* Ressemblance */}
              <Box sx={{ width: { xs: '100%', sm: 260 } }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.72rem' }}>{t('similarity')}</Typography>
                  <Typography variant="caption" fontWeight={600} sx={{ fontSize: '0.72rem', color: fuzzyThreshold < 100 ? 'warning.main' : 'text.secondary' }}>
                    {fuzzyThreshold === 100 ? t('exact') : `${fuzzyThreshold}%`}
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
            </Box>
          </Paper>

          {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

          {/* ── Barre : onglets (après recherche) ── */}
          {searchResult && (
            <Box sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
              <Tabs
                value={resultTab}
                onChange={(_, v) => setResultTab(v)}
                sx={{ minHeight: 42 }}
              >
                <Tab value="results" label={t('tabs.results', { count: searchResult.count })} icon={<TableRowsIcon fontSize="small" />} iconPosition="start" sx={{ minHeight: 42 }} />
                <Tab value="stats" label={t('tabs.stats')} icon={<QueryStatsIcon fontSize="small" />} iconPosition="start" sx={{ minHeight: 42 }} />
              </Tabs>
            </Box>
          )}

          {resultTab === 'stats' && searchResult && (
            searchResult.count > 0 ? (
              <SearchResultsStats indexId={selectedIndex} result={searchResult} />
            ) : (
              <Box sx={{ textAlign: 'center', mt: 8, color: 'text.disabled' }}>
                <QueryStatsIcon sx={{ fontSize: 56, opacity: 0.2, mb: 1 }} />
                <Typography variant="body2" color="text.disabled">
                  {t('noResultsToAnalyze')}
                </Typography>
              </Box>
            )
          )}

          {resultTab === 'results' && (
          <>
          {/* ── Recherche en cours ───────────────────── */}
          {searching && (
            <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mt: 10, gap: 2, color: 'text.secondary' }}>
              <CircularProgress />
              <Typography variant="body2" color="text.secondary">
                {t('searchInProgress')}
              </Typography>
            </Box>
          )}

          {/* ── État vide ────────────────────────────── */}
          {!searchResult && !searching && (
            <Box sx={{ textAlign: 'center', mt: 10, color: 'text.disabled' }}>
              <SearchIcon sx={{ fontSize: 56, opacity: 0.2, mb: 1 }} />
              <Typography variant="body2" color="text.disabled">
                {t('emptyPrompt')}
              </Typography>
            </Box>
          )}

          {/* ── Résultats ────────────────────────────── */}
          {searchResult && (
            <Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap', justifyContent: 'space-between', p: 1.5, bgcolor: 'grey.50', borderRadius: 2, border: '1px solid', borderColor: 'divider' }}>
                <Typography variant="body2" color="text.secondary">
                  {(() => {
                    const nbRegistres = groupByRegistre(searchResult.pages).length;
                    return <><strong style={{ color: '#2e7d32' }}>{t('summary.pages', { count: searchResult.count })}</strong> {t('summary.in')} <strong>{t('summary.registres', { count: nbRegistres })}</strong></>;
                  })()}
                </Typography>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                  <Typography variant="body2" color="text.secondary">{t('searchedWords')}</Typography>
                  {searchResult.terms.map(term => (
                    <Chip key={term} label={term} size="small" color="default" variant="filled" />
                  ))}
                  {(searchResult.year_from != null || searchResult.year_to != null) && (
                    <Chip
                      icon={<DateRangeIcon sx={{ fontSize: '14px !important' }} />}
                      label={`${searchResult.year_from ?? yearBounds?.[0] ?? '…'} – ${searchResult.year_to ?? yearBounds?.[1] ?? '…'}`}
                      size="small"
                      color="secondary"
                      variant="outlined"
                    />
                  )}
                  {searchResult.fuzzy_threshold != null && searchResult.fuzzy_threshold < 100 && (
                    <Chip
                      label={t('similarityChip', { value: searchResult.fuzzy_threshold })}
                      size="small"
                      color="warning"
                      variant="outlined"
                    />
                  )}
                  {searchResult.count > 0 && (
                    <>
                      <Button
                        variant="contained"
                        disableElevation
                        size="small"
                        startIcon={<DownloadIcon />}
                        endIcon={<ArrowDropDownIcon />}
                        onClick={e => setExportAnchor(e.currentTarget)}
                        sx={{ ml: { sm: 1 } }}
                      >
                        {t('exportButton')}
                      </Button>
                      <Menu anchorEl={exportAnchor} open={Boolean(exportAnchor)} onClose={() => setExportAnchor(null)}>
                        <MenuItem
                          component="a"
                          href={indexesApi.getResultsExportUrl(selectedIndex, 'csv', { q: searchResult.query, year_from: searchResult.year_from, year_to: searchResult.year_to, fuzzy_threshold: searchResult.fuzzy_threshold })}
                          download
                          onClick={() => setExportAnchor(null)}
                        >
                          <Box>
                            <Typography variant="body2">{t('exportMenu.csvTitle')}</Typography>
                            <Typography variant="caption" color="text.secondary">{t('exportMenu.csvDesc')}</Typography>
                          </Box>
                        </MenuItem>
                        <MenuItem onClick={handleExportZip}>
                          <Box>
                            <Typography variant="body2">{t('exportMenu.zipTitle')}</Typography>
                            <Typography variant="caption" color="text.secondary">{t('exportMenu.zipDesc')}</Typography>
                          </Box>
                        </MenuItem>
                      </Menu>
                    </>
                  )}
                </Box>
              </Box>

              {searchResult.count === 0 ? (
                <Alert severity="info">{t('noPageAllTerms')}</Alert>
              ) : (() => {
                const uniqueWords = Array.from(
                  new Set(searchResult.pages.flatMap(p => Object.keys(p.words)))
                ).sort((a, b) => a.localeCompare(b, 'fr'));
                const filteredPages = selectedWords.size > 0
                  ? searchResult.pages.filter(p => [...selectedWords].some(w => w in p.words))
                  : searchResult.pages;
                const allGroups = groupByRegistre(filteredPages);
                const pageCount = Math.ceil(allGroups.length / REG_PER_PAGE);
                const visibleGroups = allGroups.slice((regPage - 1) * REG_PER_PAGE, regPage * REG_PER_PAGE);
                return (
                  <Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 2, p: 1.5, bgcolor: 'grey.50', borderRadius: 2, border: '1px solid', borderColor: 'divider' }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
                        <FilterListIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
                        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                          {t('refineByWord')}
                        </Typography>
                      </Box>
                      {uniqueWords.map(word => {
                        const active = selectedWords.has(word);
                        return (
                          <Chip key={word} label={word} size="small"
                            color="primary" variant={active ? 'filled' : 'outlined'}
                            onClick={() => toggleWord(word)}
                            sx={{ height: 24, cursor: 'pointer', '& .MuiChip-label': { px: 1, fontSize: '0.78rem' } }}
                          />
                        );
                      })}
                      {selectedWords.size > 0 && (
                        <>
                          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                            → {t('pagesCount', { count: filteredPages.length })}
                          </Typography>
                          <Chip label={t('reset')} size="small" variant="outlined" color="default"
                            onClick={() => { setSelectedWords(new Set()); setRegPage(1); setExpandedRegistres(new Set()); }}
                            sx={{ height: 24, cursor: 'pointer', '& .MuiChip-label': { px: 1, fontSize: '0.78rem' } }}
                          />
                        </>
                      )}
                    </Box>
                    <Paper variant="outlined">
                      <TableContainer>
                        <Table size="small">
                          <TableHead>
                            <TableRow sx={{ bgcolor: 'grey.50' }}>
                              <TableCell sx={{ width: 36 }} />
                              <TableCell sx={{ fontWeight: 600 }}>{t('table.registre')}</TableCell>
                              <TableCell align="right" sx={{ fontWeight: 600 }}>{t('table.pages')}</TableCell>
                              <TableCell align="right" sx={{ fontWeight: 600 }}>{t('table.occurrences')}</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {visibleGroups.map(group => {
                              const groupOccs = group.pages.reduce((s, p) =>
                                s + Object.values(p.words).reduce((ss, o) => ss + o.length, 0), 0);
                              const isExpanded = expandedRegistres.has(group.key);
                              return (
                                <React.Fragment key={group.key}>
                                  <TableRow
                                    hover
                                    sx={{ cursor: 'pointer' }}
                                    onClick={() => toggleRegistre(group.key)}
                                  >
                                    <TableCell sx={{ width: 36, py: 0.5 }}>
                                      {isExpanded
                                        ? <ArrowDownIcon fontSize="small" />
                                        : <ArrowRightIcon fontSize="small" />}
                                    </TableCell>
                                    <TableCell sx={{ fontSize: '0.85rem', fontWeight: 600 }}>
                                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                                        <span style={{ fontFamily: 'monospace' }}>{group.registre}</span>
                                        {group.source && (
                                          <Chip
                                            size="small"
                                            variant="outlined"
                                            color="info"
                                            label={`${group.source.collection_titre || group.source.collection_folder} · ${group.source.model_name}`}
                                            sx={{ height: 20, '& .MuiChip-label': { px: 0.75, fontSize: '0.7rem' } }}
                                          />
                                        )}
                                      </Box>
                                    </TableCell>
                                    <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.85rem' }}>
                                      {t('pagesCount', { count: group.pages.length })}
                                    </TableCell>
                                    <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.85rem' }}>
                                      {t('occurrencesCount', { count: groupOccs })}
                                    </TableCell>
                                  </TableRow>

                                  <TableRow>
                                    <TableCell colSpan={4} sx={{ p: 0, border: 0 }}>
                                      <Collapse in={isExpanded} unmountOnExit>
                                        <Table size="small" sx={{ bgcolor: 'grey.50' }}>
                                          <TableHead>
                                            <TableRow>
                                              <TableCell sx={{ pl: 4, width: 40, color: 'text.secondary', fontSize: '0.75rem', fontWeight: 600 }}>#</TableCell>
                                              <TableCell sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('table.page')}</TableCell>
                                              <TableCell sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('table.wordsFound')}</TableCell>
                                              <TableCell align="right" sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('table.occurrences')}</TableCell>
                                              <TableCell sx={{ width: 80 }} />
                                            </TableRow>
                                          </TableHead>
                                          <TableBody>
                                            {group.pages.map((page, i) => {
                                              const totalOccs = Object.values(page.words).reduce((s, o) => s + o.length, 0);
                                              return (
                                                <TableRow
                                                  key={page.page_name}
                                                  hover
                                                  sx={{ cursor: loadingPage ? 'wait' : 'pointer' }}
                                                  onClick={() => openViewer(page.page_name)}
                                                >
                                                  <TableCell sx={{ pl: 4, color: 'text.disabled', fontSize: '0.75rem', width: 40 }}>
                                                    {i + 1}
                                                  </TableCell>
                                                  <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.78rem', fontWeight: 500 }}>
                                                    {displayPageName(page.page_name)}
                                                  </TableCell>
                                                  <TableCell>
                                                    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                                                      {Object.entries(page.words)
                                                        .sort(([a], [b]) => a.localeCompare(b, 'fr'))
                                                        .map(([word, occs]) => (
                                                          <Chip key={word} label={`${word} × ${occs.length}`} size="small" variant="outlined"
                                                            sx={{ height: 26, '& .MuiChip-label': { px: 1, fontSize: '0.8rem' } }}
                                                          />
                                                        ))}
                                                    </Box>
                                                  </TableCell>
                                                  <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.85rem', whiteSpace: 'nowrap' }}>
                                                    {t('occAbbr', { count: totalOccs })}
                                                  </TableCell>
                                                  <TableCell align="right" sx={{ pr: 1, whiteSpace: 'nowrap' }}>
                                                    <Tooltip title={t('showPage')}>
                                                      <IconButton
                                                        size="small"
                                                        color="primary"
                                                        onClick={e => { e.stopPropagation(); openViewer(page.page_name); }}
                                                      >
                                                        <ImageSearchIcon fontSize="small" />
                                                      </IconButton>
                                                    </Tooltip>
                                                    <Tooltip title={t('downloadPage')}>
                                                      <IconButton
                                                        size="small"
                                                        onClick={e => { e.stopPropagation(); downloadPage(page.page_name); }}
                                                      >
                                                        <DownloadIcon fontSize="small" />
                                                      </IconButton>
                                                    </Tooltip>
                                                  </TableCell>
                                                </TableRow>
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

                    {pageCount > 1 && (
                      <Box sx={{ display: 'flex', justifyContent: 'center', pt: 2 }}>
                        <Pagination
                          count={pageCount}
                          page={regPage}
                          onChange={(_, v) => { setRegPage(v); setExpandedRegistres(new Set()); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
                          color="primary"
                          shape="rounded"
                        />
                      </Box>
                    )}
                  </Box>
                );
              })()}
            </Box>
          )}
          </>
          )}
        </>
      )}

      <Backdrop open={!!loadingPage} sx={{ zIndex: theme => theme.zIndex.modal - 1, color: '#fff', flexDirection: 'column', gap: 2 }}>
        <CircularProgress color="inherit" />
        <Typography variant="body2" sx={{ color: '#fff', opacity: 0.9 }}>{t('loadingImage')}</Typography>
      </Backdrop>

      <Backdrop open={preparingZip} sx={{ zIndex: theme => theme.zIndex.modal + 1, color: '#fff', flexDirection: 'column', gap: 2 }}>
        <CircularProgress color="inherit" />
        <Typography variant="body2" sx={{ color: '#fff', opacity: 0.9 }}>{t('preparingArchive')}</Typography>
      </Backdrop>

      {viewerOpen && viewerList.length > 0 && (
        <ImageViewer
          open={viewerOpen} onClose={() => { setViewerOpen(false); setDisplayedEntry(null); }}
          entry={displayedEntry ?? viewerList[viewerIndex]} index={viewerIndex} total={viewerList.length}
          onPrev={handlePrev} onNext={handleNext}
          getImageUrl={(p) => indexesApi.getPageImageUrl(selectedIndex, p)}
          getRelated={getRelated}
          onSelectPage={(p) => {
            const i = viewerList.findIndex(e => e.pageName === p);
            if (i >= 0) { setViewerIndex(i); setDisplayedEntry(null); }
            else setDisplayedEntry({ pageName: p, boxes: [], wordSummary: '' });
          }}
          getThumbLabel={getThumbLabel}
          getBoxes={getBoxes}
          onExpand={() => { setViewerOpen(false); setDisplayedEntry(null); const p = new URLSearchParams(searchParams); p.set('view', String(viewerIndex)); setSearchParams(p); }}
        />
      )}
    </Box>
  );
}
