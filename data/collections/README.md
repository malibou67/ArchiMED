# collections folder

Each collection is a subfolder whose name is the collection identifier
(e.g. `my-collection`).

## Expected structure

```
collections/
└── <COLLECTION_NAME>/
    ├── metadata.json          → collection metadata (created by the app)
    ├── scans/                 → digitized register images
    │   └── <REGISTER_NAME>/   → e.g. my-register
    │       ├── metadata.json  → register metadata
    │       ├── page_001.jpg   → page scans (jpg/jpeg/png/tif/tiff)
    │       └── page_002.jpg
    └── ocr/                   → XML transcriptions produced by Kraken
        └── <REGISTER_NAME>/
            └── <MODEL_NAME>/  → name of the OCR model used, e.g. my-model
                ├── page_001.xml
                └── page_002.xml
```

## metadata.json format (collection)

```json
{
  "id": "my-collection",
  "type": "hospital",
  "titre": "Hospital registers",
  "periode": ["1830-01-01", "1975-12-31"],
  "lieu": "Strasbourg",
  "commentaire": "..."
}
```

The app also adds a `registres` array (populated on sync); you do not need to write it by hand.

## metadata.json format (register)

```json
{
  "id": "my-register",
  "titre": "Register 25",
  "periode": ["1961-01-01", "1961-12-31"],
  "pagination": {
    "pattern": "my-register_{num}.jpg",
    "start": 1,
    "end": 120
  }
}
```
