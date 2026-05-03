"""Asok Admin — self-contained Django-style admin interface.

Usage in wsgi.py::

    from asok import Asok
    from asok.admin import Admin

    app = Asok()
    Admin(app, site_name="My Site", url_prefix="/admin")

Per-model customisation via inner ``Admin`` class::

    class Post(Model):
        title = Field.String()
        body = Field.String()
        is_published = Field.Boolean(default=False)
        deleted_at = Field.SoftDelete()

        def __str__(self):
            return self.title or f"Post #{self.id}"

        class Admin:
            label = "Articles"
            slug = "articles"
            list_display = ["id", "title", "is_published"]
            search_fields = ["title", "body"]
            list_filter = ["is_published"]
            readonly_fields = ["created_at"]
            fieldsets = [("Content", ["title", "body"]), ("Meta", ["is_published"])]
            per_page = 25
            inlines = ["comments"]               # HasMany relation names
            can_add = True
            can_edit = True
            can_delete = True
            actions = ["publish", "unpublish"]   # methods on Model class
"""

import base64
import csv
import datetime
import hashlib
import hmac
import importlib.util
import io
import json
import mimetypes
import os
import secrets
import struct
import threading
import time
from urllib.parse import quote, urlencode

from ..exceptions import AbortException, RedirectException
from ..forms import Form
from ..orm import MODELS_REGISTRY, ModelError, ModelList, Relation
from ..templates import SafeString, render_block_string, render_template_string
from .translations import LOCALES, MESSAGES, translate

# Admin permission verbs available per model
ADMIN_VERBS = ["view", "add", "edit", "delete", "export"]


class ModelAdmin:
    """Base class for inner Admin configuration in Models to provide autocompletion.

    Example:
        class Contact(Model):
            class Admin(ModelAdmin):
                list_display = ["id", "name"]
    """

    label: str = None
    slug: str = None
    group: str = "General"
    hidden: bool = False
    list_display: list[str] = None
    search_fields: list[str] = None
    list_filter: list[str] = None
    readonly_fields: list[str] = None
    form_exclude: list[str] = None
    fieldsets: list[tuple[str, list[str]]] = None
    per_page: int = 20
    inlines: list[str] = None
    can_add: bool = True
    can_edit: bool = True
    can_delete: bool = True
    actions: list[str] = None
    vector_search_field: str = None

# Above this many target rows, FK fields render as autocomplete instead of <select>
FK_AUTOCOMPLETE_THRESHOLD = 200

_DEFAULT_USER_MODEL_SRC = """\
from asok import Field, Model


class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password()
    name = Field.String()
    is_admin = Field.Boolean(default=False)
    created_at = Field.CreatedAt()
"""

_DEFAULT_ROLE_MODEL_SRC = """\
from asok import Field, Model


class Role(Model):
    name = Field.String(unique=True, nullable=False)
    label = Field.String()
    permissions = Field.String(default="")
    created_at = Field.CreatedAt()

    def __str__(self):
        return self.label or self.name
"""

_DEFAULT_LOG_MODEL_SRC = """\
from asok import Field, Model


class AdminLog(Model):
    user_id = Field.Integer(nullable=True)
    action = Field.String(nullable=False)
    entity = Field.String(nullable=False)
    entity_id = Field.Integer(nullable=True)
    changes = Field.String()
    created_at = Field.CreatedAt()

    class Admin:
        label = "Audit logs"
        slug = "logs"
        list_display = ["id", "created_at", "user_id", "action", "entity", "entity_id"]
        list_filter = ["action", "entity"]
        search_fields = ["action", "entity", "changes"]
        per_page = 50
        can_add = False
        can_edit = False
        can_delete = False
"""


def _user_roles_accessor(self):
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


def _user_role_ids(self):
    return [r.id for r in self.roles]


def _user_can(self, perm):
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


def _totp_at(secret_b32, t=None, step=30, digits=6):
    """RFC 6238 TOTP code at time `t` (defaults to now)."""
    if t is None:
        t = int(time.time())
    counter = int(t) // step
    # Pad base32 to multiple of 8
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def _totp_verify(secret_b32, code, window=1):
    if not secret_b32 or not code:
        return False
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        return False
    now = int(time.time())
    for offset in range(-window, window + 1):
        if _totp_at(secret_b32, t=now + offset * 30) == code:
            return True
    return False


def _totp_new_secret():
    """Random 160-bit base32 secret (no padding)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_uri(secret_b32, account, issuer):
    """otpauth:// URI for QR code apps."""
    label = quote(f"{issuer}:{account}")
    params = urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": 6,
            "period": 30,
        }
    )
    return f"otpauth://totp/{label}?{params}"


_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_TPL_DIR = os.path.join(_PKG_DIR, "templates")
_STATIC_DIR = os.path.join(_PKG_DIR, "static")

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif")


def _slugify_name(name):
    return name.lower() + "s"


def _humanize(name):
    out = []
    for i, c in enumerate(name):
        if i and c.isupper():
            out.append(" ")
        out.append(c)
    return "".join(out) + "s"


