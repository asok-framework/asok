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
from .access_rules import resolve_owner_field, user_is_own_scoped
from .forms import FormMixin
from .logs import LogMixin
from .models import (
    _DEFAULT_LOG_MODEL_SRC,
    _DEFAULT_ROLE_MODEL_SRC,
    _DEFAULT_SESSION_MODEL_SRC,
    _DEFAULT_TRASH_MODEL_SRC,
    _DEFAULT_USER_MODEL_SRC,
)
from .rbac import (
    RBACMixin,
    _extract_user_roles,
    _user_can,
    _user_role_ids,
    _user_roles_accessor,
)
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
    "/revoke-session": lambda self, req: self._revoke_session(req),
    "/active-sessions": lambda self, req: self._active_sessions_view(req),
    "/active-sessions/revoke": lambda self, req: self._active_sessions_revoke(req),
    "/active-sessions/revoke-user": lambda self, req: self._active_sessions_revoke_user(
        req
    ),
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
        dashboard_models: list[str] | None = None,
    ):
        self.app = app
        self.site_name = site_name
        self.prefix = url_prefix.rstrip("/")
        self.default_locale = default_locale if default_locale in MESSAGES else "en"
        self.favicon = favicon
        self.dashboard_models = dashboard_models
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
        """Ensure User, Role, AdminLog and AdminTrash models exist. If the project was

        scaffolded without --admin and the dev added Admin(app) later,
        auto-create the model files so migrate + createsuperuser work.
        """
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        self._ensure_model_file("user.py", auth_name, _DEFAULT_USER_MODEL_SRC)
        self._ensure_model_file("role.py", "Role", _DEFAULT_ROLE_MODEL_SRC)
        self._ensure_model_file("admin_log.py", "AdminLog", _DEFAULT_LOG_MODEL_SRC)
        self._ensure_model_file(
            "admin_trash.py", "AdminTrash", _DEFAULT_TRASH_MODEL_SRC
        )
        self._ensure_model_file(
            "admin_session.py", "AdminSession", _DEFAULT_SESSION_MODEL_SRC
        )

    def _ensure_role_pivot(self) -> None:
        """Ensure the role_user pivot table exists in the database."""
        try:
            auth_name = self.app.config.get("AUTH_MODEL", "User")
            User = MODELS_REGISTRY.get(auth_name)
            if User:
                engine = User.get_engine()
                q_pivot = engine.quote_identifier("role_user")
                q_uid = engine.quote_identifier("user_id")
                q_rid = engine.quote_identifier("role_id")
                sql = (
                    f"CREATE TABLE IF NOT EXISTS {q_pivot} ("
                    f"{q_uid} INTEGER NOT NULL, "
                    f"{q_rid} INTEGER NOT NULL, "
                    f"PRIMARY KEY ({q_uid}, {q_rid}))"
                )
                engine.execute(sql)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "Could not ensure role_user pivot table: %s", e
            )

    def _ensure_2fa_columns(self) -> None:
        """No longer auto-alters the user table. Relies on migrations."""
        pass

    def _get_totp_encryption_key(self) -> str:
        """Return the dedicated TOTP encryption key, or fall back to SECRET_KEY."""
        return (
            os.getenv("TOTP_ENCRYPTION_KEY")
            or self.app.config.get("TOTP_ENCRYPTION_KEY")
            or self.app.config.get("SECRET_KEY", "")
        )

    def _decrypt_user_totp(self, user) -> tuple[str | None, bool]:
        encrypted_secret = getattr(user, "totp_secret", None)
        enabled = bool(getattr(user, "totp_enabled", False))
        if encrypted_secret:
            master_key = self._get_totp_encryption_key()
            decrypted = _decrypt_totp_secret(encrypted_secret, master_key)
            if decrypted is None and (
                os.getenv("TOTP_ENCRYPTION_KEY")
                or self.app.config.get("TOTP_ENCRYPTION_KEY")
            ):
                # Fallback to SECRET_KEY for existing secrets prior to key separation
                decrypted = _decrypt_totp_secret(
                    encrypted_secret, self.app.config.get("SECRET_KEY", "")
                )
            return decrypted, enabled
        return None, enabled

    def _get_user_2fa(self, user_id: int) -> tuple[str | None, bool]:
        """Return (secret, enabled) for a user. Decrypts the secret."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or not user_id:
            return None, False
        u = User.find(id=user_id)
        if not u:
            return None, False
        return self._decrypt_user_totp(u)

    def _inject_user_methods(self) -> None:
        """Attach roles accessor, can() helper, and BelongsToMany relation
        onto the User class, regardless of whether user.py declares them.
        """
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User:
            return
        Role = MODELS_REGISTRY.get("Role")
        if Role:
            if "roles" not in User._relations:
                User._relations["roles"] = Relation.BelongsToMany(
                    "Role", pivot_table="role_user"
                )
            self._ensure_role_pivot()
        # Idempotent: re-assigning the same functions is harmless
        User.roles = property(_user_roles_accessor)
        User.role_ids = property(_user_role_ids)
        User.can = _user_can

    # ── Discovery & Registration ─────────────────────────────

    def _find_model_target(self, model_obj: Any) -> Any:
        if not isinstance(model_obj, str):
            return model_obj
        for m in getattr(self.app, "models", []):
            if (
                getattr(m, "__name__", "") == model_obj
                or getattr(m, "_table", "") == model_obj
            ):
                return m
        return None

    @staticmethod
    def _merge_admin_options(existing_opts: Any, options_dict: dict) -> Any:
        class MergedAdmin:
            pass

        if existing_opts:
            for attr in dir(existing_opts):
                if not attr.startswith("_"):
                    setattr(MergedAdmin, attr, getattr(existing_opts, attr))

        for k, v in options_dict.items():
            setattr(MergedAdmin, k, v)
        return MergedAdmin

    def _apply_model_registration(self, model_obj: Any, options_dict: dict) -> Any:
        target = self._find_model_target(model_obj)
        if target is None:
            return model_obj

        merged = self._merge_admin_options(getattr(target, "Admin", None), options_dict)
        setattr(target, "Admin", merged)
        slug = getattr(merged, "slug", None) or getattr(target, "_table", "")
        if self._is_model_hidden(merged):
            self._registered.pop(slug, None)
            return target
        self._registered[slug] = self._build_registry_entry(target, merged, slug)
        return target

    def register(self, model_or_name: Any = None, **opts: Any) -> Any:
        """Register or re-configure a model for the Asok Admin SPA.

        Supports direct call or decorator syntax:
            admin.register(ContactMessage, show_in_dashboard=False)
            admin.register("ContactMessage", show_in_dashboard=False)

            @admin.register(show_in_dashboard=False)
            class ContactMessage(Model): ...
        """
        if model_or_name is not None and (
            callable(model_or_name) or isinstance(model_or_name, str)
        ):
            return self._apply_model_registration(model_or_name, opts)

        def decorator(cls: Any) -> Any:
            return self._apply_model_registration(cls, opts)

        return decorator

    def _discover(self) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for model in self.app.models:
            self._discover_one_model(model, logger)
        self._inject_user_methods()

    @staticmethod
    def _is_model_hidden(opts: Any) -> bool:
        if not opts:
            return False
        if getattr(opts, "hidden", False):
            return True
        show_dash = getattr(opts, "show_in_dashboard", None)
        if show_dash is False:
            return True
        return False

    def _discover_one_model(self, model, logger) -> None:
        try:
            opts = getattr(model, "Admin", None)
            if self._is_model_hidden(opts):
                return
            slug = getattr(opts, "slug", None) or model._table
            self._registered[slug] = self._build_registry_entry(model, opts, slug)
        except Exception as e:
            # Skip malformed models so one bad entry doesn't sink the whole admin.
            model_name = getattr(model, "__name__", str(model))
            logger.warning(f"Failed to register model {model_name} in admin: {e}")

    @staticmethod
    def _get_opt(options: Any, name: str, default: Any) -> Any:
        val = getattr(options, name, default) if options else default
        return val if val is not None else default

    def _resolve_entry_group(self, opts: Any, model_name: str) -> str:
        raw_group = self._get_opt(opts, "group", None)
        return (
            raw_group if raw_group is not None else self._infer_smart_group(model_name)
        )

    def _resolve_show_in_overview(self, opts: Any, model_name: str) -> bool:
        show_ov = self._get_opt(opts, "show_in_overview", None)
        if show_ov is not None:
            return bool(show_ov)
        return self._infer_show_in_dashboard(model_name)

    @staticmethod
    def _is_log_model(model_name: str) -> bool:
        return model_name.lower() in (
            "adminlog",
            "auditlog",
            "systemlog",
            "activitylog",
            "log",
        )

    @staticmethod
    def _is_readonly_system_model(model_name: str) -> bool:
        # Audit logs and session records are read-only in the admin: they must not
        # be added, edited or deleted via CRUD. Sessions are managed through the
        # /active-sessions revoke flow, and both tables are maintained internally.
        return Admin._is_log_model(model_name) or model_name.lower() in (
            "adminsession",
            "session",
            "sessions",
        )

    def _resolve_can_add(self, opts: Any, model_name: str) -> bool:
        val = self._get_opt(opts, "can_add", None)
        return (
            val if val is not None else (not self._is_readonly_system_model(model_name))
        )

    def _resolve_can_edit(self, opts: Any, model_name: str) -> bool:
        val = self._get_opt(opts, "can_edit", None)
        return (
            val if val is not None else (not self._is_readonly_system_model(model_name))
        )

    def _resolve_can_delete(self, opts: Any, model_name: str) -> bool:
        val = self._get_opt(opts, "can_delete", None)
        return (
            val if val is not None else (not self._is_readonly_system_model(model_name))
        )

    def _build_registry_entry(self, model, opts, slug: str) -> dict:
        model_name = getattr(model, "__name__", "")
        label = (
            self._get_opt(opts, "label", None)
            or model._table.replace("_", " ").capitalize()
        )
        columns = self._get_opt(opts, "list_display", None) or self._default_columns(
            model
        )
        searchable = self._get_opt(
            opts, "search_fields", None
        ) or self._default_searchable(model)

        return {
            "model": model,
            "label": label,
            "columns": columns,
            "searchable": searchable,
            "slug": slug,
            "scope": self._get_opt(opts, "scope", None),
            "list_filter": self._get_opt(opts, "list_filter", []),
            "readonly_fields": self._get_opt(opts, "readonly_fields", []),
            "form_exclude": self._get_opt(opts, "form_exclude", []),
            "fieldsets": self._get_opt(opts, "fieldsets", None),
            "per_page": self._get_opt(opts, "per_page", 20),
            "inlines": self._get_opt(opts, "inlines", []),
            "can_add": self._resolve_can_add(opts, model_name),
            "can_edit": self._resolve_can_edit(opts, model_name),
            "can_delete": self._resolve_can_delete(opts, model_name),
            "actions": self._get_opt(opts, "actions", []),
            "vector_search_field": self._get_opt(opts, "vector_search_field", None),
            "group": self._resolve_entry_group(opts, model_name),
            "show_in_overview": self._resolve_show_in_overview(opts, model_name),
            "ordering": self._get_opt(opts, "ordering", None),
            "icon": self._get_opt(opts, "icon", None),
            "export_formats": self._get_opt(opts, "export_formats", ["csv", "json"]),
        }

    @staticmethod
    def _infer_smart_group(model_name: str) -> str:
        name_lower = model_name.lower()
        if name_lower in (
            "user",
            "role",
            "address",
            "permission",
            "userrole",
            "roleuser",
        ):
            return "Users & Access"
        if name_lower in (
            "product",
            "category",
            "order",
            "orderitem",
            "cart",
            "cartitem",
            "coupon",
            "wishlist",
            "review",
            "inventory",
            "stock",
            "payment",
            "invoice",
        ):
            return "Shop & Catalog"
        if name_lower in (
            "newsletter",
            "contactmessage",
            "post",
            "comment",
            "article",
            "message",
            "page",
            "faq",
        ):
            return "Content & Communications"
        if name_lower in (
            "adminlog",
            "systemlog",
            "auditlog",
            "activitylog",
            "log",
            "adminsession",
            "session",
            "sessions",
        ):
            return "System & Audit"
        return "General"

    @staticmethod
    def _infer_show_in_dashboard(model_name: str) -> bool:
        name_lower = model_name.lower()
        if name_lower in ("cartitem", "orderitem", "adminlog", "roleuser", "userrole"):
            return False
        return True

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
        "flatpickr.css",
        "flatpickr.js",
        "chart.js",
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

    def _is_internal_static(self, p: str) -> bool:
        from .views import _PKG_DIR

        clean = p.lstrip("/")
        if clean in self._ADMIN_INTERNAL_STATIC:
            return True
        base, ext = os.path.splitext(clean)
        min_name = (
            f"{base}.min{ext}"
            if not base.endswith(".min") and ext in (".js", ".css")
            else clean
        )
        return os.path.isfile(
            os.path.join(_PKG_DIR, "static", clean)
        ) or os.path.isfile(os.path.join(_PKG_DIR, "static", min_name))

    def _admin_static_url(self, p: str) -> str:
        p = p.lstrip("/")
        is_internal = self._is_internal_static(p)
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
                return hashlib.md5(f.read(), usedforsecurity=False).hexdigest()[:8]
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
        priority = {
            "Shop & Catalog": 1,
            "Users & Access": 2,
            "Content & Communications": 3,
            "System & Audit": 98,
            "General": 99,
        }
        keys = sorted(grouped.keys(), key=lambda k: (priority.get(k, 50), k))
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
    def _find_role_by_name(RoleModel: Any, name: str) -> Any:
        fields = getattr(RoleModel, "_fields", {})
        if "name" in fields:
            r = RoleModel.find(name=name)
            if r:
                return r
        if "slug" in fields:
            return RoleModel.find(slug=name)
        return None

    @staticmethod
    def _lookup_role(RoleModel: Any, s: str) -> Any:
        if s.isdigit():
            return RoleModel.find(id=int(s))
        return Admin._find_role_by_name(RoleModel, s)

    @staticmethod
    def _resolve_role_object(role: Any) -> Any:
        if not isinstance(role, (str, int)):
            return role
        RoleModel = MODELS_REGISTRY.get("Role")
        if not RoleModel:
            return role
        try:
            return Admin._lookup_role(RoleModel, str(role)) or role
        except Exception:
            return role

    @staticmethod
    def _get_role_name_upper(user: Any) -> str:
        roles = _extract_user_roles(user)
        if not roles:
            return "USER"
        role = Admin._resolve_role_object(roles[0])
        if isinstance(role, str):
            return role.upper()
        label = getattr(role, "label", None) or getattr(role, "name", None)
        return str(label if label else role).upper()

    @staticmethod
    def _compute_user_role_label(request: Any, locale: str) -> str:
        user = request.user
        if not user or getattr(user, "is_admin", False):
            return translate(locale, "Admin")
        roles = _extract_user_roles(user)
        if roles:
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
        raw_sessions = []
        current_sid = getattr(request.session, "sid", None)
        if hasattr(self.app, "_session_store"):
            raw_sessions = self.app._session_store.get_user_sessions(me.id)

        locale = self._resolve_locale(request)
        now = time.time()
        active_sessions = []
        for s in raw_sessions:
            ts = Admin._to_timestamp(s.get("last_seen"), now)
            active_sessions.append(
                Admin._build_session_dict(s, str(me.id), me, ts, now, locale)
            )

        return self._render(
            request,
            "me.html",
            form=form,
            twofa_enabled=twofa_enabled,
            errors_global=errors_global,
            active_sessions=active_sessions,
            current_sid=current_sid,
            active="me",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "My profile", "url": None},
            ],
        )

    def _revoke_session(self, request: Any) -> Any:
        self._require_admin(request)
        if request.method != "POST":
            raise RedirectException(self.prefix + "/me")
        request.verify_csrf()
        sid = request.form.get("sid", "").strip()
        if not sid:
            request.flash("error", self.t(request, "Invalid session ID"))
            raise RedirectException(self.prefix + "/me")

        if hasattr(self.app, "_session_store"):
            revoked = self.app._session_store.revoke_user_session(request.user.id, sid)
            if revoked:
                self._log(
                    request,
                    "revoke_session",
                    "UserSession",
                    entity_id=request.user.id,
                    changes={"sid": [sid[:8], "revoked"]},
                )
                request.flash(
                    "success", self.t(request, "Session revoked successfully")
                )
            else:
                request.flash("error", self.t(request, "Failed to revoke session"))
        raise RedirectException(self.prefix + "/me")

    # ── Active Sessions Manager ──────────────────────────────

    @staticmethod
    def _clean_user_ids(user_ids: list) -> list[Any]:
        res = []
        for raw_id in user_ids:
            try:
                res.append(int(raw_id))
            except (ValueError, TypeError):
                res.append(raw_id)
        return res

    def _fetch_cached_users_map(self, user_ids: list) -> dict[Any, Any]:
        if not user_ids:
            return {}
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)
        if not User:
            return {}
        try:
            users = User.where("id", "in", Admin._clean_user_ids(user_ids)).get()
            out = {}
            for u in users:
                out[str(u.id)] = u
                out[int(u.id)] = u
            return out
        except Exception:
            return {}

    @staticmethod
    def _is_mobile_device(user_agent: str) -> bool:
        ua_lower = (user_agent or "").lower()
        return any(term in ua_lower for term in ("iphone", "android", "mobile"))

    @staticmethod
    def _is_session_matching_filter(
        age: float, active_filter: str, is_active: bool
    ) -> bool:
        if active_filter == "15m":
            return is_active and age <= 900
        if active_filter == "24h":
            return age <= 86400
        return True

    @staticmethod
    def _is_local_ip(ip: str) -> bool:
        if not ip or ip in ("127.0.0.1", "::1", "localhost"):
            return True
        return ip.startswith("192.168.") or ip.startswith("10.")

    @staticmethod
    def _clean_geo_part(val: Any) -> str:
        s = str(val or "")
        return s if s != "Unknown" else ""

    @staticmethod
    def _format_city_country(city: Any, country: Any) -> str:
        parts = [
            p
            for p in (Admin._clean_geo_part(city), Admin._clean_geo_part(country))
            if p
        ]
        return ", ".join(parts) if parts else "Localisation inconnue"

    @staticmethod
    def _resolve_geo_location(ip: str) -> str:
        try:
            from ..utils.geo import IPLocation

            loc = IPLocation.get_instance().lookup(ip)
            return Admin._format_city_country(loc.get("city"), loc.get("country"))
        except Exception:
            return "Localisation inconnue"

    @staticmethod
    def _format_location(ip: str, locale: str = "en") -> str:
        if Admin._is_local_ip(ip):
            return translate(locale, "Local Network (Localhost)")
        loc = Admin._resolve_geo_location(ip)
        if loc == "Localisation inconnue":
            return translate(locale, "Unknown location")
        return loc

    @staticmethod
    def _format_short_duration(rem: int) -> str:
        minutes, seconds = divmod(rem, 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s" if seconds > 0 else f"{minutes}m"
        return f"{seconds}s"

    @staticmethod
    def _format_duration(duration_sec: int) -> str:
        days, rem = divmod(duration_sec, 86400)
        hours, rem = divmod(rem, 3600)
        if days > 0:
            return f"{days}j {hours}h" if hours > 0 else f"{days}j"
        if hours > 0:
            minutes = rem // 60
            return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
        return Admin._format_short_duration(rem)

    @staticmethod
    def _format_last_seen(last_seen_diff: int, locale: str = "en") -> str:
        if last_seen_diff < 60:
            return translate(locale, "just now")
        if last_seen_diff < 3600:
            mins = last_seen_diff // 60
            return translate(locale, "{count} min ago", count=mins)
        if last_seen_diff < 86400:
            hours = last_seen_diff // 3600
            return translate(locale, "{count} h ago", count=hours)
        days = last_seen_diff // 86400
        return translate(locale, "{count} d ago", count=days)

    @staticmethod
    def _parse_str_timestamp(val: str, default: float) -> float:
        try:
            return float(val)
        except ValueError:
            try:
                from datetime import datetime

                val_clean = val.replace("T", " ").split(".")[0]
                return datetime.strptime(val_clean, "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                return default

    @staticmethod
    def _to_timestamp(val: Any, default: float) -> float:
        if val is None or val == "":
            return default
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            return Admin._parse_str_timestamp(val.strip(), default)
        return default

    @staticmethod
    def _calc_session_duration_sec(
        s: dict, is_active: bool, created_at_ts: float, ts: float, now: float
    ) -> int:
        if is_active:
            return max(0, int(now - created_at_ts))
        rev_ts = Admin._to_timestamp(s.get("revoked_at"), ts)
        end_ts = rev_ts if rev_ts > 0 else ts
        return max(0, int(end_ts - created_at_ts))

    @staticmethod
    def _get_session_user_display_name(u_obj: Any, uid_str: str) -> str:
        name = getattr(u_obj, "name", None)
        if name:
            return name
        email = getattr(u_obj, "email", None)
        return email if email else f"User #{uid_str}"

    @staticmethod
    def _parse_session_user_agent_info(
        s: dict, locale: str = "en"
    ) -> tuple[str, Any, str, str]:
        from ..request.user_agent import UserAgent

        ua_raw = s.get("user_agent") or s.get("_user_agent", "")
        ua = UserAgent(ua_raw)
        b_name = ua.name if ua.name != "Unknown" else translate(locale, "Browser")
        os_name = ua.os if ua.os != "Unknown" else ""
        return ua_raw, ua, b_name, os_name

    @staticmethod
    def _build_session_dict(
        s: dict, uid_str: str, u_obj: Any, ts: float, now: float, locale: str = "en"
    ) -> dict[str, Any]:
        from datetime import datetime

        user_name = Admin._get_session_user_display_name(u_obj, uid_str)
        ua_raw, ua, browser_name, os_name = Admin._parse_session_user_agent_info(
            s, locale
        )

        created_at_ts = Admin._to_timestamp(s.get("created_at"), ts)
        created_at_dt = datetime.fromtimestamp(created_at_ts).strftime(
            "%d/%m/%Y à %H:%M"
        )

        is_active = bool(s.get("is_active", True))
        duration_sec = Admin._calc_session_duration_sec(
            s, is_active, created_at_ts, ts, now
        )
        duration_str = Admin._format_duration(duration_sec)

        ip = s.get("ip", "Inconnu")
        location = Admin._format_location(ip, locale)
        last_seen_dt = datetime.fromtimestamp(ts).strftime("%d/%m/%Y à %H:%M")
        last_seen_str = Admin._format_last_seen(max(0, int(now - ts)), locale)

        return {
            "sid": s.get("sid", ""),
            "user_id": s.get("user_id", uid_str),
            "user_name": user_name,
            "user_email": getattr(u_obj, "email", ""),
            "is_admin": getattr(u_obj, "is_admin", False),
            "is_active": is_active,
            "ip": ip,
            "location": location,
            "user_agent": ua_raw,
            "browser_name": browser_name,
            "os_name": os_name,
            "is_mobile": ua.is_mobile,
            "last_seen": ts,
            "last_seen_str": last_seen_str,
            "last_seen_at_str": last_seen_dt,
            "created_at": created_at_ts,
            "created_at_str": created_at_dt,
            "duration_str": duration_str,
        }

    def _filter_and_build_sessions_list(
        self,
        raw_sessions: list,
        users_cache: dict,
        active_filter: str,
        now: float,
        locale: str = "en",
    ) -> tuple[list[dict], set[str], int, int]:
        sessions = []
        unique_users = set()
        mobile_count = 0
        desktop_count = 0

        for s in raw_sessions:
            created_ts = Admin._to_timestamp(s.get("created_at"), now)
            ts = Admin._to_timestamp(s.get("last_seen"), created_ts)
            is_active = s.get("is_active", True)
            if not self._is_session_matching_filter(now - ts, active_filter, is_active):
                continue

            uid_str = str(s["user_id"])
            if is_active:
                unique_users.add(uid_str)
                if self._is_mobile_device(s.get("user_agent", "")):
                    mobile_count += 1
                else:
                    desktop_count += 1

            u_obj = users_cache.get(uid_str)
            sessions.append(
                self._build_session_dict(s, uid_str, u_obj, ts, now, locale)
            )
        return sessions, unique_users, mobile_count, desktop_count

    @staticmethod
    def _sync_one_session_to_db(AdminSession: Any, s: dict) -> None:
        sid = s.get("sid")
        if not sid:
            return
        from ..request.user_agent import UserAgent

        try:
            existing = AdminSession.find(sid=sid)
            ua_raw = s.get("user_agent", "")
            ua = UserAgent(ua_raw)
            ip = s.get("ip", "Inconnu")
            created_ts = Admin._to_timestamp(s.get("created_at"), time.time())
            ts = int(Admin._to_timestamp(s.get("last_seen"), created_ts))
            is_active = bool(s.get("is_active", True))

            if existing:
                existing.last_seen = ts
                existing.is_active = is_active
                existing.ip = ip
                existing.user_agent = ua_raw
                existing.save()
            else:
                AdminSession.create(
                    _trust=True,
                    sid=sid,
                    user_id=s.get("user_id"),
                    ip=ip,
                    user_agent=ua_raw,
                    device_type="mobile" if ua.is_mobile else "desktop",
                    browser=ua.name,
                    os_name=ua.os,
                    location=Admin._format_location(ip),
                    is_active=is_active,
                    last_seen=ts,
                )
        except Exception:
            pass

    @staticmethod
    def _deactivate_stale_db_sessions(AdminSession: Any, active_sids: set[str]) -> None:
        try:
            active_rows = AdminSession.where("is_active", True).get()
            now = int(time.time())
            for r in active_rows:
                if r.sid not in active_sids:
                    r.is_active = False
                    if not getattr(r, "revoked_at", None):
                        r.revoked_at = now
                    r.save()
        except Exception:
            pass

    @staticmethod
    def _extract_active_sids(raw_sessions: list) -> set[str]:
        sids = set()
        for s in raw_sessions:
            sid = s.get("sid")
            if sid and s.get("is_active", True):
                sids.add(sid)
        return sids

    def _sync_sessions_to_db(self, raw_sessions: list) -> None:
        AdminSession = MODELS_REGISTRY.get("AdminSession")
        if not AdminSession:
            return
        active_sids = Admin._extract_active_sids(raw_sessions)
        for s in raw_sessions:
            Admin._sync_one_session_to_db(AdminSession, s)

        if hasattr(self.app, "_session_store"):
            Admin._deactivate_stale_db_sessions(AdminSession, active_sids)

    @staticmethod
    def _format_db_session_dict(r: Any) -> dict:
        created_at = int(Admin._to_timestamp(getattr(r, "created_at", None), 0))
        last_seen = int(Admin._to_timestamp(getattr(r, "last_seen", None), created_at))
        ip_val = getattr(r, "ip", None)
        ua_val = getattr(r, "user_agent", None)
        return {
            "sid": r.sid,
            "user_id": r.user_id,
            "ip": ip_val if ip_val else "Inconnu",
            "user_agent": ua_val if ua_val else "",
            "last_seen": last_seen if last_seen else created_at,
            "created_at": created_at if created_at else last_seen,
            "is_active": bool(getattr(r, "is_active", True)),
        }

    @staticmethod
    def _fetch_db_session_rows(AdminSession: Any) -> list[dict]:
        try:
            db_rows = AdminSession.query().order_by("-last_seen").limit(500).get()
            if not db_rows:
                return []
            return [Admin._format_db_session_dict(r) for r in db_rows]
        except Exception:
            return []

    def _fetch_all_raw_sessions(self) -> list[dict]:
        store_sessions = []
        if hasattr(self.app, "_session_store"):
            store_sessions = self.app._session_store.get_all_active_sessions(
                max_age=86400 * 30
            )
        self._sync_sessions_to_db(store_sessions)

        AdminSession = MODELS_REGISTRY.get("AdminSession")
        if not AdminSession:
            return store_sessions
        db_sessions = Admin._fetch_db_session_rows(AdminSession)
        return db_sessions if db_sessions else store_sessions

    def _revoke_db_session(self, sid: str) -> None:
        AdminSession = MODELS_REGISTRY.get("AdminSession")
        if not AdminSession or not sid:
            return
        try:
            row = AdminSession.find(sid=sid)
            if row:
                row.is_active = False
                row.revoked_at = int(time.time())
                row.save()
        except Exception:
            pass

    def _revoke_user_db_sessions(self, user_id: Any) -> None:
        AdminSession = MODELS_REGISTRY.get("AdminSession")
        if not AdminSession or not user_id:
            return
        try:
            rows = AdminSession.where("user_id", user_id).where("is_active", True).get()
            now = int(time.time())
            for r in rows:
                r.is_active = False
                r.revoked_at = now
                r.save()
        except Exception:
            pass

    @staticmethod
    def _parse_page_number(page_str: str) -> int:
        try:
            return max(1, int(page_str))
        except (ValueError, TypeError):
            return 1

    @staticmethod
    def _paginate_list(items: list, page: int, per_page: int) -> tuple[list, int, int]:
        import math

        total_count = len(items)
        total_pages = max(1, math.ceil(total_count / per_page))
        current_page = min(page, total_pages)
        start_idx = (current_page - 1) * per_page
        end_idx = start_idx + per_page
        return items[start_idx:end_idx], current_page, total_pages

    @staticmethod
    def _add_sessions_start_boundary(
        prefix: str, active_filter: str, items: list[dict], start: int
    ) -> None:
        if start > 1:
            items.append(
                {
                    "n": 1,
                    "url": f"{prefix}/active-sessions?filter={active_filter}&page=1",
                }
            )
            if start > 2:
                items.append({"n": 0, "url": ""})

    @staticmethod
    def _add_sessions_end_boundary(
        prefix: str, active_filter: str, items: list[dict], end: int, pages: int
    ) -> None:
        if end < pages:
            if end < pages - 1:
                items.append({"n": 0, "url": ""})
            items.append(
                {
                    "n": pages,
                    "url": f"{prefix}/active-sessions?filter={active_filter}&page={pages}",
                }
            )

    @staticmethod
    def _compute_sessions_page_range(
        prefix: str, active_filter: str, page: int, pages: int
    ) -> list[dict]:
        window = 2
        start = max(1, page - window)
        end = min(pages, page + window)
        items: list[dict] = []
        Admin._add_sessions_start_boundary(prefix, active_filter, items, start)
        for p in range(start, end + 1):
            items.append(
                {
                    "n": p,
                    "url": f"{prefix}/active-sessions?filter={active_filter}&page={p}",
                }
            )
        Admin._add_sessions_end_boundary(prefix, active_filter, items, end, pages)
        return items

    def _active_sessions_view(self, request: Any) -> Any:
        forbidden = self._require_admin_only(request)
        if forbidden:
            return forbidden

        active_filter = request.args.get("filter", "15m")
        all_raw = self._fetch_all_raw_sessions()
        u_ids = list({s["user_id"] for s in all_raw if s.get("user_id")})
        users_cache = self._fetch_cached_users_map(u_ids)
        locale = self._resolve_locale(request)

        sessions, unique_users, mobile_count, desktop_count = (
            self._filter_and_build_sessions_list(
                all_raw, users_cache, active_filter, time.time(), locale
            )
        )
        current_sid = getattr(request.session, "sid", None)

        req_page = Admin._parse_page_number(request.args.get("page", "1"))
        per_page = 10
        p_sessions, page, total_pages = Admin._paginate_list(
            sessions, req_page, per_page
        )
        page_range = Admin._compute_sessions_page_range(
            self.prefix, active_filter, page, total_pages
        )
        total_count = len(sessions)
        start_count = (page - 1) * per_page + 1 if total_count > 0 else 0
        end_count = min(page * per_page, total_count)

        return self._render(
            request,
            "active_sessions.html",
            active="active_sessions",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Active Admin Sessions", "url": None},
            ],
            sessions=p_sessions,
            total_sessions=total_count,
            total_users=len(unique_users),
            mobile_count=mobile_count,
            desktop_count=desktop_count,
            active_filter=active_filter,
            current_sid=current_sid,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            total_count=total_count,
            start_count=start_count,
            end_count=end_count,
            page_range=page_range,
        )

    def _process_session_revocation(self, request: Any) -> None:
        sid = request.form.get("sid", "").strip()
        current_sid = getattr(request.session, "sid", None)
        if not sid or sid == current_sid:
            return

        self._revoke_db_session(sid)
        if hasattr(self.app, "_session_store"):
            self.app._session_store.delete(sid)

        self._log(
            request,
            "revoke_active_session",
            "UserSession",
            entity_id=request.user.id,
            changes={"sid": [sid[:8], "revoked"]},
        )
        request.flash("success", self.t(request, "Session revoked successfully."))

    def _active_sessions_revoke(self, request: Any) -> Any:
        forbidden = self._require_admin_only(request)
        if forbidden:
            return forbidden

        if request.method == "POST":
            request.verify_csrf()
            self._process_session_revocation(request)

        active_filter = request.form.get("filter", "15m")
        raise RedirectException(f"{self.prefix}/active-sessions?filter={active_filter}")

    def _active_sessions_revoke_user(self, request: Any) -> Any:
        forbidden = self._require_admin_only(request)
        if forbidden:
            return forbidden

        if request.method == "POST":
            request.verify_csrf()
            target_user_id = request.form.get("user_id", "").strip()
            if target_user_id:
                self._revoke_user_db_sessions(target_user_id)
                count = 0
                if hasattr(self.app, "_session_store"):
                    count = self.app._session_store.revoke_all_user_sessions(
                        target_user_id
                    )
                self._log(
                    request,
                    "force_logout_user",
                    "User",
                    entity_id=target_user_id,
                    changes={"sessions_revoked": count},
                )
                request.flash(
                    "success",
                    self.t(
                        request, "All active sessions for this user have been revoked."
                    ),
                )

        active_filter = request.form.get("filter", "15m")
        raise RedirectException(f"{self.prefix}/active-sessions?filter={active_filter}")

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
        # Resolve the active locale once so render helpers without a `request`
        # (column values, filter options, relative timestamps) can translate via
        # `self.locale` instead of hard-coding English. `_format_timestamp_col`
        # already reads `self.locale` — it was simply never set.
        self.locale = self._resolve_locale(request)
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
        trash = self._handle_global_trash_routes(request, path, method)
        if trash is not _NO_EARLY:
            return trash
        jobs = self._handle_jobs_routes(request, path, method)
        if jobs is not None:
            return jobs
        return _NO_EARLY

    # ── Global Trash ─────────────────────────────────────────

    _TRASH_ITEM_ACTIONS: frozenset = frozenset({"restore", "delete"})

    @staticmethod
    def _parse_trash_item_parts(path: str) -> tuple:
        parts = [p for p in path.split("/") if p]
        if len(parts) != 3:
            return None, None
        try:
            return int(parts[1]), parts[2]
        except ValueError:
            return None, None

    def _handle_global_trash_item_routes(
        self, request: Any, path: str, method: str
    ) -> Any:
        trash_id, action = self._parse_trash_item_parts(path)
        if trash_id is None or action not in self._TRASH_ITEM_ACTIONS:
            return _NO_EARLY
        if method != "POST":
            return _NO_EARLY
        if action == "restore":
            return self._global_trash_restore(request, trash_id)
        return self._global_trash_delete(request, trash_id)

    def _handle_global_trash_routes(self, request: Any, path: str, method: str) -> Any:
        if path == "/trash":
            return self._global_trash_list(request)
        if path == "/trash/empty" and method == "POST":
            return self._global_trash_empty(request)
        if path.startswith("/trash/"):
            return self._handle_global_trash_item_routes(request, path, method)
        return _NO_EARLY

    def _require_admin_only(self, request: Any) -> Any:
        if not getattr(request.user, "is_admin", False):
            return self._forbid(request)
        return None

    def _render_trash_unavailable(self, request: Any) -> Any:
        return self._render_error(
            request,
            503,
            self.t(request, "Trash Not Available"),
            self.t(request, "Run `asok migrate` to enable the trash system."),
        )

    @staticmethod
    def _parse_trash_page(request: Any) -> int:
        try:
            return max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            return 1

    def _fetch_global_trash_items(
        self,
        AdminTrash: Any,
        model_filter: str,
        page: int,
        per_page: int,
    ) -> tuple:
        try:
            q = AdminTrash.query().order_by("-deleted_at")
            if model_filter:
                q = q.where("model_slug", model_filter)
            total = q.count()
            pages = max(1, (total + per_page - 1) // per_page)
            page = max(1, min(page, pages))
            items = q.limit(per_page).offset((page - 1) * per_page).get()
            return items, total, pages, page
        except Exception:
            return [], 0, 1, 1

    def _collect_distinct_trash_model_slugs(self, AdminTrash: Any) -> list:
        try:
            seen: set = set()
            slugs: list = []
            for r in AdminTrash.query().limit(2000).get():
                if r.model_slug not in seen:
                    seen.add(r.model_slug)
                    slugs.append(r.model_slug)
            return [
                {"slug": s, "label": self._registered.get(s, {}).get("label", s)}
                for s in sorted(slugs)
            ]
        except Exception:
            return []

    def _global_trash_list(self, request: Any) -> Any:
        early = self._require_admin_only(request)
        if early is not None:
            return early
        AdminTrash = MODELS_REGISTRY.get("AdminTrash")
        if not AdminTrash:
            return self._render_trash_unavailable(request)
        model_filter = request.args.get("model_slug", "")
        page = self._parse_trash_page(request)
        items, total, pages, page = self._fetch_global_trash_items(
            AdminTrash, model_filter, page, 25
        )
        model_slugs = self._collect_distinct_trash_model_slugs(AdminTrash)
        start_count = (page - 1) * 25 + 1 if total > 0 else 0
        end_count = min(page * 25, total)

        return self._render(
            request,
            "trash.html",
            items=items,
            total=total,
            page=page,
            pages=pages,
            start_count=start_count,
            end_count=end_count,
            model_filter=model_filter,
            model_slugs=model_slugs,
            active="trash",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Trash", "url": None},
            ],
        )

    @staticmethod
    def _clean_restore_data(Model: Any, data: dict) -> dict:
        cleaned = {k: v for k, v in data.items() if k in Model._fields}
        if Model._soft_delete_field and Model._soft_delete_field in cleaned:
            cleaned[Model._soft_delete_field] = None
        return cleaned

    def _do_restore_from_trash(self, request: Any, trash_item: Any) -> None:
        import json

        Model = MODELS_REGISTRY.get(trash_item.model_name)
        if not Model:
            request.flash("error", self.t(request, "Original model not found"))
            return
        try:
            data = json.loads(trash_item.data or "{}")
            restore_data = self._clean_restore_data(Model, data)
            Model.create(**restore_data)
            trash_item.delete()
            self._log(
                request, "restore", Model.__name__, entity_id=trash_item.object_id
            )
            request.flash("success", self.t(request, "Restored"))
        except Exception as e:
            request.flash("error", str(e))

    def _global_trash_restore(self, request: Any, trash_id: int) -> Any:
        early = self._require_admin_only(request)
        if early is not None:
            return early
        AdminTrash = MODELS_REGISTRY.get("AdminTrash")
        trash_item = AdminTrash.find(id=trash_id) if AdminTrash else None
        if not trash_item:
            return self._render_item_not_found(request)
        self._do_restore_from_trash(request, trash_item)
        raise RedirectException(self.prefix + "/trash")

    def _global_trash_delete(self, request: Any, trash_id: int) -> Any:
        early = self._require_admin_only(request)
        if early is not None:
            return early
        AdminTrash = MODELS_REGISTRY.get("AdminTrash")
        trash_item = AdminTrash.find(id=trash_id) if AdminTrash else None
        if not trash_item:
            return self._render_item_not_found(request)
        model_name = trash_item.model_name
        obj_id = trash_item.object_id
        trash_item.delete()
        self._log(request, "force_delete", model_name, entity_id=obj_id)
        request.flash("success", self.t(request, "Permanently deleted"))
        raise RedirectException(self.prefix + "/trash")

    def _do_empty_trash(self, request: Any, AdminTrash: Any, model_filter: str) -> None:
        try:
            q = AdminTrash.query()
            if model_filter:
                q = q.where("model_slug", model_filter)
            items = q.get()
            for item in items:
                item.delete()
            count = len(items)
            self._log(request, "empty_trash", "AdminTrash", changes={"count": count})
            request.flash(
                "success",
                self.t(request, "Trash emptied ({count} items)", count=count),
            )
        except Exception as e:
            request.flash("error", str(e))

    def _global_trash_empty(self, request: Any) -> Any:
        early = self._require_admin_only(request)
        if early is not None:
            return early
        AdminTrash = MODELS_REGISTRY.get("AdminTrash")
        if not AdminTrash:
            raise RedirectException(self.prefix + "/trash")
        model_filter = request.form.get("model_slug", "")
        self._do_empty_trash(request, AdminTrash, model_filter)
        raise RedirectException(self.prefix + "/trash")

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
        item = self._lookup_admin_item(request, entry, obj_id)
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
        if action in ("import", "import-template"):
            if not self._can(request, slug, "add"):
                return self._forbid(request, "adding disabled")
            if action == "import-template":
                return self._export_csv_template(request, entry)
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

    def _apply_admin_scope(self, request: Any, query: Any, entry: dict) -> Any:
        """Apply row-level filtering so a user only ever touches allowed rows.

        Two layers, both used by the list, export, single-item lookup and bulk
        actions: an optional code `scope(request, query)` callback, then the
        graphical "Seulement les siens" rule configured per role in the admin
        (a `<slug>.own` permission → filter by the model's owner FK).
        """
        scope = entry.get("scope")
        if scope:
            query = scope(request, query)
        return self._apply_owner_scope(request, query, entry)

    def _apply_owner_scope(self, request: Any, query: Any, entry: dict) -> Any:
        user = getattr(request, "user", None)
        if not user or not user_is_own_scoped(user, entry.get("slug", "")):
            return query
        return self._scope_to_owner(query, entry["model"], user)

    def _scope_to_owner(self, query: Any, model: Any, user: Any) -> Any:
        owner_field = resolve_owner_field(model)
        if owner_field and self._table_has_column(model, owner_field):
            return query.where(owner_field, getattr(user, "id", None))
        # 'own' scope is on but the owner column is absent from the table
        # (model/DB drift — e.g. a renamed/unmigrated FK). Fail CLOSED so a
        # scoped user can never see other people's rows, and log the fix.
        logger.warning(
            "Admin 'own' scope active on %s but owner column %r is missing from "
            "table %r (model/DB drift — run `asok make migration && asok migrate`). "
            "Returning no rows to avoid leaking other users' records.",
            getattr(model, "__name__", "?"),
            owner_field,
            getattr(model, "_table", "?"),
        )
        return query.where("id", "<", 0)

    @staticmethod
    def _table_has_column(model: Any, column: str) -> bool:
        try:
            cols = model.get_engine(op="read").get_table_columns(model._table)
            return column in cols
        except Exception:
            return False

    def _lookup_admin_item(self, request: Any, entry: dict, obj_id: int):
        model = entry["model"]
        query = model.with_trashed() if model._soft_delete_field else model.query()
        query = self._apply_admin_scope(request, query, entry)
        return query.where("id", obj_id).first()

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

    def _dispatch_view_subaction(
        self, request: Any, entry: dict, item: Any, slug: str
    ) -> Any:
        if not self._can(request, slug, "view"):
            return self._forbid(request)
        return self._detail(request, entry, item)

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
            return self._dispatch_view_subaction(request, entry, item, slug)
        if sub == "history":
            return self._history(request, entry, item)
        if sub == "clone":
            return self._handle_admin_clone(request, entry, item, slug)
        if method == "POST":
            return self._dispatch_item_post_subaction(
                request, entry, item, slug, sub, obj_id
            )
        return _NO_EARLY

    @staticmethod
    def _serialize_item_for_trash(item: Any, entry: dict) -> str:
        import json

        data = {k: getattr(item, k, None) for k in entry["model"]._fields}
        return json.dumps(data, default=str)

    def _move_to_admin_trash(
        self, request: Any, entry: dict, item: Any, slug: str
    ) -> None:
        AdminTrash = MODELS_REGISTRY.get("AdminTrash")
        if AdminTrash:
            try:
                AdminTrash.create(
                    model_name=entry["model"].__name__,
                    model_slug=slug,
                    object_id=getattr(item, "id", None),
                    object_repr=str(item)[:500],
                    data=self._serialize_item_for_trash(item, entry),
                    deleted_by_id=getattr(getattr(request, "user", None), "id", None),
                )
            except Exception:
                pass
        sd_field = entry["model"]._soft_delete_field
        if sd_field:
            item.force_delete()
        else:
            item.delete()

    def _handle_admin_delete(self, request, entry, item, slug, obj_id):
        if not entry["can_delete"] or not self._can(request, slug, "delete"):
            return self._forbid(request)
        if self._is_self(request, entry, item):
            request.flash(
                "error", self.t(request, "You cannot delete your own account.")
            )
            raise RedirectException(self.prefix + "/" + slug)
        self._move_to_admin_trash(request, entry, item, slug)
        self._log(request, "delete", entry["model"].__name__, entity_id=obj_id)
        request.flash("success", self.t(request, "Moved to trash"))
        raise RedirectException(self.prefix + "/" + slug)

    def _handle_admin_restore(self, request, entry, item, slug, obj_id):
        if not entry["can_edit"] or not self._can(request, slug, "edit"):
            return self._forbid(request)
        item.restore()
        self._log(request, "restore", entry["model"].__name__, entity_id=obj_id)
        request.flash("success", self.t(request, "Restored"))
        raise RedirectException(self.prefix + "/" + slug + "/trash")

    def _handle_admin_force_delete(self, request, entry, item, slug, obj_id):
        if not entry["can_delete"] or not self._can(request, slug, "delete"):
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

    @staticmethod
    def _should_include_in_dashboard(
        entry: dict, slug: str, dash_filter: list[str] | None
    ) -> bool:
        if dash_filter is not None:
            model_name = getattr(entry["model"], "__name__", "")
            lower_filter = [str(x).lower() for x in dash_filter]
            return (slug.lower() in lower_filter) or (
                model_name.lower() in lower_filter
            )
        return bool(entry.get("show_in_overview", entry.get("show_in_dashboard", True)))

    def _collect_dashboard_stats(self, request: Any) -> list[dict]:
        stats: list[dict] = []
        dash_filter = getattr(self, "dashboard_models", None) or getattr(
            self.app, "dashboard_models", None
        )
        for slug, entry in self._registered.items():
            if not self._should_include_in_dashboard(entry, slug, dash_filter):
                continue
            if not self._can(request, slug, "view"):
                continue
            stats.append(self._build_dashboard_stat(request, slug, entry))
        return stats

    def _collect_dashboard_stats_grouped(
        self, request: Any, flat: list[dict]
    ) -> list[dict]:
        """Returns stats grouped by model group, in the same order as the sidebar."""
        stats_by_slug = {s["slug"]: s for s in flat}
        sorted_groups = self._sort_model_groups(
            self._collect_visible_model_groups(request)
        )
        result = []
        for group in sorted_groups:
            group_stats = self._filter_group_stats(group["models"], stats_by_slug)
            if group_stats:
                result.append(
                    {
                        "name": group["name"],
                        "stats": group_stats,
                        "col_count": min(len(group_stats), 5),
                    }
                )
        return result

    @staticmethod
    def _filter_group_stats(models: list[dict], stats_by_slug: dict) -> list[dict]:
        return [stats_by_slug[m["slug"]] for m in models if m["slug"] in stats_by_slug]

    def _build_dashboard_stat(self, request: Any, slug: str, entry: dict) -> dict:
        count = self._safe_count(request, entry)
        trend = self._safe_30d_trend(request, entry)
        return {"slug": slug, "label": entry["label"], "count": count, "trend": trend}

    def _stat_scoped_query(self, request: Any, entry: dict) -> Any:
        """Fresh query for a dashboard stat, row-scoped like the list/export.

        SECURITY: honours the graphical `<slug>.own` rule so an own-scoped user
        sees the count/trend of *their* rows, not the global total — the cards
        must not leak aggregate counts of other users' data.
        """
        return self._apply_admin_scope(request, entry["model"].query(), entry)

    def _safe_count(self, request: Any, entry: dict) -> int:
        try:
            return self._stat_scoped_query(request, entry).count()
        except Exception:
            return 0

    def _safe_30d_trend(self, request: Any, entry: dict):
        model = entry["model"]
        try:
            if "created_at" not in model._fields:
                return None
            now = datetime.datetime.now()
            d30 = (now - datetime.timedelta(days=30)).isoformat()
            d60 = (now - datetime.timedelta(days=60)).isoformat()
            current_period = (
                self._stat_scoped_query(request, entry)
                .where("created_at", ">", d30)
                .count()
            )
            previous_period = (
                self._stat_scoped_query(request, entry)
                .where("created_at", ">", d60)
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

    @staticmethod
    def _extract_model_chart_stats(stats: list[dict]) -> tuple[list[str], list[int]]:
        labels = [s["label"] for s in stats if s.get("count", 0) > 0]
        data = [s["count"] for s in stats if s.get("count", 0) > 0]
        return labels, data

    @staticmethod
    def _count_log_activity_for_day(
        log_model: Any, now: datetime.date, days_ago: int
    ) -> int:
        if not log_model:
            return 0
        target_date = now - datetime.timedelta(days=days_ago)
        # Date-only bounds with an EXCLUSIVE next-day upper bound: created_at is
        # stored ISO with a 'T' separator, so "... 23:59:59" (space) excluded
        # same-day rows and the dashboard daily activity was always 0.
        start = target_date.strftime("%Y-%m-%d")
        end = (target_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            return (
                log_model.where("created_at", ">=", start)
                .where("created_at", "<", end)
                .count()
            )
        except Exception:
            return 0

    @classmethod
    def _collect_activity_timeline_data(
        cls, log_model: Any
    ) -> tuple[list[str], list[int]]:
        now = datetime.date.today()
        timeline_labels = [
            (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(6, -1, -1)
        ]
        timeline_data = [
            cls._count_log_activity_for_day(log_model, now, i) for i in range(6, -1, -1)
        ]
        return timeline_labels, timeline_data

    @staticmethod
    def _count_log_activity_range(
        log_model: Any, start: str, end_exclusive: str
    ) -> int:
        if not log_model:
            return 0
        # Date-only bounds; end is EXCLUSIVE (next period start).
        try:
            return (
                log_model.where("created_at", ">=", start)
                .where("created_at", "<", end_exclusive)
                .count()
            )
        except Exception:
            return 0

    @classmethod
    def _collect_monthly_timeline_data(
        cls, log_model: Any
    ) -> tuple[list[str], list[int]]:
        now = datetime.date.today()
        labels, data = [], []
        for i in range(11, -1, -1):
            m = now.month - i
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            labels.append(f"{y:04d}-{m:02d}-01")
            data.append(
                cls._count_log_activity_range(
                    log_model, f"{y:04d}-{m:02d}-01", cls._next_month_start(y, m)
                )
            )
        return labels, data

    @classmethod
    def _collect_quarterly_timeline_data(
        cls, log_model: Any
    ) -> tuple[list[str], list[int]]:
        now = datetime.date.today()
        curr_q = (now.month - 1) // 3 + 1
        labels, data = [], []
        for i in range(3, -1, -1):
            q = curr_q - i
            y = now.year
            while q <= 0:
                q += 4
                y -= 1
            sm, em = (q - 1) * 3 + 1, q * 3
            labels.append(f"{y:04d}-Q{q}")
            data.append(
                cls._count_log_activity_range(
                    log_model, f"{y:04d}-{sm:02d}-01", cls._next_month_start(y, em)
                )
            )
        return labels, data

    @classmethod
    def _collect_weekly_timeline_data(
        cls, log_model: Any
    ) -> tuple[list[str], list[int]]:
        now = datetime.date.today()
        start_of_current_week = now - datetime.timedelta(days=now.weekday())
        labels, data = [], []
        for i in range(3, -1, -1):
            w_start = start_of_current_week - datetime.timedelta(weeks=i)
            w_end_excl = w_start + datetime.timedelta(days=7)
            labels.append(w_start.strftime("%Y-%m-%d"))
            data.append(
                cls._count_log_activity_range(
                    log_model,
                    w_start.strftime("%Y-%m-%d"),
                    w_end_excl.strftime("%Y-%m-%d"),
                )
            )
        return labels, data

    @classmethod
    def _collect_yearly_timeline_data(
        cls, log_model: Any
    ) -> tuple[list[str], list[int]]:
        now = datetime.date.today()
        labels, data = [], []
        for i in range(4, -1, -1):
            y = now.year - i
            labels.append(str(y))
            data.append(
                cls._count_log_activity_range(
                    log_model, f"{y:04d}-01-01", f"{y + 1:04d}-01-01"
                )
            )
        return labels, data

    def _collect_dashboard_chart_data(
        self, stats: list[dict], can_view_logs: bool = False
    ) -> dict:
        labels, data = self._extract_model_chart_stats(stats)
        # SECURITY: the activity timelines are derived from AdminLog. Only expose
        # them to users who may view logs — otherwise a role without `logs.view`
        # could read the global admin-activity volume/patterns off the charts.
        # The timeline helpers treat a None model as "no data" (empty buckets).
        log_model = MODELS_REGISTRY.get("AdminLog") if can_view_logs else None
        timeline_labels, timeline_data = self._collect_activity_timeline_data(log_model)
        w_labels, w_data = self._collect_weekly_timeline_data(log_model)
        m_labels, m_data = self._collect_monthly_timeline_data(log_model)
        q_labels, q_data = self._collect_quarterly_timeline_data(log_model)
        y_labels, y_data = self._collect_yearly_timeline_data(log_model)

        return {
            "model_labels": labels,
            "model_data": data,
            "timeline_labels": timeline_labels,
            "timeline_data": timeline_data,
            "weekly_labels": w_labels,
            "weekly_data": w_data,
            "monthly_labels": m_labels,
            "monthly_data": m_data,
            "quarterly_labels": q_labels,
            "quarterly_data": q_data,
            "yearly_labels": y_labels,
            "yearly_data": y_data,
        }

    def _dashboard(self, request: Any) -> Any:
        stats = self._collect_dashboard_stats(request)
        stats_groups = self._collect_dashboard_stats_grouped(request, stats)
        can_view_logs = self._can(request, "logs", "view")
        recent_logs = self._collect_recent_logs() if can_view_logs else []
        chart_info = self._collect_dashboard_chart_data(stats, can_view_logs)

        log_slug = "logs"
        for s, entry in self._registered.items():
            if entry["model"].__name__ == "AdminLog":
                log_slug = s
                break

        return self._render(
            request,
            "dashboard.html",
            stats=stats,
            stats_groups=stats_groups,
            chart_info=chart_info,
            recent_logs=recent_logs,
            can_view_logs=can_view_logs,
            log_slug=log_slug,
            widgets=self._render_widgets(request),
            active="dashboard",
            breadcrumbs=[{"label": "Dashboard", "url": None}],
        )
