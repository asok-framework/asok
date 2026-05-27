from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple


class BaseEngine(ABC):
    """Abstract base class representing a database engine backend."""

    @abstractmethod
    def get_connection(self) -> Any:
        """Get or create a thread-local or pooled database connection."""
        pass

    @abstractmethod
    def close_connections(self) -> None:
        """Close all connections active for the current thread."""
        pass

    @abstractmethod
    def execute(self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None) -> List[Dict[str, Any]]:
        """Execute a query and return rows as a list of dictionaries."""
        pass

    @abstractmethod
    def quote_identifier(self, name: str) -> str:
        """Quote a table or column name to prevent syntax errors and SQL injection."""
        pass

    @abstractmethod
    def translate_query(self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None) -> Tuple[str, List[Any]]:
        """Translate SQL dialect (converting '?' placeholders to engine format)."""
        pass

    @abstractmethod
    def get_column_type(self, field: Any) -> str:
        """Map a Field object to the engine-specific database column type."""
        pass

    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists in the database."""
        pass

    @abstractmethod
    def get_table_columns(self, table_name: str) -> List[str]:
        """Return a list of column names for the given table."""
        pass

    @abstractmethod
    def search_sql(self, table: str, columns: List[str], term: str) -> Tuple[str, List[Any]]:
        """Build the full-text search clause and parameters for the engine."""
        pass

    @abstractmethod
    def vector_distance_sql(self, column: str, metric: str) -> str:
        """Build the vector similarity SQL ordering expression (metric: 'cosine' or 'euclidean')."""
        pass

    @abstractmethod
    def handle_exception(self, e: Exception) -> Exception:
        """Parse database exception and return a uniform exception (like ModelError)."""
        pass

    def prepare_value(self, field: Any, value: Any) -> Any:
        """Prepare a Python value for writing to the database."""
        return value

    def deserialize_value(self, field: Any, value: Any) -> Any:
        """Convert a database value back to its Python representation."""
        return value

    def post_create_table(self, model_class: Any) -> None:
        """Perform database-specific operations after table creation (e.g. index/trigger setup)."""
        pass

    @property
    @abstractmethod
    def primary_key_def(self) -> str:
        """The database-specific primary key SQL column definition."""
        pass

    @property
    @abstractmethod
    def lastrowid_query(self) -> str | None:
        """Query to retrieve the last inserted ID, or None if handled by the cursor/driver."""
        pass

    def transaction(self) -> Any:
        """Context manager for managing transactions."""
        raise NotImplementedError("Transaction is not supported on this engine.")

