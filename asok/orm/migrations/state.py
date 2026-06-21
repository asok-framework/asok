from __future__ import annotations

import copy
from typing import Any, Dict, List


class VirtualModelState:
    """Represents the schema structure of a Model at a specific migration version."""

    def __init__(
        self,
        name: str,
        table: str,
        fields: Dict[str, Dict[str, Any]],
        relations: Dict[str, Dict[str, Any]] = None,
        search_fields: List[str] = None,
    ):
        self.name = name
        self.table = table
        self.fields = fields
        self.relations = relations or {}
        self.search_fields = search_fields or []

    @classmethod
    def from_model_class(cls, model_cls: Any) -> VirtualModelState:
        """Create a VirtualModelState from a live Python Model class."""
        fields = {}
        for f_name, f_obj in model_cls._fields.items():
            fields[f_name] = {
                "type": f_obj.__class__.__name__,
                "sql_type": getattr(f_obj, "sql_type", "TEXT"),
                "nullable": getattr(f_obj, "nullable", True),
                "default": getattr(f_obj, "default", None),
                "unique": getattr(f_obj, "unique", False),
                "max_length": getattr(f_obj, "max_length", None),
                "precision": getattr(f_obj, "precision", None),
                "is_boolean": getattr(f_obj, "is_boolean", False),
                "is_json": getattr(f_obj, "is_json", False),
                "is_uuid": getattr(f_obj, "is_uuid", False),
                "is_datetime": getattr(f_obj, "is_datetime", False),
                "is_date": getattr(f_obj, "is_date", False),
                "is_time": getattr(f_obj, "is_time", False),
                "is_vector": getattr(f_obj, "is_vector", False),
                "dimensions": getattr(f_obj, "dimensions", None),
            }

        relations = {}
        if hasattr(model_cls, "_relations"):
            for r_name, r_obj in model_cls._relations.items():
                relations[r_name] = {
                    "type": getattr(r_obj, "type", "BelongsTo"),
                    "target_model_name": getattr(r_obj, "target_model_name", ""),
                    "pivot_table": getattr(r_obj, "pivot_table", None),
                    "pivot_fk": getattr(r_obj, "pivot_fk", None),
                    "pivot_other_fk": getattr(r_obj, "pivot_other_fk", None),
                }

        return cls(
            name=model_cls.__name__,
            table=model_cls._table,
            fields=fields,
            relations=relations,
            search_fields=list(getattr(model_cls, "_search_fields", [])),
        )

    def clone(self) -> VirtualModelState:
        return VirtualModelState(
            name=self.name,
            table=self.table,
            fields=copy.deepcopy(self.fields),
            relations=copy.deepcopy(self.relations),
            search_fields=list(self.search_fields),
        )


class ProjectState:
    """Represents the complete virtual schema of all models in the project."""

    def __init__(self, models: Dict[str, VirtualModelState] = None):
        self.models = models or {}

    @classmethod
    def from_codebase(cls, registry: Dict[str, Any]) -> ProjectState:
        """Create a ProjectState from the live models registered in the codebase."""
        models = {}
        for name, model_cls in registry.items():
            models[name] = VirtualModelState.from_model_class(model_cls)
        return cls(models)

    def clone(self) -> ProjectState:
        models = {name: model.clone() for name, model in self.models.items()}
        return ProjectState(models)
