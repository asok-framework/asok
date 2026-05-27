from __future__ import annotations

from typing import Optional


class Relation:
    """Definition of a relationship between two models."""

    def __init__(
        self,
        type: str,
        target_model_name: str,
        foreign_key: Optional[str] = None,
        owner_key: str = "id",
        pivot_table: Optional[str] = None,
        pivot_fk: Optional[str] = None,
        pivot_other_fk: Optional[str] = None,
    ):
        self.type: str = type
        self.target_model_name: str = target_model_name
        self.foreign_key: Optional[str] = foreign_key
        self.owner_key: str = owner_key
        self.pivot_table: Optional[str] = pivot_table
        self.pivot_fk: Optional[str] = pivot_fk
        self.pivot_other_fk: Optional[str] = pivot_other_fk

    @staticmethod
    def HasMany(target_model_name: str, foreign_key: Optional[str] = None) -> Relation:
        """One-to-many relationship."""
        return Relation("HasMany", target_model_name, foreign_key)

    @staticmethod
    def HasOne(target_model_name: str, foreign_key: Optional[str] = None) -> Relation:
        """One-to-one relationship."""
        return Relation("HasOne", target_model_name, foreign_key)

    @staticmethod
    def BelongsTo(
        target_model_name: str, foreign_key: Optional[str] = None
    ) -> Relation:
        """Inverse of HasMany/HasOne relationship."""
        return Relation("BelongsTo", target_model_name, foreign_key)

    @staticmethod
    def BelongsToMany(
        target_model_name: str,
        pivot_table: Optional[str] = None,
        pivot_fk: Optional[str] = None,
        pivot_other_fk: Optional[str] = None,
    ) -> Relation:
        """Many-to-many relationship using a pivot table."""
        return Relation(
            "BelongsToMany",
            target_model_name,
            pivot_table=pivot_table,
            pivot_fk=pivot_fk,
            pivot_other_fk=pivot_other_fk,
        )

    @staticmethod
    def MorphTo(
        id_column: Optional[str] = None, type_column: Optional[str] = None
    ) -> Relation:
        """Polymorphic belongs-to-like relationship."""
        return Relation("MorphTo", "", id_column, type_column)

    @staticmethod
    def MorphMany(
        target_model_name: str, relation_name: str
    ) -> Relation:
        """Polymorphic has-many-like relationship."""
        return Relation("MorphMany", target_model_name, relation_name)

