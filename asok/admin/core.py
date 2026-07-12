from __future__ import annotations

import datetime
import html
import importlib.util
import logging
import os
import threading
import time
from typing import Any

from ..cache import default_cache
from ..exceptions import RedirectException
from ..forms import Form
from ..orm import MODELS_REGISTRY, ModelError, Relation
from ..templates import SafeString, render_block_string, render_template_string
from .forms import FormMixin
from .logs import LogMixin
from .models import (
    _DEFAULT_LOG_MODEL_SRC,
    _DEFAULT_ROLE_MODEL_SRC,
    _DEFAULT_USER_MODEL_SRC,
)
from .rbac import RBACMixin, _user_can, _user_role_ids, _user_roles_accessor
from .translations import LOCALES, MESSAGES, translate
from .utils import _decrypt_totp_secret, _display
from .views import ViewsMixin
from .widgets import WidgetMixin

logger = logging.getLogger("asok.admin")

_export_lock = threading.Lock()

_NO_EARLY = object()


_ADMIN_STATIC_ROUTES = {
    "/": lambda self, req: self._dashboard(req),
    "": lambda self, req: self._dashboard(req),
    "/me": lambda self, req: self._me(req),
    "/2fa-setup": lambda self, req: self._twofa_setup(req),
    "/2fa-disable": lambda self, req: self._twofa_disable(req),
    "/2fa-backup-codes": lambda self, req: self._twofa_backup_codes(req),
    "/search": lambda self, req: self._search(req),
}


