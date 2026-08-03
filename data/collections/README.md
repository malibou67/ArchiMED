# collections folder

Each collection is a subfolder of `collections/` (e.g. `my-collection`). The folder name is
free: it identifies the collection on disk, while the `id` field of its `metadata.json` is a
separate identifier that may differ (a hand-written `col_001` is perfectly valid). The API
accepts either one. Same rule for a register: folder name on one side, `id` on the other —
if a register's `metadata.json` has no `id`, synchronization sets it to the folder name.

## Expected structure

```
collections/
└── <COLLECTION_FOLDER>/
    ├── metadata.json                 → collection metadata (created by the app)
    ├── scans/                        → digitized register images
    │   └── <REGISTER_FOLDER>/        → e.g. my-register
    │       ├── metadata.json         → register metadata
    │       ├── my-register_1.jpg     → page scans (jpg/jpeg/png/tif/tiff)
    │       ├── my-register_2.jpg
    │       └── my-register_2_1.jpg   → insert after page 2
    └── ocr/                          → XML transcriptions produced by Kraken
        └── <REGISTER_FOLDER>/
            └── <MODEL_NAME>/         → name of the OCR model used, e.g. my-model
                ├── my-register_1.xml
                └── my-register_2.xml
```

**Naming**: the page number is the last number of the file name, right after an underscore
(`my-register_2.jpg` → page 2); every file of a register shares the same prefix; an insert
page adds a second underscore and a suffix (`my-register_2_1.jpg`). Each `.xml` keeps the
**same base name** as its image. The full rules are in the
[main README](../../README.md#adding-your-documents).

## metadata.json format (collection)

```json
{
  "id": "col_001",
  "type": "hospital",
  "titre": "Hospital registers",
  "periode": ["1830-01-01", "1975-12-31"],
  "lieu": "Strasbourg",
  "commentaire": "..."
}
```

The app also adds a `registres` array (populated on sync); you do not need to write it by
hand. Each entry summarises one register as observed on disk: `folder_name`, `pages_count`,
the detected pagination (`pages_pattern`, `pages_start`, `pages_end`, plus any `pages_gaps` /
`pages_duplicates`), the OCR progress per model (`ocr_status`) and the `anomalies` reported by
the scan.

## metadata.json format (register)

```json
{
  "id": "reg_001",
  "titre": "Register 25",
  "periode": ["1961", "1961"],
  "pagination": {
    "pattern": "my-register_{num}.jpg",
    "start": 1,
    "end": 120
  },
  "extra_pagination": {
    "pattern": "my-register_{num}_{extra_page}.jpg"
  },
  "stats": {
    "total_pages": 120,
    "total_files": 143
  }
}
```

`pagination` describes the regular pages, `extra_pagination` the inserts; `stats` counts the
pages (`total_pages`) against the actual number of files (`total_files`, inserts included).
All three are filled in by synchronization — `titre` and `periode` are the only fields
normally worth editing by hand.
