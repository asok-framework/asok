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
