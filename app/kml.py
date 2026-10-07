from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry


GEOMETRY_TAGS = {"Point", "LineString", "Polygon", "MultiGeometry"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in element if _local_name(item.tag) == name), None)


def _coordinate_list(element: ET.Element) -> list[tuple[float, ...]]:
    coordinates = next((item for item in element.iter() if _local_name(item.tag) == "coordinates"), None)
    if coordinates is None or not (coordinates.text or "").strip():
        return []
    parsed: list[tuple[float, ...]] = []
    for token in (coordinates.text or "").split():
        values = [float(value) for value in token.split(",") if value != ""]
        if len(values) < 2:
            raise ValueError("KML coordinate entries need longitude and latitude.")
        parsed.append(tuple(values[:3]))
    return parsed


def _polygon_ring(boundary: ET.Element | None) -> list[tuple[float, ...]]:
    if boundary is None:
        return []
    ring = next((item for item in boundary.iter() if _local_name(item.tag) == "LinearRing"), None)
    return _coordinate_list(ring) if ring is not None else []


def _combine(geometries: list[BaseGeometry]) -> BaseGeometry | None:
    if not geometries:
        return None
    if len(geometries) == 1:
        return geometries[0]
    kinds = {geometry.geom_type for geometry in geometries}
    if kinds == {"Point"}:
        return MultiPoint(geometries)
    if kinds == {"LineString"}:
        return MultiLineString(geometries)
    if kinds == {"Polygon"}:
        return MultiPolygon(geometries)
    return GeometryCollection(geometries)


def _geometry(element: ET.Element) -> BaseGeometry | None:
    kind = _local_name(element.tag)
    if kind == "Point":
        coordinates = _coordinate_list(element)
        return Point(coordinates[0]) if coordinates else None
    if kind == "LineString":
        coordinates = _coordinate_list(element)
        return LineString(coordinates) if len(coordinates) >= 2 else None
    if kind == "Polygon":
        shell = _polygon_ring(_child(element, "outerBoundaryIs"))
        if len(shell) < 3:
            return None
        holes = [
            ring
            for boundary in element
            if _local_name(boundary.tag) == "innerBoundaryIs"
            for ring in [_polygon_ring(boundary)]
            if len(ring) >= 3
        ]
        return Polygon(shell, holes)
    if kind == "MultiGeometry":
        parts = [
            parsed
            for child in element
            if _local_name(child.tag) in GEOMETRY_TAGS
            for parsed in [_geometry(child)]
            if parsed is not None
        ]
        return _combine(parts)
    return None


def _properties(placemark: ET.Element) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for child in placemark:
        name = _local_name(child.tag)
        if name in {"name", "description"}:
            result[name] = child.text or ""
        elif name == "ExtendedData":
            for item in child.iter():
                item_name = _local_name(item.tag)
                if item_name == "Data" and item.get("name"):
                    value = next((part.text for part in item if _local_name(part.tag) == "value"), "")
                    result[item.get("name", "")] = value or ""
                elif item_name == "SimpleData" and item.get("name"):
                    result[item.get("name", "")] = item.text or ""
    return result


def read_kml(path: str | Path) -> gpd.GeoDataFrame:
    """Read common KML Placemark geometries without GDAL's optional KML driver."""
    root = ET.parse(path).getroot()
    placemarks = [element for element in root.iter() if _local_name(element.tag) == "Placemark"]
    properties: list[dict[str, Any]] = []
    geometries: list[BaseGeometry | None] = []

    for placemark in placemarks:
        parts = [
            parsed
            for child in placemark
            if _local_name(child.tag) in GEOMETRY_TAGS
            for parsed in [_geometry(child)]
            if parsed is not None
        ]
        properties.append(_properties(placemark))
        geometries.append(_combine(parts))

    return gpd.GeoDataFrame(
        {"_api_properties": properties},
        geometry=geometries,
        crs="EPSG:4326",
    )
