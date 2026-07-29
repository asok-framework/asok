from __future__ import annotations

import logging
from typing import Any

from ..exceptions import RedirectException
from ..orm import MODELS_REGISTRY, ModelList

logger = logging.getLogger(__name__)


def _clean_uid(raw_id: Any) -> Any:
    s = str(raw_id)
    return int(s) if s.isdigit() else raw_id


def _parse_role_rows(rows: Any, Role: Any) -> ModelList:
    if not isinstance(rows, list):
        return ModelList()
    return ModelList(Role(_trust=True, **r) for r in rows if isinstance(r, dict))


def _fetch_roles_from_pivot(self: Any, Role: Any) -> ModelList:
    raw_id = getattr(self, "id", None)
    if not raw_id:
        return ModelList()
    sql = (
        f"SELECT r.* FROM {Role._table} r "
        f"JOIN role_user p ON p.role_id = r.id "
        f"WHERE p.user_id = ?"
    )
    try:
        rows = self.get_engine().execute(sql, (_clean_uid(raw_id),))
        return _parse_role_rows(rows, Role)
    except Exception as e:
        logger.warning("Error fetching roles for user #%s: %s", raw_id, e)
    return ModelList()


def _find_role_by_name_or_slug(RoleModel: Any, name: str) -> Any:
    fields = getattr(RoleModel, "_fields", {})
    if "name" in fields:
        res = RoleModel.find(name=name)
        if res:
            return res
    if "slug" in fields:
        return RoleModel.find(slug=name)
    return None


def _find_role(RoleModel: Any, val: Any) -> Any:
    try:
        s_val = str(val)
        if s_val.isdigit():
            return RoleModel.find(id=int(s_val))
        return _find_role_by_name_or_slug(RoleModel, s_val)
    except Exception:
        return None


def _resolve_fallback_role_item(r: Any, Role: Any) -> Any:
    if not isinstance(r, (str, int)):
        if getattr(r, "permissions", None) is not None or hasattr(r, "name"):
            return r
        return None
    return _find_role(Role, r) or r


def _fetch_fallback_roles(user: Any, Role: Any) -> ModelList:
    fallback = _extract_user_roles(user, skip_roles_attr=True)
    if not fallback:
        return ModelList()
    resolved = []
    for r in fallback:
        item = _resolve_fallback_role_item(r, Role)
        if item is not None:
            resolved.append(item)
    return ModelList(resolved)


def _user_roles_accessor(self: Any) -> ModelList:
    """BelongsToMany accessor for User.roles — reads the role_user pivot with fallback."""
    cached = self.__dict__.get("_eager_roles")
    if cached is not None:
        return cached
    Role = MODELS_REGISTRY.get("Role")
    if not Role or not getattr(self, "id", None):
        return ModelList()
    roles = _fetch_roles_from_pivot(self, Role)
    if len(roles) > 0:
        return roles
    return _fetch_fallback_roles(self, Role)


def _user_role_ids(self: Any) -> list[int]:
    return [r.id for r in self.roles if getattr(r, "id", None) is not None]


def _match_perm_pattern(p: str, perm: str) -> bool:
    if p == "*" or p == perm:
        return True
    return p.endswith(".*") and perm.startswith(p[:-1])


def _resolve_role_instance(role: Any) -> Any:
    if not isinstance(role, (str, int)):
        return role
    RoleModel = MODELS_REGISTRY.get("Role")
    if not RoleModel:
        return role
    return _find_role(RoleModel, role) or role


def _parse_permission_list(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _check_perm_match(perm_list: list[str], perm: str) -> bool:
    return any(_match_perm_pattern(p, perm) for p in perm_list if p)


def _is_admin_str(role: Any) -> bool:
    return isinstance(role, str) and role.lower() in ("admin", "administrator")


def _role_has_permission(role: Any, perm: str) -> bool:
    """Return True if a role grants the given permission string."""
    if not role:
        return False
    resolved = _resolve_role_instance(role)
    raw = getattr(resolved, "permissions", None)
    if not raw:
        return _is_admin_str(resolved)
    return _check_perm_match(_parse_permission_list(raw), perm)


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
    roles = _extract_user_roles(self)
    return any(_role_has_permission(r, perm) for r in roles)


def _extract_roles_attr(u: Any) -> list:
    try:
        roles = getattr(u, "roles", None)
        if roles:
            r_list = list(roles)
            if len(r_list) > 0:
                return r_list
    except Exception:
        pass
    return []


def _extract_role_attr(u: Any) -> list:
    try:
        single_role = getattr(u, "role", None)
        if single_role:
            return [single_role]
    except Exception:
        pass
    return []


def _extract_role_id_attr(u: Any) -> list:
    try:
        role_id = getattr(u, "role_id", None)
        if role_id:
            Role = MODELS_REGISTRY.get("Role")
            if Role:
                r = Role.find(id=role_id)
                if r:
                    return [r]
            return [role_id]
    except Exception:
        pass
    return []


def _find_role_by_id(Role: Any, rid: Any) -> Any:
    try:
        return Role.find(id=rid) or rid
    except Exception:
        return rid


def _resolve_role_ids(Role: Any, role_ids: Any) -> list:
    if not isinstance(role_ids, (list, tuple)):
        return []
    if not Role:
        return list(role_ids)
    return [_find_role_by_id(Role, rid) for rid in role_ids]


def _extract_role_ids_attr(u: Any) -> list:
    try:
        r_ids = getattr(u, "role_ids", None)
        return _resolve_role_ids(MODELS_REGISTRY.get("Role"), r_ids)
    except Exception:
        return []


def _extract_fallback_user_roles(u: Any) -> list:
    return (
        _extract_role_attr(u) or _extract_role_id_attr(u) or _extract_role_ids_attr(u)
    )


def _extract_user_roles(u: Any, skip_roles_attr: bool = False) -> list:
    if not u:
        return []
    if not skip_roles_attr:
        res = _extract_roles_attr(u)
        if res:
            return res
    return _extract_fallback_user_roles(u)


def _get_user_roles(u: Any) -> list:
    if not u:
        return []
    return _extract_user_roles(u)


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
        return getattr(u, "is_admin", False) or bool(_extract_user_roles(u))

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
