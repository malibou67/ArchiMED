# Technical architecture — ArchiMED

This document gathers the technical details of the application. For a general overview of
the project, see the [README](../README.md). For the list of endpoints, see
[API.md](API.md).

## Overview

Web application (FastAPI backend + React frontend) shipped as a Windows desktop
application via PyInstaller (standalone executable + system-tray icon).

### Backend (FastAPI + Python)
- REST API to manage models, collections, registers, transcriptions, OCR, search indexes,
  background tasks and settings (**9 routers**)
- File-based data model using JSON and XML files (PAGE-XML format) — no database
- Endpoints documented automatically with Swagger
- **Persistent task engine** (see [Background tasks](#background-tasks)) for OCR runs and
  index builds — a generic in-process queue, not FastAPI `BackgroundTasks`
- The built frontend is served by FastAPI itself (same origin, no CORS)

### Frontend (React + TypeScript + Vite + Material-UI)
- Modern, responsive user interface
- Bilingual **i18n (English / French)** via `react-i18next`, switchable from the Settings page
- SPA (Single Page Application) with React Router
- Statistics dashboards and charts (`@mui/x-charts`)
- Complete management of OCR resources (models, OCR runs, indexes, tasks)

## Project structure

```
ArchiMED/
├── backend/
│   ├── data/               # User data (see DATA_DIR; not versioned)
│   │   ├── collections/    # Collections with scans and OCR results (XML)
│   │   ├── models/         # Kraken OCR models (.mlmodel + <id>_metadata.json)
│   │   ├── indexes/        # Generated full-text search indexes
│   │   └── settings.json   # OCR settings set from the UI
│   ├── routers/            # API routes (9 routers)
│   │   ├── models.py
│   │   ├── collections.py
│   │   ├── registres.py
│   │   ├── ocr.py
│   │   ├── transcriptions.py
│   │   ├── indexes.py
│   │   ├── tasks.py
│   │   ├── settings.py
│   │   └── system.py
│   ├── static/             # Built frontend (served by FastAPI)
│   ├── main.py             # FastAPI entry point + system-tray icon (pystray)
│   ├── models.py           # Pydantic models
│   ├── services.py         # Core CRUD services (models, collections, registers, indexes, transcriptions)
│   ├── ocr_service.py      # Kraken OCR pipeline + 'ocr' task runner
│   ├── index_runner.py     # Index build + 'index' task runner
│   ├── task_service.py     # Generic task engine (queue, persistence, pause/cancel, multi-PC)
│   ├── stats_service.py    # Collection and index statistics
│   ├── settings_service.py # settings.json read/write
│   ├── machine_identity.py # Per-machine identity (multi-PC deployment)
│   ├── system_checks.py    # Kraken/torch readiness checks
│   └── requirements.txt    # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── api/            # API clients (collections, registres, models, ocr, indexes, tasks, settings, system, transcriptions)
│   │   ├── components/     # Layout + shared UI, plus components/stats/ and components/ocr/
│   │   ├── context/        # React contexts (Loading, Header, Tasks)
│   │   ├── pages/          # Application pages
│   │   ├── i18n/           # i18next setup + locales/{en,fr}/*.json
│   │   ├── types.ts        # TypeScript types
│   │   └── App.tsx         # Main application + MUI theme
│   ├── vite.config.ts      # Build → backend/static/, proxy /api → localhost:38520
│   └── package.json
├── archiMED.spec           # PyInstaller configuration
├── build.ps1               # Windows build script (frontend → PyInstaller)
└── README.md
```

## Data structure

```
data/
├── collections/
│   └── my-collection/
│       ├── metadata.json           # id, type, titre, periode, lieu, commentaire
│       ├── scans/
│       │   └── my-register/
│       │       ├── metadata.json   # titre, periode, pagination stats
│       │       ├── my-register_001.jpg
│       │       └── ...
│       └── ocr/
│           └── my-register/
│               └── my-model/
│                   ├── my-register_001.xml  # OCR results in PAGE-XML format
│                   └── ...
├── models/
│   ├── my-model.mlmodel
│   └── my-model_metadata.json
├── indexes/
│   └── idx_20260115103000_hospital-index/
│       ├── metadata.json           # id, name, sources, status, stats, build progress
│       └── index.json              # words → pages + bounding-box coordinates
├── tasks/
│   ├── <task_id>.json              # Task state (queue, progress, checkpoint)
│   └── control/
│       └── <task_id>.json          # Command posted by another PC (cancel / pause / resume)
├── locks/                          # Scope locks held by a running task (multi-PC)
└── settings.json                   # OCR settings (UI overrides)
```

> **Per-machine identity.** In a multi-PC deployment (see [below](#multi-pc-deployment))
> the human-readable machine label and operator are stored **locally on each PC**
> (`%LOCALAPPDATA%\ArchiMED\identity.json`), never in the shared `data/` folder.

## Background tasks

Long-running work — OCR runs and index builds — goes through a single generic task engine
(`task_service.py`, `TaskService`) rather than FastAPI `BackgroundTasks`:

- A **persistent queue**: task state is written to disk (atomically — see below) so that tasks
  survive a restart and are **resumed on startup** (`load_on_startup()` in `main.py`'s startup hook).
- **Pause / resume / cancel** support, exposed through the `/api/tasks` router and the
  frontend Tasks page + global task widget. Both runners resume from a checkpoint rather than
  starting over: index builds use `checkpoint.json`, OCR runs use the `page_states` array persisted
  with the task (`0` to do, `1` done, `2` failed, `3` in flight) plus the PAGE-XML already on disk.
- **Pluggable runners**: `ocr_service` registers the `'ocr'` runner and `index_runner`
  registers the `'index'` runner (imported for their side effects in `main.py`).
- A **supervisor thread** (one per process) refreshes the heartbeat of this machine's running
  tasks every few seconds and applies remote commands. The heartbeat cannot ride on the runner's
  own progress writes: a single OCR page on CPU — or the preflight that imports torch and loads
  models off the NAS — can easily exceed the staleness threshold, which would let another machine
  declare the task dead and steal its scope lock.

An OCR run also publishes each register as soon as its last page has been attempted: the
register's `ocr_status` is refreshed in the collection metadata
(`CollectionsService.refresh_registre_ocr_status`, a targeted update — not the full
`sync_collection_metadata`) and its name is appended to the task's `registres_done`, which the OCR
page watches to reload its counters mid-run.

## Multi-PC deployment

ArchiMED is designed so the executable can live on a shared drive (e.g. a NAS) and be
launched from several PCs. Each PC runs its **own** backend, but they all read and write the
**same** `data/` folder. To tell the machines apart, every task is stamped with a
`machine_id` derived from the Windows computer name (`COMPUTERNAME`). Only the readable label
and operator name are stored locally per PC (see the note above); nothing machine-specific is
written to the shared `data/`.

Since every machine both reads and writes that folder, three rules keep them consistent:

- **Atomic writes.** Task files (`data/tasks/<id>.json`) and collection metadata are written to a
  temporary file then `os.replace`d. Writing in place would expose a truncated JSON to the other
  machines polling it — and a decode error makes a perfectly live scope lock look stale, hence
  reusable.
- **Liveness by heartbeat.** A `running` task whose heartbeat is older than `HEARTBEAT_STALE`
  (60 s) is considered dead: its scope is released and its lock in `data/locks/` can be reclaimed.
  The supervisor thread keeps that heartbeat fresh independently of the runner's pace, so a slow
  page never causes two machines to OCR the same register.
- **Commands, not direct writes.** A machine never writes into the task file of a *running* task it
  does not own — the owner would overwrite it at its next heartbeat. It drops a command in
  `data/tasks/control/<task_id>.json` (`cancel` / `pause` / `resume`) instead, which the owner picks
  up and applies. Tasks left behind by a machine that is off (queued, paused, interrupted) are still
  finalised directly, so an orphan never blocks anyone.

## Configuration

### Backend (`.env`)
```
HOST=0.0.0.0
PORT=38520
DATA_DIR=../data
```

- `HOST` — bind address. Note: the code **defaults to `127.0.0.1`** (local only); the shipped
  `.env.example` sets `0.0.0.0` (all interfaces). Set it explicitly to be safe.
- `PORT` — HTTP port (default `38520`).
- `DATA_DIR` — location of the `data/` folder. When frozen (PyInstaller) it defaults to the
  folder next to the executable; in development it defaults to the project root.

### OCR settings (env + `data/settings.json`)

OCR performance settings resolve in the order **UI > environment > default**. They can be set
from the Settings page (stored in `data/settings.json`) or via environment variables:

- `OCR_WORKERS` — pages processed in parallel (default: adaptive, `max(1, min(4, cores/2))`)
- `OCR_THREADS_PER_WORKER` — torch threads per worker (default `1`)
- `OCR_MIXED_PRECISION` — enable mixed precision (default off)
- `OCR_POOL_MIN_PAGES` — minimum pages before using a worker pool (default `3`)

### Frontend (`.env`)
```
VITE_API_URL=http://localhost:38520
```

## Technology stack

- **Backend**: FastAPI 0.109, Python 3.10+, Pydantic v2, Uvicorn 0.27, Kraken 4.0+,
  PyTorch 2.11 (CUDA 12.8 build for GPU acceleration), torchvision 0.26,
  RapidFuzz (fuzzy search)
- **Frontend**: React 19, TypeScript, Vite 7, Material-UI (MUI) v7, `@mui/x-charts` v9,
  React Router v7, i18next / react-i18next, Axios
- **Styling**: Material-UI, CSS-in-JS (Emotion)
- **Desktop**: PyInstaller, pystray (Windows system-tray icon)
- **OCR format**: PAGE-XML (Kraken / eScriptorium)

## Development notes

### Backend
- Services read/write JSON and XML files directly under the `data/` folder
- Metadata is stored in `metadata.json` files
- Index generation parses the PAGE-XML files, filters French stopwords, and stores
  bounding-box coordinates; search supports multiple terms, a year range, and optional
  fuzzy matching (RapidFuzz)
- Layered architecture: HTTP routers → business services → files
- Long-running work runs through the [task engine](#background-tasks), not FastAPI
  `BackgroundTasks`

### Frontend
- Modular architecture with a clear API / Components / Pages / Context separation
- Strict TypeScript types (`types.ts`) for the shared domain models; API-specific types live
  next to each `api/*.ts` client
- Local state management with React hooks and a few contexts (Loading, Header, Tasks)
- Bilingual UI: translations live in `src/i18n/locales/{en,fr}/*.json`; the chosen language
  is stored per workstation in `localStorage` (`archimed_lang`), never in `data/`
- The built frontend is served directly by FastAPI in production (`backend/static/`)

## Roadmap

1. **Authentication** — user management for a multi-user deployment
2. **Tests** — backend unit tests and frontend integration tests
3. **Server deployment** — Linux support for a production deployment outside Windows
   (see [DISTRIBUTION.md](DISTRIBUTION.md))
