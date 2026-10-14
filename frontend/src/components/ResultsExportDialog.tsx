import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { indexesApi, ResultsExportParams, ResultsExportProgress } from '../api/indexes';

interface Props {
  indexId: string;
  indexName: string;
  format: 'csv' | 'xlsx';
  params: ResultsExportParams;
  /** Fin de l'export : téléchargé, annulé (sans argument), ou en échec (message). */
  onClose: (error?: string) => void;
}

/** Export CSV / Excel d'une recherche, dans une fenêtre qui bloque la page jusqu'à la fin.
 *
 * Le serveur relance la recherche puis inventorie les images registre par registre — près d'une
 * minute sur le partage pour une recherche large. Un lien de téléchargement direct n'en montrait
 * rien, ni une erreur éventuelle : la fenêtre suit l'export en flux, étape par étape, puis
 * déclenche le téléchargement du fichier reçu. Montée avec une `key` par export : elle le lance
 * à l'ouverture et l'abandonne si on la ferme. */
export default function ResultsExportDialog({ indexId, indexName, format, params, onClose }: Props) {
  const { t, i18n } = useTranslation('search');
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const fmtN = (n: number) => n.toLocaleString(locale);
  const [progress, setProgress] = useState<ResultsExportProgress | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed((Date.now() - started) / 1000), 500);
    indexesApi.exportResultsStream(indexId, format, params, setProgress, controller.signal)
      .then(({ filename, blob }) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        onClose();
      })
      .catch((err: any) => {
        if (!controller.signal.aborted) onClose(err?.message || t('resultsExport.error'));
      })
      .finally(() => window.clearInterval(timer));
    return () => { controller.abort(); window.clearInterval(timer); };
    // Un export par montage : le parent change la `key` pour en lancer un autre.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cancel = () => {
    controllerRef.current?.abort();
    onClose();
  };

  const duration = (() => {
    const total = Math.max(0, Math.round(elapsed));
    return total >= 60
      ? t('zipExport.durationMinutes', { minutes: Math.floor(total / 60), seconds: String(total % 60).padStart(2, '0') })
      : t('zipExport.durationSeconds', { seconds: total });
  })();

  // Étape en cours : celles de la recherche, puis l'inventaire des images et l'écriture.
  const phaseLabel = !progress ? t('resultsExport.starting')
    : progress.phase === 'resolve' ? t('resultsExport.resolve')
    : progress.phase === 'write' ? t('resultsExport.write')
    : t(`progress.${progress.phase}`);
  const mb = (bytes: number) => (bytes / (1024 * 1024)).toLocaleString(locale, { maximumFractionDigits: 1 });
  const counter = !progress || progress.total <= 0 ? null
    : progress.phase === 'load' ? t('progress.megabytes', { current: mb(progress.current), total: mb(progress.total) })
    : progress.phase === 'resolve' ? t('resultsExport.registres', { current: fmtN(progress.current), total: fmtN(progress.total) })
    : t('progress.words', { current: fmtN(progress.current), total: fmtN(progress.total) });
  const percent = progress && progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : null;

  return (
    // Bloquante : ni Échap ni clic à côté ne la ferment ; seul « Annuler » l'interrompt.
    <Dialog open fullWidth maxWidth="sm" disableEscapeKeyDown onClose={() => {}}>
      <DialogTitle sx={{ pb: 0.5 }}>{t(`resultsExport.title_${format}`)}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" noWrap title={params.q} sx={{ mb: 2.5 }}>
          {t('zipExport.subtitle', { query: params.q, index: indexName })}
        </Typography>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 2, mb: 1 }}>
          <Typography variant="body2" noWrap>{phaseLabel}</Typography>
          {counter && (
            <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
              {counter}
            </Typography>
          )}
        </Box>
        {percent != null
          ? <LinearProgress variant="determinate" value={Math.min(100, percent)} sx={{ height: 6, borderRadius: 3 }} />
          : <LinearProgress sx={{ height: 6, borderRadius: 3 }} />}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1, fontVariantNumeric: 'tabular-nums' }}>
          {t('zipExport.elapsed', { duration })}
        </Typography>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={cancel}>{t('zipExport.cancel')}</Button>
      </DialogActions>
    </Dialog>
  );
}
