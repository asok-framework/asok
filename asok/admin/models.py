from __future__ import annotations


class ModelAdmin:
    """Base class for inner Admin configuration in Models to provide autocompletion.

    Example:
        class Contact(Model):
            class Admin(ModelAdmin):
                list_display = ["id", "name"]
    """

    label: str = None
    slug: str = None
    group: str = None
    hidden: bool = False
    list_display: list[str] = None
    search_fields: list[str] = None
    list_filter: list[str] = None
    readonly_fields: list[str] = None
    form_exclude: list[str] = None
    fieldsets: list[tuple[str, list[str]]] = None
    per_page: int = 20
    inlines: list[str] = None
    can_add: bool = None
    can_edit: bool = None
    can_delete: bool = None
    actions: list[str] = None
    vector_search_field: str = None
    ordering: list[str] | str = None
    icon: str = None
    export_formats: list[str] = None
    show_in_dashboard: bool = None
    show_in_overview: bool = None


_DEFAULT_USER_MODEL_SRC = """\
from asok import Field, Model, ModelAdmin


class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password(
        rules="required|password_strength",
        messages={"password_strength": "Password must be 8+ characters with uppercase, number, and special char."}
    )
    name = Field.String()
    is_admin = Field.Boolean(default=False, protected=True)
    totp_secret = Field.String(nullable=True, hidden=True, protected=True)
    totp_enabled = Field.Boolean(default=False, protected=True)
    backup_codes = Field.String(nullable=True, hidden=True, protected=True)  # JSON array of hashed codes
    created_at = Field.CreatedAt()

    class Admin(ModelAdmin):
        show_in_overview = False
"""

_DEFAULT_ROLE_MODEL_SRC = """\
from asok import Field, Model, ModelAdmin


class Role(Model):
    name = Field.String(unique=True, nullable=False)
    label = Field.String()
    permissions = Field.String(default="", protected=True)
    created_at = Field.CreatedAt()

    class Admin(ModelAdmin):
        show_in_overview = False

    def __str__(self):
        return self.label or self.name
"""

_DEFAULT_LOG_MODEL_SRC = """\
from asok import Field, Model, ModelAdmin


class AdminLog(Model):
    __tablename__ = "admin_logs"
    user_id = Field.Integer(nullable=True)
    action = Field.String(nullable=False)
    entity = Field.String(nullable=False)
    entity_id = Field.Integer(nullable=True)
    changes = Field.String()
    created_at = Field.CreatedAt()

    class Admin(ModelAdmin):
        label = "Logs"
        slug = "logs"
        group = "System & Audit"
        list_display = ["id", "created_at", "user_id", "action", "entity", "entity_id"]
        list_filter = ["action", "entity"]
        search_fields = ["action", "entity", "changes"]
        show_in_overview = False
        per_page = 50
        can_add = False
        can_edit = False
        can_delete = False
"""

_DEFAULT_TRASH_MODEL_SRC = """\
from asok import Field, Model, ModelAdmin


class AdminTrash(Model):
    __tablename__ = "asok_trash"
    model_name = Field.String(nullable=False)
    model_slug = Field.String(nullable=False)
    object_id = Field.Integer(nullable=True)
    object_repr = Field.String()
    data = Field.Text()
    deleted_by_id = Field.Integer(nullable=True)
    deleted_at = Field.CreatedAt()

    class Admin(ModelAdmin):
        hidden = True
        show_in_overview = False
"""


_DEFAULT_SESSION_MODEL_SRC = """\
from asok import Field, Model, ModelAdmin


class AdminSession(Model):
    __tablename__ = "asok_sessions"
    sid = Field.String(unique=True, nullable=False)
    user_id = Field.Integer(nullable=True)
    ip = Field.String()
    user_agent = Field.String()
    device_type = Field.String()
    browser = Field.String()
    os_name = Field.String()
    location = Field.String()
    is_active = Field.Boolean(default=True)
    last_seen = Field.Integer()
    created_at = Field.CreatedAt()
    revoked_at = Field.Integer(nullable=True)

    class Admin(ModelAdmin):
        label = "Sessions"
        slug = "sessions"
        group = "System & Audit"
        list_display = ["id", "created_at", "user_id", "ip", "device_type", "browser", "is_active", "last_seen"]
        list_filter = ["is_active", "device_type", "browser"]
        search_fields = ["sid", "ip", "user_agent", "browser", "os_name"]
        show_in_overview = False
        per_page = 50
        can_add = False
        can_edit = False
        can_delete = False
"""
