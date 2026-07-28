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

export const ocrApi = {
  // Enfile une tâche OCR (suivie via le système de tâches générique).
  // `timeout: 0` : le backend vérifie l'existence de chaque image avant d'enfiler, soit un
  // listing par registre — sur un partage réseau et une sélection de centaines de registres,
  // cela peut dépasser le filet de 60 s.
  run: async (seg_model_id: string, ocr_model_id: string, pages: OcrPageRef[]): Promise<Task> => {
    const response = await api.post('/api/ocr/run', { seg_model_id, ocr_model_id, pages }, { timeout: 0 });
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
