import api from './config';

export interface SystemRequirements {
  kraken: { ok: boolean; version: string | null; error?: string | null } | null;
  torch: { ok: boolean; version: string | null } | null;
  torchvision: { ok: boolean; version: string | null; error?: string | null } | null;
  cuda: { ok: boolean; device: string | null; vram_gb?: number | null } | null;
}

export interface MachineIdentity {
  machine_id: string;
  machine_label: string;
  operator: string;
}

// Santé du dossier de données. `exists=false` ⇒ installation cassée (écran bloquant).
export interface StorageStatus {
  data_dir: string;
  exists: boolean;
  writable: boolean;
  subdirs: {
    collections: boolean;
    models: boolean;
    indexes: boolean;
  };
}

export const systemApi = {
  getRequirements: async (): Promise<SystemRequirements> => {
    const response = await api.get('/api/system/requirements');
    return response.data;
  },

  // État du stockage : sert à détecter un dossier data manquant/non inscriptible.
  getStorage: async (): Promise<StorageStatus> => {
    const response = await api.get('/api/system/storage');
    return response.data;
  },

  // Identité de CE poste (machine_id auto + libellé/opérateur locaux).
  getIdentity: async (): Promise<MachineIdentity> => {
    const response = await api.get('/api/system/identity');
    return response.data;
  },

  // Met à jour le libellé / l'opérateur de CE poste (stockés localement, hors NAS).
  updateIdentity: async (data: { machine_label?: string; operator?: string }): Promise<MachineIdentity> => {
    const response = await api.put('/api/system/identity', data);
    return response.data;
  },
};
