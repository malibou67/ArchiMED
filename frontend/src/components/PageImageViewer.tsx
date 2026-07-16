import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Paper,
  Slider,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  ArrowBackIosNew as PrevIcon,
  ArrowForwardIos as NextIcon,
  Close as CloseIcon,
  Download as DownloadIcon,
  OpenInFull as OpenInFullIcon,
  ZoomIn as ZoomInIcon,
  ZoomOut as ZoomOutIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { PageSearchResult, ResultSource } from '../types';

// ─── Palette « lightbox » sombre (en dur : indépendante du thème clair de l'app) ────
const CANVAS_BG = '#1f1f23';   // fond de la zone image
const RAIL_BG = '#26262b';     // fond du rail de miniatures
const HEADER_BG = '#18181b';   // fond des barres (en-tête dialog / plein écran)
const FG = 'rgba(255,255,255,0.92)';
const FG_MUTED = 'rgba(255,255,255,0.55)';
const FLOAT_BG = 'rgba(0,0,0,0.55)';
const FLOAT_BG_HOVER = 'rgba(0,0,0,0.75)';
const ACCENT = '#5b9bd5';      // bleu clair lisible sur fond sombre (sélection miniature)

export interface BoundingBox { x1: number; y1: number; x2: number; y2: number; }
export interface ViewerEntry { pageName: string; boxes: BoundingBox[]; wordSummary: string; }
export interface RegistreGroup { key: string; registre: string; source?: ResultSource; pages: PageSearchResult[]; }

// Nom de page affichable : sans le préfixe technique de source ('sX::').
export function displayPageName(pageName: string): string {
  return pageName.includes('::') ? pageName.split('::').slice(1).join('::') : pageName;
}

export function extractRegistre(pageName: string): string {
  const bare = displayPageName(pageName);
  const m = bare.match(/^(.+?)_\d/);
  return m ? m[1] : bare;
}

export function groupByRegistre(pages: PageSearchResult[]): RegistreGroup[] {
  // Groupe par (source, registre) : deux collections peuvent avoir un registre homonyme.
  const map = new Map<string, RegistreGroup>();
  for (const page of pages) {
    const registre = page.registre || extractRegistre(page.page_name);
    const srcKey = page.source
      ? `${page.source.collection_folder ?? ''}/${page.source.model_name ?? ''}`
      : '';
    const key = `${srcKey}::${registre}`;
    if (!map.has(key)) map.set(key, { key, registre, source: page.source, pages: [] });
    map.get(key)!.pages.push(page);
  }
  return Array.from(map.values()).sort((a, b) =>
    (a.source?.collection_titre ?? '').localeCompare(b.source?.collection_titre ?? '', 'fr')
    || a.registre.localeCompare(b.registre, 'fr'));
}

export function parseBox(occ: string): BoundingBox | null {
  const m = occ.match(/\((\d+),\s*(\d+)\),\s*\((\d+),\s*(\d+)\)/);
  return m ? { x1: +m[1], y1: +m[2], x2: +m[3], y2: +m[4] } : null;
}

export function pageToViewerEntry(page: PageSearchResult): ViewerEntry {
  const boxes: BoundingBox[] = [];
  const parts: string[] = [];
  for (const [word, occs] of Object.entries(page.words)) {
    const wordBoxes = occs.map(parseBox).filter(Boolean) as BoundingBox[];
    boxes.push(...wordBoxes);
    parts.push(`${word} (${wordBoxes.length})`);
  }
  return { pageName: page.page_name, boxes, wordSummary: parts.sort().join(', ') };
}

const ZOOM_STEP = 0.25;
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3;

export async function renderAnnotatedBlob(imageUrl: string, boxes: BoundingBox[]): Promise<Blob> {
  const response = await fetch(imageUrl);
  const raw = await response.blob();
  const blobUrl = URL.createObjectURL(raw);
  const img = new window.Image();
  img.src = blobUrl;
  await new Promise(resolve => { img.onload = resolve; });
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d')!;
  ctx.drawImage(img, 0, 0);
  URL.revokeObjectURL(blobUrl);
  const sw = Math.max(2, Math.round(img.naturalWidth / 300));
  boxes.forEach(box => {
    ctx.fillStyle = 'rgba(255, 220, 0, 0.35)';
    ctx.fillRect(box.x1, box.y1, box.x2 - box.x1, box.y2 - box.y1);
    ctx.strokeStyle = '#e53935';
    ctx.lineWidth = sw;
    ctx.strokeRect(box.x1, box.y1, box.x2 - box.x1, box.y2 - box.y1);
  });
  return new Promise((resolve, reject) =>
    canvas.toBlob(b => b ? resolve(b) : reject(new Error('toBlob failed')), 'image/png')
  );
}

