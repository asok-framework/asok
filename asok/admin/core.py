from __future__ import annotations

import datetime
import html
import importlib.util
import os
import threading
import time
from typing import Any

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
        self._login_buckets = {}
        self._login_lock = threading.Lock()
        # CSV export rate limit: max 5 exports per hour per user
        self._export_limit_max = 5
        self._export_limit_window = 3600  # 1 hour in seconds
        self._export_buckets = {}  # {user_id: [timestamps]}
        self._export_lock = threading.Lock()
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

    def _ensure_model_file(self, filename: str, class_name: str, source: str) -> Any:
        """Create src/models/<filename> from a default template if missing,

        then load it so it's in MODELS_REGISTRY. Returns the model class or None.
        """
        if class_name in MODELS_REGISTRY:
            return MODELS_REGISTRY[class_name]

        model_dir = os.path.join(self.app.root_dir, "src/models")
        os.makedirs(model_dir, exist_ok=True)
        path = os.path.join(model_dir, filename)
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(source)
            print(
                f"  [admin] Created src/models/{filename} — run "
                f"`asok make migration add_{class_name.lower()}` then `asok migrate`."
            )

        try:
            spec = importlib.util.spec_from_file_location(
                f"model_{class_name.lower()}_admin_auto", path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"  [admin] Warning: could not load src/models/{filename}: {e}")
            return None

        from ..orm import Model as _Model

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, _Model)
                and attr is not _Model
                and attr not in self.app.models
            ):
                self.app.models.append(attr)
                # Automatic table creation removed in favor of migrations
                pass
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

    def _get_user_2fa(self, user_id: int) -> tuple[str | None, bool]:
        """Return (secret, enabled) for a user. Decrypts the secret."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or not user_id:
            return None, False

        try:
            user = User.find(id=user_id)
            if not user:
                return None, False

            encrypted_secret = getattr(user, "totp_secret", None)
            enabled = bool(getattr(user, "totp_enabled", False))

            # Decrypt the secret if present
            if encrypted_secret:
                master_key = self.app.config.get("SECRET_KEY", "")
                secret = _decrypt_totp_secret(encrypted_secret, master_key)
            else:
                secret = None

            return secret, enabled
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
            try:
                opts = getattr(model, "Admin", None)
                if opts and getattr(opts, "hidden", False):
                    continue
                slug = getattr(opts, "slug", None) or model._table
                label = (
                    getattr(opts, "label", None)
                    or model._table.replace("_", " ").capitalize()
                )
                columns = getattr(opts, "list_display", None) or self._default_columns(
                    model
                )
                searchable = getattr(opts, "search_fields", None) or [
                    k
                    for k, f in model._fields.items()
                    if f.sql_type == "TEXT" and not getattr(f, "is_password", False)
                ]
                self._registered[slug] = {
                    "model": model,
                    "label": label,
                    "columns": columns,
                    "searchable": searchable,
                    "slug": slug,
                    "list_filter": getattr(opts, "list_filter", []) or [],
                    "readonly_fields": getattr(opts, "readonly_fields", []) or [],
                    "form_exclude": getattr(opts, "form_exclude", []) or [],
                    "fieldsets": getattr(opts, "fieldsets", None),
                    "per_page": getattr(opts, "per_page", 20),
                    "inlines": getattr(opts, "inlines", []) or [],
                    "can_add": getattr(opts, "can_add", True),
                    "can_edit": getattr(opts, "can_edit", True),
                    "can_delete": getattr(opts, "can_delete", True),
                    "actions": getattr(opts, "actions", []) or [],
                    "vector_search_field": getattr(opts, "vector_search_field", None),
                    "group": getattr(opts, "group", "General"),
                }
            except Exception as e:
                # Skip malformed models instead of crashing the entire admin
                model_name = getattr(model, "__name__", str(model))
                logger.warning(
                    f"Failed to register model {model_name} in admin: {e}"
                )
                continue

    def _default_columns(self, model: Any) -> list[str]:
        cols = ["id"]
        for k, f in model._fields.items():
            if (
                getattr(f, "is_password", False)
                or getattr(f, "hidden", False)
                or getattr(f, "protected", False)
            ):
                continue
            if getattr(f, "is_soft_delete", False):
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

    def _render(self, request: Any, name: str, **ctx: Any) -> Any:
        content, root = self._read_template(name)
        locale = self._resolve_locale(request)
        ctx["request"] = request
        ctx["get_flashed_messages"] = request.get_flashed_messages
        ctx["t"] = lambda key, **kwargs: translate(locale, key, **kwargs)

        # Smart static helper: if path exists in admin's internal static folder,
        # return /admin/static/path. Otherwise return /path as a project asset.
        def _static(p: str) -> str:
            p = p.lstrip("/")
            # Check if it's a known admin internal file
            internal = (
                "admin.css",
                "admin.js",
                "logo.svg",
                "quill.js",
                "quill.snow.css",
            )
            if p in internal:
                # Always use minified versions for CSS/JS (package only contains .min files)
                base, ext = os.path.splitext(p)
                if not base.endswith(".min") and ext in [".js", ".css"]:
                    min_p = f"{base}.min{ext}"
                    return f"{self.prefix}/static/{min_p}"
                return f"{self.prefix}/static/{p}"
            return f"/{p}"

        ctx["static"] = _static
        ctx["admin_locale"] = locale
        ctx["admin_locale_label"] = LOCALES.get(locale, locale.upper())
        ctx["admin_locales"] = [
            {"code": c, "label": LOCALES[c], "active": c == locale} for c in MESSAGES
        ]
        grouped = {}
        for s, v in self._registered.items():
            if not self._can(request, s, "view"):
                continue
            g = v.get("group", "General")
            if g not in grouped:
                grouped[g] = []
            grouped[g].append({"slug": s, "label": v["label"]})

        # Sort groups (General at the end, or alphabetically)
        sorted_groups = []
        keys = sorted(grouped.keys())
        if "General" in keys:
            keys.remove("General")
            keys.append("General")

        for k in keys:
            sorted_groups.append(
                {"name": k, "models": sorted(grouped[k], key=lambda x: x["label"])}
            )

        ctx["admin_model_groups"] = sorted_groups
        ctx["admin_models"] = [
            {"slug": m["slug"], "label": m["label"]}
            for group in sorted_groups
            for m in group["models"]
        ]
        ctx["admin_prefix"] = self.prefix
        ctx["admin_site_name"] = self.site_name
        ctx["can_view_media"] = self._can(request, "assets", "view")

        # Dynamic role label for sidebar
        role_label = translate(locale, "Admin")
        if request.user:
            if getattr(request.user, "is_admin", False):
                role_label = translate(locale, "Admin")
            elif hasattr(request.user, "roles") and request.user.roles:
                role = request.user.roles[0]
                role_label = (getattr(role, "label", None) or role.name).upper()
            else:
                role_label = translate(locale, "User")
        ctx["user_role_label"] = role_label

        ctx["admin_favicon"] = self.favicon
        ctx["is_impersonating"] = request.session.get("impersonator_id") is not None
        ctx.setdefault("active", None)
        crumbs = ctx.pop("breadcrumbs", [])
        parts = []
        for i, b in enumerate(crumbs):
            sep = ' <span class="sep">›</span> ' if i > 0 else ""
            label = html.escape(translate(locale, b["label"]), quote=True)
            if b.get("url"):
                safe_url = html.escape(str(b["url"]), quote=True)
                parts.append(f'{sep}<a href="{safe_url}" data-spa>{label}</a>')
            else:
                parts.append(f"{sep}<span>{label}</span>")
        ctx["breadcrumbs_html"] = SafeString("".join(parts))

        # ASOK REACTIVE ENGINE: Handle partial block rendering
        block_header = request.environ.get("HTTP_X_BLOCK")
        if block_header:
            names = [b.strip() for b in block_header.split(",")]

            # 1. SPA navigation: JS fetches full page and extracts #page-body from it.
            if len(names) == 1 and names[0] in {
                "page-body",
                "#page-body",
                "model_table",
                "#model_table",
            }:
                return render_template_string(content, ctx, root_dir=root)

            # 2. Extract blocks
            result_parts = []
            for bname in names:
                clean_name = bname.lstrip("#")
                try:
                    frag = render_block_string(content, clean_name, ctx, root_dir=root)
                    # SECURITY: Escape block name to prevent XSS in data-block attribute
                    safe_name = html.escape(clean_name, quote=True)
                    result_parts.append(
                        f'<template data-block="#{safe_name}">{frag}</template>'
                    )
                except Exception:
                    continue

            # 3. Always append Flashes as an OOB swap if it's a partial request
            flashes_html = render_template_string(
                "{%- from 'macros.html' import flashes -%}{{ flashes() }}",
                ctx,
                root_dir=root,
            )
            result_parts.append(
                f'<template data-block="#flash-zone">{flashes_html}</template>'
            )

            return SafeString("".join(result_parts))

        return render_template_string(content, ctx, root_dir=root)

    # ── Auth helpers ─────────────────────────────────────────

    def _resolve_locale(self, request: Any) -> str:
        """Find the active locale for this request:

        1. Explicit ?lang=xx
        2. Session 'admin_locale'
        3. Cookie 'asok_lang' (persists across logout)
        4. Request.user's preferred language (not yet implemented)
        5. Accept-Language header
        6. Fallback to default_locale
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

        # 4. Accept-Language
        header = request.environ.get("HTTP_ACCEPT_LANGUAGE", "")
        if header:
            # e.g. "fr-CH, fr;q=0.9, en;q=0.8, *;q=0.5"
            for part in header.split(","):
                code = part.split(";")[0].split("-")[0].strip().lower()
                if code in MESSAGES:
                    return code

        # 5. Fallback
        return self.default_locale

    def _set_locale(self, request: Any) -> str:
        # Accept both 'lang' and 'code' query params for backwards compatibility
        lang = request.args.get("lang") or request.args.get("code")
        if lang in MESSAGES:
            # Store in session (cleared on logout)
            request.session["admin_locale"] = lang

            # FIX: Explicitly set language cookie that persists across logout
            # Build cookie with same settings as in security.py
            lang_cookie = f"asok_lang={lang}; Path=/; SameSite=Lax; Max-Age=31536000"
            # Add Secure flag in production (non-DEBUG mode)
            if not self.app.config.get("DEBUG"):
                lang_cookie += "; Secure"

            # Add cookie to response headers via environ (standard mechanism)
            if "asok.extra_headers" not in request.environ:
                request.environ["asok.extra_headers"] = []
            request.environ["asok.extra_headers"].append(("Set-Cookie", lang_cookie))

            request.flash("success", translate(lang, "Language updated"))
        ref = request.environ.get("HTTP_REFERER", self.prefix)
        # Avoid looping if ref is the lang changer itself
        if "/lang?" in ref or "/lang" in ref:
            ref = self.prefix
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
            request, "error.html", error_code=code, error_title=title, error_message=message
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

    def _client_ip(self, request: Any) -> str:
        """Get client IP, respecting TRUSTED_PROXIES configuration.

        SECURITY: Only trust X-Forwarded-For if TRUSTED_PROXIES is configured,
        otherwise an attacker can spoof their IP to bypass rate limiting.
        """
        trusted_proxies = self.app.config.get("TRUSTED_PROXIES")

        # Only use X-Forwarded-For if we explicitly trust proxies
        if trusted_proxies:
            forwarded = request.environ.get("HTTP_X_FORWARDED_FOR", "")
            if forwarded:
                # If TRUSTED_PROXIES is "*", trust all. Otherwise verify remote addr.
                if trusted_proxies == "*":
                    return forwarded.split(",")[0].strip()
                # Check if request comes from a trusted proxy
                remote_addr = request.environ.get("REMOTE_ADDR", "")
                if isinstance(trusted_proxies, (list, tuple)):
                    if remote_addr in trusted_proxies:
                        return forwarded.split(",")[0].strip()

        # Default: use direct connection IP (safe)
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
        key = self._login_rate_key(request)
        with self._login_lock:
            # Cleanup expired
            expired = [k for k, v in self._login_buckets.items() if v["reset"] <= now]
            for k in expired:
                del self._login_buckets[k]
            bucket = self._login_buckets.get(key)
            if not bucket:
                return True, 0
            remaining = max(0, int(bucket["reset"] - now))
            allowed = bucket["count"] < self._login_limit_max
            return allowed, remaining

    def _login_rate_record_failure(self, request: Any) -> None:
        """Increment the failure counter for this IP + User-Agent."""
        if self._login_limit_max is None:
            return
        now = time.time()
        key = self._login_rate_key(request)
        with self._login_lock:
            bucket = self._login_buckets.get(key)
            if not bucket or bucket["reset"] <= now:
                bucket = {"count": 0, "reset": now + self._login_limit_window}
                self._login_buckets[key] = bucket
            bucket["count"] += 1

    def _login_rate_reset(self, request: Any) -> None:
        if self._login_limit_max is None:
            return
        with self._login_lock:
            self._login_buckets.pop(self._login_rate_key(request), None)

    def _export_rate_check(self, request: Any) -> tuple[bool, int]:
        """Check if user can export CSV. Returns (allowed, remaining_seconds).

        Rate limit: max 5 exports per hour per user.
        """
        user_id = getattr(request.user, "id", None) if request.user else None
        if not user_id:
            return True, 0  # Not logged in, let other checks handle it

        now = time.time()
        with self._export_lock:
            # Cleanup old timestamps outside the window
            if user_id in self._export_buckets:
                cutoff = now - self._export_limit_window
                self._export_buckets[user_id] = [
                    ts for ts in self._export_buckets[user_id] if ts > cutoff
                ]

            timestamps = self._export_buckets.get(user_id, [])
            if len(timestamps) >= self._export_limit_max:
                # Rate limit exceeded
                oldest = min(timestamps)
                remaining = max(0, int(oldest + self._export_limit_window - now))
                return False, remaining

            return True, 0

    def _export_rate_record(self, request: Any) -> None:
        """Record an export action for rate limiting."""
        user_id = getattr(request.user, "id", None) if request.user else None
        if not user_id:
            return

        now = time.time()
        with self._export_lock:
            if user_id not in self._export_buckets:
                self._export_buckets[user_id] = []
            self._export_buckets[user_id].append(now)

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
        User = MODELS_REGISTRY.get(auth_name)
        if not User or not request.user:
            raise RedirectException(self.prefix + "/login")

        # Re-fetch a fresh copy from DB so we save against the right row
        me = User.find(id=request.user.id)
        if not me:
            raise RedirectException(self.prefix + "/login")

        errors_global = None
        schema = {}
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
        form = Form(schema, request)
        if request.method != "POST":
            form.fill(me)
            # Don't pre-fill password fields
            for k in ("current_password", "new_password", "confirm_password"):
                if k in form._fields:
                    form._fields[k].value = ""
        else:
            if form.validate():
                changed = {}
                # Email / name updates
                for k in ("email", "name"):
                    if k in form._fields:
                        new_val = form._fields[k].value
                        old_val = getattr(me, k, None)
                        if new_val != old_val:
                            setattr(me, k, new_val)
                            changed[k] = [old_val, new_val]
                # Password change (only if any of the 3 fields filled)
                cur = form._fields["current_password"].value or ""
                new = form._fields["new_password"].value or ""
                conf = form._fields["confirm_password"].value or ""
                pw_field = "password" if "password" in me._fields else None
                if cur or new or conf:
                    if not pw_field:
                        form._fields[
                            "new_password"
                        ]._error = "User model has no password field"
                    elif not cur:
                        form._fields[
                            "current_password"
                        ]._error = "Current password required"
                    elif not me.check_password(pw_field, cur):
                        form._fields[
                            "current_password"
                        ]._error = "Current password is incorrect"
                    elif new != conf:
                        form._fields[
                            "confirm_password"
                        ]._error = "Passwords do not match"
                    elif len(new) < 6:
                        form._fields[
                            "new_password"
                        ]._error = "Password must be at least 6 characters"
                    else:
                        setattr(me, pw_field, new)
                        changed["password"] = ["***", "***"]

                has_errors = any(
                    getattr(f, "_error", None) for f in form._fields.values()
                )
                if not has_errors:
                    try:
                        me.save()
                    except ModelError as e:
                        errors_global = str(e)
                    except Exception as e:
                        errors_global = f"Server crash: {str(e)}"
                    else:
                        if changed:
                            self._log(
                                request,
                                "self_update",
                                auth_name,
                                entity_id=me.id,
                                changes=changed,
                            )
                        request.flash("success", self.t(request, "Profile updated"))
                        raise RedirectException(self.prefix + "/me")

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

    def _search(self, request: Any) -> Any:
        """Cross-model search across every registered admin slug the user can view."""
        q = (request.args.get("q", "") or "").strip()
        groups = []
        total = 0
        if q:
            for slug, entry in self._registered.items():
                if not self._can(request, slug, "view"):
                    continue
                if not entry["searchable"]:
                    continue
                model = entry["model"]
                query = model.query()
                placeholders = []
                for f in entry["searchable"]:
                    if model._valid_column(f):
                        placeholders.append(f"{f} LIKE ?")
                        query._args.append(f"%{q}%")
                if not placeholders:
                    continue
                query._wheres.append("(" + " OR ".join(placeholders) + ")")
                try:
                    items = query.order_by("-id").limit(10).get()
                except Exception:
                    items = []
                if not items:
                    continue
                hits = [{"id": o.id, "label": _display(o) or f"#{o.id}"} for o in items]
                total += len(hits)
                groups.append({"slug": slug, "label": entry["label"], "hits": hits})
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
        # Impersonation logic: swap request.user if impersonator_id is in session
        try:
            impersonator_id = request.session.get("impersonator_id")
            if impersonator_id:
                # Security: auto-expire impersonation after 1 hour
                started = request.session.get("impersonate_started_at", 0)
                if time.time() - started > 3600:
                    request.session.pop("impersonator_id", None)
                    request.session.pop("impersonate_started_at", None)
                    request.session["user_id"] = impersonator_id
                    request.flash("info", self.t(request, "Impersonation expired (1 h max.)"))
                else:
                    auth_name = self.app.config.get("AUTH_MODEL", "User")
                    User = MODELS_REGISTRY.get(auth_name)
                    is_valid = False
                    if User:
                        impersonator = User.find(id=impersonator_id)
                        # Security: only real admins can keep impersonating
                        if impersonator and getattr(impersonator, "is_admin", False):
                            is_valid = True
                            target_id = request.session.get("user_id")
                            if target_id and target_id != impersonator_id:
                                target = User.find(id=target_id)
                                if target:
                                    # Overwrite the request.user for this dispatch
                                    request.user = target
                                    request.impersonator = impersonator
                    # If impersonation is invalid (admin lost privileges), clean up session
                    if not is_valid:
                        request.session.pop("impersonator_id", None)
                        request.session.pop("impersonate_started_at", None)
                        request.session["user_id"] = impersonator_id
                        request.flash("error", self.t(request, "Unauthorized impersonation."))
        except Exception:
            pass

        path = request.path[len(self.prefix) :] or "/"
        method = request.method

        if path.startswith("/static/"):
            return self._serve_static(request, path[len("/static/") :])

        if path == "/login":
            return self._login(request)
        if path == "/2fa":
            return self._twofa_challenge(request)
        if path == "/lang":
            return self._set_locale(request)
        if path == "/logout":
            request.logout()
            try:
                request.session.pop("pending_2fa_uid", None)
            except Exception:
                pass
            request.flash("info", self.t(request, "You have been logged out."))
            raise RedirectException(self.prefix + "/login")

        # Global CSRF protection for all state-changing requests in admin
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if path not in ("/login", "/lang"):
                request.verify_csrf()

        self._require_admin(request)

        if path == "/" or path == "":
            return self._dashboard(request)

        if path == "/me":
            return self._me(request)

        if path == "/2fa-setup":
            return self._twofa_setup(request)

        if path == "/2fa-disable":
            return self._twofa_disable(request)

        if path == "/2fa-backup-codes":
            return self._twofa_backup_codes(request)

        if path == "/search":
            return self._search(request)

        if path.startswith("/impersonate/") and method == "POST":
            # Impersonation is strictly for superusers
            if not getattr(request.user, "is_admin", False):
                return self._forbid(request)
            return self._impersonate(request, path[len("/impersonate/") :])

        if path == "/stop-impersonate" and method == "POST":
            return self._stop_impersonate(request)

        if path == "/media":
            if not self._can(request, "assets", "view"):
                return self._forbid(request)
            return self._media_manager(request)

        if path == "/media/upload" and method == "POST":
            if not self._can(request, "assets", "add"):
                return self._forbid(request)
            return self._media_upload(request)

        if path.startswith("/media/delete/") and method == "POST":
            if not self._can(request, "assets", "delete"):
                return self._forbid(request)
            return self._delete_media(request, path[len("/media/delete/") :])

        parts = [p for p in path.split("/") if p]
        if not parts:
            return self._dashboard(request)

        slug = parts[0]
        entry = self._registered.get(slug)
        if not entry:
            return self._render_error(
                request,
                404,
                self.t(request, "Page Not Found"),
                self.t(
                    request,
                    "The page you are looking for does not exist or has been moved.",
                ),
            )

        if not self._can(request, slug, "view"):
            return self._forbid(request)

        if len(parts) == 1:
            if request.args.get("export") == "csv":
                if not self._can(request, slug, "export"):
                    return self._forbid(request)

                # SECURITY: Check export rate limit (max 5 per hour)
                allowed, remaining = self._export_rate_check(request)
                if not allowed:
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

                # Record this export for rate limiting
                self._export_rate_record(request)
                return self._export_csv(request, entry)
            return self._list(request, entry)

        action = parts[1]

        if action == "lookup":
            return self._lookup(request, entry)

        if action == "import":
            if not self._can(request, slug, "add"):
                return self._forbid(request, "adding disabled")
            return self._import_csv(request, entry)

        if action == "new":
            if not entry["can_add"] or not self._can(request, slug, "add"):
                return self._forbid(request, "adding disabled")
            if method == "POST":
                return self._create(request, entry)
            return self._edit_form(request, entry, None)

        if action == "trash":
            return self._trash(request, entry)

        if action == "bulk" and method == "POST":
            return self._bulk_action(request, entry)

        try:
            obj_id = int(action)
        except ValueError:
            return self._render_error(
                request,
                404,
                self.t(request, "Invalid ID"),
                self.t(request, "The requested item could not be found."),
            )

        if entry["model"]._soft_delete_field:
            item = entry["model"].with_trashed().where("id", obj_id).first()
        else:
            item = entry["model"].find(id=obj_id)
        if not item:
            return self._render_error(
                request,
                404,
                self.t(request, "Item Not Found"),
                self.t(
                    request, "The requested item does not exist or has been deleted."
                ),
            )

        if len(parts) == 3:
            sub = parts[2]
            if sub == "view":
                # Detail view (read-only)
                if not self._can(request, slug, "view"):
                    return self._forbid(request)
                return self._detail(request, entry, item)
            if sub == "history":
                return self._history(request, entry, item)
            if sub == "delete" and method == "POST":
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
            if sub == "restore" and method == "POST":
                if not self._can(request, slug, "edit"):
                    return self._forbid(request)
                item.restore()
                self._log(request, "restore", entry["model"].__name__, entity_id=obj_id)
                request.flash("success", self.t(request, "Restored"))
                raise RedirectException(self.prefix + "/" + slug + "/trash")
            if sub == "force-delete" and method == "POST":
                if not self._can(request, slug, "delete"):
                    return self._forbid(request)
                if self._is_self(request, entry, item):
                    request.flash(
                        "error", self.t(request, "You cannot delete your own account.")
                    )
                    raise RedirectException(self.prefix + "/" + slug + "/trash")
                item.force_delete()
                self._log(
                    request,
                    "force_delete",
                    entry["model"].__name__,
                    entity_id=obj_id,
                )
                request.flash("success", self.t(request, "Permanently deleted"))
                raise RedirectException(self.prefix + "/" + slug + "/trash")

        if method == "POST":
            if not entry["can_edit"] or not self._can(request, slug, "edit"):
                return self._forbid(request, "editing disabled")
            return self._update(request, entry, item)

        # GET: Show edit form if user has edit permission, otherwise detail view
        if self._can(request, slug, "edit"):
            return self._edit_form(request, entry, item)
        elif self._can(request, slug, "view"):
            # User can only view, not edit - redirect to detail view
            raise RedirectException(f"{self.prefix}/{slug}/{item.id}/view")
        else:
            return self._forbid(request)

    # ── Dashboard widget renderer ────────────────────────────

    def _dashboard(self, request: Any) -> Any:
        stats = []
        for slug, entry in self._registered.items():
            if not self._can(request, slug, "view"):
                continue
            model = entry["model"]
            # Main count
            try:
                count = model.count()
            except Exception:
                count = 0

            # Trend calculation (last 30 days)
            trend = None
            try:
                if "created_at" in model._fields:
                    now = datetime.datetime.now()
                    d30 = (now - datetime.timedelta(days=30)).isoformat()
                    d60 = (now - datetime.timedelta(days=60)).isoformat()

                    current_period = model.where("created_at", ">", d30).count()
                    previous_period = (
                        model.where("created_at", ">", d60)
                        .where("created_at", "<=", d30)
                        .count()
                    )

                    if previous_period > 0:
                        diff = (
                            (current_period - previous_period) / previous_period
                        ) * 100
                        trend = round(diff, 1)
            except Exception:
                pass

            stats.append(
                {"slug": slug, "label": entry["label"], "count": count, "trend": trend}
            )

        recent_logs = []
        can_view_logs = self._can(request, "logs", "view")
        if can_view_logs:
            auth_name = self.app.config.get("AUTH_MODEL", "User")
            User = MODELS_REGISTRY.get(auth_name)
            user_cache = {}
            for log in self._recent_logs(limit=10):
                label = "—"
                if log.user_id:
                    if log.user_id not in user_cache and User:
                        u = User.find(id=log.user_id)
                        user_cache[log.user_id] = (
                            _display(u) if u else f"#{log.user_id}"
                        )
                    label = user_cache.get(log.user_id, f"#{log.user_id}")
                log.user_label = label
                # Format date for display
                dt = str(log.created_at)
                if "T" in dt:
                    log.date_label = dt.replace("T", " ").split(".")[0]
                else:
                    log.date_label = dt
                recent_logs.append(log)

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
