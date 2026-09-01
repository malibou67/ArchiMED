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
  // Première mise en route de la tâche : une reprise après pause ne la réécrit pas.
  started_at: string | null;
  // Début du segment d'exécution en cours, `null` dès qu'il est clos (pause, fin, interruption).
  // Absent des tâches enfilées avant leur introduction — cf. `taskWorkMs`.
  run_started_at?: string | null;
  work_ms?: number;   // temps de travail cumulé des segments clos, en ms
  finished_at: string | null;
  error: string | null;
  errors: { page: string; error: string }[];
  errors_truncated?: number;
  // Spécifique OCR
  collections?: string[];
  registres?: string[];
  // Couples (collection, registre) réellement couverts — l'unité de verrou côté backend.
  // Absent des tâches enfilées avant son introduction : replier sur collections × registres.
  scopes?: [string, string][];
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
  // Page (XML) en cours de lecture — l'équivalent de `current` pour l'OCR, `current` portant
  // ici le registre (collection · modèle · registre).
  current_page?: string | null;
  index_is_new?: boolean;    // première génération de cet index
  index_full?: boolean;      // reconstruction complète (sinon mise à jour incrémentale)
  // Pages conservées par une mise à jour incrémentale : déjà indexées, hors de `total`.
  index_base?: number;
  // Origine multi-PC
  machine_id?: string;
  machine_label?: string;
  operator?: string;
  owned?: boolean;   // true si la tâche a été lancée sur CE poste
  // Dernier relevé écrit par le poste propriétaire (ISO **naïf**, horloge de CE poste-là).
  // Rafraîchi toutes les ~5 s tant que la tâche est en cours ; figé sur les autres statuts —
  // c'est ce qui date les données d'une tâche distante.
  heartbeat?: string;
}

// Temps de travail effectif d'une tâche, temps passé en pause exclu — `null` si elle n'a jamais
// démarré. Une tâche reprise enchaîne plusieurs segments : `work_ms` cumule ceux qui sont clos,
// `run_started_at` date celui qui court. Mesurer depuis `started_at` reviendrait à ne compter que
// le dernier segment alors que `processed` couvre, lui, tout le travail accompli — c'est ce qui
// faisait s'effondrer le « reste ~ » après une reprise.
// Repli sur l'ancien calcul pour les tâches écrites avant l'introduction de ces champs (historique
// local, ou tâche d'un poste pas encore à jour).
export const taskWorkMs = (t: Task, now: number): number | null => {
  if (t.work_ms == null && !t.run_started_at) {
    if (!t.started_at) return null;
    // Sans date de fin ni tâche en cours (une pause d'avant le compteur), il n'y a rien de
    // mesurable : `now` compterait l'attente comme du travail.
    const fin = t.finished_at ? Date.parse(t.finished_at) : t.status === 'running' ? now : null;
    return fin == null ? null : fin - Date.parse(t.started_at);
  }
  // Le segment courant est daté par l'horloge du poste propriétaire : borné à ≥ 0, comme partout
  // où l'on compare une date distante à la nôtre.
  const encours = t.run_started_at ? Math.max(0, now - Date.parse(t.run_started_at)) : 0;
  return (t.work_ms ?? 0) + encours;
};

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
