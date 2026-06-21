from __future__ import annotations

from typing import Any, Optional

from ..forms import Form
from ..orm import MODELS_REGISTRY
from .constants import _IMAGE_EXTS, ADMIN_VERBS, FK_AUTOCOMPLETE_THRESHOLD
from .utils import _display


def _is_hidden_non_password(field: Any) -> bool:
    return bool(getattr(field, "hidden", False) and not getattr(field, "is_password", False))


def _is_mutable_timestamp(field: Any, is_readonly: bool) -> bool:
    return bool(getattr(field, "is_timestamp", False) and not is_readonly)


def _should_skip_field(name: str, field: Any, is_readonly: bool) -> bool:
    if name == "id" or getattr(field, "is_soft_delete", False):
        return True
    if _is_hidden_non_password(field) or _is_mutable_timestamp(field, is_readonly):
        return True
    return False


def _match_morph_relation(name: str, rel_name: str, rel: Any) -> Any:
    if rel.type != "MorphTo":
        return None
    fk_type = rel.owner_key or f"{rel_name}_type"
    fk_id = rel.foreign_key or f"{rel_name}_id"
    if name not in (fk_type, fk_id):
        return None
    return {
        "relation_name": rel_name,
        "type_field": fk_type,
        "id_field": fk_id,
        "is_type": name == fk_type,
        "is_id": name == fk_id,
    }


def _get_morph_info(name: str, model: Any) -> Any:
    if not model:
        return None
    for rel_name, rel in getattr(model, "_relations", {}).items():
        info = _match_morph_relation(name, rel_name, rel)
        if info:
            return info
    return None


def _check_required_rule(field: Any, is_readonly: bool, is_creation: bool) -> bool:
    if is_readonly:
        return False
    is_password = getattr(field, "is_password", False)
    if is_password:
        return is_creation
    return not getattr(field, "nullable", True)


def _get_validation_rules(field: Any, is_readonly: bool, is_creation: bool) -> str:
    rules_parts = []
    if _check_required_rule(field, is_readonly, is_creation):
        rules_parts.append("required")
    if getattr(field, "is_email", False):
        rules_parts.append("email")
    max_length = getattr(field, "max_length", None)
    if max_length:
        rules_parts.append(f"max:{max_length}")
    return "|".join(rules_parts)


def _is_morph_many_relation(rel: Any, current_model_name: str, relation_name: str) -> bool:
    return bool(
        rel.type == "MorphMany"
        and rel.target_model_name == current_model_name
        and rel.foreign_key == relation_name
    )


def _build_morph_type_field(morph_info: dict[str, Any], model: Any, label: str, rules: str, attrs: dict[str, Any]) -> tuple:
    choices = [("", "— None —")]
    current_model_name = model.__name__
    for target_model_name, target_model in MODELS_REGISTRY.items():
        for rel_name, rel in getattr(target_model, "_relations", {}).items():
            if _is_morph_many_relation(rel, current_model_name, morph_info["relation_name"]):
                choices.append((target_model_name, target_model_name))
                break
    return Form.select(label, choices, rules, **attrs)


def _get_commentable_models(current_model_name: str, relation_name: str) -> list[tuple[str, Any]]:
    commentable = []
    for target_model_name, target_model in MODELS_REGISTRY.items():
        for rel_name, rel in getattr(target_model, "_relations", {}).items():
            if _is_morph_many_relation(rel, current_model_name, relation_name):
                commentable.append((target_model_name, target_model))
                break
    return commentable


def _populate_morph_id_choices(choices: list[tuple[str, str]], commentable_models: list[tuple[str, Any]]) -> None:
    for model_name, commentable_model in sorted(commentable_models, key=lambda x: x[0]):
        try:
            items = commentable_model.all(limit=200)
            for obj in items:
                choices.append((f"{model_name}:{obj.id}", f"{model_name}: {_display(obj)}"))
        except Exception:
            pass


def _build_morph_id_field(morph_info: dict[str, Any], model: Any, label: str, rules: str, attrs: dict[str, Any]) -> tuple:
    choices = [("", "— None —")]
    commentable = _get_commentable_models(model.__name__, morph_info["relation_name"])
    _populate_morph_id_choices(choices, commentable)
    return Form.select(label, choices, rules, **attrs)


