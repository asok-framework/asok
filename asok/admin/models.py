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


_DEFAULT_USER_MODEL_SRC = """\
from asok import Field, Model


class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password(
        rules="required|password_strength",
        messages={"password_strength": "Password must be 8+ characters with uppercase, number, and special char."}
    )
    name = Field.String()
    is_admin = Field.Boolean(default=False)
    totp_secret = Field.String(nullable=True, hidden=True)
    totp_enabled = Field.Boolean(default=False)
    backup_codes = Field.String(nullable=True, hidden=True)  # JSON array of hashed codes
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
