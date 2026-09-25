import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Typography,
} from '@mui/material';
import {
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  ErrorOutline as ErrorOutlineIcon,
  RadioButtonUnchecked as PendingIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { indexesApi } from '../api/indexes';
import { PagesExportStatus } from '../types';

// Cadence du suivi, et seuils au-delà desquels on prévient plutôt que de laisser tourner.
const POLL_MS = 800;
const START_GRACE_MS = 15_000;      // 404 tolérés au départ : la requête de téléchargement est en route
const UNREACHABLE_AFTER = 3;        // sondages consécutifs en échec avant d'annoncer un serveur muet
const IDLE_WARNING_S = 30;          // sans avancée depuis… (hors analyse de l'index, d'un seul tenant)
const STUCK_CANCEL_MS = 10_000;     // annulation non confirmée : on laisse fermer quand même
const ESCAPE_UNREACHABLE_MS = 30_000;

const TERMINAL = ['done', 'error', 'cancelled'];

type Props = {
  indexId: string;
  indexName: string;
  query: string;
  token: string;
  startedAt: number;
  onClose: () => void;
};

type StepState = 'pending' | 'active' | 'done' | 'failed';

/**
 * Suivi d'un export ZIP des pages. Le téléchargement est natif (le navigateur écrit l'archive
 * sur disque au fil de l'eau) : la page n'en voit rien passer, elle interroge donc le serveur,
 * qui suit l'export sous le jeton joint à la requête. La fenêtre est modale et ne se ferme qu'à
 * la fin de l'export — sauf filet de sécurité quand le serveur ne répond plus.
 */
export default function PagesExportDialog({ indexId, indexName, query, token, startedAt, onClose }: Props) {
  const { t, i18n } = useTranslation('search');
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const [status, setStatus] = useState<PagesExportStatus | null>(null);
  const [lost, setLost] = useState(false);
  const [failures, setFailures] = useState(0);
  const [failingSince, setFailingSince] = useState<number | null>(null);
  const [cancelAskedAt, setCancelAskedAt] = useState<number | null>(null);
  const [showList, setShowList] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const seen = useRef(false);

  const finished = status != null && TERMINAL.includes(status.status);
  const over = finished || lost;
  const unreachableFor = failingSince != null ? now - failingSince : 0;
  const canClose = over
    || (cancelAskedAt != null && now - cancelAskedAt > STUCK_CANCEL_MS)
    || unreachableFor > ESCAPE_UNREACHABLE_MS;

  // Sondage de l'état, jusqu'à une fin d'export ou un export perdu.
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await indexesApi.getPagesExportStatus(indexId, token);
        if (stopped) return;
        seen.current = true;
        setStatus(next);
        setFailures(0);
        setFailingSince(null);
        if (TERMINAL.includes(next.status)) return;
      } catch (err) {
        if (stopped) return;
        if ((err as { response?: { status?: number } })?.response?.status === 404) {
          // Inconnu : normal tant que la requête de téléchargement n'est pas arrivée ; ensuite,
          // ou s'il a déjà été vu, le serveur l'a perdu (redémarrage).
          if (seen.current || Date.now() - startedAt > START_GRACE_MS) {
            setLost(true);
            return;
          }
        } else {
          setFailures(n => n + 1);
          setFailingSince(prev => prev ?? Date.now());
        }
      }
      timer = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [indexId, token, startedAt]);

  // Horloge d'affichage (temps écoulé, silence du serveur) tant que l'export tourne.
  useEffect(() => {
    if (over) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [over]);

  // Quitter ou recharger la page pendant la préparation annulerait l'export : on prévient.
  useEffect(() => {
    if (over) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [over]);

  const handleCancel = async () => {
    setCancelAskedAt(Date.now());
    try {
      setStatus(await indexesApi.cancelPagesExport(indexId, token));
    } catch {
      // Le sondage dira la suite (ou constatera un serveur injoignable).
    }
  };

  // ── Mise en forme ──
  const fmtN = (n: number) => n.toLocaleString(locale);
  const fmtBytes = (n: number) => {
    const mb = 1024 * 1024;
    if (n >= 1024 * mb) {
      return t('zipExport.sizeGB', { val: (n / (1024 * mb)).toLocaleString(locale, { maximumFractionDigits: 1 }) });
    }
    return t('zipExport.sizeMB', { val: (n / mb).toLocaleString(locale, { maximumFractionDigits: n < 10 * mb ? 1 : 0 }) });
  };
  const fmtDuration = (seconds: number) => {
    const total = Math.max(0, Math.round(seconds));
    if (total >= 60) {
      return t('zipExport.durationMinutes', { minutes: Math.floor(total / 60), seconds: String(total % 60).padStart(2, '0') });
    }
    return t('zipExport.durationSeconds', { seconds: total });
  };
  const counted = (key: string, n: number) => t(key, { count: n, val: fmtN(n) });

  // ── Étapes ──
  const phase = status?.phase ?? null;
  const current = phase === 'zip' ? 2 : phase === 'resolve' ? 1 : 0;
  const stepState = (i: number): StepState => {
    if (status?.status === 'done' || i < current) return 'done';
    if (i > current) return 'pending';
    return over ? 'failed' : 'active';
  };
  const stepSummary = (i: number): string | null => {
    if (!status || stepState(i) !== 'done') return null;
    if (i === 0) return status.pages != null ? counted('zipExport.pagesFound', status.pages) : null;
    if (i === 1) {
      const parts = [
        counted('zipExport.registresCount', status.registres ?? 0),
        counted('zipExport.imagesCount', status.files_total),
        fmtBytes(status.bytes_total),
      ];
      return parts.join(' · ');
    }
    return `${counted('zipExport.imagesCount', status.files_written)} · ${fmtBytes(status.bytes_written)}`;
  };
  const steps = [t('zipExport.steps.search'), t('zipExport.steps.resolve'), t('zipExport.steps.zip')];

  // ── Activité en cours : libellé, compteur, barre ──
  let label = t('zipExport.starting');
  let counter: string | null = null;
  let percent: number | null = null;
  if (status && !over) {
    if (phase === 'zip') {
      label = t('zipExport.filesProgress', { current: fmtN(status.files_done), total: fmtN(status.files_total) });
      counter = t('zipExport.bytesProgress', { current: fmtBytes(status.bytes_done), total: fmtBytes(status.bytes_total) });
      percent = status.bytes_total > 0 ? (status.bytes_done / status.bytes_total) * 100
        : status.files_total > 0 ? (status.files_done / status.files_total) * 100 : null;
    } else if (phase === 'resolve') {
      label = t('zipExport.registreProgress', { current: fmtN(status.current), total: fmtN(status.total) });
      percent = status.total > 0 ? (status.current / status.total) * 100 : null;
    } else if (phase) {
      label = t(`progress.${phase}`);
      if (status.total > 0) {
        counter = phase === 'load'
          ? t('progress.megabytes', {
              current: (status.current / (1024 * 1024)).toLocaleString(locale, { maximumFractionDigits: 0 }),
              total: (status.total / (1024 * 1024)).toLocaleString(locale, { maximumFractionDigits: 0 }),
            })
          : t('progress.words', { current: fmtN(status.current), total: fmtN(status.total) });
        percent = (status.current / status.total) * 100;
      }
    }
  }
  const idle = !over && status != null && phase !== 'parse' && status.idle_s >= IDLE_WARNING_S;
  const unreachable = !over && failures >= UNREACHABLE_AFTER;
  const anomalies = status ? status.missing_count + status.unreadable_count : 0;

  // Débit moyen depuis le début de l'envoi, et temps restant qu'il laisse prévoir — tus dès
  // que l'export cale ou que le serveur se tait : ils ne décriraient plus rien de réel.
  const rate = status && phase === 'zip' && !idle && !unreachable && status.zip_elapsed_s >= 2 && status.bytes_done > 0
    ? status.bytes_done / status.zip_elapsed_s : null;
  const eta = rate && status ? (status.bytes_total - status.bytes_done) / rate : null;
  // Compté ici depuis le clic, pas relu au serveur : il doit continuer d'avancer quand le
  // serveur ne répond plus, sinon il se fige à côté de l'alerte qui le signale.
  const elapsed = (now - startedAt) / 1000;
  const details = [
    rate != null ? t('zipExport.rate', { size: fmtBytes(rate) }) : null,
    eta != null ? t('zipExport.eta', { duration: fmtDuration(eta) }) : null,
    t('zipExport.elapsed', { duration: fmtDuration(elapsed) }),
  ].filter(Boolean).join(' · ');

  return (
    <Dialog
      open
      fullWidth
      maxWidth="sm"
      disableEscapeKeyDown={!canClose}
      onClose={() => { if (canClose) onClose(); }}
    >
      <DialogTitle sx={{ pb: 0.5 }}>{t('zipExport.title')}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" noWrap title={query} sx={{ mb: 2 }}>
          {t('zipExport.subtitle', { query, index: indexName })}
        </Typography>

        {/* Étapes : faite (✓ et bilan), en cours, à venir, ou arrêtée là */}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mb: 2 }}>
          {steps.map((stepLabel, i) => {
            const state = stepState(i);
            const summary = stepSummary(i);
            return (
              <Box key={stepLabel} sx={{ display: 'flex', alignItems: 'center', gap: 1.25 }}>
                <Box sx={{ width: 20, display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
                  {state === 'done' && <CheckCircleIcon color="success" sx={{ fontSize: 20 }} />}
                  {state === 'active' && <CircularProgress size={16} thickness={5} />}
                  {state === 'pending' && <PendingIcon sx={{ fontSize: 20, color: 'text.disabled' }} />}
                  {state === 'failed' && (status?.status === 'cancelled'
                    ? <CancelIcon sx={{ fontSize: 20, color: 'text.secondary' }} />
                    : <ErrorOutlineIcon color="error" sx={{ fontSize: 20 }} />)}
                </Box>
                <Typography
                  variant="body2"
                  sx={{ fontWeight: state === 'active' ? 600 : 400, color: state === 'pending' ? 'text.disabled' : 'text.primary' }}
                >
                  {`${i + 1}. ${stepLabel}`}
                </Typography>
                {summary && (
                  <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {summary}
                  </Typography>
                )}
              </Box>
            );
          })}
        </Box>

        {/* Activité en cours */}
        {!over && (
          <Box>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 2, mb: 0.75 }}>
              <Typography variant="body2" color="text.secondary" noWrap sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {label}
              </Typography>
              {counter && (
                <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                  {counter}
                </Typography>
              )}
            </Box>
            {percent != null
              ? <LinearProgress variant="determinate" value={Math.min(100, percent)} sx={{ height: 6, borderRadius: 3 }} />
              : <LinearProgress sx={{ height: 6, borderRadius: 3 }} />}
            <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 2, mt: 0.75 }}>
              <Typography
                variant="caption"
                color="text.secondary"
                noWrap
                title={status?.item ?? ''}
                sx={{ fontFamily: 'monospace', minWidth: 0 }}
              >
                {status?.item ?? ''}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                {details}
              </Typography>
            </Box>
          </Box>
        )}

        {/* Signaux d'alerte pendant l'export */}
        {idle && status && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            {t('zipExport.idle', { seconds: Math.round(status.idle_s) })}
          </Alert>
        )}
        {unreachable && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {t('zipExport.unreachable', { seconds: Math.round(unreachableFor / 1000) })}
          </Alert>
        )}

        {/* Fins possibles */}
        {status?.status === 'done' && (
          <Alert severity="success" sx={{ mt: 1 }}>
            {t('zipExport.done', {
              count: status.files_written,
              files: fmtN(status.files_written),
              size: fmtBytes(status.bytes_written),
              duration: fmtDuration(status.elapsed_s),
            })}
          </Alert>
        )}
        {status?.status === 'done' && anomalies > 0 && (
          <Alert
            severity="warning"
            sx={{ mt: 1.5 }}
            action={
              <Button color="inherit" size="small" onClick={() => setShowList(v => !v)}>
                {showList ? t('zipExport.hideList') : t('zipExport.showList')}
              </Button>
            }
          >
            {status.missing_count > 0 && <div>{counted('zipExport.missing', status.missing_count)}</div>}
            {status.unreadable_count > 0 && <div>{counted('zipExport.unreadable', status.unreadable_count)}</div>}
            <Collapse in={showList}>
              <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5, maxHeight: 180, overflowY: 'auto', fontFamily: 'monospace', fontSize: '0.78rem' }}>
                {[...status.missing, ...status.unreadable].map(name => <li key={name}>{name}</li>)}
              </Box>
              <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>{t('zipExport.reportNote')}</Typography>
            </Collapse>
          </Alert>
        )}
        {status?.status === 'error' && (
          <Alert severity="error" sx={{ mt: 1 }}>{t('zipExport.error', { detail: status.error ?? '' })}</Alert>
        )}
        {status?.status === 'cancelled' && (
          <Alert severity={status.cancel_reason === 'client' ? 'warning' : 'info'} sx={{ mt: 1 }}>
            {status.cancel_reason === 'client' ? t('zipExport.cancelledClient') : t('zipExport.cancelledUser')}
          </Alert>
        )}
        {lost && <Alert severity="error" sx={{ mt: 1 }}>{t('zipExport.lost')}</Alert>}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {over ? (
          <Button variant="contained" disableElevation onClick={onClose}>{t('zipExport.close')}</Button>
        ) : (
          <>
            {canClose && <Button onClick={onClose}>{t('zipExport.forceClose')}</Button>}
            <Button
              color="error"
              onClick={handleCancel}
              disabled={cancelAskedAt != null}
              startIcon={cancelAskedAt != null ? <CircularProgress size={14} color="inherit" /> : undefined}
            >
              {cancelAskedAt != null ? t('zipExport.cancelling') : t('zipExport.cancel')}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}
