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
    engine = self.get_engine()
    rows = engine.execute(sql, (self.id,))
    return ModelList(Role(**row) for row in rows)


def _user_role_ids(self: Any) -> list[int]:
    return [r.id for r in self.roles]


def _match_perm_pattern(p: str, perm: str) -> bool:
    if p == "*" or p == perm:
        return True
    return p.endswith(".*") and perm.startswith(p[:-1])


def _role_has_permission(role: Any, perm: str) -> bool:
    """Return True if a role grants the given permission string."""
    raw = getattr(role, "permissions", None)
    if not raw:
        return False
    for p in raw.split(","):
        trimmed = p.strip()
        if trimmed and _match_perm_pattern(trimmed, perm):
            return True
    return False


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
        # SECURITY: Audit log for superadmin actions to detect privilege misuse
        user_id = getattr(self, "id", "unknown")
        user_email = getattr(self, "email", None) or getattr(
            self, "username", f"ID:{user_id}"
        )
        logger.info(
            f"ADMIN ACCESS: User {user_email} (superadmin) granted permission '{perm}'"
        )
        return True
    return any(_role_has_permission(r, perm) for r in self.roles)


def _get_user_roles(u: Any) -> list:
    can_fn = getattr(u, "can", None)
    if not callable(can_fn):
        return []
    try:
        if hasattr(u, "roles"):
            return u.roles
    except Exception:
        pass
    return []


def _user_log_identity(u: Any) -> str:
    email = getattr(u, "email", None)
    if email:
        return email
    return getattr(u, "username", f"ID:{getattr(u, 'id', 'unknown')}")


class RBACMixin:
    """Mixin for Role-Based Access Control on the Admin application."""

    def _is_admin_or_has_roles(self, u: Any) -> bool:
        if not u:
            return False
        return getattr(u, "is_admin", False) or bool(_get_user_roles(u))

    def _require_admin(self, request: Any) -> None:
        # If the request is being impersonated by a valid admin, bypass require_admin
        if getattr(request, "impersonator", None) is not None:
            return

        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        if self._is_admin_or_has_roles(u):
            return
        # SECURITY: Use same message as wrong password to avoid leaking account status
        request.flash("error", self.t(request, "Invalid credentials"))
        raise RedirectException(self.prefix + "/login")

    def _check_user_permission(self, u: Any, slug: str, verb: str) -> bool:
        can_fn = getattr(u, "can", None)
        if not callable(can_fn):
            logger.debug(
                f"Permission check: {_user_log_identity(u)} lacks can() method for {slug}.{verb}"
            )
            return False
        result = bool(can_fn(f"{slug}.{verb}"))
        if not result:
            logger.debug(
                f"Permission check: {_user_log_identity(u)} - no permission for {verb} on {slug}"
            )
        return result

    def _can(self, request: Any, slug: str, verb: str) -> bool:
        """Check if the current user may perform `verb` on admin `slug`."""
        u = request.user
        if not u:
            # DEBUG: Routine permission checks for UI (not actual blocked access attempts)
            logger.debug(
                f"Permission check: No authenticated user for {verb} on {slug}"
            )
            return False
        if getattr(u, "is_admin", False):
            return True
        return self._check_user_permission(u, slug, verb)
