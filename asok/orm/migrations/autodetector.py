from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from .operations import (
    AddField,
    AlterField,
    BaseOperation,
    CreateModel,
    DeleteModel,
    RemoveField,
    RenameField,
)
from .state import ProjectState


class MigrationAutodetector:
    """Detects differences between two ProjectState instances and generates a list of operations."""

    def __init__(self, historical_state: ProjectState, current_state: ProjectState):
        self.historical_state = historical_state
        self.current_state = current_state

    def changes(self) -> List[BaseOperation]:
        """Compare historical and current states to generate a sequence of operations."""
        ops: List[BaseOperation] = []
        self._detect_model_deletions(ops)
        self._detect_model_additions(ops)
        self._detect_model_modifications(ops)
        return ops

    def _find_historical_model(self, name: str) -> Optional[str]:
        if name in self.historical_state.models:
            return name
        curr_model = self.current_state.models.get(name)
        curr_table = curr_model.table if curr_model else None
        return self._search_historical_models(name.lower(), curr_table)

    def _search_historical_models(self, name_lower: str, curr_table: Optional[str]) -> Optional[str]:
        for hist_name, hist_model in self.historical_state.models.items():
            if hist_name.lower() == name_lower or (curr_table and hist_model.table == curr_table):
                return hist_name
        return None

    def _detect_model_deletions(self, ops: List[BaseOperation]) -> None:
        curr_matched_hist_names = set()
        for curr_name in self.current_state.models.keys():
            matched_hist = self._find_historical_model(curr_name)
            if matched_hist:
                curr_matched_hist_names.add(matched_hist)

        for name in sorted(self.historical_state.models.keys()):
            if name not in curr_matched_hist_names:
                hist_model = self.historical_state.models[name]
                ops.append(
                    DeleteModel(
                        name=name,
                        table=hist_model.table,
                        fields=hist_model.fields,
                        relations=hist_model.relations,
                        search_fields=hist_model.search_fields,
                    )
                )

    def _detect_model_additions(self, ops: List[BaseOperation]) -> None:
        for name in sorted(self.current_state.models.keys()):
            if not self._find_historical_model(name):
                curr_model = self.current_state.models[name]
                ops.append(
                    CreateModel(
                        name=name,
                        table=curr_model.table,
                        fields=curr_model.fields,
                        relations=curr_model.relations,
                        search_fields=curr_model.search_fields,
                    )
                )

    def _detect_model_modifications(self, ops: List[BaseOperation]) -> None:
        for name in sorted(self.current_state.models.keys()):
            hist_name = self._find_historical_model(name)
            if hist_name:
                self._detect_field_changes(name, hist_name, ops)

    def _detect_field_changes(
        self, curr_model_name: str, hist_model_name: str, ops: List[BaseOperation]
    ) -> None:
        model_name = curr_model_name
        hist_model = self.historical_state.models[hist_model_name]
        curr_model = self.current_state.models[curr_model_name]

        hist_fields = set(hist_model.fields.keys())
        curr_fields = set(curr_model.fields.keys())

        removed_fields = hist_fields - curr_fields
        added_fields = curr_fields - hist_fields

        self._handle_interactive_renaming(model_name, removed_fields, added_fields, ops, curr_model.table)

        # Generate AddField operations
        for f_name in sorted(added_fields):
            ops.append(AddField(model_name, f_name, curr_model.fields[f_name], table=curr_model.table))

        # Generate RemoveField operations
        for f_name in sorted(removed_fields):
            ops.append(RemoveField(model_name, f_name, hist_model.fields[f_name], table=hist_model.table))

        # Generate AlterField operations
        common_fields = hist_fields & curr_fields
        self._detect_altered_fields(
            model_name, hist_model, curr_model, common_fields, ops
        )

    def _handle_interactive_renaming(
        self,
        model_name: str,
        removed_fields: set[str],
        added_fields: set[str],
        ops: List[BaseOperation],
        table: Optional[str] = None,
    ) -> None:
        if len(removed_fields) == 1 and len(added_fields) == 1:
            old_name = list(removed_fields)[0]
            new_name = list(added_fields)[0]
            if self._ask_rename_confirmation(model_name, old_name, new_name):
                ops.append(RenameField(model_name, old_name, new_name, table=table))
                removed_fields.clear()
                added_fields.clear()

    def _ask_rename_confirmation(
        self, model_name: str, old_name: str, new_name: str
    ) -> bool:
        import os

        env_auto = os.environ.get("ASOK_AUTO_RENAME", "").strip().lower()
        if env_auto in ("1", "true", "yes"):
            return True
        if env_auto in ("0", "false", "no"):
            return False

        if not sys.stdout.isatty():
            return False
        try:
            ans = (
                input(
                    f"Did you rename field '{old_name}' to '{new_name}' in model '{model_name}'? [y/N]: "
                )
                .strip()
                .lower()
            )
            return ans in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            return False

    def _detect_altered_fields(
        self,
        model_name: str,
        hist_model: Any,
        curr_model: Any,
        common_fields: set[str],
        ops: List[BaseOperation],
    ) -> None:
        for f_name in sorted(common_fields):
            h_f = hist_model.fields[f_name]
            c_f = curr_model.fields[f_name]
            if self._field_changed(h_f, c_f):
                ops.append(AlterField(model_name, f_name, h_f, c_f, table=curr_model.table))

    def _field_changed(self, f1: Dict[str, Any], f2: Dict[str, Any]) -> bool:
        keys_to_compare = (
            "type",
            "sql_type",
            "nullable",
            "default",
            "unique",
            "max_length",
            "precision",
            "is_boolean",
            "is_json",
            "is_uuid",
            "is_datetime",
            "is_date",
            "is_time",
            "is_vector",
            "dimensions",
        )
        for key in keys_to_compare:
            if f1.get(key) != f2.get(key):
                return True
        return False
