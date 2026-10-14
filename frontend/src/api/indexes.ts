import api from './config';
import { readNdjson } from './ndjson';
import {
  IndexMetadata,
  IndexCreate,
  IndexUpdate,
  IndexSource,
  IndexUpdates,
  IndexPreview,
  MultiSearchResponse,
  PagesExportStatus,
  SearchProgress,
  SelectionImport,
  SelectionSummary,
  VocabularyResponse,
  WordPagesResponse,
  CorpusStatsResponse,
  TermFrequencyResponse,
  QualityStatsResponse,
} from '../types';
import { Task } from './tasks';

// Filtres d'une recherche. Un filtre neutre (ressemblance à 100, aucun mot exclu…) n'est pas
// envoyé : l'URL reste celle d'une recherche simple.
export type SearchOptions = {
  yearFrom?: number;
  yearTo?: number;
  fuzzyThreshold?: number;
  wholeWord?: boolean;
  exclude?: string;   // mots à exclure, séparés par des espaces
  within?: string;    // id d'une sélection : ne chercher que dans ses pages
};

const searchParams = (query: string, o: SearchOptions): URLSearchParams => {
  const params = new URLSearchParams({ q: query });
  if (o.yearFrom !== undefined) params.set('year_from', String(o.yearFrom));
  if (o.yearTo !== undefined) params.set('year_to', String(o.yearTo));
  if (o.fuzzyThreshold !== undefined && o.fuzzyThreshold < 100) params.set('fuzzy_threshold', String(o.fuzzyThreshold));
  if (o.wholeWord) params.set('whole_word', 'true');
  if (o.exclude?.trim()) params.set('exclude', o.exclude.trim());
  if (o.within) params.set('within', o.within);
  return params;
};

// Paramètres d'un export de résultats : ceux de la recherche affichée, telle que le serveur l'a
// décrite (MultiSearchResponse), plus la langue du classeur et le jeton du ZIP.
export type ResultsExportParams = {
  q: string; year_from?: number; year_to?: number; fuzzy_threshold?: number; whole_word?: boolean;
  exclude?: string; within?: string; lang?: string; download_token?: string;
};

// Étape d'un export en flux : celles de la recherche (load/parse/scan/build), puis l'inventaire
// des images (`resolve`, compté en registres) et l'écriture du fichier (`write`).
export type ResultsExportProgress = {
  phase: 'load' | 'parse' | 'scan' | 'build' | 'resolve' | 'write';
  current: number;
  total: number;
};

