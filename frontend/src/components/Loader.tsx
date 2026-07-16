import { Box, CircularProgress, Typography } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';
import { useTranslation } from 'react-i18next';

/**
 * Loader commun (spinner + message) affiché dans le contenu pendant un chargement.
 * Message par défaut adapté aux cartes de statistiques ; passez `message` pour
 * un libellé spécifique (ex. « Chargement des modèles… » sur une page de liste).
 */
export default function Loader({
  message,
  minHeight,
  sx,
}: {
  message?: string;
  minHeight?: number | string;
  sx?: SxProps<Theme>;
}) {
  const { t } = useTranslation('common');
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1.5, py: 4, minHeight, ...sx }}>
      <CircularProgress size={20} />
      <Typography variant="body2" color="text.secondary">{message ?? t('loading.stats')}</Typography>
    </Box>
  );
}
