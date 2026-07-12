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
import uuid
import warnings
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from ..events import events
from ._model_relations import (
    build_foreign_key_property,
    build_relation_property,
    build_translatable_property,
)
from .exceptions import ModelError
from .field import Field
from .fileref import FileRef
from .list import ModelList
from .relation import Relation
from .utils import (
    _RE_EMAIL,
    _RE_TEL,
    MODELS_REGISTRY,
    _pluralize,
    slugify,
    validate_sql_identifier,
)

if TYPE_CHECKING:
    from .query import Query

from .exceptions import _UNSET

T = TypeVar("T", bound="Model")
logger = logging.getLogger("asok.orm")

# Module-level cache for derived encryption keys.
# The SHA-256 derivation + base64 encoding is computed at most once per unique
# SECRET_KEY value, then reused across all model instances and requests.
_ENCRYPTION_KEY_CACHE: dict[str, bytes] = {}
_PERSIST_SQL_CACHE: dict[tuple[type, type], tuple[str, str]] = {}


def _build_column_def(engine: Any, name: str, f: Any) -> str:
    col_type = engine.get_column_type(f)
    def_str = f"{name} {col_type}"
    if f.unique:
        def_str += " UNIQUE"
    if not f.nullable:
        def_str += " NOT NULL"
    if f.default is not None:
        def_str += f" DEFAULT {_format_column_default(f.default)}"
    return def_str


def _format_column_default(default: Any) -> str:
    if isinstance(default, bool):
        return str(default).lower()
    if isinstance(default, (int, float)):
        return str(default)
    return "'" + str(default).replace("'", "''") + "'"


def _create_pivot_table(cls, engine: Any, rel: Any) -> None:
    a = cls.__name__.lower()
    b = rel.target_model_name.lower()
    pivot_table = rel.pivot_table or "_".join(sorted([a, b]))
    pivot_fk = rel.pivot_fk or f"{a}_id"
    pivot_other_fk = rel.pivot_other_fk or f"{b}_id"
    # SECURITY: validate identifiers before quoting and embedding in SQL.
    validate_sql_identifier(pivot_table, "pivot table name")
    validate_sql_identifier(pivot_fk, "pivot foreign key")
    validate_sql_identifier(pivot_other_fk, "pivot foreign key")
    q_pivot = engine.quote_identifier(pivot_table)
    q_pfk = engine.quote_identifier(pivot_fk)
    q_pofk = engine.quote_identifier(pivot_other_fk)
    q_table = engine.quote_identifier(cls._table)
    q_other_table = engine.quote_identifier(_pluralize(b))
    engine.execute(
        f"CREATE TABLE IF NOT EXISTS {q_pivot} ("
        f"{q_pfk} INTEGER NOT NULL, {q_pofk} INTEGER NOT NULL, "
        f"PRIMARY KEY ({q_pfk}, {q_pofk}), "
        f"FOREIGN KEY ({q_pfk}) REFERENCES {q_table}(id) ON DELETE CASCADE, "
        f"FOREIGN KEY ({q_pofk}) REFERENCES {q_other_table}(id) ON DELETE CASCADE)"
    )


def _create_index_for_field(cls, engine: Any, field_name: str) -> None:
    index_name = f"idx_{cls._table}_{field_name}"
    validate_sql_identifier(index_name, "index name")
    q_index = engine.quote_identifier(index_name)
    q_table = engine.quote_identifier(cls._table)
    q_field = engine.quote_identifier(field_name)
    index_sql = _build_create_index_sql(engine, q_index, q_table, q_field)
    _execute_index_creation(engine, index_sql, index_name, cls._table, field_name)


def _build_create_index_sql(
    engine: Any, q_index: str, q_table: str, q_field: str
) -> str:
    from .engines import MySQLEngine

    # MySQL has no `IF NOT EXISTS` for CREATE INDEX; fall through to try/except.
    if isinstance(engine, MySQLEngine):
        return f"CREATE INDEX {q_index} ON {q_table}({q_field})"
    return f"CREATE INDEX IF NOT EXISTS {q_index} ON {q_table}({q_field})"


def _execute_index_creation(
    engine: Any, index_sql: str, index_name: str, table: str, field_name: str
) -> None:
    try:
        engine.execute(index_sql)
        logger.info("Created index %s on %s.%s", index_name, table, field_name)
    except Exception as e:
        if _is_duplicate_index_error(e):
            return
        logger.error(
            "Failed to create index %s on %s.%s: %s",
            index_name,
            table,
            field_name,
            e,
        )


def _is_duplicate_index_error(e: Exception) -> bool:
    msg = str(e)
    return "Duplicate key name" in msg or "already exists" in msg or "1061" in msg


# (Removed _SERIALIZER_FLAGS and _field_type_flag for O(1) lookup performance)


def _serialize_default(self, engine, field, val, f):
    return engine.prepare_value(field, val)


def _serialize_json(self, engine, field, val, f):
    return json.dumps(val)


def _serialize_decimal(self, engine, field, val, f):
    return str(val)


def _serialize_enum(self, engine, field, val, f):
    if isinstance(val, enum.Enum):
        return val.value
    return val


def _serialize_vector(self, engine, field, val, f):
    if len(val) != field.dimensions:
        raise ModelError(
            f"Vector field '{f}' expects {field.dimensions} dims, got {len(val)}"
        )
    return engine.prepare_value(field, val)


def _serialize_encrypted(self, engine, field, val, f):
    return self._encrypt_value(val)


_SERIALIZERS_BY_LABEL = {
    "json": _serialize_json,
    "decimal": _serialize_decimal,
    "enum": _serialize_enum,
    "vector": _serialize_vector,
    "encrypted": _serialize_encrypted,
}


def _coerce_file(self, name, field, val, _trust):
    if isinstance(val, FileRef):
        return val
    return FileRef(val, field.upload_to)


