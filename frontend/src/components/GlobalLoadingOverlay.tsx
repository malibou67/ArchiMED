import { Backdrop, CircularProgress, Typography, Stack } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useLoadingContext } from '../context/LoadingContext';

/**
 * Overlay de chargement global affiché par-dessus toute l'application.
 * Le message change selon ce qui est en cours de chargement.
 */
export default function GlobalLoadingOverlay() {
  const { t } = useTranslation('common');
  const { message } = useLoadingContext();

  // 1re ligne : titre ; lignes suivantes (séparées par \n) : sous-étape plus discrète.
  const [title, ...rest] = (message ?? t('loading.default')).split('\n');

  return (
    <Backdrop
      open={message !== null}
      sx={{
        zIndex: (theme) => theme.zIndex.modal + 1,
        backgroundColor: 'rgba(255, 255, 255, 0.94)',
      }}
    >
      <Stack alignItems="center" spacing={1}>
        <CircularProgress color="primary" sx={{ mb: 1 }} />
        <Typography variant="h6" color="text.primary">
          {title}
        </Typography>
        {rest.length > 0 && (
          <Typography variant="body2" color="text.secondary">
            {rest.join(' ')}
          </Typography>
        )}
      </Stack>
    </Backdrop>
  );
}
