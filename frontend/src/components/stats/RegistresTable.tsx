import { useState } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import { CorpusStatsResponse } from '../../types';
import { StatsCard, ShowAllRow, ROW_LIMIT, fmt } from './StatsCard';

// Affiche une période ; si les deux bornes sont identiques, on n'affiche que l'année.
function formatPeriode(periode?: string[] | null): string {
  if (!periode || (!periode[0] && !periode[1])) return '—';
  const [start, end] = periode;
  if (start && end && start === end) return start;
  return `${start || '…'} – ${end || '…'}`;
}

// ─── Tableau des registres les plus riches (replié à 5 lignes, dépliable) ─────

export default function RegistresTable({ data, error }: { data: CorpusStatsResponse | null; error?: string | null }) {
  const { t } = useTranslation('stats');
  const [expanded, setExpanded] = useState(false);
  const registres = data?.registres ?? [];
  const visible = expanded ? registres : registres.slice(0, ROW_LIMIT);

  return (
    <StatsCard
      title={t('richest.title')}
      subtitle={t('richest.subtitle')}
      error={error}
    >
      {data && (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: 'grey.50' }}>
                <TableCell sx={{ fontWeight: 600 }}>{t('richest.registre')}</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>{t('richest.period')}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>{t('richest.pages')}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>{t('richest.occurrences')}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>{t('richest.uniqueWords')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map(reg => (
                <TableRow key={reg.folder} hover>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{reg.folder}</TableCell>
                  <TableCell sx={{ color: 'text.secondary', fontSize: '0.8rem' }}>
                    {formatPeriode(reg.periode)}
                  </TableCell>
                  <TableCell align="right">{fmt(reg.pages)}</TableCell>
                  <TableCell align="right">{fmt(reg.occurrences)}</TableCell>
                  <TableCell align="right">{fmt(reg.unique_words)}</TableCell>
                </TableRow>
              ))}
              <ShowAllRow colSpan={5} total={registres.length} expanded={expanded} onToggle={() => setExpanded(e => !e)} />
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </StatsCard>
  );
}
