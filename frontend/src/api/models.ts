import api from './config';
import { ModelMetadata } from '../types';

export const modelsApi = {
  getAll: async (): Promise<ModelMetadata[]> => {
    const response = await api.get('/api/models/');
    return response.data;
  },

  extractMetadata: async (file: File): Promise<Partial<ModelMetadata>> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post('/api/models/extract-metadata', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  delete: async (id: string): Promise<void> => {
    await api.delete(`/api/models/${id}`);
  },

  get: async (id: string): Promise<ModelMetadata> => {
    const response = await api.get(`/api/models/${id}`);
    return response.data;
  },

  update: async (id: string, data: Partial<ModelMetadata>): Promise<ModelMetadata> => {
    const response = await api.put(`/api/models/${id}`, data);
    return response.data;
  },

  create: async (model: Partial<ModelMetadata>, file?: File | null): Promise<ModelMetadata> => {
    const formData = new FormData();
    formData.append('id', model.id || '');
    formData.append('name', model.name || '');
    formData.append('type', model.type || 'ocr');
    if (model.description) formData.append('description', model.description);
    if (model.version) formData.append('version', model.version);
    if (model.accuracy != null) formData.append('accuracy', String(model.accuracy));
    if (file) formData.append('file', file);

    const response = await api.post('/api/models/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },
};
