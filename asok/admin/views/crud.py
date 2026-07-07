from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import traceback
from typing import Any

from ...exceptions import RedirectException
from ...forms import Form
from ...orm import ModelError
from ..constants import ALLOWED_UPLOAD_MIMES, BLOCKED_EXTENSIONS
from ..utils import _display


class CRUDViewsMixin:
    # ── List view ────────────────────────────────────────────

    def _fetch_list_items(
        self, request: Any, entry: dict[str, Any], trash: bool
    ) -> tuple[Any, int, int, int]:
        per_page = entry["per_page"]
        page = max(1, int(request.args.get("page", 1) or 1))
        sort = request.args.get("sort", "-id") or "-id"

        query = self._build_query(request, entry, with_trashed=trash)
        try:
            query = query.order_by(sort)
        except ValueError:
            query = query.order_by("-id")

        total = query.count()
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        items = query.limit(per_page).offset((page - 1) * per_page).get()
        return items, page, pages, total

    def _build_item_dicts(
        self, items: list[Any], columns: list[str], model: Any
    ) -> list[dict[str, Any]]:
        item_dicts = []
        for it in items:
            d = {"id": it.id}
            for col in columns:
                d[col] = self._col_value(it, col, model)
            item_dicts.append(d)
        return item_dicts

    def _is_bulk_editable_field(
        self, f: Any, name: str, readonly_fields: list[str]
    ) -> bool:
        if name in readonly_fields:
            return False
        for attr in (
            "is_password",
            "is_file",
            "is_timestamp",
            "is_soft_delete",
            "is_foreign_key",
        ):
            if getattr(f, attr, False):
                return False
        return True

    def _can_edit_bulk(self, request: Any, entry: dict[str, Any], trash: bool) -> bool:
        if trash:
            return False
        if not entry["can_edit"]:
            return False
        return bool(self._can(request, entry["slug"], "edit"))

    def _build_bulk_edit_fields(
        self, request: Any, entry: dict[str, Any], trash: bool
    ) -> list[dict[str, Any]]:
        bulk_edit_fields = []
        if self._can_edit_bulk(request, entry, trash):
            model = entry["model"]
            for n, f in model._fields.items():
                if self._is_bulk_editable_field(f, n, entry["readonly_fields"]):
                    bulk_edit_fields.append(
                        {"name": n, "label": n.replace("_", " ").title()}
                    )
        return bulk_edit_fields

    def _build_bulk_actions(
        self, request: Any, entry: dict[str, Any], trash: bool
    ) -> list[dict[str, Any]]:
        if trash:
            return [
                {"name": "restore", "label": "Restore selected"},
                {"name": "force_delete", "label": "Delete permanently"},
            ]

        bulk_actions = []
        if entry["can_delete"]:
            if self._can(request, entry["slug"], "delete"):
                bulk_actions.append({"name": "delete", "label": "Delete selected"})
        for act in entry["actions"]:
            bulk_actions.append({"name": act, "label": act.replace("_", " ").title()})
        return bulk_actions

    def _build_list_breadcrumbs(
        self, entry: dict[str, Any], trash: bool
    ) -> list[dict[str, Any]]:
        breadcrumbs = [
            {"label": "Dashboard", "url": self.prefix},
            {
                "label": entry["label"],
                "url": None if not trash else self.prefix + "/" + entry["slug"],
            },
        ]
        if trash:
            breadcrumbs.append({"label": "Trash", "url": None})
        return breadcrumbs

    def _list(self, request: Any, entry: dict[str, Any], trash: bool = False) -> Any:
        q = request.args.get("q", "") or ""
        sort = request.args.get("sort", "-id") or "-id"

        items, page, pages, total = self._fetch_list_items(request, entry, trash)
        item_dicts = self._build_item_dicts(items, entry["columns"], entry["model"])
        bulk_edit_fields = self._build_bulk_edit_fields(request, entry, trash)
        bulk_actions = self._build_bulk_actions(request, entry, trash)
        breadcrumbs = self._build_list_breadcrumbs(entry, trash)

        auth_model_name = self.app.config.get("AUTH_MODEL", "User")
        is_auth_model = entry["model"].__name__ == auth_model_name

        user_can_view = self._can(request, entry["slug"], "view")
        user_can_add = self._can(request, entry["slug"], "add")
        user_can_edit = self._can(request, entry["slug"], "edit")
        user_can_delete = self._can(request, entry["slug"], "delete")
        user_can_export = self._can(request, entry["slug"], "export")

        return self._render(
            request,
            "list.html",
            is_auth_model=is_auth_model,
            items=item_dicts,
            columns=entry["columns"],
            sort_links=self._sort_links(request, entry["columns"], sort),
            slug=entry["slug"],
            model_label=entry["label"],
            page=page,
            pages=pages,
            total=total,
            q=q,
            sort=sort,
            filters=self._build_filters(request, entry),
            active_filters=[
                {"name": f, "value": request.args.get(f"filter_{f}", "")}
                for f in entry["list_filter"]
            ],
            trash=trash,
            has_soft_delete=bool(entry["model"]._soft_delete_field),
            bulk_actions=bulk_actions,
            bulk_edit_fields=bulk_edit_fields,
            can_add=entry["can_add"],
            can_edit=entry["can_edit"],
            can_delete=entry["can_delete"],
            user_can_view=user_can_view,
            user_can_add=user_can_add,
            user_can_edit=user_can_edit,
            user_can_delete=user_can_delete,
            user_can_export=user_can_export,
            prev_url=self._qs(request, page=page - 1),
            next_url=self._qs(request, page=page + 1),
            export_url=self._qs(request, export="csv", page=None),
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
        )

    def _trash(self, request: Any, entry: dict[str, Any]) -> Any:
        if not entry["model"]._soft_delete_field:
            return self._render_error(
                request,
                404,
                self.t(request, "Trash Not Available"),
                self.t(
                    request, "This model does not support soft delete functionality."
                ),
            )
        return self._list(request, entry, trash=True)

    def _parse_bulk_ids(self, request: Any) -> list[int]:
        ids_raw = request.form.get("ids", "")
        return [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]

    def _filter_user_id(self, ids: list[int], user_id: Any) -> list[int]:
        out = []
        for i in ids:
            if i != user_id:
                out.append(i)
        return out

    def _check_self_targeting(
        self, request: Any, model: Any, ids: list[int], slug: str
    ) -> list[int]:
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        if model.__name__ != auth_name:
            return ids
        if not request.user:
            return ids
        user_id = getattr(request.user, "id", None)
        if user_id not in ids:
            return ids

        filtered_ids = self._filter_user_id(ids, user_id)
        request.flash("error", self.t(request, "You cannot target your own account."))
        if not filtered_ids:
            raise RedirectException(self.prefix + "/" + slug)
        return filtered_ids

    def _bulk_delete(self, request: Any, entry: dict[str, Any], ids: list[int]) -> Any:
        model = entry["model"]
        slug = entry["slug"]
        if not entry["can_delete"] or not self._can(request, slug, "delete"):
            return self._forbid(request)
        for i in ids:
            obj = model.find(id=i)
            if obj:
                obj.delete()
        self._log(
            request,
            "bulk_delete",
            model.__name__,
            entity_id=None,
            changes={"ids": ids},
        )
        request.flash(
            "success", self.t(request, "Deleted {count} items", count=len(ids))
        )
        return None

    def _bulk_force_delete(self, request: Any, model: Any, ids: list[int]) -> None:
        for i in ids:
            obj = model.with_trashed().where("id", i).first()
            if obj:
                obj.force_delete()
        self._log(
            request,
            "bulk_force_delete",
            model.__name__,
            entity_id=None,
            changes={"ids": ids},
        )
        request.flash(
            "success",
            self.t(request, "Permanently deleted {count} items", count=len(ids)),
        )

    def _bulk_restore(self, request: Any, model: Any, ids: list[int]) -> None:
        for i in ids:
            obj = model.with_trashed().where("id", i).first()
            if obj:
                obj.restore()
        self._log(
            request,
            "bulk_restore",
            model.__name__,
            entity_id=None,
            changes={"ids": ids},
        )
        request.flash(
            "success", self.t(request, "Restored {count} items", count=len(ids))
        )

    def _is_bool_field_name(self, field_name: str) -> bool:
        if field_name.startswith("is_"):
            return True
        return field_name.startswith("has_")

    def _coerce_bool_value(self, raw: str) -> int:
        if raw in ("1", "on", "true", "yes"):
            return 1
        return 0

    def _coerce_int_bulk_value(self, field_name: str, raw: str) -> Any:
        if self._is_bool_field_name(field_name):
            return self._coerce_bool_value(raw)
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except Exception:
            return None

    def _coerce_real_bulk_value(self, raw: str) -> Any:
        try:
            return float(raw) if raw else None
        except Exception:
            return None

    def _coerce_bulk_value(self, field: Any, field_name: str, raw: str) -> Any:
        if field.sql_type == "INTEGER":
            return self._coerce_int_bulk_value(field_name, raw)
        if field.sql_type == "REAL":
            return self._coerce_real_bulk_value(raw)
        return raw or None

    def _apply_bulk_update(
        self, model: Any, ids: list[int], field_name: str, val: Any
    ) -> int:
        count = 0
        for i in ids:
            obj = model.find(id=i)
            if obj:
                setattr(obj, field_name, val)
                try:
                    obj.save()
                    count += 1
                except Exception:
                    pass
        return count

    def _bulk_set_field(
        self, request: Any, entry: dict[str, Any], ids: list[int], field_name: str
    ) -> Any:
        model = entry["model"]
        slug = entry["slug"]
        if not entry["can_edit"] or not self._can(request, slug, "edit"):
            return self._forbid(request)
        field = model._fields.get(field_name)
        if not field:
            request.flash(
                "error",
                self.t(request, "Unknown field '{field}'", field=field_name),
            )
            raise RedirectException(self.prefix + "/" + slug)

        raw = request.form.get("bulk_value", "")
        val = self._coerce_bulk_value(field, field_name, raw)
        count = self._apply_bulk_update(model, ids, field_name, val)
        self._log(
            request,
            "bulk_edit",
            model.__name__,
            entity_id=None,
            changes={"ids": ids, "field": field_name, "value": str(val)},
        )
        request.flash("success", self.t(request, "Updated {count} items", count=count))
        return None

    def _bulk_custom_action(
        self, request: Any, entry: dict[str, Any], ids: list[int], action: str
    ) -> None:
        model = entry["model"]
        fn = getattr(model, action, None)
        if not fn:
            request.flash(
                "error",
                self.t(request, "Action '{action}' not found on model", action=action),
            )
            return

        count = 0
        for i in ids:
            obj = model.find(id=i)
            if obj and hasattr(obj, action):
                getattr(obj, action)()
                count += 1
        self._log(
            request,
            f"action:{action}",
            model.__name__,
            entity_id=None,
            changes={"ids": ids, "count": count},
        )
        request.flash(
            "success",
            self.t(
                request,
                "Applied '{action}' to {count} items",
                action=action,
                count=count,
            ),
        )

    def _handle_soft_delete_bulk_action(
        self, request: Any, model: Any, ids: list[int], action: str
    ) -> bool:
        if not model._soft_delete_field:
            return False
        if action == "force_delete":
            self._bulk_force_delete(request, model, ids)
            return True
        if action == "restore":
            self._bulk_restore(request, model, ids)
            return True
        return False

    def _dispatch_bulk_action(
        self, request: Any, entry: dict[str, Any], ids: list[int], action: str
    ) -> Any:
        model = entry["model"]
        if action == "delete":
            return self._bulk_delete(request, entry, ids)
        if self._handle_soft_delete_bulk_action(request, model, ids, action):
            return None
        if action.startswith("set:"):
            return self._bulk_set_field(request, entry, ids, action[4:])
        if action in entry["actions"]:
            self._bulk_custom_action(request, entry, ids, action)
        return None

    def _bulk_action(self, request: Any, entry: dict[str, Any]) -> None:
        action = request.form.get("action")
        ids = self._parse_bulk_ids(request)
        if not ids or not action:
            raise RedirectException(self.prefix + "/" + entry["slug"])

        model = entry["model"]
        slug = entry["slug"]
        ids = self._check_self_targeting(request, model, ids, slug)

        res = self._dispatch_bulk_action(request, entry, ids, action)
        if res is not None:
            return res
        raise RedirectException(self.prefix + "/" + slug)

    def _fetch_export_items(self, request: Any, entry: dict[str, Any]) -> list[Any]:
        query = self._build_query(request, entry)
        sort = request.args.get("sort", "-id") or "-id"
        try:
            query = query.order_by(sort)
        except ValueError:
            query = query.order_by("-id")
        return query.get()

    def _resolve_raw_csv_val(self, it: Any, col: str, model: Any) -> Any:
        if col in model._fields or col == "id":
            return getattr(it, col, "")
        attr = getattr(it, col, None)
        return attr() if callable(attr) else attr

    def _get_csv_cell_value(self, it: Any, col: str, model: Any) -> str:
        v = self._resolve_raw_csv_val(it, col, model)
        if v is None:
            return ""
        s = str(v)
        if s.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + s
        return s

    def _export_csv(self, request: Any, entry: dict[str, Any]) -> str:
        items = self._fetch_export_items(request, entry)
        model = entry["model"]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(entry["columns"])
        for it in items:
            row = [self._get_csv_cell_value(it, col, model) for col in entry["columns"]]
            writer.writerow(row)
        data = buf.getvalue().encode("utf-8")
        request.content_type = "text/csv"
        request.environ["asok.binary_response"] = data
        request.environ["asok.extra_headers"] = [
            ("Content-Disposition", f'attachment; filename="{entry["slug"]}.csv"')
        ]
        return ""

    def _parse_lookup_limit(self, request: Any) -> int:
        try:
            return max(1, min(50, int(request.args.get("limit", 20) or 20)))
        except ValueError:
            return 20

    def _apply_lookup_search(
        self, query: Any, model: Any, searchable: list[str], q: str
    ) -> None:
        placeholders = []
        for f in searchable:
            if model._valid_column(f):
                placeholders.append(f"{f} LIKE ?")
                query._args.append(f"%{q}%")
        if placeholders:
            query._wheres.append("(" + " OR ".join(placeholders) + ")")
        elif q.isdigit():
            query._wheres.append("id = ?")
            query._args.append(int(q))

    def _get_q_param(self, request: Any) -> str:
        q = request.args.get("q", "")
        if not q:
            return ""
        return q.strip()

    def _lookup(self, request: Any, entry: dict[str, Any]) -> str:
        """Return JSON [{id,label}] for FK autocomplete on this model."""
        model = entry["model"]
        q = self._get_q_param(request)
        limit = self._parse_lookup_limit(request)
        query = model.query()
        if q:
            searchable = entry.get("searchable")
            if not searchable:
                searchable = []
            self._apply_lookup_search(query, model, searchable, q)
        try:
            items = query.order_by("-id").limit(limit).get()
        except Exception:
            items = []
        data = []
        for o in items:
            data.append({"id": o.id, "label": _display(o)})
        request.content_type = "application/json"
        return json.dumps(data)

    _PRIVILEGE_FIELDS = (
        "is_admin",
        "is_superuser",
        "is_staff",
        "permissions",
        "role",
        "roles",
    )

    def _is_blocked_import_attribute(self, field: Any) -> bool:
        for attr in (
            "protected",
            "is_timestamp",
            "is_soft_delete",
            "is_password",
            "is_file",
        ):
            if getattr(field, attr, False):
                return True
        return False

    def _is_populated_slug(self, field: Any) -> bool:
        return bool(
            getattr(field, "is_slug", False) and getattr(field, "populate_from", None)
        )

    def _is_importable_field(
        self, field: Any, name: str, allow_privileged: bool = False
    ) -> bool:
        if self._is_blocked_import_attribute(field):
            return False
        if self._is_populated_slug(field):
            return False
        return allow_privileged or name not in self._PRIVILEGE_FIELDS

    def _importable_fields(self, model: Any, request: Any = None) -> list[str]:
        """Return field names that can be imported (skip auto/timestamps/passwords/files).

        Privilege fields (is_admin, permissions, roles, ...) are stripped unless the
        importing user is a super-admin, mirroring _strip_secured_fields for the form path.
        """
        allow_privileged = bool(
            request and getattr(getattr(request, "user", None), "is_admin", False)
        )
        return [
            name
            for name, field in model._fields.items()
            if self._is_importable_field(field, name, allow_privileged=allow_privileged)
        ]

    def _get_upload_size(self, upload: Any) -> int:
        if hasattr(upload, "file"):
            upload.file.seek(0, 2)
            file_size = upload.file.tell()
            upload.file.seek(0)
            return file_size
        if hasattr(upload, "content_length"):
            return upload.content_length or 0
        return 0

    def _validate_and_read_csv(self, request: Any, upload: Any) -> str:
        MAX_CSV_SIZE = 10 * 1024 * 1024  # 10MB
        if self._get_upload_size(upload) > MAX_CSV_SIZE:
            request.flash(
                "error",
                self.t(
                    request,
                    "File size exceeds maximum allowed (10MB). Please upload a smaller file.",
                ),
            )
            raise ValueError("File too large")

        if hasattr(upload, "file"):
            raw = upload.file.read()
        else:
            raw = upload.read()

        if isinstance(raw, bytes):
            return raw.decode("utf-8-sig")
        return raw

    def _coerce_csv_integer(self, key: str, val: str) -> Any:
        is_bool = bool(key.startswith("is_") or key.startswith("has_"))
        if is_bool:
            return 1 if val.lower() in ("1", "true", "yes", "on") else 0
        try:
            return int(val)
        except ValueError:
            return None

    def _coerce_csv_real(self, val: str) -> Any:
        try:
            return float(val)
        except ValueError:
            return None

    def _coerce_csv_cell(self, key: str, field: Any, val: str) -> Any:
        if key == "id":
            try:
                return int(val)
            except ValueError:
                return None
        if field.sql_type == "INTEGER":
            return self._coerce_csv_integer(key, val)
        if field.sql_type == "REAL":
            return self._coerce_csv_real(val)
        return val

    def _is_csv_field_allowed(self, key: str, importable: list[str]) -> bool:
        if key == "id":
            return True
        return key in importable

    def _get_csv_raw_val(self, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    def _parse_csv_cell_entry(
        self, model: Any, importable: list[str], k: str, v: Any
    ) -> tuple[str, Any] | None:
        if not k:
            return None
        key = k.strip()
        if not self._is_csv_field_allowed(key, importable):
            return None
        field = model._fields.get(key)
        val = self._get_csv_raw_val(v)
        if val == "":
            return key, None
        return key, self._coerce_csv_cell(key, field, val)

    def _parse_csv_row(
        self, model: Any, importable: list[str], row: dict[str, Any]
    ) -> dict[str, Any]:
        data = {}
        for k, v in row.items():
            entry = self._parse_csv_cell_entry(model, importable, k, v)
            if entry is not None:
                key, val = entry
                data[key] = val
        return data

    def _find_item_by_unique_fields(self, model: Any, data: dict[str, Any]) -> Any:
        for f_name, f_obj in model._fields.items():
            if getattr(f_obj, "unique", False) and data.get(f_name):
                item = model.find(**{f_name: data[f_name]})
                if item:
                    return item
        return None

    def _process_csv_row(
        self, model: Any, data: dict[str, Any], update_existing: bool
    ) -> str | None:
        item = None
        if update_existing:
            if data.get("id"):
                item = model.find(id=data["id"])
            if not item:
                item = self._find_item_by_unique_fields(model, data)

        if item:
            data.pop("id", None)
            item.update(**data)
            return "updated"

        model.create(**data)
        return "created"

    def _flash_import_results(
        self, request: Any, created: int, updated: int, failed: int
    ) -> None:
        if failed == 0:
            msg = self.t(request, "Imported {count} rows", count=created + updated)
            if updated:
                msg += f" ({updated} {self.t(request, 'updated')})"
            request.flash("success", msg)
        else:
            request.flash(
                "error",
                self.t(
                    request,
                    "Imported {created}, {failed} failed",
                    created=created,
                    failed=failed,
                ),
            )

    def _record_row_error(self, errors: list[str], i: int, e: Exception) -> None:
        if len(errors) < 10:
            errors.append(f"Row {i}: {e}")

    def _handle_csv_exception(self, request: Any, e: Exception) -> None:
        if str(e) != "File too large":
            request.flash("error", self.t(request, "CSV parse error: {error}", error=e))

    def _process_csv_import(
        self,
        request: Any,
        model: Any,
        importable: list[str],
        upload: Any,
        update_existing: bool,
    ) -> dict[str, Any] | None:
        try:
            text = self._validate_and_read_csv(request, upload)
            reader = csv.DictReader(io.StringIO(text))
            created, updated, failed, errors = 0, 0, 0, []
            for i, row in enumerate(reader, start=2):
                try:
                    data = self._parse_csv_row(model, importable, row)
                    status = self._process_csv_row(model, data, update_existing)
                    if status == "updated":
                        updated += 1
                    else:
                        created += 1
                except Exception as e:
                    failed += 1
                    self._record_row_error(errors, i, e)
            self._log(
                request,
                "import_csv",
                model.__name__,
                entity_id=None,
                changes={"created": created, "updated": updated, "failed": failed},
            )
            self._flash_import_results(request, created, updated, failed)
            return {
                "created": created,
                "updated": updated,
                "failed": failed,
                "errors": errors,
            }
        except Exception as e:
            self._handle_csv_exception(request, e)
            return None

    def _import_csv(self, request: Any, entry: dict[str, Any]) -> Any:
        model = entry["model"]
        importable = self._importable_fields(model, request)
        report = None
        if request.method == "POST":
            upload = request.files.get("file") if request.files else None
            update_existing = bool(request.form.get("update_existing"))
            if not upload or not upload.filename:
                request.flash("error", self.t(request, "Choose a CSV file to upload."))
            else:
                report = self._process_csv_import(
                    request, model, importable, upload, update_existing
                )
        return self._render(
            request,
            "import.html",
            slug=entry["slug"],
            model_label=entry["label"],
            importable=importable,
            report=report,
            active=entry["slug"],
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": entry["label"], "url": self.prefix + "/" + entry["slug"]},
                {"label": "Import CSV", "url": None},
            ],
        )

    # ── Edit and views helpers ───────────────────────────────

    def _get_edit_title(self, request: Any, entry: dict[str, Any], item: Any) -> str:
        name = entry["label"][:-1] if entry["label"].endswith("s") else entry["label"]
        if item and getattr(item, "id", None):
            return self.t(request, "Edit {name}", name=self.t(request, name))
        return self.t(request, "New {name}", name=self.t(request, name))

    def _build_edit_breadcrumbs(
        self, request: Any, entry: dict[str, Any], item: Any
    ) -> list[dict[str, Any]]:
        is_new = bool(not item or not getattr(item, "id", None))
        return [
            {"label": self.t(request, "Dashboard"), "url": self.prefix},
            {
                "label": self.t(request, entry["label"]),
                "url": self.prefix + "/" + entry["slug"],
            },
            {
                "label": self.t(request, "New") if is_new else _display(item),
                "url": None,
            },
        ]

    def _overlay_form_errors(self, form: Form, errors: dict[str, str] | None) -> None:
        if not errors:
            return
        for fname, err in errors.items():
            if fname in form._fields:
                form._fields[fname]._error = err

    def _pop_meta(self, meta: dict[str, Any] | None, name: str) -> None:
        if meta:
            meta.pop(name, None)

    def _strip_secured_fields(
        self,
        request: Any,
        form: Form,
        meta: dict[str, Any] | None,
        is_role: bool,
        editing_self: bool,
    ) -> None:
        if is_role:
            form._fields.pop("permissions", None)
            self._pop_meta(meta, "permissions")

        if "is_admin" in form._fields:
            is_admin_user = getattr(request.user, "is_admin", False)
            should_strip = bool(editing_self or not is_admin_user)
            if should_strip:
                form._fields.pop("is_admin", None)
                self._pop_meta(meta, "is_admin")

    def _filter_m2m_roles(self, m2m: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in m2m:
            if m["name"] != "roles":
                out.append(m)
        return out

    def _can_edit_roles(self, request: Any) -> bool:
        if getattr(request.user, "is_admin", False):
            return True
        return bool(self._can(request, "roles", "edit"))

    def _is_roles_widget_applicable(
        self, request: Any, is_user: bool, editing_self: bool
    ) -> bool:
        if not is_user:
            return False
        if editing_self:
            return False
        return self._can_edit_roles(request)

    def _build_edit_m2m(
        self,
        request: Any,
        entry: dict[str, Any],
        item: Any,
        is_user: bool,
        editing_self: bool,
    ) -> list[dict[str, Any]]:
        m2m = self._build_m2m(entry["model"], item)
        if self._is_roles_widget_applicable(request, is_user, editing_self):
            w = self._build_user_roles_widget(item)
            if w:
                m2m = [w] + self._filter_m2m_roles(m2m)
        return m2m

    def _is_editing_self(
        self, request: Any, entry: dict[str, Any], item: Any, is_user: bool
    ) -> bool:
        if not item:
            return False
        if not getattr(item, "id", None):
            return False
        if not is_user:
            return False
        return bool(self._is_self(request, entry, item))

    def _get_can_delete_perm(
        self, request: Any, entry: dict[str, Any], editing_self: bool
    ) -> bool:
        if not entry["can_delete"]:
            return False
        if editing_self:
            return False
        return bool(self._can(request, entry["slug"], "delete"))

    def _edit_form(
        self,
        request: Any,
        entry: dict[str, Any],
        item: Any,
        form: Form | None = None,
        meta: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> Any:
        title = self._get_edit_title(request, entry, item)
        breadcrumbs = self._build_edit_breadcrumbs(request, entry, item)

        is_role = entry["model"].__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = entry["model"].__name__ == auth_name

        editing_self = self._is_editing_self(request, entry, item, is_user)

        if form is None:
            form, meta = self._build_form(request, entry, item, errors)
        else:
            self._overlay_form_errors(form, errors)

        self._strip_secured_fields(request, form, meta, is_role, editing_self)
        groups = self._grouped_fields(entry, form, meta)
        m2m = self._build_edit_m2m(request, entry, item, is_user, editing_self)

        permission_matrix = None
        if is_role:
            permission_matrix = self._build_permission_matrix(request, item)

        can_delete_perm = self._get_can_delete_perm(request, entry, editing_self)

        return self._render(
            request,
            "edit.html",
            item=item or type("E", (), {"id": None})(),
            form=form,
            field_groups=groups,
            m2m_fields=m2m,
            permission_matrix=permission_matrix,
            inlines=self._build_inlines(entry, item),
            slug=entry["slug"],
            title=title,
            can_delete=can_delete_perm,
            can_add=entry["can_add"],
            errors_global=(errors or {}).get("_"),
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
            editing_self=editing_self,
        )

    def _build_detail_breadcrumbs(
        self, request: Any, entry: dict[str, Any], item: Any
    ) -> list[dict[str, Any]]:
        name = entry["label"][:-1] if entry["label"].endswith("s") else entry["label"]
        title = _display(item) if item else self.t(request, name)
        return [
            {"label": self.t(request, "Dashboard"), "url": self.prefix},
            {
                "label": self.t(request, entry["label"]),
                "url": self.prefix + "/" + entry["slug"],
            },
            {"label": title, "url": None},
        ]

    def _get_selected_roles(self, widget: dict[str, Any]) -> list[dict[str, Any]]:
        selected = []
        for opt in widget.get("options", []):
            if opt.get("selected", False):
                selected.append({"label": opt["label"]})
        return selected

    def _build_detail_m2m(
        self, entry: dict[str, Any], item: Any, is_user: bool
    ) -> list[dict[str, Any]]:
        m2m = self._build_m2m(entry["model"], item)
        if is_user:
            w = self._build_user_roles_widget(item)
            if w:
                w["current"] = self._get_selected_roles(w)
                m2m = [w] + self._filter_m2m_roles(m2m)
        return m2m

    def _detail(self, request: Any, entry: dict[str, Any], item: Any) -> Any:
        """Render detail view (read-only) for an item."""
        name = entry["label"][:-1] if entry["label"].endswith("s") else entry["label"]
        title = _display(item) if item else self.t(request, name)
        breadcrumbs = self._build_detail_breadcrumbs(request, entry, item)

        is_role = entry["model"].__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = entry["model"].__name__ == auth_name
        viewing_self = self._is_editing_self(request, entry, item, is_user)

        form, meta = self._build_form(request, entry, item, errors=None)

        self._strip_secured_fields(request, form, meta, is_role, viewing_self)
        groups = self._grouped_fields(entry, form, meta)
        m2m = self._build_detail_m2m(entry, item, is_user)

        permission_matrix = None
        if is_role:
            permission_matrix = self._build_permission_matrix(request, item)

        can_edit_perm = self._can(request, entry["slug"], "edit")
        can_delete_perm = self._get_can_delete_perm(request, entry, viewing_self)

        return self._render(
            request,
            "detail.html",
            item=item,
            form=form,
            field_groups=groups,
            m2m_fields=m2m,
            permission_matrix=permission_matrix,
            inlines=self._build_inlines(entry, item),
            slug=entry["slug"],
            title=title,
            can_edit=can_edit_perm,
            can_delete=can_delete_perm,
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
        )

    def _is_field_skipped(
        self, name: str, field: Any, readonly: set[str], form_exclude: set[str]
    ) -> bool:
        if name in readonly:
            return True
        if name in form_exclude:
            return True
        if getattr(field, "protected", False):
            return True
        if getattr(field, "is_timestamp", False):
            return True
        return bool(getattr(field, "is_soft_delete", False))

    def _group_perms(self, perms_list: list[str]) -> dict[str, set[str]]:
        models_with_perms = {}
        for perm in perms_list:
            if "." in perm:
                slug, verb = perm.rsplit(".", 1)
                if slug not in models_with_perms:
                    models_with_perms[slug] = set()
                models_with_perms[slug].add(verb)
        return models_with_perms

    def _rebuild_perms(self, models_with_perms: dict[str, set[str]]) -> list[str]:
        validated_perms = []
        for slug, verbs in models_with_perms.items():
            if verbs:
                if "view" not in verbs:
                    verbs.add("view")
            for verb in verbs:
                validated_perms.append(f"{slug}.{verb}")
        return validated_perms

    def _parse_permissions(self, raw_perms: str) -> str:
        if not raw_perms:
            return ""
        if raw_perms == "*":
            return "*"
        perms_list = [p.strip() for p in raw_perms.split(",") if p.strip()]
        models_with_perms = self._group_perms(perms_list)
        validated_perms = self._rebuild_perms(models_with_perms)
        return ",".join(sorted(validated_perms))

    def _is_blocked_extension(self, filename: str) -> str | None:
        filename_lower = filename.lower()
        for blocked_ext in BLOCKED_EXTENSIONS:
            if filename_lower.endswith(blocked_ext):
                return blocked_ext
        return None

    def _get_upload_mime_type(self, upload: Any) -> str | None:
        if upload.content_type:
            return upload.content_type
        guess = mimetypes.guess_type(upload.filename)[0]
        return guess

    def _validate_file_upload(self, request: Any, upload: Any) -> bool:
        blocked_ext = self._is_blocked_extension(upload.filename)
        if blocked_ext:
            request.flash(
                "error",
                self.t(request, "File type not allowed: {ext}", ext=blocked_ext),
            )
            return False
        mime = self._get_upload_mime_type(upload)
        if mime and mime not in ALLOWED_UPLOAD_MIMES:
            request.flash(
                "error",
                self.t(request, "File type not allowed: {mime}", mime=mime),
            )
            return False
        return True

    def _get_upload_to_path(self, field: Any) -> str:
        upload_to = getattr(field, "upload_to", "")
        if upload_to:
            return upload_to
        return ""

    def _save_uploaded_file(
        self, request: Any, item: Any, name: str, field: Any, upload: Any
    ) -> None:
        upload_to = self._get_upload_to_path(field)
        try:
            upload.save(
                os.path.join(upload_to, upload.filename),
                allowed_types=list(ALLOWED_UPLOAD_MIMES),
            )
            setattr(item, name, upload.filename)
        except ValueError as e:
            request.flash("error", str(e))

    def _handle_file_upload(
        self, request: Any, item: Any, name: str, field: Any
    ) -> None:
        upload = request.files.get(name)
        if not upload:
            return
        if not upload.filename:
            return
        if not self._validate_file_upload(request, upload):
            return
        self._save_uploaded_file(request, item, name, field, upload)

    def _is_morph_id_field(self, name: str, raw: Any, fk_id: str) -> bool:
        if name != fk_id:
            return False
        if not raw:
            return False
        if not isinstance(raw, str):
            return False
        return ":" in raw

    def _apply_morph_fields(
        self, item: Any, fk_type: str, fk_id: str, raw: Any
    ) -> None:
        try:
            type_value, id_value = raw.split(":", 1)
            setattr(item, fk_type, type_value)
            setattr(item, fk_id, int(id_value))
        except Exception:
            setattr(item, fk_type, None)
            setattr(item, fk_id, None)

    def _get_morph_keys(self, rel: Any, rel_name: str) -> tuple[str, str]:
        fk_id = rel.foreign_key
        if not fk_id:
            fk_id = f"{rel_name}_id"
        fk_type = rel.owner_key
        if not fk_type:
            fk_type = f"{rel_name}_type"
        return fk_id, fk_type

    def _handle_morph_to(self, item: Any, name: str, raw: Any, model: Any) -> bool:
        for rel_name, rel in getattr(model, "_relations", {}).items():
            if rel.type == "MorphTo":
                fk_id, fk_type = self._get_morph_keys(rel, rel_name)
                if self._is_morph_id_field(name, raw, fk_id):
                    self._apply_morph_fields(item, fk_type, fk_id, raw)
                    return True
        return False

    def _sanitize_field_content(self, field: Any, raw: Any) -> Any:
        if getattr(field, "wysiwyg", False) and raw:
            from ...utils.html_sanitizer import sanitize_html

            return sanitize_html(raw)
        return raw

    def _safe_int(self, raw: Any) -> Any:
        try:
            return int(raw)
        except Exception:
            return None

    def _coerce_field_integer(self, name: str, raw: Any) -> Any:
        if name.startswith("is_") or name.startswith("has_"):
            return int(raw == "1")
        if raw is None:
            return None
        if raw == "":
            return None
        return self._safe_int(raw)

    def _coerce_field_real(self, raw: Any) -> Any:
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            return None

    def _coerce_field_value(self, name: str, field: Any, raw: Any) -> Any:
        if field.sql_type == "INTEGER":
            return self._coerce_field_integer(name, raw)
        if field.sql_type == "REAL":
            return self._coerce_field_real(raw)
        if getattr(field, "is_boolean", False):
            return int(raw == "1")
        return raw or None

    def _handle_password_field(self, request: Any, name: str, item: Any) -> None:
        val = request.form.get(name)
        if val:
            setattr(item, name, val)

    def _is_permissions_field(self, name: str, is_role: bool) -> bool:
        return bool(is_role and name == "permissions")

    def _is_admin_self_edit(self, name: str, editing_self: bool) -> bool:
        return bool(editing_self and name == "is_admin")

    def _handle_special_apply_fields(
        self,
        request: Any,
        entry: dict[str, Any],
        name: str,
        field: Any,
        item: Any,
        is_role: bool,
        editing_self: bool,
    ) -> bool:
        if getattr(field, "is_password", False):
            self._handle_password_field(request, name, item)
            return True

        if self._is_field_skipped(
            name, field, set(entry["readonly_fields"]), set(entry["form_exclude"])
        ):
            return True

        if self._is_permissions_field(name, is_role):
            raw_perms = (request.form.get("permissions", "") or "").strip()
            setattr(item, "permissions", self._parse_permissions(raw_perms))
            return True

        return self._is_admin_self_edit(name, editing_self)

    def _apply_single_field(
        self,
        request: Any,
        entry: dict[str, Any],
        name: str,
        field: Any,
        item: Any,
        form: Form,
        is_role: bool,
        editing_self: bool,
    ) -> None:
        if self._handle_special_apply_fields(
            request, entry, name, field, item, is_role, editing_self
        ):
            return

        if getattr(field, "is_file", False):
            self._handle_file_upload(request, item, name, field)
            return

        raw = form._fields[name].value if name in form._fields else None
        if self._handle_morph_to(item, name, raw, entry["model"]):
            return

        raw = self._sanitize_field_content(field, raw)
        val = self._coerce_field_value(name, field, raw)
        setattr(item, name, val)

    def _apply_form(
        self, request: Any, entry: dict[str, Any], item: Any, form: Form
    ) -> bool:
        """Copy validated form data + file uploads onto the item.

        Returns True on success, False if there was a save error (which is
        attached to form._fields[name]._error).
        """
        model = entry["model"]
        is_role = model.__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = model.__name__ == auth_name
        editing_self = bool(is_user and self._is_self(request, entry, item))
        for name, field in model._fields.items():
            self._apply_single_field(
                request, entry, name, field, item, form, is_role, editing_self
            )
        return True

    def _is_role_sync_authorized(
        self, request: Any, name: str, editing_self: bool
    ) -> bool:
        if name != "roles":
            return True
        if getattr(request.user, "is_admin", False):
            return True
        return bool(self._can(request, "roles", "edit"))

    def _parse_relation_ids(self, request: Any, name: str) -> list[int]:
        ids_raw = request.form.get(f"m2m_{name}", "")
        return [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]

    def _check_self_role_removal(self, request: Any, ids: list[int]) -> bool:
        if not ids:
            request.flash(
                "error",
                self.t(
                    request,
                    "You cannot remove all your roles. Keep at least one role to maintain access.",
                ),
            )
            return True
        return False

    def _safe_sync(self, item: Any, name: str, ids: list[int]) -> None:
        try:
            item.sync(name, ids)
        except Exception:
            pass

    def _is_roles_self(self, editing_self: bool, name: str) -> bool:
        if not editing_self:
            return False
        return name == "roles"

    def _sync_single_relation(
        self, request: Any, item: Any, name: str, editing_self: bool
    ) -> None:
        if not self._is_role_sync_authorized(request, name, editing_self):
            return

        ids = self._parse_relation_ids(request, name)

        if self._is_roles_self(editing_self, name):
            if self._check_self_role_removal(request, ids):
                return

        self._safe_sync(item, name, ids)

    def _sync_m2m(self, request: Any, model: Any, item: Any) -> None:
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = model.__name__ == auth_name
        is_self_uid = getattr(request.user, "id", None) == getattr(item, "id", None)
        editing_self = bool(is_user and request.user and is_self_uid)

        for name, rel in getattr(model, "_relations", {}).items():
            if rel.type == "BelongsToMany":
                self._sync_single_relation(request, item, name, editing_self)

    def _handle_save_redirect(self, request: Any, slug: str, item_id: Any) -> None:
        if request.form.get("_save_add"):
            raise RedirectException(self.prefix + "/" + slug + "/new")
        if request.form.get("_save_continue"):
            raise RedirectException(self.prefix + "/" + slug + "/" + str(item_id))
        raise RedirectException(self.prefix + "/" + slug)

    def _handle_create_model_error(
        self,
        request: Any,
        entry: dict[str, Any],
        item: Any,
        form: Form,
        meta: Any,
        e: ModelError,
    ) -> Any:
        errors = {e.field: str(e)} if e.field else {"_": str(e)}
        return self._edit_form(
            request, entry, item, form=form, meta=meta, errors=errors
        )

    def _save_and_finalize_creation(
        self, request: Any, entry: dict[str, Any], item: Any, form: Form, meta: Any
    ) -> None:
        try:
            item.save()
        except ModelError as e:
            raise e
        self._sync_m2m(request, entry["model"], item)
        self._log(request, "created", entry["model"].__name__, entity_id=item.id)
        request.flash(
            "success", self.t(request, "{label} created", label=entry["label"][:-1])
        )
        self._handle_save_redirect(request, entry["slug"], item.id)

    def _create(self, request: Any, entry: dict[str, Any]) -> Any:
        try:
            item = entry["model"]()
            form, meta = self._build_form(request, entry, item)

            if not form.validate():
                return self._edit_form(request, entry, item, form=form, meta=meta)

            self._apply_form(request, entry, item, form)

            try:
                self._save_and_finalize_creation(request, entry, item, form, meta)
            except ModelError as e:
                return self._handle_create_model_error(
                    request, entry, item, form, meta, e
                )
        except RedirectException:
            raise
        except Exception as e:
            return self._edit_form(
                request,
                entry,
                entry["model"](),
                errors={"_": f"Server crash: {str(e)}"},
            )

    def _has_privilege_diff(self, diff: dict[str, Any]) -> bool:
        privilege_fields = ["is_admin", "roles", "role", "permissions"]
        return any(field in diff for field in privilege_fields)

    def _check_privilege_change_session_regeneration(
        self,
        request: Any,
        entry: dict[str, Any],
        item: Any,
        diff: dict[str, Any] | None,
    ) -> None:
        if not diff:
            return
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        if entry["model"].__name__ != auth_name:
            return

        user_id = getattr(request.user, "id", None)
        if user_id == item.id:
            if self._has_privilege_diff(diff):
                request.session_regenerate()

    def _save_and_finalize_update(
        self, request: Any, entry: dict[str, Any], item: Any, before: Any
    ) -> None:
        try:
            item.save()
        except ModelError as e:
            raise e

        self._sync_m2m(request, entry["model"], item)
        diff = self._diff(before, self._snapshot(item))
        if diff:
            self._log(
                request,
                "update",
                entry["model"].__name__,
                entity_id=item.id,
                changes=diff,
            )

        self._check_privilege_change_session_regeneration(request, entry, item, diff)
        request.flash(
            "success", self.t(request, "{label} updated", label=entry["label"][:-1])
        )
        self._handle_save_redirect(request, entry["slug"], item.id)

    def _update(self, request: Any, entry: dict[str, Any], item: Any) -> Any:
        try:
            before = self._snapshot(item)
            form, meta = self._build_form(request, entry, item)
            if not form.validate():
                return self._edit_form(request, entry, item, form=form, meta=meta)
            self._apply_form(request, entry, item, form)
            try:
                self._save_and_finalize_update(request, entry, item, before)
            except ModelError as e:
                return self._handle_create_model_error(
                    request, entry, item, form, meta, e
                )
        except RedirectException:
            raise
        except Exception as e:
            traceback.print_exc()
            return self._edit_form(
                request, entry, item, errors={"_": f"Server crash: {str(e)}"}
            )