def _map_integer_field(
    name: str, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any]
) -> tuple:
    if name.startswith("is_") or name.startswith("has_"):
        meta["is_boolean"] = True
        return Form.checkbox(label, rules, **attrs)
    return Form.number(label, rules, **attrs)


def _map_real_field(
    field: Any, label: str, rules: str, attrs: dict[str, Any]
) -> tuple:
    precision = getattr(field, "precision", None)
    if precision is not None:
        attrs["step"] = f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
    return Form.number(label, rules, **attrs)


def _map_numeric_field(
    name: str, field: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any]
) -> Optional[tuple]:
    if field.sql_type == "INTEGER":
        return _map_integer_field(name, label, rules, attrs, meta)
    if field.sql_type == "REAL":
        return _map_real_field(field, label, rules, attrs)
    return None


def _is_dt_match(name: str, field: Any, is_timestamp: bool) -> bool:
    return bool(getattr(field, "is_datetime", False) or is_timestamp or name.endswith("_at") or name.endswith("_on"))


def _is_date_match(name: str, field: Any) -> bool:
    return bool(getattr(field, "is_date", False) or name == "date" or name.endswith("_date"))


def _map_datetime_field(
    name: str, field: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any], is_timestamp: bool
) -> Optional[tuple]:
    if _is_dt_match(name, field, is_timestamp):
        meta["is_datetime"] = True
        return Form.datetime_local(label, rules, **attrs)
    if _is_date_match(name, field):
        meta["is_date"] = True
        return Form.date(label, rules, **attrs)
    if getattr(field, "is_time", False):
        return Form.time(label, rules, **attrs)
    return None


def _map_enum_email_or_text_field(
    field: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any]
) -> Optional[tuple]:
    if getattr(field, "is_enum", False):
        return Form.enum(label, field.enum_class, rules, **attrs)
    if getattr(field, "is_email", False):
        return Form.email(label, rules, **attrs)
    if getattr(field, "is_text", False):
        meta["is_text"] = True
        meta["wysiwyg"] = getattr(field, "wysiwyg", False)
        return Form.textarea(label, rules, **attrs)
    return None


def _map_text_or_choice_field(
    field: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any]
) -> Optional[tuple]:
    if getattr(field, "is_dropdown", False):
        return Form.dropdown(
            label,
            [],
            searchable=getattr(field, "dropdown_searchable", True),
            choices=field.choices,
            rules=rules,
            **attrs,
        )
    if getattr(field, "is_boolean", False):
        meta["is_boolean"] = True
        return Form.checkbox(label, rules, **attrs)
    return _map_enum_email_or_text_field(field, label, rules, attrs, meta)


def _map_sql_type_field(
    name: str, field: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any], is_timestamp: bool
) -> tuple:
    tup = _map_text_or_choice_field(field, label, rules, attrs, meta)
    if tup is not None:
        return tup
    tup = _map_numeric_field(name, field, label, rules, attrs, meta)
    if tup is not None:
        return tup
    tup = _map_datetime_field(name, field, label, rules, attrs, meta, is_timestamp)
    if tup is not None:
        return tup
    return Form.text(label, rules, **attrs)


def _populate_readonly_slugs(model: Any, readonly_set: set[str]) -> None:
    for name, field in model._fields.items():
        if getattr(field, "is_slug", False) and getattr(field, "always_update", False):
            readonly_set.add(name)


def _resolve_fk_target(target: Any) -> Any:
    if isinstance(target, str):
        model_name = target.split(".")[0] if "." in target else target
        return MODELS_REGISTRY.get(model_name)
    return target


def _is_matching_belongs_to(rel: Any, fk_name: str) -> bool:
    if rel.type != "BelongsTo":
        return False
    fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
    return fk == fk_name


def _find_belongs_to_relation(model: Any, fk_name: str) -> Optional[str]:
    if not model:
        return None
    for rel_name, rel in getattr(model, "_relations", {}).items():
        if _is_matching_belongs_to(rel, fk_name):
            return rel_name
    return None


