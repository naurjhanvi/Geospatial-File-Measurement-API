from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: Literal["COMPLETED"]
    warnings: list[str]
    size_bytes: int
    sha256: str
    created_at: str


class Feature(BaseModel):
    feature_id: int
    geometry_type: str | None
    geometry: dict[str, Any] | None
    geometry_wgs84: dict[str, Any] | None = None
    crs: str | None
    properties: dict[str, Any]


class AreaMeasurement(BaseModel):
    type: Literal["area"]
    value: float
    unit: Literal["m²"]


class LengthMeasurement(BaseModel):
    type: Literal["length"]
    value: float
    unit: Literal["m"]


class FeatureMeasurement(BaseModel):
    feature_id: int
    measurement: AreaMeasurement | LengthMeasurement | None
    reason: str | None = None


class FileList(BaseModel):
    items: list[FileSummary]
    total: int
    limit: int
    offset: int


class FeatureCollectionResponse(BaseModel):
    id: str
    filename: str
    crs: str | None
    feature_count: int
    features: list[Feature]


class MeasurementsResponse(BaseModel):
    id: str
    filename: str
    crs: str | None
    feature_count: int
    units: dict[str, str]
    measurements: list[FeatureMeasurement]
    warnings: list[str]


class DeleteResponse(BaseModel):
    id: str
    deleted: bool = Field(default=True)
