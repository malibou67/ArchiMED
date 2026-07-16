import { createContext, useContext, useState, useCallback, useEffect, useId, ReactNode } from 'react';

interface LoadingValue {
  // Message courant à afficher (le plus récemment enregistré), ou null si rien ne charge.
  message: string | null;
  // Enregistre/retire un message pour une source donnée (clé unique par composant).
  setLoading: (key: string, message: string | null) => void;
}

const LoadingContext = createContext<LoadingValue | null>(null);

export function LoadingProvider({ children }: { children: ReactNode }) {
  // Plusieurs sources peuvent charger en parallèle : on les indexe par clé.
  const [loaders, setLoaders] = useState<Record<string, string>>({});

  const setLoading = useCallback((key: string, message: string | null) => {
    setLoaders((prev) => {
      if (message === null) {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      }
      if (prev[key] === message) return prev;
      return { ...prev, [key]: message };
    });
  }, []);

  // Le dernier message enregistré fait foi (le chargement le plus récent).
  const messages = Object.values(loaders);
  const message = messages.length > 0 ? messages[messages.length - 1] : null;

  return (
    <LoadingContext.Provider value={{ message, setLoading }}>
      {children}
    </LoadingContext.Provider>
  );
}

export function useLoadingContext() {
  const ctx = useContext(LoadingContext);
  if (!ctx) throw new Error('useLoadingContext doit être utilisé dans un LoadingProvider');
  return ctx;
}

/**
 * Déclare un message de chargement global tant que `active` est vrai.
 * À placer en haut du composant, avant tout `return` anticipé.
 *
 * @example usePageLoading('Chargement des paramètres…', loading);
 */
export function usePageLoading(message: string, active: boolean) {
  const { setLoading } = useLoadingContext();
  const key = useId();

  useEffect(() => {
    setLoading(key, active ? message : null);
    return () => setLoading(key, null);
  }, [key, message, active, setLoading]);
}
