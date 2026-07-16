import api from './config';
import { TranscriptionInfo, TranscriptionsStats, TranscriptionsSummary } from '../types';

export const transcriptionsApi = {
  getAll: async (params?: { collection_id?: string; registre_id?: string }): Promise<TranscriptionInfo[]> => {
    const response = await api.get('/api/transcriptions/', { params });
    return response.data;
  },

  getStats: async (): Promise<TranscriptionsStats> => {
    const response = await api.get('/api/transcriptions/stats');
    return response.data;
  },

  getSummary: async (): Promise<TranscriptionsSummary[]> => {
    const response = await api.get('/api/transcriptions/summary');
    return response.data;
  },
};
