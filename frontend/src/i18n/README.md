# Internationalisation (EN/FR)

Anglais par défaut, français en option. La langue est stockée dans `localStorage`
(clé `archimed_lang`), donc **propre à chaque poste** : même lancé depuis un NAS partagé,
l'exécutable n'écrit rien de linguistique dans `data/`. Aucun réglage backend n'est impliqué.
Le sélecteur se trouve sur la page **Paramètres**.

Les traductions sont importées statiquement dans [index.ts](index.ts) (pas de chargement
asynchrone, donc pas de `<Suspense>` à prévoir).

## Namespaces

Un fichier JSON par namespace dans `locales/en/` et `locales/fr/` :

`common` (nav, layout, composants partagés, actions) · `settings` · `search` · `collections` ·
`stats` · `ocr` · `indexes` · `tasks` · `transcriptions`

Ajouter un namespace veut dire ajouter les **deux** fichiers JSON, puis les importer et les
déclarer dans `resources` (`index.ts`).

## Conventions

- Hook : `const { t } = useTranslation('<namespace>')`, ou `useTranslation(['<page>', 'common'])`
  quand `common` est nécessaire. **Si une variable `t` existe déjà** (typiquement une boucle
  `map((t) => …)`), renommer la fonction de traduction : `const { t: tr } = useTranslation(...)`.
- Clés sémantiques : `settings:performance.title`, `common:actions.save`.
- Pluriels : clés `key_one` / `key_other`, appel `t('k', { count })`.
- Interpolation : `t('k', { name })` avec `{{name}}` dans le JSON.
- `en/<ns>.json` et `fr/<ns>.json` doivent avoir **exactement les mêmes clés**.
- Aucun texte visible en dur dans le JSX. Pour repérer un oubli, chercher les caractères
  accentués (é, è, à, ç…) dans les composants.

## Exceptions assumées

- [../api/collections.ts](../api/collections.ts) garde un fallback technique `'Erreur serveur'`,
  jamais affiché : le `catch` appelant le remplace par un message traduit.
- Les `console.error` restent en français (logs de développement, pas d'interface).

## Vérification

`npm run build` doit passer, puis contrôle visuel en basculant la langue dans **Paramètres**.
