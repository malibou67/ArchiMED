import { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Typography, Button } from '@mui/material';

interface EmptyStateAction {
  label: string;
  /** Navigation interne (prioritaire si fourni). */
  path?: string;
  /** Handler personnalisé (utilisé si `path` est absent). */
  onClick?: () => void;
}

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: EmptyStateAction;
}

/**
 * État vide uniforme : icône grisée, titre, description discrète et action optionnelle.
 * Centré dans l'espace disponible. À réutiliser pour « aucune collection / modèle /
 * index… » afin d'éviter les messages ad hoc éparpillés.
 */
export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  const navigate = useNavigate();

  const handleAction = () => {
    if (!action) return;
    if (action.path) navigate(action.path);
    else action.onClick?.();
  };

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center',
        gap: 1,
        p: 4,
        height: '100%',
        minHeight: 200,
        color: 'text.secondary',
      }}
    >
      {icon && (
        <Box sx={{ fontSize: 56, lineHeight: 1, color: 'action.disabled', '& > svg': { fontSize: 'inherit' } }}>
          {icon}
        </Box>
      )}
      <Typography variant="h6" color="text.primary" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      {description && (
        <Typography variant="body2" sx={{ maxWidth: 440 }}>
          {description}
        </Typography>
      )}
      {action && (
        <Button variant="outlined" onClick={handleAction} sx={{ mt: 1 }}>
          {action.label}
        </Button>
      )}
    </Box>
  );
}
