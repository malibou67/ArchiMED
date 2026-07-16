import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from 'react';
import { tasksApi, TasksSummary, Task } from '../api/tasks';

interface TasksValue {
  summary: TasksSummary | null;
  runningTasks: Task[];
  pausedTasks: Task[];
  interruptedTasks: Task[];
  queuedTasks: Task[];
  queuedCount: number;
  hasActivity: boolean;
  // Ouverture manuelle du widget flottant (déclenchée depuis le bouton de la barre).
  widgetOpen: boolean;
  openWidget: () => void;
  closeWidget: () => void;
  cancel: (taskId: string) => Promise<void>;
  pause: (taskId: string) => Promise<void>;
  resume: (taskId: string) => Promise<void>;
  remove: (taskId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const TasksContext = createContext<TasksValue | null>(null);

const ACTIVE_INTERVAL = 2000;

export function TasksProvider({ children }: { children: ReactNode }) {
  const [summary, setSummary] = useState<TasksSummary | null>(null);
  const [widgetOpen, setWidgetOpen] = useState(false);
  const openWidget = useCallback(() => setWidgetOpen(true), []);
  const closeWidget = useCallback(() => setWidgetOpen(false), []);

  const fetchSummary = useCallback(async () => {
    try {
      setSummary(await tasksApi.summary());
    } catch {
      // backend indisponible : on réessaiera plus tard
    }
  }, []);

  // Un fetch au montage (détecte une tâche déjà active : reload ou reprise au démarrage).
  useEffect(() => { fetchSummary(); }, [fetchSummary]);

  // Poll uniquement tant qu'une tâche est active. Au repos → aucune requête.
  useEffect(() => {
    if (!summary?.has_activity) return;
    const id = setInterval(fetchSummary, ACTIVE_INTERVAL);
    return () => clearInterval(id);
  }, [summary?.has_activity, fetchSummary]);

  const cancel = useCallback(async (taskId: string) => {
    await tasksApi.cancel(taskId);
    await fetchSummary();
  }, [fetchSummary]);

  const pause = useCallback(async (taskId: string) => {
    await tasksApi.pause(taskId);
    await fetchSummary();
  }, [fetchSummary]);

  const resume = useCallback(async (taskId: string) => {
    await tasksApi.resume(taskId);
    await fetchSummary();
  }, [fetchSummary]);

  const remove = useCallback(async (taskId: string) => {
    await tasksApi.remove(taskId);
    await fetchSummary();
  }, [fetchSummary]);

  const value: TasksValue = {
    summary,
    runningTasks: summary?.running ?? [],
    pausedTasks: summary?.paused ?? [],
    interruptedTasks: summary?.interrupted ?? [],
    queuedTasks: summary?.queued_tasks ?? [],
    queuedCount: summary?.queued ?? 0,
    hasActivity: summary?.has_activity ?? false,
    widgetOpen,
    openWidget,
    closeWidget,
    cancel,
    pause,
    resume,
    remove,
    refresh: fetchSummary,
  };

  return <TasksContext.Provider value={value}>{children}</TasksContext.Provider>;
}

export function useTasks(): TasksValue {
  const ctx = useContext(TasksContext);
  if (!ctx) throw new Error('useTasks doit être utilisé dans un TasksProvider');
  return ctx;
}
