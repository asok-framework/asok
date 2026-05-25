from __future__ import annotations

import logging
from typing import Any

from ..exceptions import RedirectException
from ..orm import MODELS_REGISTRY, ModelList

logger = logging.getLogger(__name__)


def _user_roles_accessor(self: Any) -> ModelList:
    """BelongsToMany accessor for User.roles — reads the role_user pivot."""
    cached = self.__dict__.get("_eager_roles")
    if cached is not None:
        return cached
    Role = MODELS_REGISTRY.get("Role")
    if not Role or not self.id:
        return ModelList()
    sql = (
        f"SELECT r.* FROM {Role._table} r "
        f"JOIN role_user p ON p.role_id = r.id "
        f"WHERE p.user_id = ?"
    )
    with self._get_conn() as conn:
        rows = conn.execute(sql, (self.id,)).fetchall()
    return ModelList(Role(**dict(row)) for row in rows)


def _user_role_ids(self: Any) -> list[int]:
    return [r.id for r in self.roles]


def _user_can(self: Any, perm: str) -> bool:
    """Check if this user has a permission.

    Permission format: "<slug>.<verb>" (e.g. "posts.edit").
    Special values: "*" (superuser), "<slug>.*" (all verbs on a slug).
    `is_admin = True` users bypass all checks.

    SECURITY NOTE: Users with `is_admin = True` have unrestricted access
    to all admin operations without granular permission checks. For production
    systems requiring fine-grained access control, prefer using role-based
    permissions instead of granting `is_admin` status. Only assign `is_admin`
    to fully trusted administrators.
    """
    if getattr(self, "is_admin", False):
        return True
    for r in self.roles:
        raw = (getattr(r, "permissions", "") or "").strip()
        if not raw:
            continue
        for p in raw.split(","):
            p = p.strip()
            if not p:
                continue
            if p == "*":
                return True
            if p == perm:
                return True
            if p.endswith(".*") and perm.startswith(p[:-1]):
                return True
    return False


class RBACMixin:
    """Mixin for Role-Based Access Control on the Admin application."""

    def _require_admin(self, request: Any) -> None:
        # If the request is being impersonated by a valid admin, bypass require_admin
        if getattr(request, "impersonator", None) is not None:
            return

        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        if getattr(u, "is_admin", False):
            return
        # Non-superusers need at least one role with permissions
        can_fn = getattr(u, "can", None)
        if callable(can_fn):
            try:
                roles = u.roles if hasattr(u, "roles") else []
            except Exception:
                roles = []
            if roles:
                return
        # SECURITY: Use same message as wrong password to avoid leaking account status
        request.flash("error", self.t(request, "Invalid credentials"))
        raise RedirectException(self.prefix + "/login")

    def _can(self, request: Any, slug: str, verb: str) -> bool:
        """Check if the current user may perform `verb` on admin `slug`."""
        u = request.user
        if not u:
            # DEBUG: UI permission checks (menu building) are routine, not security events
            logger.debug(
                f"Permission check: No authenticated user for {verb} on {slug}"
            )
            return False
        if getattr(u, "is_admin", False):
            return True
        can_fn = getattr(u, "can", None)
        if not callable(can_fn):
            user_email = getattr(u, "email", None) or getattr(u, "username", f"ID:{u.id}")
            logger.debug(
                f"Permission check: {user_email} lacks can() method for {slug}.{verb}"
            )
            return False
        result = bool(can_fn(f"{slug}.{verb}"))
        if not result:
            user_email = getattr(u, "email", None) or getattr(u, "username", f"ID:{u.id}")
            # DEBUG: Routine permission checks for UI (not actual blocked access attempts)
            # Actual HTTP access denials will log at WARNING level in the view layer
            logger.debug(
                f"Permission check: {user_email} - no permission for {verb} on {slug}"
            )
        return result
