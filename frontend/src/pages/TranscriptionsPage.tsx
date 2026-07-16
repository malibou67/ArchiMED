import { useState, useEffect } from 'react';
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  Container,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { transcriptionsApi } from '../api/transcriptions';
import { TranscriptionsSummary } from '../types';

export function CollectionSection({ data }: { data: TranscriptionsSummary }) {
  const { t, i18n } = useTranslation('transcriptions');
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const [expanded, setExpanded] = useState(true);

  return (
    <Paper variant="outlined" sx={{ mb: 2 }}>
      {/* En-tête collection */}
      <Box
        sx={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          px: 2, py: 1.5, cursor: 'pointer', bgcolor: 'grey.50',
          borderRadius: expanded ? '4px 4px 0 0' : '4px',
        }}
        onClick={() => setExpanded(e => !e)}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Typography variant="subtitle1" fontWeight="bold">{data.collection_id}</Typography>
          <Chip label={t('registresCount', { count: data.registres.length })} size="small" variant="outlined" />
          <Chip label={t('filesCount', { count: data.grand_total, val: data.grand_total.toLocaleString(locale) })} size="small" color="primary" />
          {data.models.map(m => (
            <Chip
              key={m}
              label={t('modelCount', { model: m, val: (data.totals[m] ?? 0).toLocaleString(locale) })}
              size="small"
              color="info"
              variant="outlined"
            />
          ))}
        </Box>
        <IconButton size="small">
          {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        </IconButton>
      </Box>

      {/* Tableau des registres */}
      <Collapse in={expanded}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ bgcolor: 'grey.100' }}>
                <TableCell><strong>{t('table.registre')}</strong></TableCell>
                {data.models.map(m => (
                  <TableCell key={m} align="right"><strong>{m}</strong></TableCell>
                ))}
                <TableCell align="right"><strong>{t('table.total')}</strong></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.registres.map(reg => {
                const rowTotal = Object.values(reg.counts).reduce((s, n) => s + n, 0);
                return (
                  <TableRow key={reg.registre_id} hover>
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>
                      {reg.registre_id}
                    </TableCell>
                    {data.models.map(m => (
                      <TableCell key={m} align="right">
                        {reg.counts[m] != null ? (
                          <Typography variant="body2">{reg.counts[m].toLocaleString(locale)}</Typography>
                        ) : (
                          <Typography variant="body2" color="text.disabled">—</Typography>
                        )}
                      </TableCell>
                    ))}
                    <TableCell align="right">
                      <Typography variant="body2" fontWeight="medium">
                        {rowTotal.toLocaleString(locale)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                );
              })}
              {/* Ligne totaux */}
              <TableRow sx={{ bgcolor: 'grey.50' }}>
                <TableCell><strong>{t('table.total')}</strong></TableCell>
                {data.models.map(m => (
                  <TableCell key={m} align="right">
                    <strong>{(data.totals[m] ?? 0).toLocaleString(locale)}</strong>
                  </TableCell>
                ))}
                <TableCell align="right">
                  <strong>{data.grand_total.toLocaleString(locale)}</strong>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </TableContainer>
      </Collapse>
    </Paper>
  );
}

export default function TranscriptionsPage() {
  const { t, i18n } = useTranslation('transcriptions');
  const locale = i18n.language.startsWith('fr') ? 'fr-FR' : 'en-US';
  const [summary, setSummary] = useState<TranscriptionsSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    transcriptionsApi.getSummary()
      .then(setSummary)
      .catch(() => setError(t('error')))
      .finally(() => setLoading(false));
  }, [t]);

  const grandTotal = summary.reduce((s, c) => s + c.grand_total, 0);
  const allModels = [...new Set(summary.flatMap(c => c.models))].sort();

  if (loading) {
    return (
      <Container sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
        <CircularProgress />
      </Container>
    );
  }

  return (
    <Container maxWidth="lg">
      <Typography variant="h4" gutterBottom>{t('title')}</Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {/* Résumé global */}
      {summary.length > 0 && (
        <Box sx={{ display: 'flex', gap: 2, mb: 3, flexWrap: 'wrap' }}>
          <Chip label={t('summaryFiles', { count: grandTotal, val: grandTotal.toLocaleString(locale) })} color="primary" />
          <Chip label={t('collectionsCount', { count: summary.length })} variant="outlined" />
          {allModels.map(m => (
            <Chip
              key={m}
              label={t('modelCount', { model: m, val: summary.reduce((s, c) => s + (c.totals[m] ?? 0), 0).toLocaleString(locale) })}
              color="info"
              variant="outlined"
            />
          ))}
        </Box>
      )}

      {summary.length === 0 ? (
        <Alert severity="info">{t('empty')}</Alert>
      ) : (
        summary.map(col => <CollectionSection key={col.collection_id} data={col} />)
      )}
    </Container>
  );
}
