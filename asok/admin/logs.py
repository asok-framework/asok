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
        try:
            log = AdminLog()
            log.user_id = getattr(request.user, "id", None) if request.user else None
            log.action = action
            log.entity = entity
            log.entity_id = entity_id
            if changes:
                log.changes = json.dumps(changes)
            try:
                log.save()
            except Exception:
                pass
        except Exception:
            pass

    def _recent_logs(self, limit: int = 10) -> list[Any]:
        AdminLog = MODELS_REGISTRY.get("AdminLog")
        if not AdminLog:
            return []
        try:
            return AdminLog.query().order_by("-id").limit(limit).get()
        except Exception:
            return []

    def _history(self, request: Any, entry: dict[str, Any], item: Any) -> Any:
        """Show the audit-log timeline for a single object."""
        if not self._can(request, "logs", "view") and not self._can(
            request, entry["slug"], "edit"
        ):
            return self._forbid(request)
        AdminLog = MODELS_REGISTRY.get("AdminLog")
        entries = []
        if AdminLog:
            try:
                rows = (
                    AdminLog.query()
                    .where("entity", entry["model"].__name__)
                    .where("entity_id", item.id)
                    .order_by("-id")
                    .limit(200)
                    .get()
                )
            except Exception:
                rows = []
            auth_name = self.app.config.get("AUTH_MODEL", "User")
            User = MODELS_REGISTRY.get(auth_name)
            user_cache = {}
            for log in rows:
                label = "—"
                if log.user_id:
                    if log.user_id not in user_cache and User:
                        u = User.find(id=log.user_id)
                        user_cache[log.user_id] = (
                            _display(u) if u else f"#{log.user_id}"
                        )
                    label = user_cache.get(log.user_id, f"#{log.user_id}")
                changes = []
                raw = log.changes or ""
                if raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            for k, v in parsed.items():
                                if isinstance(v, list) and len(v) == 2:
                                    changes.append(
                                        {"field": k, "old": v[0], "new": v[1]}
                                    )
                                else:
                                    changes.append(
                                        {"field": k, "old": "", "new": str(v)}
                                    )
                    except Exception:
                        pass
                entries.append(
                    {
                        "id": log.id,
                        "when": log.created_at,
                        "who": label,
                        "action": log.action,
                        "changes": changes,
                    }
                )
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
                    "label": _display(item) or f"#{item.id}",
                    "url": self.prefix + "/" + entry["slug"] + "/" + str(item.id),
                },
                {"label": "History", "url": None},
            ],
        )
