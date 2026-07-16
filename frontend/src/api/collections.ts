import api from './config';
import { CollectionMetadata, CollectionUpdate, ScanReport, ScanProgress, CollectionStatsResponse } from '../types';

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

  scan: async (): Promise<ScanReport> => {
    // Scan complet du NAS : peut dépasser le filet global → timeout désactivé.
    const response = await api.get('/api/collections/scan', { timeout: 0 });
    return response.data;
  },

  // Même analyse que scan(), mais en flux NDJSON : onProgress est appelé à chaque
  // collection traitée, et la promesse résout sur le rapport final.
  scanStream: async (onProgress: (p: ScanProgress) => void): Promise<ScanReport> => {
    let report: ScanReport | null = null;
    await readNdjson('/api/collections/scan/stream', {}, (event) => {
      if (event.type === 'progress') {
        onProgress({ current: event.current, total: event.total, name: event.name });
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

  // Même synchronisation que syncAll(), mais en flux NDJSON : onProgress est appelé à
  // chaque collection synchronisée, et la promesse résout sur la liste finale.
  syncAllStream: async (onProgress: (p: ScanProgress) => void): Promise<CollectionMetadata[]> => {
    let results: CollectionMetadata[] | null = null;
    await readNdjson('/api/collections/sync-all/stream', { method: 'POST' }, (event) => {
      if (event.type === 'progress') {
        onProgress({ current: event.current, total: event.total, name: event.name });
      } else if (event.type === 'done') {
        results = event.results;
      }
    });
    return results ?? [];
  },

  getStats: async (id: string): Promise<CollectionStatsResponse> => {
    const response = await api.get(`/api/collections/${id}/stats`);
    return response.data;
  },
};
