import { useState } from 'react';
import { Box, Chip, CircularProgress, Popover, Stack, Typography } from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  Cancel as CancelIcon,
  WarningAmber as WarningAmberIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { SystemRequirements } from '../../api/system';

interface EnvStatusChipProps {
  requirements: SystemRequirements | null;
  loading: boolean;
  failed: boolean; // le backend n'a pas répondu à la vérification
}

function DetailRow({ ok, label, detail, error }: { ok: boolean; label: string; detail?: string | null; error?: string | null }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
      {ok
        ? <CheckCircleIcon fontSize="small" color="success" sx={{ mt: '1px' }} />
        : <CancelIcon fontSize="small" color="error" sx={{ mt: '1px' }} />}
      <Box>
        <Typography variant="body2">
          {label}{detail ? ` — ${detail}` : ''}
        </Typography>
        {error && (
          <Typography variant="caption" color="error.main" sx={{ display: 'block', maxWidth: 320 }}>
            {error}
          </Typography>
        )}
      </Box>
    </Box>
  );
}

/** Pastille compacte d'état de l'environnement OCR ; le détail s'affiche au clic. */
export default function EnvStatusChip({ requirements, loading, failed }: EnvStatusChipProps) {
  const { t } = useTranslation('ocr');
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  if (loading) {
    return (
      <Chip
        size="small"
        variant="outlined"
        icon={<CircularProgress size={14} sx={{ mx: 0.5 }} />}
        label={t('env.checking')}
      />
    );
  }

  const krakenOk = requirements?.kraken?.ok ?? false;
  const torchOk = requirements?.torch?.ok ?? false;
  const torchvisionOk = requirements?.torchvision?.ok ?? false;
  const cudaOk = requirements?.cuda?.ok ?? false;
  const blocked = failed || !requirements || !krakenOk || !torchOk || !torchvisionOk;

  const chip = blocked
    ? { label: t('env.problem'), color: 'error' as const, icon: <CancelIcon fontSize="small" /> }
    : cudaOk
      ? { label: t('env.ok'), color: 'success' as const, icon: <CheckCircleIcon fontSize="small" /> }
      : { label: t('env.cpuOnly'), color: 'warning' as const, icon: <WarningAmberIcon fontSize="small" /> };

  return (
    <>
      <Chip
        size="small"
        variant="outlined"
        color={chip.color}
        icon={chip.icon}
        label={chip.label}
        onClick={(e) => setAnchor(e.currentTarget)}
      />
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Stack spacing={1} sx={{ p: 2 }}>
          {failed || !requirements ? (
            <Typography variant="body2" color="error.main" sx={{ maxWidth: 320 }}>
              {t('env.backendNoResponse')}
            </Typography>
          ) : (
            <>
              <DetailRow
                ok={krakenOk}
                label="Kraken"
                detail={requirements.kraken?.version ?? t('env.notInstalled')}
                error={requirements.kraken?.error}
              />
              <DetailRow
                ok={torchOk}
                label="PyTorch"
                detail={requirements.torch?.version ?? t('env.notInstalled')}
              />
              <DetailRow
                ok={torchvisionOk}
                label="torchvision"
                detail={requirements.torchvision?.version ?? t('env.notInstalled')}
                error={requirements.torchvision?.error}
              />
              <DetailRow
                ok={cudaOk}
                label="CUDA"
                detail={cudaOk ? requirements.cuda?.device : t('env.cudaNotDetected')}
              />
            </>
          )}
        </Stack>
      </Popover>
    </>
  );
}
