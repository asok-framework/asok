from __future__ import annotations

import mimetypes
import os
import re
from typing import Any
from urllib.parse import urlencode

from ...orm import MODELS_REGISTRY
from ...templates import SafeString
from ..utils import _display

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATIC_DIR = os.path.join(_PKG_DIR, "static")


class HelperViewsMixin:
    # ── Column display ───────────────────────────────────────

    def _is_sensitive_field(self, col: str, field: Any) -> bool:
        if field and getattr(field, "is_password", False):
            return True
        return col in ("totp_secret", "backup_codes")

    def _col_value_sensitive(self, item: Any, col: str) -> SafeString:
        val = getattr(item, col, None)
        if val:
            return SafeString(
                '<span style="letter-spacing:2px;color:var(--fg-3)">••••••••</span>'
            )
        return SafeString('<span class="muted">—</span>')

    def _col_value_fk(self, item: Any, col: str, field: Any) -> Any:
        val = getattr(item, col, None)
        if not val:
            return SafeString('<span class="muted">—</span>')
        target_model = field.related_model
        if isinstance(target_model, str):
            target_model = MODELS_REGISTRY.get(target_model)
        if target_model:
            rel = target_model.find(id=val)
            return _display(rel) if rel else f"#{val}"
        return f"#{val}"

    def _get_raw_col_value(self, item: Any, col: str, model: Any) -> Any:
        if col not in model._fields and col != "id" and hasattr(item, col):
            attr = getattr(item, col)
            return attr() if callable(attr) else attr
        return getattr(item, col, "")

    def _is_boolean_column_name(self, col: str) -> bool:
        return col.startswith("is_") or col.startswith("has_")

    def _col_value_boolean(self, field: Any, col: str, v: Any) -> SafeString | None:
        if field and field.sql_type == "INTEGER" and self._is_boolean_column_name(col):
            if v:
                return SafeString('<span class="badge badge-yes">Yes</span>')
            return SafeString('<span class="badge badge-no">No</span>')
        return None

    def _format_col_string(self, field: Any, v: Any) -> str:
        import enum
        if isinstance(v, enum.Enum):
            v = v.value
        if field and getattr(field, "wysiwyg", False):
            s = re.sub(r"<[^>]+>", "", str(v))
        else:
            s = str(v)
        if len(s) <= 60:
            return s
        return s[:60] + "…"

    def _check_special_fields(self, item: Any, col: str, field: Any) -> Any:
        if not field:
            return None
        if getattr(field, "hidden", False):
            return SafeString('<span class="muted">[hidden]</span>')
        if getattr(field, "is_foreign_key", False):
            return self._col_value_fk(item, col, field)
        return None

    def _col_value(self, item: Any, col: str, model: Any) -> Any:
        field = model._fields.get(col)
        if self._is_sensitive_field(col, field):
            return self._col_value_sensitive(item, col)

        res = self._check_special_fields(item, col, field)
        if res is not None:
            return res

        v = self._get_raw_col_value(item, col, model)
        if v in (None, ""):
            return SafeString('<span class="muted">—</span>')

        bool_badge = self._col_value_boolean(field, col, v)
        if bool_badge is not None:
            return bool_badge

        return self._format_col_string(field, v)

    # ── Query string preservation ────────────────────────────

    def _qs(self, request: Any, **overrides: Any) -> str:
        """Helper to generate links preserving filter, sort, search page query state."""
        params = dict(request.args)
        for k, v in overrides.items():
            if v is None:
                params.pop(k, None)
            else:
                params[k] = str(v)
        if not params:
            return request.path

        return request.path + "?" + urlencode(params)

    def _init_query_base(self, model: Any, with_trashed: bool) -> Any:
        if with_trashed:
            return model.only_trashed()
        return model.query()

    def _try_vector_search(self, model: Any, v_field: str, q: str) -> Any:
        embed_fn = getattr(model, "embed_query", None)
        if not callable(embed_fn):
            return None
        try:
            return embed_fn(q)
        except Exception:
            return None

    def _apply_text_search(self, query: Any, model: Any, searchable_fields: list[str], q: str) -> Any:
        placeholders = []
        search_args = []
        for f in searchable_fields:
            if model._valid_column(f):
                placeholders.append(f"{f} LIKE ?")
                search_args.append(f"%{q}%")
        if placeholders:
            query._wheres.append("(" + " OR ".join(placeholders) + ")")
            query._args.extend(search_args)
        return query

    def _apply_search_filter(self, query: Any, model: Any, entry: dict[str, Any], q: str) -> Any:
        v_field = entry.get("vector_search_field")
        if v_field:
            vector = self._try_vector_search(model, v_field, q)
            if vector:
                return query.nearest(v_field, vector)
        return self._apply_text_search(query, model, entry["searchable"], q)

    def _apply_list_filters(self, query: Any, request: Any, entry: dict[str, Any]) -> Any:
        for f in entry["list_filter"]:
            val = request.args.get(f"filter_{f}")
            if val not in (None, "", "__all__"):
                query = query.where(f, val)
        return query

    def _build_query(
        self, request: Any, entry: dict[str, Any], with_trashed: bool = False
    ) -> Any:
        model = entry["model"]
        q = request.args.get("q", "")
        if not q:
            q = ""
        query = self._init_query_base(model, with_trashed)

        has_searchable = bool(entry.get("searchable"))
        if q and has_searchable:
            query = self._apply_search_filter(query, model, entry, q)

        return self._apply_list_filters(query, request, entry)

    def _query_distinct_values(self, model: Any, f: str) -> list[Any]:
        try:
            engine = model.get_engine()
            q_f = engine.quote_identifier(f)
            q_table = engine.quote_identifier(model._table)
            rows = engine.execute(
                f"SELECT DISTINCT {q_f} FROM {q_table} ORDER BY {q_f}"
            )
            return [
                list(r.values())[0] for r in rows if list(r.values())[0] is not None
            ]
        except Exception:
            return []

    def _is_boolean_like(self, field: Any, f: str) -> bool:
        if field.sql_type != "INTEGER":
            return False
        return f.startswith("is_") or f.startswith("has_")

    def _build_filter_options(self, field: Any, f: str, values: list[Any], current: str) -> list[dict[str, Any]]:
        is_all_selected = bool(current == "")
        options = [{"value": "", "label": "All", "selected": is_all_selected}]
        is_bool = self._is_boolean_like(field, f)
        for v in values:
            label = str(v)
            if is_bool:
                label = "Yes" if v else "No"
            options.append(
                {
                    "value": str(v),
                    "label": label,
                    "selected": str(v) == current,
                }
            )
        return options

    def _build_filters(
        self, request: Any, entry: dict[str, Any]
    ) -> list[dict[str, Any]]:
        out = []
        model = entry["model"]
        for f in entry["list_filter"]:
            field = model._fields.get(f)
            if not field:
                continue
            values = self._query_distinct_values(model, f)
            current = request.args.get(f"filter_{f}", "")
            options = self._build_filter_options(field, f, values, current)
            out.append(
                {"name": f, "label": f.replace("_", " ").title(), "options": options}
            )
        return out

    def _sort_links(
        self, request: Any, columns: list[str], current_sort: str
    ) -> list[dict[str, Any]]:
        out = []
        for col in columns:
            arrow = ""
            new_sort = col
            if current_sort == col:
                arrow = " ↑"
                new_sort = "-" + col
            elif current_sort == "-" + col:
                arrow = " ↓"
                new_sort = col
            out.append(
                {
                    "col": col,
                    "arrow": arrow,
                    "url": self._qs(request, sort=new_sort, page=None),
                    "sort": new_sort,
                }
            )
        return out

    # ── Static serving ───────────────────────────────────────

    def _resolve_minified_name(self, name: str) -> str:
        base, ext = os.path.splitext(name)
        if base.endswith(".min"):
            return name
        if ext not in [".js", ".css"]:
            return name
        min_name = f"{base}.min{ext}"
        min_path = os.path.abspath(os.path.join(_STATIC_DIR, min_name))
        prefix = _STATIC_DIR + os.sep
        if min_path.startswith(prefix):
            if os.path.isfile(min_path):
                return min_name
        return name

    def _guess_mime_type(self, full_path: str) -> str:
        mime, _ = mimetypes.guess_type(full_path)
        if mime:
            return mime
        if full_path.endswith(".woff2"):
            return "font/woff2"
        if full_path.endswith(".woff"):
            return "font/woff"
        return "application/octet-stream"

    def _serve_file_response(self, request: Any, full_path: str) -> str:
        request.content_type = self._guess_mime_type(full_path)
        with open(full_path, "rb") as f:
            request.environ["asok.binary_response"] = f.read()
        return ""

    def _serve_static(self, request: Any, name: str) -> str:
        # Package only contains minified files, so always try to serve .min versions
        name = self._resolve_minified_name(name)
        full = os.path.abspath(os.path.join(_STATIC_DIR, name))
        prefix = _STATIC_DIR + os.sep

        if not full.startswith(prefix):
            request.status_code(404)
            return "Not found"
        if not os.path.isfile(full):
            request.status_code(404)
            return "Not found"

        return self._serve_file_response(request, full)
