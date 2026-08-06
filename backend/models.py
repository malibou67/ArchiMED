from pydantic import BaseModel, ConfigDict
from typing import List, Literal, Optional, Dict, Any

class ModelMetadata(BaseModel):
    id: str
    name: str
    type: Literal['ocr', 'segmentation'] = 'ocr'
    description: Optional[str] = None
    version: Optional[str] = None
    created_at: Optional[str] = None
    trained_on: Optional[str] = None
    accuracy: Optional[float] = None
    file_path: Optional[str] = None

class OcrModelStatus(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    pages_done: int = 0
    pages_total: int = 0
    last_updated: Optional[str] = None

class RegistreSummary(BaseModel):
    id: str
    titre: str
    periode: List[str]
    folder_name: str
    pages_count: int = 0
    pages_pattern: Optional[str] = None
    pages_start: Optional[int] = None
    pages_end: Optional[int] = None
    pages_gaps: Optional[List[int]] = None
    pages_duplicates: Optional[List[int]] = None
    extra_pages: Optional[List[str]] = None
    extra_pagination: Optional[Dict[str, Any]] = None
    ocr_status: Optional[Dict[str, OcrModelStatus]] = None
    # Anomalies persistées au dernier sync (registre_vide, pagination_indetectable…).
    anomalies: Optional[List[str]] = None

class CollectionMetadata(BaseModel):
    id: str
    type: str
    titre: str
    periode: List[str]
    lieu: str
    commentaire: Optional[str] = None
    folder_name: Optional[str] = None
    registres: Optional[List[RegistreSummary]] = None
    # Anomalies de niveau collection persistées au dernier sync (ocr_orphelin…).
    anomalies: List[str] = []

class RegistreMetadata(BaseModel):
    id: str
    titre: str
    periode: List[str]
    pagination: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None
    folder_name: Optional[str] = None
    collection_id: Optional[str] = None

class TranscriptionInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    collection_id: str
    registre_id: str
    model_name: str
    file_name: str
    file_path: str
    has_content: bool

class CollectionCreate(BaseModel):
    type: str
    titre: str
    periode: List[str]
    lieu: str
    commentaire: Optional[str] = None

class CollectionUpdate(BaseModel):
    type: Optional[str] = None
    titre: Optional[str] = None
    periode: Optional[List[str]] = None
    lieu: Optional[str] = None
    commentaire: Optional[str] = None

class RegistreCreate(BaseModel):
    titre: str
    periode: List[str]
    pagination: Optional[Dict[str, Any]] = None

class RegistreUpdate(BaseModel):
    titre: Optional[str] = None
    periode: Optional[List[str]] = None
    pagination: Optional[Dict[str, Any]] = None
    extra_pagination: Optional[Dict[str, Any]] = None

class IndexStats(BaseModel):
    total_unique_words: int = 0
    total_word_occurrences: int = 0
    total_pages: Optional[int] = None
    registres_count: int = 0
    year_min: Optional[int] = None
    year_max: Optional[int] = None

class IndexProgress(BaseModel):
    processed: int = 0
    total: int = 0
    current_registre: Optional[str] = None
    current_page: Optional[str] = None   # page (XML) en cours de lecture

class IndexSource(BaseModel):
    """Une brique d'un index : un modèle OCR d'une collection."""
    model_config = ConfigDict(protected_namespaces=())

    collection_id: str
    model_name: str

class IndexSourceInfo(BaseModel):
    """Source résolue et persistée dans l'index (avec sa clé de namespace)."""
    model_config = ConfigDict(protected_namespaces=())

    key: str                       # préfixe de namespace des pages (s0, s1, …)
    collection_id: str
    collection_folder: str
    collection_titre: Optional[str] = None
    model_name: str

class IndexBuild(BaseModel):
    """État d'une (re)génération en cours. Présent tant qu'une construction tourne ;
    permet de garder l'index précédent consultable pendant une reconstruction."""
    status: str = "generating"     # "generating", "error"
    progress: Optional[IndexProgress] = None
    error: Optional[str] = None

class IndexMetadata(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    name: Optional[str] = None
    sources: Optional[List[IndexSourceInfo]] = None
    # Legacy : anciens index mono-source (avant le multi-sources).
    collection_id: Optional[str] = None
    model_name: Optional[str] = None
    status: str = "ready"  # "ready", "generating", "error"
    created_at: Optional[str] = None
    stats: Optional[IndexStats] = None
    progress: Optional[IndexProgress] = None
    build: Optional[IndexBuild] = None

class IndexSourceCoverage(BaseModel):
    """Couverture d'une source d'un index : pages indexées vs pages OCR disponibles."""
    model_config = ConfigDict(protected_namespaces=())

    key: Optional[str] = None      # None pour un ancien index mono-source
    collection_folder: str
    collection_titre: Optional[str] = None
    model_name: str
    resolved: bool = True          # False : collection introuvable ou metadata illisible
    indexed_pages: Optional[int] = None
    ocr_pages: Optional[int] = None
    new_registres: int = 0
    new_pages: int = 0

class IndexUpdates(BaseModel):
    """Fraîcheur et couverture d'un index prêt (cf. GET /api/indexes/updates)."""
    id: str
    new_registres: int = 0
    new_pages: int = 0
    coverage_known: bool = True    # False : index construit avant le suivi de couverture
    indexed_pages: Optional[int] = None
    ocr_pages: Optional[int] = None
    stale_pages: int = 0           # pages indexées dont l'OCR n'existe plus
    rescanned: bool = False        # les compteurs viennent d'un scan disque, pas de l'ocr_status
    sources: List[IndexSourceCoverage] = []

class IndexCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    sources: List[IndexSource]

class IndexUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: Optional[str] = None
    sources: Optional[List[IndexSource]] = None

class IndexPreviewRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    sources: List[IndexSource]

class PaginationDiag(BaseModel):
    pattern: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    gaps: List[int] = []
    duplicates: List[int] = []
    extra_pages: List[str] = []

class RegistreScanStatus(BaseModel):
    folder_name: str
    is_known: bool
    has_metadata: bool
    has_ocr_folder: bool
    pages_count: int = 0
    ocr_status: Dict[str, OcrModelStatus] = {}
    anomalies: List[str] = []
    pagination: Optional[PaginationDiag] = None

class CollectionScanStatus(BaseModel):
    folder_name: str
    is_known: bool
    has_metadata: bool
    has_scans_folder: bool
    has_ocr_folder: bool
    anomalies: List[str] = []
    registres: List['RegistreScanStatus']

class ScanReport(BaseModel):
    collections: List[CollectionScanStatus]
    new_collections: int
    new_registres: int
    anomalies_count: int = 0
    # Identifie le parcours disque qui a produit ce rapport. Le client le représente à la
    # synchronisation, qui repart alors de ce qui a déjà été lu au lieu de tout reparcourir.
    token: str = ""

class CollectionStatsTotals(BaseModel):
    registres_count: int = 0
    pages_total: int = 0
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    ocr_models_count: int = 0

class CollectionOcrModelStat(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str
    pages_done: int = 0
    pages_total: int = 0
    coverage: float = 0.0  # pages_done / pages_total, 0..1

class CollectionYearPages(BaseModel):
    year: int
    pages: int

class CollectionTranscriptionModelStat(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str
    files: int = 0

class CollectionRegistreStat(BaseModel):
    folder_name: str
    titre: Optional[str] = None
    periode: Optional[List[str]] = None
    pages_count: int = 0
    ocr: Dict[str, CollectionOcrModelStat] = {}
    transcriptions: Dict[str, int] = {}

class CollectionStatsResponse(BaseModel):
    id: str
    titre: Optional[str] = None
    totals: CollectionStatsTotals
    ocr_by_model: List[CollectionOcrModelStat] = []
    pages_by_year: List[CollectionYearPages] = []
    transcriptions_by_model: List[CollectionTranscriptionModelStat] = []
    transcriptions_grand_total: int = 0
    registres: List[CollectionRegistreStat] = []
    registres_without_periode: List[str] = []

class WordSearchResponse(BaseModel):
    query: str
    results: Dict[str, List[str]]
    count: int

class SourceLabel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    collection_titre: Optional[str] = None
    collection_folder: Optional[str] = None
    model_name: Optional[str] = None

class PageSearchResult(BaseModel):
    page_name: str
    words: Dict[str, List[str]]  # mot trouvé -> liste d'occurrences (avec coordonnées)
    source: Optional[SourceLabel] = None   # collection/modèle d'origine (multi-sources)
    registre: Optional[str] = None         # folder du registre (sans préfixe de source)

class MultiSearchResponse(BaseModel):
    query: str
    terms: List[str]             # termes effectivement recherchés (stop words exclus)
    pages: List[PageSearchResult]
    count: int
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    fuzzy_threshold: Optional[int] = None
