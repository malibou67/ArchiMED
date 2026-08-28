import { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, CircularProgress, IconButton, LinearProgress, Tooltip, Typography } from '@mui/material';
import { Pause as PauseIcon, PlayArrow as PlayArrowIcon } from '@mui/icons-material';
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
  /** Registres distincts couverts par la sélection (unité de verrou côté backend). */
  selectedRegistreCount: number;
  segModelName: string | null;
  ocrModelName: string | null;
  estimateSeconds: number | null;
  /** Tâche OCR en cours **ou en pause** de ce poste (progression + contrôle inline). */
  activeOcr: Task | null;
  /** Pause demandée, pas encore effective (elle ne prend effet qu'entre deux pages). */
  pausingOcr: boolean;
  launching: boolean;
  canLaunch: boolean;
  disabledReason: string | null;
  envChip?: ReactNode;
  onLaunch: () => void;
  onClearSelection: () => void;
  onPauseOcr: () => void;
  onResumeOcr: () => void;
}

/** Barre de lancement : récapitulatif de la sélection, estimation de durée,
 * progression inline de la tâche OCR en cours (avec pause / reprise), bouton « Lancer ». */
export default function OcrLaunchBar({
  selectedCount, selectedRegistreCount, segModelName, ocrModelName, estimateSeconds,
  activeOcr, pausingOcr, launching, canLaunch, disabledReason, envChip,
  onLaunch, onClearSelection, onPauseOcr, onResumeOcr,
}: OcrLaunchBarProps) {
  const { t } = useTranslation('ocr');
  const navigate = useNavigate();

  const recap = selectedCount > 0
    ? [
        t('launchBar.selected', {
          count: selectedCount,
          registres: t('registres', { count: selectedRegistreCount }),
        }),
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

      {activeOcr && (() => {
        const paused = activeOcr.status === 'paused';
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Tooltip title={t('launchBar.runningTooltip')} arrow>
              <Box
                onClick={() => navigate('/tasks')}
                sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer' }}
              >
                <LinearProgress
                  variant={paused || activeOcr.total > 0 ? 'determinate' : 'indeterminate'}
                  value={activeOcr.total > 0
                    ? Math.min(100, ((activeOcr.processed + activeOcr.failed) / activeOcr.total) * 100)
                    : undefined}
                  color={paused ? 'warning' : 'primary'}
                  sx={{ width: 160, height: 6, borderRadius: 3 }}
                />
                <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
                  {fmtNum(activeOcr.processed + activeOcr.failed)} / {fmtNum(activeOcr.total)}
                </Typography>
              </Box>
            </Tooltip>
            {paused ? (
              <Tooltip title={t('launchBar.resume')} arrow>
                <IconButton size="small" color="primary" onClick={onResumeOcr}>
                  <PlayArrowIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            ) : pausingOcr ? (
              <Tooltip title={t('launchBar.pausing')} arrow>
                <span><IconButton size="small" disabled><CircularProgress size={14} /></IconButton></span>
              </Tooltip>
            ) : (
              <Tooltip title={t('launchBar.pause')} arrow>
                <IconButton size="small" color="primary" onClick={onPauseOcr}>
                  <PauseIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Box>
        );
      })()}

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
