import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Button,
  IconButton,
  Skeleton,
} from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  ArrowForward as ArrowForwardIcon,
  GridView as GridViewIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { registresApi } from '../api/registres';
import { RegistreMetadata } from '../types';
import { usePageLoading } from '../context/LoadingContext';

export default function RegistreViewerPage() {
  const { t } = useTranslation('collections');
  const { collectionId, registreId } = useParams<{ collectionId: string; registreId: string }>();
  const navigate = useNavigate();

  const [registre, setRegistre] = useState<RegistreMetadata | null>(null);
  const [pages, setPages] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  usePageLoading(t('viewer.loading'), loading);
  const [selectedPageIndex, setSelectedPageIndex] = useState<number | null>(null);

  const [zoomLevel, setZoomLevel] = useState(0.25);
  const ZOOM_STEP = 0.25;
  const ZOOM_MIN = 0.25;
  const ZOOM_MAX = 3;

  useEffect(() => {
    if (!collectionId || !registreId) return;
    const load = async () => {
      setLoading(true);
      try {
        const [reg, pgs] = await Promise.all([
          registresApi.getById(collectionId, registreId),
          registresApi.getPages(collectionId, registreId),
        ]);
        setRegistre(reg);
        setPages(pgs);
      } catch (err) {
        console.error('Erreur chargement registre:', err);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [collectionId, registreId]);

  if (!collectionId || !registreId) return null;

  const titre = registre?.titre || registreId;

  return (
    <Box sx={{ width: '100%' }}>
      {/* Barre de navigation du registre */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <Button variant="outlined" size="small" onClick={() => navigate('/collections')}>
          {t('backToCollections')}
        </Button>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>
          {titre} — {t('viewer.headerPages', { count: pages.length })}
        </Typography>
        {selectedPageIndex !== null && (
          <>
            <Button
              size="small"
              variant="outlined"
              startIcon={<GridViewIcon />}
              onClick={() => { setSelectedPageIndex(null); setZoomLevel(0.25); }}
            >
              {t('viewer.thumbnails')}
            </Button>
            <IconButton
              onClick={() => { setSelectedPageIndex(Math.max(0, selectedPageIndex - 1)); setZoomLevel(0.25); }}
              disabled={selectedPageIndex === 0}
            >
              <ArrowBackIcon />
            </IconButton>
            <Typography variant="body1">
              {selectedPageIndex + 1} / {pages.length}
            </Typography>
            <IconButton
              onClick={() => { setSelectedPageIndex(Math.min(pages.length - 1, selectedPageIndex + 1)); setZoomLevel(0.25); }}
              disabled={selectedPageIndex === pages.length - 1}
            >
              <ArrowForwardIcon />
            </IconButton>
          </>
        )}
      </Box>

      {/* Contenu */}
      {loading ? (
        null  // overlay global (usePageLoading) pendant le chargement initial
      ) : selectedPageIndex !== null ? (
        <Box>
          <Box sx={{ overflow: 'auto', textAlign: 'center', maxHeight: 'calc(100vh - 200px)', position: 'relative' }}>
            <Skeleton
              variant="rectangular"
              sx={{ position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)', width: '60%', height: 300 }}
            />
            <img
              src={registresApi.getPageUrl(collectionId, registreId, pages[selectedPageIndex])}
              alt={pages[selectedPageIndex]}
              style={{
                width: `${zoomLevel * 100}%`,
                maxWidth: 'none',
                objectFit: 'contain',
                position: 'relative',
              }}
              onLoad={(e) => {
                const skeleton = (e.target as HTMLElement).previousElementSibling;
                if (skeleton) (skeleton as HTMLElement).style.display = 'none';
              }}
            />
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 2, mt: 1 }}>
            <IconButton
              onClick={() => setZoomLevel((z) => Math.max(ZOOM_MIN, z - ZOOM_STEP))}
              disabled={zoomLevel <= ZOOM_MIN}
              size="small"
            >
              <Typography variant="h6" sx={{ fontWeight: 'bold', lineHeight: 1 }}>−</Typography>
            </IconButton>
            <Box sx={{ width: 120, height: 6, bgcolor: 'grey.300', borderRadius: 3, position: 'relative' }}>
              <Box
                sx={{
                  position: 'absolute',
                  left: `${((zoomLevel - ZOOM_MIN) / (ZOOM_MAX - ZOOM_MIN)) * 100}%`,
                  top: '50%',
                  transform: 'translate(-50%, -50%)',
                  width: 14,
                  height: 14,
                  borderRadius: '50%',
                  bgcolor: 'primary.main',
                }}
              />
            </Box>
            <IconButton
              onClick={() => setZoomLevel((z) => Math.min(ZOOM_MAX, z + ZOOM_STEP))}
              disabled={zoomLevel >= ZOOM_MAX}
              size="small"
            >
              <Typography variant="h6" sx={{ fontWeight: 'bold', lineHeight: 1 }}>+</Typography>
            </IconButton>
            <Typography variant="body2" color="text.secondary" sx={{ minWidth: 45 }}>
              {Math.round(zoomLevel * 100)}%
            </Typography>
          </Box>
        </Box>
      ) : pages.length === 0 ? (
        <Typography variant="body1" color="text.secondary" sx={{ textAlign: 'center', mt: 4 }}>
          {t('viewer.noPages')}
        </Typography>
      ) : (
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
          {pages.map((page, index) => (
            <Box
              key={page}
              sx={{ width: 150, cursor: 'pointer', '&:hover': { opacity: 0.8 } }}
              onClick={() => setSelectedPageIndex(index)}
            >
              <Box sx={{ position: 'relative', height: 200 }}>
                <Skeleton
                  variant="rectangular"
                  height={200}
                  sx={{ position: 'absolute', top: 0, left: 0, width: '100%' }}
                />
                <img
                  src={registresApi.getPageUrl(collectionId, registreId, page)}
                  alt={page}
                  loading="lazy"
                  style={{ height: 200, width: '100%', objectFit: 'cover', position: 'relative' }}
                  onLoad={(e) => {
                    const skeleton = (e.target as HTMLElement).previousElementSibling;
                    if (skeleton) (skeleton as HTMLElement).style.display = 'none';
                  }}
                />
              </Box>
              <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block', mt: 0.5 }}>
                {page}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
}
