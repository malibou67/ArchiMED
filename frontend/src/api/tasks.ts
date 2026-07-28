import api from './config';
import { SystemRequirements } from './system';

export type TaskType = 'ocr' | 'index';
export type TaskStatus = 'queued' | 'running' | 'paused' | 'done' | 'error' | 'cancelled' | 'interrupted';

// Vérification d'environnement effectuée en première étape d'une tâche OCR.
export interface TaskPreflight extends SystemRequirements {
  device: 'cuda' | 'cpu';
  workers: number;
  threads: number;
  mixed_precision: boolean;
  checked_at: string;
  // Mode réellement utilisé : 'sequential' peut être un choix (1 worker, petit job) ou un
  // repli subi — dans ce cas `pool_fallback` porte la cause.
  mode?: 'parallel' | 'sequential';
  pool_fallback?: string;
  // Renseignés quand le réglage a été ramené à ce que la machine peut tenir
  // (`workers` porte alors la valeur appliquée).
  workers_requested?: number;
  workers_cap_reason?: 'gpu_vram';
}

export interface Task {
  id: string;
  type: TaskType;
  status: TaskStatus;
  label: string;
  total: number;
  processed: number;
  failed: number;
  current: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  errors: { page: string; error: string }[];
  errors_truncated?: number;
  // Spécifique OCR
  collections?: string[];
  registres?: string[];
  // « collection/registre » des registres entièrement traités, publiés au fil de l'eau
  // (les métadonnées de la collection sont à jour pour eux, sans attendre la fin de la tâche).
  registres_done?: string[];
  seg_model?: string;
  ocr_model?: string;
  preflight?: TaskPreflight;
  // Pages écartées à l'enfilage parce que leur image n'existe pas sur le disque
  // (trou de pagination, fichier déplacé depuis la dernière synchronisation).
  skipped_missing?: number;
  // Tâche née de la relance des pages en échec d'une autre tâche.
  retry_of?: string;
  // Spécifique Index
  collection_id?: string;
  collection_folder?: string;
  model_name?: string;
  index_id?: string;
  // Sources d'un index multi-collections/modèles (résumé compact pour l'affichage).
  index_sources?: { collection_titre?: string; collection_folder?: string; model_name?: string }[];
  // Origine multi-PC
  machine_id?: string;
  machine_label?: string;
  operator?: string;
  owned?: boolean;   // true si la tâche a été lancée sur CE poste
}

// Réponse des endpoints de contrôle (annuler / mettre en pause / reprendre).
// `requested` : la tâche appartient à un autre poste, la commande lui a été transmise et
// prendra effet à son prochain relevé — l'état ne change donc pas immédiatement.
export interface TaskControlResult {
  cancelled?: boolean;
  paused?: boolean;
  resumed?: boolean;
  requested: boolean;
  machine_label: string | null;
  task: Task | null;
}

// Réponse de la relance des pages en échec : `task` est une **nouvelle** tâche, la tâche
// d'origine (`source_task_id`) n'est pas modifiée.
export interface TaskRetryResult {
  task: Task;
  source_task_id: string;
  retried: number;
  skipped: number;
}

export interface TasksSummary {
  running: Task[];
  paused: Task[];
  interrupted: Task[];
  queued: number;
  queued_tasks: Task[];
  has_activity: boolean;
}

// Détail par page (tâches OCR)
export type PageState = 'done' | 'failed' | 'current' | 'pending';

export interface TaskPageStatus {
  page: string;
  status: PageState;
  error?: string | null;
}

export interface TaskPagesResponse {
  total: number;
  offset: number;
  limit: number;
  counts: { all: number; done: number; failed: number; todo: number };
  items: TaskPageStatus[];
}

// Détail par registre (tâches d'indexation)
export interface TaskRegistre {
  name: string;
  pages: number;
  status: 'done' | 'current' | 'pending';
}

export interface TaskRegistresResponse {
  counts: { all: number; done: number; current: number; pending: number };
  items: TaskRegistre[];
}

export const tasksApi = {
  summary: async (): Promise<TasksSummary> => {
    const response = await api.get('/api/tasks/summary');
    return response.data;
  },

  list: async (): Promise<Task[]> => {
    const response = await api.get('/api/tasks');
    return response.data;
  },

  get: async (taskId: string): Promise<Task> => {
    const response = await api.get(`/api/tasks/${taskId}`);
    return response.data;
  },

  cancel: async (taskId: string): Promise<TaskControlResult> => {
    const response = await api.post(`/api/tasks/${taskId}/cancel`);
    return response.data;
  },

  pause: async (taskId: string): Promise<TaskControlResult> => {
    const response = await api.post(`/api/tasks/${taskId}/pause`);
    return response.data;
  },

  resume: async (taskId: string): Promise<TaskControlResult> => {
    const response = await api.post(`/api/tasks/${taskId}/resume`);
    return response.data;
  },

  remove: async (taskId: string): Promise<void> => {
    await api.delete(`/api/tasks/${taskId}`);
  },

  // Réenfile les pages en échec d'une tâche OCR terminée dans une nouvelle tâche.
  // `timeout: 0` : l'enfilage vérifie l'existence des images (listing par registre).
  retryFailed: async (taskId: string): Promise<TaskRetryResult> => {
    const response = await api.post(`/api/tasks/${taskId}/retry-failed`, undefined, { timeout: 0 });
    return response.data;
  },

  getPages: async (
    taskId: string,
    status: 'all' | 'todo' | 'done' | 'failed',
    offset: number,
    limit: number,
  ): Promise<TaskPagesResponse> => {
    const response = await api.get(`/api/tasks/${taskId}/pages`, { params: { status, offset, limit } });
    return response.data;
  },

  getRegistres: async (taskId: string): Promise<TaskRegistresResponse> => {
    const response = await api.get(`/api/tasks/${taskId}/registres`);
    return response.data;
  },
};
