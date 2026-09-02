"""The dynamic data layer: a DuckDB catalog built from whatever is in data/."""

from app.data.catalog import DataCatalog, TableInfo, UnsafeQueryError, get_catalog

__all__ = ["DataCatalog", "TableInfo", "UnsafeQueryError", "get_catalog"]
