import { ReactNode } from 'react';
import { Box, Typography, alpha } from '@mui/material';
import {
  TextFields as TextFieldsIcon,
  Functions as FunctionsIcon,
  Description as DescriptionIcon,
  FolderOpen as FolderOpenIcon,
  DateRange as DateRangeIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { CorpusStatsResponse } from '../../types';
import { fmt } from './StatsCard';

// ─── Tuile de statistique chiffrée (bandeau de KPI moderne) ───────────────────

function StatTile({ icon, value, label, color }: { icon: ReactNode; value: string; label: string; color: string }) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        px: 1.5,
        py: 1.25,
        borderRadius: 2,
        border: '1px solid',
        borderColor: 'divider',
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          flexShrink: 0,
          width: 32,
          height: 32,
          borderRadius: '50%',
          display: 'grid',
          placeItems: 'center',
          bgcolor: theme => alpha(color, theme.palette.mode === 'dark' ? 0.25 : 0.14),
          color,
        }}
      >
        {icon}
      </Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="subtitle1" fontWeight={700} lineHeight={1.2} noWrap>{value}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap display="block" sx={{ fontSize: '0.72rem' }}>{label}</Typography>
      </Box>
    </Box>
  );
}

// ─── Bandeau horizontal des chiffres clés du corpus (en tête du dashboard) ────

export default function CorpusKpiBand({ totals }: { totals: CorpusStatsResponse['totals'] }) {
  const { t } = useTranslation('stats');
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 1.5,
        gridTemplateColumns: { xs: 'repeat(2, 1fr)', sm: 'repeat(3, 1fr)', md: 'repeat(5, 1fr)' },
      }}
    >
      <StatTile
        icon={<TextFieldsIcon fontSize="small" />}
        value={fmt(totals.total_unique_words)}
        label={t('kpi.uniqueWords')}
        color="#5c6bc0"
      />
      <StatTile
        icon={<FunctionsIcon fontSize="small" />}
        value={fmt(totals.total_word_occurrences)}
        label={t('kpi.occurrences')}
        color="#7e57c2"
      />
      <StatTile
        icon={<DescriptionIcon fontSize="small" />}
        value={fmt(totals.total_pages)}
        label={t('kpi.indexedPages')}
        color="#26a69a"
      />
      <StatTile
        icon={<FolderOpenIcon fontSize="small" />}
        value={fmt(totals.registres_count)}
        label={t('kpi.registres')}
        color="#ef6c00"
      />
      <StatTile
        icon={<DateRangeIcon fontSize="small" />}
        value={totals.year_min != null && totals.year_max != null
          ? `${totals.year_min} – ${totals.year_max}`
          : '—'}
        label={t('kpi.periodCovered')}
        color="#ec407a"
      />
    </Box>
  );
}
