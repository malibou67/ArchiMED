# API Reference — ArchiMED

The REST API is documented automatically (Swagger): once the server is running, open
**http://localhost:38520/docs**. This document summarises the **main** endpoints of API
v1.0.0; refer to Swagger for the full, exhaustive contract.

All endpoints are served under `/api`. There is also a top-level health check:

- `GET /health` — liveness probe, returns `{"status": "ok"}`

## Models
- `GET /api/models/` — List all models (OCR and segmentation)
- `GET /api/models/{model_id}` — Model details
- `POST /api/models/` — Upload a new model (multipart, `.mlmodel`)
- `PUT /api/models/{model_id}` — Update metadata
- `DELETE /api/models/{model_id}` — Delete a model
- `POST /api/models/extract-metadata` — Extract metadata from an uploaded `.mlmodel` (Kraken introspection)

## Collections
- `GET /api/collections/` — List all collections
- `GET /api/collections/{collection_id}` — Get one collection
- `POST /api/collections/` — Create a collection
- `PUT /api/collections/{collection_id}` — Update a collection
- `POST /api/collections/{collection_id}/sync` — Rebuild one collection's registers from the filesystem
- `GET /api/collections/scan` — Scan `data/collections` and report the status of every collection/register
- `GET /api/collections/scan/stream` — Same scan as an NDJSON progress stream
- `POST /api/collections/sync-all` — Rebuild the register lists of every collection
- `POST /api/collections/sync-all/stream` — Same as an NDJSON progress stream
- `GET /api/collections/{collection_id}/stats` — Progress statistics (OCR coverage, pages per decade…)

## Registers
- `GET /api/registres/` — List all registers across every collection
- `GET /api/registres/{collection_id}` — List a collection's registers
- `GET /api/registres/{collection_id}/{registre_id}` — Get one register
- `POST /api/registres/{collection_id}` — Create a register
- `PUT /api/registres/{collection_id}/{registre_id}` — Update register metadata
- `GET /api/registres/{collection_id}/{registre_id}/pages` — List the scanned page filenames (sorted)
- `GET /api/registres/{collection_id}/{registre_id}/pages/{filename}` — Serve a scan image
- `GET /api/registres/{collection_id}/{registre_id}/transcriptions` — Map each image to the models that transcribed it

## OCR
- `POST /api/ocr/run` — Enqueue a Kraken OCR task (segmentation model + OCR model + an explicit list of pages); pages whose image is missing from disk (pagination gap, file moved since the last sync) are dropped and counted in `skipped_missing`, and the call returns `400` if none is left; returns `409` if a conflicting task is already running
- `GET /api/ocr/done` — Stems of the pages already transcribed for a (collection, register, model)
- `POST /api/ocr/missing` — Pages still lacking a transcription for a model (optional scope)

## Transcriptions
- `GET /api/transcriptions/` — List all transcriptions, with optional `collection_id` / `registre_id` filters
- `GET /api/transcriptions/summary` — Quick summary by collection/register/model
- `GET /api/transcriptions/stats` — Aggregated totals by collection and by model

## Search indexes
- `GET /api/indexes/` — List all indexes
- `GET /api/indexes/{index_id}` — Index metadata and status
- `POST /api/indexes/generate` — Enqueue a (multi-source) index build (background task)
- `POST /api/indexes/preview` — Preview the registers/pages of the selected sources before building
- `POST /api/indexes/{index_id}/regenerate[?full=true]` — Update an existing index from its own sources. Incremental by default (only new or modified registers are re-read); `full=true` reindexes everything
- `PATCH /api/indexes/{index_id}` — Rename or change the sources (re-enqueues a **full** build if sources change)
- `DELETE /api/indexes/{index_id}` — Delete an index
- `GET /api/indexes/available-models` — OCR models available for a collection (`?collection_id=…`)
- `GET /api/indexes/updates[?rescan=true]` — Coverage **and** freshness of every *ready* index:
  `{id, new_registres, new_pages, coverage_known, indexed_pages, ocr_pages, stale_pages, rescanned, sources[]}`.
  By default the OCR counters come from each collection's published `ocr_status` (a few milliseconds);
  `rescan=true` walks the OCR folders instead — authoritative even for XML dropped outside the app, but
  seconds-slow, and what the "Refresh the list" button sends. Indexes built before coverage tracking
  report `coverage_known: false`
