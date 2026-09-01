import { useCallback, useState } from 'react';

/** Action déclenchée depuis une ligne de tableau, dont l'effet n'est pas immédiat.
 *  `start` : mise en route d'un traitement (enfilage), dont l'attente est celle de l'appel. */
export type TaskActionKind = 'pause' | 'resume' | 'cancel' | 'delete' | 'start';

export interface PendingTaskAction {
  kind: TaskActionKind;
  /** Tâche (page Tâches) ou index (page Index) visé — sert à savoir quand l'action a abouti. */
  id: string;
  /** Précise le détail affiché : l'arrêt attend la fin de la page (OCR) ou du registre (index). */
  taskType?: 'ocr' | 'index';
  /** Poste destinataire, quand la commande a été transmise à une autre machine. */
  machine?: string | null;
  /** Libellés explicites, quand le sujet n'est pas une tâche (suppression d'un index). */
  title?: string;
  detail?: string;
}

/**
 * État de l'action en attente : laquelle, et faut-il encore afficher son overlay
 * (cf. `TaskActionOverlay`).
 *
 * Savoir si une action a abouti se lit dans l'état de la page hôte (statut de la tâche, disparition
 * de la ligne…) et diffère d'une page à l'autre : c'est donc à l'appelant de surveiller `pending` et
 * d'appeler `done()`. Les fonctions renvoyées sont stables, elles peuvent figurer telles quelles
 * dans les dépendances de cet effet.
 */
export function usePendingTaskAction() {
  const [pending, setPending] = useState<PendingTaskAction | null>(null);
  const [masqué, setMasqué] = useState(false);

  const start = useCallback((action: PendingTaskAction) => { setMasqué(false); setPending(action); }, []);
  /** Complète l'action en cours — `machine` n'est connue qu'au retour de l'appel. */
  const update = useCallback(
    (patch: Partial<PendingTaskAction>) => setPending((p) => (p ? { ...p, ...patch } : p)), []);
  /** Ferme l'overlay : l'action a abouti, ou elle a échoué. */
  const done = useCallback(() => { setPending(null); setMasqué(false); }, []);
  /** « Masquer » : rend la main sans rien annuler, l'action suit son cours. */
  const hide = useCallback(() => setMasqué(true), []);

  return { pending, overlayOpen: pending !== null && !masqué, start, update, done, hide };
}
