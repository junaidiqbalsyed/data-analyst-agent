"""Data-discovery skills: "what data do we even have?".

These are the tools a Data Discovery / Schema Explorer agent reaches for
before writing any SQL — they let the model introspect the dynamic dataset
instead of guessing table or column names.
"""

from __future__ import annotations

import json

from crewai.tools import tool

from app.data import get_catalog


@tool("list_tables")
def list_tables() -> str:
    """List every table currently available in the dataset, with row counts
    AND full column names + types.

    Call this first, and often *only* — the dataset is dynamic, so new
    tables can appear between conversations, but this single call already
    includes enough schema detail (column names and types) to write SQL
    against most tables directly. Only reach for describe_table afterward
    if you specifically need sample rows to see real example values.
    Takes no arguments. Returns a JSON array of {name, source_file,
    row_count, columns: [{name, dtype}, ...]} objects.
    """
    catalog = get_catalog()
    tables = catalog.list_tables()
    payload = [
        {
            "name": t.name,
            "source_file": t.source_file,
            "row_count": t.row_count,
            "columns": [{"name": c.name, "dtype": c.dtype} for c in t.columns],
        }
        for t in tables
    ]
    return json.dumps(payload, default=str)


@tool("describe_table")
def describe_table(table_name: str) -> str:
    """Describe one table's schema and show a few sample rows.

    Args:
        table_name: The exact table name as returned by list_tables.

    Returns a JSON object {name, row_count, columns: [{name, dtype}, ...],
    sample_rows: [...]}. list_tables already gives you every column's name
    and type — reach for this only when you need real example values (e.g.
    to see the exact format of a status/category column) before filtering
    on it.
    """
    catalog = get_catalog()
    try:
        info = catalog.describe_table(table_name)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(
        {
            "name": info.name,
            "source_file": info.source_file,
            "row_count": info.row_count,
            "columns": [{"name": c.name, "dtype": c.dtype} for c in info.columns],
            "sample_rows": info.sample_rows,
        },
        default=str,
    )


@tool("quick_profile")
def quick_profile(table_name: str, column_name: str) -> str:
    """Summarize one column: count, distinct values, and either numeric
    stats (min/max/avg/sum) or the top value frequencies, whichever fits.

    Args:
        table_name: The exact table name as returned by list_tables.
        column_name: The exact column name as returned by describe_table.

    Returns a JSON object with the computed statistics. Prefer this over a
    hand-written SQL query for a simple "profile this column" request.
    """
    catalog = get_catalog()
    try:
        info = catalog.describe_table(table_name)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    known_columns = {c.name: c.dtype for c in info.columns}
    if column_name not in known_columns:
        return json.dumps(
            {"error": f"Unknown column '{column_name}' on '{table_name}'. Known: {sorted(known_columns)}"}
        )

    dtype = known_columns[column_name].upper()
    numeric = any(kind in dtype for kind in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "NUMERIC", "REAL"))

    if numeric:
        sql = (
            f'SELECT count("{column_name}") AS non_null_count, '
            f'count(DISTINCT "{column_name}") AS distinct_count, '
            f'min("{column_name}") AS min_value, max("{column_name}") AS max_value, '
            f'avg("{column_name}") AS avg_value, sum("{column_name}") AS sum_value '
            f'FROM "{table_name}"'
        )
        rows = catalog.run_query(sql)
        return json.dumps({"table": table_name, "column": column_name, "type": "numeric", "stats": rows[0]}, default=str)

    sql = (
        f'SELECT "{column_name}" AS value, count(*) AS frequency FROM "{table_name}" '
        f'GROUP BY "{column_name}" ORDER BY frequency DESC LIMIT 10'
    )
    rows = catalog.run_query(sql)
    return json.dumps({"table": table_name, "column": column_name, "type": "categorical", "top_values": rows}, default=str)
