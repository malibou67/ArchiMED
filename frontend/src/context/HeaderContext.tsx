import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface HeaderValue {
  // Contenu personnalisé à afficher dans la barre bleue, ou null pour le titre par défaut.
  content: ReactNode | null;
  setContent: (content: ReactNode | null) => void;
}

const HeaderContext = createContext<HeaderValue | null>(null);

export function HeaderProvider({ children }: { children: ReactNode }) {
  const [content, setContent] = useState<ReactNode | null>(null);
  return (
    <HeaderContext.Provider value={{ content, setContent }}>
      {children}
    </HeaderContext.Provider>
  );
}

export function useHeaderContext() {
  const ctx = useContext(HeaderContext);
  if (!ctx) throw new Error('useHeaderContext doit être utilisé dans un HeaderProvider');
  return ctx;
}

/**
 * Affiche un contenu personnalisé dans la barre bleue tant que la page est montée.
 * À placer en haut du composant. Le contenu est recalculé quand `deps` change.
 *
 * @example usePageHeader(<MonTitre />, [titre]);
 */
export function usePageHeader(content: ReactNode, deps: unknown[]) {
  const { setContent } = useHeaderContext();
  useEffect(() => {
    setContent(content);
    return () => setContent(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
