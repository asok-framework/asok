from __future__ import annotations

import json
from typing import Any

from ..orm import MODELS_REGISTRY
from .utils import _display


class LogMixin:
    """Mixin for audit logging in Asok Admin."""

    def _snapshot(self, item: Any) -> dict[str, Any]:
        """Capture field values for diff computation. Skips passwords."""
        if not item:
            return {}
        snap = {}
        for name, field in item._fields.items():
            if getattr(field, "is_password", False):
                continue
            snap[name] = getattr(item, name, None)
        return snap

    def _diff(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> dict[str, list[Any]]:
        """Return {field: [old, new]} for values that changed."""
        out = {}
        keys = set(before) | set(after)
        for k in keys:
            a = before.get(k)
            b = after.get(k)
            if a != b:
                # Convert FileRef/Model to string for JSON
                out[k] = [
                    None if a is None else str(a),
                    None if b is None else str(b),
                ]
        return out

    def _create_log_entry(self, AdminLog: Any, user_id: Any, action: str, entity: str, entity_id: Any, changes: Any) -> None:
        try:
            log = AdminLog()
            log.user_id = user_id
            log.action = action
            log.entity = entity
            log.entity_id = entity_id
            if changes:
                log.changes = json.dumps(changes)
            log.save()
        except Exception:
            pass

    def _log(
        self,
        request: Any,
        action: str,
        entity: str,
        entity_id: int | None = None,
        changes: dict[str, Any] | None = None,
    ) -> None:
        """Write an AdminLog row. Failures are silent (never break the UI)."""
        AdminLog = MODELS_REGISTRY.get("AdminLog")
        if not AdminLog:
            return
        user_id = getattr(request.user, "id", None) if request.user else None
        self._create_log_entry(AdminLog, user_id, action, entity, entity_id, changes)

    def _recent_logs(self, limit: int = 10) -> list[Any]:
        AdminLog = MODELS_REGISTRY.get("AdminLog")
        if not AdminLog:
            return []
        try:
            return AdminLog.query().order_by("-id").limit(limit).get()
        except Exception:
            return []

    def _fetch_log_rows(self, AdminLog: Any, entry: dict[str, Any], item: Any) -> list:
        """Fetch audit log rows for a specific entity and item."""
        try:
            return (
                AdminLog.query()
                .where("entity", entry["model"].__name__)
                .where("entity_id", item.id)
                .order_by("-id")
                .limit(200)
                .get()
            )
        except Exception:
            return []

    def _cache_log_user(self, User: Any, user_id: Any, user_cache: dict) -> None:
        if not User or user_id in user_cache:
            return
        u = User.find(id=user_id)
        user_cache[user_id] = _display(u) if u else f"#{user_id}"

    def _build_user_cache(self, rows: list) -> dict:
        """Build a {user_id: display_name} cache from log rows."""
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)
        user_cache = {}
        for log in rows:
            if log.user_id:
                self._cache_log_user(User, log.user_id, user_cache)
        return user_cache

    def _format_parsed_changes(self, parsed: dict) -> list:
        changes = []
        for k, v in parsed.items():
            if isinstance(v, list) and len(v) == 2:
                changes.append({"field": k, "old": v[0], "new": v[1]})
            else:
                changes.append({"field": k, "old": "", "new": str(v)})
        return changes

    def _parse_log_changes(self, raw: str) -> list:
        """Parse a JSON changes blob into a list of {field, old, new} dicts."""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return self._format_parsed_changes(parsed)
        except Exception:
            pass
        return []

    def _get_history_entries(self, rows: list, user_cache: dict) -> list[dict]:
        entries = []
        for log in rows:
            label = "—"
            if log.user_id:
                label = user_cache.get(log.user_id, f"#{log.user_id}")
            changes = self._parse_log_changes(log.changes or "")
            entries.append({
                "id": log.id,
                "when": log.created_at,
                "who": label,
                "action": log.action,
                "changes": changes,
            })
        return entries

    def _can_view_history(self, request: Any, slug: str) -> bool:
        return self._can(request, "logs", "view") or self._can(request, slug, "edit")

    def _history(self, request: Any, entry: dict[str, Any], item: Any) -> Any:
        """Show the audit-log timeline for a single object."""
        if not self._can_view_history(request, entry["slug"]):
            return self._forbid(request)
        AdminLog = MODELS_REGISTRY.get("AdminLog")
        entries = []
        if AdminLog:
            rows = self._fetch_log_rows(AdminLog, entry, item)
            user_cache = self._build_user_cache(rows)
            entries = self._get_history_entries(rows, user_cache)

        label = _display(item)
        if not label:
            label = f"#{item.id}"

        return self._render(
            request,
            "history.html",
            slug=entry["slug"],
            model_label=entry["label"],
            item=item,
            entries=entries,
            active=entry["slug"],
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": entry["label"], "url": self.prefix + "/" + entry["slug"]},
                {
                    "label": label,
                    "url": self.prefix + "/" + entry["slug"] + "/" + str(item.id),
                },
                {"label": "History", "url": None},
            ],
        )
