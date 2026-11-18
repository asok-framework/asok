import enum
from typing import Any, Dict, List, Optional


def _safe_repr(obj: Any) -> str:
    def _clean(item: Any) -> Any:
        if isinstance(item, enum.Enum):
            return item.value
        if hasattr(item, "value") and not isinstance(item, (type, type(None))):
            return item.value
        if isinstance(item, dict):
            return {k: _clean(v) for k, v in item.items()}
        if isinstance(item, list):
            return [_clean(v) for v in item]
        if isinstance(item, tuple):
            return tuple(_clean(v) for v in item)
        return item

    return repr(_clean(obj))


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
        db_path: Optional[str] = None,
    ):
        self.name = name
        self.fields = fields
        self.table = table
        self.relations = relations or {}
        self.search_fields = search_fields or []
        self.db_path = db_path

    def state_forwards(self, state: Any) -> None:
        from .state import VirtualModelState

        state.models[self.name] = VirtualModelState(
            name=self.name,
            table=self.table,
            fields=self.fields,
            relations=self.relations,
            search_fields=self.search_fields,
            db_path=self.db_path,
        )

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.create_table(self)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.delete_table_by_name(self.table)

    def deconstruct(self) -> str:
        db_path_part = (
            f",\n        db_path={repr(self.db_path)}" if self.db_path is not None else ""
        )
        return (
            f"operations.CreateModel(\n"
            f"        name={repr(self.name)},\n"
            f"        table={repr(self.table)},\n"
            f"        fields={_safe_repr(self.fields)},\n"
            f"        relations={repr(self.relations)},\n"
            f"        search_fields={repr(self.search_fields)}{db_path_part},\n"
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
        db_path: Optional[str] = None,
    ):
        self.name = name
        self.table = table
        self.fields = fields or {}
        self.relations = relations or {}
        self.search_fields = search_fields or []
        self.db_path = db_path

    def state_forwards(self, state: Any) -> None:
        model = state.get_model(self.name) if hasattr(state, "get_model") else None
        if model is not None:
            state.models.pop(model.name, None)
        else:
            state.models.pop(self.name, None)

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.delete_table_by_name(self.table)

    def database_backwards(self, schema_editor: Any) -> None:
        dummy_create = CreateModel(
            name=self.name,
            fields=self.fields,
            table=self.table,
            relations=self.relations,
            search_fields=self.search_fields,
            db_path=self.db_path,
        )
        schema_editor.create_table(dummy_create)

    def deconstruct(self) -> str:
        db_path_part = (
            f",\n        db_path={repr(self.db_path)}" if self.db_path is not None else ""
        )
        return (
            f"operations.DeleteModel(\n"
            f"        name={repr(self.name)},\n"
            f"        table={repr(self.table)},\n"
            f"        fields={_safe_repr(self.fields)},\n"
            f"        relations={repr(self.relations)},\n"
            f"        search_fields={repr(self.search_fields)}{db_path_part},\n"
            f"    )"
        )


