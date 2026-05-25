from __future__ import annotations

from typing import Any

from ..forms import Form
from ..orm import MODELS_REGISTRY
from .constants import _IMAGE_EXTS, ADMIN_VERBS, FK_AUTOCOMPLETE_THRESHOLD
from .utils import _display


class FormMixin:
    """Mixin for model form building and representation in Asok Admin."""

    def _field_meta(
        self, name: str, field: Any, readonly_set: set[str], is_creation: bool = False
    ) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
        """Build a Form factory tuple + extra metadata for one model field.

        Returns (form_field_tuple, meta_dict) or (None, None) if the field
        should be skipped entirely.
        """
        # Skip id and hidden fields EXCEPT passwords
        if name == "id":
            return None, None
        if getattr(field, "hidden", False) and not getattr(field, "is_password", False):
            return None, None
        if getattr(field, "is_soft_delete", False):
            return None, None
        is_readonly = name in readonly_set
        is_timestamp = getattr(field, "is_timestamp", False)
        if is_timestamp and not is_readonly:
            return None, None

        label = name.replace("_", " ").title()
        rules_parts = []
        if getattr(field, "nullable", True) is False and not is_readonly:
            if not getattr(field, "is_password", False):
                rules_parts.append("required")
        # Password fields: required on creation, optional on edit
        if getattr(field, "is_password", False) and is_creation and not is_readonly:
            rules_parts.append("required")
        if getattr(field, "is_email", False):
            rules_parts.append("email")
        max_length = getattr(field, "max_length", None)
        if max_length:
            rules_parts.append(f"max:{max_length}")
        rules = "|".join(rules_parts)
        attrs = {}
        if is_readonly:
            attrs["readonly"] = True
        if max_length:
            attrs["maxlength"] = max_length

        meta = {
            "name": name,
            "is_password": False,
            "is_file": False,
            "is_image": False,
            "is_text": False,
            "wysiwyg": False,
            "is_fk_autocomplete": False,
            "fk_target_slug": None,
            "fk_current_label": "",
            "file_value": "",
            "is_date": False,
            "is_datetime": False,
            "is_fk": False,
            "fk_model_slug": None,
        }

        if getattr(field, "is_password", False):
            meta["is_password"] = True
            tup = Form.password(label, rules, **attrs)
        elif getattr(field, "is_file", False):
            meta["is_file"] = True
            tup = Form.file(label, rules, **attrs)
        elif getattr(field, "is_foreign_key", False):
            target = field.related_model
            if isinstance(target, str):
                target = MODELS_REGISTRY.get(target)

            meta["is_fk"] = True
            meta["fk_model_slug"] = self._slug_for_model(target) if target else None

            try:
                count = target.count() if target else 0
            except Exception:
                count = 0
            use_auto = (
                getattr(field, "autocomplete", False)
                or count > FK_AUTOCOMPLETE_THRESHOLD
            )
            if use_auto:
                meta["is_fk_autocomplete"] = True
                meta["fk_target_slug"] = self._slug_for_model(target)
                tup = Form.text(label, rules, **attrs)
            else:
                try:
                    choices = [
                        (o.id, _display(o))
                        for o in target.all(limit=FK_AUTOCOMPLETE_THRESHOLD)
                    ]
                except Exception:
                    choices = []
                choices = [("", "— None —")] + choices
                tup = Form.select(label, choices, rules, **attrs)
        elif getattr(field, "is_boolean", False):
            tup = Form.checkbox(label, rules, **attrs)
        elif getattr(field, "is_enum", False):
            tup = Form.enum(label, field.enum_class, rules, **attrs)
        elif field.sql_type == "INTEGER":
            if name.startswith("is_") or name.startswith("has_"):
                tup = Form.checkbox(label, rules, **attrs)
            else:
                tup = Form.number(label, rules, **attrs)
        elif field.sql_type == "REAL":
            precision = getattr(field, "precision", None)
            if precision is not None:
                attrs["step"] = f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
            tup = Form.number(label, rules, **attrs)
        elif getattr(field, "is_email", False):
            tup = Form.email(label, rules, **attrs)
        elif getattr(field, "is_text", False):
            meta["is_text"] = True
            meta["wysiwyg"] = getattr(field, "wysiwyg", False)
            tup = Form.textarea(label, rules, **attrs)
        elif getattr(field, "is_datetime", False):
            meta["is_datetime"] = True
            tup = Form.datetime_local(label, rules, **attrs)
        elif getattr(field, "is_date", False):
            meta["is_date"] = True
            tup = Form.date(label, rules, **attrs)
        elif getattr(field, "is_time", False):
            tup = Form.time(label, rules, **attrs)
        else:
            # Fallback: heuristic based on field name
            if is_timestamp or name.endswith("_at") or name.endswith("_on"):
                meta["is_datetime"] = True
                tup = Form.datetime_local(label, rules, **attrs)
            elif name == "date" or name.endswith("_date"):
                meta["is_date"] = True
                tup = Form.date(label, rules, **attrs)
            else:
                tup = Form.text(label, rules, **attrs)
        return tup, meta

    def _build_form(
        self,
        request: Any,
        entry: dict[str, Any],
        item: Any,
        errors: dict[str, str] | None = None,
    ) -> tuple[Form, dict[str, dict[str, Any]]]:
        """Build a real Form instance from the model fields and pre-fill from item."""
        model = entry["model"]
        readonly_set = set(entry["readonly_fields"])
        form_exclude = set(entry["form_exclude"])
        # Auto-readonly slugs that auto-populate and always update
        for name, field in model._fields.items():
            if getattr(field, "is_slug", False) and getattr(
                field, "always_update", False
            ):
                readonly_set.add(name)

        schema = {}
        meta = {}
        is_creation = not (item and getattr(item, "id", None))
        for name, field in model._fields.items():
            # Skip excluded fields
            if name in form_exclude:
                continue
            tup, m = self._field_meta(name, field, readonly_set, is_creation)
            if tup is None:
                continue
            schema[name] = tup
            meta[name] = m

        form = Form(schema, request)
        if item and item.id:
            # Snapshot file values for preview before fill (Form file inputs render empty)
            for name, m in meta.items():
                if m["is_file"]:
                    val = getattr(item, name, "") or ""
                    m["file_value"] = str(val)
                    if m["file_value"].lower().endswith(_IMAGE_EXTS):
                        m["is_image"] = True
                if m.get("is_fk_autocomplete"):
                    val = getattr(item, name, None)
                    if val:
                        field = model._fields.get(name)
                        if field and getattr(field, "related_model", None):
                            try:
                                target_model = field.related_model
                                if isinstance(target_model, str):
                                    target_model = MODELS_REGISTRY.get(target_model)
                                rel = (
                                    target_model.find(id=val) if target_model else None
                                )
                            except Exception:
                                rel = None
                            if rel:
                                m["fk_current_label"] = _display(rel)
            form.fill(item)

        # Apply per-field errors from a previous failed save
        if errors:
            for fname, err in errors.items():
                if fname in form._fields:
                    form._fields[fname]._error = err

        return form, meta

    def _grouped_fields(
        self, entry: dict[str, Any], form: Form, meta: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return [{label, fields:[{f, m}, ...]}, ...] honoring fieldsets."""

        def pair(name: str) -> dict[str, Any]:
            return {"f": form._fields[name], "m": meta[name]}

        ordered = list(form._fields.keys())
        if not entry["fieldsets"]:
            return [{"label": None, "fields": [pair(n) for n in ordered]}]
        groups = []
        seen = set()
        for label, names in entry["fieldsets"]:
            items = [pair(n) for n in names if n in form._fields]
            seen.update(names)
            groups.append({"label": label, "fields": items})
        extras = [pair(n) for n in ordered if n not in seen]
        if extras:
            groups.append({"label": "Other", "fields": extras})
        return groups

    def _build_m2m(self, model: Any, item: Any) -> list[dict[str, Any]]:
        """Return list of {name, label, options, selected_ids} for BelongsToMany relations."""
        out = []
        for name, rel in getattr(model, "_relations", {}).items():
            if rel.type != "BelongsToMany":
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue
            try:
                all_options = [
                    {"id": o.id, "label": _display(o)} for o in target.all(limit=500)
                ]
            except Exception:
                all_options = []
            selected_ids = []
            if item and item.id:
                try:
                    selected_ids = [o.id for o in getattr(item, name)()]
                except Exception:
                    selected_ids = []
            for opt in all_options:
                opt["selected"] = opt["id"] in selected_ids
            out.append(
                {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "options": all_options,
                }
            )
        return out

    def _build_inlines(self, entry: dict[str, Any], item: Any) -> list[dict[str, Any]]:
        """Render related HasMany rows as inline list."""
        if not item or not item.id:
            return []
        out = []
        model = entry["model"]
        for rel_name in entry["inlines"]:
            rel = model._relations.get(rel_name)
            if not rel or rel.type != "HasMany":
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue
            target_slug = None
            for s, e in self._registered.items():
                if e["model"] is target:
                    target_slug = s
                    break
            try:
                children = getattr(item, rel_name)()
            except Exception:
                children = []
            cols = []
            for k in target._fields:
                if getattr(target._fields[k], "is_password", False):
                    continue
                cols.append(k)
                if len(cols) >= 4:
                    break
            rows = []
            for c in children:
                d = {"id": c.id}
                for col in cols:
                    d[col] = self._col_value(c, col, target)
                rows.append(d)
            out.append(
                {
                    "name": rel_name,
                    "label": rel_name.replace("_", " ").title(),
                    "columns": cols,
                    "rows": rows,
                    "target_slug": target_slug,
                }
            )
        return out

    def _build_permission_matrix(self, request: Any, item: Any) -> dict[str, Any]:
        """Build a checkbox matrix (models × verbs) for Role.permissions."""
        current_raw = (getattr(item, "permissions", "") or "") if item else ""
        perms = {p.strip() for p in current_raw.split(",") if p.strip()}
        wildcard = "*" in perms
        rows = []
        for slug, entry in self._registered.items():
            # Hide the Role row itself from the matrix to avoid lockout loops
            if entry["model"].__name__ == "Role":
                cells = []
                for v in ADMIN_VERBS:
                    perm = f"{slug}.{v}"
                    cells.append(
                        {
                            "perm": perm,
                            "checked": wildcard
                            or perm in perms
                            or f"{slug}.*" in perms,
                        }
                    )
                rows.append({"slug": slug, "label": entry["label"], "cells": cells})
                continue
            cells = []
            for v in ADMIN_VERBS:
                perm = f"{slug}.{v}"
                cells.append(
                    {
                        "perm": perm,
                        "checked": wildcard or perm in perms or f"{slug}.*" in perms,
                    }
                )
            rows.append({"slug": slug, "label": entry["label"], "cells": cells})
        return {
            "verbs": ADMIN_VERBS,
            "rows": rows,
            "wildcard": wildcard,
            "current": current_raw,
        }

    def _build_user_roles_widget(self, item: Any) -> dict[str, Any] | None:
        """Return a synthetic m2m-like widget for User.roles."""
        Role = MODELS_REGISTRY.get("Role")
        if not Role:
            return None
        try:
            all_roles = Role.all(limit=500)
        except Exception:
            all_roles = []
        selected_ids = set()
        if item and item.id:
            try:
                selected_ids = {r.id for r in item.roles}
            except Exception:
                selected_ids = set()
        options = [
            {
                "id": r.id,
                "label": _display(r),
                "selected": r.id in selected_ids,
            }
            for r in all_roles
        ]
        return {"name": "roles", "label": "Roles", "options": options}