const resultsExportQuery = (params: ResultsExportParams): URLSearchParams => {
  const qs = new URLSearchParams();
  qs.set('q', params.q);
  if (params.year_from != null) qs.set('year_from', String(params.year_from));
  if (params.year_to != null) qs.set('year_to', String(params.year_to));
  if (params.fuzzy_threshold != null && params.fuzzy_threshold < 100) qs.set('fuzzy_threshold', String(params.fuzzy_threshold));
  if (params.whole_word) qs.set('whole_word', 'true');
  if (params.exclude?.trim()) qs.set('exclude', params.exclude.trim());
  if (params.within) qs.set('within', params.within);
  if (params.lang) qs.set('lang', params.lang);   // langue du classeur Excel
  if (params.download_token) qs.set('download_token', params.download_token);
  return qs;
};

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

  search: async (indexId: string, query: string, options: SearchOptions = {}): Promise<MultiSearchResponse> => {
    const response = await api.get(`/api/indexes/${indexId}/search`, { params: searchParams(query, options), timeout: 0 });
    return response.data;
  },

  // Même recherche que search(), mais en flux NDJSON : onProgress est appelé à chaque
  // événement de progression (chargement de l'index puis parcours du vocabulaire), et la
  // promesse résout sur le résultat final. Sur un gros index, l'attente dépasse la minute.
  searchStream: async (
    indexId: string,
    query: string,
    options: SearchOptions,
    onProgress: (p: SearchProgress) => void,
  ): Promise<MultiSearchResponse> => {
    const params = searchParams(query, options);
    let result: MultiSearchResponse | null = null;
    await readNdjson(`/api/indexes/${indexId}/search/stream?${params}`, {}, (event) => {
      if (event.type === 'progress') {
        onProgress({ phase: event.phase, current: event.current, total: event.total });
      } else if (event.type === 'result') {
        result = event.result;
      }
    });
    if (!result) throw new Error('Résultat de recherche manquant');
    return result;
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
    kind: 'csv' | 'xlsx' | 'zip',
    params: ResultsExportParams,
  ): string => {
    const file = { csv: 'export-results.csv', xlsx: 'export-results.xlsx', zip: 'export-pages.zip' }[kind];
    return `/api/indexes/${indexId}/${file}?${resultsExportQuery(params).toString()}`;
  },

  // Export CSV / Excel en flux : onProgress suit la recherche, puis l'inventaire des images
  // registre par registre (l'étape longue sur le partage), puis l'écriture ; le fichier arrive
  // dans le dernier événement. Un lien de téléchargement direct ne montrait rien de cette
  // attente, ni une erreur éventuelle.
  exportResultsStream: async (
    indexId: string,
    format: 'csv' | 'xlsx',
    params: ResultsExportParams,
    onProgress: (p: ResultsExportProgress) => void,
    signal?: AbortSignal,
  ): Promise<{ filename: string; blob: Blob }> => {
    const qs = resultsExportQuery(params);
    qs.set('format', format);
    let file: { filename: string; blob: Blob } | null = null;
    await readNdjson(`/api/indexes/${indexId}/export-results/stream?${qs}`, { signal }, (event) => {
      if (event.type === 'progress') {
        onProgress({ phase: event.phase, current: event.current, total: event.total });
      } else if (event.type === 'file') {
        const bytes = Uint8Array.from(atob(event.data), c => c.charCodeAt(0));
        file = { filename: event.filename, blob: new Blob([bytes], { type: event.media_type }) };
      }
    });
    if (!file) throw new Error('Fichier d\'export manquant');
    return file;
  },

  // Sélections de pages (recherche en entonnoir), partagées par tous les postes. `timeout: 0` :
  // elles sont lues et écrites sur le partage, qui peut être lent.
  listSelections: async (indexId: string): Promise<SelectionSummary[]> => {
    const response = await api.get(`/api/indexes/${indexId}/selections`, { timeout: 0 });
    return response.data;
  },

  // Importe un classeur exporté en « Liste à trier (Excel) » puis trié : ses lignes à « oui »
  // deviennent une sélection.
  importSelection: async (indexId: string, file: File): Promise<SelectionImport> => {
    const form = new FormData();
    form.append('file', file);
    // L'instance axios envoie du JSON par défaut : sans cet en-tête, le fichier n'arrive pas.
    const response = await api.post(`/api/indexes/${indexId}/selections/import`, form, {
      timeout: 0, headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  deleteSelection: async (indexId: string, selectionId: string): Promise<void> => {
    await api.delete(`/api/indexes/${indexId}/selections/${selectionId}`, { timeout: 0 });
  },

  // Suivi d'un export ZIP lancé avec `download_token` : le téléchargement est natif, la page
  // n'en voit rien passer et interroge donc le serveur. Timeout court : un sondage qui traîne
  // est lui-même le signe d'un serveur en difficulté, et le suivant repartira.
  getPagesExportStatus: async (indexId: string, token: string): Promise<PagesExportStatus> => {
    const response = await api.get(`/api/indexes/${indexId}/export-pages/${token}`, { timeout: 5000 });
    return response.data;
  },

  cancelPagesExport: async (indexId: string, token: string): Promise<PagesExportStatus> => {
    const response = await api.delete(`/api/indexes/${indexId}/export-pages/${token}`, { timeout: 5000 });
    return response.data;
  },
};
