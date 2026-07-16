import axios from 'axios';

// Filet de sécurité : au-delà de 60 s sans réponse, on considère que le backend est figé/mort
// (process arrêté, NAS décroché, connexion gelée) plutôt que de laisser l'UI tourner à l'infini.
// Les appels légitimement longs (scan complet, lecture de gros index.json) passent `timeout: 0`
// par requête pour désactiver ce filet.
export const api = axios.create({
  baseURL: '',
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Normalise les erreurs côté client (timeout / backend injoignable) dans la forme déjà lue par
// les pages (`e.response.data.detail`), pour afficher un message parlant sans modifier chaque page.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!error.response && (error.code === 'ECONNABORTED' || error.code === 'ERR_NETWORK')) {
      error.response = {
        data: {
          detail:
            error.code === 'ECONNABORTED'
              ? 'Le serveur ne répond pas (délai dépassé). Réessayez.'
              : "Serveur injoignable. Vérifiez qu'ArchiMED est bien lancé, puis réessayez.",
        },
      };
    }
    return Promise.reject(error);
  },
);

export default api;
