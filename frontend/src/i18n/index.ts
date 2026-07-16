// Initialisation i18next — bilingue FR/EN, anglais par défaut.
//
// La langue est stockée dans `localStorage` (clé `archimed_lang`), donc PROPRE À CHAQUE
// POSTE : même si l'exécutable vient du NAS, `localStorage` s'écrit dans le navigateur
// local du PC (jamais sur le NAS). Même principe que l'identité du poste
// (`backend/machine_identity.py`). Aucun réglage backend n'est impliqué.
//
// Ressources bundlées statiquement (imports JSON) → pas de chargement asynchrone,
// donc pas besoin de <Suspense> autour de l'application.
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import enCommon from './locales/en/common.json';
import frCommon from './locales/fr/common.json';
import enSettings from './locales/en/settings.json';
import frSettings from './locales/fr/settings.json';
import enSearch from './locales/en/search.json';
import frSearch from './locales/fr/search.json';
import enCollections from './locales/en/collections.json';
import frCollections from './locales/fr/collections.json';
import enStats from './locales/en/stats.json';
import frStats from './locales/fr/stats.json';
import enOcr from './locales/en/ocr.json';
import frOcr from './locales/fr/ocr.json';
import enIndexes from './locales/en/indexes.json';
import frIndexes from './locales/fr/indexes.json';
import enTasks from './locales/en/tasks.json';
import frTasks from './locales/fr/tasks.json';
import enTranscriptions from './locales/en/transcriptions.json';
import frTranscriptions from './locales/fr/transcriptions.json';

export const resources = {
  en: { common: enCommon, settings: enSettings, search: enSearch, collections: enCollections, stats: enStats, ocr: enOcr, indexes: enIndexes, tasks: enTasks, transcriptions: enTranscriptions },
  fr: { common: frCommon, settings: frSettings, search: frSearch, collections: frCollections, stats: frStats, ocr: frOcr, indexes: frIndexes, tasks: frTasks, transcriptions: frTranscriptions },
} as const;

export const SUPPORTED_LANGUAGES = ['en', 'fr'] as const;
export type AppLanguage = (typeof SUPPORTED_LANGUAGES)[number];

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    // Anglais par défaut : on ne lit QUE localStorage (pas la langue du navigateur),
    // et à défaut de valeur stockée on retombe sur `fallbackLng`.
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LANGUAGES as unknown as string[],
    defaultNS: 'common',
    detection: {
      order: ['localStorage'],
      caches: ['localStorage'],
      lookupLocalStorage: 'archimed_lang',
    },
    interpolation: { escapeValue: false }, // React échappe déjà le contenu.
    debug: import.meta.env.DEV,
  });

export default i18n;
