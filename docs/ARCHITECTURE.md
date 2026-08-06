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
├── data/                   # User data (see DATA_DIR; contents not versioned)
│   ├── collections/        # Collections with scans and OCR results (XML)
│   ├── models/             # Kraken OCR models (.mlmodel + <id>_metadata.json)
│   ├── indexes/            # Generated full-text search indexes
│   ├── tasks/              # Background task state (+ tasks/control/)
│   ├── locks/              # Scope locks held by a running task
│   ├── logs/               # Operation log, one subfolder per machine
│   └── settings.json       # OCR settings set from the UI
├── backend/
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
│   ├── app_logging.py      # Per-machine log file (setup, kv formatting)
│   ├── system_checks.py    # Kraken/torch readiness checks
│   ├── tests/              # pytest suite (see Roadmap)
│   ├── .env.example        # HOST / PORT / DATA_DIR
│   ├── requirements.txt    # Python dependencies
│   └── requirements-dev.txt # Test-only dependencies (pytest)
├── frontend/
│   ├── src/
│   │   ├── api/            # API clients (collections, registres, models, ocr, indexes, tasks, settings, system)
│   │   ├── components/     # Layout + shared UI, plus components/stats/ and components/ocr/
│   │   ├── context/        # React contexts (Loading, Header, Tasks)
│   │   ├── pages/          # Application pages
│   │   ├── i18n/           # i18next setup + locales/{en,fr}/*.json
│   │   ├── types.ts        # TypeScript types
│   │   └── App.tsx         # Main application + MUI theme
│   ├── vite.config.ts      # Build → backend/static/, proxy /api → localhost:38520
│   ├── eslint.config.js
│   └── package.json
├── archiMED.spec           # PyInstaller configuration — local, NOT versioned
├── build.ps1               # Windows build script — local, NOT versioned
└── README.md
```

> `archiMED.spec` and `build.ps1` are excluded by `.gitignore`: they exist on the build
> machine but not in a fresh clone. See [DISTRIBUTION.md](DISTRIBUTION.md).

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
│       ├── metadata.json           # id, name, sources, status, stats, build progress,
│       │                           # coverage + index_state (per-register fingerprints)
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
  starting over: index builds use `checkpoint.json` (which records its `mode`, `full` or
  `incremental`, so that an explicit full rebuild never inherits a half-done incremental update),
  OCR runs use the `page_states` array persisted with the task (`0` to do, `1` done, `2` failed,
  `3` in flight) plus the PAGE-XML already on disk.
- **Pluggable runners**: `ocr_service` registers the `'ocr'` runner and `index_runner`
  registers the `'index'` runner (imported for their side effects in `main.py`).
- A **supervisor thread** (one per process) refreshes the heartbeat of this machine's running
  tasks every few seconds and applies remote commands. The heartbeat cannot ride on the runner's
  own progress writes: a single OCR page on CPU — or the preflight that imports torch and loads
  models off the NAS — can easily exceed the staleness threshold, which would let another machine
  declare the task dead and steal its scope lock.

Two details of an OCR run are worth spelling out:

- **Pages are checked against the disk before being enqueued.** The page list is built by the
  frontend from the *detected* pagination, so it can name files that do not exist (a gap in the
  numbering, a scan moved since the last sync). `OcrService.enqueue` drops those — one directory
  listing per register — and reports the count as `skipped_missing`; without it every phantom
  page would be transcribed for nothing and land in the `failed` bucket.
- **PAGE-XML files are written to a temporary file then `os.replace`d, with retries.** The
  atomicity is what lets a killed worker leave nothing behind that `done_stems` would count as a
  transcribed page; the retries (`_write_xml_atomic`, ~3 s of escalating backoff) absorb the
  `WinError 32` that antivirus or the Windows indexer cause on a network share by briefly holding
  the freshly written file open.

A resume redoes only the pages still *to do*: the ones recorded as `failed` are deliberately not
retried automatically. `POST /api/tasks/{id}/retry-failed` re-queues them as a **new** task, which
leaves the original task — possibly owned by another machine — untouched.

An OCR run also publishes each register as soon as its last page has been attempted: the
register's `ocr_status` is refreshed in the collection metadata
(`CollectionsService.refresh_registre_ocr_status`, a targeted update — not the full
`sync_collection_metadata`) and its name is appended to the task's `registres_done`, which the OCR
page watches to reload its counters mid-run.

That published `ocr_status` is also what feeds the Indexes page: `GET /api/indexes/updates` reports
each index's coverage (`indexed_pages / ocr_pages`) by reading one metadata file per collection —
about half a millisecond — instead of walking the OCR folders, which costs over a second per
(collection, model) on a cold cache. Being *published* rather than observed, it can lag behind: XML
copied or deleted outside the app, an OCR process killed before its cleanup, or a register present
in `ocr/` but absent from `registres[]`. The consequence is purely cosmetic — index generation never
consults `ocr_status`, its first pass really scans the folders, so an update never misses pages
whatever the badge showed. "Refresh the list" (`?rescan=true`) forces the real scan.

The runner **never** calls `sync_collection_metadata` when it finishes, only the same targeted
refresh for registers a cancellation or an error left half-done. A task is only marked `done`
once its runner returns, so any work in that path delays the status: rebuilding a whole
collection means relisting every register over the network — measured at 2.5 s per register,
about 16 minutes for the 383 registers of a real collection — and an OCR run changes nothing
that rebuild recomputes (pagination, page counts, anomalies), only the XML files under `ocr/`.
Rebuilding a collection stays an explicit action of the Collections page, where progress is
streamed.

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
  reusable. This is also what makes published counters usable across machines: the PC running an
  OCR rewrites the shared collection metadata register by register, so the others read its
  `ocr_status` instead of listing thousands of files over SMB (see the Indexes page above). Caches
  built on it must therefore stay scoped to a single request — a longer-lived one would hide what
  another machine just published.
- **Liveness by heartbeat.** A `running` task whose heartbeat is older than `HEARTBEAT_STALE`
  (60 s) is considered dead: its scope is released and its lock in `data/locks/` can be reclaimed.
  The supervisor thread keeps that heartbeat fresh independently of the runner's pace, so a slow
  page never causes two machines to OCR the same register.
- **Commands, not direct writes.** A machine never writes into the task file of a *running* task it
  does not own — the owner would overwrite it at its next heartbeat. It drops a command in
  `data/tasks/control/<task_id>.json` (`cancel` / `pause` / `resume`) instead, which the owner picks
  up and applies. Tasks left behind by a machine that is off (queued, paused, interrupted) are still
  finalised directly, so an orphan never blocks anyone.

### Logs

Each machine writes its own file, `data/logs/<machine_id>/archimed.log` — a single writer per
file, so nothing interleaves and no two processes fight over the rotation rename, yet every
machine's log is readable from any PC. That last point is what makes remote diagnosis possible
at all: each backend binds `127.0.0.1`, so there is no cross-machine HTTP, and the shared folder
is the only common ground. If `data/` is unreachable the log falls back to
`%LOCALAPPDATA%\ArchiMED\logs\` — an unreachable NAS is exactly when the log is needed.

Format is plain text plus `key=value` fields (`app_logging.kv`), openable in Notepad straight
from the NAS. Timestamps carry their UTC offset, unlike the naive `datetime.now()` used
elsewhere: files from several machines are meant to be read side by side. Rotation caps each
machine at 5 MB × 5 files ≈ 25 MB, which is the entire cleanup story — there is no age-based
purge to run.

What gets logged: process lifecycle, task lifecycle (created / started / finished with status,
duration and counts), OCR preflight, per-page failures, index runs, and the multi-machine
events that are otherwise invisible — refused creations, stale locks reclaimed, control
commands sent and received, failed writes to the shared folder. Successful OCR pages are
deliberately **not** logged (thousands of lines per register); one summary per finished
register is emitted instead. `setup()` is a no-op in child processes, because the OCR pool
workers re-execute the executable and a second writer would corrupt rotation.

**Confidentiality.** The registers are hospital archives: transcribed text and patient names
must never reach the log. Only identifiers — register, page number, file name, model.

## Configuration

### Backend (`.env`)
```
HOST=0.0.0.0
PORT=38520
DATA_DIR=../data
LOG_LEVEL=INFO
```

- `HOST` — bind address. Note: the code **defaults to `127.0.0.1`** (local only); the shipped
  `.env.example` sets `0.0.0.0` (all interfaces). Set it explicitly to be safe.
- `PORT` — HTTP port (default `38520`).
- `DATA_DIR` — location of the `data/` folder. When frozen (PyInstaller) it defaults to the
  folder next to the executable; in development it defaults to the project root.
- `LOG_LEVEL` — log verbosity (default `INFO`). `DEBUG` adds the shared-folder read retries,
  which is what you want when investigating a flaky NAS. An unrecognised value falls back to
  `INFO` rather than preventing startup.

### OCR settings (env + `data/settings.json`)

OCR performance settings resolve in the order **UI > environment > default**. They can be set
from the Settings page (stored in `data/settings.json`) or via environment variables:

- `OCR_WORKERS` — pages processed in parallel (default: adaptive, `max(1, min(4, cores/2))`)
- `OCR_THREADS_PER_WORKER` — torch threads per worker (default `1`)
- `OCR_MIXED_PRECISION` — enable mixed precision (default off). Environment only: the toggle
  was removed from the Settings page, even though the key is still accepted by `PUT /api/settings`
- `OCR_POOL_MIN_PAGES` — minimum pages before using a worker pool (default `3`)
- `OCR_GPU_VRAM_PER_WORKER_GB` — VRAM budget per CUDA worker (default `2`)
- `OCR_GPU_VRAM_RESERVE_GB` — VRAM left to the desktop and to segmentation peaks (default `1.5`)

**On GPU, VRAM caps the parallelism, not the core count.** The cost is neither the weights
(22 MB) nor the CUDA context (20 MB) but the segmentation activations, proportional to image
area: **1.7 GB per worker measured on 25 Mpx scans**. Once the card is full the NVIDIA driver
spills into system memory — the GPU still reports 100 % while throughput drops tenfold.

Benchmark, 12 pages of 25 Mpx on a 12 GB RTX 3060:

| Workers | Throughput | Speed-up | Page latency | Peak VRAM |
|---|---|---|---|---|
| 1 | 12.94 s/page | ×1.00 | 12.2 s | 2.8 GB |
| 4 | 4.63 s/page | ×2.79 | 14.3 s | 8.0 GB |
| 6 | 4.18 s/page | ×3.09 | 16.9 s | 11.3 GB |
| 8 | 48 s/page | collapse | 92 s | 12.0 GB (saturated) |

Gains flatten past ~4 workers (page latency rises) while the risk of saturation grows, so the
default targets ~80 % of VRAM: `run_ocr_task` caps the requested workers to
`(vram_gb - reserve) // per_worker` — 5 on that card. Lower `OCR_GPU_VRAM_PER_WORKER_GB` to
push the cap up on bigger cards or smaller scans. The cap is applied **at run time on each
machine**, never when saving the setting, because `data/settings.json` is shared across
machines with different GPUs. A capped run records `workers_requested` / `workers_cap_reason`
in its preflight, shown on the Tasks page.

