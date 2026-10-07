from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from typing import Any

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st


st.set_page_config(
    page_title="Geospatial Measurement",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1400px;}
      [data-testid="stMetric"] {background: #171d27; border: 1px solid #293544; padding: 1rem; border-radius: 0.7rem;}
      div[data-testid="stFileUploader"] {border: 1px dashed #557887; border-radius: 0.7rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def api_url(path: str) -> str:
    base = st.session_state.api_base_url.rstrip("/")
    return f"{base}{path}"


def response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if detail:
            return str(detail)
    except ValueError:
        pass
    return response.text or f"Request failed with HTTP {response.status_code}."


def load_json(path: str) -> dict[str, Any]:
    response = requests.get(api_url(path), timeout=(5, 60))
    if not response.ok:
        raise RuntimeError(response_error(response))
    return response.json()


def coordinate_pairs(value: Any) -> Iterator[tuple[float, float]]:
    """Read coordinate pairs only to position the map viewport."""
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(part, (int, float)) for part in value[:2]):
            longitude, latitude = float(value[0]), float(value[1])
            if math.isfinite(longitude) and math.isfinite(latitude):
                yield longitude, latitude
        else:
            for part in value:
                yield from coordinate_pairs(part)


def geometry_coordinate_pairs(geometry: dict[str, Any]) -> Iterator[tuple[float, float]]:
    if "coordinates" in geometry:
        yield from coordinate_pairs(geometry["coordinates"])
    for child in geometry.get("geometries", []):
        yield from geometry_coordinate_pairs(child)


def map_features(features: list[dict[str, Any]]) -> dict[str, Any]:
    geojson_features: list[dict[str, Any]] = []
    for feature in features:
        geometry = feature.get("geometry_wgs84")
        if geometry is None:
            continue
        geojson_features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "feature_id": feature.get("feature_id"),
                "geometry_type": feature.get("geometry_type"),
            },
        })
    return {"type": "FeatureCollection", "features": geojson_features}


def map_view(geojson: dict[str, Any]) -> pdk.ViewState:
    points = [
        pair
        for feature in geojson["features"]
        for pair in geometry_coordinate_pairs(feature["geometry"] or {})
    ]
    if not points:
        return pdk.ViewState(latitude=20, longitude=0, zoom=1)
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    longitude = (min(longitudes) + max(longitudes)) / 2
    latitude = (min(latitudes) + max(latitudes)) / 2
    span = max(max(longitudes) - min(longitudes), max(latitudes) - min(latitudes), 0.01)
    zoom = max(1, min(15, math.log2(360 / span) - 1))
    return pdk.ViewState(latitude=latitude, longitude=longitude, zoom=zoom)


def render_map(features: list[dict[str, Any]]) -> None:
    geojson = map_features(features)
    if not geojson["features"]:
        st.info("There is no WGS84 geometry to display for this file.")
        return
    layer = pdk.Layer(
        "GeoJsonLayer",
        data=geojson,
        pickable=True,
        stroked=True,
        filled=True,
        auto_highlight=True,
        get_fill_color=[15, 118, 138, 70],
        get_line_color=[8, 72, 91, 220],
        get_line_width=2,
        line_width_min_pixels=1,
    )
    deck = pdk.Deck(
        layers=[layer],
        initial_view_state=map_view(geojson),
        map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        tooltip={"html": "<b>Feature {feature_id}</b><br/>{geometry_type}", "style": {"color": "white"}},
    )
    st.pydeck_chart(deck, use_container_width=True)


