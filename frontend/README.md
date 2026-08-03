# ArchiMED — frontend

React + TypeScript + Vite single-page application, served in production by the FastAPI
backend. For the overall picture see [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md); for the
endpoints this app calls, [docs/API.md](../docs/API.md).

## Scripts

```bash
npm install
npm run dev       # dev server on http://localhost:5173, proxies /api → localhost:38520
npm run build     # tsc -b && vite build → output goes to ../backend/static/
npm run lint      # eslint
npm run preview   # serve the production build locally
```

The dev proxy and the build output directory are both set in
[vite.config.ts](vite.config.ts) — there is no frontend `.env`. In production the API is
same-origin (FastAPI serves `backend/static/`), so `api/config.ts` keeps an empty `baseURL`.

## Source layout

```
src/
├── api/          # one module per backend router, over the shared Axios instance (config.ts)
├── components/   # Layout, TaskWidget, Loader, DataDirGuard, PageImageViewer…
│   ├── ocr/      # OCR launch bar and environment status chip
│   └── stats/    # dashboards, charts and statistics cards
├── context/      # React contexts: Loading, Header, Tasks
├── pages/        # one file per route (Collections, Ocr, Indexes, Search, Tasks, Settings…)
├── i18n/         # i18next setup + locales/{en,fr}/*.json — see i18n/README.md
├── types.ts      # shared domain types (API-specific types live next to each api/*.ts)
└── App.tsx       # routes + MUI theme
```

## Conventions

- Material-UI v7 for every component; styling through the `sx` prop, no CSS modules.
- Strict TypeScript: shared domain models in `types.ts`, request/response shapes exported
  from the matching `api/*.ts`.
- All user-facing text goes through `react-i18next` — never hard-code a string in the JSX.
  See [src/i18n/README.md](src/i18n/README.md).