- `GET /api/indexes/{index_id}/search?q=…&year_from=…&year_to=…&fuzzy_threshold=…` — Full-text search (multiple terms, optional year range and fuzzy matching)
- `GET /api/indexes/{index_id}/year-range` — Min/max year covered by the index
- `GET /api/indexes/{index_id}/words` — Paginated vocabulary (filter / sort / stopwords / min occurrences)
- `GET /api/indexes/{index_id}/words/{word}/pages` — Every page (with bounding-box coordinates) where a word occurs
- `GET /api/indexes/{index_id}/page-image/{page_name}` — Image of a matched page
- `GET /api/indexes/{index_id}/stats/corpus` — Corpus statistics (top words, per register, per decade)
- `GET /api/indexes/{index_id}/stats/term-frequency` — Term evolution over time (per decade)
- `GET /api/indexes/{index_id}/stats/quality` — OCR quality indicators
- `GET /api/indexes/{index_id}/export-results.csv` — Export search results as CSV
- `GET /api/indexes/{index_id}/export-pages.zip` — Export matched page images as a streamed ZIP
- `GET /api/indexes/{index_id}/words/export` — Export the full vocabulary as CSV

## Tasks
The background task engine (OCR and index builds). See [ARCHITECTURE.md](ARCHITECTURE.md).
- `GET /api/tasks/summary` — Lightweight running/pending summary for the global widget
- `GET /api/tasks` — All tasks (running, pending, history)
- `GET /api/tasks/{task_id}` — Full detail of one task
- `POST /api/tasks/{task_id}/cancel` — Cancel a task
- `POST /api/tasks/{task_id}/pause` — Pause a task (OCR and index builds alike; resumes from its checkpoint)
- `POST /api/tasks/{task_id}/resume` — Resume a paused or interrupted task
- `POST /api/tasks/{task_id}/retry-failed` — Re-queue the failed pages of a finished OCR task as a **new** task (a resume only redoes the pages still *to do*, never the failed ones). `400` if the task is not an OCR task, has no failed page, or none of them still has an image on disk; `409` if the task is not finished (`done`/`error`/`cancelled`) or its scope is busy
- `DELETE /api/tasks/{task_id}` — Remove a task (`409` if it is still running)

> The three control endpoints answer `{ "<action>": bool, "requested": bool, "machine_label": str|null, "task": {…} }`.
> `requested: true` means the task belongs to **another machine**: the command was posted to it and
> takes effect at its next poll (a few seconds), so the returned state is still the old one.
> See [Multi-PC deployment](ARCHITECTURE.md#multi-pc-deployment).

- `GET /api/tasks/{task_id}/pages` — Per-page detail (OCR tasks)
- `GET /api/tasks/{task_id}/registres` — Per-register state (index tasks)

## Settings
- `GET /api/settings` — Stored settings, effective values (UI > env > default) and system info (CPU, RAM, disk)
- `PUT /api/settings` — Update OCR settings (`ocr_workers`, `ocr_threads_per_worker`, `ocr_mixed_precision`, `ocr_pool_min_pages`); a `null` value resets the key to its default

## System
- `GET /api/system/requirements` — Check that the environment can run Kraken OCR (Kraken, torch, torchvision, CUDA)
- `GET /api/system/storage` — Health of the `data/` folder
- `GET /api/system/identity` — This machine's identity (`machine_id` + label/operator)
- `PUT /api/system/identity` — Update this machine's local label/operator