def measurement_rows(
    features: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    geometry_types = {feature["feature_id"]: feature.get("geometry_type") for feature in features}
    rows = []
    for item in measurements:
        result = item.get("measurement")
        formatted = "—"
        if result is not None:
            formatted = f"{result['value']:,.2f} {result['unit']}"
        rows.append({
            "Feature": item["feature_id"],
            "Geometry": geometry_types.get(item["feature_id"], "Unknown"),
            "Measurement": formatted,
            "Note": item.get("reason") or "",
        })
    return rows


default_api = os.getenv("API_BASE_URL", "http://localhost:8000")
default_docs = os.getenv("API_DOCS_URL", "http://localhost:8000/docs")
with st.sidebar:
    st.markdown("### Connection")
    api_base = st.text_input("FastAPI URL", value=default_api, help="The API address reachable from this Streamlit process.")
    docs_url = st.text_input("Swagger docs URL", value=default_docs)
    st.link_button("Open Swagger UI ↗", docs_url, use_container_width=True)

st.session_state.api_base_url = api_base.strip().rstrip("/")

st.title("🗺️ Geospatial File Measurement")
st.caption("Upload a KML or zipped Shapefile. FastAPI processes it; this page is an interactive API client.")

with st.container(border=True):
    st.subheader("Upload a geospatial file")
    uploaded = st.file_uploader("Choose a .KML or .ZIP file", type=["kml", "zip"], label_visibility="collapsed")
    process_clicked = st.button("Process file", type="primary", disabled=uploaded is None, use_container_width=True)

if process_clicked and uploaded is not None:
    st.session_state.pop("current_file_id", None)
    st.session_state.pop("current_filename", None)
    try:
        with st.spinner("Uploading file to FastAPI and calculating measurements…"):
            response = requests.post(
                api_url("/api/files/"),
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
                timeout=(10, 300),
            )
        if not response.ok:
            st.error(response_error(response))
        else:
            result = response.json()
            st.session_state.current_file_id = result["id"]
            st.session_state.current_filename = result["filename"]
            st.success("File processed and saved by the API.")
    except requests.RequestException as exc:
        st.error(f"Could not reach the FastAPI service at {st.session_state.api_base_url}: {exc}")

file_id = st.session_state.get("current_file_id")
if file_id:
    try:
        summary = load_json(f"/api/files/{file_id}/")
        feature_result = load_json(f"/api/files/{file_id}/features/")
        measurement_result = load_json(f"/api/files/{file_id}/measurements/")
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        st.error(f"Could not load results from the API: {exc}")
    else:
        if summary.get("warnings"):
            for warning in summary["warnings"]:
                st.warning(warning)

        first, second, third, fourth = st.columns(4)
        first.metric("File", summary["filename"])
        second.metric("Features", f"{summary['feature_count']:,}")
        third.metric("Source CRS", summary.get("crs") or "Unknown")
        fourth.metric("Status", summary["status"])

        measurements = measurement_result["measurements"]
        areas = [entry["measurement"]["value"] for entry in measurements if (entry.get("measurement") or {}).get("type") == "area"]
        lengths = [entry["measurement"]["value"] for entry in measurements if (entry.get("measurement") or {}).get("type") == "length"]
        metric_columns = st.columns(3)
        metric_columns[0].metric("Polygon area total", f"{sum(areas):,.2f} m²")
        metric_columns[1].metric("Line length total", f"{sum(lengths):,.2f} m")
        metric_columns[2].metric("Stored result ID", summary["id"][:12] + "…")

        map_tab, measurement_tab, attributes_tab = st.tabs(["Map", "Measurements", "Feature attributes"])
        with map_tab:
            render_map(feature_result["features"])
        with measurement_tab:
            rows = measurement_rows(feature_result["features"], measurements)
            table = pd.DataFrame(rows)
            st.dataframe(table, hide_index=True, use_container_width=True)
            st.download_button(
                "Download measurements as CSV",
                data=table.to_csv(index=False).encode("utf-8"),
                file_name=f"{summary['filename']}.measurements.csv",
                mime="text/csv",
            )
        with attributes_tab:
            attributes = [
                {
                    "Feature": feature["feature_id"],
                    "Geometry": feature.get("geometry_type") or "—",
                    "Properties": json.dumps(feature.get("properties", {}), ensure_ascii=False),
                }
                for feature in feature_result["features"]
            ]
            st.dataframe(pd.DataFrame(attributes), hide_index=True, use_container_width=True)
elif not process_clicked:
    st.info("Upload a file above, or open Swagger UI to try the REST endpoints directly.")
