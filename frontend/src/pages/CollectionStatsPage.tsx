import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Box, Divider, IconButton, Tooltip, Typography } from '@mui/material';
import {
  ArrowBack as ArrowBackIcon,
  QueryStats as QueryStatsIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { collectionsApi } from '../api/collections';
import { CollectionMetadata } from '../types';
import { usePageHeader } from '../context/HeaderContext';
import CollectionStatsDashboard from '../components/stats/CollectionStatsDashboard';

export default function CollectionStatsPage() {
  const { t } = useTranslation('collections');
  const { collectionId = '' } = useParams();
  const navigate = useNavigate();
  const [collection, setCollection] = useState<CollectionMetadata | null>(null);

  useEffect(() => {
    collectionsApi.getById(collectionId).then(setCollection).catch(() => setCollection(null));
  }, [collectionId]);

  const periode = collection?.periode && (collection.periode[0] || collection.periode[1])
    ? `${collection.periode[0] || '…'} – ${collection.periode[1] || '…'}`
    : '';
  const subtitle = collection
    ? [collection.type, collection.lieu, periode].filter(Boolean).join(' · ')
    : '';

  // En-tête affiché dans la barre bleue (titre + bouton retour).
  usePageHeader(
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, minWidth: 0 }}>
      <Tooltip title={t('backToCollections')} arrow>
        <IconButton color="inherit" edge="start" onClick={() => navigate('/collections')} aria-label={t('backToCollections')}>
          <ArrowBackIcon />
        </IconButton>
      </Tooltip>
      <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.4)', my: 1 }} />
      <QueryStatsIcon sx={{ opacity: 0.9, flexShrink: 0 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="h6" noWrap sx={{ fontSize: '1.05rem', lineHeight: 1.2, fontWeight: 600 }}>
          {t('statsPage.headerTitle', { name: collection?.titre ?? collectionId })}
        </Typography>
        {subtitle && (
          <Typography variant="caption" noWrap sx={{ display: 'block', opacity: 0.85, lineHeight: 1.2 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
    </Box>,
    [collectionId, collection?.titre, subtitle],
  );

  return <CollectionStatsDashboard collectionId={collectionId} />;
}