def _get_target_count(target: Any) -> int:
    try:
        return target.count() if target else 0
    except Exception:
        return 0


def _get_fk_choices(target: Any) -> list[tuple[Any, str]]:
    try:
        choices = [
            (o.id, _display(o))
            for o in target.all(limit=FK_AUTOCOMPLETE_THRESHOLD)
        ]
    except Exception:
        choices = []
    return [("", "— None —")] + choices


def _build_morph_field(morph_info: dict[str, Any], model: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any]) -> tuple:
    if morph_info["is_type"]:
        return _build_morph_type_field(morph_info, model, label, rules, attrs)
    meta["morph_type_field"] = morph_info["type_field"]
    return _build_morph_id_field(morph_info, model, label, rules, attrs)


def _get_target_model(field: Any) -> Optional[Any]:
    target = getattr(field, "related_model", None)
    if isinstance(target, str):
        return MODELS_REGISTRY.get(target)
    return target


def _fetch_related_item(target_model: Any, val: Any) -> Optional[Any]:
    if not target_model:
        return None
    try:
        return target_model.find(id=val)
    except Exception:
        return None


def _postprocess_file_meta(name: str, m: dict[str, Any], item: Any) -> None:
    if m["is_file"]:
        val = getattr(item, name, "") or ""
        m["file_value"] = str(val)
        if m["file_value"].lower().endswith(_IMAGE_EXTS):
            m["is_image"] = True


def _postprocess_morph_field(name: str, m: dict[str, Any], item: Any, form: Form) -> None:
    if m.get("morph_type_field") and name in form._fields:
        type_val = getattr(item, m["morph_type_field"], None)
        id_val = getattr(item, name, None)
        if type_val and id_val:
            form._fields[name].value = f"{type_val}:{id_val}"


def _get_extra_fields(ordered: list[str], seen: set[str], form: Form, meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [_pair_field(n, form, meta) for n in ordered if n not in seen]


def _get_m2m_target(rel: Any) -> Optional[Any]:
    return MODELS_REGISTRY.get(rel.target_model_name)


def _get_selected_m2m_labels(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"label": opt["label"]} for opt in options if opt["selected"]]


def _parse_permissions(item: Any) -> tuple[str, set[str]]:
    if not item:
        return "", set()
    current_raw = getattr(item, "permissions", "") or ""
    perms = {p.strip() for p in current_raw.split(",") if p.strip()}
    return current_raw, perms




def _pair_field(name: str, form: Form, meta: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"f": form._fields[name], "m": meta[name]}


