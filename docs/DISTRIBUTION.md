# Distribution / publication ideas for ArchiMED

> Design notes (June 2026) on how to make ArchiMED available to the public.
> **Nothing has been implemented yet** — this file lists the analysis and the steps to take
> when the time comes.

Target audiences: non-technical general public (genealogists, archivists — Windows **and**
macOS), technical users (Docker/Linux), and possibly an online hosting option. Users do not
necessarily have an NVIDIA GPU.

---

## Current state (what makes distribution easier)

- **Fully self-contained app**: no external API (no OpenAI/Mistral), local Kraken OCR.
  All data is JSON/XML files under `data/` — no database, so a backup is just a copy of the
  folder.
- **Windows desktop build already works**: `build.ps1` → PyInstaller → `dist/ArchiMED/ArchiMED.exe`
  with a system-tray icon. This is already the simplest channel for the Windows general
  public; only publication is missing.
- **Weight**: `requirements.txt` forces the torch CUDA 12.8 build (~2.7 GB). A CPU torch build
  is ~200 MB and is enough for consultation/search (OCR is ~100× slower there but still works).
- **Code is already server-portable**: `pystray` (Windows/desktop) is imported only inside the
  `__main__` block of `backend/main.py` → `uvicorn main:app` runs as-is under Linux/Docker,
  with no code changes.
- **A single service to deploy**: FastAPI serves the built frontend from `backend/static/`
  (Vite `outDir: '../backend/static'`) → same origin, no CORS.
- Configuration via environment variables: `HOST`, `PORT` (38520), `DATA_DIR`.

---

## Recommendation: three channels, in order of ease for the user

### 1. Windows + macOS binaries on GitHub Releases (general public) ⭐ priority

The user downloads a zip, unzips it, double-clicks. No installation.

- **Windows**: already ready (`build.ps1`). Publish the zip of `dist/ArchiMED/` in a GitHub
  release.
- **macOS**: impossible to compile from Windows → go through **GitHub Actions** (free macOS
  runners for a public repo). The code is portable: pystray supports macOS, CPU torch exists on
  PyPI for arm64, and the `--extra-index-url cu128` index is safely ignored on Mac (pip falls
  back to the PyPI build).
- **Lightweight CPU builds by default** (~10× smaller); the Windows CUDA variant remains
  buildable locally via `build.ps1` and can be added by hand to a release
  (`ArchiMED-windows-cuda.zip`).
- **Caveat — unsigned binaries**: SmartScreen (Windows) and Gatekeeper (macOS) will show a
  warning on first launch. Workaround to document: right-click → Open (macOS) / "More info →
  Run anyway" (Windows). Apple signing/notarization costs $99/year — to consider later.

### 2. Docker (technical users, Linux/server)

`docker compose up -d` then open `http://localhost:38520`. Also works on Mac/Windows with
Docker Desktop.

- Multi-stage `Dockerfile`: stage 1 `node:22-slim` (npm ci + build the frontend →
  `backend/static/`), stage 2 `python:3.12-slim` (CPU requirements, copy backend + static).
- Default env in the image: `HOST=0.0.0.0`, `PORT=38520`, `DATA_DIR=/data`; `EXPOSE 38520`,
  `HEALTHCHECK` on `/health`.
- CMD: `uvicorn main:app --host 0.0.0.0 --port 38520` (definitely not `python main.py`, which
  triggers systray + browser opening).
- `docker-compose.yml`: a single service, port `38520:38520`, volume `./data:/data`. NVIDIA
  GPU option as a commented block (`deploy.resources.reservations.devices`), which requires
  rebuilding with the CUDA requirements.

### 3. Online hosting (later, not as-is)

Technically possible (the container runs on any VPS), **but the app has no authentication**:
any visitor could create/delete collections and launch OCR jobs. Reserve this for
private/LAN use until an account system exists. A Heroku-style PaaS is not viable (PyTorch too
heavy, not enough RAM). If it ever happens: a classic VPS for consultation (CPU is enough), a
GPU instance only for multi-user OCR (expensive, ~$0.5–2/h).

### To avoid

Making people install Python + npm + CUDA manually (the current README installation): keep
this only as a development procedure.

---

## Steps to take when the time comes (checklist)

- [ ] `backend/requirements-cpu.txt`: a copy of `requirements.txt` with
      `--extra-index-url https://download.pytorch.org/whl/cpu` and without `pystray`.
- [ ] `Dockerfile` multi-stage + `docker-compose.yml` + `.dockerignore`
      (exclude `data/`, `dist/`, `node_modules/`, `.venv`, `*.log`, `old_scripts/`, `.git`).
- [ ] `.github/workflows/release.yml`: triggered on a `v*` tag (+ `workflow_dispatch`),
      matrix `windows-latest` / `macos-latest`: npm build → pip install requirements-cpu
      + pystray + pyinstaller → `pyinstaller archiMED.spec` → zips attached to the release.
- [ ] Check that `archiMED.spec` has nothing Windows-only (`.ico` icon, paths).
- [ ] A "Distribution" section in the README (Windows / macOS / Docker / hosting), with the
      SmartScreen/Gatekeeper workarounds.
- [ ] Publication: `git tag v1.0.0 && git push --tags` → CI builds and attaches the zips.
- [ ] Have the macOS zip tested by a Mac user (not testable from Windows).

## Associated verifications (when implementing)

1. `docker build -t archimed .` then `docker compose up` →
   `curl http://localhost:38520/health` returns `{"status":"ok"}`, the UI loads, and the
   collections created show up in `./data` on the host.
2. Mini-collection with one image: CPU OCR runs (slow but functional).
3. CI workflow triggered on a test tag: both zips build; test `ArchiMED-windows.zip` locally
   (systray + UI).
