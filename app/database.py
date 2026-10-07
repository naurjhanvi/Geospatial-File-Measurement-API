from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

from app.config import DATABASE_PATH


def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database() -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                feature_count INTEGER NOT NULL,
                crs TEXT,
                status TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                features_json TEXT NOT NULL,
                measurements_json TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS files_created_at_idx ON files(created_at DESC)")


def create_file(record: dict[str, Any]) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO files (
                id, filename, feature_count, crs, status, warnings_json,
                features_json, measurements_json, size_bytes, sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"], record["filename"], record["feature_count"], record["crs"],
                record["status"], json.dumps(record["warnings"], allow_nan=False),
                json.dumps(record["features"], allow_nan=False),
                json.dumps(record["measurements"], allow_nan=False),
                record["size_bytes"], record["sha256"], record["created_at"],
            ),
        )


def get_file_summary(file_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT id, filename, feature_count, crs, status, warnings_json,
                   size_bytes, sha256, created_at
            FROM files WHERE id = ?
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "feature_count": row["feature_count"],
        "crs": row["crs"],
        "status": row["status"],
        "warnings": json.loads(row["warnings_json"]),
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "created_at": row["created_at"],
    }


def get_file_features(file_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            "SELECT id, filename, feature_count, crs, features_json FROM files WHERE id = ?",
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "feature_count": row["feature_count"],
        "crs": row["crs"],
        "features": json.loads(row["features_json"]),
    }


def get_file_measurements(file_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT id, filename, feature_count, crs, warnings_json, measurements_json
            FROM files WHERE id = ?
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "feature_count": row["feature_count"],
        "crs": row["crs"],
        "warnings": json.loads(row["warnings_json"]),
        "measurements": json.loads(row["measurements_json"]),
    }


def list_files(limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    with closing(_connect()) as connection, connection:
        total = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        rows = connection.execute(
            """
            SELECT id, filename, feature_count, crs, status, warnings_json,
                   size_bytes, sha256, created_at
            FROM files ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    items = [
        {
            "id": row["id"],
            "filename": row["filename"],
            "feature_count": row["feature_count"],
            "crs": row["crs"],
            "status": row["status"],
            "warnings": json.loads(row["warnings_json"]),
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return items, total


def delete_file(file_id: str) -> bool:
    with closing(_connect()) as connection, connection:
        cursor = connection.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return cursor.rowcount > 0
