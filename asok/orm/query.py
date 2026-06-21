from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Generic, Optional, TypeVar, Union

from .list import ModelList
from .model import Model
from .utils import MODELS_REGISTRY, interpolate_sql

T = TypeVar("T", bound="Model")


def _parse_eager_paths(eager: list[str]) -> dict:
    """Split nested eager paths like 'posts.comments' into {'posts': ['comments']}."""
    groups: dict = {}
    for eager_path in eager:
        parts = eager_path.split(".", 1)
        parent = parts[0]
        sub = parts[1] if len(parts) > 1 else None
        groups.setdefault(parent, []).append(sub)
    return groups


def _bucket_by_morph_type(results, fk_id, fk_type) -> dict:
    by_type: dict = {}
    for r in results:
        t_type = getattr(r, fk_type, None)
        t_id = getattr(r, fk_id, None)
        if t_type and t_id:
            by_type.setdefault(t_type, []).append((r, t_id))
    return by_type


_AGG_TOKENS = ("COUNT(", "SUM(", "AVG(", "MIN(", "MAX(")


def _is_aggregate_select(select: Optional[str]) -> bool:
    if select is None:
        return False
    upper = select.upper()
    return any(tok in upper for tok in _AGG_TOKENS)


def _collect_result_ids(results) -> list:
    return [r.id for r in results if r.id]


def _collect_parent_ids(results, fk: str) -> list:
    return list({getattr(r, fk) for r in results if getattr(r, fk)})


def _group_pivot_targets(pivot_rows, pfk, pofk, targets) -> dict:
    by_id = {t.id: t for t in targets}
    parent_to_targets: dict = {}
    for row in pivot_rows:
        t_obj = by_id.get(row[pofk])
        if t_obj:
            parent_to_targets.setdefault(row[pfk], []).append(t_obj)
    return parent_to_targets


