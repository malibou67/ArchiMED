import { Box } from '@mui/material';
import { indexesApi } from '../../api/indexes';
import Loader from '../Loader';
import { StatsCard, useStatsData } from './StatsCard';
import CorpusKpiBand from './CorpusKpiBand';
import CorpusStatsCard from './CorpusStatsCard';
import RegistresTable from './RegistresTable';
import TermTrendCard from './TermTrendCard';
import QualityCard from './QualityCard';
import EmptyPagesCard from './EmptyPagesCard';

const TOP_WORDS = 15;

// ─── Dashboard global d'un index (onglet « Statistiques » du détail d'index) ──
// Hiérarchie descendante : chiffres clés → graphiques → outils → tableaux (en bas).

export default function IndexStatsDashboard({ indexId }: { indexId: string }) {
  const corpus = useStatsData(indexId, id => indexesApi.getCorpusStats(id, TOP_WORDS));
  const quality = useStatsData(indexId, id => indexesApi.getQualityStats(id));

  // Loader commun : on attend les deux jeux de statistiques (ou une erreur) avant
  // d'afficher les cartes, plutôt qu'un loader par carte.
  const corpusReady = corpus.data || corpus.error;
  const qualityReady = quality.data || quality.error;
  if (!corpusReady || !qualityReady) return <Loader />;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
      {/* ── Chiffres clés + graphiques du corpus (l'erreur corpus est affichée une seule fois) ── */}
      {corpus.error ? (
        <StatsCard error={corpus.error} />
      ) : corpus.data && (
        <>
          <CorpusKpiBand totals={corpus.data.totals} />
          <CorpusStatsCard data={corpus.data} />
        </>
      )}

      {/* ── Outils interactifs (pleine largeur) ── */}
      <TermTrendCard indexId={indexId} />
      <QualityCard data={quality.data} error={quality.error} />

      {/* ── Tableaux détaillés (en bas, repliés à 5 lignes) ── */}
      {!corpus.error && corpus.data && <RegistresTable data={corpus.data} />}
      <EmptyPagesCard data={quality.data} error={quality.error} />
    </Box>
  );
}
