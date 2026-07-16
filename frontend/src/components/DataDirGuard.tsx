import { ReactNode, useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Stack, Typography, Button, Alert, CircularProgress } from '@mui/material';
import {
  FolderOff as FolderOffIcon,
  Refresh as RefreshIcon,
  WarningAmber as WarningAmberIcon,
} from '@mui/icons-material';
import { systemApi, StorageStatus } from '../api/system';

/**
 * Garde globale du stockage : vérifie une fois que le dossier de données existe.
 *
 * - data introuvable (ou backend injoignable) → écran bloquant plein, à la place du contenu.
 * - data présent mais non inscriptible → bannière d'avertissement + contenu normal.
 * - sinon → contenu normal.
 *
 * Un seul point de contrôle, monté autour de l'<Outlet /> du Layout : couvre toutes les pages.
 */
export default function DataDirGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation('common');
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const check = useCallback(() => {
    setLoading(true);
    systemApi.getStorage()
      .then((s) => { setStorage(s); setFailed(false); })
      .catch(() => { setStorage(null); setFailed(true); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { check(); }, [check]);

  // Premier chargement : on laisse passer le contenu (les pages gèrent leur propre
  // ressenti de chargement via GlobalLoadingOverlay). On bloque seulement une fois
  // qu'on sait que data est introuvable.
  const blocking = !loading && (failed || (storage !== null && !storage.exists));

  if (blocking) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', p: 3 }}>
        <Stack alignItems="center" spacing={2} sx={{ maxWidth: 560, textAlign: 'center' }}>
          <FolderOffIcon color="error" sx={{ fontSize: 64 }} />
          <Typography variant="h5" color="text.primary" sx={{ fontWeight: 700 }}>
            {t('dataDir.notFoundTitle')}
          </Typography>
          <Typography variant="body1" color="text.secondary">
            {failed ? t('dataDir.cannotReachServer') : t('dataDir.missing')}
          </Typography>
          {storage?.data_dir && (
            <Box
              sx={{
                fontFamily: 'monospace',
                fontSize: '0.85rem',
                px: 1.5, py: 1,
                bgcolor: 'grey.100',
                borderRadius: 1,
                border: '1px solid',
                borderColor: 'divider',
                wordBreak: 'break-all',
              }}
            >
              {storage.data_dir}
            </Box>
          )}
          <Button
            variant="contained"
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <RefreshIcon />}
            onClick={check}
            disabled={loading}
          >
            {t('dataDir.retry')}
          </Button>
        </Stack>
      </Box>
    );
  }

  return (
    <>
      {storage && storage.exists && !storage.writable && (
        <Alert severity="warning" icon={<WarningAmberIcon />} sx={{ mb: 2 }}>
          {t('dataDir.readOnly')}
          <Box component="span" sx={{ fontFamily: 'monospace' }}>{storage.data_dir}</Box>.
        </Alert>
      )}
      {children}
    </>
  );
}
