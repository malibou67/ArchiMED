import api from './config';

export interface SettingsEffective {
  ocr_workers: number;
  ocr_threads_per_worker: number;
  ocr_mixed_precision: boolean;
  ocr_pool_min_pages: number;
}

export interface SettingsStored {
  ocr_workers?: number;
  ocr_threads_per_worker?: number;
  ocr_mixed_precision?: boolean;
  ocr_pool_min_pages?: number;
}

export interface SettingsSystem {
  cpu_count: number;
  max_workers: number;
  recommended_workers: number;
  ram_total_gb: number | null;
  disk_free_gb: number | null;
}

export interface SettingsResponse {
  stored: SettingsStored;
  effective: SettingsEffective;
  system: SettingsSystem;
}

export interface SettingsUpdate {
  ocr_workers?: number | null;
  ocr_threads_per_worker?: number | null;
  ocr_mixed_precision?: boolean | null;
  ocr_pool_min_pages?: number | null;
}

export const settingsApi = {
  get: async (): Promise<SettingsResponse> => {
    const response = await api.get('/api/settings');
    return response.data;
  },
  update: async (update: SettingsUpdate): Promise<SettingsResponse> => {
    const response = await api.put('/api/settings', update);
    return response.data;
  },
};
