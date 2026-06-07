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

T = TypeVar("T", bound="Model")
logger = logging.getLogger("asok.orm")

# Module-level cache for derived encryption keys.
# The SHA-256 derivation + base64 encoding is computed at most once per unique
# SECRET_KEY value, then reused across all model instances and requests.
_ENCRYPTION_KEY_CACHE: dict[str, bytes] = {}


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
        attrs["_encrypted_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_encrypted")
        ]
        # Pre-compute field type map once at class definition to avoid repeated hasattr()
        # calls in __init__ (which runs for every row returned from the database).
        _ftm = {}
        for k, v in fields.items():
            if hasattr(v, "is_file"):
                _ftm[k] = "file"
            elif hasattr(v, "is_boolean"):
                _ftm[k] = "boolean"
            elif hasattr(v, "is_json"):
                _ftm[k] = "json"
            elif hasattr(v, "is_decimal"):
                _ftm[k] = "decimal"
            elif hasattr(v, "is_enum"):
                _ftm[k] = "enum"
            elif hasattr(v, "is_vector"):
                _ftm[k] = "vector"
            elif hasattr(v, "is_encrypted"):
                _ftm[k] = "encrypted"
        attrs["_field_type_map"] = _ftm
        soft_delete_fields = [
            k for k, v in fields.items() if hasattr(v, "is_soft_delete")
        ]
        attrs["_soft_delete_field"] = (
            soft_delete_fields[0] if soft_delete_fields else None
        )
        attrs["_search_fields"] = [
            k for k, v in fields.items() if getattr(v, "searchable", False)
        ]
        # Inherit and setup global scopes
        scopes = {}
        for base in bases:
            if hasattr(base, "_global_scopes"):
                scopes.update(base._global_scopes)
        if "_global_scopes" in attrs:
            scopes.update(attrs["_global_scopes"])
        if attrs["_soft_delete_field"]:
            sdf = attrs["_soft_delete_field"]
            scopes["soft_delete"] = lambda q, sdf=sdf: q.where_null(sdf)
        attrs["_global_scopes"] = scopes

        # Use explicit __tablename__ if provided, otherwise auto-pluralize
        attrs["_table"] = attrs.get("__tablename__", _pluralize(name))
        attrs["_model_name"] = name
        attrs["_conn_attr"] = f"conn_{attrs.get('_db_path') or 'db.sqlite3'}"

        relations = {k: v for k, v in attrs.items() if isinstance(v, Relation)}
        attrs["_relations"] = relations

        for k, v in fields.items():
            if hasattr(v, "is_foreign_key"):
                rel_name = k.replace("_id", "")

                def get_related(self, field_name=k, model=v.related_model):
                    val = getattr(self, field_name)
                    if not val:
                        return None
                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
                        return model.find(id=val)

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
                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
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
                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
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
                    if not val:
                        return None
                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
                        return target_model.find(id=val)

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

                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
                        engine = self.get_engine(op="read")
                        q_target = engine.quote_identifier(target_model._table)
                        q_pivot = engine.quote_identifier(pivot)
                        q_pfk = engine.quote_identifier(pfk)
                        q_pofk = engine.quote_identifier(pofk)

                        # SECURITY: Quote all table and column names to prevent SQL injection
                        sql = (
                            f"SELECT t.* FROM {q_target} t "
                            f"JOIN {q_pivot} p ON p.{q_pofk} = t.id "
                            f"WHERE p.{q_pfk} = ?"
                        )
                        rows = engine.execute(sql, (self.id,))
                        results = ModelList(
                            (target_model(**row) for row in rows),
                            sql=sql,
                            args=[self.id],
                        )
                        for r in results:
                            r._shard = getattr(self, "_shard", None)
                        return results

                attrs[k] = property(get_many_to_many)

            elif v.type == "MorphTo":

                def get_morph_to(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    fk_id = rel.foreign_key or f"{rel_name}_id"
                    fk_type = rel.owner_key or f"{rel_name}_type"

                    target_id = getattr(self, fk_id, None)
                    target_type = getattr(self, fk_type, None)
                    if not target_id or not target_type:
                        return None

                    target_model = MODELS_REGISTRY.get(target_type)
                    if not target_model:
                        return None
                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
                        return target_model.find(id=target_id)

                attrs[k] = property(get_morph_to)

            elif v.type == "MorphMany":

                def get_morph_many(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return []
                    fk_id = f"{rel.foreign_key}_id"
                    fk_type = f"{rel.foreign_key}_type"
                    from .router import database_router_context

                    with database_router_context(shard=getattr(self, "_shard", None)):
                        return (
                            target_model.where(fk_id, self.id)
                            .where(fk_type, self.__class__.__name__)
                            .get()
                        )

                attrs[k] = property(get_morph_many)

        for k in fields:
            if k in attrs and isinstance(attrs[k], Field):
                attrs.pop(k)

        # Auto-detect and generate translatable properties for fields suffix with _xx (e.g. title_fr)
        translatable_bases = set()
        for field_name in fields:
            if len(field_name) > 3 and field_name[-3] == "_":
                lang_suffix = field_name[-2:]
                if lang_suffix.isalpha() and lang_suffix.islower():
                    base_name = field_name[:-3]
                    translatable_bases.add(base_name)

        for base_name in translatable_bases:
            if base_name not in attrs:

                def make_getter(b_name=base_name):
                    def getter(self):
                        from ..context import current_request

                        try:
                            lang = current_request.lang if current_request else "en"
                        except RuntimeError:
                            lang = "en"

                        # Load default application locale
                        app_ref = None
                        if current_request and hasattr(current_request, "environ"):
                            app_ref = current_request.environ.get("asok.app")
                        default_lang = (
                            app_ref.config.get("LOCALE") if app_ref else "en"
                        ) or "en"

                        # 1. Try active language specific field (e.g. title_fr, title_en)
                        target_field = f"{b_name}_{lang}"
                        if target_field in self._fields:
                            val = getattr(self, target_field, None)
                            if val:
                                return val

                        # 2. If the active language is the default language,
                        # or if the target field for the active language does not exist in fields,
                        # try the base field (e.g. title) from raw dictionary.
                        if lang == default_lang or target_field not in self._fields:
                            val = self.__dict__.get(b_name)
                            if val:
                                return val

                        # 3. Fallback to default application locale field if it exists
                        default_field = f"{b_name}_{default_lang}"
                        if default_field in self._fields:
                            val = getattr(self, default_field, None)
                            if val:
                                return val
                        elif default_lang == "en":
                            val = self.__dict__.get(b_name)
                            if val:
                                return val

                        # 4. Try common fallbacks (default_lang, then en, then fr)
                        for fallback in (default_lang, "en", "fr"):
                            fallback_field = f"{b_name}_{fallback}"
                            if fallback_field in self._fields:
                                val = getattr(self, fallback_field, None)
                            elif fallback == default_lang:
                                val = self.__dict__.get(b_name)
                            if val:
                                return val

                        # 5. Fallback to any defined translation field
                        for f_name in self._fields:
                            if f_name.startswith(f"{b_name}_"):
                                val = getattr(self, f_name, None)
                                if val:
                                    return val
                        return self.__dict__.get(b_name)

                    return getter

                def make_setter(b_name=base_name):
                    def setter(self, value):
                        from ..context import current_request

                        try:
                            lang = current_request.lang if current_request else "en"
                        except RuntimeError:
                            lang = "en"

                        # Load default application locale
                        app_ref = None
                        if current_request and hasattr(current_request, "environ"):
                            app_ref = current_request.environ.get("asok.app")
                        default_lang = (
                            app_ref.config.get("LOCALE") if app_ref else "en"
                        ) or "en"

                        target_field = f"{b_name}_{lang}"
                        if lang == default_lang:
                            self.__dict__[b_name] = value
                            if target_field in self._fields:
                                setattr(self, target_field, value)
                        else:
                            if target_field in self._fields:
                                setattr(self, target_field, value)
                            else:
                                self.__dict__[b_name] = value

                    return setter

                attrs[base_name] = property(make_getter(), make_setter())

        cls = super().__new__(mcs, name, bases, attrs)
        if name != "Model":
            MODELS_REGISTRY[name] = cls
        return cls


class Model(metaclass=ModelMeta):
    _db_path: str | None = (os.getenv("DATABASE_URL") or "").strip() or None
    _global_scopes: dict[str, Any] = {}

    @classmethod
    def get_engine(cls, op: str | None = None, shard: str | None = None):
        from .engines import _ENGINES_CACHE
        from .engines import get_engine as get_engine_raw
        from .router import _routing_state, resolve_primary_dsn, route_database

        if op is None:
            op = getattr(_routing_state, "op", "write")
        if shard is None:
            shard = getattr(_routing_state, "shard", None)

        primary_dsn = resolve_primary_dsn(cls, shard)
        primary_engine = _ENGINES_CACHE.get(primary_dsn)
        if (
            primary_engine
            and hasattr(primary_engine, "_local")
            and getattr(primary_engine._local, "txn_level", 0) > 0
        ):
            op = "write"

        resolved_dsn = route_database(cls, op, shard)
        return get_engine_raw(resolved_dsn)

    @classmethod
    def _get_encryption_key(cls) -> bytes:
        secret = os.getenv("SECRET_KEY")
        if not secret:
            from ..context import current_request

            app_ref = (
                current_request.environ.get("asok.app")
                if current_request and hasattr(current_request, "environ")
                else None
            )
            if app_ref:
                secret = app_ref.config.get("SECRET_KEY")

        if not secret:
            secret = "change-me-in-production-default-secret-key-for-test"

        # Return cached derived key if already computed for this secret.
        cached = _ENCRYPTION_KEY_CACHE.get(secret)
        if cached is not None:
            return cached

        import base64
        import hashlib

        derived = hashlib.sha256(secret.encode()).digest()
        key = base64.urlsafe_b64encode(derived)
        _ENCRYPTION_KEY_CACHE[secret] = key
        return key

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
                # Use pre-computed type map (built once in ModelMeta) instead of
                # repeated hasattr() calls — major performance win for large result sets.
                _ftype = self._field_type_map.get(name)
                if _ftype == "file" and not isinstance(val, FileRef):
                    val = FileRef(val, field.upload_to)
                elif _ftype == "boolean":
                    # Convert string/bool to int (0 or 1)
                    if isinstance(val, str):
                        val = 1 if val and val != "0" else 0
                    elif isinstance(val, bool):
                        val = 1 if val else 0
                    elif val:
                        val = int(bool(val))
                elif _ftype == "json" and isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception as e:
                        # Log JSON parsing errors for debugging
                        logger.debug("Failed to parse JSON field '%s': %s", name, e)
                elif _ftype == "decimal" and not isinstance(val, decimal.Decimal):
                    try:
                        val = decimal.Decimal(str(val))
                    except Exception as e:
                        # Log Decimal conversion errors for debugging
                        logger.debug(
                            "Failed to convert Decimal field '%s': %s", name, e
                        )
                elif _ftype == "enum" and not isinstance(val, enum.Enum):
                    try:
                        val = field.enum_class(val)
                    except Exception as e:
                        # Log enum conversion errors for debugging
                        logger.debug("Failed to convert Enum field '%s': %s", name, e)
                elif _ftype == "vector":
                    val = self.get_engine().deserialize_value(field, val)
                elif _ftype == "encrypted":
                    if _trust:
                        val = self._decrypt_value(val)

                # Automatic SafeString for WYSIWYG content
                if getattr(field, "wysiwyg", False) and isinstance(val, str):
                    from ..templates import SafeString

                    val = SafeString(val)

            if _trust:
                self.__dict__[name] = val
            else:
                setattr(self, name, val)

        # Handle extra fields (e.g. aggregates from GROUP BY)
        if _trust:
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
        # SECURITY: Validate table name to prevent SQL injection
        validate_sql_identifier(cls._table, "table name")

        engine = cls.get_engine(op="write")

        # Use engine-specific primary key definition
        pk_def = getattr(
            engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        if hasattr(engine, "primary_key_def"):
            pk_def = engine.primary_key_def
        f_defs = [pk_def]

        for name, f in cls._fields.items():
            # SECURITY: Validate column name to prevent SQL injection
            validate_sql_identifier(name, "column name")
            col_type = engine.get_column_type(f)
            def_str = f"{name} {col_type}"
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

        sql = f"CREATE TABLE IF NOT EXISTS {engine.quote_identifier(cls._table)} ({', '.join(f_defs)})"
        engine.execute(sql)

        # ── AUTO-MIGRATION: Add missing columns ──
        existing_cols = engine.get_table_columns(cls._table)
        for name, f in cls._fields.items():
            if name not in existing_cols:
                # SECURITY: Validate column name
                validate_sql_identifier(name, "column name")
                col_type = engine.get_column_type(f)
                def_str = f"{name} {col_type}"
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
                    engine.execute(
                        f"ALTER TABLE {engine.quote_identifier(cls._table)} ADD COLUMN {def_str}"
                    )
                except Exception as e:
                    logger.error(
                        "Failed to migrate %s (adding %s): %s", cls._table, name, e
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

                    q_pivot = engine.quote_identifier(pivot_table)
                    q_pfk = engine.quote_identifier(pivot_fk)
                    q_pofk = engine.quote_identifier(pivot_other_fk)
                    q_table = engine.quote_identifier(cls._table)
                    q_other_table = engine.quote_identifier(_pluralize(b))

                    # Create the pivot table
                    pivot_sql = f"""
                    CREATE TABLE IF NOT EXISTS {q_pivot} (
                        {q_pfk} INTEGER NOT NULL,
                        {q_pofk} INTEGER NOT NULL,
                        PRIMARY KEY ({q_pfk}, {q_pofk}),
                        FOREIGN KEY ({q_pfk}) REFERENCES {q_table}(id) ON DELETE CASCADE,
                        FOREIGN KEY ({q_pofk}) REFERENCES {q_other_table}(id) ON DELETE CASCADE
                    )
                    """
                    engine.execute(pivot_sql)

        # Create indexes for fields marked with index=True
        for field_name, field in cls._fields.items():
            if getattr(field, "index", False) and not field.unique:
                # SECURITY: Validate identifiers (field_name already validated above)
                index_name = f"idx_{cls._table}_{field_name}"
                validate_sql_identifier(index_name, "index name")

                q_index = engine.quote_identifier(index_name)
                q_table = engine.quote_identifier(cls._table)
                q_field = engine.quote_identifier(field_name)

                index_sql = f"CREATE INDEX {q_index} ON {q_table}({q_field})"

                # Check index existence or try-catch for dialect differences (like MySQL lack of IF NOT EXISTS)
                # In sqlite/postgres, we can prefix CREATE INDEX with IF NOT EXISTS.
                from .engines import MySQLEngine

                if not isinstance(engine, MySQLEngine):
                    index_sql = (
                        f"CREATE INDEX IF NOT EXISTS {q_index} ON {q_table}({q_field})"
                    )

                try:
                    engine.execute(index_sql)
                    logger.info(
                        "Created index %s on %s.%s",
                        index_name,
                        cls._table,
                        field_name,
                    )
                except Exception as e:
                    # Ignore duplicate key error for MySQL (1061) or general issues if already exists
                    if (
                        "Duplicate key name" in str(e)
                        or "already exists" in str(e)
                        or "1061" in str(e)
                    ):
                        pass
                    else:
                        logger.error(
                            "Failed to create index %s on %s.%s: %s",
                            index_name,
                            cls._table,
                            field_name,
                            e,
                        )

        # Delegate FTS and engine-specific setups
        engine.post_create_table(cls)

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

        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
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
                    values.append(engine.prepare_value(field, val))
            elif hasattr(field, "is_encrypted"):
                values.append(self._encrypt_value(val))
            else:
                values.append(engine.prepare_value(field, val))

        if self.id:
            set_str = ", ".join([f"{f} = ?" for f in fields])
            sql = f"UPDATE {self._table} SET {set_str} WHERE id = ?"
            args = values + [self.id]
        else:
            placeholders = ", ".join(["?" for _ in fields])
            sql = f"INSERT INTO {self._table} ({', '.join(fields)}) VALUES ({placeholders})"
            args = values

        try:
            engine.execute(sql, args)
        except Exception as e:
            raise engine.handle_exception(e)

        if not self.id:
            if engine.lastrowid_query:
                res_id = engine.execute(engine.lastrowid_query)
                self.id = list(res_id[0].values())[0] if res_id else None

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

        engine = cls.get_engine(op="read")
        rows = engine.execute(sql, args)
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
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        engine = cls.get_engine(op="read")
        args = []
        for k, v in kwargs.items():
            field = cls._fields.get(k)
            if field:
                v = engine.prepare_value(field, v)
            args.append(v)
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        if wheres:
            sql = f"SELECT COUNT(*) FROM {cls._table} WHERE {' AND '.join(wheres)}"
        else:
            sql = f"SELECT COUNT(*) FROM {cls._table}"
        rows = engine.execute(sql, args)
        return list(rows[0].values())[0] if rows else 0

    @classmethod
    def exists(cls, **kwargs):
        """Return True if at least one record exists matching the given criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        engine = cls.get_engine(op="read")
        args = []
        for k, v in kwargs.items():
            field = cls._fields.get(k)
            if field:
                v = engine.prepare_value(field, v)
            args.append(v)
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
        from .engines import SQLiteEngine

        is_sqlite = isinstance(engine, SQLiteEngine)

        # SECURITY: Validate and quote soft delete field name
        sd_where = ""
        if cls._soft_delete_field:
            cls._valid_column(cls._soft_delete_field)
            q_sd = engine.quote_identifier(cls._soft_delete_field)
            sd_where = f" AND t.{q_sd} IS NULL"

        # SQLite FTS5 uses prefix wildcards (term*); MySQL FULLTEXT handles this natively
        if is_sqlite and term and "*" not in term:
            term = " ".join([f"{t}*" for t in term.split() if t])

        q_table = engine.quote_identifier(cls._table)
        where_clause, search_args = engine.search_sql(
            cls._table, cls._search_fields, term
        )
        sql = f"SELECT * FROM {q_table} WHERE {where_clause}{sd_where} LIMIT ? OFFSET ?"
        all_args = search_args + [limit, offset]

        try:
            rows = engine.execute(sql, all_args)
        except Exception as e:
            logger.error("FTS search failed for %s: %s", cls._table, e)
            return ModelList()

        from .router import _routing_state

        active_shard = getattr(_routing_state, "shard", None)

        def _instantiate(row):
            obj = cls(_trust=True, **row)
            obj._shard = active_shard
            return obj

        return ModelList(
            (_instantiate(row) for row in rows),
            sql=sql,
            args=all_args,
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

        from .router import _routing_state

        op = getattr(_routing_state, "op", "write")
        shard = getattr(_routing_state, "shard", None)
        engine = cls.get_engine(op=op, shard=shard)
        rows = engine.execute(sql, args or [])

        def _instantiate(row):
            obj = cls(_trust=True, **row)
            obj._shard = shard
            return obj

        return ModelList((_instantiate(row) for row in rows), sql=sql, args=args or [])

    @classmethod
    def find(cls: type[T], **kwargs: Any) -> Optional[T]:
        """Find the first record matching simple field criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        engine = cls.get_engine(op="read")
        args = []
        for k, v in kwargs.items():
            field = cls._fields.get(k)
            if field:
                v = engine.prepare_value(field, v)
            args.append(v)
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        rows = engine.execute(sql, args)
        if rows:
            from .router import _routing_state

            active_shard = getattr(_routing_state, "shard", None)
            obj = cls(_trust=True, **rows[0])
            obj._shard = active_shard
            return obj
        return None

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
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        if not isinstance(ids, (list, tuple, set)):
            ids = [ids]
        if not ids:
            return
        ids = list(ids)
        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
        q_pivot = engine.quote_identifier(pivot)
        q_pfk = engine.quote_identifier(pfk)
        q_pofk = engine.quote_identifier(pofk)

        # Fetch all already-existing pivot rows in a single batch query instead of
        # issuing one SELECT per id (N+1 → 1 query).
        placeholders_in = ", ".join(["?"] * len(ids))
        existing_rows = engine.execute(
            f"SELECT {q_pofk} FROM {q_pivot} WHERE {q_pfk} = ? AND {q_pofk} IN ({placeholders_in})",
            [self.id] + ids,
        )
        existing_ids = {row[pofk] for row in existing_rows}

        to_insert = [tid for tid in ids if tid not in existing_ids]
        if not to_insert:
            return

        # Batch INSERT all missing rows in a single statement.
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
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        engine = self.get_engine(op="write", shard=getattr(self, "_shard", None))
        q_pivot = engine.quote_identifier(pivot)
        q_pfk = engine.quote_identifier(pfk)
        q_pofk = engine.quote_identifier(pofk)

        engine.execute(f"DELETE FROM {q_pivot} WHERE {q_pfk} = ?", (self.id,))
        if ids:
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