export async function downloadAnnotatedPage(imageUrl: string, entry: ViewerEntry): Promise<void> {
  const blob = await renderAnnotatedBlob(imageUrl, entry.boxes);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = entry.pageName.replace(/\.[^.]+$/, '') + '.png';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

// ─── Corps réutilisable : image + zoom (boutons + molette) + navigation ─────────

/** Pages liées à la page affichée (ex. pages « extra » d'une page de registre),
 * montrées en miniatures cliquables sur le côté. Inclut la page courante. */
export interface RelatedPages {
  pages: string[];
  onSelect: (pageName: string) => void;
  /** Libellé d'une miniature (défaut : thumbLabel sans motif). */
  label?: (pageName: string) => string;
  /** Boîtes du mot recherché pour une page liée — surlignées sur la miniature. */
  boxesFor?: (pageName: string) => BoundingBox[];
}

/** Miniature d'une page liée avec, si fournies, les boîtes du mot recherché
 * surlignées (overlay SVG calé sur les dimensions naturelles de l'image). */
function RelatedThumb({ src, alt, boxes }: { src: string; alt: string; boxes?: BoundingBox[] }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  return (
    <Box sx={{ position: 'relative', lineHeight: 0 }}>
      <img
        src={src} alt={alt} loading="lazy"
        style={{ width: '100%', display: 'block', minHeight: 40, background: '#0d0d0f' }}
        onLoad={e => { const img = e.target as HTMLImageElement; setSize({ w: img.naturalWidth, h: img.naturalHeight }); }}
      />
      {size && boxes && boxes.length > 0 && (
        <svg viewBox={`0 0 ${size.w} ${size.h}`} style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
          {boxes.map((box, i) => (
            <rect key={i} x={box.x1} y={box.y1} width={box.x2 - box.x1} height={box.y2 - box.y1}
              fill="rgba(255,220,0,0.35)" stroke="#e53935" strokeWidth={Math.max(2, Math.round(size.w / 300))} />
          ))}
        </svg>
      )}
    </Box>
  );
}

/** Famille d'une page déduite du nom de fichier (« REG_16.jpg » et « REG_16-1.jpg »
 * partagent le préfixe et le numéro). Pour les contextes sans motifs de registre
 * (recherche) : la famille est limitée aux pages de la liste fournie. */
export function relatedByNumber(pages: string[], page: string): string[] {
  const keyOf = (p: string) => {
    const m = p.match(/^(.+?)_(\d+)(?:-.+)?\.[^.]+$/);
    return m ? `${m[1]}_${m[2]}` : null;
  };
  const key = keyOf(page);
  if (key == null) return [page];
  return pages.filter(p => keyOf(p) === key);
}

// ─── Pagination par motif (partagé avec CollectionsPage) ───────────────────────

/** Construit une regex depuis un motif de nom de fichier contenant {num} et
 * {extra_page}. Si `capturing`, les deux jetons sont capturés (groupe 1 = num,
 * groupe 2 = extra_page). */
export function makePatternRegex(pattern: string, capturing: boolean): RegExp {
  const parts = pattern.split(/(\{num\}|\{extra_page\})/);
  const regex = parts.map(p => {
    if (p === '{num}') return capturing ? '(\\d+)' : '\\d+';
    if (p === '{extra_page}') return capturing ? '(.+?)' : '.+';
    return p.replace(/[.+*?^${}()|[\]\\]/g, '\\$&');
  }).join('');
  return new RegExp('^' + regex + '$');
}

/** Trie des noms de pages par numéro principal, page principale avant ses extras. */
export function sortPages(pages: string[], mainPattern?: string, extraPattern?: string): string[] {
  const mainRx = mainPattern ? makePatternRegex(mainPattern, true) : null;
  const extraRx = extraPattern ? makePatternRegex(extraPattern, true) : null;

  const info = pages.map(file => {
    const mainM = mainRx?.exec(file);
    if (mainM) return { file, mainNum: parseInt(mainM[1]), isExtra: false, extraId: '' };
    const extraM = extraRx?.exec(file);
    if (extraM) return { file, mainNum: parseInt(extraM[1]), isExtra: true, extraId: extraM[2] ?? '' };
    return { file, mainNum: 0, isExtra: false, extraId: '' };
  });

  return info.sort((a, b) => {
    if (a.mainNum !== b.mainNum) return a.mainNum - b.mainNum;
    if (a.isExtra !== b.isExtra) return a.isExtra ? 1 : -1;
    return a.extraId.localeCompare(b.extraId, undefined, { numeric: true });
  }).map(i => i.file);
}

/** Famille d'une page (principale + extras du même numéro) parmi `pages`, ordonnée
 * (principale d'abord). Utilise les motifs si fournis ; sinon repli sur un
 * regroupement par préfixe `<base>_<num>` (séparateur `-` ou `_` pour les extras),
 * extension optionnelle — adapté aussi bien aux noms de fichiers qu'aux stems. */
export function pageFamilyFromList(
  pages: string[], page: string, mainPattern?: string, extraPattern?: string,
): string[] {
  const mainRx = mainPattern ? makePatternRegex(mainPattern, true) : null;
  const extraRx = extraPattern ? makePatternRegex(extraPattern, true) : null;

  const numByPattern = (p: string): string | null => {
    const m = mainRx?.exec(p);
    if (m) return m[1];
    const e = extraRx?.exec(p);
    if (e) return e[1];
    return null;
  };
  // Repli sans motif : capture `<base>_<num>` (les extras suivent par `-` ou `_`).
  const numFallback = (p: string): string | null => {
    const m = p.match(/^(.+?)_(\d+)(?:[-_].+)?(?:\.[^.]+)?$/);
    return m ? `${m[1]}_${m[2]}` : null;
  };
  const keyOf = mainRx || extraRx ? numByPattern : numFallback;

  const key = keyOf(page);
  if (key == null) return [page];
  const family = pages.filter(p => keyOf(p) === key);
  return sortPages(family, mainPattern, extraPattern);
}

// Libellé court d'une miniature : numéro de page principale, et « num - extra » pour
// une page extra. Utilise les motifs si fournis (extension du motif rendue optionnelle,
// pour fonctionner aussi bien sur les noms de fichiers que sur les stems) ; sinon repli
// sur la partie après le dernier « _ ».
export function thumbLabel(pageName: string, mainPattern?: string, extraPattern?: string): string {
  const stem = pageName.replace(/\.[^.]+$/, '');
  const stripExt = (p: string) => p.replace(/\.[^.]+$/, '');
  const extraRx = extraPattern ? makePatternRegex(stripExt(extraPattern), true) : null;
  const e = extraRx?.exec(stem);
  if (e) return `${e[1]} - ${e[2]}`;
  const mainRx = mainPattern ? makePatternRegex(stripExt(mainPattern), true) : null;
  const m = mainRx?.exec(stem);
  if (m) return m[1];
  const idx = stem.lastIndexOf('_');
  return idx >= 0 ? stem.slice(idx + 1) : stem;
}

interface BodyProps {
  entry: ViewerEntry;
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  /** Résout l'URL de l'image d'une page (index, registre…) — le viewer est agnostique de la source. */
  getImageUrl: (pageName: string) => string;
  related?: RelatedPages;
  maxHeight?: string;
}

export function PageViewerBody({ entry, index, total, onPrev, onNext, getImageUrl, related, maxHeight = 'calc(100vh - 260px)' }: BodyProps) {
  const { t } = useTranslation('common');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [zoomLevel, setZoomLevel] = useState(0.25);
  const wheelCleanup = useRef<(() => void) | null>(null);
  const imageUrl = getImageUrl(entry.pageName);

  useEffect(() => { setLoading(true); setError(false); setNaturalSize(null); setZoomLevel(0.25); }, [entry.pageName]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'ArrowLeft') onPrev(); else if (e.key === 'ArrowRight') onNext(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onPrev, onNext]);

  // Zoom à la molette (ancré sur le curseur) + panoramique « cliquer-glisser ».
  // Ref callback : écouteurs non-passifs posés dès le montage, nettoyés au démontage.
  const attachScroll = useCallback((node: HTMLDivElement | null) => {
    if (wheelCleanup.current) { wheelCleanup.current(); wheelCleanup.current = null; }
    if (!node) return;
    const WHEEL_STEP = 0.15;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = node.getBoundingClientRect();
      const cursorX = e.clientX - rect.left;
      const cursorY = e.clientY - rect.top;
      const beforeW = node.scrollWidth;
      const beforeH = node.scrollHeight;
      const ratioX = beforeW > 0 ? (node.scrollLeft + cursorX) / beforeW : 0.5;
      const ratioY = beforeH > 0 ? (node.scrollTop + cursorY) / beforeH : 0.5;
      setZoomLevel(z => {
        const dir = e.deltaY < 0 ? 1 : -1;
        const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((z + dir * WHEEL_STEP) * 100) / 100));
        if (next !== z) {
          requestAnimationFrame(() => {
            node.scrollLeft = ratioX * node.scrollWidth - cursorX;
            node.scrollTop = ratioY * node.scrollHeight - cursorY;
          });
        }
        return next;
      });
    };

    // Panoramique : on déplace le scroll pendant le glissement. Écouteurs sur window
    // pour continuer même si la souris sort du cadre.
    let startX = 0, startY = 0, startLeft = 0, startTop = 0;
    const onMouseMove = (e: MouseEvent) => {
      node.scrollLeft = startLeft - (e.clientX - startX);
      node.scrollTop = startTop - (e.clientY - startY);
    };
    const endDrag = () => {
      node.style.cursor = 'grab';
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', endDrag);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const scrollable = node.scrollWidth > node.clientWidth || node.scrollHeight > node.clientHeight;
      if (!scrollable) return;
      e.preventDefault();
      startX = e.clientX; startY = e.clientY;
      startLeft = node.scrollLeft; startTop = node.scrollTop;
      node.style.cursor = 'grabbing';
      window.addEventListener('mousemove', onMouseMove);
      window.addEventListener('mouseup', endDrag);
    };

    node.addEventListener('wheel', onWheel, { passive: false });
    node.addEventListener('mousedown', onMouseDown);
    wheelCleanup.current = () => {
      node.removeEventListener('wheel', onWheel);
      node.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', endDrag);
    };
  }, []);

  const strokeWidth = naturalSize ? Math.max(2, Math.round(naturalSize.w / 300)) : 2;

  const navBtnSx = {
    position: 'absolute' as const, top: '50%', transform: 'translateY(-50%)', zIndex: 3,
    width: 44, height: 44, color: FG, bgcolor: FLOAT_BG, backdropFilter: 'blur(6px)',
    border: '1px solid rgba(255,255,255,0.12)',
    '&:hover': { bgcolor: FLOAT_BG_HOVER },
  };

  return (
    <Box sx={{ display: 'flex', bgcolor: CANVAS_BG }}>
      <Box sx={{ position: 'relative', flex: 1, minWidth: 0 }}>
        {loading && !error && (
          <Box sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2, pointerEvents: 'none' }}>
            <CircularProgress sx={{ color: FG }} />
          </Box>
        )}

        <Box
          ref={attachScroll}
          sx={{ height: maxHeight, overflow: 'auto', display: 'flex', p: 3, cursor: 'grab' }}
        >
          {error && <Alert severity="error" sx={{ m: 'auto' }}>{t('imageViewer.loadError')}</Alert>}
          {!error && (
            <Box sx={{ position: 'relative', m: 'auto', flex: '0 0 auto', width: `${zoomLevel * 100}%`, minHeight: loading ? 320 : undefined }}>
              <img key={imageUrl} src={imageUrl} alt={entry.pageName} draggable={false}
                style={{ width: '100%', display: 'block', opacity: loading ? 0 : 1, transition: 'opacity 0.15s', boxShadow: '0 8px 40px rgba(0,0,0,0.5)' }}
                onLoad={e => { const img = e.target as HTMLImageElement; setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight }); setLoading(false); }}
                onError={() => { setError(true); setLoading(false); }}
              />
              {naturalSize && !loading && (
                <svg viewBox={`0 0 ${naturalSize.w} ${naturalSize.h}`} style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
                  {entry.boxes.map((box, i) => (
                    <rect key={i} x={box.x1} y={box.y1} width={box.x2 - box.x1} height={box.y2 - box.y1}
                      fill="rgba(255,220,0,0.35)" stroke="#e53935" strokeWidth={strokeWidth} />
                  ))}
                </svg>
              )}
            </Box>
          )}
        </Box>

        {/* Flèches de navigation flottantes (masquées aux extrémités) */}
        <Tooltip title={t('imageViewer.prev')} placement="right">
          <IconButton onClick={onPrev} aria-label={t('imageViewer.prev')}
            sx={{ ...navBtnSx, left: 16, visibility: index === 0 ? 'hidden' : 'visible' }}>
            <PrevIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t('imageViewer.next')} placement="left">
          <IconButton onClick={onNext} aria-label={t('imageViewer.next')}
            sx={{ ...navBtnSx, right: 16, visibility: index === total - 1 ? 'hidden' : 'visible' }}>
            <NextIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        {/* Légende discrète */}
        <Typography sx={{ position: 'absolute', bottom: 18, left: 18, zIndex: 3, fontSize: '0.7rem', color: FG_MUTED, pointerEvents: 'none', userSelect: 'none' }}>
          {t('imageViewer.legend')}
        </Typography>

        {/* Pilule de zoom flottante */}
        <Box sx={{
          position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 3,
          display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.5,
          bgcolor: FLOAT_BG, backdropFilter: 'blur(6px)', borderRadius: 999,
          border: '1px solid rgba(255,255,255,0.12)',
        }}>
          <IconButton size="small" sx={{ color: FG }} onClick={() => setZoomLevel(z => Math.max(ZOOM_MIN, z - ZOOM_STEP))} disabled={zoomLevel <= ZOOM_MIN}>
            <ZoomOutIcon fontSize="small" />
          </IconButton>
          <Slider
            value={zoomLevel} min={ZOOM_MIN} max={ZOOM_MAX} step={0.05}
            onChange={(_, v) => setZoomLevel(v as number)}
            size="small" aria-label={t('imageViewer.zoom')}
            sx={{ width: 130, color: FG, '& .MuiSlider-rail': { opacity: 0.4 } }}
          />
          <IconButton size="small" sx={{ color: FG }} onClick={() => setZoomLevel(z => Math.min(ZOOM_MAX, z + ZOOM_STEP))} disabled={zoomLevel >= ZOOM_MAX}>
            <ZoomInIcon fontSize="small" />
          </IconButton>
          <Typography variant="caption" sx={{ color: FG, minWidth: 38, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
            {Math.round(zoomLevel * 100)}%
          </Typography>
        </Box>
      </Box>

      {/* Pages liées (page principale + pages extra) en miniatures cliquables */}
      {related && related.pages.length > 1 && (
        <Box sx={{ width: 140, flexShrink: 0, bgcolor: RAIL_BG, borderLeft: '1px solid rgba(255,255,255,0.08)', overflow: 'auto', height: maxHeight, p: 1.25 }}>
          <Typography
            variant="overline"
            sx={{ fontSize: '0.62rem', fontWeight: 700, letterSpacing: 0.6, color: FG_MUTED, display: 'block', textAlign: 'center', mb: 1 }}
          >
            {t('imageViewer.relatedPages', { count: related.pages.length })}
          </Typography>
          {related.pages.map((p) => {
            const isCurrent = p === entry.pageName;
            return (
              <Tooltip key={p} title={p} arrow placement="left">
                <Box
                  onClick={() => { if (!isCurrent) related.onSelect(p); }}
                  sx={{
                    mb: 1.25, borderRadius: 1.5, overflow: 'hidden',
                    border: '2px solid', borderColor: isCurrent ? ACCENT : 'rgba(255,255,255,0.12)',
                    boxShadow: isCurrent ? `0 0 0 3px ${ACCENT}59` : 'none',
                    cursor: isCurrent ? 'default' : 'pointer',
                    transition: 'border-color 0.15s, box-shadow 0.15s',
                    '&:hover': { borderColor: ACCENT },
                  }}
                >
                  <RelatedThumb src={getImageUrl(p)} alt={p} boxes={related.boxesFor?.(p)} />
                  <Typography
                    variant="caption"
                    sx={{
                      display: 'block', textAlign: 'center', py: 0.4,
                      fontFamily: 'monospace', fontSize: '0.68rem', fontWeight: isCurrent ? 700 : 500,
                      bgcolor: isCurrent ? ACCENT : 'rgba(255,255,255,0.06)',
                      color: isCurrent ? '#0d1b2a' : FG_MUTED,
                    }}
                  >
                    {related.label ? related.label(p) : thumbLabel(p)}
                  </Typography>
                </Box>
              </Tooltip>
            );
          })}
        </Box>
      )}
    </Box>
  );
}

