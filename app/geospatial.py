from __future__ import annotations

import hashlib
import math
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import PurePosixPath
from typing import Any

import geopandas as gpd
import pandas as pd
from fastapi import HTTPException, UploadFile
from shapely.geometry import mapping
from starlette.concurrency import run_in_threadpool

from app.config import MAX_FEATURES, MAX_UPLOAD_BYTES, MAX_UNZIPPED_BYTES, MAX_ZIP_ENTRIES
from app.kml import read_kml


def _json_value(value: Any) -> Any:
    """Normalize Fiona, pandas, and NumPy values for strict JSON storage."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except (ValueError, TypeError):
            pass
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _extract_shapefile(archive_path: str, destination: str) -> str:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(status_code=400, detail="The uploaded ZIP file is invalid.") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise HTTPException(status_code=413, detail="The ZIP contains too many files.")
        if sum(info.file_size for info in infos) > MAX_UNZIPPED_BYTES:
            raise HTTPException(status_code=413, detail="The uncompressed ZIP exceeds the configured limit.")

        shapefiles: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        seen: set[str] = set()
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            mode = info.external_attr >> 16
            if (path.is_absolute() or ".." in path.parts or ":" in normalized
                    or stat.S_ISLNK(mode)):
                raise HTTPException(status_code=400, detail="The ZIP contains an unsafe file path.")
            if normalized.casefold() in seen:
                raise HTTPException(status_code=400, detail="The ZIP contains duplicate file paths.")
            seen.add(normalized.casefold())
            if not info.is_dir() and path.suffix.lower() == ".shp":
                shapefiles.append((info, path))

        if len(shapefiles) != 1:
            raise HTTPException(
                status_code=400,
                detail="The ZIP must contain exactly one .shp file and its related Shapefile components.",
            )

        _, shp_path = shapefiles[0]
        extracted_shp = os.path.join(destination, *shp_path.parts)
        os.makedirs(os.path.dirname(extracted_shp), exist_ok=True)
        allowed = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx"}
        for info in infos:
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if info.is_dir() or path.parent != shp_path.parent:
                continue
            if path.stem.casefold() != shp_path.stem.casefold() or path.suffix.lower() not in allowed:
                continue
            target = os.path.join(destination, *path.parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with archive.open(info) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise HTTPException(status_code=400, detail="A Shapefile component could not be extracted.") from exc

        if not os.path.isfile(extracted_shp):
            raise HTTPException(status_code=400, detail="The ZIP does not contain a readable Shapefile.")
        return extracted_shp


def _read_upload(path: str, extension: str) -> gpd.GeoDataFrame:
    try:
        if extension == ".kml":
            return read_kml(path)
        if extension == ".zip":
            with tempfile.TemporaryDirectory(prefix="geospatial-shapefile-") as folder:
                shp_path = _extract_shapefile(path, folder)
                return gpd.read_file(shp_path, engine="fiona")
        return gpd.read_file(path, engine="fiona")
    except HTTPException:
        raise
    except Exception as exc:
        message = str(exc)
        raise HTTPException(status_code=422, detail="The geospatial file could not be read or is malformed.") from exc


def _process_path(path: str, extension: str, filename: str, size_bytes: int, sha256: str) -> dict[str, Any]:
    frame = _read_upload(path, extension)
    if len(frame) > MAX_FEATURES:
        raise HTTPException(status_code=413, detail="The file contains more features than the configured limit.")
    source_crs = frame.crs.to_string() if frame.crs else None
    warnings: list[str] = []
    wgs84: gpd.GeoDataFrame | None = None
    projected: gpd.GeoDataFrame | None = None
    if frame.crs is None:
        warnings.append("The source file has no CRS. Measurements are unavailable because units cannot be determined safely.")
    else:
        try:
            wgs84 = frame.to_crs("EPSG:4326")
            projected_crs = wgs84.estimate_utm_crs()
            if projected_crs is None:
                warnings.append("A local projected CRS could not be determined. Measurements are unavailable.")
            else:
                projected = wgs84.to_crs(projected_crs)
        except Exception:
            wgs84 = None
            warnings.append("The source CRS could not be projected safely. Measurements are unavailable.")

    features: list[dict[str, Any]] = []
    measurements: list[dict[str, Any]] = []
    projected_geometries = list(projected.geometry) if projected is not None else [None] * len(frame)
    geometry_column = frame.geometry.name

    for index, (_, row) in enumerate(frame.iterrows()):
        geometry = row[geometry_column]
        geometry_type = geometry.geom_type if geometry is not None else None
        kml_properties = row.get("_api_properties")
        if isinstance(kml_properties, dict):
            properties = _json_value(kml_properties)
        else:
            properties = {
                str(key): _json_value(value)
                for key, value in row.items()
                if key != geometry_column
            }
        features.append({
            "feature_id": index,
            "geometry_type": geometry_type,
            "geometry": _json_value(mapping(geometry)) if geometry is not None else None,
            "geometry_wgs84": (
                _json_value(mapping(wgs84.geometry.iloc[index]))
                if wgs84 is not None and wgs84.geometry.iloc[index] is not None
                else None
            ),
            "crs": source_crs,
            "properties": properties,
        })

        measured_geometry = projected_geometries[index]
        if geometry is None:
            measurements.append({"feature_id": index, "measurement": None, "reason": "Feature has no geometry."})
        elif measured_geometry is None:
            measurements.append({"feature_id": index, "measurement": None, "reason": warnings[0]})
        elif geometry_type in {"Polygon", "MultiPolygon"}:
            value = _json_value(measured_geometry.area)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                measurements.append({"feature_id": index, "measurement": None, "reason": "Area is not finite for this geometry."})
            else:
                measurements.append({
                    "feature_id": index,
                    "measurement": {"type": "area", "value": value, "unit": "m²"},
                    "reason": None,
                })
        elif geometry_type in {"LineString", "MultiLineString"}:
            value = _json_value(measured_geometry.length)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                measurements.append({"feature_id": index, "measurement": None, "reason": "Length is not finite for this geometry."})
            else:
                measurements.append({
                    "feature_id": index,
                    "measurement": {"type": "length", "value": value, "unit": "m"},
                    "reason": None,
                })
        else:
            measurements.append({
                "feature_id": index,
                "measurement": None,
                "reason": f"Measurement is not supported for geometry type {geometry_type}.",
            })

    return {
        "filename": filename,
        "feature_count": len(features),
        "crs": source_crs,
        "status": "COMPLETED",
        "warnings": warnings,
        "features": features,
        "measurements": measurements,
        "size_bytes": size_bytes,
        "sha256": sha256,
    }


async def process_upload(upload: UploadFile) -> dict[str, Any]:
    filename = (upload.filename or "upload").replace("\\", "/").split("/")[-1]
    extension = os.path.splitext(filename)[1].lower()
    if extension not in {".zip", ".kml"}:
        await upload.close()
        raise HTTPException(status_code=415, detail="Upload a .kml file or a .zip containing a Shapefile.")

    fd, path = tempfile.mkstemp(prefix="geospatial-upload-", suffix=extension)
    size = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="The upload exceeds the configured size limit.")
                digest.update(chunk)
                destination.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        return await run_in_threadpool(_process_path, path, extension, filename, size, digest.hexdigest())
    finally:
        await upload.close()
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
