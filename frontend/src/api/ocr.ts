import api from './config';
import { Task } from './tasks';

export interface OcrPageRef {
  collection: string;
  registre: string;
  page: string;
}

export interface OcrScopeItem {
  collection: string;
  registre?: string;
}

// Filtre d'état appliqué à un périmètre par le backend, miroir de `OcrService._SCOPE_ONLY`.
export type OcrScopeOnly = 'all' | 'missing' | 'done';

export const ocrApi = {
  // Enfile une tâche OCR (suivie via le système de tâches générique).
  //
  // Deux façons de désigner les pages, cumulables : `pages` les nomme une à une, `scopes`
  // désigne des registres entiers que le backend développe depuis le disque. Le périmètre
  // évite d'envoyer les centaines de milliers de noms d'une grosse collection — et surtout
  // de les fabriquer depuis la pagination, ce que le client ne sait pas faire (la largeur du
  // champ numérique est perdue dans le motif).
  //
  // `timeout: 0` : le backend liste chaque registre concerné avant d'enfiler — sur un partage
  // réseau et une sélection de centaines de registres, cela dépasse le filet de 60 s.
  run: async (
    seg_model_id: string,
    ocr_model_id: string,
    pages: OcrPageRef[],
    scopes?: OcrScopeItem[],
    scope_only: OcrScopeOnly = 'all',
  ): Promise<Task> => {
    const response = await api.post(
      '/api/ocr/run', { seg_model_id, ocr_model_id, pages, scopes, scope_only }, { timeout: 0 });
    return response.data;
  },

  // Stems (nom sans extension) des pages déjà transcrites pour (collection, registre, modèle).
  donePages: async (collection: string, registre: string, model: string): Promise<string[]> => {
    const response = await api.get('/api/ocr/done', { params: { collection, registre, model } });
    return response.data.stems;
  },

  // Pages sans transcription pour un modèle (périmètre optionnel ; absent = tout).
  missingPages: async (model: string, scope?: OcrScopeItem[]): Promise<OcrPageRef[]> => {
    const response = await api.post('/api/ocr/missing', { model, scope });
    return response.data.pages;
  },
};
