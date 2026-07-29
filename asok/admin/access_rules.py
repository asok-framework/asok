"""Graphical row- and field-level access rules, driven by role permissions.

Beyond the standard ``<slug>.<verb>`` CRUD permissions, a role may carry two
extra, self-describing permission strings. Both are produced by the checkboxes
in the ``/admin/roles`` editor, so there is nothing to write in application
code — the rules live in the admin, not in the models:

    <slug>.own            -> the role only sees / edits records it owns
    <slug>.lock:<field>   -> <field> is read-only (hidden) for the role

"Owns" means the record's owner foreign key — a FK pointing at the ``User``
model, e.g. ``user_id`` — equals the current user's id. The owner column is
auto-detected. Superusers (``is_admin``) and any role holding the global ``"*"``
grant are never restricted.
"""

from __future__ import annotations

from typing import Any, Iterable

from .rbac import (
    _extract_user_roles,
    _parse_permission_list,
    _resolve_role_instance,
)

OWN_SUFFIX = ".own"
LOCK_MARK = ".lock:"


def _iter_user_perm_strings(user: Any) -> Iterable[str]:
    """Yield every raw permission string across all of the user's roles."""
    for role in _extract_user_roles(user):
        raw = getattr(_resolve_role_instance(role), "permissions", None)
        yield from _parse_permission_list(raw)


def _restriction_perms(user: Any) -> set[str] | None:
    """Permissions to honour for restrictions, or None when the user is
    unrestricted — no user, a superadmin, or a role holding the global "*"."""
    if not user or getattr(user, "is_admin", False):
        return None
    perms = set(_iter_user_perm_strings(user))
    return None if "*" in perms else perms


def user_is_own_scoped(user: Any, slug: str) -> bool:
    """True if the user's roles restrict `slug` to records they own."""
    perms = _restriction_perms(user)
    return bool(perms) and f"{slug}{OWN_SUFFIX}" in perms


def user_locked_fields(user: Any, slug: str) -> set[str]:
    """Field names the user's roles mark read-only (locked) on `slug`."""
    perms = _restriction_perms(user)
    if not perms:
        return set()
    prefix = f"{slug}{LOCK_MARK}"
    return {p[len(prefix) :] for p in perms if p.startswith(prefix)}


# ── owner-field detection ───────────────────────────────────────────


def _related_model_name(field: Any) -> str:
    related = getattr(field, "related_model", None)
    if isinstance(related, str):
        return related
    return getattr(related, "__name__", "")


def _is_user_fk(field: Any) -> bool:
    return (
        bool(getattr(field, "is_foreign_key", False))
        and _related_model_name(field) == "User"
    )


def _user_fk_names(model: Any) -> list[str]:
    return [n for n, f in model._fields.items() if _is_user_fk(f)]


def resolve_owner_field(model: Any) -> str | None:
    """Name of the FK column pointing at User (the record owner), or None.

    An explicit ``Admin.owner_field`` wins; otherwise the single User FK is
    used, preferring a column literally named ``user_id`` when several exist.
    """
    explicit = getattr(getattr(model, "Admin", None), "owner_field", None)
    if explicit:
        return explicit
    candidates = _user_fk_names(model)
    if "user_id" in candidates:
        return "user_id"
    return candidates[0] if candidates else None
