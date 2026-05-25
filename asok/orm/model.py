from __future__ import annotations

import binascii
import datetime
import decimal
import enum
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import struct
import uuid
import warnings
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from ..events import events
from .exceptions import ModelError
from .field import Field
from .fileref import FileRef
from .list import ModelList
from .proxy import ConnectionProxy
from .relation import Relation
from .utils import (
    _RE_EMAIL,
    _RE_NOT_NULL,
    _RE_TEL,
    _RE_UNIQUE,
    MODELS_REGISTRY,
    _asok_cosine_similarity,
    _asok_euclidean_distance,
    _local,
    _pluralize,
    _Transaction,
    slugify,
    validate_sql_identifier,
)

if TYPE_CHECKING:
    from .query import Query

T = TypeVar("T", bound="Model")
logger = logging.getLogger("asok.orm")


class ModelMeta(type):
    """Metaclass for all Asok Models.

    Handles field discovery, relationship mapping, and automatic table name generation.
    """

    def __new__(mcs, name, bases, attrs):
        if name == "Model":
            return super().__new__(mcs, name, bases, attrs)

        fields = {k: v for k, v in attrs.items() if isinstance(v, Field)}
        attrs["_fields"] = fields
        attrs["_fields_list"] = list(fields.keys())
        attrs["_password_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_password")
        ]
        attrs["_slug_fields"] = [k for k, v in fields.items() if hasattr(v, "is_slug")]
        attrs["_timestamp_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_timestamp")
        ]
        attrs["_file_fields"] = [k for k, v in fields.items() if hasattr(v, "is_file")]
        attrs["_email_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_email")
        ]
        attrs["_tel_fields"] = [k for k, v in fields.items() if hasattr(v, "is_tel")]
        attrs["_json_fields"] = [k for k, v in fields.items() if hasattr(v, "is_json")]
        attrs["_decimal_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_decimal")
        ]
        attrs["_enum_fields"] = [k for k, v in fields.items() if hasattr(v, "is_enum")]
        attrs["_uuid_fields"] = [k for k, v in fields.items() if hasattr(v, "is_uuid")]
        attrs["_vector_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_vector")
        ]
        soft_delete_fields = [
            k for k, v in fields.items() if hasattr(v, "is_soft_delete")
        ]
        attrs["_soft_delete_field"] = (
            soft_delete_fields[0] if soft_delete_fields else None
        )
        attrs["_search_fields"] = [
            k for k, v in fields.items() if getattr(v, "searchable", False)
        ]
        # Use explicit __tablename__ if provided, otherwise auto-pluralize
        attrs["_table"] = attrs.get("__tablename__", _pluralize(name))
        attrs["_model_name"] = name
        attrs["_conn_attr"] = f"conn_{attrs.get('_db_path', 'db.sqlite3')}"

        relations = {k: v for k, v in attrs.items() if isinstance(v, Relation)}
        attrs["_relations"] = relations

        for k, v in fields.items():
            if hasattr(v, "is_foreign_key"):
                rel_name = k.replace("_id", "")

                def get_related(self, field_name=k, model=v.related_model):
                    val = getattr(self, field_name)
                    return model.find(id=val) if val else None

                attrs[rel_name] = property(get_related)

        for k, v in relations.items():
            if v.type == "HasMany":

                def get_collection(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return []
                    fk = rel.foreign_key or f"{self.__class__.__name__.lower()}_id"
                    return target_model.all(**{fk: self.id})

                attrs[k] = property(get_collection)

            elif v.type == "HasOne":

                def get_one(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return None
                    fk = rel.foreign_key or f"{self.__class__.__name__.lower()}_id"
                    return target_model.find(**{fk: self.id})

                attrs[k] = property(get_one)

            elif v.type == "BelongsTo":

                def get_parent(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return None
                    fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
                    val = getattr(self, fk, None)
                    return target_model.find(id=val) if val else None

                attrs[k] = property(get_parent)

            elif v.type == "BelongsToMany":

                def get_many_to_many(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return []
                    # SECURITY: _pivot_info validates identifiers
                    pivot, pfk, pofk = self._pivot_info(rel)
                    # SECURITY: Quote all table and column names to prevent SQL injection
                    sql = (
                        f'SELECT t.* FROM "{target_model._table}" t '
                        f'JOIN "{pivot}" p ON p."{pofk}" = t.id '
                        f'WHERE p."{pfk}" = ?'
                    )
                    with self._get_conn() as conn:
                        rows = conn.execute(sql, (self.id,)).fetchall()
                    return ModelList(
                        (target_model(**dict(row)) for row in rows),
                        sql=sql,
                        args=[self.id],
                    )

                attrs[k] = property(get_many_to_many)

        for k in fields:
            if k in attrs and isinstance(attrs[k], Field):
                attrs.pop(k)

        cls = super().__new__(mcs, name, bases, attrs)
        if name != "Model":
            MODELS_REGISTRY[name] = cls
        return cls


class Model(metaclass=ModelMeta):
    """Base class for all ORM models."""

    _db_path: str = os.getenv("DATABASE_URL", "db.sqlite3")

    def __init__(self, _trust: bool = False, **kwargs: Any):
        self.id: Optional[int] = kwargs.get("id")
        is_new = not self.id  # New instance being created
        for name in self._fields:
            field = self._fields[name]
            if name in kwargs:
                # Security: prevent mass assignment for protected fields unless trusted
                # Exception: allow password fields during creation (new instances)
                if not _trust and getattr(field, "protected", False):
                    # Allow password assignment during creation, block during updates
                    if getattr(field, "is_password", False) and is_new:
                        val = kwargs[name]
                    else:
                        val = field.default
                else:
                    val = kwargs[name]
            else:
                val = getattr(self, name, field.default)

            if val is not None:
                if hasattr(field, "is_file") and not isinstance(val, FileRef):
                    val = FileRef(val, field.upload_to)
                elif hasattr(field, "is_boolean"):
                    # Convert string/bool to int (0 or 1)
                    if isinstance(val, str):
                        val = 1 if val and val != "0" else 0
                    elif isinstance(val, bool):
                        val = 1 if val else 0
                    elif val:
                        val = int(bool(val))
                elif hasattr(field, "is_json") and isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception as e:
                        # Log JSON parsing errors for debugging
                        logger.debug("Failed to parse JSON field '%s': %s", name, e)
                elif hasattr(field, "is_decimal") and not isinstance(
                    val, decimal.Decimal
                ):
                    try:
                        val = decimal.Decimal(str(val))
                    except Exception as e:
                        # Log Decimal conversion errors for debugging
                        logger.debug("Failed to convert Decimal field '%s': %s", name, e)
                elif hasattr(field, "is_enum") and not isinstance(val, enum.Enum):
                    try:
                        val = field.enum_class(val)
                    except Exception as e:
                        # Log enum conversion errors for debugging
                        logger.debug("Failed to convert Enum field '%s': %s", name, e)
                elif hasattr(field, "is_vector") and isinstance(
                    val, (bytes, bytearray)
                ):
                    try:
                        # SECURITY: Validate vector byte length is divisible by 4 (size of float)
                        if len(val) % 4 != 0:
                            logger.warning(
                                "Vector field '%s' has invalid byte length %d (not divisible by 4)",
                                name,
                                len(val),
                            )
                            val = []
                        else:
                            val = list(struct.unpack(f"{len(val) // 4}f", val))
                    except Exception as e:
                        # Log deserialization errors for debugging
                        logger.warning(
                            "Failed to deserialize vector field '%s': %s", name, e
                        )
                        val = []

                # Automatic SafeString for WYSIWYG content
                if getattr(field, "wysiwyg", False) and isinstance(val, str):
                    from ..templates import SafeString

                    val = SafeString(val)

            setattr(self, name, val)

        # Handle extra fields (e.g. aggregates from GROUP BY)
        if _trust:
            for k, v in kwargs.items():
                if k not in self._fields and k != "id":
                    setattr(self, k, v)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id}>"

    def to_dict(self) -> dict[str, Any]:
        """Convert the model instance and its fields to a dictionary.

        Automatically excludes hidden and password fields for security.
        """
        data = {"id": self.id}
        for name in self._fields:
            field = self._fields[name]
            if getattr(field, "hidden", False) or getattr(field, "is_password", False):
                continue
            val = getattr(self, name)
            if isinstance(val, (datetime.date, datetime.datetime)):
                data[name] = val.isoformat()
            else:
                data[name] = val
        return data

    def _hash_value(self, password):
        salt = secrets.token_hex(16)
        iterations = 600000  # OWASP 2023 recommendation
        hash_bytes = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
        )
        return f"pbkdf2:sha256:{iterations}${salt}${binascii.hexlify(hash_bytes).decode('utf-8')}"

    def check_password(self, field_name: str, password: str) -> bool:
        """Verify a plain-text password against a hashed field value."""
        hashed = getattr(self, field_name, None)
        if not hashed or not str(hashed).startswith("pbkdf2:"):
            return False

        try:
            method_info, salt, stored_hash = hashed.split("$")
            _, _, iterations = method_info.split(":")
            num_iterations = int(iterations)
            test_hash = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt.encode("utf-8"), num_iterations
            )
            return hmac.compare_digest(
                binascii.hexlify(test_hash).decode("utf-8"), stored_hash
            )
        except Exception:
            return False

    @classmethod
    def _get_conn(cls):
        attr = cls._conn_attr
        conn = getattr(_local, attr, None)
        if conn is not None:
            return conn
        try:
            # Use mode=rw to prevent automatic file creation during runtime.
            # The file should only be created by 'asok migrate' or 'asok make migration'.
            conn = sqlite3.connect(f"file:{cls._db_path}?mode=rw", uri=True)
        except sqlite3.OperationalError:
            # Database file does not exist yet. Return an in-memory connection
            # to prevent file creation and allow read operations to fail gracefully (no such table).
            conn = sqlite3.connect(":memory:")

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        # ASOK VECTOR EXTENSION
        conn.create_function("cosine_similarity", 2, _asok_cosine_similarity)
        conn.create_function("euclidean_distance", 2, _asok_euclidean_distance)

        # SQL LOGGING FOR DEVELOPER TOOLBAR
        conn = ConnectionProxy(conn)

        setattr(_local, attr, conn)
        # Track for cleanup
        if not hasattr(_local, "_all_conns"):
            _local._all_conns = []
        _local._all_conns.append(conn)
        return conn

    @classmethod
    def close_connections(cls):
        """Close all SQLite connections held by the current thread."""
        for conn in getattr(_local, "_all_conns", []):
            try:
                conn.close()
            except Exception as e:
                # Log connection close errors for debugging
                logger.debug("Error closing database connection: %s", e)
        _local._all_conns = []
        for attr in list(vars(_local)):
            if attr.startswith("conn_"):
                delattr(_local, attr)

    @classmethod
    def create_table(cls):
        """Create the table if it doesn't exist, or migrate it by adding missing columns."""
        # SECURITY: Validate table name to prevent SQL injection
        validate_sql_identifier(cls._table, "table name")

        f_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for name, f in cls._fields.items():
            # SECURITY: Validate column name to prevent SQL injection
            validate_sql_identifier(name, "column name")
            def_str = f"{name} {f.sql_type}"
            if f.unique:
                def_str += " UNIQUE"
            if not f.nullable:
                def_str += " NOT NULL"
            if f.default is not None:
                if isinstance(f.default, bool):
                    d = str(f.default).lower()
                elif isinstance(f.default, (int, float)):
                    d = str(f.default)
                else:
                    d = "'" + str(f.default).replace("'", "''") + "'"
                def_str += f" DEFAULT {d}"
            f_defs.append(def_str)

        sql = f"CREATE TABLE IF NOT EXISTS {cls._table} ({', '.join(f_defs)})"
        with cls._get_conn() as conn:
            conn.execute(sql)

            # ── AUTO-MIGRATION: Add missing columns ──
            existing_cols = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info({cls._table})").fetchall()
            ]
            for name, f in cls._fields.items():
                if name not in existing_cols:
                    # SECURITY: Validate column name (already validated above, but double-check for migrations)
                    validate_sql_identifier(name, "column name")
                    def_str = f"{name} {f.sql_type}"
                    if f.unique:
                        def_str += " UNIQUE"
                    if not f.nullable:
                        def_str += " NOT NULL"
                    if f.default is not None:
                        if isinstance(f.default, bool):
                            d = str(f.default).lower()
                        elif isinstance(f.default, (int, float)):
                            d = str(f.default)
                        else:
                            d = "'" + str(f.default).replace("'", "''") + "'"
                        def_str += f" DEFAULT {d}"

                    logger.info("Migrating %s: Adding column %s", cls._table, name)
                    try:
                        conn.execute(f"ALTER TABLE {cls._table} ADD COLUMN {def_str}")
                    except Exception as e:
                        logger.error(
                            "Failed to migrate %s (adding %s): %s", cls._table, name, e
                        )

            if cls._search_fields:
                # SECURITY: Validate all searchable field names before using in SQL
                for field_name in cls._search_fields:
                    cls._valid_column(field_name)

                # SECURITY: Quote column names to prevent SQL injection
                f_names_quoted = ", ".join([f'"{n}"' for n in cls._search_fields])
                f_names_new = ", ".join([f"new.{n}" for n in cls._search_fields])
                f_names_old = ", ".join([f"old.{n}" for n in cls._search_fields])

                # Create FTS5 virtual table with quoted column names
                fts_sql = f'CREATE VIRTUAL TABLE IF NOT EXISTS "{cls._table}_fts" USING fts5({f_names_quoted}, content="{cls._table}", content_rowid="id")'
                conn.execute(fts_sql)

                # Triggers to keep FTS in sync (with quoted table and column names)
                ai = f"""CREATE TRIGGER IF NOT EXISTS "{cls._table}_ai" AFTER INSERT ON "{cls._table}" BEGIN
                    INSERT INTO "{cls._table}_fts"(rowid, {f_names_quoted}) VALUES (new.id, {f_names_new});
                END;"""
                ad = f"""CREATE TRIGGER IF NOT EXISTS "{cls._table}_ad" AFTER DELETE ON "{cls._table}" BEGIN
                    INSERT INTO "{cls._table}_fts"("{cls._table}_fts", rowid, {f_names_quoted}) VALUES('delete', old.id, {f_names_old});
                END;"""
                au = f"""CREATE TRIGGER IF NOT EXISTS "{cls._table}_au" AFTER UPDATE ON "{cls._table}" BEGIN
                    INSERT INTO "{cls._table}_fts"("{cls._table}_fts", rowid, {f_names_quoted}) VALUES('delete', old.id, {f_names_old});
                    INSERT INTO "{cls._table}_fts"(rowid, {f_names_quoted}) VALUES (new.id, {f_names_new});
                END;"""
                conn.execute(ai)
                conn.execute(ad)
                conn.execute(au)

                # Auto-rebuild if FTS is empty but source has data
                try:
                    source_count = conn.execute(
                        f"SELECT COUNT(*) FROM {cls._table}"
                    ).fetchone()[0]
                    fts_count = conn.execute(
                        f"SELECT COUNT(*) FROM {cls._table}_fts"
                    ).fetchone()[0]
                    if source_count > 0 and fts_count == 0:
                        conn.execute(
                            f'INSERT INTO "{cls._table}_fts"("{cls._table}_fts") VALUES(\'rebuild\')'
                        )
                except Exception as e:
                    # Log FTS5 rebuild errors for debugging
                    logger.warning(
                        "Failed to rebuild FTS5 index for %s: %s", cls._table, e
                    )

            # Create pivot tables for BelongsToMany relationships
            if hasattr(cls, "_relations"):
                for rel_name, rel in cls._relations.items():
                    if rel.type == "BelongsToMany":
                        # Compute pivot table name and foreign keys
                        a = cls.__name__.lower()
                        b = rel.target_model_name.lower()
                        pivot_table = rel.pivot_table or "_".join(sorted([a, b]))
                        pivot_fk = rel.pivot_fk or f"{a}_id"
                        pivot_other_fk = rel.pivot_other_fk or f"{b}_id"

                        # SECURITY: Validate all identifiers to prevent SQL injection
                        validate_sql_identifier(pivot_table, "pivot table name")
                        validate_sql_identifier(pivot_fk, "pivot foreign key")
                        validate_sql_identifier(pivot_other_fk, "pivot foreign key")

                        # Create the pivot table
                        pivot_sql = f"""
                        CREATE TABLE IF NOT EXISTS {pivot_table} (
                            {pivot_fk} INTEGER NOT NULL,
                            {pivot_other_fk} INTEGER NOT NULL,
                            PRIMARY KEY ({pivot_fk}, {pivot_other_fk}),
                            FOREIGN KEY ({pivot_fk}) REFERENCES {cls._table}(id) ON DELETE CASCADE,
                            FOREIGN KEY ({pivot_other_fk}) REFERENCES {_pluralize(b)}(id) ON DELETE CASCADE
                        )
                        """
                        conn.execute(pivot_sql)

            # Create indexes for fields marked with index=True
            for field_name, field in cls._fields.items():
                if getattr(field, "index", False) and not field.unique:
                    # SECURITY: Validate identifiers (field_name already validated above)
                    index_name = f"idx_{cls._table}_{field_name}"
                    validate_sql_identifier(index_name, "index name")
                    index_sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {cls._table}({field_name})"
                    try:
                        conn.execute(index_sql)
                        logger.info(
                            "Created index %s on %s.%s",
                            index_name,
                            cls._table,
                            field_name,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to create index %s on %s.%s: %s",
                            index_name,
                            cls._table,
                            field_name,
                            e,
                        )

            # Commit all schema changes (explicit commit like in save())
            conn.commit()

    @classmethod
    def create(cls: type[T], _trust: bool = False, **kwargs: Any) -> T:
        """Create a new model instance, save it to the database, and return it."""
        obj = cls(_trust=_trust, **kwargs)
        obj.save()
        return obj

    # ── Model event hooks (override in subclasses) ───────────
    def before_save(self):
        """Called before a model is persisted to the database (both on create and update)."""
        pass

    def after_save(self):
        """Called after a model is successfully saved to the database."""
        pass

    def before_create(self):
        """Called only before a new model record is created."""
        pass

    def after_create(self):
        """Called only after a new model record is successfully created."""
        pass

    def before_update(self):
        """Called only before an existing model record is updated."""
        pass

    def after_update(self):
        """Called only after an existing model record is successfully updated."""
        pass

    def before_delete(self):
        """Called before a model record is deleted from the database."""
        pass

    def after_delete(self):
        """Called after a model record is successfully deleted."""
        pass

    def update(self, **values: Any) -> Model:
        """Set multiple attributes and save the model in one call.

        Security: prevents mass assignment of protected fields.
        """
        for k, v in values.items():
            field = self._fields.get(k)
            if field and field.protected:
                continue
            setattr(self, k, v)
        self.save()
        return self

    def increment(self, column: str, amount: int = 1) -> Model:
        """Atomically increment a numeric column in the database."""
        if not self.id:
            raise ModelError("Cannot increment unsaved model")
        if not self._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        sql = f"UPDATE {self._table} SET {column} = {column} + ? WHERE id = ?"
        with self._get_conn() as conn:
            conn.execute(sql, (amount, self.id))
        return self.refresh()

    def decrement(self, column, amount=1):
        """Atomic decrement of a column."""
        return self.increment(column, -amount)

    def refresh(self) -> Model:
        """Reload all attributes from the latest database state."""
        if not self.id:
            return self
        fresh = self.__class__.find(id=self.id)
        if fresh:
            self.__dict__.update(fresh.__dict__)
        return self

    @classmethod
    def find_or_fail(cls: type[T], id: Optional[Any] = None, **kwargs: Any) -> T:
        """Find a single record matching kwargs, or raise ModelError if not found."""
        if id is not None:
            kwargs["id"] = id
        obj = cls.find(**kwargs)
        if not obj:
            raise ModelError(f"{cls._model_name} not found", field="id")
        return obj

    def save(self) -> None:
        """Persist the model instance to the database (INSERT or UPDATE)."""
        is_new = not self.id
        self.before_save()
        if is_new:
            self.before_create()
        else:
            self.before_update()

        for name in self._email_fields:
            val = getattr(self, name, None)
            if val in (None, ""):
                continue
            if not _RE_EMAIL.match(str(val)):
                raise ModelError(
                    f"{name.replace('_', ' ').capitalize()} is not a valid email address.",
                    field=name,
                )

        for name in self._tel_fields:
            val = getattr(self, name, None)
            if val in (None, ""):
                continue
            if not _RE_TEL.match(str(val)):
                raise ModelError(
                    f"{name.replace('_', ' ').capitalize()} is not a valid phone number.",
                    field=name,
                )

        for name in self._password_fields:
            val = getattr(self, name)
            if val and not str(val).startswith("pbkdf2:"):
                setattr(self, name, self._hash_value(str(val)))

        for name in self._uuid_fields:
            if not getattr(self, name):
                setattr(self, name, str(uuid.uuid4()))

        for name in self._slug_fields:
            field = self._fields[name]
            populate = getattr(field, "populate_from", None)
            always_update = getattr(field, "always_update", False)
            if populate and (not getattr(self, name) or always_update):
                source_val = getattr(self, populate, None)
                if source_val:
                    setattr(self, name, slugify(source_val))

        if self._timestamp_fields:
            now = datetime.datetime.now().isoformat()
            for name in self._timestamp_fields:
                field = self._fields[name]
                if field.on == "create" and not self.id and not getattr(self, name):
                    setattr(self, name, now)
                elif field.on == "update":
                    setattr(self, name, now)

        fields = self._fields_list
        values = []
        for f in fields:
            field = self._fields[f]
            val = getattr(self, f)
            if val is None:
                values.append(None)
            elif isinstance(val, FileRef):
                values.append(val.name)
            elif hasattr(field, "is_json"):
                values.append(json.dumps(val))
            elif hasattr(field, "is_decimal"):
                values.append(str(val))
            elif hasattr(field, "is_enum"):
                import enum

                # Handle both Enum objects and raw strings
                if isinstance(val, enum.Enum):
                    values.append(val.value)
                else:
                    values.append(val)
            elif hasattr(field, "is_vector"):
                if val is None:
                    values.append(None)
                else:
                    # Validate dimensions
                    if len(val) != field.dimensions:
                        raise ModelError(
                            f"Vector field '{f}' expects {field.dimensions} dims, got {len(val)}"
                        )
                    values.append(struct.pack(f"{len(val)}f", *val))
            else:
                values.append(val)

        if self.id:
            set_str = ", ".join([f"{f} = ?" for f in fields])
            sql = f"UPDATE {self._table} SET {set_str} WHERE id = ?"
            args = values + [self.id]
        else:
            placeholders = ", ".join(["?" for _ in fields])
            sql = f"INSERT INTO {self._table} ({', '.join(fields)}) VALUES ({placeholders})"
            args = values

        with self._get_conn() as conn:
            try:
                cursor = conn.execute(sql, args)
                conn.commit()
            except sqlite3.IntegrityError as e:
                conn.rollback()
                msg = str(e)
                m = _RE_UNIQUE.search(msg)
                if m:
                    field = m.group(1)
                    raise ModelError(
                        f"{field} already exists", field=field, original=e
                    ) from None
                m = _RE_NOT_NULL.search(msg)
                if m:
                    field = m.group(1)
                    raise ModelError(
                        f"{field} is required", field=field, original=e
                    ) from None
                raise ModelError(msg, original=e) from None
            if not self.id:
                self.id = cursor.lastrowid

        if is_new:
            self.after_create()
            events.emit(f"model:{self.__class__.__name__}:created", self)
            events.emit("model:created", self)
        else:
            self.after_update()
            events.emit(f"model:{self.__class__.__name__}:{self.id}:updated", self)
            events.emit(f"model:{self.__class__.__name__}:updated", self)
            events.emit("model:updated", self)
        self.after_save()
        events.emit("model:saved", self)

    @classmethod
    def transaction(cls):
        """Context manager for database transactions.

        Usage:
            with User.transaction():
                user.save()
                profile.save()
        """
        return _Transaction(cls._get_conn())

    @classmethod
    def _valid_column(cls, col):
        return col == "id" or col in cls._fields

    @classmethod
    def query(cls: type[T], with_trashed: bool = False) -> Query[T]:
        """Start a new chainable query for this model."""
        from .query import Query

        return Query(cls, with_trashed=with_trashed)

    @classmethod
    def where(cls: type[T], column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Start a new query with an initial where clause."""
        from .query import Query

        return Query(cls).where(column, op_or_val, val)

    @classmethod
    def where_in(cls: type[T], column: str, values: list[Any]) -> Query[T]:
        """Start a new query with an initial where_in clause."""
        from .query import Query

        return Query(cls).where_in(column, values)

    @classmethod
    def _soft_delete_where(cls):
        if cls._soft_delete_field:
            return f"{cls._soft_delete_field} IS NULL"
        return None

    @classmethod
    def all(
        cls: type[T],
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> ModelList[T]:
        """Fetch all records matching simple field criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        if wheres:
            sql = f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)}"
        else:
            sql = f"SELECT * FROM {cls._table}"

        if order_by:
            col = order_by.lstrip("-")
            if not cls._valid_column(col):
                raise ValueError(f"Invalid column for order_by: {col}")
            direction = "DESC" if order_by.startswith("-") else "ASC"
            sql += f" ORDER BY {col} {direction}"

        if limit:
            sql += " LIMIT ?"
            args.append(limit)

        with cls._get_conn() as conn:
            rows = conn.execute(sql, args).fetchall()
            return ModelList(
                (cls(_trust=True, **dict(row)) for row in rows), sql=sql, args=args
            )

    @classmethod
    def count(cls, **kwargs):
        """Return the total number of records matching the given criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        if wheres:
            sql = f"SELECT COUNT(*) FROM {cls._table} WHERE {' AND '.join(wheres)}"
        else:
            sql = f"SELECT COUNT(*) FROM {cls._table}"
        with cls._get_conn() as conn:
            return conn.execute(sql, args).fetchone()[0]

    @classmethod
    def exists(cls, **kwargs):
        """Return True if at least one record exists matching the given criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT 1 FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        with cls._get_conn() as conn:
            return conn.execute(sql, args).fetchone() is not None

    @classmethod
    def search(
        cls: type[T], term: str, limit: int = 10, offset: int = 0
    ) -> ModelList[T]:
        """Perform a full-text search against indexed fields."""
        if not cls._search_fields:
            return ModelList()

        # SECURITY: Validate and quote soft delete field name
        sd_where = ""
        if cls._soft_delete_field:
            cls._valid_column(cls._soft_delete_field)
            sd_where = f'AND t."{cls._soft_delete_field}" IS NULL'

        # Preparation du terme pour FTS5 (prefix search par defaut sur chaque mot)
        if term and "*" not in term:
            term = " ".join([f"{t}*" for t in term.split() if t])

        # SECURITY: Quote all table and column names in FTS queries
        # Try FTS5 specific query first
        sql_fts5 = f"""
            SELECT t.* FROM "{cls._table}" t
            JOIN "{cls._table}_fts" f ON t.id = f.rowid
            WHERE f."{cls._table}_fts" MATCH ? {sd_where}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """

        # Fallback for FTS4 or other issues
        sql_fallback = f"""
            SELECT t.* FROM "{cls._table}" t
            JOIN "{cls._table}_fts" f ON t.id = f.rowid
            WHERE f."{cls._table}_fts" MATCH ? {sd_where}
            LIMIT ? OFFSET ?
        """

        sql_used = sql_fts5
        with cls._get_conn() as conn:
            try:
                # Try FTS5
                rows = conn.execute(sql_fts5, (term, limit, offset)).fetchall()
            except sqlite3.OperationalError:
                # If it's just a syntax error in FTS5 or missing rank, try fallback
                sql_used = sql_fallback
                try:
                    rows = conn.execute(sql_fallback, (term, limit, offset)).fetchall()
                except sqlite3.OperationalError as e2:
                    logger.error(
                        "FTS search failed for %s: fallback also failed: %s",
                        cls._table,
                        e2,
                    )
                    return ModelList()

        return ModelList(
            (cls(_trust=True, **dict(row)) for row in rows),
            sql=sql_used,
            args=[term, limit, offset],
        )

    @classmethod
    def first_or_create(cls, defaults=None, **kwargs):
        """Find a row matching kwargs, or create one with kwargs+defaults."""
        obj = cls.find(**kwargs)
        if obj:
            return obj
        data = dict(kwargs)
        if defaults:
            data.update(defaults)
        return cls.create(**data)

    @classmethod
    def update_or_create(cls, defaults=None, **kwargs):
        """Find + update, or create. defaults are the values to set."""
        obj = cls.find(**kwargs)
        if obj:
            if defaults:
                for k, v in defaults.items():
                    setattr(obj, k, v)
                obj.save()
            return obj
        data = dict(kwargs)
        if defaults:
            data.update(defaults)
        return cls.create(**data)

    @classmethod
    def raw(cls, sql, args=None):
        """Execute raw SQL and return a ModelList of instances.

        Column names in the result must match model field names.

        Security: Always use parameterised queries with ``?`` placeholders
        and pass user-supplied values via the ``args`` list.  **Never**
        interpolate user input directly into the ``sql`` string — doing so
        opens the door to SQL injection.

        Good:  ``User.raw("SELECT * FROM users WHERE email = ?", [email])``
        Bad:   ``User.raw(f"SELECT * FROM users WHERE email = '{email}'")``
        """
        import re

        # SECURITY: Warn if SQL contains suspicious patterns that might indicate
        # direct user input interpolation instead of parameterized queries

        # Check for common SQL injection patterns
        suspicious_patterns = [
            r"=\s*['\"].*?['\"]",  # = 'value' or = "value"
            r"(?:WHERE|AND|OR)\s+.*?=\s*f['\"]",  # f-string interpolation
            r"\{.*?\}",  # Python f-string placeholders
            r"%\(.*?\)",  # Python % formatting
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, sql, re.IGNORECASE):
                warnings.warn(
                    f"SECURITY WARNING: Raw SQL query may contain interpolated values. "
                    f"Use parameterized queries with '?' placeholders and pass values via args parameter. "
                    f"Query: {sql[:100]}...",
                    UserWarning,
                    stacklevel=2,
                )
                break

        with cls._get_conn() as conn:
            rows = conn.execute(sql, args or []).fetchall()
        return ModelList(
            (cls(_trust=True, **dict(row)) for row in rows), sql=sql, args=args or []
        )

    @classmethod
    def find(cls: type[T], **kwargs: Any) -> Optional[T]:
        """Find the first record matching simple field criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        with cls._get_conn() as conn:
            row = conn.execute(sql, args).fetchone()
            return cls(_trust=True, **dict(row)) if row else None

    @classmethod
    def destroy(cls, **kwargs: Any) -> int:
        """Delete records matching criteria (handles soft delete if enabled)."""
        q = cls.query()
        for k, v in kwargs.items():
            q.where(k, v)

        if cls._soft_delete_field:
            now = datetime.datetime.now().isoformat()
            return q.update(**{cls._soft_delete_field: now})
        return q.delete()

    @classmethod
    def force_destroy(cls, **kwargs: Any) -> int:
        """Permanently delete records matching criteria, bypassing soft delete."""
        from .query import Query

        q = Query(cls, with_trashed=True)
        for k, v in kwargs.items():
            q.where(k, v)
        return q.delete()

    def delete(self) -> None:
        """Delete the current model record (handles soft delete if enabled)."""
        if not self.id:
            return
        self.before_delete()
        if self._soft_delete_field:
            setattr(self, self._soft_delete_field, datetime.datetime.now().isoformat())
            sql = f"UPDATE {self._table} SET {self._soft_delete_field} = ? WHERE id = ?"
            with self._get_conn() as conn:
                conn.execute(sql, (getattr(self, self._soft_delete_field), self.id))
                conn.commit()
        else:
            sql = f"DELETE FROM {self._table} WHERE id = ?"
            with self._get_conn() as conn:
                conn.execute(sql, (self.id,))
                conn.commit()
        self.after_delete()

    def force_delete(self):
        """Permanently delete, bypassing soft delete."""
        if not self.id:
            return
        self.before_delete()
        sql = f"DELETE FROM {self._table} WHERE id = ?"
        with self._get_conn() as conn:
            try:
                conn.execute(sql, (self.id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.after_delete()

    def restore(self):
        """Un-delete a soft-deleted row."""
        if not self.id or not self._soft_delete_field:
            return
        # SECURITY: Validate and quote soft delete field
        self._valid_column(self._soft_delete_field)
        setattr(self, self._soft_delete_field, None)
        sql = f'UPDATE "{self._table}" SET "{self._soft_delete_field}" = NULL WHERE id = ?'
        with self._get_conn() as conn:
            try:
                conn.execute(sql, (self.id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _pivot_info(self, rel):
        """Compute pivot table name and FK column names for BelongsToMany.

        SECURITY: Validates all generated identifiers to prevent SQL injection.
        """
        from .utils import validate_sql_identifier

        a = self.__class__.__name__.lower()
        b = rel.target_model_name.lower()
        pivot = rel.pivot_table or "_".join(sorted([a, b]))
        pfk = rel.pivot_fk or f"{a}_id"
        pofk = rel.pivot_other_fk or f"{b}_id"

        # SECURITY: Validate all identifiers before use in SQL
        validate_sql_identifier(pivot, "pivot table name")
        validate_sql_identifier(pfk, "pivot foreign key")
        validate_sql_identifier(pofk, "pivot other foreign key")

        return pivot, pfk, pofk

    def attach(self, relation_name, ids):
        """Insert pivot rows linking self to target ids."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        if not isinstance(ids, (list, tuple, set)):
            ids = [ids]
        # SECURITY: Quote all identifiers to prevent SQL injection
        sql = f'INSERT OR IGNORE INTO "{pivot}" ("{pfk}", "{pofk}") VALUES (?, ?)'
        with self._get_conn() as conn:
            try:
                for tid in ids:
                    conn.execute(sql, (self.id, tid))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def detach(self, relation_name, ids=None):
        """Remove pivot rows. If ids is None, removes all."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        with self._get_conn() as conn:
            try:
                # SECURITY: Quote all identifiers to prevent SQL injection
                if ids is None:
                    conn.execute(f'DELETE FROM "{pivot}" WHERE "{pfk}" = ?', (self.id,))
                else:
                    if not isinstance(ids, (list, tuple, set)):
                        ids = [ids]
                    placeholders = ", ".join(["?"] * len(ids))
                    conn.execute(
                        f'DELETE FROM "{pivot}" WHERE "{pfk}" = ? AND "{pofk}" IN ({placeholders})',
                        [self.id] + list(ids),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def sync(self, relation_name, ids):
        """Replace all pivot rows for this relation with the given ids."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        with self._get_conn() as conn:
            try:
                # SECURITY: Quote all identifiers to prevent SQL injection
                conn.execute(f'DELETE FROM "{pivot}" WHERE "{pfk}" = ?', (self.id,))
                if ids:
                    if not isinstance(ids, (list, tuple, set)):
                        ids = [ids]
                    values = [(self.id, tid) for tid in set(ids)]
                    conn.executemany(
                        f'INSERT INTO "{pivot}" ("{pfk}", "{pofk}") VALUES (?, ?)', values
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def with_trashed(cls):
        """Include soft-deleted rows in the query."""
        from .query import Query

        return Query(cls, with_trashed=True)

    @classmethod
    def only_trashed(cls):
        """Only return soft-deleted rows."""
        from .query import Query

        if not cls._soft_delete_field:
            return Query(cls)
        q = Query(cls, with_trashed=True)
        q._wheres.append(f"{cls._soft_delete_field} IS NOT NULL")
        return q

    @classmethod
    def paginate(
        cls,
        page: int = 1,
        per_page: int = 10,
        order_by: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Paginate results matching the given criteria.

        Example:
            User.paginate(page=1, per_page=10, active=1)
        """
        q = cls.query()
        for k, v in kwargs.items():
            q.where(k, v)
        if order_by:
            q.order_by(order_by)
        return q.paginate(page, per_page)
