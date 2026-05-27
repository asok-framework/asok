from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Generic, Optional, TypeVar, Union

from .list import ModelList
from .model import Model
from .utils import MODELS_REGISTRY, interpolate_sql

T = TypeVar("T", bound="Model")


class Query(Generic[T]):
    """Chainable SQL query builder for a specific Model.

    Example:
        User.query().where("age", ">", 18).get()
    """

    _OPERATORS = {"=", "!=", "<", ">", "<=", ">=", "LIKE", "NOT LIKE", "IN", "NOT IN"}

    def __init__(self, model: type[T], with_trashed: bool = False):
        self.model: type[T] = model
        self._select: str = "*"
        self._wheres: list[str] = []
        self._args: list[Any] = []
        self._order: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._groups: list[str] = []
        self._eager: list[str] = []
        self._union_queries: list[Query[T]] = []
        self._intersect_queries: list[Query[T]] = []
        # Auto-filter soft-deleted rows unless explicitly included
        if model._soft_delete_field and not with_trashed:
            self._wheres.append(f"{model._soft_delete_field} IS NULL")

    def with_(self, *relation_names: str) -> Query[T]:
        """Eager load relationships to avoid N+1 query problems."""
        self._eager.extend(relation_names)
        return self

    def cache(self, ttl: int = 60, key: Optional[str] = None) -> Query[T]:
        """Enable caching for this query."""
        self._cache_ttl = ttl
        self._cache_key = key
        return self

    def select(self, *columns: str) -> Query[T]:
        """Set specific columns to select (useful for aggregates or partial loads)."""
        valid_cols = []
        for col in columns:
            col_strip = col.strip()
            # Allow *
            if col_strip == "*":
                valid_cols.append(col_strip)
                continue

            # Allow simple column names
            if self.model._valid_column(col_strip):
                valid_cols.append(col_strip)
                continue

            # Allow common aggregates e.g. COUNT(*) or SUM(price) as total
            # Regex to match FUNC(col) [AS alias]
            match = re.match(
                r"^(COUNT|SUM|AVG|MIN|MAX)\((.*?)\)(?:\s+AS\s+(\w+))?$", col_strip, re.I
            )
            if match:
                func, inner, alias = match.groups()
                inner_strip = inner.strip()

                # SECURITY: Validate alias to prevent SQL injection
                if alias:
                    # Alias must be alphanumeric + underscore only
                    if not re.match(r"^\w+$", alias):
                        raise ValueError(f"Invalid alias in aggregate: {alias}")

                # SECURITY: Validate inner column
                if inner_strip == "*":
                    inner_validated = "*"
                elif self.model._valid_column(inner_strip):
                    inner_validated = self.model.get_engine().quote_identifier(inner_strip)
                else:
                    raise ValueError(f"Invalid column in aggregate: {inner_strip}")

                # SECURITY: Reconstruct expression from validated parts
                safe_expr = f"{func.upper()}({inner_validated})"
                if alias:
                    safe_expr += f" AS {alias}"
                valid_cols.append(safe_expr)
                continue

            raise ValueError(f"Invalid column or expression for selection: {col}")

        self._select = ", ".join(valid_cols)
        return self

    def group_by(self, *columns: str) -> Query[T]:
        """Add a GROUP BY clause to the query."""
        for col in columns:
            if not self.model._valid_column(col):
                raise ValueError(f"Invalid column for grouping: {col}")
        self._groups.extend(columns)
        return self

    def union(self, other: Query[T]) -> Query[T]:
        """Combine results with another query using UNION (removes duplicates).

        Example:
            admins = User.where('role', 'admin')
            mods = User.where('role', 'moderator')
            staff = admins.union(mods)
        """
        if not isinstance(other, Query):
            raise ValueError("union() requires another Query object")
        if other.model != self.model:
            raise ValueError("Cannot union queries from different models")
        self._union_queries.append(other)
        return self

    def intersect(self, other: Query[T]) -> Query[T]:
        """Get only results that appear in both queries using INTERSECT.

        Example:
            active = User.where('active', 1)
            premium = User.where('premium', 1)
            active_premium = active.intersect(premium)
        """
        if not isinstance(other, Query):
            raise ValueError("intersect() requires another Query object")
        if other.model != self.model:
            raise ValueError("Cannot intersect queries from different models")
        self._intersect_queries.append(other)
        return self

    def __getattr__(self, name: str):
        """Allow calling scope methods defined on the model (e.g. scope_active)."""
        scope_method = f"scope_{name}"
        if hasattr(self.model, scope_method):
            method = getattr(self.model, scope_method)
            # Return a wrapper that passes 'self' (the query) as first argument
            return lambda *args, **kwargs: method(self, *args, **kwargs)
        raise AttributeError(
            f"'{self.__class__.__name__}' object or model '{self.model.__name__}' has no attribute '{name}'"
        )

    def where(self, column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Add a where clause. (column, val) or (column, operator, val)."""
        if val is None:
            op, val = "=", op_or_val
        else:
            op = op_or_val.upper()
            if op not in self._OPERATORS:
                raise ValueError(f"Invalid operator: {op_or_val}")
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        field = self.model._fields.get(column)
        if field:
            val = self.model.get_engine().prepare_value(field, val)
        self._wheres.append(f"{column} {op} ?")
        self._args.append(val)
        return self

    def where_in(self, column: str, values) -> Query[T]:
        """Filter by a list of values or a subquery.

        Args:
            column: The column name to filter
            values: Either a list of values OR a Query object (subquery)

        Example with list:
            User.where_in('id', [1, 2, 3])

        Example with subquery:
            active_user_ids = User.query().where('active', 1).select('id')
            Post.where_in('user_id', active_user_ids)
        """
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        # Check if values is a Query (subquery)
        if isinstance(values, Query):
            subquery_sql = values._build()
            self._wheres.append(f"{column} IN ({subquery_sql})")
            self._args.extend(values._args)
            return self

        # Regular list of values
        if not values:
            self._wheres.append("0")
            return self

        field = self.model._fields.get(column)
        if field:
            values = [self.model.get_engine().prepare_value(field, v) for v in values]

        placeholders = ", ".join(["?"] * len(values))
        self._wheres.append(f"{column} IN ({placeholders})")
        self._args.extend(values)
        return self

    def like(self, column: str, pattern: str) -> Query[T]:
        """Filter using SQL LIKE operator."""
        return self.where(column, "LIKE", pattern)

    def or_where(self, column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Append an OR condition."""
        if val is None:
            op, val = "=", op_or_val
        else:
            op = op_or_val.upper()
            if op not in self._OPERATORS:
                raise ValueError(f"Invalid operator: {op_or_val}")

        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        if not self._wheres:
            return self.where(column, op, val)

        field = self.model._fields.get(column)
        if field:
            val = self.model.get_engine().prepare_value(field, val)

        self._wheres.append(f"OR {column} {op} ?")
        self._args.append(val)
        return self

    def where_null(self, column: str) -> Query[T]:
        """Filter rows where column is NULL."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        self._wheres.append(f"{column} IS NULL")
        return self

    def where_not_null(self, column: str) -> Query[T]:
        """Filter rows where column is NOT NULL."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        self._wheres.append(f"{column} IS NOT NULL")
        return self

    def where_between(self, column: str, start: Any, end: Any) -> Query[T]:
        """Filter rows where column value is between start and end."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        field = self.model._fields.get(column)
        if field:
            start = self.model.get_engine().prepare_value(field, start)
            end = self.model.get_engine().prepare_value(field, end)
        self._wheres.append(f"{column} BETWEEN ? AND ?")
        self._args.extend([start, end])
        return self

    def nearest(
        self, column: str, vector: list[float], metric: str = "cosine", limit: int = 10
    ) -> Query[T]:
        """Perform a proximity search using vector similarity (cosine or euclidean)."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        # Delegate vector serialization/preparation to the engine
        field = self.model._fields.get(column)
        prepared_val = self.model.get_engine().prepare_value(field, vector)

        # Let the engine build the similarity ordering expression
        self._order = self.model.get_engine().vector_distance_sql(column, metric)
        self._args.append(prepared_val)
        return self.limit(limit)

    def search(self, term: str) -> Query[T]:
        """Perform a full-text search against indexed fields."""
        if not self.model._search_fields:
            return self

        # Let the engine build the full text search clause
        where_clause, args = self.model.get_engine().search_sql(self.model._table, self.model._search_fields, term)
        self._wheres.append(where_clause)
        self._args.extend(args)
        return self

    def order_by(self, column: str) -> Query[T]:
        """Sort the query results. Use '-column' for descending order."""
        col = column.lstrip("-")
        if not self.model._valid_column(col):
            raise ValueError(f"Invalid column: {col}")
        direction = "DESC" if column.startswith("-") else "ASC"
        self._order = f"{col} {direction}"
        return self

    def latest(self, column: str = "created_at") -> Query[T]:
        """Order by the given column descending (default: created_at)."""
        if not self.model._valid_column(column):
            column = "id"
        return self.order_by(f"-{column}")

    def oldest(self, column: str = "created_at") -> Query[T]:
        """Order by the given column ascending (default: created_at)."""
        if not self.model._valid_column(column):
            column = "id"
        return self.order_by(column)

    def limit(self, n: int) -> Query[T]:
        """Limit the number of records returned."""
        self._limit = int(n)
        return self

    def offset(self, n: int) -> Query[T]:
        """Skip the first N records."""
        self._offset = int(n)
        return self

    def _build_where(self) -> str:
        """Build the WHERE clause, correctly handling OR fragments."""
        if not self._wheres:
            return ""
        where_sql = ""
        for i, w in enumerate(self._wheres):
            if i == 0:
                where_sql += w
            elif w.startswith("OR "):
                where_sql += " " + w
            else:
                where_sql += " AND " + w
        return " WHERE " + where_sql

    def to_sql(self) -> str:
        """Return the SQL query string with placeholders."""
        return self._build()

    def raw_sql(self) -> str:
        """Return the SQL query with parameters interpolated (for debugging only).

        WARNING: This is naive and NOT SECURE against SQL injection.
        Use only for inspection in logs/console; never execute this string.
        """
        all_args = list(self._args)
        for u in self._union_queries:
            all_args.extend(u._args)
        for i in self._intersect_queries:
            all_args.extend(i._args)
        return interpolate_sql(self.to_sql(), all_args)

    def __repr__(self) -> str:
        return f"<Query: {self.to_sql()}>"

    def _build(self, select: Optional[str] = None) -> str:
        """Internal helper to construct the SQL query string."""
        sel = select or self._select
        sql = f"SELECT {sel} FROM {self.model._table}"
        sql += self._build_where()
        if self._groups:
            sql += f" GROUP BY {', '.join(self._groups)}"

        # Add UNION queries
        for union_query in self._union_queries:
            union_sql = f"SELECT {union_query._select} FROM {union_query.model._table}"
            union_sql += union_query._build_where()
            if union_query._groups:
                union_sql += f" GROUP BY {', '.join(union_query._groups)}"
            sql = f"{sql} UNION {union_sql}"

        # Add INTERSECT queries
        for intersect_query in self._intersect_queries:
            intersect_sql = (
                f"SELECT {intersect_query._select} FROM {intersect_query.model._table}"
            )
            intersect_sql += intersect_query._build_where()
            if intersect_query._groups:
                intersect_sql += f" GROUP BY {', '.join(intersect_query._groups)}"
            sql = f"{sql} INTERSECT {intersect_sql}"

        # ORDER/LIMIT/OFFSET apply to the final result
        # Aggregates like COUNT(*) do not allow ORDER BY without GROUP BY in strict SQL (PostgreSQL)
        is_aggregate = select is not None and any(agg in select.upper() for agg in ["COUNT(", "SUM(", "AVG(", "MIN(", "MAX("])
        if self._order and not (is_aggregate and not self._groups):
            sql += f" ORDER BY {self._order}"
        if self._limit is not None and not is_aggregate:
            sql += f" LIMIT {self._limit}"
        if self._offset is not None and not is_aggregate:
            sql += f" OFFSET {self._offset}"
        return sql

    def get(self) -> ModelList[T]:
        """Execute the query and return a ModelList of results."""
        sql = self._build()

        # Collect all args from this query and any union/intersect queries
        all_args = list(self._args)
        for union_query in self._union_queries:
            all_args.extend(union_query._args)
        for intersect_query in self._intersect_queries:
            all_args.extend(intersect_query._args)

        cache_ttl = getattr(self, "_cache_ttl", None)
        if cache_ttl is not None:
            from ..cache import default_cache

            if hasattr(self, "_cache_key") and self._cache_key:
                cache_key = self._cache_key
            else:
                raw_key = f"{sql}_{all_args}_{self._eager}"
                cache_key = "orm_" + hashlib.md5(raw_key.encode()).hexdigest()

            cached_rows = default_cache.get(cache_key)
            if cached_rows is not None:
                results = ModelList(
                    (self.model(_trust=True, **row) for row in cached_rows),
                    sql=sql,
                    args=all_args,
                )
                if self._eager and results:
                    self._load_eager(results)
                return results

        rows = self.model.get_engine().execute(sql, all_args)
        results = ModelList(
            (self.model(_trust=True, **row) for row in rows),
            sql=sql,
            args=all_args,
        )
        if self._eager and results:
            self._load_eager(results)

        if getattr(self, "_cache_ttl", None) is not None:
            from ..cache import default_cache

            # Ensure cache_key is available in this scope
            cache_key = (
                getattr(self, "_cache_key", None)
                or "orm_"
                + hashlib.md5(f"{sql}_{all_args}_{self._eager}".encode()).hexdigest()
            )
            default_cache.set(cache_key, rows, ttl=self._cache_ttl)

        return results

    def _load_eager(self, results):
        """Batch load relations to avoid N+1 queries."""
        for rel_name in self._eager:
            rel = self.model._relations.get(rel_name)
            if not rel:
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue

            if rel.type in ("HasMany", "HasOne"):
                fk = rel.foreign_key or f"{self.model.__name__.lower()}_id"
                ids = [r.id for r in results if r.id]
                if not ids:
                    continue
                children = Query(target).where_in(fk, ids).get()
                grouped = {}
                for c in children:
                    grouped.setdefault(getattr(c, fk), []).append(c)
                for r in results:
                    items = grouped.get(r.id, [])
                    if rel.type == "HasMany":
                        r.__dict__[f"_eager_{rel_name}"] = items
                    else:
                        r.__dict__[f"_eager_{rel_name}"] = items[0] if items else None

            elif rel.type == "BelongsTo":
                fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
                parent_ids = list({getattr(r, fk) for r in results if getattr(r, fk)})
                if not parent_ids:
                    continue
                parents = Query(target).where_in("id", parent_ids).get()
                by_id = {p.id: p for p in parents}
                for r in results:
                    r.__dict__[f"_eager_{rel_name}"] = by_id.get(getattr(r, fk))

    def first(self) -> Optional[T]:
        """Execute the query and return the first matching record or None."""
        self._limit = 1
        rows = self.get()
        return rows[0] if rows else None

    def count(self) -> int:
        """Return the number of records matching the query."""
        if self._union_queries or self._intersect_queries or self._groups:
            subquery = self._build()
            sql = f"SELECT COUNT(*) FROM ({subquery}) AS sub"
        else:
            sql = self._build(select="COUNT(*)")

        all_args = list(self._args)
        if self._union_queries or self._intersect_queries:
            for union_query in self._union_queries:
                all_args.extend(union_query._args)
            for intersect_query in self._intersect_queries:
                all_args.extend(intersect_query._args)

        res = self.model.get_engine().execute(sql, all_args)
        return list(res[0].values())[0] if res else 0

    def _aggregate(self, func: str, column: str) -> Any:
        """Perform a SQL aggregate function (SUM, AVG, etc.) on a column."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        if self._union_queries or self._intersect_queries or self._groups:
            subquery = self._build()
            sql = f"SELECT {func}({column}) FROM ({subquery}) AS sub"
        else:
            sql = self._build(select=f"{func}({column})")

        all_args = list(self._args)
        if self._union_queries or self._intersect_queries:
            for union_query in self._union_queries:
                all_args.extend(union_query._args)
            for intersect_query in self._intersect_queries:
                all_args.extend(intersect_query._args)

        res = self.model.get_engine().execute(sql, all_args)
        result = list(res[0].values())[0] if res else None
        return result if result is not None else 0

    def sum(self, column: str) -> Union[int, float]:
        """Calculate the sum of a numeric column."""
        return self._aggregate("SUM", column)

    def avg(self, column: str) -> float:
        """Calculate the average of a numeric column."""
        return self._aggregate("AVG", column)

    def min(self, column: str) -> Any:
        """Find the minimum value of a column."""
        return self._aggregate("MIN", column)

    def max(self, column: str) -> Any:
        """Find the maximum value of a column."""
        return self._aggregate("MAX", column)

    def pluck(self, column: str) -> list[Any]:
        """Return a flat list of values for a single column across all matches."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        if self._union_queries or self._intersect_queries:
            subquery = self._build()
            sql = f"SELECT {column} FROM ({subquery}) AS sub"
        else:
            sql = self._build(select=column)

        all_args = list(self._args)
        if self._union_queries or self._intersect_queries:
            for union_query in self._union_queries:
                all_args.extend(union_query._args)
            for intersect_query in self._intersect_queries:
                all_args.extend(intersect_query._args)

        rows = self.model.get_engine().execute(sql, all_args)
        return [list(row.values())[0] for row in rows]

    def update(self, **values: Any) -> int:
        """Bulk update matching rows with the provided values."""
        if self._union_queries or self._intersect_queries:
            raise ValueError("Cannot update a compound query (UNION/INTERSECT)")
        if not values:
            return 0
        for col in values:
            if not self.model._valid_column(col):
                raise ValueError(f"Invalid column: {col}")
        set_str = ", ".join([f"{k} = ?" for k in values])
        sql = f"UPDATE {self.model._table} SET {set_str}"
        args = []
        for k, v in values.items():
            field = self.model._fields.get(k)
            if field:
                v = self.model.get_engine().prepare_value(field, v)
            args.append(v)
        sql += self._build_where()
        args += self._args
        return self.model.get_engine().execute(sql, args)

    def exists(self) -> bool:
        """Return True if any records match the query."""
        return self.count() > 0

    def delete(self) -> int:
        """Bulk delete matching records (handles soft delete if enabled)."""
        if self._union_queries or self._intersect_queries:
            raise ValueError("Cannot delete a compound query (UNION/INTERSECT)")
        import datetime

        if self.model._soft_delete_field:
            return self.update(
                **{self.model._soft_delete_field: datetime.datetime.now().isoformat()}
            )
        sql = f"DELETE FROM {self.model._table}"
        sql += self._build_where()
        return self.model.get_engine().execute(sql, self._args)

    def force_delete(self) -> int:
        """Bulk delete matching records permanently, bypassing soft delete."""
        if self._union_queries or self._intersect_queries:
            raise ValueError("Cannot delete a compound query (UNION/INTERSECT)")
        sql = f"DELETE FROM {self.model._table}"
        sql += self._build_where()
        return self.model.get_engine().execute(sql, self._args)

    def paginate(self, page: int = 1, per_page: int = 10) -> dict[str, Any]:
        """Paginate the current query and return results with metadata.

        Example:
            User.query().where("active", 1).paginate(page=2)
        """
        total = self.count()
        pages = math.ceil(total / per_page)
        items = self.limit(per_page).offset((page - 1) * per_page).get()

        return {
            "items": items,
            "total": total,
            "pages": pages,
            "current_page": page,
        }
