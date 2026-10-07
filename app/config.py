from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")).expanduser().resolve()
DATABASE_PATH = Path(
    os.getenv("DATABASE_PATH", DATA_DIR / "geospatial.sqlite3")
).expanduser().resolve()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
MAX_UNZIPPED_BYTES = int(os.getenv("MAX_UNZIPPED_BYTES", 200 * 1024 * 1024))
MAX_ZIP_ENTRIES = int(os.getenv("MAX_ZIP_ENTRIES", 1_000))
MAX_FEATURES = int(os.getenv("MAX_FEATURES", 100_000))
APP_VERSION = "1.0.0"
