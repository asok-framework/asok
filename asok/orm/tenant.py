from __future__ import annotations

import contextvars
import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Dict, Generator, Optional, Type

from .router import BaseDatabaseRouter, register_router

if TYPE_CHECKING:
    from .model import Model

_current_tenant_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "asok_current_tenant", default=None
)

_TENANT_DATABASES: Dict[str, str] = {}
_TENANT_RESOLVER: Optional[Callable[[str], str]] = None


def get_current_tenant() -> Optional[str]:
    """Return the currently active tenant identifier for the current context."""
    return _current_tenant_var.get()


current_tenant = get_current_tenant


def set_current_tenant(tenant: Optional[str]) -> None:
    """Set the currently active tenant identifier for the current context."""
    _current_tenant_var.set(tenant)


def clear_current_tenant() -> None:
    """Reset the tenant context to None."""
    _current_tenant_var.set(None)


@contextmanager
def tenant_context(tenant: Optional[str]) -> Generator[Optional[str], None, None]:
    """Context manager to execute a block of code in the scope of a specific tenant."""
    token = _current_tenant_var.set(tenant)
    try:
        yield tenant
    finally:
        _current_tenant_var.reset(token)


def register_tenant_database(tenant_id: str, dsn: str) -> None:
    """Register an explicit database DSN or file path for a specific tenant."""
    _TENANT_DATABASES[tenant_id.strip().lower()] = dsn.strip()


def unregister_tenant_database(tenant_id: str) -> None:
    """Remove a registered tenant database from the registry."""
    _TENANT_DATABASES.pop(tenant_id.strip().lower(), None)


def clear_tenant_databases() -> None:
    """Clear all registered tenant databases."""
    _TENANT_DATABASES.clear()


def set_tenant_resolver(resolver: Optional[Callable[[str], str]]) -> None:
    """Register a custom resolver callback that dynamically maps tenant_id to a DSN."""
    global _TENANT_RESOLVER
    _TENANT_RESOLVER = resolver


_RE_TENANT_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def is_safe_tenant_id(tenant_id: Any) -> bool:
    """Validate that a tenant identifier contains only safe alphanumeric/dash/underscore chars."""
    if not tenant_id or not isinstance(tenant_id, str):
        return False
    return bool(_RE_TENANT_ID.match(tenant_id.strip()))


def _resolve_from_template(tenant_id: str) -> Optional[str]:
    # SECURITY: Prevent path traversal (e.g. '../../etc/passwd') or injection when substituting into template
    if not is_safe_tenant_id(tenant_id):
        return None
    template = os.getenv("ASOK_TENANT_DB_TEMPLATE") or os.getenv(
        "DATABASE_TENANT_TEMPLATE"
    )
    if template and "{tenant}" in template:
        return template.replace("{tenant}", tenant_id)
    return None


def _clean_tenant_id(tenant_id: Any) -> Optional[str]:
    if not tenant_id or not isinstance(tenant_id, str):
        return None
    return tenant_id.strip().lower()


def _resolve_custom(clean_id: str) -> Optional[str]:
    if _TENANT_RESOLVER is None:
        return None
    try:
        return _TENANT_RESOLVER(clean_id)
    except Exception:
        return None


def resolve_tenant_database(tenant_id: str) -> Optional[str]:
    """Resolve the database connection DSN or file path for a tenant."""
    clean_id = _clean_tenant_id(tenant_id)
    if not clean_id:
        return None
    if clean_id in _TENANT_DATABASES:
        return _TENANT_DATABASES[clean_id]
    return _resolve_custom(clean_id) or _resolve_from_template(clean_id)


class TenantRouter(BaseDatabaseRouter):
    """Database router that dynamically routes ORM queries to the active tenant's database."""

    def _resolve_dsn_for_active_tenant(self) -> Optional[str]:
        tenant = get_current_tenant()
        if not tenant:
            return None
        dsn = resolve_tenant_database(tenant)
        if not dsn:
            raise ValueError(f"Unknown or unconfigured tenant database for '{tenant}'")
        return dsn

    def db_for_read(self, model: Type[Model], **hints: Any) -> Optional[str]:
        return self._resolve_dsn_for_active_tenant()

    def db_for_write(self, model: Type[Model], **hints: Any) -> Optional[str]:
        return self._resolve_dsn_for_active_tenant()


# Register TenantRouter with priority so it runs before DefaultRouter fallback
register_router(TenantRouter(), priority=True)
