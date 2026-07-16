import { ReactNode, useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, Paper, TableCell, TableRow, Typography } from '@mui/material';
import { ExpandLess as ExpandLessIcon, ExpandMore as ExpandMoreIcon } from '@mui/icons-material';
import type { SxProps, Theme } from '@mui/material';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import Loader from '../Loader';

// ─── Carte du dashboard : chrome commun (titre, chargement, erreur) ──────────

export function StatsCard({
  title,
  subtitle,
  loading,
  error,
  children,
  sx,
}: {
  title?: string;
  subtitle?: string;
  loading?: boolean;
  error?: string | null;
  children?: ReactNode;
  sx?: SxProps<Theme>;
}) {
  return (
    <Paper variant="outlined" sx={{ borderRadius: 2, p: 2.5, ...sx }}>
      {title && <Typography variant="subtitle1" fontWeight={700}>{title}</Typography>}
      {subtitle && (
        <Typography variant="caption" color="text.secondary" display="block">{subtitle}</Typography>
      )}
      <Box sx={{ mt: title || subtitle ? 1.5 : 0 }}>
        {loading ? (
          <Loader />
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : (
          children
        )}
      </Box>
    </Paper>
  );
}

// ─── Petit indicateur chiffré (bandeau de KPI) ────────────────────────────────

export function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 110 }}>
      <Typography variant="h6" fontWeight={700} lineHeight={1.2}>{value}</Typography>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
    </Box>
  );
}

export const fmt = (n: number | null | undefined): string =>
  n == null ? '—' : n.toLocaleString(i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US');

// ─── Tableaux repliables : on affiche ROW_LIMIT lignes, le reste derrière « Afficher tout » ──

export const ROW_LIMIT = 5;

// Ligne de pied de tableau pour déplier / replier les lignes au-delà de ROW_LIMIT.
export function ShowAllRow({
  colSpan,
  total,
  expanded,
  onToggle,
}: {
  colSpan: number;
  total: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation('stats');
  if (total <= ROW_LIMIT) return null;
  return (
    <TableRow>
      <TableCell colSpan={colSpan} sx={{ p: 0, border: 0 }}>
        <Button
          fullWidth
          size="small"
          color="inherit"
          onClick={onToggle}
          endIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          sx={{ py: 1, color: 'text.secondary', fontWeight: 600, textTransform: 'none', borderRadius: 0 }}
        >
          {expanded ? t('showLess') : t('showAll', { count: total })}
        </Button>
      </TableCell>
    </TableRow>
  );
}

// ─── Hook de chargement par index : refetch quand l'index (ou refreshKey) change ──

export function useStatsData<T>(indexId: string, fetcher: (id: string) => Promise<T>, refreshKey = 0) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!indexId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    fetcherRef.current(indexId)
      .then(d => { if (!cancelled) setData(d); })
      .catch(err => { if (!cancelled) setError(err?.response?.data?.detail ?? i18n.t('stats:loadError')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [indexId, refreshKey]);

  return { data, loading, error };
}
