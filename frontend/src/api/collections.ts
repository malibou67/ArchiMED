import api from './config';
import { CollectionMetadata, CollectionUpdate, ScanReport, ScanProgress, CollectionStatsResponse, TranscriptionsSummary } from '../types';

// Lit un flux NDJSON (une ligne JSON par événement) et appelle onEvent pour chacun.
// Utilisé par les variantes en flux du scan et de la synchronisation.
async function readNdjson(
  url: string,
  init: RequestInit,
  onEvent: (event: any) => void,
): Promise<void> {
  const response = await fetch(url, init);
  if (!response.ok || !response.body) {
    throw new Error(`Échec de la requête (HTTP ${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const handleLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const event = JSON.parse(trimmed);
    if (event.type === 'error') throw new Error(event.detail || 'Erreur serveur');
    onEvent(event);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      handleLine(buffer.slice(0, nl));
      buffer = buffer.slice(nl + 1);
    }
  }
  handleLine(buffer); // dernière ligne éventuelle sans saut final
}

// Pas de création de collection via l'UI : on dépose le dossier dans data/collections,
// puis Scanner → Synchroniser crée le metadata.json (le POST backend reste utilisable en script).
export const collectionsApi = {
  getAll: async (): Promise<CollectionMetadata[]> => {
    const response = await api.get('/api/collections/');
    return response.data;
  },

  getById: async (id: string): Promise<CollectionMetadata> => {
    const response = await api.get(`/api/collections/${id}`);
    return response.data;
  },

  update: async (id: string, collection: CollectionUpdate): Promise<CollectionMetadata> => {
    const response = await api.put(`/api/collections/${id}`, collection);
    return response.data;
  },

  sync: async (id: string): Promise<CollectionMetadata> => {
    const response = await api.post(`/api/collections/${id}/sync`);
    return response.data;
  },

  // Même synchronisation que sync(), mais en flux NDJSON : onProgress est appelé à
  // chaque registre traité (avec le compteur reg_current/reg_total), et la promesse
  // résout sur le metadata final. Le back n'émet pas d'événement collection ici, donc
  // on amorce le nom depuis l'id.
  // `token` est celui du rapport d'analyse : il évite de relire le NAS.
  syncStream: async (id: string, token: string | undefined, onProgress: (p: ScanProgress) => void): Promise<CollectionMetadata | null> => {
    let cur: ScanProgress = { current: 1, total: 1, name: id };
    let result: CollectionMetadata | null = null;
    onProgress(cur);
    await readNdjson(`/api/collections/${id}/sync/stream${token ? `?token=${encodeURIComponent(token)}` : ''}`, { method: 'POST' }, (event) => {
      if (event.type === 'registre') {
        cur = { ...cur, reg_current: event.reg_current, reg_total: event.reg_total };
        onProgress(cur);
      } else if (event.type === 'result') {
        result = event.metadata;
      }
    });
    return result;
  },

  scan: async (): Promise<ScanReport> => {
    // Scan complet du NAS : peut dépasser le filet global → timeout désactivé.
    const response = await api.get('/api/collections/scan', { timeout: 0 });
    return response.data;
  },

  // Même analyse que scan(), mais en flux NDJSON : onProgress est appelé à chaque
  // collection traitée, et la promesse résout sur le rapport final.
  scanStream: async (onProgress: (p: ScanProgress) => void): Promise<ScanReport> => {
    let report: ScanReport | null = null;
    let cur: ScanProgress | null = null;
    await readNdjson('/api/collections/scan/stream', {}, (event) => {
      if (event.type === 'progress') {
        cur = { current: event.current, total: event.total, name: event.name };
        onProgress(cur);
      } else if (event.type === 'registre' && cur) {
        cur = { ...cur, reg_current: event.reg_current, reg_total: event.reg_total };
        onProgress(cur);
      } else if (event.type === 'report') {
        report = event.report;
      }
    });
    if (!report) throw new Error('Rapport de scan manquant');
    return report;
  },

  syncAll: async (): Promise<CollectionMetadata[]> => {
    // Synchronisation de toutes les collections : opération longue → timeout désactivé.
    const response = await api.post('/api/collections/sync-all', undefined, { timeout: 0 });
    return response.data;
  },

  // Rejoue le rapport d'analyse à partir de l'instantané `token`, sans relire le NAS.
  // Lève si l'instantané a expiré (HTTP 409) : l'appelant relance alors une analyse.
  scanReport: async (token: string): Promise<ScanReport> => {
    const response = await api.get('/api/collections/scan/report', { params: { token } });
    return response.data;
  },

  // Même synchronisation que syncAll(), mais en flux NDJSON : onProgress est appelé à
  // chaque collection synchronisée, et la promesse résout sur la liste finale.
  // `token` est celui du rapport d'analyse : fourni, la synchronisation repart de ce que
  // l'analyse a déjà lu au lieu de reparcourir tout le NAS. Le résumé de transcription est
  // renvoyé avec les résultats, ce qui évite un troisième parcours côté page.
  syncAllStream: async (
    token: string | undefined,
    onProgress: (p: ScanProgress) => void,
  ): Promise<{ results: CollectionMetadata[]; summary: TranscriptionsSummary[] }> => {
    let results: CollectionMetadata[] | null = null;
    let summary: TranscriptionsSummary[] = [];
    let cur: ScanProgress | null = null;
    const url = `/api/collections/sync-all/stream${token ? `?token=${encodeURIComponent(token)}` : ''}`;
    await readNdjson(url, { method: 'POST' }, (event) => {
      if (event.type === 'progress') {
        cur = { current: event.current, total: event.total, name: event.name };
        onProgress(cur);
      } else if (event.type === 'registre' && cur) {
        cur = { ...cur, reg_current: event.reg_current, reg_total: event.reg_total };
        onProgress(cur);
      } else if (event.type === 'done') {
        results = event.results;
        summary = event.summary ?? [];
      }
    });
    return { results: results ?? [], summary };
  },

  getStats: async (id: string): Promise<CollectionStatsResponse> => {
    const response = await api.get(`/api/collections/${id}/stats`);
    return response.data;
  },
};