class AddField(BaseOperation):
    """Operation to add a new column to a table."""

    def __init__(
        self,
        model_name: str,
        name: str,
        field: Dict[str, Any],
        table: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.model_name = model_name
        self.name = name
        self.field = field
        self.table = table
        self.db_path = db_path

    def state_forwards(self, state: Any) -> None:
        from .state import VirtualModelState

        model = (
            state.get_model(self.model_name)
            if hasattr(state, "get_model")
            else state.models.get(self.model_name)
        )
        if model is not None:
            model.fields[self.name] = self.field
        else:
            tbl = self.table or self.model_name.lower()
            state.models[self.model_name] = VirtualModelState(
                name=self.model_name,
                table=tbl,
                fields={self.name: self.field},
            )

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.add_column(
            self.model_name, self.name, self.field, table=self.table
        )

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.remove_column(self.model_name, self.name, table=self.table)

    def deconstruct(self) -> str:
        tbl_repr = f", table={repr(self.table)}" if self.table else ""
        db_path_part = (
            f", db_path={repr(self.db_path)}" if self.db_path is not None else ""
        )
        return (
            f"operations.AddField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        name={repr(self.name)},\n"
            f"        field={_safe_repr(self.field)}{tbl_repr}{db_path_part},\n"
            f"    )"
        )


class RemoveField(BaseOperation):
    """Operation to drop a column from a table."""

    def __init__(
        self,
        model_name: str,
        name: str,
        field: Optional[Dict[str, Any]] = None,
        table: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.model_name = model_name
        self.name = name
        self.field = field or {}
        self.table = table
        self.db_path = db_path

    def state_forwards(self, state: Any) -> None:
        model = (
            state.get_model(self.model_name)
            if hasattr(state, "get_model")
            else state.models.get(self.model_name)
        )
        if model is not None:
            model.fields.pop(self.name, None)

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.remove_column(self.model_name, self.name, table=self.table)

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.add_column(
            self.model_name, self.name, self.field, table=self.table
        )

    def deconstruct(self) -> str:
        tbl_repr = f", table={repr(self.table)}" if self.table else ""
        db_path_part = (
            f", db_path={repr(self.db_path)}" if self.db_path is not None else ""
        )
        return (
            f"operations.RemoveField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        name={repr(self.name)},\n"
            f"        field={_safe_repr(self.field)}{tbl_repr}{db_path_part},\n"
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
        table: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.model_name = model_name
        self.name = name
        self.old_field = old_field
        self.new_field = new_field
        self.table = table
        self.db_path = db_path

    def state_forwards(self, state: Any) -> None:
        from .state import VirtualModelState

        model = (
            state.get_model(self.model_name)
            if hasattr(state, "get_model")
            else state.models.get(self.model_name)
        )
        if model is not None:
            model.fields[self.name] = self.new_field
        else:
            tbl = self.table or self.model_name.lower()
            state.models[self.model_name] = VirtualModelState(
                name=self.model_name,
                table=tbl,
                fields={self.name: self.new_field},
            )

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.alter_column(
            self.model_name, self.name, self.old_field, self.new_field, table=self.table
        )

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.alter_column(
            self.model_name, self.name, self.new_field, self.old_field, table=self.table
        )

    def deconstruct(self) -> str:
        tbl_repr = f", table={repr(self.table)}" if self.table else ""
        db_path_part = (
            f", db_path={repr(self.db_path)}" if self.db_path is not None else ""
        )
        return (
            f"operations.AlterField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        name={repr(self.name)},\n"
            f"        old_field={_safe_repr(self.old_field)},\n"
            f"        new_field={_safe_repr(self.new_field)}{tbl_repr}{db_path_part},\n"
            f"    )"
        )


class RenameField(BaseOperation):
    """Operation to rename an existing column in a table."""

    def __init__(
        self,
        model_name: str,
        old_name: str,
        new_name: str,
        table: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.model_name = model_name
        self.old_name = old_name
        self.new_name = new_name
        self.table = table
        self.db_path = db_path

    def state_forwards(self, state: Any) -> None:
        model = (
            state.get_model(self.model_name)
            if hasattr(state, "get_model")
            else state.models.get(self.model_name)
        )
        if model is not None:
            if self.old_name in model.fields:
                field_data = model.fields.pop(self.old_name)
                model.fields[self.new_name] = field_data

    def database_forwards(self, schema_editor: Any) -> None:
        schema_editor.rename_column(
            self.model_name, self.old_name, self.new_name, table=self.table
        )

    def database_backwards(self, schema_editor: Any) -> None:
        schema_editor.rename_column(
            self.model_name, self.new_name, self.old_name, table=self.table
        )

    def deconstruct(self) -> str:
        tbl_repr = f", table={repr(self.table)}" if self.table else ""
        db_path_part = (
            f", db_path={repr(self.db_path)}" if self.db_path is not None else ""
        )
        return (
            f"operations.RenameField(\n"
            f"        model_name={repr(self.model_name)},\n"
            f"        old_name={repr(self.old_name)},\n"
            f"        new_name={repr(self.new_name)}{tbl_repr}{db_path_part},\n"
            f"    )"
        )
