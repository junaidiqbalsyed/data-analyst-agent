"""The one skill that actually answers analytical questions: read-only SQL.

Every aggregation, join, filter, or trend the chatbot can report comes
through here. Two safety properties matter:

1. **Read-only** — `DataCatalog.run_query` rejects anything but a single
   SELECT/WITH statement (see `app.data.catalog.validate_select_only`).
2. **Grounded** — every query and its result is appended to the current
   turn's evidence log (`app.critique.evidence`), which the Insight &
   Reporting guard rails later use to reject any answer that states a
   number the data never actually produced.
"""

from __future__ import annotations

import json

from crewai.tools import tool

from app.critique.evidence import record_query
from app.data import UnsafeQueryError, get_catalog

_MAX_ROWS = 200


@tool("run_sql_query")
def run_sql_query(sql: str) -> str:
    """Run a read-only DuckDB SQL SELECT query against the dataset and return the rows.

    Use `list_tables`/`describe_table` first to learn exact table and column
    names — this tool does not guess or fuzzy-match identifiers. Only a
    single SELECT (or WITH ... SELECT) statement is accepted; joins,
    aggregates, GROUP BY, ORDER BY and LIMIT are all supported, exactly as
    in standard SQL.

    Args:
        sql: A single read-only SQL SELECT statement.

    Returns a JSON object {row_count, rows} on success, or {error} on
    failure (e.g. an unknown column, or a disallowed statement type).
    """
    catalog = get_catalog()
    try:
        rows = catalog.run_query(sql, row_limit=_MAX_ROWS)
    except UnsafeQueryError as exc:
        return json.dumps({"error": f"Query rejected: {exc}"})
    except Exception as exc:  # DuckDB parser/binder errors, etc. — surfaced, not swallowed
        return json.dumps({"error": f"Query failed: {exc}"})

    result_json = json.dumps({"row_count": len(rows), "rows": rows}, default=str)
    record_query(sql=sql, result_json=result_json)
    return result_json
