# ArchiMED

**An automated search engine for OCR/HTR-transcribed documents**

Web platform for text recognition — handwritten (HTR) or printed (OCR) depending on
the model used — and full-text indexing of historical document registers.
Designed to run **entirely locally**, with no external service or cloud dependency.

---

## Overview

ArchiMED supports the scientific exploitation of digitized archive corpora.
Starting from register images, the application applies text recognition models
([Kraken](https://kraken.re/)) — handwritten or printed depending on the chosen
model — produces transcriptions in **PAGE-XML** format, then builds a **full-text
index** enabling keyword search across the entire corpus, with visual rendering of
the exact position of matches on the scans.

The tool is aimed at researchers, archivists, and historians who want to make large
sets of unstructured documents — both handwritten and printed — searchable. It runs
entirely on a local machine: images, transcriptions, and indexes never leave the
host, which makes it well suited to large or sensitive corpora of any kind.

## Key features

- **Full-text search** with multiple terms across the corpus, with filtering by year
  range and result visualization through highlighting (bounding boxes) on the scans.
- **Model management** for Kraken OCR/HTR (`.mlmodel` import, metadata extraction).
- **Corpus organization** into collections and registers, with synchronization from
  the file system.
- **OCR execution** on a register using a selected model.
- **Index generation** on demand (background processing with progress tracking).
- **Transcription review** and aggregated statistics (by collection, register, model).

## Requirements

- **Python** 3.10 or higher (with `pip` and `venv`)
- **Node.js** 18 or higher (with `npm`)
- **Git**
- *(Optional, for GPU acceleration)* an **NVIDIA** GPU with a recent driver
  (`nvidia-smi` must show `CUDA Version` ≥ 12.8)

## Installation

### 1. Get the code

```bash
git clone https://github.com/malibou67/ArchiMED
cd ArchiMED
```

### 2. Backend (FastAPI / Python)

Create and activate a virtual environment, then install the dependencies:

```bash
cd backend
python -m venv venv
# Windows: venv\Scripts\activate   |   Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
```

> **GPU acceleration (optional).** `requirements.txt` pulls the CUDA 12.8 build of
> PyTorch, so Kraken OCR uses an NVIDIA GPU when one is present and falls back to the
> CPU otherwise — no separate CUDA toolkit is needed, just a recent NVIDIA driver.
> Heads-up: the PyTorch download is large (~2.7 GB).

### 3. Frontend (React / Vite)

```bash
cd frontend
npm install
```

### 4. Configuration (optional)

The default values (ports, paths) are suitable for local use. To customize them, see
the `.env` files described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#configuration).

## Quick start

Once installation is complete, launch both servers (in two terminals).

**Backend** — from `backend/`, with the virtual environment activated:

```bash
uvicorn main:app --reload --port 38520
```

Server: http://localhost:38520 — API documentation: http://localhost:38520/docs

**Frontend** — from `frontend/`:

```bash
npm run dev
```

Application: http://localhost:5173 (proxy `/api` → `localhost:38520`).

## Adding your documents

ArchiMED reads everything from the `data/` folder — there is no upload step for scans. You
drop your images into `data/collections/`, then let the app detect them from the
**Collections** page (**Scan**, then **Synchronize**). The same guidance is available in-app
via the **Information** button on that page.

Each **collection** is one folder holding a `scans/` subfolder (and, after OCR, an `ocr/`
subfolder). Each **register** is a folder inside `scans/`:

```
data/collections/
└── my-collection/                     ← one folder = one collection
    ├── scans/
    │   └── my-register/               ← one folder = one register
    │       ├── my-register_1.jpg      ← _1 = page 1 (the number after the last _)
    │       ├── my-register_2.jpg      ← _2 = page 2
    │       └── my-register_2_1.jpg    ← insert after page 2 (suffix _1)
    └── ocr/
        └── my-register/
            └── my-model/              ← one subfolder per OCR model
                └── my-register_1.xml  ← PAGE-XML, same base name as the scan
```

The underscore is what separates the register name from the page number: everything before
the last `_` is the register's fixed prefix, and the number right after it is the page.

**Naming rules**

- The page number must be the **last number** in the file name, placed right after an
  underscore (e.g. `my-register_2.jpg` → page 2).
- Every file of the same register must share **exactly the same prefix**.
- An insert / "extra" page adds a second underscore and a suffix after the page number:
  `<prefix>_<page>_<suffix>.jpg` (e.g. `my-register_2_1.jpg` = an insert after page 2).
- OCR transcriptions go in `ocr/<register>/<model>/` as `.xml` (PAGE-XML) files with the
  **same base name** as the image. The `metadata.json` files are generated automatically on
  sync — no need to write them by hand.
- Accepted image formats: JPG, JPEG, PNG, TIF, TIFF.

Then, from the **Collections** page: **Scan** (read-only diagnosis) → **Synchronize**
(registers the collections and registers) → run **OCR** → **generate an index** →
**search**. See [data/collections/README.md](data/collections/README.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#data-structure) for the full structure.

## Technical documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture, project tree, data
  structure, configuration, development notes.
- [docs/API.md](docs/API.md) — REST API endpoint reference.
- [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) — notes on distribution and publication (roadmap).
- Interactive documentation (Swagger): http://localhost:38520/docs

<br>

---

# About the project

## How to cite

If you use ArchiMED in your work, please cite:

```bibtex
@software{archimed_2026,
  author  = {Veith, Gilles and Zvenigorosky, Vincent},
  title   = {ArchiMED: An automated search engine for OCR/HTR-transcribed documents},
  year    = {2026},
  url     = {https://github.com/malibou67/ArchiMED}
}
```

## Team & partners

- **Christian Bonah** (DHPS) — *Lead*
  - SAGE (UMR 7363), Faculté de médecine, Université de Strasbourg, France
- **Christine Keyser** — *Lead*
  - BABEL Laboratory, CNRS UMR 8045, Paris, France

## Funding

This work was funded as part of the **ArchiMED** project, an **ANR-PRCI** project
supported by the **French National Research Agency (ANR)** (reference
**ANR-23-CE45-0028**) and by the **Swiss National Science Foundation (SNSF)**
(project no. **219457**).
