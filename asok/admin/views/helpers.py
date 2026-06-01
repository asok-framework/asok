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

    def _col_value(self, item: Any, col: str, model: Any) -> Any:
        field = model._fields.get(col)
        # Mask sensitive fields (passwords, 2FA secrets, backup codes)
        if field and (
            getattr(field, "is_password", False)
            or col in ("totp_secret", "backup_codes")
        ):
            val = getattr(item, col, None)
            if val:
                return SafeString(
                    '<span style="letter-spacing:2px;color:var(--fg-3)">••••••••</span>'
                )
            return SafeString('<span class="muted">—</span>')
        # Never render hidden fields, even if explicitly requested in columns
        if field and getattr(field, "hidden", False):
            return SafeString('<span class="muted">[hidden]</span>')
        if field and getattr(field, "is_foreign_key", False):
            val = getattr(item, col, None)
            if val:
                target_model = field.related_model
                if isinstance(target_model, str):
                    target_model = MODELS_REGISTRY.get(target_model)
                if target_model:
                    rel = target_model.find(id=val)
                    return _display(rel) if rel else f"#{val}"
                return f"#{val}"
            return SafeString('<span class="muted">—</span>')
        # Calculated column (method on model)
        if col not in model._fields and col != "id" and hasattr(item, col):
            attr = getattr(item, col)
            v = attr() if callable(attr) else attr
        else:
            v = getattr(item, col, "")
        if v is None or v == "":
            return SafeString('<span class="muted">—</span>')
        # Boolean badge
        if (
            field
            and field.sql_type == "INTEGER"
            and (col.startswith("is_") or col.startswith("has_"))
        ):
            if v:
                return SafeString('<span class="badge badge-yes">Yes</span>')
            return SafeString('<span class="badge badge-no">No</span>')
        # Clean output for strings (WYSIWYG stripping)
        if getattr(field, "wysiwyg", False):
            s = re.sub(r"<[^>]+>", "", str(v))
        else:
            s = str(v)

        return s if len(s) <= 60 else s[:60] + "…"

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

    def _build_query(
        self, request: Any, entry: dict[str, Any], with_trashed: bool = False
    ) -> Any:
        model = entry["model"]
        q = request.args.get("q", "") or ""
        if with_trashed:
            query = model.only_trashed()
        else:
            query = model.query()

        if q and entry["searchable"]:
            # ASOK VECTOR EXTENSION: Check if we can perform a semantic search
            v_field = entry.get("vector_search_field")
            vector = None
            if v_field:
                # Try to get vector by calling model.embed_query or Admin.embed_query
                embed_fn = getattr(model, "embed_query", None)
                if callable(embed_fn):
                    try:
                        vector = embed_fn(q)
                    except Exception:
                        pass

                if vector:
                    # Switch to vector search!
                    return query.nearest(v_field, vector)

            placeholders = []
            search_args = []
            for f in entry["searchable"]:
                if model._valid_column(f):
                    placeholders.append(f"{f} LIKE ?")
                    search_args.append(f"%{q}%")
            if placeholders:
                query._wheres.append("(" + " OR ".join(placeholders) + ")")
                query._args.extend(search_args)

        for f in entry["list_filter"]:
            val = request.args.get(f"filter_{f}")
            if val not in (None, "", "__all__"):
                query = query.where(f, val)

        return query

    def _build_filters(
        self, request: Any, entry: dict[str, Any]
    ) -> list[dict[str, Any]]:
        out = []
        model = entry["model"]
        for f in entry["list_filter"]:
            field = model._fields.get(f)
            if not field:
                continue
            try:
                engine = model.get_engine()
                q_f = engine.quote_identifier(f)
                q_table = engine.quote_identifier(model._table)
                rows = engine.execute(
                    f"SELECT DISTINCT {q_f} FROM {q_table} ORDER BY {q_f}"
                )
                values = [
                    list(r.values())[0] for r in rows if list(r.values())[0] is not None
                ]
            except Exception:
                values = []
            current = request.args.get(f"filter_{f}", "")
            options = [{"value": "", "label": "All", "selected": current == ""}]
            for v in values:
                label = str(v)
                if field.sql_type == "INTEGER" and (
                    f.startswith("is_") or f.startswith("has_")
                ):
                    label = "Yes" if v else "No"
                options.append(
                    {
                        "value": str(v),
                        "label": label,
                        "selected": str(v) == current,
                    }
                )
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

    def _serve_static(self, request: Any, name: str) -> str:
        # Package only contains minified files, so always try to serve .min versions
        base, ext = os.path.splitext(name)
        if not base.endswith(".min") and ext in [".js", ".css"]:
            min_name = f"{base}.min{ext}"
            min_path = os.path.abspath(os.path.join(_STATIC_DIR, min_name))
            if min_path.startswith(_STATIC_DIR + os.sep) and os.path.isfile(min_path):
                name = min_name

        full = os.path.abspath(os.path.join(_STATIC_DIR, name))
        if not full.startswith(_STATIC_DIR + os.sep) or not os.path.isfile(full):
            request.status_code(404)
            return "Not found"
        mime, _ = mimetypes.guess_type(full)
        request.content_type = mime or "application/octet-stream"
        with open(full, "rb") as f:
            request.environ["asok.binary_response"] = f.read()
        return ""
