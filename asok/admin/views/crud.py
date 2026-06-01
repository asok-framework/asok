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

    def _list(self, request: Any, entry: dict[str, Any], trash: bool = False) -> Any:
        model = entry["model"]
        per_page = entry["per_page"]
        page = max(1, int(request.args.get("page", 1) or 1))
        q = request.args.get("q", "") or ""
        sort = request.args.get("sort", "-id") or "-id"

        query = self._build_query(request, entry, with_trashed=trash)
        try:
            query = query.order_by(sort)
        except ValueError:
            query = query.order_by("-id")

        total = query.count()
        pages = max(1, (total + per_page - 1) // per_page)
        # Ensure page doesn't exceed max pages
        page = max(1, min(page, pages))
        items = query.limit(per_page).offset((page - 1) * per_page).get()

        item_dicts = []
        for it in items:
            d = {"id": it.id}
            for col in entry["columns"]:
                d[col] = self._col_value(it, col, model)
            item_dicts.append(d)

        # Fields available for bulk-edit (simple scalar columns only)
        bulk_edit_fields = []
        if (
            not trash
            and entry["can_edit"]
            and self._can(request, entry["slug"], "edit")
        ):
            for n, f in model._fields.items():
                if getattr(f, "is_password", False):
                    continue
                if getattr(f, "is_file", False):
                    continue
                if getattr(f, "is_timestamp", False):
                    continue
                if getattr(f, "is_soft_delete", False):
                    continue
                if getattr(f, "is_foreign_key", False):
                    continue
                if n in entry["readonly_fields"]:
                    continue
                bulk_edit_fields.append(
                    {"name": n, "label": n.replace("_", " ").title()}
                )

        bulk_actions = []
        if trash:
            bulk_actions = [
                {"name": "restore", "label": "Restore selected"},
                {"name": "force_delete", "label": "Delete permanently"},
            ]
        else:
            # SECURITY FIX: Vérifier les permissions RBAC, pas seulement l'option statique
            if entry["can_delete"] and self._can(request, entry["slug"], "delete"):
                bulk_actions.append({"name": "delete", "label": "Delete selected"})
            for act in entry["actions"]:
                bulk_actions.append(
                    {"name": act, "label": act.replace("_", " ").title()}
                )

        breadcrumbs = [
            {"label": "Dashboard", "url": self.prefix},
            {
                "label": entry["label"],
                "url": None if not trash else self.prefix + "/" + entry["slug"],
            },
        ]
        if trash:
            breadcrumbs.append({"label": "Trash", "url": None})

        # Check if this model is the User/Auth model
        auth_model_name = self.app.config.get("AUTH_MODEL", "User")
        is_auth_model = entry["model"].__name__ == auth_model_name

        # SECURITY: Check user-specific RBAC permissions for action buttons
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
            has_soft_delete=bool(model._soft_delete_field),
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

    # ── Bulk + custom actions ────────────────────────────────

    def _bulk_action(self, request: Any, entry: dict[str, Any]) -> None:
        action = request.form.get("action")
        ids_raw = request.form.get("ids", "")
        ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        if not ids or not action:
            raise RedirectException(self.prefix + "/" + entry["slug"])
        model = entry["model"]
        slug = entry["slug"]
        # Prevent self-targeting on the User model
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        if (
            model.__name__ == auth_name
            and request.user
            and getattr(request.user, "id", None) in ids
        ):
            ids = [i for i in ids if i != request.user.id]
            request.flash(
                "error", self.t(request, "You cannot target your own account.")
            )
            if not ids:
                raise RedirectException(self.prefix + "/" + slug)
        if action == "delete":
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
        elif action == "force_delete" and model._soft_delete_field:
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
        elif action == "restore" and model._soft_delete_field:
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
        elif action.startswith("set:"):
            if not entry["can_edit"] or not self._can(request, slug, "edit"):
                return self._forbid(request)
            field_name = action[4:]
            field = model._fields.get(field_name)
            if not field:
                request.flash(
                    "error",
                    self.t(request, "Unknown field '{field}'", field=field_name),
                )
                raise RedirectException(self.prefix + "/" + slug)
            raw = request.form.get("bulk_value", "")
            # Coerce to the field's type
            if field.sql_type == "INTEGER":
                if field_name.startswith("is_") or field_name.startswith("has_"):
                    val = 1 if raw in ("1", "on", "true", "yes") else 0
                elif raw in (None, ""):
                    val = None
                else:
                    try:
                        val = int(raw)
                    except (ValueError, TypeError):
                        val = None
            elif field.sql_type == "REAL":
                try:
                    val = float(raw) if raw else None
                except (ValueError, TypeError):
                    val = None
            else:
                val = raw or None
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
            self._log(
                request,
                "bulk_edit",
                model.__name__,
                entity_id=None,
                changes={"ids": ids, "field": field_name, "value": str(val)},
            )
            request.flash(
                "success", self.t(request, "Updated {count} items", count=count)
            )
        elif action in entry["actions"]:
            fn = getattr(model, action, None)
            if not fn:
                request.flash(
                    "error",
                    self.t(
                        request, "Action '{action}' not found on model", action=action
                    ),
                )
            else:
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
        raise RedirectException(self.prefix + "/" + entry["slug"])

    # ── CSV export ───────────────────────────────────────────

    def _export_csv(self, request: Any, entry: dict[str, Any]) -> str:
        query = self._build_query(request, entry)
        try:
            query = query.order_by(request.args.get("sort", "-id") or "-id")
        except ValueError:
            query = query.order_by("-id")
        items = query.get()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(entry["columns"])
        for it in items:
            row = []
            for col in entry["columns"]:
                v = (
                    getattr(it, col, "")
                    if col in entry["model"]._fields or col == "id"
                    else (
                        getattr(it, col)()
                        if callable(getattr(it, col, None))
                        else getattr(it, col, "")
                    )
                )
                row.append("" if v is None else str(v))
            writer.writerow(row)
        data = buf.getvalue().encode("utf-8")
        request.content_type = "text/csv"
        request.environ["asok.binary_response"] = data
        request.environ["asok.extra_headers"] = [
            ("Content-Disposition", f'attachment; filename="{entry["slug"]}.csv"')
        ]
        return ""

    # ── FK autocomplete lookup ───────────────────────────────

    def _lookup(self, request: Any, entry: dict[str, Any]) -> str:
        """Return JSON [{id,label}] for FK autocomplete on this model."""
        model = entry["model"]
        q = (request.args.get("q", "") or "").strip()
        try:
            limit = max(1, min(50, int(request.args.get("limit", 20) or 20)))
        except ValueError:
            limit = 20
        query = model.query()
        if q:
            searchable = entry["searchable"] or []
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
        try:
            items = query.order_by("-id").limit(limit).get()
        except Exception:
            items = []
        data = [{"id": o.id, "label": _display(o)} for o in items]
        request.content_type = "application/json"
        return json.dumps(data)

    # ── CSV import ───────────────────────────────────────────

    def _importable_fields(self, model: Any) -> list[str]:
        """Return field names that can be imported (skip auto/timestamps/passwords/files)."""
        out = []
        for name, field in model._fields.items():
            if getattr(field, "protected", False):
                continue
            if getattr(field, "is_timestamp", False):
                continue
            if getattr(field, "is_soft_delete", False):
                continue
            if getattr(field, "is_password", False):
                continue
            if getattr(field, "is_file", False):
                continue
            if getattr(field, "is_slug", False) and field.populate_from:
                continue
            out.append(name)
        return out

    def _import_csv(self, request: Any, entry: dict[str, Any]) -> Any:
        model = entry["model"]
        importable = self._importable_fields(model)
        report = None
        if request.method == "POST":
            upload = request.files.get("file") if request.files else None
            update_existing = bool(request.form.get("update_existing"))
            if not upload or not upload.filename:
                request.flash("error", self.t(request, "Choose a CSV file to upload."))
            else:
                try:
                    # SECURITY: Check file size before reading (max 10MB)
                    MAX_CSV_SIZE = 10 * 1024 * 1024  # 10MB
                    if hasattr(upload, "file"):
                        upload.file.seek(0, 2)  # Seek to end
                        file_size = upload.file.tell()
                        upload.file.seek(0)  # Reset to beginning
                    elif hasattr(upload, "content_length"):
                        file_size = upload.content_length
                    else:
                        file_size = 0

                    if file_size > MAX_CSV_SIZE:
                        request.flash(
                            "error",
                            self.t(
                                request,
                                "File size exceeds maximum allowed (10MB). Please upload a smaller file.",
                            ),
                        )
                        raise ValueError("File too large")

                    raw = (
                        upload.file.read() if hasattr(upload, "file") else upload.read()
                    )
                    if isinstance(raw, bytes):
                        text = raw.decode("utf-8-sig")
                    else:
                        text = raw
                    reader = csv.DictReader(io.StringIO(text))
                    created = 0
                    updated = 0
                    failed = 0
                    errors = []
                    for i, row in enumerate(reader, start=2):
                        data = {}
                        for k, v in row.items():
                            if not k:
                                continue
                            key = k.strip()
                            # Special case: allow 'id' even if not in _importable_fields
                            if key != "id" and key not in importable:
                                continue
                            field = model._fields.get(key)
                            val = (v or "").strip() if v is not None else ""
                            if val == "":
                                data[key] = None
                                continue

                            # Handle ID separately
                            if key == "id":
                                try:
                                    data[key] = int(val)
                                except ValueError:
                                    pass
                                continue

                            if field.sql_type == "INTEGER":
                                if key.startswith("is_") or key.startswith("has_"):
                                    data[key] = (
                                        1
                                        if val.lower() in ("1", "true", "yes", "on")
                                        else 0
                                    )
                                else:
                                    try:
                                        data[key] = int(val)
                                    except ValueError:
                                        data[key] = None
                            elif field.sql_type == "REAL":
                                try:
                                    data[key] = float(val)
                                except ValueError:
                                    data[key] = None
                            else:
                                data[key] = val
                        try:
                            item = None
                            if update_existing:
                                # 1. Try to find by explicit ID
                                if data.get("id"):
                                    item = model.find(id=data["id"])

                                # 2. Try to find by unique fields
                                if not item:
                                    for f_name, f_obj in model._fields.items():
                                        if getattr(f_obj, "unique", False) and data.get(
                                            f_name
                                        ):
                                            item = model.find(**{f_name: data[f_name]})
                                            if item:
                                                break

                            if item:
                                # Update existing
                                # Remove ID from data to avoid trying to update PK
                                data.pop("id", None)
                                item.update(**data)
                                updated += 1
                            else:
                                # Create new
                                model.create(**data)
                                created += 1
                        except Exception as e:
                            failed += 1
                            if len(errors) < 10:
                                errors.append(f"Row {i}: {e}")
                    self._log(
                        request,
                        "import_csv",
                        model.__name__,
                        entity_id=None,
                        changes={
                            "created": created,
                            "updated": updated,
                            "failed": failed,
                        },
                    )
                    report = {
                        "created": created,
                        "updated": updated,
                        "failed": failed,
                        "errors": errors,
                    }
                    if failed == 0:
                        msg = self.t(
                            request, "Imported {count} rows", count=created + updated
                        )
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
                except Exception as e:
                    request.flash(
                        "error", self.t(request, "CSV parse error: {error}", error=e)
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

    def _edit_form(
        self,
        request: Any,
        entry: dict[str, Any],
        item: Any,
        form: Form | None = None,
        meta: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> Any:
        name = entry["label"][:-1] if entry["label"].endswith("s") else entry["label"]
        if item and getattr(item, "id", None):
            title = self.t(request, "Edit {name}", name=self.t(request, name))
        else:
            title = self.t(request, "New {name}", name=self.t(request, name))

        breadcrumbs = [
            {"label": self.t(request, "Dashboard"), "url": self.prefix},
            {
                "label": self.t(request, entry["label"]),
                "url": self.prefix + "/" + entry["slug"],
            },
            {
                "label": _display(item)
                if item and getattr(item, "id", None)
                else self.t(request, "New"),
                "url": None,
            },
        ]
        is_role = entry["model"].__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = entry["model"].__name__ == auth_name
        editing_self = (
            is_user and self._is_self(request, entry, item)
            if item and getattr(item, "id", None)
            else False
        )

        if form is None:
            form, meta = self._build_form(request, entry, item, errors)
        elif errors:
            for fname, err in errors.items():
                if fname in form._fields:
                    form._fields[fname]._error = err

        # Strip 'permissions' from Role form — it's replaced by the matrix
        if is_role and "permissions" in form._fields:
            form._fields.pop("permissions", None)
            if meta and "permissions" in meta:
                meta.pop("permissions", None)

        # Strip is_admin from self-edit (prevent self-demotion)
        if editing_self and "is_admin" in form._fields:
            form._fields.pop("is_admin", None)
            if meta and "is_admin" in meta:
                meta.pop("is_admin", None)

        # SECURITY: Prevent privilege escalation - only admins can grant/revoke is_admin
        if not getattr(request.user, "is_admin", False) and "is_admin" in form._fields:
            form._fields.pop("is_admin", None)
            if meta and "is_admin" in meta:
                meta.pop("is_admin", None)

        groups = self._grouped_fields(entry, form, meta)

        # Build m2m list; for User, inject roles widget unless editing self
        m2m = self._build_m2m(entry["model"], item)
        # SECURITY: Only show roles widget to admins or users with roles.edit permission
        can_edit_roles = getattr(request.user, "is_admin", False) or self._can(
            request, "roles", "edit"
        )
        if is_user and not editing_self and can_edit_roles:
            w = self._build_user_roles_widget(item)
            if w:
                m2m = [w] + [m for m in m2m if m["name"] != "roles"]

        permission_matrix = (
            self._build_permission_matrix(request, item) if is_role else None
        )

        # SECURITY FIX: Check RBAC permissions for the delete button
        # Do not only check entry["can_delete"] (static option)
        # but also self._can() which checks user permissions
        can_delete_permission = (
            entry["can_delete"]
            and not editing_self
            and self._can(request, entry["slug"], "delete")
        )

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
            can_delete=can_delete_permission,
            can_add=entry["can_add"],
            errors_global=(errors or {}).get("_"),
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
            editing_self=editing_self,
        )

    def _detail(self, request: Any, entry: dict[str, Any], item: Any) -> Any:
        """Render detail view (read-only) for an item."""
        name = entry["label"][:-1] if entry["label"].endswith("s") else entry["label"]
        title = _display(item) if item else self.t(request, name)

        breadcrumbs = [
            {"label": self.t(request, "Dashboard"), "url": self.prefix},
            {
                "label": self.t(request, entry["label"]),
                "url": self.prefix + "/" + entry["slug"],
            },
            {"label": _display(item), "url": None},
        ]

        is_role = entry["model"].__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = entry["model"].__name__ == auth_name
        viewing_self = is_user and self._is_self(request, entry, item)

        # Build form just to get field structure, but we won't use it for editing
        form, meta = self._build_form(request, entry, item, errors=None)

        # Strip 'permissions' from Role form — it's replaced by the matrix
        if is_role and "permissions" in form._fields:
            form._fields.pop("permissions", None)
            if meta and "permissions" in meta:
                meta.pop("permissions", None)

        # Strip is_admin from self-view
        if viewing_self and "is_admin" in form._fields:
            form._fields.pop("is_admin", None)
            if meta and "is_admin" in meta:
                meta.pop("is_admin", None)

        groups = self._grouped_fields(entry, form, meta)

        # Build m2m list
        m2m = self._build_m2m(entry["model"], item)
        # For User model, always show roles in detail view (read-only, safe even for self)
        if is_user:
            w = self._build_user_roles_widget(item)
            if w:
                # Convert widget format to detail view format
                # Extract only selected roles for display
                selected_roles = [
                    {"label": opt["label"]}
                    for opt in w.get("options", [])
                    if opt.get("selected", False)
                ]
                w["current"] = selected_roles
                m2m = [w] + [m for m in m2m if m["name"] != "roles"]

        permission_matrix = (
            self._build_permission_matrix(request, item) if is_role else None
        )

        # SECURITY: Check permissions for action buttons
        can_edit_permission = self._can(request, entry["slug"], "edit")
        can_delete_permission = (
            entry["can_delete"]
            and not viewing_self
            and self._can(request, entry["slug"], "delete")
        )

        return self._render(
            request,
            "detail.html",
            item=item,
            form=form,  # Used only for field structure
            field_groups=groups,
            m2m_fields=m2m,
            permission_matrix=permission_matrix,
            inlines=self._build_inlines(entry, item),
            slug=entry["slug"],
            title=title,
            can_edit=can_edit_permission,
            can_delete=can_delete_permission,
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
        )

    def _apply_form(
        self, request: Any, entry: dict[str, Any], item: Any, form: Form
    ) -> bool:
        """Copy validated form data + file uploads onto the item.

        Returns True on success, False if there was a save error (which is
        attached to form._fields[name]._error).
        """
        model = entry["model"]
        readonly = set(entry["readonly_fields"])
        form_exclude = set(entry["form_exclude"])
        is_role = model.__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = model.__name__ == auth_name
        editing_self = is_user and self._is_self(request, entry, item)
        for name, field in model._fields.items():
            # Handle password fields BEFORE the protected check (passwords are protected but need special handling)
            if getattr(field, "is_password", False):
                val = request.form.get(name)
                if val:
                    setattr(item, name, val)
                continue

            if (
                name in readonly
                or name in form_exclude
                or getattr(field, "protected", False)
            ):
                continue
            if getattr(field, "is_timestamp", False):
                continue
            if getattr(field, "is_soft_delete", False):
                continue
            # Role: permissions handled separately from the matrix POST
            if is_role and name == "permissions":
                raw_perms = (request.form.get("permissions", "") or "").strip()
                # PERMISSION DEPENDENCY: Enforce that "view" is required for all other permissions
                # This prevents illogical permissions like "delete without view"
                if raw_perms and raw_perms != "*":
                    perms_list = [p.strip() for p in raw_perms.split(",") if p.strip()]
                    # Group permissions by model slug
                    models_with_perms = {}
                    for perm in perms_list:
                        if "." in perm:
                            slug, verb = perm.rsplit(".", 1)
                            if slug not in models_with_perms:
                                models_with_perms[slug] = set()
                            models_with_perms[slug].add(verb)

                    # For each model with any permission, ensure "view" is included
                    validated_perms = []
                    for slug, verbs in models_with_perms.items():
                        # Auto-add "view" if any other permission exists
                        if verbs and "view" not in verbs:
                            verbs.add("view")
                        # Rebuild permission strings
                        for verb in verbs:
                            validated_perms.append(f"{slug}.{verb}")

                    item.permissions = ",".join(sorted(validated_perms))
                else:
                    item.permissions = raw_perms
                continue
            # Self-edit: never allow changing is_admin on yourself
            if editing_self and name == "is_admin":
                continue
            if getattr(field, "is_file", False):
                upload = request.files.get(name)
                if upload and upload.filename:
                    # SECURITY: Validate file extension and MIME type
                    filename_lower = upload.filename.lower()

                    # Check blocked extensions
                    for blocked_ext in BLOCKED_EXTENSIONS:
                        if filename_lower.endswith(blocked_ext):
                            request.flash(
                                "error",
                                self.t(
                                    request,
                                    "File type not allowed: {ext}",
                                    ext=blocked_ext,
                                ),
                            )
                            continue

                    # Validate MIME type
                    mime_type = (
                        upload.content_type or mimetypes.guess_type(upload.filename)[0]
                    )
                    if mime_type and mime_type not in ALLOWED_UPLOAD_MIMES:
                        request.flash(
                            "error",
                            self.t(
                                request, "File type not allowed: {mime}", mime=mime_type
                            ),
                        )
                        continue

                    # Save the file
                    try:
                        upload.save(
                            os.path.join(field.upload_to or "", upload.filename)
                        )
                        setattr(item, name, upload.filename)
                    except ValueError as e:
                        # Capture validation errors (invalid magic bytes, etc.)
                        request.flash("error", str(e))
                continue
            raw = form._fields[name].value if name in form._fields else None

            # SECURITY: Sanitize WYSIWYG content to prevent Stored XSS
            if getattr(field, "wysiwyg", False) and raw:
                from ...utils.html_sanitizer import sanitize_html

                raw = sanitize_html(raw)

            if field.sql_type == "INTEGER":
                if name.startswith("is_") or name.startswith("has_"):
                    # Checkbox: raw is "0" or "1" (string), not boolean
                    setattr(item, name, 1 if raw == "1" else 0)
                elif raw in (None, ""):
                    setattr(item, name, None)
                else:
                    try:
                        setattr(item, name, int(raw))
                    except (ValueError, TypeError):
                        setattr(item, name, None)
            elif field.sql_type == "REAL":
                try:
                    setattr(item, name, float(raw) if raw else None)
                except (ValueError, TypeError):
                    setattr(item, name, None)
            elif getattr(field, "is_boolean", False):
                # Boolean checkbox: raw is "0" or "1" (string)
                setattr(item, name, 1 if raw == "1" else 0)
            else:
                setattr(item, name, raw or None)
        return True

    def _sync_m2m(self, request: Any, model: Any, item: Any) -> None:
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = model.__name__ == auth_name
        editing_self = (
            is_user
            and request.user
            and getattr(request.user, "id", None) == getattr(item, "id", None)
        )
        for name, rel in getattr(model, "_relations", {}).items():
            if rel.type != "BelongsToMany":
                continue

            # SECURITY: Self-protection - prevent users from removing ALL their roles
            # Users can change their roles, but must keep at least one to maintain access
            if editing_self and name == "roles":
                ids_raw = request.form.get(f"m2m_{name}", "")
                ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]

                # Check if user is trying to remove all roles
                if not ids:
                    request.flash(
                        "error",
                        self.t(
                            request,
                            "You cannot remove all your roles. Keep at least one role to maintain access.",
                        ),
                    )
                    continue  # Skip this sync, keep existing roles

                # Allow the change if at least one role remains
                try:
                    item.sync(name, ids)
                except Exception:
                    pass
                continue

            # SECURITY: Only admins or users with role.edit permission can assign roles
            if name == "roles" and not getattr(request.user, "is_admin", False):
                if not self._can(request, "roles", "edit"):
                    continue
            ids_raw = request.form.get(f"m2m_{name}", "")
            ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
            try:
                item.sync(name, ids)
            except Exception:
                pass

    def _create(self, request: Any, entry: dict[str, Any]) -> Any:
        try:
            item = entry["model"]()
            form, meta = self._build_form(request, entry, item)

            if not form.validate():
                return self._edit_form(request, entry, item, form=form, meta=meta)

            self._apply_form(request, entry, item, form)

            try:
                item.save()
            except ModelError as e:
                errors = {e.field: str(e)} if e.field else {"_": str(e)}
                return self._edit_form(
                    request, entry, item, form=form, meta=meta, errors=errors
                )

            self._sync_m2m(request, entry["model"], item)
            self._log(request, "created", entry["model"].__name__, entity_id=item.id)

            request.flash(
                "success", self.t(request, "{label} created", label=entry["label"][:-1])
            )
            if request.form.get("_save_add"):
                raise RedirectException(self.prefix + "/" + entry["slug"] + "/new")
            if request.form.get("_save_continue"):
                raise RedirectException(
                    self.prefix + "/" + entry["slug"] + "/" + str(item.id)
                )
            raise RedirectException(self.prefix + "/" + entry["slug"])
        except RedirectException:
            raise
        except Exception as e:
            return self._edit_form(
                request,
                entry,
                entry["model"](),
                errors={"_": f"Server crash: {str(e)}"},
            )

    def _update(self, request: Any, entry: dict[str, Any], item: Any) -> Any:
        try:
            before = self._snapshot(item)
            form, meta = self._build_form(request, entry, item)
            if not form.validate():
                return self._edit_form(request, entry, item, form=form, meta=meta)
            self._apply_form(request, entry, item, form)
            try:
                item.save()
            except ModelError as e:
                errors = {e.field: str(e)} if e.field else {"_": str(e)}
                return self._edit_form(
                    request, entry, item, form=form, meta=meta, errors=errors
                )
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

            # SECURITY: Regenerate session if current user's privileges changed
            auth_name = self.app.config.get("AUTH_MODEL", "User")
            if (
                entry["model"].__name__ == auth_name
                and request.user
                and item.id == request.user.id
            ):
                # Check if security-relevant fields changed
                privilege_fields = ["is_admin", "roles", "role", "permissions"]
                if diff and any(field in diff for field in privilege_fields):
                    request.session_regenerate()

            request.flash(
                "success", self.t(request, "{label} updated", label=entry["label"][:-1])
            )
            if request.form.get("_save_add"):
                raise RedirectException(self.prefix + "/" + entry["slug"] + "/new")
            if request.form.get("_save_continue"):
                raise RedirectException(
                    self.prefix + "/" + entry["slug"] + "/" + str(item.id)
                )
            raise RedirectException(self.prefix + "/" + entry["slug"])
        except RedirectException:
            raise
        except Exception as e:
            traceback.print_exc()
            return self._edit_form(
                request, entry, item, errors={"_": f"Server crash: {str(e)}"}
            )