// ─── Visualiseur en dialog (aperçu rapide) ──────────────────────────────────────

interface ViewerProps {
  open: boolean; onClose: () => void; entry: ViewerEntry;
  index: number; total: number; onPrev: () => void; onNext: () => void;
  getImageUrl: (pageName: string) => string;
  onExpand?: () => void;  // « Ouvrir en pleine page »
  /** Famille de pages liées (page courante incluse) — affichées en miniatures. */
  getRelated?: (pageName: string) => string[];
  onSelectPage?: (pageName: string) => void;
  /** Libellé d'une miniature (ex. « num - extra ») — défaut : thumbLabel sans motif. */
  getThumbLabel?: (pageName: string) => string;
  /** Boîtes du mot recherché pour une page liée — surlignées sur la miniature. */
  getBoxes?: (pageName: string) => BoundingBox[];
}

export function ImageViewer({ open, onClose, entry, index, total, onPrev, onNext, getImageUrl, onExpand, getRelated, onSelectPage, getThumbLabel, getBoxes }: ViewerProps) {
  const { t } = useTranslation('common');
  const [downloading, setDownloading] = useState(false);

  const download = async () => {
    setDownloading(true);
    try { await downloadAnnotatedPage(getImageUrl(entry.pageName), entry); } finally { setDownloading(false); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth
      slotProps={{ paper: { sx: { bgcolor: HEADER_BG, borderRadius: 3, overflow: 'hidden', height: '92vh' } } }}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, py: 1.25, px: 2, bgcolor: HEADER_BG, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
          <Typography sx={{ fontFamily: 'monospace', fontWeight: 600, color: FG, fontSize: '0.95rem' }} noWrap>{entry.pageName}</Typography>
          {entry.wordSummary && <Chip label={entry.wordSummary} size="small" color="warning" sx={{ flexShrink: 0 }} />}
          <Typography variant="body2" sx={{ color: FG_MUTED, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>{index + 1} / {total}</Typography>
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexShrink: 0 }}>
          <Tooltip title={t('imageViewer.downloadAnnotated')}>
            <span>
              <IconButton onClick={download} disabled={downloading} sx={{ color: FG, '&.Mui-disabled': { color: FG_MUTED } }}>
                {downloading ? <CircularProgress size={20} sx={{ color: FG }} /> : <DownloadIcon />}
              </IconButton>
            </span>
          </Tooltip>
          {onExpand && (
            <Tooltip title={t('imageViewer.openFull')}>
              <IconButton onClick={onExpand} sx={{ color: FG }}><OpenInFullIcon /></IconButton>
            </Tooltip>
          )}
          <Tooltip title={t('actions.close')}>
            <IconButton onClick={onClose} sx={{ color: FG }}><CloseIcon /></IconButton>
          </Tooltip>
        </Box>
      </DialogTitle>
      <DialogContent sx={{ p: 0 }}>
        <PageViewerBody
          entry={entry} index={index} total={total} onPrev={onPrev} onNext={onNext} getImageUrl={getImageUrl}
          related={getRelated && onSelectPage ? { pages: getRelated(entry.pageName), onSelect: onSelectPage, label: getThumbLabel, boxesFor: getBoxes } : undefined}
          maxHeight="calc(92vh - 57px)"
        />
      </DialogContent>
    </Dialog>
  );
}

// ─── Visualiseur en pleine page (vraie page, avec retour) ───────────────────────

interface FullPageProps {
  entries: ViewerEntry[];
  startIndex: number;
  getImageUrl: (pageName: string) => string;
  onBack: () => void;
  backLabel?: string;
  /** Famille de pages liées (page courante incluse) — la sélection saute à l'entrée correspondante. */
  getRelated?: (pageName: string) => string[];
  /** Résout en entrée affichable une page liée absente de `entries` (ex. page « extra »
   * d'une recherche). Sélectionner une telle page l'affiche seule (override). */
  getEntry?: (pageName: string) => ViewerEntry | undefined;
  /** Libellé d'une miniature (ex. « num - extra ») — défaut : thumbLabel sans motif. */
  getThumbLabel?: (pageName: string) => string;
  /** Boîtes du mot recherché pour une page liée — surlignées sur la miniature. */
  getBoxes?: (pageName: string) => BoundingBox[];
}

export function FullPageViewer({ entries, startIndex, getImageUrl, onBack, backLabel, getRelated, getEntry, getThumbLabel, getBoxes }: FullPageProps) {
  const { t } = useTranslation('common');
  const backText = backLabel ?? t('imageViewer.back');
  const [idx, setIdx] = useState(startIndex);
  const [override, setOverride] = useState<ViewerEntry | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => { setIdx(startIndex); setOverride(null); }, [startIndex]);

  const entry = override ?? entries[idx];
  const prev = useCallback(() => { setOverride(null); setIdx(i => Math.max(0, i - 1)); }, []);
  const next = useCallback(() => { setOverride(null); setIdx(i => Math.min(entries.length - 1, i + 1)); }, [entries.length]);

  const download = async () => {
    if (!entry) return;
    setDownloading(true);
    try { await downloadAnnotatedPage(getImageUrl(entry.pageName), entry); } finally { setDownloading(false); }
  };

  if (!entry) {
    return (
      <Box>
        <Button startIcon={<ArrowBackIcon />} onClick={onBack} size="small" sx={{ mb: 2 }}>{backText}</Button>
        <Alert severity="info">{t('imageViewer.noPage')}</Alert>
      </Box>
    );
  }

  return (
    <Paper elevation={0} sx={{
      // Plein-cadre : sort du padding du <main> (p:3), collé sous l'AppBar (64px),
      // jusqu'en bas et contre les bords — un seul bloc sombre cohérent.
      m: -3, borderRadius: 0, overflow: 'hidden', bgcolor: HEADER_BG,
      height: 'calc(100vh - 64px)', display: 'flex', flexDirection: 'column',
    }}>
      {/* Barre de titre intégrée à la carte */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 2, py: 1.25, borderBottom: '1px solid rgba(255,255,255,0.08)', flexWrap: 'wrap' }}>
        <Button startIcon={<ArrowBackIcon />} onClick={onBack} size="small" sx={{ flexShrink: 0, color: FG }}>{backText}</Button>
        <Box sx={{ width: '1px', height: 24, bgcolor: 'rgba(255,255,255,0.15)', flexShrink: 0, display: { xs: 'none', sm: 'block' } }} />
        <Typography variant="subtitle2" sx={{ fontFamily: 'monospace', fontWeight: 600, minWidth: 0, color: FG }} noWrap>{entry.pageName}</Typography>
        {entry.wordSummary && <Chip label={entry.wordSummary} size="small" color="warning" />}
        <Typography variant="caption" sx={{ flexShrink: 0, color: FG_MUTED, fontVariantNumeric: 'tabular-nums' }}>{idx + 1} / {entries.length}</Typography>
        <Box sx={{ flex: 1 }} />
        <Tooltip title={t('imageViewer.downloadAnnotated')}>
          <span>
            <Button onClick={download} disabled={downloading} size="small" variant="outlined"
              startIcon={downloading ? <CircularProgress size={16} sx={{ color: FG }} /> : <DownloadIcon />}
              sx={{ flexShrink: 0, color: FG, borderColor: 'rgba(255,255,255,0.3)', '&:hover': { borderColor: FG, bgcolor: 'rgba(255,255,255,0.08)' }, '&.Mui-disabled': { color: FG_MUTED, borderColor: 'rgba(255,255,255,0.15)' } }}>
              {t('imageViewer.download')}
            </Button>
          </span>
        </Tooltip>
      </Box>
      <PageViewerBody
        entry={entry}
        index={idx}
        total={entries.length}
        onPrev={prev}
        onNext={next}
        getImageUrl={getImageUrl}
        related={getRelated ? {
          pages: getRelated(entry.pageName),
          onSelect: (p) => {
            const i = entries.findIndex(e => e.pageName === p);
            if (i >= 0) { setOverride(null); setIdx(i); return; }
            const extra = getEntry?.(p);
            if (extra) setOverride(extra);
          },
          label: getThumbLabel,
          boxesFor: getBoxes,
        } : undefined}
        maxHeight="calc(100vh - 120px)"
      />
    </Paper>
  );
}
