import { useState } from 'react';
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  IconButton,
  InputLabel,
  ListSubheader,
  MenuItem,
  Select,
  Tooltip,
  Typography,
} from '@mui/material';
import {
  DeleteOutline as DeleteIcon,
  WarningAmber as WarningIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { SelectionStep, SelectionSummary } from '../../types';

// Libellé d'une étape de l'entonnoir : « cancer sans sein, poumon ».
const stepLabel = (step: SelectionStep, t: TFunction): string =>
  step.excluded_terms.length > 0
    ? t('selections.stepWithout', { query: step.query, words: step.excluded_terms.join(', ') })
    : step.query;

// Chaîne complète d'une sélection : « cancer › sein › métastase » (vide pour un classeur
// importé sans sa feuille d'étapes).
const chainLabel = (sel: SelectionSummary, t: TFunction): string =>
  sel.steps.map(s => stepLabel(s, t)).join(' › ');

interface Props {
  selections: SelectionSummary[];
  value: string;                                   // '' = tout l'index
  onChange: (selectionId: string) => void;
  onDelete: (selection: SelectionSummary) => Promise<void>;
}

/** « Chercher dans : [Tout l'index ▾] » — la portée de la recherche : tout l'index, ou un
 * classeur Excel trié puis réimporté. Une entrée se supprime depuis le menu (confirmé : les
 * fichiers importés sont partagés par tous les postes). */
export default function SelectionScopeSelect({ selections, value, onChange, onDelete }: Props) {
  const { t, i18n } = useTranslation('search');
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const [open, setOpen] = useState(false);
  const [toDelete, setToDelete] = useState<SelectionSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const confirmDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(toDelete);
      setToDelete(null);
    } catch (err: any) {
      setDeleteError(err?.response?.data?.detail ?? err?.message ?? t('selections.deleteError'));
    } finally {
      setDeleting(false);
    }
  };

  const active = selections.find(s => s.id === value);

  return (
    <>
      {/* Sans fichier importé, il n'y aurait que « Tout l'index » à choisir : pas de menu. La
          fenêtre de suppression, elle, reste montée pour finir de retirer le dernier fichier. */}
      {selections.length > 0 && (
      <FormControl size="small" sx={{ width: { xs: '100%', sm: 280 }, flexShrink: 0 }}>
        <InputLabel id="search-scope-label" shrink>{t('selections.scope')}</InputLabel>
        <Select
          labelId="search-scope-label"
          label={t('selections.scope')}
          value={active ? value : ''}
          displayEmpty
          notched
          open={open}
          onOpen={() => setOpen(true)}
          onClose={() => setOpen(false)}
          onChange={e => onChange(e.target.value)}
          renderValue={val => {
            const sel = selections.find(s => s.id === val);
            return (
              <Typography variant="body2" noWrap fontWeight={sel ? 600 : 400} color={sel ? 'primary.main' : 'text.secondary'}>
                {sel ? sel.name : t('selections.wholeIndex')}
              </Typography>
            );
          }}
          MenuProps={{ slotProps: { paper: { sx: { maxWidth: 520 } } } }}
        >
          <MenuItem value="">
            <Typography variant="body2">{t('selections.wholeIndex')}</Typography>
          </MenuItem>
          {selections.length > 0 && <ListSubheader sx={{ lineHeight: '32px' }}>{t('selections.saved')}</ListSubheader>}
          {selections.map(sel => (
            <MenuItem key={sel.id} value={sel.id} sx={{ alignItems: 'flex-start', gap: 1 }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  <Typography variant="body2" fontWeight={500} noWrap>{sel.name}</Typography>
                  {sel.stale && (
                    <Tooltip title={t('selections.stale')}>
                      <WarningIcon color="warning" sx={{ fontSize: 16 }} />
                    </Tooltip>
                  )}
                </Box>
                <Typography variant="caption" color="text.secondary" component="div" noWrap>
                  {t('selections.pagesCount', { count: sel.count, val: sel.count.toLocaleString(locale) })}
                  {sel.steps.length > 0 && <>{' · '}{chainLabel(sel, t)}</>}
                </Typography>
              </Box>
              <Tooltip title={t('selections.delete')}>
                <IconButton
                  size="small"
                  aria-label={t('selections.delete')}
                  onClick={e => { e.stopPropagation(); setOpen(false); setDeleteError(null); setToDelete(sel); }}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      )}

      <Dialog open={!!toDelete} onClose={() => !deleting && setToDelete(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('selections.deleteTitle')}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {t('selections.deleteConfirm', { name: toDelete?.name ?? '' })}
          </DialogContentText>
          {deleteError && <Typography variant="body2" color="error" sx={{ mt: 1.5 }}>{deleteError}</Typography>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setToDelete(null)} disabled={deleting}>{t('selections.cancel')}</Button>
          <Button onClick={confirmDelete} color="error" variant="contained" disableElevation disabled={deleting}
            startIcon={deleting ? <CircularProgress size={16} color="inherit" /> : <DeleteIcon />}>
            {t('selections.delete')}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