def _coerce_boolean(self, name, field, val, _trust):
    if isinstance(val, str):
        return _bool_from_str(val)
    if isinstance(val, bool):
        return 1 if val else 0
    return int(bool(val)) if val else val


def _bool_from_str(val: str) -> int:
    return 1 if val and val != "0" else 0


def _coerce_json(self, name, field, val, _trust):
    if not isinstance(val, str):
        return val
    try:
        return json.loads(val)
    except Exception as e:
        logger.debug("Failed to parse JSON field '%s': %s", name, e)
        return val


def _coerce_decimal(self, name, field, val, _trust):
    if isinstance(val, decimal.Decimal):
        return val
    try:
        return decimal.Decimal(str(val))
    except Exception as e:
        logger.debug("Failed to convert Decimal field '%s': %s", name, e)
        return val


def _coerce_enum(self, name, field, val, _trust):
    if isinstance(val, enum.Enum):
        return val
    try:
        return field.enum_class(val)
    except Exception as e:
        logger.debug("Failed to convert Enum field '%s': %s", name, e)
        return val


def _coerce_vector(self, name, field, val, _trust):
    return self.get_engine().deserialize_value(field, val)


def _coerce_encrypted(self, name, field, val, _trust):
    if _trust:
        return self._decrypt_value(val)
    return val


_FIELD_COERCERS = {
    "file": _coerce_file,
    "boolean": _coerce_boolean,
    "json": _coerce_json,
    "decimal": _coerce_decimal,
    "enum": _coerce_enum,
    "vector": _coerce_vector,
    "encrypted": _coerce_encrypted,
}


def _extract_typed_attrs(attrs: dict, base_type: type) -> dict:
    return {k: v for k, v in attrs.items() if isinstance(v, base_type)}


def _populate_model_metadata(
    attrs: dict, fields: dict, relations: dict, bases: tuple, name: str
) -> None:
    _collect_field_bucket_lists(attrs, fields)
    _collect_field_type_map(attrs, fields)
    _collect_soft_delete_and_search(attrs, fields)
    _collect_global_scopes(attrs, bases)
    _collect_meta(attrs, name)
    attrs["_relations"] = relations
    _attach_foreign_key_properties(attrs, fields)
    _attach_relation_properties(attrs, relations)
    _strip_field_descriptors(attrs, fields)
    _attach_translatable_properties(attrs, fields)


_FIELD_BUCKETS = (
    ("_password_fields", "is_password"),
    ("_slug_fields", "is_slug"),
    ("_timestamp_fields", "is_timestamp"),
    ("_file_fields", "is_file"),
    ("_email_fields", "is_email"),
    ("_tel_fields", "is_tel"),
    ("_json_fields", "is_json"),
    ("_decimal_fields", "is_decimal"),
    ("_enum_fields", "is_enum"),
    ("_uuid_fields", "is_uuid"),
    ("_vector_fields", "is_vector"),
    ("_encrypted_fields", "is_encrypted"),
)

# Ordered tuple → first match wins (mirrors original elif chain).
_FIELD_TYPE_FLAGS = (
    ("file", "is_file"),
    ("boolean", "is_boolean"),
    ("json", "is_json"),
    ("decimal", "is_decimal"),
    ("enum", "is_enum"),
    ("vector", "is_vector"),
    ("encrypted", "is_encrypted"),
)


def _collect_field_bucket_lists(attrs: dict, fields: dict) -> None:
    attrs["_fields"] = fields
    attrs["_fields_list"] = list(fields.keys())
    for bucket_name, flag in _FIELD_BUCKETS:
        attrs[bucket_name] = [k for k, v in fields.items() if hasattr(v, flag)]


def _collect_field_type_map(attrs: dict, fields: dict) -> None:
    type_map: dict[str, str] = {}
    for k, v in fields.items():
        for label, flag in _FIELD_TYPE_FLAGS:
            if hasattr(v, flag):
                type_map[k] = label
                break
    attrs["_field_type_map"] = type_map


def _collect_soft_delete_and_search(attrs: dict, fields: dict) -> None:
    soft_delete_fields = _filter_field_names(fields, "is_soft_delete")
    attrs["_soft_delete_field"] = soft_delete_fields[0] if soft_delete_fields else None
    attrs["_search_fields"] = [
        k for k, v in fields.items() if getattr(v, "searchable", False)
    ]


def _filter_field_names(fields: dict, flag: str) -> list[str]:
    return [k for k, v in fields.items() if hasattr(v, flag)]


def _collect_global_scopes(attrs: dict, bases: tuple) -> None:
    scopes: dict[str, Any] = {}
    for base in bases:
        if hasattr(base, "_global_scopes"):
            scopes.update(base._global_scopes)
    if "_global_scopes" in attrs:
        scopes.update(attrs["_global_scopes"])
    sdf = attrs["_soft_delete_field"]
    if sdf:
        scopes["soft_delete"] = lambda q, sdf=sdf: q.where_null(sdf)
    attrs["_global_scopes"] = scopes


def _collect_meta(attrs: dict, name: str) -> None:
    attrs["_table"] = attrs.get("__tablename__", _pluralize(name))
    attrs["_model_name"] = name
    attrs["_conn_attr"] = f"conn_{attrs.get('_db_path') or 'db.sqlite3'}"


def _attach_foreign_key_properties(attrs: dict, fields: dict) -> None:
    for k, v in fields.items():
        if not hasattr(v, "is_foreign_key"):
            continue
        rel_name = k.replace("_id", "")
        attrs[rel_name] = build_foreign_key_property(k, v.related_model)


def _attach_relation_properties(attrs: dict, relations: dict) -> None:
    for k, v in relations.items():
        prop = build_relation_property(v, k)
        if prop is not None:
            attrs[k] = prop


def _strip_field_descriptors(attrs: dict, fields: dict) -> None:
    for k in fields:
        if k in attrs and isinstance(attrs[k], Field):
            attrs.pop(k)


