import api from './config';

export interface SettingsEffective {
  ocr_workers: number;
  ocr_threads_per_worker: number;
  ocr_mixed_precision: boolean;
  ocr_pool_min_pages: number;
  index_workers: number;
  index_pool_min_pages: number;
}

export interface SettingsStored {
  ocr_workers?: number;
  ocr_threads_per_worker?: number;
  ocr_mixed_precision?: boolean;
  ocr_pool_min_pages?: number;
  index_workers?: number;
  index_pool_min_pages?: number;
}

export interface SettingsSystem {
  cpu_count: number;
  // Plafond applicable sur CE poste : min(cœurs, plafond VRAM du GPU s'il y en a un).
  max_workers: number;
  gpu_max_workers: number | null;
  gpu_vram_gb: number | null;
  recommended_workers: number;
  // L'indexation n'utilise pas le GPU : seuls les cœurs (et le NAS) la bornent.
  recommended_index_workers: number;
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
  index_workers?: number | null;
  index_pool_min_pages?: number | null;
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
