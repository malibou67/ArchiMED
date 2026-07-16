export type ModelType = 'ocr' | 'segmentation';

export type ModelMetadata = {
  id: string;
  name: string;
  type: ModelType;
  description?: string;
  version?: string;
  created_at?: string;
  trained_on?: string;
  accuracy?: number | null; // null = effacer la précision (édition)
  file_path?: string;
  is_directory?: boolean;
}

export type RegistreSummary = {
  id: string;
  titre: string;
  periode: string[];
  folder_name: string;
  pages_count: number;
  pages_pattern?: string;
  pages_start?: number;
  pages_end?: number;
  pages_gaps?: number[];
  pages_duplicates?: number[];
  extra_pages?: string[];
  extra_pagination?: { pattern?: string };
  ocr_status?: Record<string, OcrModelStatus>;
  anomalies?: string[];  // anomalies du registre, persistées à la synchro
}

export type CollectionMetadata = {
  id: string;
  type: string;
  titre: string;
  periode: string[];
  lieu: string;
  commentaire?: string;
  folder_name?: string;
  registres?: RegistreSummary[];
  anomalies?: string[];  // anomalies de niveau collection, persistées à la synchro
}

export type CollectionCreate = {
  type: string;
  titre: string;
  periode: string[];
  lieu: string;
  commentaire?: string;
}

export type CollectionUpdate = {
  type?: string;
  titre?: string;
  periode?: string[];
  lieu?: string;
  commentaire?: string;
}

export type RegistreMetadata = {
  id: string;
  titre: string;
  periode: string[];
  pagination?: {
    pattern?: string;
    start?: number;
    end?: number;
  };
  stats?: {
    total_pages?: number;
    total_files?: number;
  };
  folder_name?: string;
  collection_id?: string;
}

export type RegistreCreate = {
  titre: string;
  periode: string[];
  pagination?: {
    pattern?: string;
    start?: number;
    end?: number;
  };
}

export type RegistreUpdate = {
  titre?: string;
  periode?: string[];
  pagination?: {
    pattern?: string;
    start?: number;
    end?: number;
  };
  extra_pagination?: {
    pattern?: string;
  };
}

export type TranscriptionInfo = {
  collection_id: string;
  registre_id: string;
  model_name: string;
  file_name: string;
  file_path: string;
  has_content: boolean;
}

export type TranscriptionsStats = {
  total: number;
  by_collection: Record<string, number>;
  by_model: Record<string, number>;
}

export type TranscriptionsSummaryRegistre = {
  registre_id: string;
  counts: Record<string, number>;  // model -> nb fichiers
}

export type TranscriptionsSummary = {
  collection_id: string;
  models: string[];
  registres: TranscriptionsSummaryRegistre[];
  totals: Record<string, number>;
  grand_total: number;
}

export type OcrModelStatus = {
  pages_done: number;
  pages_total: number;
  last_updated?: string | null;
}

export type PaginationDiag = {
  pattern: string | null;
  start: number | null;
  end: number | null;
  gaps: number[];
  duplicates: number[];
  extra_pages: string[];
}

export type RegistreScanStatus = {
  folder_name: string;
  is_known: boolean;
  has_metadata: boolean;
  has_ocr_folder: boolean;
  pages_count: number;
  ocr_status: Record<string, OcrModelStatus>;
  anomalies: string[];
  pagination: PaginationDiag | null;
}

export type CollectionScanStatus = {
  folder_name: string;
  is_known: boolean;
  has_metadata: boolean;
  has_scans_folder: boolean;
  has_ocr_folder: boolean;
  anomalies: string[];
  registres: RegistreScanStatus[];
}

export type ScanReport = {
  collections: CollectionScanStatus[];
  new_collections: number;
  new_registres: number;
  anomalies_count: number;
}

// Événement de progression émis par le flux d'analyse (une collection traitée).
export type ScanProgress = {
  current: number;
  total: number;
  name: string;
}

export type IndexStats = {
  total_unique_words: number;
  total_word_occurrences: number;
  total_pages?: number;
  registres_count: number;
  year_min?: number;
  year_max?: number;
}

export type IndexProgress = {
  processed: number;
  total: number;
  current_registre?: string;
}

// Une brique d'un index : un modèle OCR d'une collection.
export type IndexSource = {
  collection_id: string;
  model_name: string;
}

// Source résolue et persistée dans l'index (avec sa clé de namespace).
export type IndexSourceInfo = {
  key: string;
  collection_id: string;
  collection_folder: string;
  collection_titre?: string;
  model_name: string;
}

// Progression d'une (re)construction en cours (l'index reste consultable pendant).
export type IndexBuild = {
  status: 'generating' | 'error';
  progress?: IndexProgress;
  error?: string;
}