class Query(Generic[T]):
    """Chainable SQL query builder for a specific Model.

    Example:
        User.query().where("age", ">", 18).get()
    """

    _OPERATORS = {"=", "!=", "<", ">", "<=", ">=", "LIKE", "NOT LIKE", "IN", "NOT IN"}

    def __init__(self, model: type[T], with_trashed: bool = False):
        self.model: type[T] = model
        self._shard: Optional[str] = None
        self._select: str = "*"
        self._wheres: list[str] = []
        self._args: list[Any] = []
        self._order: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._groups: list[str] = []
        self._eager: list[str] = []
        self._parsed_eager_groups: Optional[dict] = None
        self._union_queries: list[Query[T]] = []
        self._intersect_queries: list[Query[T]] = []
        self._disabled_global_scopes: set[str] = set()
        if with_trashed:
            self._disabled_global_scopes.add("soft_delete")

    def clone(self) -> Query[T]:
        """Return a copy of the query builder state."""
        q = Query(self.model, with_trashed=True)
        q._shard = self._shard
        q._select = self._select
        q._wheres = list(self._wheres)
        q._args = list(self._args)
        q._order = self._order
        q._limit = self._limit
        q._offset = self._offset
        q._groups = list(self._groups)
        q._eager = list(self._eager)
        q._parsed_eager_groups = self._parsed_eager_groups
        q._union_queries = list(self._union_queries)
        q._intersect_queries = list(self._intersect_queries)
        q._disabled_global_scopes = set(self._disabled_global_scopes)
        if hasattr(self, "_cache_ttl"):
            q._cache_ttl = self._cache_ttl
        if hasattr(self, "_cache_key"):
            q._cache_key = self._cache_key
        return q

    def on(self, shard_name: str) -> Query[T]:
        """Direct the query to a specific database shard."""
        clone = self.clone()
        clone._shard = shard_name
        return clone

    def create(self, **kwargs: Any) -> T:
        """Create and save a new model instance on this query's shard."""
        from .router import database_router_context

        with database_router_context(op="write", shard=self._shard):
            obj = self.model(_trust=True, _shard=self._shard, **kwargs)
            obj.save()
            return obj

    def _apply_global_scopes(self) -> None:
        """Apply all active global scopes defined on the model."""
        for name, scope in self.model._global_scopes.items():
            if name not in self._disabled_global_scopes:
                scope(self)
                self._disabled_global_scopes.add(name)

    def without_global_scope(self, name: str) -> Query[T]:
        """Disable a specific global scope for this query."""
        self._disabled_global_scopes.add(name)
        return self

    def without_global_scopes(self) -> Query[T]:
        """Disable all global scopes for this query."""
        self._disabled_global_scopes.update(self.model._global_scopes.keys())
        return self

    def with_trashed(self) -> Query[T]:
        """Include soft-deleted records in the results."""
        return self.without_global_scope("soft_delete")

    def with_(self, *relation_names: str) -> Query[T]:
        """Eager load relationships to avoid N+1 query problems."""
        self._eager.extend(relation_names)
        self._parsed_eager_groups = None
        return self

    def cache(self, ttl: int = 60, key: Optional[str] = None) -> Query[T]:
        """Enable caching for this query."""
        self._cache_ttl = ttl
        self._cache_key = key
        return self

    _AGG_RE = re.compile(
        r"^(COUNT|SUM|AVG|MIN|MAX)\((.*?)\)(?:\s+AS\s+(\w+))?$", re.I
    )

    def select(self, *columns: str) -> Query[T]:
        """Set specific columns to select (useful for aggregates or partial loads)."""
        valid_cols = [self._validate_select_column(col) for col in columns]
        self._select = ", ".join(valid_cols)
        return self

    def _validate_select_column(self, col: str) -> str:
        col_strip = col.strip()
        if col_strip == "*" or self.model._valid_column(col_strip):
            return col_strip
        agg = self._validate_aggregate_expr(col_strip)
        if agg is not None:
            return agg
        raise ValueError(f"Invalid column or expression for selection: {col}")

    def _validate_aggregate_expr(self, col_strip: str) -> Optional[str]:
        match = self._AGG_RE.match(col_strip)
        if not match:
            return None
        func, inner, alias = match.groups()
        # SECURITY: validate alias + inner column before reconstructing the expr.
        if alias and not re.match(r"^\w+$", alias):
            raise ValueError(f"Invalid alias in aggregate: {alias}")
        inner_validated = self._validate_aggregate_inner(inner)
        safe_expr = f"{func.upper()}({inner_validated})"
        if alias:
            safe_expr += f" AS {alias}"
        return safe_expr

    def _validate_aggregate_inner(self, inner: str) -> str:
        inner_strip = inner.strip()
        if inner_strip == "*":
            return "*"
        if not self.model._valid_column(inner_strip):
            raise ValueError(f"Invalid column in aggregate: {inner_strip}")
        return self.model.get_engine().quote_identifier(inner_strip)

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
        if isinstance(values, Query):
            return self._where_in_subquery(column, values)
        if not values:
            self._wheres.append("0")
            return self
        return self._where_in_values(column, values)

    def _where_in_subquery(self, column: str, subquery: "Query") -> "Query[T]":
        self._wheres.append(f"{column} IN ({subquery._build()})")
        self._args.extend(subquery._args)
        return self

    def _where_in_values(self, column: str, values) -> "Query[T]":
        field = self.model._fields.get(column)
        if field:
            values = [self.model.get_engine().prepare_value(field, v) for v in values]
        placeholders = ", ".join(["?"] * len(values))
        self._wheres.append(f"{column} IN ({placeholders})")
        self._args.extend(values)
        return self

    def like(
        self, column: str, pattern: str, escape_wildcards: bool = False
    ) -> Query[T]:
        """Filter using SQL LIKE operator.

        Args:
            column: The column name to filter
            pattern: The LIKE pattern (can include % and _ wildcards)
            escape_wildcards: If True, escape literal % and _ characters in pattern
                             (useful when searching for literal wildcards)

        SECURITY: By default, wildcards are NOT escaped to preserve backward compatibility.
        Set escape_wildcards=True if you need to search for literal % or _ characters.

        Example:
            # Search for users with "test" in name (wildcards active)
            User.query().like('name', '%test%')

            # Search for literal "100%" in description (wildcards escaped)
            Product.query().like('description', '100%', escape_wildcards=True)
        """
        if escape_wildcards:
            # SECURITY: Escape backslash first, then wildcards
            pattern = (
                pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
        return self.where(column, "LIKE", pattern)

    def or_where(self, column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Append an OR condition."""
        op, val = self._normalize_op_value(op_or_val, val)
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

    def _normalize_op_value(self, op_or_val: Any, val: Any) -> tuple[str, Any]:
        if val is None:
            return "=", op_or_val
        op = op_or_val.upper()
        if op not in self._OPERATORS:
            raise ValueError(f"Invalid operator: {op_or_val}")
        return op, val

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
        where_clause, args = self.model.get_engine().search_sql(
            self.model._table, self.model._search_fields, term
        )
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
        clone = self.clone()
        clone._apply_global_scopes()
        return clone._build()

    def raw_sql(self) -> str:
        """Return the SQL query with parameters interpolated (for debugging only).

        WARNING: This is naive and NOT SECURE against SQL injection.
        Use only for inspection in logs/console; never execute this string.
        """
        clone = self.clone()
        clone._apply_global_scopes()
        all_args = list(clone._args)
        for u in clone._union_queries:
            all_args.extend(u._args)
        for i in clone._intersect_queries:
            all_args.extend(i._args)
        return interpolate_sql(clone.to_sql(), all_args)

    def __repr__(self) -> str:
        return f"<Query: {self.to_sql()}>"

    def _build(self, select: Optional[str] = None) -> str:
        """Internal helper to construct the SQL query string."""
        sel = select or self._select
        sql = f"SELECT {sel} FROM {self.model._table}{self._build_where()}"
        sql += self._group_by_clause(self._groups)
        sql = self._apply_set_ops(sql, "UNION", self._union_queries)
        sql = self._apply_set_ops(sql, "INTERSECT", self._intersect_queries)
        return sql + self._build_tail_clauses(select)

    @staticmethod
    def _group_by_clause(groups) -> str:
        return f" GROUP BY {', '.join(groups)}" if groups else ""

    @classmethod
    def _apply_set_ops(cls, sql: str, op: str, queries) -> str:
        for q in queries:
            sub_sql = (
                f"SELECT {q._select} FROM {q.model._table}"
                f"{q._build_where()}{cls._group_by_clause(q._groups)}"
            )
            sql = f"{sql} {op} {sub_sql}"
        return sql

    def _build_tail_clauses(self, select: Optional[str]) -> str:
        is_aggregate = _is_aggregate_select(select)
        tail = self._order_clause(is_aggregate)
        if self._limit is not None and not is_aggregate:
            tail += f" LIMIT {self._limit}"
        if self._offset is not None and not is_aggregate:
            tail += f" OFFSET {self._offset}"
        return tail

    def _order_clause(self, is_aggregate: bool) -> str:
        # Strict SQL (Postgres) rejects ORDER BY on aggregates without GROUP BY.
        if not self._order or (is_aggregate and not self._groups):
            return ""
        return f" ORDER BY {self._order}"

    def get(self) -> ModelList[T]:
        """Execute the query and return a ModelList of results."""
        clone = self.clone()
        clone._apply_global_scopes()
        sql = clone._build()
        all_args = clone._collect_all_args()
        if getattr(clone, "_cache_ttl", None) is not None:
            cached = clone._maybe_return_cached(sql, all_args)
            if cached is not None:
                return cached
        rows = clone.model.get_engine(op="read", shard=clone._shard).execute(sql, all_args)
        results = clone._instantiate_results(rows, sql, all_args)
        if clone._eager and results:
            clone._load_eager(results)
        clone._maybe_store_cache(sql, all_args, rows)
        return results

    def _collect_all_args(self) -> list[Any]:
        all_args = list(self._args)
        for q in self._union_queries:
            all_args.extend(q._args)
        for q in self._intersect_queries:
            all_args.extend(q._args)
        return all_args

    def _instantiate_results(self, rows, sql: str, all_args: list[Any]) -> ModelList[T]:
        return ModelList(
            (self._instantiate_row(row) for row in rows),
            sql=sql, args=all_args,
        )

    def _instantiate_row(self, row):
        obj = self.model(_trust=True, **row)
        obj._shard = self._shard
        return obj

    def _maybe_return_cached(self, sql: str, all_args: list[Any]) -> Optional[ModelList[T]]:
        from ..cache import default_cache

        cache_key = self._resolve_cache_key(sql, all_args)
        cached_rows = default_cache.get(cache_key)
        if cached_rows is None:
            return None
        results = self._instantiate_results(cached_rows, sql, all_args)
        if self._eager and results:
            self._load_eager(results)
        return results

    def _resolve_cache_key(self, sql: str, all_args: list[Any]) -> str:
        if getattr(self, "_cache_key", None):
            return self._cache_key
        raw_key = f"{sql}_{all_args}_{self._eager}"
        return "orm_" + hashlib.md5(raw_key.encode()).hexdigest()

    def _maybe_store_cache(self, sql: str, all_args: list[Any], rows) -> None:
        if getattr(self, "_cache_ttl", None) is None:
            return
        from ..cache import default_cache

        default_cache.set(self._resolve_cache_key(sql, all_args), rows, ttl=self._cache_ttl)

    def _load_eager(self, results):
        """Batch load relations to avoid N+1 queries supporting nesting and polymorphism."""
        if self._parsed_eager_groups is None:
            self._parsed_eager_groups = _parse_eager_paths(self._eager)
        for rel_name, sub_paths in self._parsed_eager_groups.items():
            rel = self.model._relations.get(rel_name)
            if rel:
                self._dispatch_eager_load(rel, rel_name, sub_paths, results)

    def _dispatch_eager_load(self, rel, rel_name, sub_paths, results) -> None:
        active_subs = [p for p in sub_paths if p is not None]
        loader = _EAGER_LOADERS.get(rel.type)
        if loader is not None:
            loader(self, rel, rel_name, active_subs, results)

    def _load_morph_to(self, rel, rel_name, active_subs, results) -> None:
        fk_id = rel.foreign_key or f"{rel_name}_id"
        fk_type = rel.owner_key or f"{rel_name}_type"
        by_type = _bucket_by_morph_type(results, fk_id, fk_type)
        for t_type, pairs in by_type.items():
            target_model = MODELS_REGISTRY.get(t_type)
            if target_model:
                self._attach_morph_targets(target_model, rel_name, active_subs, pairs)

    def _attach_morph_targets(self, target_model, rel_name, active_subs, pairs) -> None:
        t_ids = list({p[1] for p in pairs})
        targets = self._build_relation_query(target_model, active_subs).where_in("id", t_ids).get()
        by_id = {t.id: t for t in targets}
        for r, t_id in pairs:
            r.__dict__[f"_eager_{rel_name}"] = by_id.get(t_id)

    def _load_has(self, rel, rel_name, active_subs, results) -> None:
        ctx = self._prepare_has_load(rel, results)
        if ctx is None:
            return
        target, fk, ids = ctx
        grouped = self._fetch_has_children(target, active_subs, fk, ids)
        is_many = rel.type == "HasMany"
        for r in results:
            r.__dict__[f"_eager_{rel_name}"] = self._select_has_value(
                grouped.get(r.id, []), is_many
            )

    def _prepare_has_load(self, rel, results):
        target = MODELS_REGISTRY.get(rel.target_model_name)
        ids = _collect_result_ids(results)
        if not target or not ids:
            return None
        fk = rel.foreign_key or f"{self.model.__name__.lower()}_id"
        return target, fk, ids

    def _fetch_has_children(self, target, active_subs, fk, ids) -> dict:
        children = self._build_relation_query(target, active_subs).where_in(fk, ids).get()
        grouped: dict = {}
        for c in children:
            grouped.setdefault(getattr(c, fk), []).append(c)
        return grouped

    @staticmethod
    def _select_has_value(items, is_many):
        if is_many:
            return items
        return items[0] if items else None

    def _load_belongs_to(self, rel, rel_name, active_subs, results) -> None:
        ctx = self._prepare_belongs_to(rel, results)
        if ctx is None:
            return
        target, fk, parent_ids = ctx
        parents = self._build_relation_query(target, active_subs).where_in("id", parent_ids).get()
        by_id = {p.id: p for p in parents}
        for r in results:
            r.__dict__[f"_eager_{rel_name}"] = by_id.get(getattr(r, fk))

    def _prepare_belongs_to(self, rel, results):
        target = MODELS_REGISTRY.get(rel.target_model_name)
        if not target:
            return None
        fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
        parent_ids = _collect_parent_ids(results, fk)
        if not parent_ids:
            return None
        return target, fk, parent_ids

    def _load_belongs_to_many(self, rel, rel_name, active_subs, results) -> None:
        ctx = self._prepare_belongs_to_many(rel, results)
        if ctx is None:
            return
        target, pivot_info, ids = ctx
        pivot_rows = self._fetch_pivot_rows(*pivot_info, ids)
        if not pivot_rows:
            for r in results:
                r.__dict__[f"_eager_{rel_name}"] = ModelList()
            return
        self._attach_belongs_to_many_targets(
            target, rel_name, active_subs, results, pivot_rows, pivot_info
        )

    def _prepare_belongs_to_many(self, rel, results):
        target = MODELS_REGISTRY.get(rel.target_model_name)
        pivot_info = self._safe_pivot_info(rel, results)
        ids = _collect_result_ids(results)
        if not target or pivot_info is None or not ids:
            return None
        return target, pivot_info, ids

    @staticmethod
    def _safe_pivot_info(rel, results):
        # SECURITY: _pivot_info validates identifiers before quoting.
        if not results:
            return None
        info = results[0]._pivot_info(rel)
        return info if info[0] else None

    def _attach_belongs_to_many_targets(
        self, target, rel_name, active_subs, results, pivot_rows, pivot_info
    ) -> None:
        _, pfk, pofk = pivot_info
        targets = (
            self._build_relation_query(target, active_subs)
            .where_in("id", list({row[pofk] for row in pivot_rows}))
            .get()
        )
        parent_to_targets = _group_pivot_targets(pivot_rows, pfk, pofk, targets)
        for r in results:
            r.__dict__[f"_eager_{rel_name}"] = ModelList(
                parent_to_targets.get(r.id, []),
                sql=targets.sql, args=targets.args,
            )

    def _fetch_pivot_rows(self, pivot, pfk, pofk, ids):
        engine = self.model.get_engine(op="read", shard=self._shard)
        q_pivot, q_pfk, q_pofk = (
            engine.quote_identifier(pivot),
            engine.quote_identifier(pfk),
            engine.quote_identifier(pofk),
        )
        placeholders = ", ".join(["?"] * len(ids))
        sql = (
            f"SELECT {q_pfk}, {q_pofk} FROM {q_pivot} "
            f"WHERE {q_pfk} IN ({placeholders})"
        )
        return engine.execute(sql, ids)

    def _load_morph_many(self, rel, rel_name, active_subs, results) -> None:
        ctx = self._prepare_morph_many(rel, results)
        if ctx is None:
            return
        target, fk_id, fk_type, ids = ctx
        grouped = self._fetch_morph_many_children(target, active_subs, fk_id, fk_type, ids)
        for r in results:
            r.__dict__[f"_eager_{rel_name}"] = grouped.get(r.id, [])

    @staticmethod
    def _prepare_morph_many(rel, results):
        target = MODELS_REGISTRY.get(rel.target_model_name)
        if not target:
            return None
        ids = [r.id for r in results if r.id]
        if not ids:
            return None
        return target, f"{rel.foreign_key}_id", f"{rel.foreign_key}_type", ids

    def _fetch_morph_many_children(self, target, active_subs, fk_id, fk_type, ids) -> dict:
        children = (
            self._build_relation_query(target, active_subs)
            .where_in(fk_id, ids)
            .where(fk_type, self.model.__name__)
            .get()
        )
        grouped: dict = {}
        for c in children:
            grouped.setdefault(getattr(c, fk_id), []).append(c)
        return grouped

    def _build_relation_query(self, target, active_subs):
        q = Query(target).on(self._shard)
        if active_subs:
            q = q.with_(*active_subs)
        return q

    def first(self) -> Optional[T]:
        """Execute the query and return the first matching record or None."""
        self._limit = 1
        rows = self.get()
        return rows[0] if rows else None

    def count(self) -> int:
        """Return the number of records matching the query."""
        clone = self.clone()
        clone._apply_global_scopes()
        sql = clone._build_count_sql()
        all_args = clone._args_for_set_ops()
        res = clone.model.get_engine(op="read", shard=clone._shard).execute(sql, all_args)
        return list(res[0].values())[0] if res else 0

    def _build_count_sql(self) -> str:
        if self._union_queries or self._intersect_queries or self._groups:
            return f"SELECT COUNT(*) FROM ({self._build()}) AS sub"
        return self._build(select="COUNT(*)")

    def _args_for_set_ops(self) -> list[Any]:
        all_args = list(self._args)
        if not (self._union_queries or self._intersect_queries):
            return all_args
        for q in self._union_queries:
            all_args.extend(q._args)
        for q in self._intersect_queries:
            all_args.extend(q._args)
        return all_args

    def _aggregate(self, func: str, column: str) -> Any:
        """Perform a SQL aggregate function (SUM, AVG, etc.) on a column."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        clone = self.clone()
        clone._apply_global_scopes()
        sql = clone._build_aggregate_sql(func, column)
        all_args = clone._args_for_set_ops()
        res = clone.model.get_engine(op="read", shard=clone._shard).execute(sql, all_args)
        result = list(res[0].values())[0] if res else None
        return result if result is not None else 0

    def _build_aggregate_sql(self, func: str, column: str) -> str:
        if self._union_queries or self._intersect_queries or self._groups:
            return f"SELECT {func}({column}) FROM ({self._build()}) AS sub"
        return self._build(select=f"{func}({column})")

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
        clone = self.clone()
        clone._apply_global_scopes()
        sql = clone._build_pluck_sql(column)
        all_args = clone._args_for_set_ops()
        rows = clone.model.get_engine(op="read", shard=clone._shard).execute(sql, all_args)
        return [list(row.values())[0] for row in rows]

    def _build_pluck_sql(self, column: str) -> str:
        if self._union_queries or self._intersect_queries:
            return f"SELECT {column} FROM ({self._build()}) AS sub"
        return self._build(select=column)

    def update(self, **values: Any) -> int:
        """Bulk update matching rows with the provided values."""
        clone = self.clone()
        clone._apply_global_scopes()
        if clone._union_queries or clone._intersect_queries:
            raise ValueError("Cannot update a compound query (UNION/INTERSECT)")
        if not values:
            return 0
        clone._validate_update_columns(values)
        set_str = ", ".join(f"{k} = ?" for k in values)
        sql = f"UPDATE {clone.model._table} SET {set_str}{clone._build_where()}"
        args = clone._prepare_update_args(values) + clone._args
        return clone.model.get_engine(op="write", shard=clone._shard).execute(sql, args)

    def _validate_update_columns(self, values: dict) -> None:
        for col in values:
            if not self.model._valid_column(col):
                raise ValueError(f"Invalid column: {col}")

    def _prepare_update_args(self, values: dict) -> list[Any]:
        engine = self.model.get_engine(op="write", shard=self._shard)
        args: list[Any] = []
        for k, v in values.items():
            field = self.model._fields.get(k)
            if field:
                v = engine.prepare_value(field, v)
            args.append(v)
        return args

    def exists(self) -> bool:
        """Return True if any records match the query.

        Uses SELECT 1 LIMIT 1 instead of COUNT(*) — the database stops at the
        first matching row, which is significantly faster on large tables.
        """
        clone = self.clone()
        clone._apply_global_scopes()
        # Collect args from this query (UNION/INTERSECT args are not relevant for exists)
        all_args = list(clone._args)
        where_clause = clone._build_where()
        sql = f"SELECT 1 FROM {clone.model._table}{where_clause} LIMIT 1"
        rows = clone.model.get_engine(op="read", shard=clone._shard).execute(
            sql, all_args
        )
        return bool(rows)

    def delete(self) -> int:
        """Bulk delete matching records (handles soft delete if enabled)."""
        clone = self.clone()
        clone._apply_global_scopes()
        if clone._union_queries or clone._intersect_queries:
            raise ValueError("Cannot delete a compound query (UNION/INTERSECT)")
        import datetime

        if clone.model._soft_delete_field:
            return clone.update(
                **{clone.model._soft_delete_field: datetime.datetime.now().isoformat()}
            )
        sql = f"DELETE FROM {clone.model._table}"
        sql += clone._build_where()
        return clone.model.get_engine(op="write", shard=clone._shard).execute(
            sql, clone._args
        )

    def force_delete(self) -> int:
        """Bulk delete matching records permanently, bypassing soft delete."""
        clone = self.clone()
        clone._apply_global_scopes()
        if clone._union_queries or clone._intersect_queries:
            raise ValueError("Cannot delete a compound query (UNION/INTERSECT)")
        sql = f"DELETE FROM {clone.model._table}"
        sql += clone._build_where()
        return clone.model.get_engine(op="write", shard=clone._shard).execute(
            sql, clone._args
        )

    def paginate(
        self, page: int = 1, per_page: int = 10, count: bool = True
    ) -> dict[str, Any]:
        """Paginate the current query and return results with metadata.

        Args:
            page: The page number (1-indexed).
            per_page: Number of items per page.
            count: If True (default), also run a SELECT COUNT(*) to get the total
                   number of matching rows and compute total pages. Set to False
                   to skip the count query and save a database round-trip when
                   the total is not needed (e.g. infinite-scroll / "Load more" UIs).

        Example:
            User.query().where("active", 1).paginate(page=2)
            User.query().where("active", 1).paginate(page=2, count=False)
        """
        total = self.count() if count else None
        pages = math.ceil(total / per_page) if total is not None else None
        items = self.limit(per_page).offset((page - 1) * per_page).get()

        return {
            "items": items,
            "total": total,
            "pages": pages,
            "current_page": page,
        }



_EAGER_LOADERS = {
    "MorphTo": Query._load_morph_to,
    "HasMany": Query._load_has,
    "HasOne": Query._load_has,
    "BelongsTo": Query._load_belongs_to,
    "BelongsToMany": Query._load_belongs_to_many,
    "MorphMany": Query._load_morph_many,
}