def _display(obj):
    if obj is None:
        return ""
    s = str(obj)
    if not s.startswith("<") or "id=" not in s:
        return s
    for attr in ("name", "title", "label", "email", "username", "slug"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    return f"#{getattr(obj, 'id', '?')}"


class Admin:
    def __init__(
        self,
        app,
        site_name="Asok Admin",
        url_prefix="/admin",
        login_rate_limit=(5, 900),
        default_locale="en",
        favicon=None,
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
        self._ensure_auth_models()
        self._ensure_role_pivot()
        self._ensure_2fa_columns()
        self._inject_user_methods()
        self._discover()
        app._admin = self

    def t(self, request, key, **kwargs):
        locale = self._resolve_locale(request)
        return translate(locale, key, **kwargs)

    # ── Auto-provision User + Role models ────────────────────

    def _ensure_model_file(self, filename, class_name, source):
        """Create src/models/<filename> from a default template if missing,
        then load it so it's in MODELS_REGISTRY. Returns the model class or None."""
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
                f"`asok migrate` then `asok createsuperuser`."
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
                try:
                    attr.create_table()
                except Exception:
                    pass
        return MODELS_REGISTRY.get(class_name)

    def _ensure_auth_models(self):
        """Ensure User, Role and AdminLog models exist. If the project was
        scaffolded without --admin and the dev added Admin(app) later,
        auto-create the model files so migrate + createsuperuser work."""
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        self._ensure_model_file("user.py", auth_name, _DEFAULT_USER_MODEL_SRC)
        self._ensure_model_file("role.py", "Role", _DEFAULT_ROLE_MODEL_SRC)
        self._ensure_model_file("admin_log.py", "AdminLog", _DEFAULT_LOG_MODEL_SRC)

    def _ensure_role_pivot(self):
        """Create the role_user pivot table if missing."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or "Role" not in MODELS_REGISTRY:
            return
        try:
            with User._get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS role_user ("
                    "role_id INTEGER NOT NULL, "
                    "user_id INTEGER NOT NULL, "
                    "PRIMARY KEY (role_id, user_id))"
                )
        except Exception as e:
            print(f"  [admin] Warning: could not create role_user pivot: {e}")

    def _ensure_2fa_columns(self):
        """Add totp_secret + totp_enabled columns to the User table if missing."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User:
            return
        try:
            with User._get_conn() as conn:
                cols = {
                    r[1]
                    for r in conn.execute(
                        f"PRAGMA table_info({User._table})"
                    ).fetchall()
                }
                if "totp_secret" not in cols:
                    conn.execute(
                        f"ALTER TABLE {User._table} ADD COLUMN totp_secret TEXT"
                    )
                if "totp_enabled" not in cols:
                    conn.execute(
                        f"ALTER TABLE {User._table} "
                        f"ADD COLUMN totp_enabled INTEGER DEFAULT 0"
                    )
        except Exception as e:
            print(f"  [admin] Warning: could not add 2FA columns: {e}")

    def _get_user_2fa(self, user_id):
        """Return (secret, enabled) for a user via raw SQL."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or not user_id:
            return None, False
        try:
            with User._get_conn() as conn:
                row = conn.execute(
                    f"SELECT totp_secret, totp_enabled FROM {User._table} WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if not row:
                    return None, False
                return row[0], bool(row[1])
        except Exception:
            return None, False

    def _set_user_2fa(self, user_id, secret, enabled):
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or not user_id:
            return
        try:
            with User._get_conn() as conn:
                conn.execute(
                    f"UPDATE {User._table} SET totp_secret = ?, totp_enabled = ? "
                    f"WHERE id = ?",
                    (secret, 1 if enabled else 0, user_id),
                )
        except Exception:
            pass

    def _inject_user_methods(self):
        """Attach roles accessor, can() helper, and BelongsToMany relation
        onto the User class, regardless of whether user.py declares them."""
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        if not User or "Role" not in MODELS_REGISTRY:
            return
        # Inject BelongsToMany relation so .sync('roles', ids) works via ORM
        if "roles" not in User._relations:
            User._relations["roles"] = Relation.BelongsToMany(
                "Role", pivot_table="role_user"
            )
        # Idempotent: re-assigning the same functions is harmless
        User.roles = _user_roles_accessor
        User.role_ids = _user_role_ids
        User.can = _user_can

    # ── Discovery ────────────────────────────────────────────

    def _discover(self):
        for model in self.app.models:
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

    def _default_columns(self, model):
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

    def _read_template(self, name):
        override = os.path.join(self.app.root_dir, "src/admin/templates", name)
        if os.path.isfile(override):
            with open(override, "r", encoding="utf-8") as f:
                return f.read(), os.path.dirname(override)
        path = os.path.join(_TPL_DIR, name)
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), _TPL_DIR

    def _render(self, request, name, **ctx):
        content, root = self._read_template(name)
        locale = self._resolve_locale(request)
        ctx["request"] = request
        ctx["get_flashed_messages"] = request.get_flashed_messages
        ctx["t"] = lambda key, **kwargs: translate(locale, key, **kwargs)

        # Smart static helper: if path exists in admin's internal static folder,
        # return /admin/static/path. Otherwise return /path as a project asset.
        def _static(p):
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
        ctx["admin_favicon"] = self.favicon
        ctx["is_impersonating"] = request.session.get("impersonator_id") is not None
        ctx.setdefault("active", None)
        crumbs = ctx.pop("breadcrumbs", [])
        parts = []
        for i, b in enumerate(crumbs):
            sep = ' <span class="sep">›</span> ' if i > 0 else ""
            label = translate(locale, b["label"])
            if b.get("url"):
                parts.append(f'{sep}<a href="{b["url"]}" data-spa>{label}</a>')
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
                    result_parts.append(
                        f'<template data-block="#{clean_name}">{frag}</template>'
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

    def _require_admin(self, request):
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
        raise RedirectException(self.prefix + "/login")

    def _can(self, request, slug, verb):
        """Check if the current user may perform `verb` on admin `slug`."""
        u = request.user
        if not u:
            return False
        if getattr(u, "is_admin", False):
            return True
        can_fn = getattr(u, "can", None)
        if not callable(can_fn):
            return False
        return bool(can_fn(f"{slug}.{verb}"))

    # ── i18n ─────────────────────────────────────────────────

    def _resolve_locale(self, request):
        """Pick the active locale: session > Accept-Language > default."""
        try:
            sess = request.session.get("admin_locale")
            if sess and sess in MESSAGES:
                return sess
        except Exception:
            pass
        accept = request.environ.get("HTTP_ACCEPT_LANGUAGE", "")
        for chunk in accept.split(","):
            code = chunk.split(";")[0].strip().lower()
            if not code:
                continue
            # Try full code (fr-fr)
            if code in MESSAGES:
                return code
            # Try base code (fr)
            base = code.split("-")[0]
            if base in MESSAGES:
                return base
        return self.default_locale

    def _set_locale(self, request):
        """Handle /admin/lang?code=fr — store choice in session and redirect back."""
        code = request.args.get("code", "")
        if code in MESSAGES:
            try:
                request.session["admin_locale"] = code
            except Exception:
                pass
        back = request.environ.get("HTTP_REFERER") or self.prefix
        # Only redirect back if it's within the same site's admin
        if "://" in back:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(back)
                if not parsed.path.startswith(self.prefix):
                    back = self.prefix
            except Exception:
                back = self.prefix
        elif not back.startswith(self.prefix):
            back = self.prefix

        raise RedirectException(back)

    def _render_error(self, request, code, title, message):
        """Render a beautiful error page consistent with admin design."""
        request.status_code(code)
        return self._render(
            request,
            "error.html",
            error_code=code,
            error_title=title,
            error_message=message,
        )

    def _forbid(self, request, msg="Forbidden"):
        return self._render_error(
            request,
            403,
            self.t(request, "Access Denied"),
            msg,
        )

    # ── Dispatcher ───────────────────────────────────────────

    def dispatch(self, request):
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
                    request.flash("info", "Impersonation expired (1 h max).")
                else:
                    auth_name = self.app.config.get("AUTH_MODEL", "User")
                    User = MODELS_REGISTRY.get(auth_name)
                    if User:
                        impersonator = User.find(id=impersonator_id)
                        # Security: only real admins can keep impersonating
                        if impersonator and getattr(impersonator, "is_admin", False):
                            target_id = request.session.get("user_id")
                            if target_id and target_id != impersonator_id:
                                target = User.find(id=target_id)
                                if target:
                                    # Overwrite the request.user for this dispatch
                                    request.user = target
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

        if path == "/search":
            return self._search(request)

        if path.startswith("/impersonate/") and method == "POST":
            return self._impersonate(request, path[len("/impersonate/") :])

        if path == "/stop-impersonate" and method == "POST":
            return self._stop_impersonate(request)

        if path == "/media":
            return self._media_manager(request)

        if path == "/media/upload" and method == "POST":
            return self._media_upload(request)

        if path.startswith("/media/delete/") and method == "POST":
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
                self.t(request, "The page you are looking for does not exist or has been moved."),
            )

        if not self._can(request, slug, "view"):
            return self._forbid(request)

        if len(parts) == 1:
            if request.args.get("export") == "csv":
                if not self._can(request, slug, "export"):
                    return self._forbid(request)
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
                self.t(request, "The requested item does not exist or has been deleted."),
            )

        if len(parts) == 3:
            sub = parts[2]
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
        return self._edit_form(request, entry, item)

    # ── Static serving ───────────────────────────────────────

    def _serve_static(self, request, name):
        full = os.path.abspath(os.path.join(_STATIC_DIR, name))
        if not full.startswith(_STATIC_DIR + os.sep) or not os.path.isfile(full):
            request.status_code(404)
            return "Not found"
        mime, _ = mimetypes.guess_type(full)
        request.content_type = mime or "application/octet-stream"
        with open(full, "rb") as f:
            request.environ["asok.binary_response"] = f.read()
        return ""

    # ── Auth pages ───────────────────────────────────────────

    def _client_ip(self, request):
        forwarded = request.environ.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.environ.get("REMOTE_ADDR", "unknown")

    def _login_rate_check(self, request):
        """Return (allowed, remaining_seconds). Read-only check; failures
        are recorded separately via _login_rate_record_failure()."""
        if self._login_limit_max is None:
            return True, 0
        now = time.time()
        key = self._client_ip(request)
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

    def _login_rate_record_failure(self, request):
        """Increment the failure counter for this IP."""
        if self._login_limit_max is None:
            return
        now = time.time()
        key = self._client_ip(request)
        with self._login_lock:
            bucket = self._login_buckets.get(key)
            if not bucket or bucket["reset"] <= now:
                bucket = {"count": 0, "reset": now + self._login_limit_window}
                self._login_buckets[key] = bucket
            bucket["count"] += 1

    def _login_rate_reset(self, request):
        if self._login_limit_max is None:
            return
        with self._login_lock:
            self._login_buckets.pop(self._client_ip(request), None)

    def _login(self, request):
        form = Form(
            {
                "email": Form.email("Email", "required|email", autofocus=True),
                "password": Form.password("Password", "required"),
            },
            request,
        )
        is_post = request.method == "POST"
        if is_post:
            allowed, remaining = self._login_rate_check(request)
            if not allowed:
                self._log(
                    request,
                    "login_rate_limited",
                    "User",
                    entity_id=None,
                    changes={"ip": self._client_ip(request)},
                )
                request.status_code(429)
                request.flash(
                    "error",
                    f"Too many failed attempts. Try again in {remaining}s.",
                )
                return self._render(request, "login.html", form=form)
        try:
            if form.validate():
                user = request.authenticate(
                    email=form.email.value, password=form.password.value
                )
                if user and (
                    getattr(user, "is_admin", False)
                    or (hasattr(user, "roles") and user.roles)
                ):
                    self._login_rate_reset(request)
                    _, totp_enabled = self._get_user_2fa(user.id)
                    if totp_enabled:
                        # Demote to a pending-2FA state
                        pending_uid = user.id
                        request.logout()
                        try:
                            request.session["pending_2fa_uid"] = pending_uid
                        except Exception:
                            pass
                        raise RedirectException(self.prefix + "/2fa")
                    self._log(
                        request,
                        "login",
                        "User",
                        entity_id=getattr(user, "id", None),
                    )
                    request.flash(
                        "success",
                        self.t(
                            request, "Welcome back, {name}!", name=user.name or user.email
                        ),
                    )
                    raise RedirectException(self.prefix)
                # Failed auth — count it
                self._login_rate_record_failure(request)
                self._log(
                    request,
                    "login_failed",
                    "User",
                    entity_id=None,
                    changes={"email": form.email.value},
                )
                request.flash("error", self.t(request, "Invalid credentials"))
        except AbortException as e:
            # Special handling for CSRF failure in login form to avoid 403 pages
            if e.status == 403:
                request.flash("error", self.t(request, "Security session expired. Please try again."))
            else:
                raise
        return self._render(request, "login.html", form=form)

    # ── 2FA / TOTP ───────────────────────────────────────────

    def _twofa_challenge(self, request):
        """Verify a TOTP code for a user mid-login (after password ok)."""
        try:
            pending_uid = request.session.get("pending_2fa_uid")
        except Exception:
            pending_uid = None
        if not pending_uid:
            raise RedirectException(self.prefix + "/login")
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        user = User.find(id=pending_uid) if User else None
        if not user:
            try:
                request.session.pop("pending_2fa_uid", None)
            except Exception:
                pass
            raise RedirectException(self.prefix + "/login")
        form = Form(
            {"code": Form.text("Authentication code", "required", autofocus=True)},
            request,
        )
        if request.method == "POST" and form.validate():
            allowed, remaining = self._login_rate_check(request)
            if not allowed:
                request.status_code(429)
                request.flash(
                    "error",
                    self.t(
                        request,
                        "Too many attempts. Try again in {remaining}s.",
                        remaining=remaining,
                    ),
                )
                return self._render(request, "2fa.html", form=form)
            secret, enabled = self._get_user_2fa(user.id)
            if enabled and _totp_verify(secret, form.code.value):
                self._login_rate_reset(request)
                try:
                    request.session.pop("pending_2fa_uid", None)
                except Exception:
                    pass
                request.login(user)
                self._log(
                    request,
                    "login",
                    "User",
                    entity_id=user.id,
                    changes={"twofa": True},
                )
                request.flash(
                    "success",
                    self.t(
                        request, "Welcome back, {name}!", name=user.name or user.email
                    ),
                )
                raise RedirectException(self.prefix)
            self._login_rate_record_failure(request)
            self._log(
                request,
                "login_2fa_failed",
                "User",
                entity_id=user.id,
            )
            request.flash("error", self.t(request, "Invalid code"))
        return self._render(request, "2fa.html", form=form)

    def _twofa_setup(self, request):
        """Enable 2FA for the current user."""
        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        secret, enabled = self._get_user_2fa(u.id)
        if enabled:
            request.flash("error", self.t(request, "2FA is already enabled."))
            raise RedirectException(self.prefix + "/me")
        # Use the existing pending secret in session, or generate a new one
        try:
            secret = request.session.get("pending_2fa_secret") or _totp_new_secret()
            request.session["pending_2fa_secret"] = secret
        except Exception:
            secret = _totp_new_secret()
        account = getattr(u, "email", None) or f"user-{u.id}"
        uri = _totp_uri(secret, account, self.site_name)
        form = Form(
            {"code": Form.text("Verification code", "required", autofocus=True)},
            request,
        )
        if request.method == "POST" and form.validate():
            if _totp_verify(secret, form.code.value):
                self._set_user_2fa(u.id, secret, True)
                try:
                    request.session.pop("pending_2fa_secret", None)
                except Exception:
                    pass
                self._log(request, "2fa_enabled", "User", entity_id=u.id)
                request.flash(
                    "success", self.t(request, "Two-factor authentication enabled.")
                )
                raise RedirectException(self.prefix + "/me")
            request.flash("error", self.t(request, "Invalid code, try again."))
        return self._render(
            request,
            "2fa_setup.html",
            form=form,
            secret=secret,
            uri=uri,
            active=None,
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "My profile", "url": self.prefix + "/me"},
                {"label": "Enable 2FA", "url": None},
            ],
        )

    def _twofa_disable(self, request):
        """Disable 2FA for the current user (requires current password)."""
        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        if request.method != "POST":
            raise RedirectException(self.prefix + "/me")
        pw = request.form.get("current_password", "")
        if not pw or not u.check_password("password", pw):
            request.flash("error", self.t(request, "Current password is incorrect."))
            raise RedirectException(self.prefix + "/me")
        self._set_user_2fa(u.id, None, False)
        self._log(request, "2fa_disabled", "User", entity_id=u.id)
        request.flash("success", self.t(request, "Two-factor authentication disabled."))
        raise RedirectException(self.prefix + "/me")

    # ── Audit log ────────────────────────────────────────────

    def _snapshot(self, item):
        """Capture field values for diff computation. Skips passwords."""
        if not item:
            return {}
        snap = {}
        for name, field in item._fields.items():
            if getattr(field, "is_password", False):
                continue
            snap[name] = getattr(item, name, None)
        return snap

    def _diff(self, before, after):
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

    def _log(self, request, action, entity, entity_id=None, changes=None):
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
                import json

                log.changes = json.dumps(changes)
            try:
                log.save()
            except Exception:
                pass
        except Exception:
            pass

    def _recent_logs(self, limit=10):
        AdminLog = MODELS_REGISTRY.get("AdminLog")
        if not AdminLog:
            return []
        try:
            return AdminLog.query().order_by("-id").limit(limit).get()
        except Exception:
            return []

    def _history(self, request, entry, item):
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

    def _slug_for_model(self, model):
        """Return the registered admin slug for a model class, or None."""
        for s, e in self._registered.items():
            if e["model"] is model:
                return s
        return None

    def _is_self(self, request, entry, item):
        """True if item is the currently-logged-in user (for self-protection)."""
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        return (
            entry["model"].__name__ == auth_name
            and request.user
            and getattr(request.user, "id", None) == getattr(item, "id", None)
        )

    def _me(self, request):
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
                    form._fields[k]._value = ""
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
                        form._fields["new_password"]._error = (
                            "User model has no password field"
                        )
                    elif not cur:
                        form._fields["current_password"]._error = (
                            "Current password required"
                        )
                    elif not me.check_password(pw_field, cur):
                        form._fields["current_password"]._error = (
                            "Current password is incorrect"
                        )
                    elif new != conf:
                        form._fields["confirm_password"]._error = (
                            "Passwords do not match"
                        )
                    elif len(new) < 6:
                        form._fields["new_password"]._error = (
                            "Password must be at least 6 characters"
                        )
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

    # ── Custom dashboard widgets ─────────────────────────────

    def add_widget(self, title, render, size="medium", permission=None):
        """Register a custom dashboard widget.

        - title: display title
        - render: callable(request) -> str (HTML body) or dict
            {"html": str, "footer": str}
        - size: 'small' | 'medium' | 'large' (CSS hint)
        - permission: optional 'slug.verb' string; widget hidden if user lacks it
        """
        self._widgets.append(
            {
                "title": title,
                "render": render,
                "size": size,
                "permission": permission,
            }
        )

    def widget(self, title, size="medium", permission=None):
        """Decorator form: @admin.widget("Title")"""

        def deco(fn):
            self.add_widget(title, fn, size=size, permission=permission)
            return fn

        return deco

    def _render_widgets(self, request):
        """Run each registered widget; skip ones the user can't see or that error."""
        out = []
        for w in self._widgets:
            if w["permission"]:
                try:
                    slug, verb = w["permission"].split(".", 1)
                except ValueError:
                    continue
                if not self._can(request, slug, verb):
                    continue
            try:
                result = w["render"](request)
            except Exception as e:
                result = f'<div class="muted">Widget error: {e}</div>'
            if isinstance(result, dict):
                html = result.get("html", "")
                footer = result.get("footer", "")
            else:
                html = str(result or "")
                footer = ""
            out.append(
                {
                    "title": w["title"],
                    "size": w["size"],
                    "html": SafeString(html),
                    "footer": SafeString(footer),
                }
            )
        return out

    def _search(self, request):
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

    def _dashboard(self, request):
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
                    pass
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

    # ── Column display ───────────────────────────────────────

    def _col_value(self, item, col, model):
        field = model._fields.get(col)
        # Never render hidden fields, even if explicitly requested in columns
        if field and getattr(field, "hidden", False):
            return SafeString('<span class="muted">[hidden]</span>')
        if field and getattr(field, "is_foreign_key", False):
            val = getattr(item, col, None)
            if val:
                target_model = field.related_model
                if isinstance(target_model, str):
                    target_model = MODELS_REGISTRY.get(target_model)
                if target_model:
                    rel = target_model.find(id=val)
                    return _display(rel) if rel else f"#{val}"
                return f"#{val}"
            return SafeString('<span class="muted">—</span>')
        # Calculated column (method on model)
        if col not in model._fields and col != "id" and hasattr(item, col):
            attr = getattr(item, col)
            v = attr() if callable(attr) else attr
        else:
            v = getattr(item, col, "")
        if v is None or v == "":
            return SafeString('<span class="muted">—</span>')
        # Boolean badge
        if (
            field
            and field.sql_type == "INTEGER"
            and (col.startswith("is_") or col.startswith("has_"))
        ):
            if v:
                return SafeString('<span class="badge badge-yes">Yes</span>')
            return SafeString('<span class="badge badge-no">No</span>')
        # Clean output for strings (WYSIWYG stripping)
        if getattr(field, "wysiwyg", False):
            import re

            s = re.sub(r"<[^>]+>", "", str(v))
        else:
            s = str(v)

        return s if len(s) <= 60 else s[:60] + "…"

    # ── Query string preservation ────────────────────────────

    def _qs(self, request, **overrides):
        """Build a query string preserving current params, with overrides.
        Pass key=None to drop a key."""
        base = dict(request.args)
        for k, v in overrides.items():
            if v is None:
                base.pop(k, None)
            else:
                base[k] = v
        return "?" + urlencode(base) if base else ""

    # ── Query building ───────────────────────────────────────

    def _build_query(self, request, entry, with_trashed=False):
        model = entry["model"]
        q = request.args.get("q", "") or ""
        if with_trashed:
            query = model.only_trashed()
        else:
            query = model.query()

        if q and entry["searchable"]:
            # ASOK VECTOR EXTENSION: Check if we can perform a semantic search
            v_field = entry.get("vector_search_field")
            vector = None
            if v_field:
                # Try to get vector by calling model.embed_query or Admin.embed_query
                embed_fn = getattr(model, "embed_query", None)
                if callable(embed_fn):
                    try:
                        vector = embed_fn(q)
                    except Exception:
                        pass

                if vector:
                    # Switch to vector search!
                    return query.nearest(v_field, vector)

            placeholders = []
            search_args = []
            for f in entry["searchable"]:
                if model._valid_column(f):
                    placeholders.append(f"{f} LIKE ?")
                    search_args.append(f"%{q}%")
            if placeholders:
                query._wheres.append("(" + " OR ".join(placeholders) + ")")
                query._args.extend(search_args)

        for f in entry["list_filter"]:
            val = request.args.get(f"filter_{f}")
            if val not in (None, "", "__all__"):
                query = query.where(f, val)

        return query

    def _build_filters(self, request, entry):
        out = []
        model = entry["model"]
        for f in entry["list_filter"]:
            field = model._fields.get(f)
            if not field:
                continue
            try:
                with model._get_conn() as conn:
                    rows = conn.execute(
                        f"SELECT DISTINCT {f} FROM {model._table} ORDER BY {f}"
                    ).fetchall()
                values = [r[0] for r in rows if r[0] is not None]
            except Exception:
                values = []
            current = request.args.get(f"filter_{f}", "")
            options = [{"value": "", "label": "All", "selected": current == ""}]
            for v in values:
                label = str(v)
                if field.sql_type == "INTEGER" and (
                    f.startswith("is_") or f.startswith("has_")
                ):
                    label = "Yes" if v else "No"
                options.append(
                    {
                        "value": str(v),
                        "label": label,
                        "selected": str(v) == current,
                    }
                )
            out.append(
                {"name": f, "label": f.replace("_", " ").title(), "options": options}
            )
        return out

    # ── List view ────────────────────────────────────────────

    def _sort_links(self, request, columns, current_sort):
        out = []
        for col in columns:
            arrow = ""
            new_sort = col
            if current_sort == col:
                arrow = " ↑"
                new_sort = "-" + col
            elif current_sort == "-" + col:
                arrow = " ↓"
                new_sort = col
            out.append(
                {
                    "col": col,
                    "arrow": arrow,
                    "url": self._qs(request, sort=new_sort, page=None),
                    "sort": new_sort,
                }
            )
        return out

    def _list(self, request, entry, trash=False):
        model = entry["model"]
        per_page = entry["per_page"]
        page = max(1, int(request.args.get("page", 1) or 1))
        q = request.args.get("q", "") or ""
        sort = request.args.get("sort", "-id") or "-id"

        query = self._build_query(request, entry, with_trashed=trash)
        try:
            query = query.order_by(sort)
        except ValueError:
            query = query.order_by("-id")

        total = query.count()
        pages = max(1, (total + per_page - 1) // per_page)
        items = query.limit(per_page).offset((page - 1) * per_page).get()

        item_dicts = []
        for it in items:
            d = {"id": it.id}
            for col in entry["columns"]:
                d[col] = self._col_value(it, col, model)
            item_dicts.append(d)

        # Fields available for bulk-edit (simple scalar columns only)
        bulk_edit_fields = []
        if (
            not trash
            and entry["can_edit"]
            and self._can(request, entry["slug"], "edit")
        ):
            for n, f in model._fields.items():
                if getattr(f, "is_password", False):
                    continue
                if getattr(f, "is_file", False):
                    continue
                if getattr(f, "is_timestamp", False):
                    continue
                if getattr(f, "is_soft_delete", False):
                    continue
                if getattr(f, "is_foreign_key", False):
                    continue
                if n in entry["readonly_fields"]:
                    continue
                bulk_edit_fields.append(
                    {"name": n, "label": n.replace("_", " ").title()}
                )

        bulk_actions = []
        if trash:
            bulk_actions = [
                {"name": "restore", "label": "Restore selected"},
                {"name": "force_delete", "label": "Delete permanently"},
            ]
        else:
            if entry["can_delete"]:
                bulk_actions.append({"name": "delete", "label": "Delete selected"})
            for act in entry["actions"]:
                bulk_actions.append(
                    {"name": act, "label": act.replace("_", " ").title()}
                )

        breadcrumbs = [
            {"label": "Dashboard", "url": self.prefix},
            {
                "label": entry["label"],
                "url": None if not trash else self.prefix + "/" + entry["slug"],
            },
        ]
        if trash:
            breadcrumbs.append({"label": "Trash", "url": None})

        # Check if this model is the User/Auth model
        auth_model_name = self.app.config.get("AUTH_MODEL", "User")
        is_auth_model = entry["model"].__name__ == auth_model_name

        return self._render(
            request,
            "list.html",
            is_auth_model=is_auth_model,
            items=item_dicts,
            columns=entry["columns"],
            sort_links=self._sort_links(request, entry["columns"], sort),
            slug=entry["slug"],
            model_label=entry["label"],
            page=page,
            pages=pages,
            total=total,
            q=q,
            sort=sort,
            filters=self._build_filters(request, entry),
            active_filters=[
                {"name": f, "value": request.args.get(f"filter_{f}", "")}
                for f in entry["list_filter"]
            ],
            trash=trash,
            has_soft_delete=bool(model._soft_delete_field),
            bulk_actions=bulk_actions,
            bulk_edit_fields=bulk_edit_fields,
            can_add=entry["can_add"],
            can_edit=entry["can_edit"],
            can_delete=entry["can_delete"],
            prev_url=self._qs(request, page=page - 1),
            next_url=self._qs(request, page=page + 1),
            export_url=self._qs(request, export="csv", page=None),
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
        )

    def _trash(self, request, entry):
        if not entry["model"]._soft_delete_field:
            return self._render_error(
                request,
                404,
                self.t(request, "Trash Not Available"),
                self.t(request, "This model does not support soft delete functionality."),
            )
        return self._list(request, entry, trash=True)

    # ── Bulk + custom actions ────────────────────────────────

    def _bulk_action(self, request, entry):
        action = request.form.get("action")
        ids_raw = request.form.get("ids", "")
        ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        if not ids or not action:
            raise RedirectException(self.prefix + "/" + entry["slug"])
        model = entry["model"]
        slug = entry["slug"]
        # Prevent self-targeting on the User model
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        if (
            model.__name__ == auth_name
            and request.user
            and getattr(request.user, "id", None) in ids
        ):
            ids = [i for i in ids if i != request.user.id]
            request.flash(
                "error", self.t(request, "You cannot target your own account.")
            )
            if not ids:
                raise RedirectException(self.prefix + "/" + slug)
        if action == "delete":
            if not entry["can_delete"] or not self._can(request, slug, "delete"):
                return self._forbid(request)
            for i in ids:
                obj = model.find(id=i)
                if obj:
                    obj.delete()
            self._log(
                request,
                "bulk_delete",
                model.__name__,
                entity_id=None,
                changes={"ids": ids},
            )
            request.flash(
                "success", self.t(request, "Deleted {count} items", count=len(ids))
            )
        elif action == "force_delete" and model._soft_delete_field:
            for i in ids:
                obj = model.with_trashed().where("id", i).first()
                if obj:
                    obj.force_delete()
            self._log(
                request,
                "bulk_force_delete",
                model.__name__,
                entity_id=None,
                changes={"ids": ids},
            )
            request.flash(
                "success",
                self.t(request, "Permanently deleted {count} items", count=len(ids)),
            )
        elif action == "restore" and model._soft_delete_field:
            for i in ids:
                obj = model.with_trashed().where("id", i).first()
                if obj:
                    obj.restore()
            self._log(
                request,
                "bulk_restore",
                model.__name__,
                entity_id=None,
                changes={"ids": ids},
            )
            request.flash(
                "success", self.t(request, "Restored {count} items", count=len(ids))
            )
        elif action.startswith("set:"):
            if not entry["can_edit"] or not self._can(request, slug, "edit"):
                return self._forbid(request)
            field_name = action[4:]
            field = model._fields.get(field_name)
            if not field:
                request.flash(
                    "error",
                    self.t(request, "Unknown field '{field}'", field=field_name),
                )
                raise RedirectException(self.prefix + "/" + slug)
            raw = request.form.get("bulk_value", "")
            # Coerce to the field's type
            if field.sql_type == "INTEGER":
                if field_name.startswith("is_") or field_name.startswith("has_"):
                    val = 1 if raw in ("1", "on", "true", "yes") else 0
                elif raw in (None, ""):
                    val = None
                else:
                    try:
                        val = int(raw)
                    except (ValueError, TypeError):
                        val = None
            elif field.sql_type == "REAL":
                try:
                    val = float(raw) if raw else None
                except (ValueError, TypeError):
                    val = None
            else:
                val = raw or None
            count = 0
            for i in ids:
                obj = model.find(id=i)
                if obj:
                    setattr(obj, field_name, val)
                    try:
                        obj.save()
                        count += 1
                    except Exception:
                        pass
            self._log(
                request,
                "bulk_edit",
                model.__name__,
                entity_id=None,
                changes={"ids": ids, "field": field_name, "value": str(val)},
            )
            request.flash(
                "success", self.t(request, "Updated {count} items", count=count)
            )
        elif action in entry["actions"]:
            fn = getattr(model, action, None)
            if not fn:
                request.flash(
                    "error",
                    self.t(
                        request, "Action '{action}' not found on model", action=action
                    ),
                )
            else:
                count = 0
                for i in ids:
                    obj = model.find(id=i)
                    if obj and hasattr(obj, action):
                        getattr(obj, action)()
                        count += 1
                self._log(
                    request,
                    f"action:{action}",
                    model.__name__,
                    entity_id=None,
                    changes={"ids": ids, "count": count},
                )
                request.flash(
                    "success",
                    self.t(
                        request,
                        "Applied '{action}' to {count} items",
                        action=action,
                        count=count,
                    ),
                )
        raise RedirectException(self.prefix + "/" + entry["slug"])

    # ── CSV export ───────────────────────────────────────────

    def _export_csv(self, request, entry):
        query = self._build_query(request, entry)
        try:
            query = query.order_by(request.args.get("sort", "-id") or "-id")
        except ValueError:
            query = query.order_by("-id")
        items = query.get()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(entry["columns"])
        for it in items:
            row = []
            for col in entry["columns"]:
                v = (
                    getattr(it, col, "")
                    if col in entry["model"]._fields or col == "id"
                    else (
                        getattr(it, col)()
                        if callable(getattr(it, col, None))
                        else getattr(it, col, "")
                    )
                )
                row.append("" if v is None else str(v))
            writer.writerow(row)
        data = buf.getvalue().encode("utf-8")
        request.content_type = "text/csv"
        request.environ["asok.binary_response"] = data
        request.environ["asok.extra_headers"] = [
            ("Content-Disposition", f'attachment; filename="{entry["slug"]}.csv"')
        ]
        return ""

    # ── FK autocomplete lookup ───────────────────────────────

    def _lookup(self, request, entry):
        """Return JSON [{id,label}] for FK autocomplete on this model."""
        model = entry["model"]
        q = (request.args.get("q", "") or "").strip()
        try:
            limit = max(1, min(50, int(request.args.get("limit", 20) or 20)))
        except ValueError:
            limit = 20
        query = model.query()
        if q:
            searchable = entry["searchable"] or []
            placeholders = []
            for f in searchable:
                if model._valid_column(f):
                    placeholders.append(f"{f} LIKE ?")
                    query._args.append(f"%{q}%")
            if placeholders:
                query._wheres.append("(" + " OR ".join(placeholders) + ")")
            elif q.isdigit():
                query._wheres.append("id = ?")
                query._args.append(int(q))
        try:
            items = query.order_by("-id").limit(limit).get()
        except Exception:
            items = []
        data = [{"id": o.id, "label": _display(o)} for o in items]
        request.content_type = "application/json"
        return json.dumps(data)

    # ── CSV import ───────────────────────────────────────────

    def _importable_fields(self, model):
        """Return field names that can be imported (skip auto/timestamps/passwords/files)."""
        out = []
        for name, field in model._fields.items():
            if getattr(field, "protected", False):
                continue
            if getattr(field, "is_timestamp", False):
                continue
            if getattr(field, "is_soft_delete", False):
                continue
            if getattr(field, "is_password", False):
                continue
            if getattr(field, "is_file", False):
                continue
            if getattr(field, "is_slug", False) and field.populate_from:
                continue
            out.append(name)
        return out

    def _import_csv(self, request, entry):
        model = entry["model"]
        importable = self._importable_fields(model)
        report = None
        if request.method == "POST":
            upload = request.files.get("file") if request.files else None
            update_existing = bool(request.form.get("update_existing"))
            if not upload or not upload.filename:
                request.flash("error", self.t(request, "Choose a CSV file to upload."))
            else:
                try:
                    raw = (
                        upload.file.read() if hasattr(upload, "file") else upload.read()
                    )
                    if isinstance(raw, bytes):
                        text = raw.decode("utf-8-sig")
                    else:
                        text = raw
                    reader = csv.DictReader(io.StringIO(text))
                    created = 0
                    updated = 0
                    failed = 0
                    errors = []
                    for i, row in enumerate(reader, start=2):
                        data = {}
                        for k, v in row.items():
                            if not k:
                                continue
                            key = k.strip()
                            # Special case: allow 'id' even if not in _importable_fields
                            if key != "id" and key not in importable:
                                continue
                            field = model._fields.get(key)
                            val = (v or "").strip() if v is not None else ""
                            if val == "":
                                data[key] = None
                                continue

                            # Handle ID separately
                            if key == "id":
                                try:
                                    data[key] = int(val)
                                except ValueError:
                                    pass
                                continue

                            if field.sql_type == "INTEGER":
                                if key.startswith("is_") or key.startswith("has_"):
                                    data[key] = (
                                        1
                                        if val.lower() in ("1", "true", "yes", "on")
                                        else 0
                                    )
                                else:
                                    try:
                                        data[key] = int(val)
                                    except ValueError:
                                        data[key] = None
                            elif field.sql_type == "REAL":
                                try:
                                    data[key] = float(val)
                                except ValueError:
                                    data[key] = None
                            else:
                                data[key] = val
                        try:
                            item = None
                            if update_existing:
                                # 1. Try to find by explicit ID
                                if data.get("id"):
                                    item = model.find(id=data["id"])

                                # 2. Try to find by unique fields
                                if not item:
                                    for f_name, f_obj in model._fields.items():
                                        if getattr(f_obj, "unique", False) and data.get(
                                            f_name
                                        ):
                                            item = model.find(**{f_name: data[f_name]})
                                            if item:
                                                break

                            if item:
                                # Update existing
                                # Remove ID from data to avoid trying to update PK
                                data.pop("id", None)
                                item.update(**data)
                                updated += 1
                            else:
                                # Create new
                                model.create(**data)
                                created += 1
                        except Exception as e:
                            failed += 1
                            if len(errors) < 10:
                                errors.append(f"Row {i}: {e}")
                    self._log(
                        request,
                        "import_csv",
                        model.__name__,
                        entity_id=None,
                        changes={
                            "created": created,
                            "updated": updated,
                            "failed": failed,
                        },
                    )
                    report = {
                        "created": created,
                        "updated": updated,
                        "failed": failed,
                        "errors": errors,
                    }
                    if failed == 0:
                        msg = self.t(
                            request, "Imported {count} rows", count=created + updated
                        )
                        if updated:
                            msg += f" ({updated} {self.t(request, 'updated')})"
                        request.flash("success", msg)
                    else:
                        request.flash(
                            "error",
                            self.t(
                                request,
                                "Imported {created}, {failed} failed",
                                created=created,
                                failed=failed,
                            ),
                        )
                except Exception as e:
                    request.flash(
                        "error", self.t(request, "CSV parse error: {error}", error=e)
                    )
        return self._render(
            request,
            "import.html",
            slug=entry["slug"],
            model_label=entry["label"],
            importable=importable,
            report=report,
            active=entry["slug"],
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": entry["label"], "url": self.prefix + "/" + entry["slug"]},
                {"label": "Import CSV", "url": None},
            ],
        )

    # ── Form rendering ───────────────────────────────────────

    def _field_meta(self, name, field, readonly_set, is_creation=False):
        """Build a Form factory tuple + extra metadata for one model field.

        Returns (form_field_tuple, meta_dict) or (None, None) if the field
        should be skipped entirely.
        """
        if getattr(field, "is_soft_delete", False):
            return None, None
        is_readonly = name in readonly_set
        is_timestamp = getattr(field, "is_timestamp", False)
        if is_timestamp and not is_readonly:
            return None, None

        label = name.replace("_", " ").title()
        rules_parts = []
        if getattr(field, "nullable", True) is False and not is_readonly:
            if not getattr(field, "is_password", False):
                rules_parts.append("required")
        # Password fields: required on creation, optional on edit
        if getattr(field, "is_password", False) and is_creation and not is_readonly:
            rules_parts.append("required")
        if getattr(field, "is_email", False):
            rules_parts.append("email")
        max_length = getattr(field, "max_length", None)
        if max_length:
            rules_parts.append(f"max:{max_length}")
        rules = "|".join(rules_parts)
        attrs = {}
        if is_readonly:
            attrs["readonly"] = True
        if max_length:
            attrs["maxlength"] = max_length

        meta = {
            "name": name,
            "is_password": False,
            "is_file": False,
            "is_image": False,
            "is_text": False,
            "wysiwyg": False,
            "is_fk_autocomplete": False,
            "fk_target_slug": None,
            "fk_current_label": "",
            "file_value": "",
        }

        if getattr(field, "is_password", False):
            meta["is_password"] = True
            tup = Form.password(label, rules, **attrs)
        elif getattr(field, "is_file", False):
            meta["is_file"] = True
            tup = Form.file(label, rules, **attrs)
        elif getattr(field, "is_foreign_key", False):
            target = field.related_model
            if isinstance(target, str):
                target = MODELS_REGISTRY.get(target)

            try:
                count = target.count() if target else 0
            except Exception:
                count = 0
            use_auto = (
                getattr(field, "autocomplete", False)
                or count > FK_AUTOCOMPLETE_THRESHOLD
            )
            if use_auto:
                meta["is_fk_autocomplete"] = True
                meta["fk_target_slug"] = self._slug_for_model(target)
                tup = Form.text(label, rules, **attrs)
            else:
                try:
                    choices = [
                        (o.id, _display(o))
                        for o in target.all(limit=FK_AUTOCOMPLETE_THRESHOLD)
                    ]
                except Exception:
                    choices = []
                choices = [("", "— None —")] + choices
                tup = Form.select(label, choices, rules, **attrs)
        elif getattr(field, "is_boolean", False):
            tup = Form.checkbox(label, rules, **attrs)
        elif getattr(field, "is_enum", False):
            tup = Form.enum(label, field.enum_class, rules, **attrs)
        elif field.sql_type == "INTEGER":
            if name.startswith("is_") or name.startswith("has_"):
                tup = Form.checkbox(label, rules, **attrs)
            else:
                tup = Form.number(label, rules, **attrs)
        elif field.sql_type == "REAL":
            precision = getattr(field, "precision", None)
            if precision is not None:
                attrs["step"] = f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
            tup = Form.number(label, rules, **attrs)
        elif getattr(field, "is_email", False):
            tup = Form.email(label, rules, **attrs)
        elif getattr(field, "is_text", False):
            meta["is_text"] = True
            meta["wysiwyg"] = getattr(field, "wysiwyg", False)
            tup = Form.textarea(label, rules, **attrs)
        elif getattr(field, "is_datetime", False):
            tup = Form.datetime_local(label, rules, **attrs)
        elif getattr(field, "is_date", False):
            tup = Form.date(label, rules, **attrs)
        elif getattr(field, "is_time", False):
            tup = Form.time(label, rules, **attrs)
        else:
            # Fallback: heuristic based on field name
            if is_timestamp or name.endswith("_at") or name.endswith("_on"):
                tup = Form.datetime_local(label, rules, **attrs)
            elif name == "date" or name.endswith("_date"):
                tup = Form.date(label, rules, **attrs)
            else:
                tup = Form.text(label, rules, **attrs)
        return tup, meta

    def _build_form(self, request, entry, item, errors=None):
        """Build a real Form instance from the model fields and pre-fill from item."""
        model = entry["model"]
        readonly_set = set(entry["readonly_fields"])
        form_exclude = set(entry["form_exclude"])
        # Auto-readonly slugs that auto-populate and always update
        for name, field in model._fields.items():
            if getattr(field, "is_slug", False) and getattr(
                field, "always_update", False
            ):
                readonly_set.add(name)

        schema = {}
        meta = {}
        is_creation = not (item and getattr(item, 'id', None))
        for name, field in model._fields.items():
            # Skip excluded fields
            if name in form_exclude:
                continue
            tup, m = self._field_meta(name, field, readonly_set, is_creation)
            if tup is None:
                continue
            schema[name] = tup
            meta[name] = m

        form = Form(schema, request)
        if item and item.id:
            # Snapshot file values for preview before fill (Form file inputs render empty)
            for name, m in meta.items():
                if m["is_file"]:
                    val = getattr(item, name, "") or ""
                    m["file_value"] = str(val)
                    if m["file_value"].lower().endswith(_IMAGE_EXTS):
                        m["is_image"] = True
                if m.get("is_fk_autocomplete"):
                    val = getattr(item, name, None)
                    if val:
                        field = model._fields.get(name)
                        if field and getattr(field, "related_model", None):
                            try:
                                target_model = field.related_model
                                if isinstance(target_model, str):
                                    target_model = MODELS_REGISTRY.get(target_model)
                                rel = (
                                    target_model.find(id=val) if target_model else None
                                )
                            except Exception:
                                rel = None
                            if rel:
                                m["fk_current_label"] = _display(rel)
            form.fill(item)

        # Apply per-field errors from a previous failed save
        if errors:
            for fname, err in errors.items():
                if fname in form._fields:
                    form._fields[fname]._error = err

        return form, meta

    def _grouped_fields(self, entry, form, meta):
        """Return [{label, fields:[{f, m}, ...]}, ...] honoring fieldsets."""

        def pair(name):
            return {"f": form._fields[name], "m": meta[name]}

        ordered = list(form._fields.keys())
        if not entry["fieldsets"]:
            return [{"label": None, "fields": [pair(n) for n in ordered]}]
        groups = []
        seen = set()
        for label, names in entry["fieldsets"]:
            items = [pair(n) for n in names if n in form._fields]
            seen.update(names)
            groups.append({"label": label, "fields": items})
        extras = [pair(n) for n in ordered if n not in seen]
        if extras:
            groups.append({"label": "Other", "fields": extras})
        return groups

    def _build_m2m(self, model, item):
        """Return list of {name, label, options, selected_ids} for BelongsToMany relations."""
        out = []
        for name, rel in getattr(model, "_relations", {}).items():
            if rel.type != "BelongsToMany":
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue
            try:
                all_options = [
                    {"id": o.id, "label": _display(o)} for o in target.all(limit=500)
                ]
            except Exception:
                all_options = []
            selected_ids = []
            if item and item.id:
                try:
                    selected_ids = [o.id for o in getattr(item, name)()]
                except Exception:
                    selected_ids = []
            for opt in all_options:
                opt["selected"] = opt["id"] in selected_ids
            out.append(
                {
                    "name": name,
                    "label": name.replace("_", " ").title(),
                    "options": all_options,
                }
            )
        return out

    def _build_inlines(self, entry, item):
        """Render related HasMany rows as inline list."""
        if not item or not item.id:
            return []
        out = []
        model = entry["model"]
        for rel_name in entry["inlines"]:
            rel = model._relations.get(rel_name)
            if not rel or rel.type != "HasMany":
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue
            target_slug = None
            for s, e in self._registered.items():
                if e["model"] is target:
                    target_slug = s
                    break
            try:
                children = getattr(item, rel_name)()
            except Exception:
                children = []
            cols = []
            for k in target._fields:
                if getattr(target._fields[k], "is_password", False):
                    continue
                cols.append(k)
                if len(cols) >= 4:
                    break
            rows = []
            for c in children:
                d = {"id": c.id}
                for col in cols:
                    d[col] = self._col_value(c, col, target)
                rows.append(d)
            out.append(
                {
                    "name": rel_name,
                    "label": rel_name.replace("_", " ").title(),
                    "columns": cols,
                    "rows": rows,
                    "target_slug": target_slug,
                }
            )
        return out

    def _build_permission_matrix(self, request, item):
        """Build a checkbox matrix (models × verbs) for Role.permissions."""
        current_raw = (getattr(item, "permissions", "") or "") if item else ""
        perms = {p.strip() for p in current_raw.split(",") if p.strip()}
        wildcard = "*" in perms
        rows = []
        for slug, entry in self._registered.items():
            # Hide the Role row itself from the matrix to avoid lockout loops
            if entry["model"].__name__ == "Role":
                cells = []
                for v in ADMIN_VERBS:
                    perm = f"{slug}.{v}"
                    cells.append(
                        {
                            "perm": perm,
                            "checked": wildcard
                            or perm in perms
                            or f"{slug}.*" in perms,
                        }
                    )
                rows.append({"slug": slug, "label": entry["label"], "cells": cells})
                continue
            cells = []
            for v in ADMIN_VERBS:
                perm = f"{slug}.{v}"
                cells.append(
                    {
                        "perm": perm,
                        "checked": wildcard or perm in perms or f"{slug}.*" in perms,
                    }
                )
            rows.append({"slug": slug, "label": entry["label"], "cells": cells})
        return {
            "verbs": ADMIN_VERBS,
            "rows": rows,
            "wildcard": wildcard,
            "current": current_raw,
        }

    def _build_user_roles_widget(self, item):
        """Return a synthetic m2m-like widget for User.roles."""
        Role = MODELS_REGISTRY.get("Role")
        if not Role:
            return None
        try:
            all_roles = Role.all(limit=500)
        except Exception:
            all_roles = []
        selected_ids = set()
        if item and item.id:
            try:
                selected_ids = {r.id for r in item.roles}
            except Exception:
                selected_ids = set()
        options = [
            {
                "id": r.id,
                "label": _display(r),
                "selected": r.id in selected_ids,
            }
            for r in all_roles
        ]
        return {"name": "roles", "label": "Roles", "options": options}

    def _edit_form(self, request, entry, item, form=None, meta=None, errors=None):
        name = entry["label"][:-1] if entry["label"].endswith("s") else entry["label"]
        if item:
            title = self.t(request, "Edit {name}", name=self.t(request, name))
        else:
            title = self.t(request, "New {name}", name=self.t(request, name))

        breadcrumbs = [
            {"label": self.t(request, "Dashboard"), "url": self.prefix},
            {
                "label": self.t(request, entry["label"]),
                "url": self.prefix + "/" + entry["slug"],
            },
            {"label": _display(item) if item else self.t(request, "New"), "url": None},
        ]
        is_role = entry["model"].__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = entry["model"].__name__ == auth_name
        editing_self = (
            is_user and self._is_self(request, entry, item) if item else False
        )

        if form is None:
            form, meta = self._build_form(request, entry, item, errors)
        elif errors:
            for fname, err in errors.items():
                if fname in form._fields:
                    form._fields[fname]._error = err

        # Strip 'permissions' from Role form — it's replaced by the matrix
        if is_role and "permissions" in form._fields:
            form._fields.pop("permissions", None)
            if meta and "permissions" in meta:
                meta.pop("permissions", None)

        # Strip is_admin from self-edit (prevent self-demotion)
        if editing_self and "is_admin" in form._fields:
            form._fields.pop("is_admin", None)
            if meta and "is_admin" in meta:
                meta.pop("is_admin", None)

        groups = self._grouped_fields(entry, form, meta)

        # Build m2m list; for User, inject roles widget unless editing self
        m2m = self._build_m2m(entry["model"], item)
        if is_user and not editing_self:
            w = self._build_user_roles_widget(item)
            if w:
                m2m = [w] + [m for m in m2m if m["name"] != "roles"]

        permission_matrix = (
            self._build_permission_matrix(request, item) if is_role else None
        )

        return self._render(
            request,
            "edit.html",
            item=item or type("E", (), {"id": None})(),
            form=form,
            field_groups=groups,
            m2m_fields=m2m,
            permission_matrix=permission_matrix,
            inlines=self._build_inlines(entry, item),
            slug=entry["slug"],
            title=title,
            can_delete=entry["can_delete"] and not editing_self,
            can_add=entry["can_add"],
            errors_global=(errors or {}).get("_"),
            active=entry["slug"],
            breadcrumbs=breadcrumbs,
            editing_self=editing_self,
        )

    def _apply_form(self, request, entry, item, form):
        """Copy validated form data + file uploads onto the item.

        Returns True on success, False if there was a save error (which is
        attached to form._fields[name]._error).
        """
        model = entry["model"]
        readonly = set(entry["readonly_fields"])
        form_exclude = set(entry["form_exclude"])
        is_role = model.__name__ == "Role"
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = model.__name__ == auth_name
        editing_self = is_user and self._is_self(request, entry, item)
        for name, field in model._fields.items():
            # Handle password fields BEFORE the protected check (passwords are protected but need special handling)
            if getattr(field, "is_password", False):
                val = request.form.get(name)
                if val:
                    setattr(item, name, val)
                continue

            if (
                name in readonly
                or name in form_exclude
                or getattr(field, "protected", False)
            ):
                continue
            if getattr(field, "is_timestamp", False):
                continue
            if getattr(field, "is_soft_delete", False):
                continue
            # Role: permissions handled separately from the matrix POST
            if is_role and name == "permissions":
                item.permissions = (request.form.get("permissions", "") or "").strip()
                continue
            # Self-edit: never allow changing is_admin on yourself
            if editing_self and name == "is_admin":
                continue
            if getattr(field, "is_file", False):
                upload = request.files.get(name)
                if upload and upload.filename:
                    upload.save(os.path.join(field.upload_to or "", upload.filename))
                    setattr(item, name, upload.filename)
                continue
            raw = form._fields[name].value if name in form._fields else None
            if field.sql_type == "INTEGER":
                if name.startswith("is_") or name.startswith("has_"):
                    # Checkbox: raw is "0" or "1" (string), not boolean
                    setattr(item, name, 1 if raw == "1" else 0)
                elif raw in (None, ""):
                    setattr(item, name, None)
                else:
                    try:
                        setattr(item, name, int(raw))
                    except (ValueError, TypeError):
                        setattr(item, name, None)
            elif field.sql_type == "REAL":
                try:
                    setattr(item, name, float(raw) if raw else None)
                except (ValueError, TypeError):
                    setattr(item, name, None)
            elif getattr(field, "is_boolean", False):
                # Boolean checkbox: raw is "0" or "1" (string)
                setattr(item, name, 1 if raw == "1" else 0)
            else:
                setattr(item, name, raw or None)
        return True

    def _sync_m2m(self, request, model, item):
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        is_user = model.__name__ == auth_name
        editing_self = (
            is_user
            and request.user
            and getattr(request.user, "id", None) == getattr(item, "id", None)
        )
        for name, rel in getattr(model, "_relations", {}).items():
            if rel.type != "BelongsToMany":
                continue
            # Never let a user change their own role assignments
            if editing_self and name == "roles":
                continue
            ids_raw = request.form.get(f"m2m_{name}", "")
            ids = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
            try:
                item.sync(name, ids)
            except Exception:
                pass

    def _create(self, request, entry):

        try:
            item = entry["model"]()
            form, meta = self._build_form(request, entry, item)

            if not form.validate():
                return self._edit_form(request, entry, item, form=form, meta=meta)

            self._apply_form(request, entry, item, form)

            try:
                item.save()
            except ModelError as e:
                errors = {e.field: str(e)} if e.field else {"_": str(e)}
                return self._edit_form(
                    request, entry, item, form=form, meta=meta, errors=errors
                )

            self._sync_m2m(request, entry["model"], item)
            self._log(request, entry, "created", item.id)

            request.flash(
                "success", self.t(request, "{label} created", label=entry["label"][:-1])
            )
            if request.form.get("_save_add"):
                raise RedirectException(self.prefix + "/" + entry["slug"] + "/new")
            if request.form.get("_save_continue"):
                raise RedirectException(
                    self.prefix + "/" + entry["slug"] + "/" + str(item.id)
                )
            raise RedirectException(self.prefix + "/" + entry["slug"])
        except RedirectException:
            raise
        except Exception as e:
            return self._edit_form(
                request,
                entry,
                entry["model"](),
                errors={"_": f"Server crash: {str(e)}"},
            )

    def _update(self, request, entry, item):
        try:
            before = self._snapshot(item)
            form, meta = self._build_form(request, entry, item)
            if not form.validate():
                return self._edit_form(request, entry, item, form=form, meta=meta)
            self._apply_form(request, entry, item, form)
            try:
                item.save()
            except ModelError as e:
                errors = {e.field: str(e)} if e.field else {"_": str(e)}
                return self._edit_form(
                    request, entry, item, form=form, meta=meta, errors=errors
                )
            self._sync_m2m(request, entry["model"], item)
            diff = self._diff(before, self._snapshot(item))
            if diff:
                self._log(
                    request,
                    "update",
                    entry["model"].__name__,
                    entity_id=item.id,
                    changes=diff,
                )
            request.flash(
                "success", self.t(request, "{label} updated", label=entry["label"][:-1])
            )
            if request.form.get("_save_add"):
                raise RedirectException(self.prefix + "/" + entry["slug"] + "/new")
            if request.form.get("_save_continue"):
                raise RedirectException(
                    self.prefix + "/" + entry["slug"] + "/" + str(item.id)
                )
            raise RedirectException(self.prefix + "/" + entry["slug"])
        except RedirectException:
            raise
        except Exception as e:
            import traceback

            traceback.print_exc()
            return self._edit_form(
                request, entry, item, errors={"_": f"Server crash: {str(e)}"}
            )

    # ── Impersonation ────────────────────────────────────────

    def _impersonate(self, request, target_id):
        # Security: only super-admins (is_admin=True) can start impersonation
        # We check the original user from the session if already impersonating
        orig_id = request.session.get("impersonator_id") or request.user.id
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)

        # Verify permissions of the ACTUAL user performing the action
        admin_user = User.find(id=orig_id)
        if not admin_user or not getattr(admin_user, "is_admin", False):
            return self._forbid(request, "Only admins can impersonate")

        target = User.find(id=target_id)
        if not target:
            request.flash("error", self.t(request, "Target user not found"))
            raise RedirectException(self.prefix + "/users")

        if target.id == admin_user.id:
            request.flash("info", self.t(request, "You are already yourself"))
            raise RedirectException(self.prefix + "/users")

        # Save the real admin ID in session
        request.session["impersonator_id"] = admin_user.id
        request.session["impersonate_started_at"] = time.time()
        request.session["user_id"] = target.id

        self._log(request, "impersonate_start", auth_name, entity_id=target.id)
        request.flash(
            "success",
            self.t(request, "Now acting as {name}", name=target.name or target.email),
        )
        raise RedirectException(self.prefix)

    def _stop_impersonate(self, request):
        impersonator_id = request.session.get("impersonator_id")
        if not impersonator_id:
            raise RedirectException(self.prefix)

        request.session["user_id"] = impersonator_id
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonate_started_at", None)

        request.flash("info", self.t(request, "Stopped impersonation"))
        raise RedirectException(self.prefix)

    # ── Media Manager ────────────────────────────────────────

    def _media_manager(self, request):
        self._require_admin(request)
        upload_dir = os.path.join(self.app.root_dir, "src/partials/uploads")
        os.makedirs(upload_dir, exist_ok=True)

        files = []
        for root, dirs, filenames in os.walk(upload_dir):
            for f in filenames:
                if f.startswith("."):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, upload_dir)
                stat = os.stat(full_path)

                # Check if it's an image
                is_img = f.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
                )

                files.append(
                    {
                        "name": f,
                        "rel_path": rel_path,
                        "url": f"/uploads/{rel_path}",
                        "size": round(stat.st_size / 1024, 1),  # KB
                        "mtime": datetime.datetime.fromtimestamp(
                            stat.st_mtime
                        ).strftime("%Y-%m-%d %H:%M"),
                        "is_image": is_img,
                    }
                )

        # Sort by most recent
        files.sort(key=lambda x: x["mtime"], reverse=True)

        return self._render(
            request,
            "media.html",
            files=files,
            active="media",
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "Media Manager", "url": None},
            ],
        )

    def _delete_media(self, request, rel_path):
        self._require_admin(request)
        # Security: prevent path traversal
        rel_path = rel_path.lstrip("/")
        if ".." in rel_path:
            return self._forbid(request)

        base_dir = os.path.abspath(
            os.path.join(self.app.root_dir, "src/partials/uploads")
        )
        full_path = os.path.abspath(os.path.join(base_dir, rel_path))
        try:
            common = os.path.commonpath([full_path, base_dir])
        except ValueError:
            common = ""
        if common != base_dir:
            return self._forbid(request)

        # Reject symlinks to avoid deleting files outside the media directory
        if os.path.islink(full_path):
            return self._forbid(request)

        if os.path.isfile(full_path):
            os.remove(full_path)
            request.flash("success", self.t(request, "File deleted"))
        else:
            request.flash("error", self.t(request, "File not found"))

        raise RedirectException(self.prefix + "/media")

    def _media_upload(self, request):
        self._require_admin(request)
        if request.method != "POST":
            raise RedirectException(self.prefix + "/media")

        if not request.files:
            request.flash("error", self.t(request, "No files selected"))
            raise RedirectException(self.prefix + "/media")

        count = 0
        for file in request.all_files:
            ext = os.path.splitext(file.filename)[1].lower()

            # Sorting logic based on user requirements
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
                subdir = "images"
            elif ext == ".pdf":
                subdir = "pdfs"
            else:
                subdir = "others"

            # UploadedFile.save(path) prepends src/partials/uploads in Asok
            rel_dest = os.path.join(subdir, file.filename)
            file.save(rel_dest)
            count += 1

        request.flash(
            "success",
            self.t(request, "Successfully uploaded {count} file(s)", count=count),
        )
        raise RedirectException(self.prefix + "/media")