export type IndexMetadata = {
  id: string;
  name?: string;
  sources?: IndexSourceInfo[];
  // Legacy : anciens index mono-source.
  collection_id?: string;
  model_name?: string;
  status: 'ready' | 'generating' | 'error';
  created_at?: string;
  stats?: IndexStats;
  progress?: IndexProgress;
  build?: IndexBuild;
}

export type IndexCreate = {
  name: string;
  sources: IndexSource[];
}

export type IndexUpdate = {
  name?: string;
  sources?: IndexSource[];
}

export type IndexSourcePreview = {
  collection_id: string;
  collection_titre?: string;
  collection_folder: string;
  model_name: string;
  registres: number;
  pages: number;
}

export type IndexPreview = {
  sources: IndexSourcePreview[];
  totals: { registres: number; pages: number; sources: number };
  duplicate_registres: number;
  warnings: string[];
}

// Source d'origine d'une page de résultat (multi-sources).
export type ResultSource = {
  collection_titre?: string;
  collection_folder?: string;
  model_name?: string;
}

export type WordSearchResponse = {
  query: string;
  results: Record<string, string[]>;
  count: number;
}

export type PageSearchResult = {
  page_name: string;
  words: Record<string, string[]>;  // mot trouvé -> occurrences avec coords
  source?: ResultSource;             // collection/modèle d'origine (multi-sources)
  registre?: string;                 // folder du registre (sans préfixe de source)
}

export type MultiSearchResponse = {
  query: string;
  terms: string[];
  pages: PageSearchResult[];
  count: number;
  year_from?: number;
  year_to?: number;
  fuzzy_threshold?: number;
}

export type VocabularyEntry = {
  word: string;
  occurrences: number;
  pages: number;
}

export type VocabularyResponse = {
  total: number;
  total_unique_words: number;
  items: VocabularyEntry[];
}

export type WordPageEntry = {
  page_name: string;
  occurrences: string[];  // coordonnées "(x1, y1), (x2, y2)" par occurrence
  count: number;
}

export type WordPagesResponse = {
  word: string;
  page_count: number;
  total_occurrences: number;
  pages: WordPageEntry[];
}

// ── Statistiques de recherche (dashboard) ────────────────────────────────────

export type RegistreStatEntry = {
  folder: string;                    // clé registre (namespacée 'sX::folder' en multi-sources)
  titre?: string | null;
  collection?: string | null;        // titre de la collection d'origine (multi-sources)
  periode?: string[] | null;
  pages: number;
  occurrences: number;
  unique_words: number;
}

export type DecadePagesEntry = {
  decade: number;
  pages: number;
  registres: number;
}

export type CorpusStatsResponse = {
  totals: {
    total_unique_words: number;
    total_word_occurrences: number;
    total_pages: number;
    registres_count: number;
    year_min?: number | null;
    year_max?: number | null;
  };
  top_words: VocabularyEntry[];
  frequency_distribution: { bucket: string; words: number }[];
  registres: RegistreStatEntry[];
  pages_by_decade: DecadePagesEntry[];
}

export type TermFrequencyPoint = {
  decade: number;
  occurrences: number;
  pages: number;
  per_1000_pages: number | null;
}

export type TermFrequencyResponse = {
  terms: string[];
  decades: number[];
  series: { term: string; points: TermFrequencyPoint[] }[];
  fuzzy_threshold?: number | null;
  excluded_registres: number;
}

export type QualityStatsResponse = {
  short_words: { unique: number; occurrences: number; ratio_unique: number; ratio_occurrences: number };
  hapax: { unique: number; ratio_unique: number };
  pages: { indexed: number; ocr_total: number | null; empty: number | null; coverage_known: boolean };
  registres_without_periode: string[];
  per_registre: { folder: string; pages_indexed: number; pages_ocr: number; empty_pages: number }[];
}

// ── Statistiques d'une collection (avancement OCR / transcription) ────────────

export type CollectionOcrModelStat = {
  model: string;
  pages_done: number;
  pages_total: number;
  coverage: number;  // 0..1
}

export type CollectionRegistreStat = {
  folder_name: string;
  titre?: string | null;
  periode?: string[] | null;
  pages_count: number;
  ocr: Record<string, CollectionOcrModelStat>;
  transcriptions: Record<string, number>;  // model -> nb fichiers
}

export type YearPagesEntry = {
  year: number;
  pages: number;
}

export type CollectionStatsResponse = {
  id: string;
  titre?: string | null;
  totals: {
    registres_count: number;
    pages_total: number;
    year_min?: number | null;
    year_max?: number | null;
    ocr_models_count: number;
  };
  ocr_by_model: CollectionOcrModelStat[];
  pages_by_year: YearPagesEntry[];
  transcriptions_by_model: { model: string; files: number }[];
  transcriptions_grand_total: number;
  registres: CollectionRegistreStat[];
  registres_without_periode: string[];
}
