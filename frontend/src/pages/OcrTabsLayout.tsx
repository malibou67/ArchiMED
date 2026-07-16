import { Outlet } from 'react-router-dom';
import { Box } from '@mui/material';

// Page conteneur de la section OCR. Les onglets « Lancement » / « Modèles » sont
// désormais affichés dans la barre bleue (AppBar du Layout), à la place du titre.
export default function OcrTabsLayout() {
  return (
    // mt négatif : on réduit l'écart entre la barre bleue et le contenu (le padding
    // top de 24px du conteneur principal est un peu trop grand pour cette section).
    // Hauteur ajustée en conséquence (112px - 12px de remontée = 100px) pour que la
    // barre de lancement reste collée en bas sans débordement.
    <Box sx={{ height: 'calc(100vh - 100px)', mt: -1.5, display: 'flex', flexDirection: 'column' }}>
      {/* L'onglet Lancement remplit la hauteur (100%) ; l'onglet Modèles défile si besoin. */}
      <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        <Outlet />
      </Box>
    </Box>
  );
}
