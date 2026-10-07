from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, status

from app import database
from app.config import APP_VERSION
from app.geospatial import process_upload
from app.schemas import (
    DeleteResponse,
    FeatureCollectionResponse,
    FileList,
    FileSummary,
    MeasurementsResponse,
)

logger = logging.getLogger("geospatial_api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize_database()
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description=(
        "Upload KML or zipped Shapefiles, inspect extracted features, and retrieve "
        "polygon areas and line lengths."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"features", "measurements"}}


def _load_or_404(file_id: str, loader: Any) -> dict[str, Any]:
    try:
        record = loader(file_id)
    except Exception as exc:
        logger.exception("Could not read file record")
        raise HTTPException(status_code=500, detail="The file record could not be read.") from exc
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "service": "Geospatial File Measurement API",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["system"], summary="Health check")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/files/",
    response_model=FileSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["files"],
    summary="Upload and process a geospatial file",
)
async def upload_file(file: UploadFile = File(..., description="A .kml file or ZIP containing one Shapefile")) -> dict[str, Any]:
    processed = await process_upload(file)
    record = {
        "id": uuid4().hex,
        **processed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        database.create_file(record)
    except Exception as exc:
        logger.exception("Could not persist processed file")
        raise HTTPException(status_code=500, detail="The processed file could not be saved.") from exc
    return _summary(record)


@app.get("/api/files/", response_model=FileList, tags=["files"], summary="List uploaded files")
def get_files(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        items, total = database.list_files(limit, offset)
    except Exception as exc:
        logger.exception("Could not list file records")
        raise HTTPException(status_code=500, detail="File records could not be listed.") from exc
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/files/{file_id}/", response_model=FileSummary, tags=["files"], summary="Get file information")
def get_file(file_id: str) -> dict[str, Any]:
    return _load_or_404(file_id, database.get_file_summary)


@app.get(
    "/api/files/{file_id}/features/",
    response_model=FeatureCollectionResponse,
    tags=["files"],
    summary="Get extracted features",
)
def get_features(file_id: str) -> dict[str, Any]:
    return _load_or_404(file_id, database.get_file_features)


@app.get(
    "/api/files/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    tags=["measurements"],
    summary="Get feature measurements",
)
def get_measurements(file_id: str) -> dict[str, Any]:
    record = _load_or_404(file_id, database.get_file_measurements)
    return {
        "id": file_id,
        "filename": record["filename"],
        "crs": record["crs"],
        "feature_count": record["feature_count"],
        "units": {"area": "m²", "length": "m"},
        "measurements": record["measurements"],
        "warnings": record["warnings"],
    }


@app.delete(
    "/api/files/{file_id}/",
    response_model=DeleteResponse,
    tags=["files"],
    summary="Delete a processed file record",
)
def delete_file(file_id: str) -> dict[str, Any]:
    try:
        deleted = database.delete_file(file_id)
    except Exception as exc:
        logger.exception("Could not delete file record")
        raise HTTPException(status_code=500, detail="The file record could not be deleted.") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found.")
    return {"id": file_id, "deleted": True}
