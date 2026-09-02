""""Skills": the function-calling tools every analyst agent can reach for.

Each tool is a thin, dynamic wrapper around `app.data.catalog.DataCatalog` —
none of them hardcode a table or column name, so a new CSV dropped into
data/ is usable through every one of these the moment it lands.
"""

from app.tools.schema_tools import describe_table, list_tables, quick_profile
from app.tools.sql_tool import run_sql_query

__all__ = [
    "describe_table",
    "list_tables",
    "quick_profile",
    "run_sql_query",
]
