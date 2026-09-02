"""A DuckDB catalog that stays in sync with data/*.csv, dynamically.

This is the single place in the codebase that is allowed to know "the data
lives in CSV files" — every table/column name used anywhere else (tools,
guard rails, prompts) is discovered here at call time, never hardcoded. Drop
a new CSV into ``data/`` and the very next tool call sees it; no restart, no
code change, matching the workshop's "dynamic data" requirement.

DuckDB can query CSVs directly, but registering each file as a named VIEW
means agents can write ordinary SQL (``SELECT ... FROM orders JOIN ...``)
instead of repeating ``read_csv_auto('data/orders.csv')`` everywhere.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from app.config import DATA_DIR

_SAFE_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]")
_LEADING_DIGIT = re.compile(r"^(?=\d)")
_READ_ONLY_PREFIX = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|COPY|PRAGMA|"
    r"CALL|EXPORT|IMPORT|VACUUM|LOAD|INSTALL)\b",
    re.IGNORECASE,
)


class UnsafeQueryError(ValueError):
    """Raised when a proposed SQL statement is not a single read-only query."""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    dtype: str


@dataclass(frozen=True)
class TableInfo:
    name: str
    source_file: str
    row_count: int
    columns: list[ColumnInfo]
    sample_rows: list[dict[str, Any]] = field(default_factory=list)


def _table_name_for(csv_path: Path) -> str:
    """Turn a CSV filename into a safe, predictable SQL view name."""
    slug = _SAFE_IDENTIFIER.sub("_", csv_path.stem).lower()
    return _LEADING_DIGIT.sub("t_", slug)


def validate_select_only(sql: str) -> None:
    """Reject anything that is not a single, read-only SELECT/WITH statement.

    This is the tool-layer's guard rail (see app/tools/sql_tool.py): agents
    only ever get read access to the data, and only one statement at a time.
    """
    stripped = sql.strip()
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise UnsafeQueryError("Only a single SQL statement is allowed (no ';' inside the query).")
    if not _READ_ONLY_PREFIX.match(body):
        raise UnsafeQueryError("Only SELECT or WITH (read-only) queries are allowed.")
    if _FORBIDDEN_KEYWORDS.search(body):
        raise UnsafeQueryError("Query contains a disallowed, non-read-only keyword.")


class DataCatalog:
    """Discovers and queries every CSV under ``data_dir`` through DuckDB.

    One long-lived in-memory DuckDB connection guarded by a lock: DuckDB
    connections are not safe for concurrent use from multiple threads, and
    the FastAPI server may run more than one chat request's crew at once
    (each in its own worker thread — see app/server/main.py).
    """

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self._data_dir = data_dir
        self._conn = duckdb.connect(database=":memory:")
        self._lock = threading.Lock()

    # -- internal: keep the view set current with what's on disk -----------------

    def _sync_views_locked(self) -> dict[str, Path]:
        """(Re)create one view per CSV currently in data_dir. Caller holds the lock."""
        mapping: dict[str, Path] = {}
        for csv_path in sorted(self._data_dir.glob("*.csv")):
            table = _table_name_for(csv_path)
            mapping[table] = csv_path
            escaped_path = str(csv_path).replace("'", "''")
            self._conn.execute(
                f'CREATE OR REPLACE VIEW "{table}" AS '
                f"SELECT * FROM read_csv_auto('{escaped_path}', header=true)"
            )
        return mapping

    def _columns_locked(self, table: str) -> list[ColumnInfo]:
        rows = self._conn.execute(f'DESCRIBE "{table}"').fetchall()
        return [ColumnInfo(name=row[0], dtype=row[1]) for row in rows]

    # -- public API ---------------------------------------------------------------

    def list_tables(self) -> list[TableInfo]:
        """Every table currently discoverable under data/, with row counts."""
        with self._lock:
            mapping = self._sync_views_locked()
            result: list[TableInfo] = []
            for table, path in mapping.items():
                count = self._conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                result.append(
                    TableInfo(
                        name=table,
                        source_file=path.name,
                        row_count=count,
                        columns=self._columns_locked(table),
                    )
                )
            return result

    def describe_table(self, table: str, sample_size: int = 5) -> TableInfo:
        """Schema + a few sample rows for one table, resolved dynamically."""
        with self._lock:
            mapping = self._sync_views_locked()
            if table not in mapping:
                known = ", ".join(sorted(mapping)) or "(no tables found in data/)"
                raise ValueError(f"Unknown table '{table}'. Known tables: {known}")
            count = self._conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            columns = self._columns_locked(table)
            cursor = self._conn.execute(f'SELECT * FROM "{table}" LIMIT {int(sample_size)}')
            col_names = [d[0] for d in cursor.description]
            sample = [dict(zip(col_names, row, strict=True)) for row in cursor.fetchall()]
            return TableInfo(
                name=table,
                source_file=mapping[table].name,
                row_count=count,
                columns=columns,
                sample_rows=sample,
            )

    def known_identifiers(self) -> set[str]:
        """Every table and column name known right now (lowercased).

        Used by the schema-grounding guard rail (app/critique/analyst_critic.py)
        to reject an answer that references a table/column that doesn't exist.
        """
        with self._lock:
            mapping = self._sync_views_locked()
            identifiers = set(mapping.keys())
            for table in mapping:
                identifiers.update(col.name.lower() for col in self._columns_locked(table))
            return identifiers

    def run_query(self, sql: str, row_limit: int = 500) -> list[dict[str, Any]]:
        """Execute a validated, read-only SQL query and return rows as dicts."""
        validate_select_only(sql)
        with self._lock:
            self._sync_views_locked()
            cursor = self._conn.execute(sql)
            if cursor.description is None:
                return []
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(row_limit)
            return [dict(zip(columns, row, strict=True)) for row in rows]


_catalog_singleton: DataCatalog | None = None
_singleton_lock = threading.Lock()


def get_catalog() -> DataCatalog:
    """Process-wide DataCatalog singleton (thread-safe, built once)."""
    global _catalog_singleton
    if _catalog_singleton is None:
        with _singleton_lock:
            if _catalog_singleton is None:
                _catalog_singleton = DataCatalog()
    return _catalog_singleton
