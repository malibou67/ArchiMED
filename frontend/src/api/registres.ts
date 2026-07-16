import api from './config';
import { RegistreMetadata, RegistreCreate, RegistreUpdate } from '../types';

export const registresApi = {
  getAll: async (): Promise<RegistreMetadata[]> => {
    const response = await api.get('/api/registres/');
    return response.data;
  },

  getByCollection: async (collectionId: string): Promise<RegistreMetadata[]> => {
    const response = await api.get(`/api/registres/${collectionId}`);
    return response.data;
  },

  getById: async (collectionId: string, registreId: string): Promise<RegistreMetadata> => {
    const response = await api.get(`/api/registres/${collectionId}/${registreId}`);
    return response.data;
  },

  create: async (collectionId: string, registre: RegistreCreate): Promise<RegistreMetadata> => {
    const response = await api.post(`/api/registres/${collectionId}`, registre);
    return response.data;
  },

  update: async (collectionId: string, registreId: string, update: RegistreUpdate): Promise<RegistreMetadata> => {
    const response = await api.put(`/api/registres/${collectionId}/${registreId}`, update);
    return response.data;
  },

  getPages: async (collectionId: string, registreId: string): Promise<string[]> => {
    const response = await api.get(`/api/registres/${collectionId}/${registreId}/pages`);
    return response.data;
  },

  getPageUrl: (collectionId: string, registreId: string, filename: string): string => {
    return `/api/registres/${collectionId}/${registreId}/pages/${filename}`;
  },

  // Modèles ayant transcrit chaque page : { stem du fichier image -> [modèles] }
  getPageTranscriptions: async (collectionId: string, registreId: string): Promise<Record<string, string[]>> => {
    const response = await api.get(`/api/registres/${collectionId}/${registreId}/transcriptions`);
    return response.data;
  },
};
