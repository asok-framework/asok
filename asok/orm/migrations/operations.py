from __future__ import annotations

from typing import Any, Dict, List, Optional


class BaseOperation:
    """Base class for all migration operations."""

    def state_forwards(self, state: Any) -> None:
        """Mutate the ProjectState to reflect this change in virtual memory."""
        raise NotImplementedError()

    def database_forwards(self, schema_editor: Any) -> None:
        """Apply this operation's schema modification to the database."""
        raise NotImplementedError()

    def database_backwards(self, schema_editor: Any) -> None:
        """Roll back this operation's schema modification from the database."""
        raise NotImplementedError()

    def deconstruct(self) -> str:
        """Return a string to reconstruct this operation class in python code."""
        raise NotImplementedError()


class CreateModel(BaseOperation):
    """Operation to create a new database table representation."""

    def __init__(
        self,
        name: str,
        fields: Dict[str, Dict[str, Any]],
        table: str,
        relations: Optional[Dict[str, Dict[str, Any]]] = None,
        search_fields: Optional[List[str]] = None,
    ):
        self.name = name
        self.fields = fields
        self.table = table
        self.relations = relations or {}
        self.search_fields = search_fields or []

    def state_forwards(self, state: Any) -> None:
        from .state import VirtualModelState
        state.models[self.name] = VirtualModelState(
            name=self.name,
            table=self.table,
            fields=self.fields,
            relations=self.relations,
            search_fields=self.search_fields,
        )

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.create_table(self)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.delete_table_by_name(self.table)

    def deconstruct(self) -> str:
        return (
            f"operations.CreateModel(\n"
            f"        name={repr(self.name)},\n"
            f"        table={repr(self.table)},\n"
            f"        fields={repr(self.fields)},\n"
            f"        relations={repr(self.relations)},\n"
            f"        search_fields={repr(self.search_fields)},\n"
            f"    )"
        )


class DeleteModel(BaseOperation):
    """Operation to delete an existing database table representation."""

    def __init__(
        self,
        name: str,
        table: str,
        fields: Optional[Dict[str, Dict[str, Any]]] = None,
        relations: Optional[Dict[str, Dict[str, Any]]] = None,
        search_fields: Optional[List[str]] = None,
    ):
        self.name = name
        self.table = table
        self.fields = fields or {}
        self.relations = relations or {}
        self.search_fields = search_fields or []

    def state_forwards(self, state: Any) -> None:
        state.models.pop(self.name, None)

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.delete_table_by_name(self.table)

    def database_backwards(self, schema_editor: Any) -> None:
        # Recreate model table in database backwards
        dummy_create = CreateModel(
            name=self.name,
            fields=self.fields,
            table=self.table,
            relations=self.relations,
            search_fields=self.search_fields,
        )
        schema_editor.create_table(dummy_create)

    def deconstruct(self) -> str:
        return (
            f"operations.DeleteModel(\n"
            f"        name={repr(self.name)},\n"
            f"        table={repr(self.table)},\n"
            f"        fields={repr(self.fields)},\n"
            f"        relations={repr(self.relations)},\n"
            f"        search_fields={repr(self.search_fields)},\n"
            f"    )"
        )


class AddField(BaseOperation):
    """Operation to add a new column to a table."""

    def __init__(self, model_name: str, name: str, field: Dict[str, Any]):
        self.model_name = model_name
        self.name = name
        self.field = field

    def state_forwards(self, state: Any) -> None:
        model = state.models[self.model_name]
        model.fields[self.name] = self.field

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.add_column(self.model_name, self.name, self.field)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.remove_column(self.model_name, self.name)

    def deconstruct(self) -> str:
        return (
            f"operations.AddField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        name={repr(self.name)},\n"
            f"        field={repr(self.field)},\n"
            f"    )"
        )


class RemoveField(BaseOperation):
    """Operation to drop a column from a table."""

    def __init__(self, model_name: str, name: str, field: Optional[Dict[str, Any]] = None):
        self.model_name = model_name
        self.name = name
        self.field = field or {}

    def state_forwards(self, state: Any) -> None:
        model = state.models[self.model_name]
        model.fields.pop(self.name, None)

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.remove_column(self.model_name, self.name)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.add_column(self.model_name, self.name, self.field)

    def deconstruct(self) -> str:
        return (
            f"operations.RemoveField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        name={repr(self.name)},\n"
            f"        field={repr(self.field)},\n"
            f"    )"
        )


class AlterField(BaseOperation):
    """Operation to modify type or constraints on a column."""

    def __init__(
        self,
        model_name: str,
        name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
    ):
        self.model_name = model_name
        self.name = name
        self.old_field = old_field
        self.new_field = new_field

    def state_forwards(self, state: Any) -> None:
        model = state.models[self.model_name]
        model.fields[self.name] = self.new_field

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.alter_column(self.model_name, self.name, self.old_field, self.new_field)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.alter_column(self.model_name, self.name, self.new_field, self.old_field)

    def deconstruct(self) -> str:
        return (
            f"operations.AlterField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        name={repr(self.name)},\n"
            f"        old_field={repr(self.old_field)},\n"
            f"        new_field={repr(self.new_field)},\n"
            f"    )"
        )


class RenameField(BaseOperation):
    """Operation to rename an existing column in a table."""

    def __init__(self, model_name: str, old_name: str, new_name: str):
        self.model_name = model_name
        self.old_name = old_name
        self.new_name = new_name

    def state_forwards(self, state: Any) -> None:
        model = state.models[self.model_name]
        if self.old_name in model.fields:
            field_data = model.fields.pop(self.old_name)
            model.fields[self.new_name] = field_data

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.rename_column(self.model_name, self.old_name, self.new_name)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.rename_column(self.model_name, self.new_name, self.old_name)

    def deconstruct(self) -> str:
        return (
            f"operations.RenameField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        old_name={repr(self.old_name)},\n"
            f"        new_name={repr(self.new_name)},\n"
            f"    )"
        )