### Frontend

Nothing to configure: the dev proxy (`/api` → `localhost:38520`) and the build output
(`../backend/static/`) are both hard-coded in `frontend/vite.config.ts`, and the Axios
instance uses an empty `baseURL` since production is same-origin.

## Technology stack

- **Backend**: FastAPI 0.109, Python 3.10+, Pydantic v2, Uvicorn 0.27, Kraken 7.0,
  PyTorch 2.10 (CUDA 12.8 build for GPU acceleration), torchvision 0.25,
  RapidFuzz (fuzzy search) — pinned versions in `backend/requirements.txt`
- **Frontend**: React 19, TypeScript, Vite 7, Material-UI (MUI) v7, `@mui/x-charts` v9,
  React Router v7, i18next / react-i18next, Axios, JSZip (ZIP export)
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
- **Updating an index is incremental.** Each build records a per-register fingerprint
  (`index_state` in `metadata.json`: file names, sizes and mtimes, hashed with blake2b) plus the
  signature of its sources. A later update reloads the existing `index.json`, purges only the
  registers that are new, modified or gone, and reindexes those — the rest is kept as is, and
  shows up as already-done progress. Anything doubtful (no `index_state`, unknown version,
  changed sources, unreadable `index.json`) falls back to a full rebuild, as does an explicit
  “full rebuild” from the UI or `?full=true`. Since no existing index carries an `index_state`,
  the first update after this feature ships is necessarily a full rebuild — it is the one that
  writes the state. Note that a register missing from disk counts as deleted, so an unavailable
  collection (disconnected network share) has its pages purged, exactly as a full rebuild would
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
2. **Tests** — `backend/tests/` covers incremental index updates (fingerprints, purge, resume)
   and the coverage/freshness of `GET /api/indexes/updates`
   (`pip install -r backend/requirements-dev.txt`, then `python -m pytest backend/tests`).
   Everything else — OCR pipeline, task engine, routers, frontend — is still untested
3. **Server deployment** — Linux support for a production deployment outside Windows
   (see [DISTRIBUTION.md](DISTRIBUTION.md))
