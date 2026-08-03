# models folder

Holds the Kraken OCR models used to transcribe the registers.

## Expected structure

```
models/
├── <model_id>.mlmodel            → Kraken model file
└── <model_id>_metadata.json      → associated metadata (created by the app)
```

## Example

```
models/
├── my-model.mlmodel
└── my-model_metadata.json
```

## metadata.json format

```json
{
  "id": "my-model",
  "name": "My model",
  "type": "ocr",
  "description": "Model trained on 19th-century registers",
  "version": "1.0",
  "created_at": "2026-01-15T10:30:00",
  "trained_on": "...",
  "accuracy": 0.95,
  "file_path": "/path/to/my-model.mlmodel"
}
```

`type` is either `"ocr"` (text recognition) or `"segmentation"` (line/region detection).

## How to add a model

`.mlmodel` files can be imported directly from the interface (the "OCR models" page),
or copied manually into this folder.
