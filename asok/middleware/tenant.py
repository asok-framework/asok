from __future__ import annotations

import os
from typing import Any, Callable, Iterable, Optional, Set

from asok.orm.tenant import tenant_context


def _scan_header_items(headers: Any, targets: tuple[str, ...]) -> Optional[str]:
    if not hasattr(headers, "items"):
        return None
    for k, v in headers.items():
        if k.lower() in targets:
            return str(v).strip()
    return None


class TenantMiddleware:
    """Middleware that resolves the current tenant and activates its ORM database context."""

    def __init__(
        self,
        header_name: str = "X-Tenant-ID",
        query_param: Optional[str] = "tenant",
        extract_subdomain: bool = False,
        base_domain: Optional[str] = None,
        allowed_tenants: Optional[Iterable[str]] = None,
        strict: bool = False,
        custom_extractor: Optional[Callable[[Any], Optional[str]]] = None,
    ):
        self.header_name = header_name.lower()
        self.query_param = query_param
        self.extract_subdomain = extract_subdomain
        self.base_domain = base_domain.lower() if base_domain else None
        self.allowed_tenants: Optional[Set[str]] = (
            {t.strip().lower() for t in allowed_tenants}
            if allowed_tenants is not None
            else self._get_env_allowed_tenants()
        )
        self.strict = strict
        self.custom_extractor = custom_extractor

    @staticmethod
    def _get_env_allowed_tenants() -> Optional[Set[str]]:
        env_val = os.getenv("ASOK_ALLOWED_TENANTS")
        if not env_val:
            return None
        return {t.strip().lower() for t in env_val.split(",") if t.strip()}

    def _extract_from_header(self, request: Any) -> Optional[str]:
        headers = getattr(request, "headers", {})
        if not hasattr(headers, "get"):
            return None
        targets = (self.header_name, "x-tenant")
        val = headers.get(self.header_name) or headers.get("x-tenant")
        if not val:
            val = _scan_header_items(headers, targets)
        return str(val).strip() if val else None

    def _extract_from_query(self, request: Any) -> Optional[str]:
        if not self.query_param:
            return None
        args = getattr(request, "args", {})
        if not hasattr(args, "get"):
            return None
        val = args.get(self.query_param)
        return str(val).strip() if val else None

    def _extract_from_host(self, host: str) -> Optional[str]:
        clean_host = host.split(":")[0].strip().lower()
        if self.base_domain:
            suffix = f".{self.base_domain}"
            if clean_host.endswith(suffix):
                return clean_host[: -len(suffix)].split(".")[-1]
            return None
        parts = clean_host.split(".")
        if len(parts) >= 3 and parts[0] != "www":
            return parts[0]
        return None

    def _extract_from_subdomain(self, request: Any) -> Optional[str]:
        if not self.extract_subdomain:
            return None
        host = getattr(request, "host", "")
        if not host:
            headers = getattr(request, "headers", {})
            host = headers.get("host", "") if hasattr(headers, "get") else ""
        return self._extract_from_host(host) if host else None

    def _extract_from_standards(self, request: Any) -> Optional[str]:
        for extractor in (
            self._extract_from_header,
            self._extract_from_query,
            self._extract_from_subdomain,
        ):
            extracted = extractor(request)
            if extracted:
                return extracted.lower()
        return None

    def extract_tenant(self, request: Any) -> Optional[str]:
        """Extract the tenant identifier from request using configured extractors."""
        if self.custom_extractor is not None:
            res = self.custom_extractor(request)
            if res:
                return str(res).strip().lower()
        return self._extract_from_standards(request)

    def _validate_tenant(self, tenant: Optional[str]) -> bool:
        if not tenant:
            return not self.strict
        from asok.orm.tenant import is_safe_tenant_id

        if not is_safe_tenant_id(tenant):
            return False
        if self.allowed_tenants is not None:
            return tenant.lower() in self.allowed_tenants
        return True

    def _reject_invalid_tenant(self, request: Any) -> Any:
        msg = "Missing or invalid tenant identifier."
        if hasattr(request, "text"):
            return request.text(msg, status=400)
        from asok.response import Response

        return Response(msg, status_code=400)

    def __call__(self, request: Any, next_handler: Callable) -> Any:
        tenant_id = self.extract_tenant(request)

        if not self._validate_tenant(tenant_id):
            return self._reject_invalid_tenant(request)

        setattr(request, "tenant", tenant_id)
        with tenant_context(tenant_id):
            return next_handler(request)


def tenant_middleware(request: Any, next_handler: Callable) -> Any:
    """Default functional tenant middleware reading from X-Tenant-ID header or query param."""
    default_mw = TenantMiddleware(extract_subdomain=True)
    return default_mw(request, next_handler)
