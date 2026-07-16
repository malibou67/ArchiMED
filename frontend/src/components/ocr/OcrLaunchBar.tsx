import { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, LinearProgress, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { Task } from '../../api/tasks';

const fmtNum = (n: number) => n.toLocaleString('fr-FR');

function fmtEstimate(seconds: number): string {
  const min = Math.max(1, Math.round(seconds / 60));
  if (min < 60) return `~${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m > 0 ? `~${h} h ${String(m).padStart(2, '0')}` : `~${h} h`;
}

interface OcrLaunchBarProps {
  selectedCount: number;
  segModelName: string | null;
  ocrModelName: string | null;
  estimateSeconds: number | null;
  runningOcr: Task | null;
  launching: boolean;
  canLaunch: boolean;
  disabledReason: string | null;
  envChip?: ReactNode;
  onLaunch: () => void;
  onClearSelection: () => void;
}

/** Barre de lancement : récapitulatif de la sélection, estimation de durée,
 * progression inline de la tâche OCR en cours, bouton « Lancer ». */
export default function OcrLaunchBar({
  selectedCount, segModelName, ocrModelName, estimateSeconds,
  runningOcr, launching, canLaunch, disabledReason, envChip, onLaunch, onClearSelection,
}: OcrLaunchBarProps) {
  const { t } = useTranslation('ocr');
  const navigate = useNavigate();

  const recap = selectedCount > 0
    ? [
        t('launchBar.selected', { count: selectedCount }),
        segModelName && t('launchBar.seg', { name: segModelName }),
        ocrModelName && t('launchBar.ocr', { name: ocrModelName }),
        estimateSeconds != null && fmtEstimate(estimateSeconds),
      ].filter(Boolean).join(' · ')
    : t('launchBar.noneSelected');

  const launchButton = (
    <Button
      variant="contained"
      color="success"
      onClick={onLaunch}
      disabled={!canLaunch}
    >
      {launching ? t('launchBar.adding') : (selectedCount > 0 ? t('launchBar.launchCount', { count: selectedCount }) : t('launchBar.launch'))}
    </Button>
  );

  return (
    <Box sx={{
      pt: 1.5, flexShrink: 0,
      borderTop: 1, borderColor: 'divider',
      display: 'flex', alignItems: 'center', gap: 2,
    }}>
      <Tooltip
        title={estimateSeconds != null ? t('launchBar.estimateTooltip') : ''}
        arrow
      >
        <Typography variant="body2" color="text.secondary" sx={{ mr: 'auto' }}>
          {recap}
        </Typography>
      </Tooltip>

      {runningOcr && (
        <Tooltip title={t('launchBar.runningTooltip')} arrow>
          <Box
            onClick={() => navigate('/tasks')}
            sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer' }}
          >
            <LinearProgress
              variant={runningOcr.total > 0 ? 'determinate' : 'indeterminate'}
              value={runningOcr.total > 0
                ? Math.min(100, ((runningOcr.processed + runningOcr.failed) / runningOcr.total) * 100)
                : undefined}
              sx={{ width: 160, height: 6, borderRadius: 3 }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
              {fmtNum(runningOcr.processed + runningOcr.failed)} / {fmtNum(runningOcr.total)}
            </Typography>
          </Box>
        </Tooltip>
      )}

      {selectedCount > 0 && (
        <Button variant="outlined" size="small" onClick={onClearSelection}>
          {t('launchBar.clearSelection')}
        </Button>
      )}

      {envChip}

      {!canLaunch && disabledReason
        ? <Tooltip title={disabledReason} arrow><span>{launchButton}</span></Tooltip>
        : launchButton}
    </Box>
  );
}
