import { Backdrop, Button, CircularProgress, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { PendingTaskAction } from './usePendingTaskAction';

/**
 * Overlay bloquant affiché pendant qu'une action de tâche prend effet.
 *
 * Pause et arrêt sont **coopératifs** : le backend pose un drapeau, et le runner ne s'arrête qu'à
 * la fin de la page (OCR) ou du registre (index) en cours. Plusieurs secondes peuvent donc passer
 * entre le clic et le changement de statut — sans overlay, l'écran paraît inerte et l'utilisateur
 * reclique.
 *
 * L'overlay se ferme de lui-même dès que l'action a produit son effet (la page hôte surveille
 * `pending` et appelle `done()`), et « Masquer » rend la main sans rien annuler : une pause
 * d'indexation peut attendre la fin d'un gros registre, on ne peut pas immobiliser l'interface
 * aussi longtemps sans échappatoire. Les indicateurs de ligne (spinner à la place de l'icône)
 * prennent alors le relais.
 */
interface Props {
  pending: PendingTaskAction | null;
  open: boolean;
  onHide: () => void;
}

export default function TaskActionOverlay({ pending, open, onHide }: Props) {
  const { t } = useTranslation('tasks');
  if (!pending) return null;

  const détailParDéfaut = () => {
    // Une commande partie vers un autre poste n'y sera lue qu'à son prochain relevé : c'est cette
    // attente-là qu'il faut expliquer, pas la fin de la page en cours.
    if (pending.machine) return t('actions.requestSent', { machine: pending.machine });
    switch (pending.kind) {
      case 'pause':
        return t(`overlay.pauseDetail.${pending.taskType ?? 'generic'}`, {
          defaultValue: t('overlay.pauseDetail.generic'),
        });
      case 'cancel':
        return t('overlay.cancelDetail');
      case 'resume':
        return t('overlay.resumeDetail');
      default:
        return '';
    }
  };

  const titre = pending.title ?? t(`overlay.${pending.kind}`);
  const détail = pending.detail ?? détailParDéfaut();

  return (
    <Backdrop
      open={open}
      sx={{ zIndex: (theme) => theme.zIndex.modal + 1, color: '#fff', flexDirection: 'column', gap: 2 }}
    >
      <CircularProgress color="inherit" />
      <Typography variant="h6">{titre}</Typography>
      {détail && (
        <Typography variant="body2" sx={{ opacity: 0.85, textAlign: 'center', px: 2, maxWidth: 460 }}>
          {détail}
        </Typography>
      )}
      <Button onClick={onHide} color="inherit" variant="outlined" size="small">
        {t('overlay.hide')}
      </Button>
    </Backdrop>
  );
}