class Admin(RBACMixin, WidgetMixin, LogMixin, FormMixin, ViewsMixin):
    """The master registration and request handling class for the Asok Admin SPA."""

    def __init__(
        self,
        app: Any,
        site_name: str = "Asok Admin",
        url_prefix: str = "/admin",
        login_rate_limit: tuple[int, int] | None = (5, 900),
        default_locale: str = "en",
        favicon: str | None = None,
    ):
        self.app = app
        self.site_name = site_name
        self.prefix = url_prefix.rstrip("/")
        self.default_locale = default_locale if default_locale in MESSAGES else "en"
        self.favicon = favicon
        self._registered = {}
        self._widgets = []
        # Login rate limit: (max_failed_attempts, window_seconds), or None to disable.
        # Default 5 failed attempts / 15 min per IP.
        if login_rate_limit:
            self._login_limit_max, self._login_limit_window = login_rate_limit
        else:
            self._login_limit_max = None
            self._login_limit_window = 0
        # CSV export rate limit: max 5 exports per hour per user
        self._export_limit_max = 5
        self._export_limit_window = 3600  # 1 hour in seconds
        self._ensure_auth_models()
        self._ensure_role_pivot()
        self._ensure_2fa_columns()
        self._inject_user_methods()
        self._discover()
        app._admin = self

    def t(self, request: Any, key: str, **kwargs: Any) -> str:
        """Helper to translate a key into the active admin locale."""
        locale = self._resolve_locale(request)
        return translate(locale, key, **kwargs)

    # ── Auto-provision User + Role models ────────────────────

    def _register_imported_model(self, mod, _Model) -> None:
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if self._is_app_model(attr, _Model):
                self.app.models.append(attr)

    def _is_app_model(self, attr: Any, _Model: Any) -> bool:
        if not isinstance(attr, type):
            return False
        if not issubclass(attr, _Model):
            return False
        return attr is not _Model and attr not in self.app.models

    def _write_default_model_if_missing(
        self, filename: str, class_name: str, source: str
    ) -> str:
        model_dir = os.path.join(self.app.root_dir, "src/models")
        os.makedirs(model_dir, exist_ok=True)
        path = os.path.join(model_dir, filename)
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(source)
            logger.info(
                "[admin] Created src/models/%s — run "
                "`asok make migration add_%s` then `asok migrate`.",
                filename,
                class_name.lower(),
            )
        return path

    def _load_model_spec(self, filename: str, class_name: str, path: str) -> Any:
        try:
            spec = importlib.util.spec_from_file_location(
                f"model_{class_name.lower()}_admin_auto", path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception as e:
            logger.warning("[admin] Could not load src/models/%s: %s", filename, e)
            return None

    def _ensure_model_file(self, filename: str, class_name: str, source: str) -> Any:
        """Create src/models/<filename> from a default template if missing,

        then load it so it's in MODELS_REGISTRY. Returns the model class or None.
        """
        if class_name in MODELS_REGISTRY:
            return MODELS_REGISTRY[class_name]
        path = self._write_default_model_if_missing(filename, class_name, source)
        mod = self._load_model_spec(filename, class_name, path)
        if mod is None:
            return None
        from ..orm import Model as _Model

        self._register_imported_model(mod, _Model)
        return MODELS_REGISTRY.get(class_name)

    def _ensure_auth_models(self) -> None:
        """Ensure User, Role and AdminLog models exist. If the project was

        scaffolded without --admin and the dev added Admin(app) later,
        auto-create the model files so migrate + createsuperuser work.
        """
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        self._ensure_model_file("user.py", auth_name, _DEFAULT_USER_MODEL_SRC)
        self._ensure_model_file("role.py", "Role", _DEFAULT_ROLE_MODEL_SRC)
        self._ensure_model_file("admin_log.py", "AdminLog", _DEFAULT_LOG_MODEL_SRC)

    def _ensure_role_pivot(self) -> None:
        """No longer auto-creates the pivot table. Relies on migrations."""
        pass

    def _ensure_2fa_columns(self) -> None:
        """No longer auto-alters the user table. Relies on migrations."""
        pass

    def _decrypt_user_totp(self, user) -> tuple[str | None, bool]:
        encrypted_secret = getattr(user, "totp_secret", None)
        enabled = bool(getattr(user, "totp_enabled", False))
        if encrypted_secret:
            master_key = self.app.config.get("SECRET_KEY", "")
            return _decrypt_totp_secret(encrypted_secret, master_key), enabled
        return None, enabled

    def _get_user_2fa(self, user_id: int) -> tuple[str | None, bool]:
        """Return (secret, enabled) for a user. Decrypts the secret."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or not user_id:
            return None, False

        try:
            user = User.find(id=user_id)
            if not user:
                return None, False
            return self._decrypt_user_totp(user)
        except Exception:
            return None, False

    def _inject_user_methods(self) -> None:
        """Attach roles accessor, can() helper, and BelongsToMany relation

        onto the User class, regardless of whether user.py declares them.
        """
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or "Role" not in MODELS_REGISTRY:
            return
        # Inject BelongsToMany relation so .sync('roles', ids) works via ORM
        if "roles" not in User._relations:
            User._relations["roles"] = Relation.BelongsToMany(
                "Role", pivot_table="role_user"
            )
        # Idempotent: re-assigning the same functions is harmless
        User.roles = property(_user_roles_accessor)
        User.role_ids = property(_user_role_ids)
        User.can = _user_can

    # ── Discovery ────────────────────────────────────────────

    def _discover(self) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for model in self.app.models:
            self._discover_one_model(model, logger)

    def _discover_one_model(self, model, logger) -> None:
        try:
            opts = getattr(model, "Admin", None)
            if opts and getattr(opts, "hidden", False):
                return
            slug = getattr(opts, "slug", None) or model._table
            self._registered[slug] = self._build_registry_entry(model, opts, slug)
        except Exception as e:
            # Skip malformed models so one bad entry doesn't sink the whole admin.
            model_name = getattr(model, "__name__", str(model))
            logger.warning(f"Failed to register model {model_name} in admin: {e}")

    def _build_registry_entry(self, model, opts, slug: str) -> dict:
        def _get_opt(options, name: str, default: Any) -> Any:
            val = getattr(options, name, default)
            return val if val is not None else default

        label = _get_opt(opts, "label", None)
        if not label:
            label = model._table.replace("_", " ").capitalize()
        columns = _get_opt(opts, "list_display", None)
        if not columns:
            columns = self._default_columns(model)
        searchable = _get_opt(opts, "search_fields", None)
        if not searchable:
            searchable = self._default_searchable(model)
        return {
            "model": model,
            "label": label,
            "columns": columns,
            "searchable": searchable,
            "slug": slug,
            "list_filter": _get_opt(opts, "list_filter", []),
            "readonly_fields": _get_opt(opts, "readonly_fields", []),
            "form_exclude": _get_opt(opts, "form_exclude", []),
            "fieldsets": _get_opt(opts, "fieldsets", None),
            "per_page": _get_opt(opts, "per_page", 20),
            "inlines": _get_opt(opts, "inlines", []),
            "can_add": _get_opt(opts, "can_add", True),
            "can_edit": _get_opt(opts, "can_edit", True),
            "can_delete": _get_opt(opts, "can_delete", True),
            "actions": _get_opt(opts, "actions", []),
            "vector_search_field": _get_opt(opts, "vector_search_field", None),
            "group": _get_opt(opts, "group", "General"),
        }

    @staticmethod
    def _default_searchable(model: Any) -> list[str]:
        return [
            k
            for k, f in model._fields.items()
            if f.sql_type == "TEXT" and not getattr(f, "is_password", False)
        ]

    @staticmethod
    def _should_skip_default_col(f: Any) -> bool:
        if (
            getattr(f, "is_password", False)
            or getattr(f, "hidden", False)
            or getattr(f, "protected", False)
        ):
            return True
        return bool(getattr(f, "is_soft_delete", False))

    def _default_columns(self, model: Any) -> list[str]:
        cols = ["id"]
        for k, f in model._fields.items():
            if Admin._should_skip_default_col(f):
                continue
            cols.append(k)
            if len(cols) >= 5:
                break
        return cols

    # ── Templating ───────────────────────────────────────────

    def _read_template(self, name: str) -> tuple[str, str]:
        override = os.path.join(self.app.root_dir, "src/admin/templates", name)
        if os.path.isfile(override):
            with open(override, "r", encoding="utf-8") as f:
                return f.read(), os.path.dirname(override)
        from .views import _PKG_DIR

        TPL_DIR = os.path.join(_PKG_DIR, "templates")
        path = os.path.join(TPL_DIR, name)
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), TPL_DIR

    _ADMIN_INTERNAL_STATIC = (
        "admin.css",
        "admin.js",
        "logo.svg",
        "quill.js",
        "quill.snow.css",
    )
    _ADMIN_FULL_PAGE_BLOCKS = frozenset(
        {"page-body", "#page-body", "model_table", "#model_table"}
    )

    def _render(self, request: Any, name: str, **ctx: Any) -> Any:
        content, root = self._read_template(name)
        locale = self._resolve_locale(request)
        self._populate_base_ctx(ctx, request, locale)
        self._populate_static_helper(ctx)
        self._populate_locale_ctx(ctx, locale)
        self._populate_model_groups_ctx(ctx, request)
        self._populate_chrome_ctx(ctx, request, locale)
        self._populate_breadcrumbs(ctx, locale)
        block_header = request.environ.get("HTTP_X_BLOCK")
        if block_header:
            return self._render_block_response(content, root, ctx, block_header)
        return render_template_string(content, ctx, root_dir=root, template_name=name)

    @staticmethod
    def _populate_base_ctx(ctx: dict, request: Any, locale: str) -> None:
        ctx["request"] = request
        ctx["get_flashed_messages"] = request.get_flashed_messages
        ctx["t"] = lambda key, **kwargs: translate(locale, key, **kwargs)

    def _populate_static_helper(self, ctx: dict) -> None:
        ctx["static"] = lambda p: self._admin_static_url(p)

    def _admin_static_url(self, p: str) -> str:
        p = p.lstrip("/")
        is_internal = p in self._ADMIN_INTERNAL_STATIC
        url = self._admin_static_base_url(p, is_internal)
        return url + self._admin_static_cache_buster(p, is_internal)

    def _admin_static_base_url(self, p: str, is_internal: bool) -> str:
        if not is_internal:
            return f"/{p}"
        base, ext = os.path.splitext(p)
        if not base.endswith(".min") and ext in (".js", ".css"):
            return f"{self.prefix}/static/{base}.min{ext}"
        return f"{self.prefix}/static/{p}"

    def _admin_static_cache_buster(self, p: str, is_internal: bool) -> str:
        import time

        if self.app.config.get("DEBUG"):
            return f"?v={int(time.time())}"
        h = self._admin_static_hash(p, is_internal)
        return f"?v={h}" if h else f"?v={int(time.time())}"

    def _admin_static_hash(self, p: str, is_internal: bool):
        if is_internal:
            return self._hash_admin_internal_static(p)
        if hasattr(self.app, "_static_hash"):
            return self.app._static_hash(p)
        return None

    @staticmethod
    def _hash_admin_internal_static(p: str):
        from .views import _PKG_DIR

        base, ext = os.path.splitext(p)
        filename = (
            f"{base}.min{ext}"
            if not base.endswith(".min") and ext in (".js", ".css")
            else p
        )
        full_path = os.path.join(_PKG_DIR, "static", filename)
        if not os.path.isfile(full_path):
            return None
        try:
            import hashlib

            with open(full_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()[:8]
        except Exception:
            return None

    @staticmethod
    def _populate_locale_ctx(ctx: dict, locale: str) -> None:
        ctx["admin_locale"] = locale
        ctx["admin_locale_label"] = LOCALES.get(locale, locale.upper())
        ctx["admin_locales"] = [
            {"code": c, "label": LOCALES[c], "active": c == locale} for c in MESSAGES
        ]

    def _populate_model_groups_ctx(self, ctx: dict, request: Any) -> None:
        grouped = self._collect_visible_model_groups(request)
        sorted_groups = self._sort_model_groups(grouped)
        ctx["admin_model_groups"] = sorted_groups
        ctx["admin_models"] = [
            {"slug": m["slug"], "label": m["label"]}
            for group in sorted_groups
            for m in group["models"]
        ]

    def _collect_visible_model_groups(self, request: Any) -> dict:
        grouped: dict = {}
        for s, v in self._registered.items():
            if not self._can(request, s, "view"):
                continue
            grouped.setdefault(v.get("group", "General"), []).append(
                {"slug": s, "label": v["label"]}
            )
        return grouped

    @staticmethod
    def _sort_model_groups(grouped: dict) -> list:
        keys = sorted(grouped.keys())
        if "General" in keys:
            keys.remove("General")
            keys.append("General")
        return [
            {"name": k, "models": sorted(grouped[k], key=lambda x: x["label"])}
            for k in keys
        ]

    def _populate_chrome_ctx(self, ctx: dict, request: Any, locale: str) -> None:
        ctx["admin_prefix"] = self.prefix
        ctx["admin_site_name"] = self.site_name
        ctx["can_view_media"] = self._can(request, "assets", "view")
        ctx["user_role_label"] = self._compute_user_role_label(request, locale)
        ctx["admin_favicon"] = self.favicon
        ctx["is_impersonating"] = request.session.get("impersonator_id") is not None
        ctx.setdefault("active", None)

    @staticmethod
    def _get_role_name_upper(user: Any) -> str:
        role = user.roles[0]
        label = getattr(role, "label", None)
        return (label if label else role.name).upper()

    @staticmethod
    def _compute_user_role_label(request: Any, locale: str) -> str:
        user = request.user
        if not user or getattr(user, "is_admin", False):
            return translate(locale, "Admin")
        if getattr(user, "roles", None):
            return Admin._get_role_name_upper(user)
        return translate(locale, "User")

    @staticmethod
    def _populate_breadcrumbs(ctx: dict, locale: str) -> None:
        crumbs = ctx.pop("breadcrumbs", [])
        parts = []
        for i, b in enumerate(crumbs):
            parts.append(Admin._render_breadcrumb_part(b, i, locale))
        ctx["breadcrumbs_html"] = SafeString("".join(parts))

    @staticmethod
    def _render_breadcrumb_part(b: dict, i: int, locale: str) -> str:
        sep = ' <span class="sep">›</span> ' if i > 0 else ""
        label = html.escape(translate(locale, b["label"]), quote=True)
        if b.get("url"):
            safe_url = html.escape(str(b["url"]), quote=True)
            return f'{sep}<a href="{safe_url}" data-spa>{label}</a>'
        return f"{sep}<span>{label}</span>"

    def _render_block_response(
        self, content: str, root: str, ctx: dict, block_header: str
    ):
        names = [b.strip() for b in block_header.split(",")]
        if len(names) == 1 and names[0] in self._ADMIN_FULL_PAGE_BLOCKS:
            return render_template_string(content, ctx, root_dir=root)
        return self._render_block_fragments(content, root, ctx, names)

    def _render_block_fragments(
        self, content: str, root: str, ctx: dict, names: list[str]
    ):
        result_parts = []
        for bname in names:
            fragment = self._render_one_block_fragment(content, root, ctx, bname)
            if fragment is not None:
                result_parts.append(fragment)
        result_parts.append(self._render_flash_oob(content, root, ctx))
        return SafeString("".join(result_parts))

    @staticmethod
    def _render_one_block_fragment(content: str, root: str, ctx: dict, bname: str):
        clean_name = bname.lstrip("#")
        try:
            frag = render_block_string(content, clean_name, ctx, root_dir=root)
        except Exception:
            return None
        safe_name = html.escape(clean_name, quote=True)
        return f'<template data-block="#{safe_name}">{frag}</template>'

    @staticmethod
    def _render_flash_oob(content: str, root: str, ctx: dict) -> str:
        flashes_html = render_template_string(
            "{%- from 'macros.html' import flashes -%}{{ flashes() }}",
            ctx,
            root_dir=root,
        )
        return f'<template data-block="#flash-zone">{flashes_html}</template>'

    # ── Auth helpers ─────────────────────────────────────────

    def _resolve_locale(self, request: Any) -> str:
        """Find the active locale for this request:

        1. Explicit ?lang=xx
        2. Session 'admin_locale'
        3. Cookie 'asok_lang' (persists across logout)
        4. Fallback to default_locale
        """
        # 1. Query param
        lang = request.args.get("lang")
        if lang in MESSAGES:
            return lang

        # 2. Session
        lang = request.session.get("admin_locale")
        if lang in MESSAGES:
            return lang

        # 3. Cookie (persists even after logout)
        lang = request.cookies_dict.get("asok_lang")
        if lang in MESSAGES:
            return lang

        # 4. Fallback
        return self.default_locale

    def _create_lang_cookie(self, request: Any, lang: str) -> str:
        lang_cookie = f"asok_lang={lang}; Path=/; SameSite=Lax; Max-Age=31536000"
        if request.scheme == "https":
            lang_cookie += "; Secure"
        return lang_cookie

    def _persist_locale(self, request: Any, lang: str) -> None:
        request.session["admin_locale"] = lang
        lang_cookie = self._create_lang_cookie(request, lang)
        if "asok.extra_headers" not in request.environ:
            request.environ["asok.extra_headers"] = []
        request.environ["asok.extra_headers"].append(("Set-Cookie", lang_cookie))
        request.flash("success", translate(lang, "Language updated"))

    def _resolve_redirect_referer(self, request: Any) -> str:
        from ..utils.security import is_safe_url, request_authority

        ref = request.environ.get("HTTP_REFERER", self.prefix)
        host = request_authority(request)
        if "/lang?" in ref or "/lang" in ref:
            return self.prefix
        if is_safe_url(ref, allowed_host=host):
            return ref
        return self.prefix

    def _set_locale(self, request: Any) -> str:
        # Accept both 'lang' and 'code' query params for backwards compatibility
        lang = request.args.get("lang") or request.args.get("code")
        if lang in MESSAGES:
            self._persist_locale(request, lang)
        ref = self._resolve_redirect_referer(request)
        raise RedirectException(ref)

    def _render_error(self, request: Any, code: int, title: str, message: str) -> Any:
        request.status_code(code)
        # SECURITY FIX: Force X-Block to page-body for error pages
        # When a delete button sends X-Block: row-2, we need to return the full error page
        # instead of trying to find a non-existent {% block row-2 %} in error.html
        original_block = request.environ.get("HTTP_X_BLOCK")
        if original_block and original_block not in {"page-body", "#page-body"}:
            request.environ["HTTP_X_BLOCK"] = "page-body"

        result = self._render(
            request,
            "error.html",
            error_code=code,
            error_title=title,
            error_message=message,
        )

        # Restore original X-Block header for any subsequent processing
        if original_block:
            request.environ["HTTP_X_BLOCK"] = original_block

        return result

    def _forbid(self, request: Any, msg: str = "Forbidden") -> Any:
        return self._render_error(
            request,
            403,
            self.t(request, "Access Denied"),
            self.t(request, msg),
        )

    def _is_proxy_trusted(self, remote_addr: str, trusted_proxies: Any) -> bool:
        if trusted_proxies == "*":
            return True
        if isinstance(trusted_proxies, (list, tuple)):
            return remote_addr in trusted_proxies
        return False

    def _client_ip(self, request: Any) -> str:
        """Get client IP, respecting TRUSTED_PROXIES configuration.

        SECURITY: Only trust X-Forwarded-For if TRUSTED_PROXIES is configured,
        otherwise an attacker can spoof their IP to bypass rate limiting.
        """
        trusted_proxies = self.app.config.get("TRUSTED_PROXIES")
        if trusted_proxies:
            forwarded = request.environ.get("HTTP_X_FORWARDED_FOR", "")
            remote_addr = request.environ.get("REMOTE_ADDR", "")
            if forwarded and self._is_proxy_trusted(remote_addr, trusted_proxies):
                return forwarded.split(",")[-1].strip()
        return request.environ.get("REMOTE_ADDR", "unknown")

    def _login_rate_key(self, request: Any) -> str:
        """Generate rate limit key combining IP and User-Agent.

        SECURITY: Combining multiple request attributes makes rate limit
        bypass via proxy/VPN more difficult.
        """
        import hashlib

        ip = self._client_ip(request)
        user_agent = request.environ.get("HTTP_USER_AGENT", "unknown")
        # Hash to avoid storing long user agents
        ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:16]
        return f"{ip}:{ua_hash}"

    def _login_rate_check(self, request: Any) -> tuple[bool, int]:
        """Return (allowed, remaining_seconds). Read-only check; failures
        are recorded separately via _login_rate_record_failure().
        """
        if self._login_limit_max is None:
            return True, 0
        now = time.time()
        base_key = self._login_rate_key(request)
        count_key = f"admin_login_count:{base_key}"
        reset_key = f"admin_login_reset:{base_key}"

        count = default_cache.get(count_key, 0)
        reset_ts = default_cache.get(reset_key, 0)

        remaining = max(0, int(reset_ts - now))
        if remaining == 0:
            default_cache.forget(count_key)
            default_cache.forget(reset_key)
            return True, 0

        allowed = count < self._login_limit_max
        return allowed, remaining

    def _login_rate_record_failure(self, request: Any) -> None:
        """Increment the failure counter for this IP + User-Agent."""
        if self._login_limit_max is None:
            return
        now = time.time()
        base_key = self._login_rate_key(request)
        count_key = f"admin_login_count:{base_key}"
        reset_key = f"admin_login_reset:{base_key}"

        reset_ts = default_cache.get(reset_key)
        if not reset_ts or reset_ts <= now:
            reset_ts = now + self._login_limit_window
            default_cache.set(reset_key, reset_ts, ttl=self._login_limit_window)

        ttl = max(1, int(reset_ts - now))
        default_cache.incr(count_key, amount=1, ttl=ttl)

    def _login_rate_reset(self, request: Any) -> None:
        if self._login_limit_max is None:
            return
        base_key = self._login_rate_key(request)
        default_cache.forget(f"admin_login_count:{base_key}")
        default_cache.forget(f"admin_login_reset:{base_key}")

    def _export_rate_check(self, request: Any) -> tuple[bool, int]:
        """Check if user can export CSV. Returns (allowed, remaining_seconds).

        Rate limit: max 5 exports per hour per user.
        """
        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None)
        if not user_id:
            return True, 0

        now = time.time()
        key = f"admin_export:{user_id}"
        timestamps = default_cache.get(key, [])
        cutoff = now - self._export_limit_window
        # SECURITY: use >= to avoid boundary bias allowing 6 exports per hour
        timestamps = [ts for ts in timestamps if ts >= cutoff]
        if len(timestamps) >= self._export_limit_max:
            oldest = min(timestamps)
            remaining = max(0, int(oldest + self._export_limit_window - now))
            return False, remaining

        return True, 0

    def _export_rate_record(self, request: Any) -> None:
        """Record an export action for rate limiting."""
        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None)
        if not user_id:
            return

        with _export_lock:
            now = time.time()
            key = f"admin_export:{user_id}"
            timestamps = default_cache.get(key, [])
            cutoff = now - self._export_limit_window
            # SECURITY: use >= to avoid boundary bias allowing 6 exports per hour
            timestamps = [ts for ts in timestamps if ts >= cutoff]
            timestamps.append(now)
            default_cache.set(key, timestamps, ttl=self._export_limit_window)

    def _slug_for_model(self, model: Any) -> str | None:
        """Return the registered admin slug for a model class, or None."""
        for s, e in self._registered.items():
            if e["model"] is model:
                return s
        return None

    def _is_self(self, request: Any, entry: dict[str, Any], item: Any) -> bool:
        """True if item is the currently-logged-in user (for self-protection)."""
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        return (
            entry["model"].__name__ == auth_name
            and request.user
            and getattr(request.user, "id", None) == getattr(item, "id", None)
        )

    def _me(self, request: Any) -> Any:
        """Self-profile page: edit name/email + change password."""
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        me = self._load_self_user(request, auth_name)
        if me is None:
            raise RedirectException(self.prefix + "/login")
        form = self._build_me_form(me, request)
        errors_global = None
        if request.method != "POST":
            self._populate_me_form(form, me)
        else:
            errors_global = self._handle_me_post(form, me, request, auth_name)
        return self._render_me_page(request, form, me, errors_global)

    def _load_self_user(self, request: Any, auth_name: str):
        User = MODELS_REGISTRY.get(auth_name)
        if not User or not request.user:
            return None
        return User.find(id=request.user.id)

    @staticmethod
    def _build_me_form(me: Any, request: Any) -> Any:
        schema: dict = {}
        if "email" in me._fields:
            schema["email"] = Form.email("Email", "required|email")
        if "name" in me._fields:
            schema["name"] = Form.text("Name", "")
        schema["current_password"] = Form.password(
            "Current password", "", autocomplete="current-password"
        )
        schema["new_password"] = Form.password(
            "New password", "", autocomplete="new-password"
        )
        schema["confirm_password"] = Form.password(
            "Confirm new password", "", autocomplete="new-password"
        )
        return Form(schema, request)

    @staticmethod
    def _populate_me_form(form: Any, me: Any) -> None:
        form.fill(me)
        for k in ("current_password", "new_password", "confirm_password"):
            if k in form._fields:
                form._fields[k].value = ""

    @staticmethod
    def _has_form_errors(form: Any) -> bool:
        for f in form._fields.values():
            if getattr(f, "_error", None):
                return True
        return False

    def _handle_me_post(self, form: Any, me: Any, request: Any, auth_name: str):
        if not form.validate():
            return None
        changed = self._collect_me_profile_changes(form, me)
        self._validate_me_password_change(form, me, changed)
        if Admin._has_form_errors(form):
            return None
        try:
            me.save()
        except ModelError as e:
            return str(e)
        except Exception as e:
            return f"Server crash: {str(e)}"
        self._log_me_update(request, me, auth_name, changed)
        request.flash("success", self.t(request, "Profile updated"))
        raise RedirectException(self.prefix + "/me")

    @staticmethod
    def _collect_me_profile_changes(form: Any, me: Any) -> dict:
        changed: dict = {}
        for k in ("email", "name"):
            if k not in form._fields:
                continue
            new_val = form._fields[k].value
            old_val = getattr(me, k, None)
            if new_val != old_val:
                setattr(me, k, new_val)
                changed[k] = [old_val, new_val]
        return changed

    @staticmethod
    def _get_password_fields(form: Any) -> tuple[str, str, str]:
        cur = form._fields["current_password"].value
        new = form._fields["new_password"].value
        conf = form._fields["confirm_password"].value
        return cur or "", new or "", conf or ""

    def _apply_password_change_if_valid(
        self, form: Any, me: Any, changed: dict, cur: str, new: str, conf: str
    ) -> None:
        pw_field = "password" if "password" in me._fields else None
        error_target, error_msg = self._password_change_error(
            me, pw_field, cur, new, conf
        )
        if error_target:
            form._fields[error_target]._error = error_msg
            return
        setattr(me, pw_field, new)
        changed["password"] = ["***", "***"]

    def _validate_me_password_change(self, form: Any, me: Any, changed: dict) -> None:
        cur, new, conf = Admin._get_password_fields(form)
        if not cur and not new and not conf:
            return
        self._apply_password_change_if_valid(form, me, changed, cur, new, conf)

    @staticmethod
    def _password_change_basic_error(
        me, pw_field, cur: str
    ) -> tuple[str | None, str | None]:
        if not pw_field:
            return "new_password", "User model has no password field"
        if not cur:
            return "current_password", "Current password required"
        if not me.check_password(pw_field, cur):
            return "current_password", "Current password is incorrect"
        return None, None

    @staticmethod
    def _password_change_error(me, pw_field, cur: str, new: str, conf: str):
        target, msg = Admin._password_change_basic_error(me, pw_field, cur)
        if target:
            return target, msg
        if new != conf:
            return "confirm_password", "Passwords do not match"
        if len(new) < 6:
            return "new_password", "Password must be at least 6 characters"
        return None, None

    def _log_me_update(
        self, request: Any, me: Any, auth_name: str, changed: dict
    ) -> None:
        if not changed:
            return
        self._log(request, "self_update", auth_name, entity_id=me.id, changes=changed)

    def _render_me_page(self, request: Any, form: Any, me: Any, errors_global) -> Any:
        _, twofa_enabled = self._get_user_2fa(me.id)
        return self._render(
            request,
            "me.html",
            form=form,
            twofa_enabled=twofa_enabled,
            errors_global=errors_global,
            active="me",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "My profile", "url": None},
            ],
        )

    # ── Cross-model search ───────────────────────────────────

    def _build_search_placeholders(
        self, model: Any, searchable: list[str], q: str, query: Any
    ) -> list[str]:
        placeholders = []
        for f in searchable:
            if model._valid_column(f):
                placeholders.append(f"{f} LIKE ?")
                query._args.append(f"%{q}%")
        return placeholders

    @staticmethod
    def _map_search_hits(items: list) -> list[dict]:
        return [{"id": o.id, "label": _display(o) or f"#{o.id}"} for o in items]

    def _search_model_hits(self, slug: str, entry: dict, q: str) -> list | None:
        """Run a search query against one registered model. Returns hit list or None."""
        model = entry["model"]
        query = model.query()
        placeholders = self._build_search_placeholders(
            model, entry["searchable"], q, query
        )
        if not placeholders:
            return None
        query._wheres.append("(" + " OR ".join(placeholders) + ")")
        try:
            items = query.order_by("-id").limit(10).get()
        except Exception:
            items = []
        if not items:
            return None
        return Admin._map_search_hits(items)

    def _collect_search_results(self, request: Any, q: str) -> tuple[list[dict], int]:
        groups = []
        total = 0
        for slug, entry in self._registered.items():
            if not self._can(request, slug, "view") or not entry["searchable"]:
                continue
            hits = self._search_model_hits(slug, entry, q)
            if hits is not None:
                total += len(hits)
                groups.append({"slug": slug, "label": entry["label"], "hits": hits})
        return groups, total

    def _search(self, request: Any) -> Any:
        """Cross-model search across every registered admin slug the user can view."""
        q = (request.args.get("q", "") or "").strip()
        groups, total = ([], 0)
        if q:
            groups, total = self._collect_search_results(request, q)
        return self._render(
            request,
            "search.html",
            q=q,
            groups=groups,
            total=total,
            active="search",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Search", "url": None},
            ],
        )

    # ── Dispatcher ───────────────────────────────────────────

    def dispatch(self, request: Any) -> Any:
        self._maybe_apply_impersonation(request)
        path = request.path[len(self.prefix) :] or "/"
        method = request.method
        early = self._handle_public_admin_routes(request, path)
        if early is not _NO_EARLY:
            return early
        self._enforce_admin_csrf(request, path)
        self._require_admin(request)
        guarded = self._handle_authenticated_admin_routes(request, path, method)
        if guarded is not _NO_EARLY:
            return guarded
        return self._dispatch_entry_routes(request, path, method)

    def _maybe_apply_impersonation(self, request: Any) -> None:
        try:
            impersonator_id = request.session.get("impersonator_id")
            if not impersonator_id:
                return
            if self._impersonation_expired(request):
                self._expire_impersonation(request, impersonator_id)
                return
            self._apply_or_clear_impersonation(request, impersonator_id)
        except Exception:
            pass

    def _impersonation_expired(self, request: Any) -> bool:
        started = request.session.get("impersonate_started_at", 0)
        return time.time() - started > 3600

    def _expire_impersonation(self, request: Any, impersonator_id) -> None:
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonate_started_at", None)
        request.session["user_id"] = impersonator_id
        request.flash("info", self.t(request, "Impersonation expired (1 h max.)"))

    def _apply_or_clear_impersonation(self, request: Any, impersonator_id) -> None:
        impersonator = self._lookup_impersonator(impersonator_id)
        if not impersonator:
            self._expire_impersonation_with_error(request, impersonator_id)
            return
        self._set_impersonation_target(request, impersonator, impersonator_id)

    def _lookup_impersonator(self, impersonator_id):
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)
        if not User:
            return None
        impersonator = User.find(id=impersonator_id)
        # Security: only real admins can keep impersonating
        if not impersonator or not getattr(impersonator, "is_admin", False):
            return None
        return impersonator

    def _expire_impersonation_with_error(self, request: Any, impersonator_id) -> None:
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonate_started_at", None)
        request.session["user_id"] = impersonator_id
        request.flash("error", self.t(request, "Unauthorized impersonation."))

    def _set_impersonation_target(
        self, request: Any, impersonator, impersonator_id
    ) -> None:
        target_id = request.session.get("user_id")
        if not target_id or target_id == impersonator_id:
            return
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)
        if not User:
            return
        target = User.find(id=target_id)
        if target:
            request.user = target
            request.impersonator = impersonator

    def _handle_auth_action_routes(self, request: Any, path: str):
        if path == "/2fa":
            return self._twofa_challenge(request)
        if path == "/lang":
            return self._set_locale(request)
        if path == "/logout":
            return self._handle_logout(request)
        return _NO_EARLY

    def _handle_public_admin_routes(self, request: Any, path: str):
        if path.startswith("/static/"):
            return self._serve_static(request, path[len("/static/") :])
        if path == "/login":
            return self._login(request)
        return self._handle_auth_action_routes(request, path)

    def _handle_logout(self, request: Any):
        # SECURITY: reject GET-based logout to prevent CSRF logout via <img> tags.
        if request.method != "POST":
            raise RedirectException(self.prefix)
        request.verify_csrf()
        request.logout()
        try:
            request.session.pop("pending_2fa_uid", None)
        except Exception:
            pass
        request.flash("info", self.t(request, "You have been logged out."))
        raise RedirectException(self.prefix + "/login")

    def _enforce_admin_csrf(self, request: Any, path: str) -> None:
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and path not in (
            "/login",
            "/lang",
        ):
            request.verify_csrf()

    def _handle_authenticated_admin_routes(self, request: Any, path: str, method: str):
        handler = _ADMIN_STATIC_ROUTES.get(path)
        if handler:
            return handler(self, request)
        special = self._handle_special_admin_routes(request, path, method)
        if special is not _NO_EARLY:
            return special
        return _NO_EARLY

    def _handle_impersonate_start(self, request: Any, path: str) -> Any:
        if not getattr(request.user, "is_admin", False):
            return self._forbid(request)
        return self._impersonate(request, path[len("/impersonate/") :])

    def _handle_impersonate_routes(self, request: Any, path: str, method: str):
        if method != "POST":
            return _NO_EARLY
        if path.startswith("/impersonate/"):
            return self._handle_impersonate_start(request, path)
        if path == "/stop-impersonate":
            return self._stop_impersonate(request)
        return _NO_EARLY

    def _handle_special_admin_routes(self, request: Any, path: str, method: str):
        impersonate = self._handle_impersonate_routes(request, path, method)
        if impersonate is not _NO_EARLY:
            return impersonate
        media = self._handle_media_routes(request, path, method)
        if media is not _NO_EARLY:
            return media
        return _NO_EARLY

    def _handle_media_upload_route(self, request: Any) -> Any:
        if not self._can(request, "assets", "add"):
            return self._forbid(request)
        return self._media_upload(request)

    def _handle_media_delete_route(self, request: Any, path: str) -> Any:
        if not self._can(request, "assets", "delete"):
            return self._forbid(request)
        return self._delete_media(request, path[len("/media/delete/") :])

    def _handle_media_view_route(self, request: Any) -> Any:
        if not self._can(request, "assets", "view"):
            return self._forbid(request)
        return self._media_manager(request)

    def _handle_media_routes(self, request: Any, path: str, method: str):
        if path == "/media":
            return self._handle_media_view_route(request)
        if method == "POST":
            if path == "/media/upload":
                return self._handle_media_upload_route(request)
            if path.startswith("/media/delete/"):
                return self._handle_media_delete_route(request, path)
        return _NO_EARLY

    @staticmethod
    def _split_path_parts(path: str) -> list[str]:
        return [p for p in path.split("/") if p]

    def _dispatch_entry_routes(self, request: Any, path: str, method: str):
        parts = Admin._split_path_parts(path)
        if not parts:
            return self._dashboard(request)
        slug = parts[0]
        entry = self._registered.get(slug)
        if not entry:
            return self._render_page_not_found(request)
        if not self._can(request, slug, "view"):
            return self._forbid(request)
        if len(parts) == 1:
            return self._dispatch_list_or_export(request, entry, slug)
        return self._dispatch_entry_action(request, entry, parts, slug, method)

    def _render_page_not_found(self, request: Any):
        return self._render_error(
            request,
            404,
            self.t(request, "Page Not Found"),
            self.t(
                request,
                "The page you are looking for does not exist or has been moved.",
            ),
        )

    def _dispatch_list_or_export(self, request: Any, entry: dict, slug: str):
        if request.args.get("export") == "csv":
            return self._handle_csv_export(request, entry, slug)
        return self._list(request, entry)

    def _handle_csv_export(self, request: Any, entry: dict, slug: str):
        if not self._can(request, slug, "export"):
            return self._forbid(request)
        allowed, remaining = self._export_rate_check(request)
        if not allowed:
            return self._render_export_rate_limit(request, remaining)
        self._export_rate_record(request)
        return self._export_csv(request, entry)

    def _render_export_rate_limit(self, request: Any, remaining: int):
        minutes = (remaining + 59) // 60  # Round up to minutes
        return self._render_error(
            request,
            429,
            self.t(request, "Too Many Requests"),
            self.t(
                request,
                "Export rate limit exceeded. Please wait {minutes} minutes before trying again.",
                minutes=minutes,
            ),
        )

    def _lookup_item_or_error(self, request: Any, entry: dict, obj_id: int | None):
        if obj_id is None:
            return None, self._render_invalid_id(request)
        item = self._lookup_admin_item(entry, obj_id)
        if not item:
            return None, self._render_item_not_found(request)
        return item, None

    def _dispatch_entry_action(
        self, request: Any, entry: dict, parts: list[str], slug: str, method: str
    ):
        action = parts[1]
        bulk = self._dispatch_bulk_actions(request, entry, action, slug, method)
        if bulk is not _NO_EARLY:
            return bulk
        obj_id = self._parse_obj_id(action)
        item, error_res = self._lookup_item_or_error(request, entry, obj_id)
        if error_res:
            return error_res
        if len(parts) == 3:
            sub_result = self._dispatch_item_subaction(
                request, entry, item, slug, parts[2], method, obj_id
            )
            if sub_result is not _NO_EARLY:
                return sub_result
        return self._dispatch_item_edit_view(request, entry, item, slug, method)

    def _dispatch_csv_and_new_actions(
        self, request: Any, entry: dict, action: str, slug: str, method: str
    ):
        if action == "import":
            if not self._can(request, slug, "add"):
                return self._forbid(request, "adding disabled")
            return self._import_csv(request, entry)
        if action == "new":
            return self._dispatch_new_form(request, entry, slug, method)
        return _NO_EARLY

    def _dispatch_trash_or_bulk(
        self, request: Any, entry: dict, action: str, method: str
    ):
        if action == "trash":
            return self._trash(request, entry)
        if action == "bulk" and method == "POST":
            return self._bulk_action(request, entry)
        return _NO_EARLY

    def _dispatch_bulk_actions(
        self, request: Any, entry: dict, action: str, slug: str, method: str
    ):
        if action == "lookup":
            return self._lookup(request, entry)
        csv_new = self._dispatch_csv_and_new_actions(
            request, entry, action, slug, method
        )
        if csv_new is not _NO_EARLY:
            return csv_new
        return self._dispatch_trash_or_bulk(request, entry, action, method)

    def _dispatch_new_form(self, request: Any, entry: dict, slug: str, method: str):
        if not entry["can_add"] or not self._can(request, slug, "add"):
            return self._forbid(request, "adding disabled")
        if method == "POST":
            return self._create(request, entry)
        item = entry["model"]()
        return self._edit_form(request, entry, item)

    @staticmethod
    def _parse_obj_id(action: str):
        try:
            return int(action)
        except ValueError:
            return None

    def _render_invalid_id(self, request: Any):
        return self._render_error(
            request,
            404,
            self.t(request, "Invalid ID"),
            self.t(request, "The requested item could not be found."),
        )

    @staticmethod
    def _lookup_admin_item(entry: dict, obj_id: int):
        if entry["model"]._soft_delete_field:
            return entry["model"].with_trashed().where("id", obj_id).first()
        return entry["model"].find(id=obj_id)

    def _render_item_not_found(self, request: Any):
        return self._render_error(
            request,
            404,
            self.t(request, "Item Not Found"),
            self.t(request, "The requested item does not exist or has been deleted."),
        )

    def _dispatch_item_post_subaction(
        self, request, entry, item, slug: str, sub: str, obj_id: int
    ):
        if sub == "delete":
            return self._handle_admin_delete(request, entry, item, slug, obj_id)
        if sub == "restore":
            return self._handle_admin_restore(request, entry, item, slug, obj_id)
        if sub == "force-delete":
            return self._handle_admin_force_delete(request, entry, item, slug, obj_id)
        return _NO_EARLY

    def _dispatch_item_subaction(
        self,
        request: Any,
        entry: dict,
        item,
        slug: str,
        sub: str,
        method: str,
        obj_id: int,
    ):
        if sub == "view":
            if not self._can(request, slug, "view"):
                return self._forbid(request)
            return self._detail(request, entry, item)
        if sub == "history":
            return self._history(request, entry, item)
        if method == "POST":
            return self._dispatch_item_post_subaction(
                request, entry, item, slug, sub, obj_id
            )
        return _NO_EARLY

    def _handle_admin_delete(self, request, entry, item, slug, obj_id):
        if not entry["can_delete"] or not self._can(request, slug, "delete"):
            return self._forbid(request)
        if self._is_self(request, entry, item):
            request.flash(
                "error", self.t(request, "You cannot delete your own account.")
            )
            raise RedirectException(self.prefix + "/" + slug)
        item.delete()
        self._log(request, "delete", entry["model"].__name__, entity_id=obj_id)
        request.flash(
            "success",
            self.t(request, "{label} deleted", label=entry["label"][:-1]),
        )
        raise RedirectException(self.prefix + "/" + slug)

    def _handle_admin_restore(self, request, entry, item, slug, obj_id):
        if not self._can(request, slug, "edit"):
            return self._forbid(request)
        item.restore()
        self._log(request, "restore", entry["model"].__name__, entity_id=obj_id)
        request.flash("success", self.t(request, "Restored"))
        raise RedirectException(self.prefix + "/" + slug + "/trash")

    def _handle_admin_force_delete(self, request, entry, item, slug, obj_id):
        if not self._can(request, slug, "delete"):
            return self._forbid(request)
        if self._is_self(request, entry, item):
            request.flash(
                "error", self.t(request, "You cannot delete your own account.")
            )
            raise RedirectException(self.prefix + "/" + slug + "/trash")
        item.force_delete()
        self._log(request, "force_delete", entry["model"].__name__, entity_id=obj_id)
        request.flash("success", self.t(request, "Permanently deleted"))
        raise RedirectException(self.prefix + "/" + slug + "/trash")

    def _handle_item_update_if_allowed(self, request, entry, item, slug: str):
        if not entry["can_edit"] or not self._can(request, slug, "edit"):
            return self._forbid(request, "editing disabled")
        return self._update(request, entry, item)

    def _dispatch_item_edit_view(self, request, entry, item, slug: str, method: str):
        if method == "POST":
            return self._handle_item_update_if_allowed(request, entry, item, slug)
        if self._can(request, slug, "edit"):
            return self._edit_form(request, entry, item)
        if self._can(request, slug, "view"):
            raise RedirectException(f"{self.prefix}/{slug}/{item.id}/view")
        return self._forbid(request)

    # ── Dashboard widget renderer ────────────────────────────

    def _collect_dashboard_stats(self, request: Any) -> list[dict]:
        stats: list[dict] = []
        for slug, entry in self._registered.items():
            if not self._can(request, slug, "view"):
                continue
            stats.append(self._build_dashboard_stat(slug, entry))
        return stats

    @classmethod
    def _build_dashboard_stat(cls, slug: str, entry: dict) -> dict:
        model = entry["model"]
        count = cls._safe_count(model)
        trend = cls._safe_30d_trend(model)
        return {"slug": slug, "label": entry["label"], "count": count, "trend": trend}

    @staticmethod
    def _safe_count(model) -> int:
        try:
            return model.count()
        except Exception:
            return 0

    @staticmethod
    def _safe_30d_trend(model):
        try:
            if "created_at" not in model._fields:
                return None
            now = datetime.datetime.now()
            d30 = (now - datetime.timedelta(days=30)).isoformat()
            d60 = (now - datetime.timedelta(days=60)).isoformat()
            current_period = model.where("created_at", ">", d30).count()
            previous_period = (
                model.where("created_at", ">", d60)
                .where("created_at", "<=", d30)
                .count()
            )
            if previous_period <= 0:
                return None
            diff = ((current_period - previous_period) / previous_period) * 100
            return round(diff, 1)
        except Exception:
            return None

    def _collect_recent_logs(self) -> list:
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)
        user_cache: dict = {}
        return [
            self._enrich_log(log, User, user_cache)
            for log in self._recent_logs(limit=10)
        ]

    @staticmethod
    def _enrich_log(log, User, user_cache: dict):
        log.user_label = Admin._resolve_log_user_label(log, User, user_cache)
        log.date_label = Admin._format_log_date(log.created_at)
        return log

    @staticmethod
    def _resolve_log_user_label(log, User, user_cache: dict) -> str:
        if not log.user_id:
            return "—"
        if log.user_id not in user_cache and User:
            u = User.find(id=log.user_id)
            user_cache[log.user_id] = _display(u) if u else f"#{log.user_id}"
        return user_cache.get(log.user_id, f"#{log.user_id}")

    @staticmethod
    def _format_log_date(created_at) -> str:
        dt = str(created_at)
        if "T" in dt:
            return dt.replace("T", " ").split(".")[0]
        return dt

    def _dashboard(self, request: Any) -> Any:
        stats = self._collect_dashboard_stats(request)
        can_view_logs = self._can(request, "logs", "view")
        recent_logs = self._collect_recent_logs() if can_view_logs else []
        return self._render(
            request,
            "dashboard.html",
            stats=stats,
            recent_logs=recent_logs,
            can_view_logs=can_view_logs,
            widgets=self._render_widgets(request),
            active="dashboard",
            breadcrumbs=[{"label": "Dashboard", "url": None}],
        )