def _attach_translatable_properties(attrs: dict, fields: dict) -> None:
    for base_name in _detect_translatable_bases(fields):
        if base_name not in attrs:
            attrs[base_name] = build_translatable_property(base_name)


def _detect_translatable_bases(fields: dict) -> set[str]:
    return {
        field_name[:-3] for field_name in fields if _is_translatable_field(field_name)
    }


def _is_translatable_field(field_name: str) -> bool:
    if len(field_name) <= 3 or field_name[-3] != "_":
        return False
    lang_suffix = field_name[-2:]
    return lang_suffix.isalpha() and lang_suffix.islower()


class ModelMeta(type):
    """Metaclass for all Asok Models.

    Handles field discovery, relationship mapping, and automatic table name generation.
    """

    def __new__(mcs, name, bases, attrs):
        if name == "Model":
            return super().__new__(mcs, name, bases, attrs)
        fields = _extract_typed_attrs(attrs, Field)
        relations = _extract_typed_attrs(attrs, Relation)
        _populate_model_metadata(attrs, fields, relations, bases, name)
        cls = super().__new__(mcs, name, bases, attrs)
        MODELS_REGISTRY[name] = cls
        return cls


class Model(metaclass=ModelMeta):
    _db_path: str | None = (os.getenv("DATABASE_URL") or "").strip() or None
    _global_scopes: dict[str, Any] = {}

    @classmethod
    def get_engine(cls, op: str | None = None, shard: str | None = None):
        from .engines import get_engine as get_engine_raw
        from .router import _routing_state, route_database

        if op is None:
            op = getattr(_routing_state, "op", "write")
        if shard is None:
            shard = getattr(_routing_state, "shard", None)
        if cls._primary_engine_in_txn(shard):
            op = "write"
        return get_engine_raw(route_database(cls, op, shard))

    @classmethod
    def _primary_engine_in_txn(cls, shard) -> bool:
        # If the primary engine has an open txn, force writes through it.
        from .engines import _ENGINES_CACHE
        from .router import resolve_primary_dsn

        primary_engine = _ENGINES_CACHE.get(resolve_primary_dsn(cls, shard))
        if not primary_engine or not hasattr(primary_engine, "_local"):
            return False
        return getattr(primary_engine._local, "txn_level", 0) > 0

    @classmethod
    def _get_encryption_key(cls) -> bytes:
        secret = cls._resolve_secret_key()
        cached = _ENCRYPTION_KEY_CACHE.get(secret)
        if cached is not None:
            return cached
        import base64

        # SECURITY: use PBKDF2-HMAC-SHA256 (600k iterations) instead of a bare
        # SHA-256 so that offline dictionary attacks against leaked ciphertext
        # require ~625 000× more work to recover the key.
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            secret.encode(),
            b"asok:field-encryption-v1",
            600_000,
        )
        key = base64.urlsafe_b64encode(derived)
        _ENCRYPTION_KEY_CACHE[secret] = key
        return key

    @classmethod
    def _resolve_secret_key(cls) -> str:
        secret = os.getenv("SECRET_KEY") or cls._secret_from_request_app()
        if not secret:
            raise RuntimeError("SECRET_KEY is not configured")
        return secret

    @staticmethod
    def _secret_from_request_app() -> Optional[str]:
        from ..context import current_request

        if not current_request or not hasattr(current_request, "environ"):
            return None
        app_ref = current_request.environ.get("asok.app")
        return app_ref.config.get("SECRET_KEY") if app_ref else None

    def _encrypt_value(self, val: Any) -> Optional[str]:
        if val is None:
            return None

        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ImportError(
                "The 'cryptography' library is required to use encrypted fields. "
                "Install it using 'pip install cryptography'."
            )

        key = self._get_encryption_key()
        f = Fernet(key)
        return f.encrypt(str(val).encode()).decode()

    def _decrypt_value(self, val: Any) -> Optional[str]:
        if val is None:
            return None

        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ImportError(
                "The 'cryptography' library is required to use encrypted fields. "
                "Install it using 'pip install cryptography'."
            )

        key = self._get_encryption_key()
        f = Fernet(key)
        try:
            return f.decrypt(str(val).encode()).decode()
        except Exception as e:
            logger.error("Failed to decrypt field value: %s", e)
            return str(val)

    @classmethod
    def on(cls: type[T], shard_name: str) -> Query[T]:
        """Start a new chainable query targeted at a specific database shard."""
        return cls.query().on(shard_name)

    def __init__(self, _trust: bool = False, **kwargs: Any):
        self._shard: Optional[str] = kwargs.get("_shard")
        self.id: Optional[int] = kwargs.get("id")
        is_new = not self.id
        for name in self._fields:
            self._assign_field(name, kwargs, _trust, is_new)
        if _trust:
            self._absorb_extra_kwargs(kwargs)

    def _assign_field(
        self, name: str, kwargs: dict, _trust: bool, is_new: bool
    ) -> None:
        field = self._fields[name]
        val = self._resolve_field_value(name, field, kwargs, _trust, is_new)
        if val is not None:
            val = self._coerce_field_value(name, field, val, _trust)
            val = self._maybe_wrap_safestring(field, val)
        if _trust:
            self.__dict__[name] = val
        else:
            setattr(self, name, val)

    def _resolve_field_value(
        self, name: str, field: Any, kwargs: dict, _trust: bool, is_new: bool
    ) -> Any:
        if name not in kwargs:
            return getattr(self, name, field.default)
        if self._field_blocked_by_protection(field, _trust, is_new):
            logger.warning(
                "Mass-assignment blocked on protected field '%s' of model '%s'. "
                "The value was silently discarded. Use cls.create(_trust=True, ...) "
                "or model instance attributes to set protected fields.",
                name,
                self.__class__.__name__,
            )
            return field.default
        return kwargs[name]

    @staticmethod
    def _field_blocked_by_protection(field: Any, _trust: bool, is_new: bool) -> bool:
        # SECURITY: forbid mass-assignment on protected fields unless trusted.
        if _trust or not getattr(field, "protected", False):
            return False
        if getattr(field, "is_password", False) and is_new:
            return False
        return True

    def _coerce_field_value(self, name: str, field: Any, val: Any, _trust: bool) -> Any:
        ftype = self._field_type_map.get(name)
        if ftype is None:
            return val
        coercer = _FIELD_COERCERS.get(ftype)
        if coercer is None:
            return val
        return coercer(self, name, field, val, _trust)

    @staticmethod
    def _maybe_wrap_safestring(field: Any, val: Any) -> Any:
        if not getattr(field, "wysiwyg", False) or not isinstance(val, str):
            return val
        from ..templates import SafeString

        return SafeString(val)

    def _absorb_extra_kwargs(self, kwargs: dict) -> None:
        # Allow aggregates from GROUP BY / raw SELECT to land on the instance.
        for k, v in kwargs.items():
            if k not in self._fields and k != "id":
                self.__dict__[k] = v

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
        from .router import _routing_state

        op = getattr(_routing_state, "op", "write")
        shard = getattr(_routing_state, "shard", None)
        return cls.get_engine(op=op, shard=shard).get_connection()

    @classmethod
    def close_connections(cls):
        """Close all database connections held by the current thread."""
        cls.get_engine().close_connections()

    @classmethod
    def create_table(cls):
        """Create the table if it doesn't exist, or migrate it by adding missing columns."""
        # SECURITY: validate identifiers to prevent SQL injection.
        validate_sql_identifier(cls._table, "table name")
        engine = cls.get_engine(op="write")
        cls._execute_create_table(engine)
        cls._migrate_missing_columns(engine)
        cls._create_pivot_tables(engine)
        cls._create_field_indexes(engine)
        engine.post_create_table(cls)

    @classmethod
    def _execute_create_table(cls, engine: Any) -> None:
        pk_def = getattr(
            engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        f_defs = [pk_def]
        for name, f in cls._fields.items():
            validate_sql_identifier(name, "column name")
            f_defs.append(_build_column_def(engine, name, f))
        sql = (
            f"CREATE TABLE IF NOT EXISTS {engine.quote_identifier(cls._table)} "
            f"({', '.join(f_defs)})"
        )
        engine.execute(sql)

    @classmethod
    def _migrate_missing_columns(cls, engine: Any) -> None:
        existing_cols = engine.get_table_columns(cls._table)
        for name, f in cls._fields.items():
            if name in existing_cols:
                continue
            cls._add_missing_column(engine, name, f)

    @classmethod
    def _add_missing_column(cls, engine: Any, name: str, f: Any) -> None:
        validate_sql_identifier(name, "column name")
        def_str = _build_column_def(engine, name, f)
        logger.info("Migrating %s: Adding column %s", cls._table, name)
        try:
            engine.execute(
                f"ALTER TABLE {engine.quote_identifier(cls._table)} ADD COLUMN {def_str}"
            )
        except Exception as e:
            logger.error("Failed to migrate %s (adding %s): %s", cls._table, name, e)

    @classmethod
    def _create_pivot_tables(cls, engine: Any) -> None:
        if not hasattr(cls, "_relations"):
            return
        for _, rel in cls._relations.items():
            if rel.type == "BelongsToMany":
                _create_pivot_table(cls, engine, rel)

    @classmethod
    def _create_field_indexes(cls, engine: Any) -> None:
        for field_name, field in cls._fields.items():
            if getattr(field, "index", False) and not field.unique:
                _create_index_for_field(cls, engine, field_name)

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

        Security: prevents mass assignment of protected fields and id reassignment.
        """
        for k, v in values.items():
            # SECURITY: never allow id reassignment — it would redirect save() to a
            # different row, potentially overwriting another record.
            if k == "id":
                continue
            field = self._fields.get(k)
            # Skip unknown keys silently (not part of the schema).
            if not field:
                continue
            if field.protected:
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
        self.get_engine(op="write", shard=getattr(self, "_shard", None)).execute(
            sql, (amount, self.id)
        )
        return self.refresh()

    def decrement(self, column, amount=1):
        """Atomic decrement of a column."""
        return self.increment(column, -amount)

    def refresh(self) -> Model:
        """Reload all attributes from the latest database state."""
        if not self.id:
            return self
        from .router import database_router_context

        with database_router_context(shard=getattr(self, "_shard", None)):
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
        self._fire_pre_save_hooks()
        self._validate_email_fields()
        self._validate_tel_fields()
        self._hash_password_fields()
        self._assign_uuid_fields()
        self._populate_slug_fields()
        self._apply_timestamp_fields()
        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
        values = self._serialize_fields(engine)
        sql, args = self._build_persist_sql(engine, values)
        try:
            engine.execute(sql, args)
        except Exception as e:
            raise engine.handle_exception(e)
        if is_new:
            self._populate_lastrowid(engine)
        self._fire_post_save_hooks(is_new)

    def _populate_lastrowid(self, engine: Any) -> None:
        if not engine.lastrowid_query:
            return
        res_id = engine.execute(engine.lastrowid_query)
        self.id = list(res_id[0].values())[0] if res_id else None

    def _fire_post_save_hooks(self, is_new: bool) -> None:
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

    def _fire_pre_save_hooks(self) -> None:
        self.before_save()
        if not self.id:
            self.before_create()
        else:
            self.before_update()

    def _validate_email_fields(self) -> None:
        for name in self._email_fields:
            val = getattr(self, name, None)
            if val in (None, ""):
                continue
            if not _RE_EMAIL.match(str(val)):
                raise ModelError(
                    f"{name.replace('_', ' ').capitalize()} is not a valid email address.",
                    field=name,
                )

    def _validate_tel_fields(self) -> None:
        for name in self._tel_fields:
            val = getattr(self, name, None)
            if val in (None, ""):
                continue
            if not _RE_TEL.match(str(val)):
                raise ModelError(
                    f"{name.replace('_', ' ').capitalize()} is not a valid phone number.",
                    field=name,
                )

    def _hash_password_fields(self) -> None:
        for name in self._password_fields:
            val = getattr(self, name)
            if val and not str(val).startswith("pbkdf2:"):
                setattr(self, name, self._hash_value(str(val)))

    def _assign_uuid_fields(self) -> None:
        for name in self._uuid_fields:
            if not getattr(self, name):
                setattr(self, name, str(uuid.uuid4()))

    def _populate_slug_fields(self) -> None:
        for name in self._slug_fields:
            self._populate_single_slug(name)

    def _populate_single_slug(self, name: str) -> None:
        field = self._fields[name]
        populate = getattr(field, "populate_from", None)
        if not populate:
            return
        always_update = getattr(field, "always_update", False)
        if getattr(self, name) and not always_update:
            return
        source_val = getattr(self, populate, None)
        if source_val:
            setattr(self, name, slugify(source_val))

    def _apply_timestamp_fields(self) -> None:
        if not self._timestamp_fields:
            return
        now = datetime.datetime.now().isoformat()
        for name in self._timestamp_fields:
            self._apply_single_timestamp(name, now)

    def _apply_single_timestamp(self, name: str, now: str) -> None:
        field = self._fields[name]
        if field.on == "create" and not self.id and not getattr(self, name):
            setattr(self, name, now)
        elif field.on == "update":
            setattr(self, name, now)

    def _serialize_fields(self, engine: Any) -> list[Any]:
        return [self._serialize_field(engine, f) for f in self._fields_list]

    def _serialize_field(self, engine: Any, f: str) -> Any:
        field = self._fields[f]
        val = getattr(self, f)
        if val is None:
            return None
        if isinstance(val, FileRef):
            return val.name
        label = self._field_type_map.get(f)
        serializer = _SERIALIZERS_BY_LABEL.get(label) if label else None
        if serializer is not None:
            return serializer(self, engine, field, val, f)
        return engine.prepare_value(field, val)

    def _build_persist_sql(
        self, engine: Any, values: list[Any]
    ) -> tuple[str, list[Any]]:
        cache_key = (self.__class__, engine.__class__)
        cached = _PERSIST_SQL_CACHE.get(cache_key)
        if cached is None:
            cached = self._generate_persist_sql(engine)
            _PERSIST_SQL_CACHE[cache_key] = cached

        insert_sql, update_sql = cached
        if self.id:
            return update_sql, values + [self.id]
        return insert_sql, values

    def _generate_persist_sql(self, engine: Any) -> tuple[str, str]:
        fields = self._fields_list
        placeholders = ", ".join("?" for _ in fields)
        quoted_fields = ", ".join(engine.quote_identifier(f) for f in fields)
        insert_sql = (
            f"INSERT INTO {self._table} ({quoted_fields}) VALUES ({placeholders})"
        )
        set_str = ", ".join(f"{engine.quote_identifier(f)} = ?" for f in fields)
        update_sql = f"UPDATE {self._table} SET {set_str} WHERE id = ?"
        return insert_sql, update_sql

    @classmethod
    def transaction(cls):
        """Context manager for database transactions.

        Usage:
            with User.transaction():
                user.save()
                profile.save()
        """
        from .router import _routing_state

        shard = getattr(_routing_state, "shard", None)
        return cls.get_engine(op="write", shard=shard).transaction()

    @classmethod
    def _valid_column(cls, col):
        return col == "id" or col in cls._fields

    @classmethod
    def query(cls: type[T], with_trashed: bool = False) -> Query[T]:
        """Start a new chainable query for this model."""
        from .query import Query

        return Query(cls, with_trashed=with_trashed)

    @classmethod
    def where(cls: type[T], column: str, op_or_val: Any, val: Any = _UNSET) -> Query[T]:
        """Start a new query with an initial where clause."""
        from .query import Query

        return Query(cls).where(column, op_or_val, val)

    @classmethod
    def where_in(cls: type[T], column: str, values: list[Any]) -> Query[T]:
        """Start a new query with an initial where_in clause."""
        from .query import Query

        return Query(cls).where_in(column, values)

    @classmethod
    def filter_by(cls: type[T], **kwargs: Any) -> Query[T]:
        """Start a new query filtered by key-value pairs."""
        from .query import Query

        return Query(cls).filter_by(**kwargs)

    @classmethod
    def first_or_fail(cls: type[T], **kwargs: Any) -> T:
        """Start a new query filtered by key-value pairs and return the first record, or raise ModelError."""
        return cls.filter_by(**kwargs).first_or_fail()

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
        cls._validate_columns(kwargs)
        sql, args = cls._build_select_all_sql(kwargs, order_by, limit)
        engine = cls.get_engine(op="read")
        rows = engine.execute(sql, args)
        return cls._instantiate_rows(rows, sql, args)

    @classmethod
    def _validate_columns(cls, kwargs: dict) -> None:
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")

    @classmethod
    def _build_select_all_sql(
        cls, kwargs: dict, order_by: Optional[str], limit: Optional[int]
    ) -> tuple[str, list[Any]]:
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = (
            f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)}"
            if wheres
            else f"SELECT * FROM {cls._table}"
        )
        sql += cls._order_by_clause(order_by)
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return sql, args

    @classmethod
    def _order_by_clause(cls, order_by: Optional[str]) -> str:
        if not order_by:
            return ""
        col = order_by.lstrip("-")
        if not cls._valid_column(col):
            raise ValueError(f"Invalid column for order_by: {col}")
        direction = "DESC" if order_by.startswith("-") else "ASC"
        return f" ORDER BY {col} {direction}"

    @classmethod
    def _instantiate_rows(cls, rows, sql: str, args: list[Any]) -> ModelList[T]:
        from .router import _routing_state

        active_shard = getattr(_routing_state, "shard", None)

        def _instantiate(row):
            obj = cls(_trust=True, **row)
            obj._shard = active_shard
            return obj

        return ModelList((_instantiate(row) for row in rows), sql=sql, args=args)

    @classmethod
    def count(cls, **kwargs):
        """Return the total number of records matching the given criteria."""
        cls._validate_columns(kwargs)
        engine = cls.get_engine(op="read")
        args = cls._prepare_kwarg_values(engine, kwargs)
        wheres = [f"{k} = ?" for k in kwargs]
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = (
            f"SELECT COUNT(*) FROM {cls._table} WHERE {' AND '.join(wheres)}"
            if wheres
            else f"SELECT COUNT(*) FROM {cls._table}"
        )
        rows = engine.execute(sql, args)
        return list(rows[0].values())[0] if rows else 0

    @classmethod
    def _prepare_kwarg_values(cls, engine: Any, kwargs: dict) -> list[Any]:
        args: list[Any] = []
        for k, v in kwargs.items():
            field = cls._fields.get(k)
            if field:
                v = engine.prepare_value(field, v)
            args.append(v)
        return args

    @classmethod
    def exists(cls, **kwargs):
        """Return True if at least one record exists matching the given criteria."""
        cls._validate_columns(kwargs)
        engine = cls.get_engine(op="read")
        args = cls._prepare_kwarg_values(engine, kwargs)
        wheres = [f"{k} = ?" for k in kwargs]
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT 1 FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        rows = engine.execute(sql, args)
        return len(rows) > 0

    @classmethod
    def search(
        cls: type[T], term: str, limit: int = 10, offset: int = 0
    ) -> ModelList[T]:
        """Perform a full-text search against indexed fields."""
        if not cls._search_fields:
            return ModelList()
        engine = cls.get_engine(op="read")
        term = cls._maybe_prefix_search_term(engine, term)
        sql, all_args = cls._build_search_sql(engine, term, limit, offset)
        try:
            rows = engine.execute(sql, all_args)
        except Exception as e:
            logger.error("FTS search failed for %s: %s", cls._table, e)
            return ModelList()
        return cls._instantiate_rows(rows, sql, all_args)

    @classmethod
    def _maybe_prefix_search_term(cls, engine: Any, term: str) -> str:
        # SQLite FTS5 needs explicit prefix wildcards; MySQL FULLTEXT handles them itself.
        if not cls._needs_sqlite_prefix(engine, term):
            return term
        return " ".join(f"{t}*" for t in term.split() if t)

    @staticmethod
    def _needs_sqlite_prefix(engine: Any, term: str) -> bool:
        from .engines import SQLiteEngine

        return isinstance(engine, SQLiteEngine) and bool(term) and "*" not in term

    @classmethod
    def _build_search_sql(
        cls, engine: Any, term: str, limit: int, offset: int
    ) -> tuple[str, list[Any]]:
        sd_where = cls._search_soft_delete_clause(engine)
        q_table = engine.quote_identifier(cls._table)
        where_clause, search_args = engine.search_sql(
            cls._table, cls._search_fields, term
        )
        sql = f"SELECT * FROM {q_table} WHERE {where_clause}{sd_where} LIMIT ? OFFSET ?"
        return sql, search_args + [limit, offset]

    @classmethod
    def _search_soft_delete_clause(cls, engine: Any) -> str:
        # SECURITY: validate then quote the soft-delete column.
        if not cls._soft_delete_field:
            return ""
        cls._valid_column(cls._soft_delete_field)
        q_sd = engine.quote_identifier(cls._soft_delete_field)
        return f" AND {q_sd} IS NULL"

    @classmethod
    def first_or_create(cls, defaults=None, **kwargs):
        """Find a row matching kwargs, or create one with kwargs+defaults."""
        obj = cls.find(**kwargs)
        if obj:
            return obj
        data = dict(defaults or {})
        data.update(kwargs)
        return cls.create(**data)

    @classmethod
    def update_or_create(cls, defaults=None, **kwargs):
        """Find + update, or create. defaults are the values to set."""
        obj = cls.find(**kwargs)
        if obj:
            if defaults:
                # SECURITY: delegate to update() so protected fields are respected,
                # matching the behaviour of Query.update_or_create().
                obj.update(**defaults)
            return obj
        data = dict(defaults or {})
        data.update(kwargs)
        return cls.create(**data)

    @classmethod
    def raw(cls, sql, args=None):
        """Execute raw SQL and return a ModelList of instances.

        Column names in the result must match model field names.

        SECURITY: always use parameterised queries with ``?`` placeholders and
        pass user-supplied values via ``args``. Never interpolate user input
        directly into ``sql`` — that's a SQL-injection invitation.

        Good:  ``User.raw("SELECT * FROM users WHERE email = ?", [email])``
        Bad:   ``User.raw(f"SELECT * FROM users WHERE email = '{email}'")``
        """
        cls._warn_if_raw_sql_looks_interpolated(sql)
        from .router import _routing_state

        op = getattr(_routing_state, "op", "write")
        shard = getattr(_routing_state, "shard", None)
        engine = cls.get_engine(op=op, shard=shard)
        rows = engine.execute(sql, args or [])
        return cls._instantiate_rows(rows, sql, args or [])

    _RAW_SUSPICIOUS_PATTERNS = (
        r"=\s*['\"].*?['\"]",
        r"(?:WHERE|AND|OR)\s+.*?=\s*f['\"]",
        r"\{.*?\}",
        r"%\(.*?\)",
    )

    @classmethod
    def _warn_if_raw_sql_looks_interpolated(cls, sql: str) -> None:
        import re

        for pattern in cls._RAW_SUSPICIOUS_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE):
                warnings.warn(
                    "SECURITY WARNING: Raw SQL query may contain interpolated values. "
                    "Use parameterized queries with '?' placeholders and pass values via args parameter. "
                    f"Query: {sql[:100]}...",
                    UserWarning,
                    stacklevel=3,
                )
                return

    @classmethod
    def find(cls: type[T], **kwargs: Any) -> Optional[T]:
        """Find the first record matching simple field criteria."""
        cls._validate_columns(kwargs)
        engine = cls.get_engine(op="read")
        args = cls._prepare_kwarg_values(engine, kwargs)
        wheres = [f"{k} = ?" for k in kwargs]
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        rows = engine.execute(sql, args)
        if not rows:
            return None
        from .router import _routing_state

        obj = cls(_trust=True, **rows[0])
        obj._shard = getattr(_routing_state, "shard", None)
        return obj

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
            sql = f'UPDATE "{self._table}" SET "{self._soft_delete_field}" = ? WHERE id = ?'
            self.get_engine(op="write", shard=getattr(self, "_shard", None)).execute(
                sql, (getattr(self, self._soft_delete_field), self.id)
            )
        else:
            sql = f'DELETE FROM "{self._table}" WHERE id = ?'
            self.get_engine(op="write", shard=getattr(self, "_shard", None)).execute(
                sql, (self.id,)
            )
        self.after_delete()
        events.emit(f"model:{self.__class__.__name__}:deleted", self)
        events.emit("model:deleted", self)

    def force_delete(self):
        """Permanently delete, bypassing soft delete."""
        if not self.id:
            return
        self.before_delete()
        sql = f'DELETE FROM "{self._table}" WHERE id = ?'
        self.get_engine(op="write", shard=getattr(self, "_shard", None)).execute(
            sql, (self.id,)
        )
        self.after_delete()
        events.emit(f"model:{self.__class__.__name__}:deleted", self)
        events.emit("model:deleted", self)

    def restore(self):
        """Un-delete a soft-deleted row."""
        if not self.id or not self._soft_delete_field:
            return
        # SECURITY: Validate and quote soft delete field
        self._valid_column(self._soft_delete_field)
        setattr(self, self._soft_delete_field, None)
        sql = f'UPDATE "{self._table}" SET "{self._soft_delete_field}" = NULL WHERE id = ?'
        self.get_engine(op="write", shard=getattr(self, "_shard", None)).execute(
            sql, (self.id,)
        )

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
        """Insert pivot rows linking self to target ids (idempotent — skips existing)."""
        ids = self._normalize_attach_ids(ids)
        if not ids:
            return
        pivot, pfk, pofk = self._resolve_belongs_to_many(relation_name)
        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
        q_pivot, q_pfk, q_pofk = self._quote_pivot_idents(engine, pivot, pfk, pofk)
        existing_ids = self._fetch_existing_pivot_ids(
            engine, q_pivot, q_pfk, q_pofk, pofk, ids
        )
        to_insert = [tid for tid in ids if tid not in existing_ids]
        if not to_insert:
            return
        self._insert_pivot_rows(engine, q_pivot, q_pfk, q_pofk, to_insert)

    @staticmethod
    def _normalize_attach_ids(ids: Any) -> list:
        if not isinstance(ids, (list, tuple, set)):
            ids = [ids]
        return list(ids) if ids else []

    def _resolve_belongs_to_many(self, relation_name: str) -> tuple[str, str, str]:
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        return self._pivot_info(rel)

    @staticmethod
    def _quote_pivot_idents(engine: Any, pivot, pfk, pofk):
        return (
            engine.quote_identifier(pivot),
            engine.quote_identifier(pfk),
            engine.quote_identifier(pofk),
        )

    def _fetch_existing_pivot_ids(
        self, engine, q_pivot, q_pfk, q_pofk, pofk, ids
    ) -> set:
        # Batch SELECT instead of N+1 round trips.
        placeholders_in = ", ".join(["?"] * len(ids))
        rows = engine.execute(
            f"SELECT {q_pofk} FROM {q_pivot} "
            f"WHERE {q_pfk} = ? AND {q_pofk} IN ({placeholders_in})",
            [self.id] + ids,
        )
        return {row[pofk] for row in rows}

    def _insert_pivot_rows(self, engine, q_pivot, q_pfk, q_pofk, to_insert) -> None:
        row_placeholders = ", ".join(["(?, ?)"] * len(to_insert))
        args = [v for tid in to_insert for v in (self.id, tid)]
        engine.execute(
            f"INSERT INTO {q_pivot} ({q_pfk}, {q_pofk}) VALUES {row_placeholders}",
            args,
        )

    def detach(self, relation_name, ids=None):
        """Remove pivot rows. If ids is None, removes all."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
        q_pivot = engine.quote_identifier(pivot)
        q_pfk = engine.quote_identifier(pfk)
        q_pofk = engine.quote_identifier(pofk)

        if ids is None:
            engine.execute(f"DELETE FROM {q_pivot} WHERE {q_pfk} = ?", (self.id,))
        else:
            if not isinstance(ids, (list, tuple, set)):
                ids = [ids]
            placeholders = ", ".join(["?"] * len(ids))
            engine.execute(
                f"DELETE FROM {q_pivot} WHERE {q_pfk} = ? AND {q_pofk} IN ({placeholders})",
                [self.id] + list(ids),
            )

    def sync(self, relation_name, ids):
        """Replace all pivot rows for this relation with the given ids."""
        pivot, pfk, pofk = self._resolve_belongs_to_many(relation_name)
        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
        q_pivot, q_pfk, q_pofk = self._quote_pivot_idents(engine, pivot, pfk, pofk)
        engine.execute(f"DELETE FROM {q_pivot} WHERE {q_pfk} = ?", (self.id,))
        self._insert_synced_pivot_rows(engine, q_pivot, q_pfk, q_pofk, ids)

    def _insert_synced_pivot_rows(self, engine, q_pivot, q_pfk, q_pofk, ids) -> None:
        if not ids:
            return
        if not isinstance(ids, (list, tuple, set)):
            ids = [ids]
        insert_sql = f"INSERT INTO {q_pivot} ({q_pfk}, {q_pofk}) VALUES (?, ?)"
        for tid in set(ids):
            engine.execute(insert_sql, (self.id, tid))

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
        count: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Paginate results matching the given criteria.

        Args:
            page: The page number (1-indexed).
            per_page: Number of items per page.
            order_by: Optional column to order by (prefix with '-' for DESC).
            count: If True (default), also run a COUNT(*) query to return total
                   pages. Set to False to skip it for faster infinite-scroll UIs.

        Example:
            User.paginate(page=1, per_page=10, active=1)
            User.paginate(page=1, per_page=10, count=False)
        """
        q = cls.query()
        for k, v in kwargs.items():
            q.where(k, v)
        if order_by:
            q.order_by(order_by)
        return q.paginate(page, per_page, count=count)

    @classmethod
    async def all_async(
        cls: type[T],
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> ModelList[T]:
        """Fetch all records matching simple criteria asynchronously."""
        import asyncio

        return await asyncio.to_thread(
            cls.all, order_by=order_by, limit=limit, **kwargs
        )

    @classmethod
    async def find_async(cls: type[T], **kwargs: Any) -> Optional[T]:
        """Find the first record matching simple criteria asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.find, **kwargs)

    @classmethod
    async def create_async(cls: type[T], **kwargs: Any) -> T:
        """Create and save a new record asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.create, **kwargs)

    async def save_async(self) -> None:
        """Persist the model instance to the database asynchronously."""
        import asyncio

        await asyncio.to_thread(self.save)

    async def delete_async(self) -> None:
        """Delete the current model record asynchronously."""
        import asyncio

        await asyncio.to_thread(self.delete)

    async def update_async(self, **values: Any) -> "Model":
        """Set multiple attributes and save the model asynchronously."""
        import asyncio

        return await asyncio.to_thread(self.update, **values)

    async def increment_async(self, column: str, amount: int = 1) -> "Model":
        """Atomically increment a numeric column asynchronously."""
        import asyncio

        return await asyncio.to_thread(self.increment, column, amount)

    async def decrement_async(self, column: str, amount: int = 1) -> "Model":
        """Atomic decrement of a column asynchronously."""
        import asyncio

        return await asyncio.to_thread(self.decrement, column, amount)

    async def refresh_async(self) -> "Model":
        """Reload all attributes from the latest database state asynchronously."""
        import asyncio

        return await asyncio.to_thread(self.refresh)

    async def force_delete_async(self) -> None:
        """Permanently delete, bypassing soft delete, asynchronously."""
        import asyncio

        await asyncio.to_thread(self.force_delete)

    async def restore_async(self) -> None:
        """Un-delete a soft-deleted row asynchronously."""
        import asyncio

        await asyncio.to_thread(self.restore)

    async def attach_async(self, relation_name: str, ids: Any) -> None:
        """Insert pivot rows linking self to target ids asynchronously."""
        import asyncio

        await asyncio.to_thread(self.attach, relation_name, ids)

    async def detach_async(self, relation_name: str, ids: Any = None) -> None:
        """Remove pivot rows asynchronously. If ids is None, removes all."""
        import asyncio

        await asyncio.to_thread(self.detach, relation_name, ids)

    async def sync_async(self, relation_name: str, ids: Any) -> None:
        """Replace all pivot rows for this relation asynchronously."""
        import asyncio

        await asyncio.to_thread(self.sync, relation_name, ids)

    @classmethod
    async def count_async(cls, **kwargs: Any) -> int:
        """Return the total number of records matching criteria asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.count, **kwargs)

    @classmethod
    async def exists_async(cls, **kwargs: Any) -> bool:
        """Return True if at least one matching record exists asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.exists, **kwargs)

    @classmethod
    async def find_or_fail_async(
        cls: type[T], id: Optional[Any] = None, **kwargs: Any
    ) -> T:
        """Find a single record or raise ModelError asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.find_or_fail, id, **kwargs)

    @classmethod
    async def first_or_fail_async(cls: type[T], **kwargs: Any) -> T:
        """Return the first matching record or raise ModelError asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.first_or_fail, **kwargs)

    @classmethod
    async def search_async(
        cls: type[T], term: str, limit: int = 10, offset: int = 0
    ) -> "ModelList[T]":
        """Full-text search asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.search, term, limit, offset)

    @classmethod
    async def first_or_create_async(
        cls: type[T], defaults: Optional[dict] = None, **kwargs: Any
    ) -> T:
        """Find a row matching kwargs or create one asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.first_or_create, defaults, **kwargs)

    @classmethod
    async def update_or_create_async(
        cls: type[T], defaults: Optional[dict] = None, **kwargs: Any
    ) -> T:
        """Find + update, or create, asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.update_or_create, defaults, **kwargs)

    @classmethod
    async def raw_async(
        cls: type[T], sql: str, args: Optional[list] = None
    ) -> "ModelList[T]":
        """Execute raw SQL and return model instances asynchronously."""
        import asyncio

        return await asyncio.to_thread(cls.raw, sql, args)

    @classmethod
    async def destroy_async(cls, **kwargs: Any) -> int:
        """Delete records matching criteria asynchronously (handles soft delete)."""
        import asyncio

        return await asyncio.to_thread(cls.destroy, **kwargs)

    @classmethod
    async def force_destroy_async(cls, **kwargs: Any) -> int:
        """Permanently delete records asynchronously, bypassing soft delete."""
        import asyncio

        return await asyncio.to_thread(cls.force_destroy, **kwargs)

    @classmethod
    async def paginate_async(
        cls,
        page: int = 1,
        per_page: int = 10,
        order_by: Optional[str] = None,
        count: bool = True,
        **kwargs: Any,
    ) -> dict:
        """Paginate results matching the given criteria asynchronously."""
        import asyncio

        return await asyncio.to_thread(
            cls.paginate, page, per_page, order_by, count, **kwargs
        )


def close_all_db_connections() -> None:
    """Close all database connections held by the current thread."""
    try:
        from .engines import _ENGINES_CACHE

        for engine in list(_ENGINES_CACHE.values()):
            try:
                engine.close_connections()
            except Exception:
                pass
    except Exception:
        pass
