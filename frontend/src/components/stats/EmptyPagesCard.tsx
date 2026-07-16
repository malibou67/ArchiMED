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
import { QualityStatsResponse } from '../../types';
import { StatsCard, ShowAllRow, ROW_LIMIT, fmt } from './StatsCard';

// Tableau des registres comportant des pages OCR sans texte indexé,
// présenté dans sa propre carte (extrait de « Qualité OCR & couverture »).

export default function EmptyPagesCard({ data, error }: { data: QualityStatsResponse | null; error?: string | null }) {
  const { t } = useTranslation('stats');
  const [expanded, setExpanded] = useState(false);
  const emptyRows = data?.per_registre.filter(r => r.empty_pages > 0) ?? [];
  if (data && emptyRows.length === 0) return null;

  const visible = expanded ? emptyRows : emptyRows.slice(0, ROW_LIMIT);

  return (
    <StatsCard
      title={t('emptyPages.title')}
      subtitle={t('emptyPages.subtitle')}
      error={error}
    >
      {data && (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: 'grey.50' }}>
                <TableCell sx={{ fontWeight: 600 }}>{t('emptyPages.registre')}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>{t('emptyPages.indexed')}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>{t('emptyPages.ocr')}</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>{t('emptyPages.empty')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visible.map(r => (
                <TableRow key={r.folder} hover>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{r.folder}</TableCell>
                  <TableCell align="right">{fmt(r.pages_indexed)}</TableCell>
                  <TableCell align="right">{fmt(r.pages_ocr)}</TableCell>
                  <TableCell align="right" sx={{ color: 'error.main', fontWeight: 600 }}>{fmt(r.empty_pages)}</TableCell>
                </TableRow>
              ))}
              <ShowAllRow colSpan={4} total={emptyRows.length} expanded={expanded} onToggle={() => setExpanded(e => !e)} />
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </StatsCard>
  );
}