def _build_fieldset_groups(entry: dict[str, Any], form: Form, meta: dict[str, dict[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
    groups = []
    for label, names in entry["fieldsets"]:
        items = [_pair_field(n, form, meta) for n in names if n in form._fields]
        seen.update(names)
        groups.append({"label": label, "fields": items})
    return groups


def _fetch_all_target_options(target: Any) -> list[dict[str, Any]]:
    try:
        return [{"id": o.id, "label": f"{target.__name__} #{o.id}"} for o in target.all(limit=500)]
    except Exception:
        return []


def _fetch_selected_ids(item: Any, rel_name: str) -> list[Any]:
    if not item or not getattr(item, "id", None):
        return []
    try:
        return [o.id for o in getattr(item, rel_name)()]
    except Exception:
        return []


def _load_m2m_options(target: Any, item: Any, rel_name: str) -> list[dict[str, Any]]:
    options = _fetch_all_target_options(target)
    selected_ids = set(_fetch_selected_ids(item, rel_name))
    for opt in options:
        opt["selected"] = opt["id"] in selected_ids
    return options


def _fetch_inline_children(item: Any, rel_name: str) -> list[Any]:
    try:
        return getattr(item, rel_name)()
    except Exception:
        return []


def _get_target_cols(target: Any) -> list[str]:
    cols = []
    for k, field in target._fields.items():
        if getattr(field, "is_password", False):
            continue
        cols.append(k)
        if len(cols) >= 4:
            break
    return cols


def _build_perm_cells(slug: str, wildcard: bool, perms: set[str]) -> list[dict[str, Any]]:
    cells = []
    for v in ADMIN_VERBS:
        perm = f"{slug}.{v}"
        cells.append({
            "perm": perm,
            "checked": wildcard or perm in perms or f"{slug}.*" in perms,
        })
    return cells


def _fetch_all_roles() -> list[Any]:
    Role = MODELS_REGISTRY.get("Role")
    if not Role:
        return []
    try:
        return Role.all(limit=500)
    except Exception:
        return []


def _fetch_user_role_ids(item: Any) -> set[Any]:
    if not item or not getattr(item, "id", None):
        return set()
    try:
        return {r.id for r in item.roles}
    except Exception:
        return set()


class FormMixin:
    """Mixin for model form building and representation in Asok Admin."""

    def _build_fk_field(
        self, name: str, field: Any, model: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any]
    ) -> tuple:
        target = _resolve_fk_target(field.related_model)
        meta["is_fk"] = True
        meta["fk_model_slug"] = self._slug_for_model(target) if target else None
        meta["fk_rel_name"] = _find_belongs_to_relation(model, name)

        count = _get_target_count(target)
        use_auto = getattr(field, "autocomplete", False) or count > FK_AUTOCOMPLETE_THRESHOLD

        if use_auto:
            meta["is_fk_autocomplete"] = True
            meta["fk_target_slug"] = self._slug_for_model(target)
            return Form.text(label, rules, **attrs)

        choices = _get_fk_choices(target)
        return Form.select(label, choices, rules, **attrs)

    def _build_field_type_tup(
        self, name: str, field: Any, model: Any, label: str, rules: str, attrs: dict[str, Any], meta: dict[str, Any], morph_info: Optional[dict[str, Any]]
    ) -> tuple:
        if getattr(field, "is_password", False):
            meta["is_password"] = True
            return Form.password(label, rules, **attrs)
        if morph_info:
            return _build_morph_field(morph_info, model, label, rules, attrs, meta)
        if getattr(field, "is_file", False):
            meta["is_file"] = True
            return Form.file(label, rules, **attrs)
        if getattr(field, "is_foreign_key", False):
            return self._build_fk_field(name, field, model, label, rules, attrs, meta)

        is_timestamp = getattr(field, "is_timestamp", False)
        return _map_sql_type_field(name, field, label, rules, attrs, meta, is_timestamp)

    def _field_meta(
        self,
        name: str,
        field: Any,
        readonly_set: set[str],
        is_creation: bool = False,
        model: Any = None,
    ) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
        """Build a Form factory tuple + extra metadata for one model field.

        Returns (form_field_tuple, meta_dict) or (None, None) if the field
        should be skipped entirely.
        """
        is_readonly = name in readonly_set
        if _should_skip_field(name, field, is_readonly):
            return None, None

        morph_info = _get_morph_info(name, model)
        label = name.replace("_", " ").title()
        rules = _get_validation_rules(field, is_readonly, is_creation)
        attrs = {}
        if is_readonly:
            attrs["readonly"] = True
        max_length = getattr(field, "max_length", None)
        if max_length:
            attrs["maxlength"] = max_length

        meta = {
            "name": name,
            "is_password": False,
            "is_file": False,
            "is_image": False,
            "is_text": False,
            "is_boolean": False,
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

        tup = self._build_field_type_tup(name, field, model, label, rules, attrs, meta, morph_info)
        return tup, meta

    def _build_schema_and_meta(
        self, model: Any, form_exclude: set[str], readonly_set: set[str], is_creation: bool
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        schema = {}
        meta = {}
        for name, field in model._fields.items():
            if name in form_exclude:
                continue
            tup, m = self._field_meta(
                name, field, readonly_set, is_creation, model=model
            )
            if tup is not None:
                schema[name] = tup
                meta[name] = m
        return schema, meta

    def _resolve_fk_current_label(self, name: str, m: dict[str, Any], item: Any, model: Any) -> None:
        val = getattr(item, name, None)
        if not val:
            return
        field = model._fields.get(name)
        if not field:
            return
        target_model = _get_target_model(field)
        rel = _fetch_related_item(target_model, val)
        if rel:
            m["fk_current_label"] = _display(rel)

    def _postprocess_autocomplete_field(self, name: str, m: dict[str, Any], item: Any, model: Any) -> None:
        if m.get("is_fk_autocomplete") and item.id:
            self._resolve_fk_current_label(name, m, item, model)

    def _postprocess_form_meta(
        self, form: Form, meta: dict[str, dict[str, Any]], item: Any, model: Any
    ) -> None:
        if not item:
            return
        for name, m in meta.items():
            _postprocess_file_meta(name, m, item)
            self._postprocess_autocomplete_field(name, m, item, model)

        form.fill(item)

        for name, m in meta.items():
            _postprocess_morph_field(name, m, item, form)

    def _apply_form_errors(self, form: Form, errors: Optional[dict[str, str]]) -> None:
        if errors:
            for fname, err in errors.items():
                if fname in form._fields:
                    form._fields[fname]._error = err

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
        _populate_readonly_slugs(model, readonly_set)

        is_creation = not (item and getattr(item, "id", None))
        schema, meta = self._build_schema_and_meta(model, form_exclude, readonly_set, is_creation)

        form = Form(schema, request)
        self._postprocess_form_meta(form, meta, item, model)
        self._apply_form_errors(form, errors)

        return form, meta

    def _grouped_fields(
        self, entry: dict[str, Any], form: Form, meta: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return [{label, fields:[{f, m}, ...]}, ...] honoring fieldsets."""
        ordered = list(form._fields.keys())
        if not entry["fieldsets"]:
            return [{"label": None, "fields": [_pair_field(n, form, meta) for n in ordered]}]
        seen = set()
        groups = _build_fieldset_groups(entry, form, meta, seen)
        extras = _get_extra_fields(ordered, seen, form, meta)
        if extras:
            groups.append({"label": "Other", "fields": extras})
        return groups

    def _build_m2m(self, model: Any, item: Any) -> list[dict[str, Any]]:
        """Return list of {name, label, options, selected_ids} for BelongsToMany relations."""
        out = []
        for name, rel in getattr(model, "_relations", {}).items():
            if rel.type == "BelongsToMany":
                target = _get_m2m_target(rel)
                if target:
                    options = _load_m2m_options(target, item, name)
                    out.append({
                        "name": name,
                        "label": name.replace("_", " ").title(),
                        "options": options,
                        "current": _get_selected_m2m_labels(options),
                    })
        return out

    def _find_target_slug(self, target: Any) -> Optional[str]:
        for s, e in self._registered.items():
            if e["model"] is target:
                return s
        return None

    def _get_inline_rows(self, children: list[Any], cols: list[str], target: Any) -> list[dict[str, Any]]:
        rows = []
        for c in children:
            d = {"id": c.id}
            for col in cols:
                d[col] = self._col_value(c, col, target)
            rows.append(d)
        return rows

    def _build_inlines(self, entry: dict[str, Any], item: Any) -> list[dict[str, Any]]:
        """Render related HasMany rows as inline list."""
        if not getattr(item, "id", None):
            return []
        out = []
        model = entry["model"]
        for rel_name in entry["inlines"]:
            rel = model._relations.get(rel_name)
            if getattr(rel, "type", None) != "HasMany":
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue
            target_slug = self._find_target_slug(target)
            children = _fetch_inline_children(item, rel_name)
            cols = _get_target_cols(target)
            rows = self._get_inline_rows(children, cols, target)
            out.append({
                "name": rel_name,
                "label": rel_name.replace("_", " ").title(),
                "columns": cols,
                "rows": rows,
                "target_slug": target_slug,
            })
        return out

    def _build_permission_matrix(self, request: Any, item: Any) -> dict[str, Any]:
        """Build a checkbox matrix (models × verbs) for Role.permissions."""
        current_raw, perms = _parse_permissions(item)
        wildcard = "*" in perms
        rows = []
        for slug, entry in self._registered.items():
            cells = _build_perm_cells(slug, wildcard, perms)
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
        all_roles = _fetch_all_roles()
        selected_ids = _fetch_user_role_ids(item)
        options = [
            {
                "id": r.id,
                "label": _display(r),
                "selected": r.id in selected_ids,
            }
            for r in all_roles
        ]
        return {"name": "roles", "label": "Roles", "options": options}

