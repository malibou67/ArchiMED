import api from './config';
import {
  IndexMetadata,
  IndexCreate,
  IndexUpdate,
  IndexSource,
  IndexUpdates,
  IndexPreview,
  MultiSearchResponse,
  VocabularyResponse,
  WordPagesResponse,
  CorpusStatsResponse,
  TermFrequencyResponse,
  QualityStatsResponse,
} from '../types';
import { Task } from './tasks';

export const indexesApi = {
  getAll: async (): Promise<IndexMetadata[]> => {
    const response = await api.get('/api/indexes/');
    return response.data;
  },

  // Couverture et fraîcheur de chaque index prêt. Par défaut les compteurs OCR viennent des
  // `ocr_status` publiés (quelques ms) ; `rescan` scanne les dossiers — exact même si des XML ont
  // été déposés hors de l'application, mais lent, d'où le timeout désactivé.
  getUpdates: async (rescan = false): Promise<IndexUpdates[]> => {
    const response = await api.get('/api/indexes/updates', {
      params: rescan ? { rescan: true } : undefined,
      timeout: 0,
    });
    return response.data;
  },

  get: async (indexId: string): Promise<IndexMetadata> => {
    const response = await api.get(`/api/indexes/${indexId}`);
    return response.data;
  },

  // Enfile une tâche d'indexation multi-sources ; renvoie la tâche (suivie dans les tâches).
  // `timeout: 0` : l'enfilage écrit sur le partage, qui peut être lent — le timeout par défaut
  // faisait annoncer un échec alors que la tâche était bel et bien partie.
  generate: async (data: IndexCreate): Promise<Task> => {
    const response = await api.post('/api/indexes/generate', data, { timeout: 0 });
    return response.data;
  },

  // Aperçu avant génération : registres/pages par source, totaux et avertissements.
  preview: async (sources: IndexSource[]): Promise<IndexPreview> => {
    const response = await api.post('/api/indexes/preview', { sources }, { timeout: 0 });
    return response.data;
  },

  // Édite un index (nom et/ou sources). Si les sources changent, une reconstruction est enfilée.
  update: async (indexId: string, data: IndexUpdate): Promise<IndexMetadata> => {
    const response = await api.patch(`/api/indexes/${indexId}`, data, { timeout: 0 });
    return response.data;
  },

  // Met à jour un index existant (mêmes sources) : seuls les registres nouveaux ou modifiés sont
  // réindexés, sauf `full` qui réindexe tout. L'ancien index reste consultable jusqu'au bout.
  regenerate: async (indexId: string, opts?: { full?: boolean }): Promise<Task> => {
    const response = await api.post(
      `/api/indexes/${indexId}/regenerate`,
      null,
      { timeout: 0, ...(opts?.full ? { params: { full: true } } : {}) },
    );
    return response.data;
  },

  // Abandonne une génération en cours en **conservant** l'index précédent s'il existe.
  // Repli de l'annulation quand plus aucune tâche ne correspond à l'index ; à ne pas confondre
  // avec `delete`, qui efface tout le dossier et reste réservé à la suppression explicite.
  abortBuild: async (indexId: string): Promise<{ outcome: 'cleared' | 'deleted' }> => {
    const response = await api.post(`/api/indexes/${indexId}/abort-build`, null, { timeout: 0 });
    return response.data;
  },

  search: async (indexId: string, query: string, yearFrom?: number, yearTo?: number, fuzzyThreshold?: number): Promise<MultiSearchResponse> => {
    const params: Record<string, unknown> = { q: query };
    if (yearFrom !== undefined) params.year_from = yearFrom;
    if (yearTo !== undefined) params.year_to = yearTo;
    if (fuzzyThreshold !== undefined && fuzzyThreshold < 100) params.fuzzy_threshold = fuzzyThreshold;
    const response = await api.get(`/api/indexes/${indexId}/search`, { params, timeout: 0 });
    return response.data;
  },

  getWords: async (
    indexId: string,
    params: {
      q?: string;
      sort?: 'word' | 'occurrences' | 'pages';
      direction?: 'asc' | 'desc';
      hide_stopwords?: boolean;
      min_occurrences?: number;
      offset?: number;
      limit?: number;
    } = {},
  ): Promise<VocabularyResponse> => {
    const response = await api.get(`/api/indexes/${indexId}/words`, { params, timeout: 0 });
    return response.data;
  },

  getWordPages: async (indexId: string, word: string): Promise<WordPagesResponse> => {
    const response = await api.get(`/api/indexes/${indexId}/words/${encodeURIComponent(word)}/pages`, { timeout: 0 });
    return response.data;
  },

  getWordsExportUrl: (
    indexId: string,
    params: {
      q?: string;
      sort?: 'word' | 'occurrences' | 'pages';
      direction?: 'asc' | 'desc';
      hide_stopwords?: boolean;
      min_occurrences?: number;
    } = {},
  ): string => {
    const qs = new URLSearchParams();
    if (params.q) qs.set('q', params.q);
    if (params.sort) qs.set('sort', params.sort);
    if (params.direction) qs.set('direction', params.direction);
    if (params.hide_stopwords) qs.set('hide_stopwords', 'true');
    if (params.min_occurrences && params.min_occurrences > 1) qs.set('min_occurrences', String(params.min_occurrences));
    const query = qs.toString();
    return `/api/indexes/${indexId}/words/export${query ? `?${query}` : ''}`;
  },

  delete: async (indexId: string): Promise<void> => {
    await api.delete(`/api/indexes/${indexId}`);
  },

  getAvailableModels: async (collectionId: string): Promise<string[]> => {
    const response = await api.get('/api/indexes/available-models', { params: { collection_id: collectionId } });
    return response.data;
  },

  getYearRange: async (indexId: string): Promise<{ year_min: number; year_max: number } | null> => {
    try {
      const response = await api.get(`/api/indexes/${indexId}/year-range`);
      return response.data;
    } catch {
      return null;
    }
  },

  getPageImageUrl: (indexId: string, pageName: string): string => {
    return `/api/indexes/${indexId}/page-image/${pageName}`;
  },

  getCorpusStats: async (indexId: string, top = 20): Promise<CorpusStatsResponse> => {
    const response = await api.get(`/api/indexes/${indexId}/stats/corpus`, { params: { top }, timeout: 0 });
    return response.data;
  },

  getTermFrequency: async (indexId: string, terms: string[], fuzzyThreshold?: number): Promise<TermFrequencyResponse> => {
    const params: Record<string, unknown> = { q: terms.join(',') };
    if (fuzzyThreshold !== undefined && fuzzyThreshold < 100) params.fuzzy_threshold = fuzzyThreshold;
    const response = await api.get(`/api/indexes/${indexId}/stats/term-frequency`, { params, timeout: 0 });
    return response.data;
  },

  getQualityStats: async (indexId: string): Promise<QualityStatsResponse> => {
    const response = await api.get(`/api/indexes/${indexId}/stats/quality`, { timeout: 0 });
    return response.data;
  },

  getResultsExportUrl: (
    indexId: string,
    kind: 'csv' | 'zip',
    params: { q: string; year_from?: number; year_to?: number; fuzzy_threshold?: number; download_token?: string },
  ): string => {
    const qs = new URLSearchParams();
    qs.set('q', params.q);
    if (params.year_from != null) qs.set('year_from', String(params.year_from));
    if (params.year_to != null) qs.set('year_to', String(params.year_to));
    if (params.fuzzy_threshold != null && params.fuzzy_threshold < 100) qs.set('fuzzy_threshold', String(params.fuzzy_threshold));
    if (params.download_token) qs.set('download_token', params.download_token);
    const file = kind === 'csv' ? 'export-results.csv' : 'export-pages.zip';
    return `/api/indexes/${indexId}/${file}?${qs.toString()}`;
  },
};
