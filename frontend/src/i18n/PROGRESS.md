# Suivi de l'internationalisation (FR/EN)

Anglais par défaut, français en option. Langue stockée dans `localStorage`
(`archimed_lang`), donc propre à chaque poste — aucun réglage backend.

## Comment reprendre le travail
1. Lire ce fichier pour voir la prochaine phase non cochée.
2. Pour chaque page/composant : extraire les chaînes FR, remplir `locales/fr/<ns>.json`
   (texte existant) **et** `locales/en/<ns>.json` (anglais), puis remplacer le JSX par `t()`.
3. `cd frontend && npm run build` doit passer, puis vérif visuelle en basculant la langue
   dans **Paramètres**.
4. Cocher la case ci-dessous et committer (1 commit par phase).

## Conventions
- Hook : `const { t } = useTranslation('<namespace>')` ; `useTranslation(['<page>', 'common'])`
  si besoin de `common`. **Si une variable `t` existe déjà** (ex. boucle `map((t) => …)`),
  renommer la fonction de trad : `const { t: tr } = useTranslation(...)`.
- Clés sémantiques : `settings:performance.title`, `common:actions.save`.
- Pluriels : clés `key_one` / `key_other`, appel `t('k', { count })`.
- Interpolation : `t('k', { name })` avec `{{name}}` dans le JSON.
- `en/<ns>.json` et `fr/<ns>.json` doivent avoir **exactement les mêmes clés**.
- Repérer le texte FR restant : chercher les caractères accentués (é, è, à, ç…) dans le JSX.

## Namespaces
`common` (nav, layout, composants partagés, actions) · `settings` · `search` ·
`collections` · `ocr` · `indexes` · `tasks` · `transcriptions`

## Phases
- [x] **Phase 1 — Infra + coquille** : setup i18next (`i18n/index.ts`, `main.tsx`),
  sélecteur de langue dans `SettingsPage`, `Layout.tsx`, `TaskWidget`, `Loader`,
  `GlobalLoadingOverlay`, `DataDirGuard`. Namespace `common`.
- [x] **Phase 2 — Paramètres** : `pages/SettingsPage.tsx` (tout le reste). Namespace `settings`.
- [x] **Phase 3 — Recherche** : `pages/SearchPage.tsx`, `IndexVocabularyPage.tsx`,
  `WordPagesPage.tsx`, `components/stats/SearchResultsStats.tsx`. Namespace `search`.
- [x] **Phase 4 — Collections** : `pages/CollectionsPage.tsx`, `CollectionStatsPage.tsx`,
  `RegistreViewerPage.tsx`, `components/PageImageViewer.tsx` (→ `common.imageViewer`),
  `components/stats/*` (→ namespace `stats`). Namespaces `collections` + `stats`.
- [x] **Phase 5 — OCR + Modèles** : `pages/OcrPage.tsx`, `OcrTabsLayout.tsx` (aucun texte),
  `ModelsPage.tsx`, `components/ocr/*`. Namespace `ocr` (avec sous-section `modelsPage`).
- [x] **Phase 6 — Index + Tâches + Transcriptions** : `pages/IndexesPage.tsx`, `TasksPage.tsx`,
  `TranscriptionsPage.tsx`. Namespaces `indexes`, `tasks`, `transcriptions`.

## ✅ Terminé — les 6 phases sont faites
Tous les namespaces ont une parité de clés EN/FR parfaite (`npm run build` vert).
Namespaces : `common` `settings` `search` `collections` `stats` `ocr` `indexes` `tasks` `transcriptions`.
Note : `backend/api/collections.ts` garde un fallback technique `'Erreur serveur'` (jamais affiché —
remplacé par un message traduit dans le `catch` appelant). Les `console.error` restent en français (logs dev).

> Tant qu'une page n'est pas traitée, son texte reste en français codé en dur, quelle que
> soit la langue choisie — c'est normal pendant le déploiement progressif.
