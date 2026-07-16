import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Backdrop,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  IconButton,
  Pagination,
  Paper,
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
  ArrowBack as ArrowBackIcon,
  Download as DownloadIcon,
  FormatQuote as FormatQuoteIcon,
  ImageSearch as ImageSearchIcon,
  KeyboardArrowDown as ArrowDownIcon,
  KeyboardArrowRight as ArrowRightIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { indexesApi } from '../api/indexes';
import { PageSearchResult } from '../types';
import { usePageLoading } from '../context/LoadingContext';
import { usePageHeader } from '../context/HeaderContext';
import {
  ViewerEntry,
  groupByRegistre,
  displayPageName,
  pageToViewerEntry,
  renderAnnotatedBlob,
  ImageViewer,
  FullPageViewer,
  relatedByNumber,
} from '../components/PageImageViewer';

const REG_PER_PAGE = 10;

export default function WordPagesPage() {
  const { t } = useTranslation('search');
  const { indexId = '', word = '' } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [pages, setPages] = useState<PageSearchResult[]>([]);
  const [totalOccurrences, setTotalOccurrences] = useState(0);
  const [loading, setLoading] = useState(true);
  usePageLoading(t('loadingPages'), loading);
  const [error, setError] = useState<string | null>(null);

  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [loadingPage, setLoadingPage] = useState<string | null>(null);
  const [regPage, setRegPage] = useState(1);
  const [expandedRegistres, setExpandedRegistres] = useState<Set<string>>(new Set());
  // Registre dont on déroule actuellement les pages : affiche un loader le temps
  // que le rendu (potentiellement lourd) des lignes se fasse.
  const [expandingRegistre, setExpandingRegistre] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setRegPage(1);
    setExpandedRegistres(new Set());
    setExpandingRegistre(null);
    indexesApi.getWordPages(indexId, word)
      .then(data => {
        if (cancelled) return;
        // Adapter au format PageSearchResult pour réutiliser le visualiseur partagé.
        setPages(data.pages.map(p => ({ page_name: p.page_name, words: { [word]: p.occurrences } })));
        setTotalOccurrences(data.total_occurrences);
      })
      .catch(() => { if (!cancelled) setError(t('errors.loadWordPages')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [indexId, word]);

  const viewerList: ViewerEntry[] = useMemo(() => pages.map(pageToViewerEntry), [pages]);
  const allGroups = useMemo(() => groupByRegistre(pages), [pages]);

  const pageCount = Math.ceil(allGroups.length / REG_PER_PAGE);
  const visibleGroups = allGroups.slice((regPage - 1) * REG_PER_PAGE, regPage * REG_PER_PAGE);

  // En-tête affiché dans la barre bleue (titre + bouton retour vers le vocabulaire).
  usePageHeader(
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
      <Tooltip title={t('backToVocabulary')} arrow>
        <IconButton color="inherit" edge="start" onClick={() => navigate(`/indexes/${indexId}`)} aria-label={t('backToVocabulary')}>
          <ArrowBackIcon />
        </IconButton>
      </Tooltip>
      <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.4)', my: 1 }} />
      <FormatQuoteIcon sx={{ opacity: 0.9, flexShrink: 0 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="h6" noWrap sx={{ fontSize: '1.05rem', lineHeight: 1.2, fontWeight: 600 }}>
          «&nbsp;<span style={{ fontFamily: 'monospace' }}>{word}</span>&nbsp;»
        </Typography>
        {!loading && !error && (
          <Typography variant="caption" noWrap sx={{ display: 'block', opacity: 0.85, lineHeight: 1.2 }}>
            {t('pagesCount', { count: pages.length })} · {t('occurrencesCount', { count: totalOccurrences })} · {t('registresCount', { count: allGroups.length })}
          </Typography>
        )}
      </Box>
    </Box>,
    [indexId, word, loading, error, pages.length, totalOccurrences, allGroups.length],
  );

  const toggleRegistre = (registre: string) => {
    // Repli : immédiat.
    if (expandedRegistres.has(registre)) {
      setExpandedRegistres(prev => {
        const next = new Set(prev);
        next.delete(registre);
        return next;
      });
      return;
    }
    // Déploiement : on affiche d'abord un loader, puis on déclenche le rendu des
    // pages après deux frames (le temps que le loader soit peint), car un registre
    // peut contenir beaucoup de pages → rendu synchrone perceptible.
    setExpandingRegistre(registre);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      setExpandedRegistres(prev => {
        const next = new Set(prev);
        next.add(registre);
        return next;
      });
      // Garde le loader visible jusqu'à ce que les lignes soient peintes.
      requestAnimationFrame(() => requestAnimationFrame(() => setExpandingRegistre(null)));
    }));
  };

  const openViewer = (pageName: string) => {
    if (loadingPage) return;
    const idx = viewerList.findIndex(e => e.pageName === pageName);
    const entry = viewerList[idx >= 0 ? idx : 0];
    setLoadingPage(pageName);
    const img = new window.Image();
    img.src = indexesApi.getPageImageUrl(indexId, entry.pageName);
    const open = () => { setLoadingPage(null); setViewerIndex(idx >= 0 ? idx : 0); setViewerOpen(true); };
    img.onload = open;
    img.onerror = open;
  };

  const downloadPage = async (pageName: string) => {
    const url = indexesApi.getPageImageUrl(indexId, pageName);
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

  const handlePrev = useCallback(() => setViewerIndex(i => Math.max(0, i - 1)), []);
  const handleNext = useCallback(() => setViewerIndex(i => Math.min(viewerList.length - 1, i + 1)), [viewerList.length]);

  // Vue pleine page (?view=<index>) — composant maintenu monté, retour sans perte.
  const viewParam = searchParams.get('view');
  if (viewParam !== null && viewerList.length > 0) {
    const startIndex = Math.min(Math.max(0, Number(viewParam) || 0), viewerList.length - 1);
    return (
      <FullPageViewer
        entries={viewerList}
        startIndex={startIndex}
        getImageUrl={(p) => indexesApi.getPageImageUrl(indexId, p)}
        getRelated={(p) => relatedByNumber(viewerList.map(e => e.pageName), p)}
        backLabel={t('backToWordPages')}
        onBack={() => { const p = new URLSearchParams(searchParams); p.delete('view'); setSearchParams(p); }}
      />
    );
  }

  return (
    <Box>
      {error ? (
        <Alert severity="error">{error}</Alert>
      ) : loading ? (
        null  // overlay global (usePageLoading) pendant le chargement initial
      ) : pages.length === 0 ? (
        <Alert severity="info">{t('wordNotFound')}</Alert>
      ) : (
        <>
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
                        <TableRow hover sx={{ cursor: 'pointer' }} onClick={() => toggleRegistre(group.key)}>
                          <TableCell sx={{ width: 36, py: 0.5 }}>
                            {(isExpanded || expandingRegistre === group.key)
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
                            <Collapse in={isExpanded || expandingRegistre === group.key} unmountOnExit>
                              {expandingRegistre === group.key ? (
                                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5, py: 2.5, bgcolor: 'grey.50' }}>
                                  <CircularProgress size={20} thickness={5} />
                                  <Typography variant="body2" color="text.secondary">{t('loadingPages')}</Typography>
                                </Box>
                              ) : (
                              <Table size="small" sx={{ bgcolor: 'grey.50' }}>
                                <TableHead>
                                  <TableRow>
                                    <TableCell sx={{ pl: 4, width: 40, color: 'text.secondary', fontSize: '0.75rem', fontWeight: 600 }}>#</TableCell>
                                    <TableCell sx={{ fontWeight: 600, color: 'text.secondary', fontSize: '0.75rem' }}>{t('table.page')}</TableCell>
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
                                        <TableCell sx={{ pl: 4, color: 'text.disabled', fontSize: '0.75rem', width: 40 }}>{i + 1}</TableCell>
                                        <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.78rem', fontWeight: 500 }}>{displayPageName(page.page_name)}</TableCell>
                                        <TableCell align="right" sx={{ color: 'text.secondary', fontSize: '0.85rem', whiteSpace: 'nowrap' }}>{t('occAbbr', { count: totalOccs })}</TableCell>
                                        <TableCell align="right" sx={{ pr: 1, whiteSpace: 'nowrap' }}>
                                          <Tooltip title={t('showPage')}>
                                            <IconButton size="small" color="primary" onClick={e => { e.stopPropagation(); openViewer(page.page_name); }}>
                                              <ImageSearchIcon fontSize="small" />
                                            </IconButton>
                                          </Tooltip>
                                          <Tooltip title={t('downloadPage')}>
                                            <IconButton size="small" onClick={e => { e.stopPropagation(); downloadPage(page.page_name); }}>
                                              <DownloadIcon fontSize="small" />
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
            </TableContainer>
          </Paper>

          {pageCount > 1 && (
            <Box sx={{ display: 'flex', justifyContent: 'center', pt: 2 }}>
              <Pagination
                count={pageCount}
                page={regPage}
                onChange={(_, v) => { setRegPage(v); setExpandedRegistres(new Set()); setExpandingRegistre(null); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
                color="primary"
                shape="rounded"
              />
            </Box>
          )}
        </>
      )}

      <Backdrop open={!!loadingPage} sx={{ zIndex: theme => theme.zIndex.modal - 1, color: '#fff', flexDirection: 'column', gap: 2 }}>
        <CircularProgress color="inherit" />
        <Typography variant="body2" sx={{ color: '#fff', opacity: 0.9 }}>{t('loadingImage')}</Typography>
      </Backdrop>

      {viewerOpen && viewerList.length > 0 && (
        <ImageViewer
          open={viewerOpen} onClose={() => setViewerOpen(false)}
          entry={viewerList[viewerIndex]} index={viewerIndex} total={viewerList.length}
          onPrev={handlePrev} onNext={handleNext}
          getImageUrl={(p) => indexesApi.getPageImageUrl(indexId, p)}
          getRelated={(p) => relatedByNumber(viewerList.map(e => e.pageName), p)}
          onSelectPage={(p) => { const i = viewerList.findIndex(e => e.pageName === p); if (i >= 0) setViewerIndex(i); }}
          onExpand={() => { setViewerOpen(false); const p = new URLSearchParams(searchParams); p.set('view', String(viewerIndex)); setSearchParams(p); }}
        />
      )}
    </Box>
  );
}
