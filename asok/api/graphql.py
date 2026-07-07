from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from ..orm.utils import MODELS_REGISTRY
from ..request import Request

logger = logging.getLogger("asok.graphql")


# ── Tokenizer ───────────────────────────────────────────


def tokenize(source: str) -> list[str]:
    """Tokenize a GraphQL query string."""
    source = re.sub(r"#.*", "", source)
    token_specification = [
        ("SPREAD", r"\.\.\."),  # fragment spread '...' – must be before NUMBER
        ("NUMBER", r"-?\d+(?:\.\d+)?"),
        ("STRING", r'"[^"\\]*(?:\\.[^"\\]*)*"'),
        ("NAME", r"[a-zA-Z_][a-zA-Z0-9_]*"),
        ("PUNCT", r"[{}():,!]"),
        ("VAR", r"\$[a-zA-Z_][a-zA-Z0-9_]*"),
        ("SKIP", r"[ \t\r\n]+"),
    ]
    tok_regex = "|".join(f"(?P<{p[0]}>{p[1]})" for p in token_specification)
    tokens = []
    for mo in re.finditer(tok_regex, source):
        kind = mo.lastgroup
        value = mo.group()
        if kind == "SKIP":
            continue
        tokens.append(value)
    return tokens


# ── AST Representation & Parser ────────────────────────


class GraphQLField:
    """Represents a requested field node in a GraphQL query AST."""

    def __init__(self, name: str):
        self.name = name
        self.alias: Optional[str] = None
        self.arguments: dict[str, Any] = {}
        self.selections: list[GraphQLField] = []


class GraphQLParser:
    """Recursive-descent parser for GraphQL query syntax with full fragment support."""

    def __init__(
        self,
        tokens: list[str],
        variables: Optional[dict[str, Any]] = None,
        max_depth: int = 20,
    ):
        self.tokens = tokens
        self.variables = variables or {}
        self.pos = 0
        self.max_depth = max_depth
        self.current_depth = 0
        # Named fragment definitions collected during pre-scan
        self.fragments: dict[str, list[GraphQLField]] = {}

    def peek(self) -> Optional[str]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, expected: Optional[str] = None) -> str:
        token = self.peek()
        if token is None:
            raise SyntaxError("Unexpected end of input")
        if expected is not None and token != expected:
            raise SyntaxError(f"Expected '{expected}', got '{token}'")
        self.pos += 1
        return token

    def _parse_variable_or_string(self, token: str) -> tuple[bool, Any]:
        if token.startswith("$"):
            self.consume()
            return True, self.variables.get(token[1:])
        if token.startswith('"') and token.endswith('"'):
            self.consume()
            return True, token[1:-1]
        return False, None

    def _parse_scalar_fallback(self, token: str) -> Any:
        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        return token

    def parse_value(self) -> Any:
        token = self.peek()
        if not token:
            raise SyntaxError("Unexpected end of input")

        is_parsed, val = self._parse_variable_or_string(token)
        if is_parsed:
            return val

        token = self.consume()
        try:
            if "." in token:
                return float(token)
            return int(token)
        except ValueError:
            return self._parse_scalar_fallback(token)

    def parse_arguments(self) -> dict[str, Any]:
        args = {}
        self.consume("(")
        while self.peek() != ")":
            name = self.consume()
            self.consume(":")
            val = self.parse_value()
            args[name] = val
            if self.peek() == ",":
                self.consume(",")
        self.consume(")")
        return args

    def parse_selection_set(self) -> list[GraphQLField]:
        self.current_depth += 1
        if self.current_depth > self.max_depth:
            raise ValueError(
                f"Max GraphQL query depth exceeded (limit: {self.max_depth})"
            )
        selections = []
        self.consume("{")
        while self.peek() not in ("}", None):
            selections.append(self.parse_field())
        self.consume("}")
        self.current_depth -= 1
        return selections

    def _parse_fragment_field(self) -> GraphQLField:
        self.consume()  # consume '...'
        if self.peek() == "on":
            self.consume()  # consume 'on'
            self.consume()  # consume type condition name (e.g. __Type)
            inline_fields = self.parse_selection_set()
            placeholder = GraphQLField("__inline_fragment__")
            placeholder.selections = inline_fields
            return placeholder
        frag_name = self.consume()
        return GraphQLField(f"...{frag_name}")

    def parse_field(self) -> GraphQLField:
        if self.peek() == "...":
            return self._parse_fragment_field()

        name_or_alias = self.consume()
        name = name_or_alias
        alias = None
        if self.peek() == ":":
            self.consume(":")
            alias = name_or_alias
            name = self.consume()

        field = GraphQLField(name)
        field.alias = alias

        if self.peek() == "(":
            field.arguments = self.parse_arguments()

        if self.peek() == "{":
            field.selections = self.parse_selection_set()

        return field

    def _skip_variable_definitions_loop(self) -> None:
        depth = 1
        while depth > 0 and self.peek() is not None:
            tok = self.consume()
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1

    def _skip_variable_definitions(self) -> None:
        if self.peek() == "(":
            self.consume("(")
            self._skip_variable_definitions_loop()

    def _skip_definition_header(self) -> None:
        if self.peek() not in ("query", "mutation", "subscription"):
            return
        self.consume()
        if self.peek() not in (None, "{", "("):
            self.consume()  # operation name
        self._skip_variable_definitions()

    # ── Fragment support ─────────────────────────────────

    def _skip_tokens_before_body(self, i: int) -> int:
        if i < len(self.tokens) and self.tokens[i] == "on":
            i += 1
        if i < len(self.tokens) and self.tokens[i] not in ("{", "}", "..."):
            i += 1
        return i

    def _run_fragment_parser(self, frag_name: str, i: int) -> int:
        saved_pos = self.pos
        saved_depth = self.current_depth
        self.pos = i
        self.current_depth = 0
        try:
            self.fragments[frag_name] = self.parse_selection_set()
            return self.pos
        except Exception:
            return i + 1
        finally:
            self.pos = saved_pos
            self.current_depth = saved_depth

    def _parse_single_collected_fragment(self, i: int) -> int:
        i += 1  # skip 'fragment'
        if i >= len(self.tokens):
            return i
        frag_name = self.tokens[i]
        i = self._skip_tokens_before_body(i + 1)
        if i < len(self.tokens) and self.tokens[i] == "{":
            return self._run_fragment_parser(frag_name, i)
        return i + 1

    def _collect_fragments(self) -> None:
        """Pre-scan the full token list to collect all named fragment definitions.

        Fragment definitions appear after the main operation in the query string.
        We collect them first so that fragment spreads inside selections can be
        expanded after the main parse pass.
        """
        i = 0
        while i < len(self.tokens):
            if self.tokens[i] != "fragment":
                i += 1
            else:
                i = self._parse_single_collected_fragment(i)

    def _expand_single_field(
        self, f: GraphQLField, visited: frozenset, result: list[GraphQLField]
    ) -> None:
        if f.name == "__inline_fragment__":
            result.extend(self._expand_fragments(f.selections, visited))
        elif f.name.startswith("..."):
            frag_name = f.name[3:]
            if frag_name not in visited:
                frag_fields = self.fragments.get(frag_name, [])
                result.extend(
                    self._expand_fragments(frag_fields, visited | {frag_name})
                )
        else:
            f.selections = self._expand_fragments(f.selections, visited)
            result.append(f)

    def _expand_fragments(
        self,
        fields: list[GraphQLField],
        _visited: Optional[frozenset] = None,
    ) -> list[GraphQLField]:
        """Recursively inline named fragment spreads and inline fragment placeholders.

        A cycle-detection set (_visited) prevents infinite recursion if two
        fragments reference each other.
        """
        visited = frozenset() if _visited is None else _visited
        result: list[GraphQLField] = []
        for f in fields:
            self._expand_single_field(f, visited, result)
        return result

    def parse(self) -> list[GraphQLField]:
        # 1. Pre-collect all fragment definitions from the full token stream.
        self._collect_fragments()
        # 2. Parse the main operation.
        self._skip_definition_header()
        fields = self.parse_selection_set()
        # 3. Inline all fragment spreads.
        return self._expand_fragments(fields)


# ── Query Complexity Analysis ──────────────────────────


def check_complexity(fields: list[GraphQLField], max_complexity: int = 100) -> int:
    """Calculate query complexity and raise error if it exceeds the limit."""

    def get_field_type_and_is_list(
        parent_model: Optional[type], field_name: str
    ) -> tuple[Optional[type], bool]:
        if parent_model is None:
            # Root level queries/mutations
            for model_name, model_cls in MODELS_REGISTRY.items():
                plural_name = model_cls._table
                if field_name in (
                    plural_name,
                    plural_name.lower(),
                    model_name.lower() + "s",
                ):
                    return model_cls, True
                if field_name in (model_name, model_name.lower()):
                    return model_cls, False
            return None, False

        # Model instance level fields/relations
        if (
            hasattr(parent_model, "_relations")
            and field_name in parent_model._relations
        ):
            rel = parent_model._relations[field_name]
            target_model = MODELS_REGISTRY.get(rel.target_model_name)
            is_list = rel.type in ("HasMany", "BelongsToMany", "MorphMany")
            return target_model, is_list

        if hasattr(parent_model, "_fields"):
            fk_name = f"{field_name}_id"
            if fk_name in parent_model._fields:
                fk_field = parent_model._fields[fk_name]
                if hasattr(fk_field, "related_model"):
                    return fk_field.related_model, False

        return None, False

    def calculate(fields_list: list[GraphQLField], parent_model: Optional[type]) -> int:
        score = 0
        for f in fields_list:
            field_score = 1
            target_model, is_list = get_field_type_and_is_list(parent_model, f.name)

            limit = f.arguments.get("limit") or f.arguments.get("first") or 10

            if f.selections:
                sub_score = calculate(f.selections, target_model)
                if is_list:
                    field_score += sub_score * int(limit)
                else:
                    field_score += sub_score
            score += field_score
        return score

    score = calculate(fields, None)
    if score > max_complexity:
        raise ValueError(
            f"Query complexity of {score} exceeds maximum allowed complexity of {max_complexity}."
        )
    return score


# ── GraphQL Introspection ─────────────────────────────────

# Map ORM field class names → GraphQL scalar type names
_ORM_TO_GQL: dict[str, str] = {
    "IntField": "Int",
    "FloatField": "Float",
    "BooleanField": "Boolean",
    "CharField": "String",
    "TextField": "String",
    "DateField": "String",
    "DateTimeField": "String",
    "EmailField": "String",
    "SlugField": "String",
    "URLField": "String",
    "JSONField": "String",
    "FileField": "String",
    "ImageField": "String",
    "UUIDField": "String",
    "ColorField": "String",
    "MonthField": "String",
    "Base64Field": "String",
    "ForeignKey": "ID",
}


def _type_ref(kind: str, name: Optional[str] = None, of_type: Any = None) -> dict:
    return {"kind": kind, "name": name, "ofType": of_type}


def _scalar_ref(name: str) -> dict:
    return _type_ref("SCALAR", name)


def _object_ref(name: str) -> dict:
    return _type_ref("OBJECT", name)


def _list_ref(inner: dict) -> dict:
    return _type_ref("LIST", of_type=inner)


def _non_null_ref(inner: dict) -> dict:
    return _type_ref("NON_NULL", of_type=inner)


def _orm_field_to_type_ref(field_obj: Any) -> dict:
    scalar = _ORM_TO_GQL.get(field_obj.__class__.__name__, "String")
    return _scalar_ref(scalar)


def _full_type(
    kind: str,
    name: str,
    description: Optional[str] = None,
    fields: Any = None,
    input_fields: Any = None,
    interfaces: Any = None,
    enum_values: Any = None,
    possible_types: Any = None,
) -> dict:
    return {
        "kind": kind,
        "name": name,
        "description": description,
        "fields": fields,
        "inputFields": input_fields,
        "interfaces": interfaces if interfaces is not None else [],
        "enumValues": enum_values,
        "possibleTypes": possible_types,
    }


def _field_def(
    name: str,
    type_ref: dict,
    description: Optional[str] = None,
    args: Optional[list] = None,
) -> dict:
    return {
        "name": name,
        "description": description,
        "type": type_ref,
        "args": args or [],
        "isDeprecated": False,
        "deprecationReason": None,
    }


def _input_value(name: str, type_ref: dict, default: Any = None) -> dict:
    return {
        "name": name,
        "description": None,
        "type": type_ref,
        "defaultValue": default,
    }


def _add_model_type_fields(model_cls: Any, fields: list) -> None:
    if hasattr(model_cls, "_fields"):
        for fname, fobj in model_cls._fields.items():
            if not (
                getattr(fobj, "is_password", False) or getattr(fobj, "hidden", False)
            ):
                fields.append(_field_def(fname, _orm_field_to_type_ref(fobj)))


def _add_model_type_relations(model_cls: Any, fields: list) -> None:
    if hasattr(model_cls, "_relations"):
        for rname, robj in model_cls._relations.items():
            target = robj.target_model_name
            is_list = robj.type in ("HasMany", "BelongsToMany", "MorphMany")
            ref = _list_ref(_object_ref(target)) if is_list else _object_ref(target)
            fields.append(_field_def(rname, ref))


def _build_model_type(model_name: str, model_cls: Any) -> dict:
    """Build a GraphQL OBJECT type definition for an ORM model."""
    fields = [_field_def("id", _scalar_ref("ID"), "Primary key")]
    _add_model_type_fields(model_cls, fields)
    _add_model_type_relations(model_cls, fields)
    return _full_type("OBJECT", model_name, fields=fields)


def _build_schema_types() -> list:
    """Build the full list of GraphQL types for the introspection __schema.types field."""
    types: list[dict] = []
    # Built-in scalars
    for sname in ("String", "Boolean", "Int", "Float", "ID"):
        types.append(_full_type("SCALAR", sname))

    q_fields: list[dict] = []
    m_fields: list[dict] = []
    for mname, mcls in MODELS_REGISTRY.items():
        table = mcls._table
        low = mname.lower()
        # Plural list query
        q_fields.append(
            _field_def(
                table,
                _list_ref(_object_ref(mname)),
                f"List {mname} records",
                args=[
                    _input_value("limit", _scalar_ref("Int")),
                    _input_value("offset", _scalar_ref("Int")),
                ],
            )
        )
        # Singular by-id query
        q_fields.append(
            _field_def(
                low,
                _object_ref(mname),
                f"Fetch {mname} by id",
                args=[_input_value("id", _non_null_ref(_scalar_ref("ID")))],
            )
        )
        # Mutations
        m_fields.append(
            _field_def(f"create{mname}", _object_ref(mname), f"Create {mname}")
        )
        m_fields.append(
            _field_def(
                f"update{mname}",
                _object_ref(mname),
                f"Update {mname}",
                args=[_input_value("id", _non_null_ref(_scalar_ref("ID")))],
            )
        )
        m_fields.append(
            _field_def(
                f"delete{mname}",
                _scalar_ref("Boolean"),
                f"Delete {mname}",
                args=[_input_value("id", _non_null_ref(_scalar_ref("ID")))],
            )
        )

    types.append(_full_type("OBJECT", "Query", fields=q_fields))
    types.append(_full_type("OBJECT", "Mutation", fields=m_fields))
    for mname, mcls in MODELS_REGISTRY.items():
        types.append(_build_model_type(mname, mcls))
    return types


def _extract_single_introspection_field(obj: Any, f: Any, result: dict) -> None:
    val = obj.get(f.name) if isinstance(obj, dict) else None
    if f.selections:
        result[f.alias or f.name] = _extract_introspection(val, f.selections)
    else:
        result[f.alias or f.name] = val


def _extract_introspection_list(obj: list, selections: list) -> list:
    return [_extract_introspection(item, selections) for item in obj]


def _extract_introspection(obj: Any, selections: list) -> Any:
    """Recursively extract selected fields from a plain dict produced by introspection."""
    if obj is None:
        return None
    if isinstance(obj, list):
        return _extract_introspection_list(obj, selections)
    if not selections:
        return obj
    result: dict = {}
    for f in selections:
        _extract_single_introspection_field(obj, f, result)
    return result


def _resolve_schema_introspection(selections: list) -> dict:
    """Resolve __schema introspection field."""
    schema_obj: dict = {
        "description": None,
        "queryType": {"name": "Query"},
        "mutationType": {"name": "Mutation"},
        "subscriptionType": None,
        "types": _build_schema_types(),
        "directives": [],
    }
    return _extract_introspection(schema_obj, selections)  # type: ignore[return-value]


def _resolve_type_introspection(
    type_name: Optional[str], selections: list
) -> Optional[dict]:
    """Resolve __type(name: ...) introspection field."""
    if not type_name:
        return None
    matched = next(
        (t for t in _build_schema_types() if t.get("name") == type_name), None
    )
    if matched is None:
        return None
    return _extract_introspection(matched, selections)  # type: ignore[return-value]


# ── Query Resolvers ─────────────────────────────────────


def _resolve_relation_field(app: Any, val: Any, relation: Any, f: GraphQLField) -> Any:
    if relation.type in ("HasMany", "BelongsToMany", "MorphMany"):
        items = val or []
        return [resolve_object(app, item, f.selections) for item in items]
    return resolve_object(app, val, f.selections)


def _resolve_scalar_field(app: Any, val: Any, f: GraphQLField) -> Any:
    if f.selections:
        return resolve_object(app, val, f.selections)
    if hasattr(val, "to_dict"):
        return val.to_dict()
    return val


def _get_relation(model_cls: type, name: str) -> Any | None:
    if hasattr(model_cls, "_relations"):
        return model_cls._relations.get(name)
    return None


def _is_field_protected(obj_cls: type, name: str) -> bool:
    field_obj = getattr(obj_cls, "_fields", {}).get(name)
    if field_obj:
        return bool(
            getattr(field_obj, "is_password", False)
            or getattr(field_obj, "hidden", False)
        )
    return False


def _resolve_single_field(app: Any, obj: Any, f: GraphQLField) -> Any:
    if f.name == "__typename":
        return obj.__class__.__name__

    if _is_field_protected(obj.__class__, f.name):
        return None

    val = getattr(obj, f.name, None)
    relation = _get_relation(obj.__class__, f.name)
    if relation:
        return _resolve_relation_field(app, val, relation, f)
    return _resolve_scalar_field(app, val, f)


def resolve_object(
    app: Any, obj: Any, selections: list[GraphQLField]
) -> dict[str, Any]:
    """Recursively resolve fields on an ORM model object."""
    if obj is None:
        return {}
    res = {}
    for f in selections:
        res[f.alias or f.name] = _resolve_single_field(app, obj, f)
    return res


def _is_plural_query(field_name: str, model_name: str, plural_name: str) -> bool:
    if field_name == plural_name:
        return True
    if field_name == plural_name.lower():
        return True
    return field_name == (model_name.lower() + "s")


def _is_query_arg_valid(k: str, fields: Any) -> bool:
    if k == "limit":
        return False
    if k == "offset":
        return False
    return k in fields


def _apply_pagination(query: Any, limit: Any, offset: Any) -> Any:
    if limit is not None:
        query = query.limit(int(limit))
    if offset is not None:
        query = query.offset(int(offset))
    return query


def _resolve_plural_query(app: Any, model_cls: Any, field: GraphQLField) -> Any:
    query = model_cls.query()
    raw_limit = field.arguments.get("limit")
    if raw_limit is None:
        raw_limit = getattr(app, "config", {}).get("GRAPHQL_DEFAULT_PAGE_SIZE", 100)
    query = _apply_pagination(query, raw_limit, field.arguments.get("offset"))

    for k, v in field.arguments.items():
        if _is_query_arg_valid(k, model_cls._fields):
            query = query.where(k, v)

    results = query.get()
    out = []
    for item in results:
        out.append(resolve_object(app, item, field.selections))
    return out


def _resolve_singular_query(app: Any, model_cls: Any, field: GraphQLField) -> Any:
    obj_id = field.arguments.get("id")
    if not obj_id:
        raise ValueError(f"Argument 'id' is required for query '{field.name}'")
    item = model_cls.find(id=obj_id)
    if not item:
        return None
    return resolve_object(app, item, field.selections)


def _resolve_introspection_query(field: GraphQLField) -> Any:
    if field.name == "__schema":
        return _resolve_schema_introspection(field.selections)
    if field.name == "__type":
        return _resolve_type_introspection(
            field.arguments.get("name"), field.selections
        )
    if field.name == "__typename":
        return "Query"
    return None


def _resolve_model_query(app: Any, field: GraphQLField) -> Any:
    for model_name, model_cls in MODELS_REGISTRY.items():
        plural_name = model_cls._table
        if _is_plural_query(field.name, model_name, plural_name):
            return _resolve_plural_query(app, model_cls, field)
        if field.name in (model_name, model_name.lower()):
            return _resolve_singular_query(app, model_cls, field)
    return None


def _resolve_query_field(app: Any, field: GraphQLField) -> Any:
    val = _resolve_introspection_query(field)
    if val is not None:
        return val
    val = _resolve_model_query(app, field)
    if val is not None:
        return val
    raise ValueError(f"Unknown root query field '{field.name}'")


def _is_mutation_match(
    field_name: str, prefix: str, model_name: str, low_model: str
) -> bool:
    if field_name == f"{prefix}{model_name}":
        return True
    return field_name == f"{prefix}_{low_model}"


def _filter_protected_fields(model_cls: Any, arguments: dict) -> dict:
    model_fields = getattr(model_cls, "_fields", {})
    return {
        k: v
        for k, v in arguments.items()
        if not getattr(model_fields.get(k), "protected", False)
    }


def _resolve_create_mutation(
    app: Any, model_cls: Any, field: GraphQLField, model_name: str
) -> Any:
    safe_args = _filter_protected_fields(model_cls, field.arguments)
    obj = model_cls.query().create(**safe_args)
    from ..events import events

    events.emit(f"model:{model_name}:created", obj)
    return resolve_object(app, obj, field.selections)


def _resolve_update_mutation(
    app: Any, model_cls: Any, field: GraphQLField, model_name: str
) -> Any:
    obj_id = field.arguments.get("id")
    if not obj_id:
        raise ValueError("Argument 'id' is required for update mutation")
    obj = model_cls.find(id=obj_id)
    if not obj:
        raise ValueError(f"{model_name} with id {obj_id} not found")

    update_args = _filter_protected_fields(
        model_cls, {k: v for k, v in field.arguments.items() if k != "id"}
    )

    obj.update(**update_args)
    from ..events import events

    events.emit(f"model:{model_name}:updated", obj)
    return resolve_object(app, obj, field.selections)


def _resolve_delete_mutation(
    model_cls: Any, field: GraphQLField, model_name: str
) -> bool:
    obj_id = field.arguments.get("id")
    if not obj_id:
        raise ValueError("Argument 'id' is required for delete mutation")
    obj = model_cls.find(id=obj_id)
    if not obj:
        return False
    obj.delete()
    from ..events import events

    events.emit(f"model:{model_name}:deleted", obj)
    return True


def _resolve_mutation_field(app: Any, field: GraphQLField) -> Any:
    for model_name, model_cls in MODELS_REGISTRY.items():
        low_model = model_name.lower()
        if _is_mutation_match(field.name, "create", model_name, low_model):
            return _resolve_create_mutation(app, model_cls, field, model_name)

        if _is_mutation_match(field.name, "update", model_name, low_model):
            return _resolve_update_mutation(app, model_cls, field, model_name)

        if _is_mutation_match(field.name, "delete", model_name, low_model):
            return _resolve_delete_mutation(model_cls, field, model_name)

    raise ValueError(f"Unknown root mutation field '{field.name}'")


def resolve_root_field(app: Any, field: GraphQLField, is_mutation: bool) -> Any:
    """Resolve a single root-level query or mutation field."""
    if not is_mutation:
        return _resolve_query_field(app, field)
    return _resolve_mutation_field(app, field)


_INTROSPECTION_FIELDS = frozenset(("__schema", "__type", "__typename"))


def _is_introspection_query(fields: list[GraphQLField]) -> bool:
    """Return True if every root field is a GraphQL introspection meta-field.

    Introspection queries never touch the database, so complexity limits
    must not apply to them (the standard GraphiQL introspection query scores ~179).
    """
    return bool(fields) and all(f.name in _INTROSPECTION_FIELDS for f in fields)


def _check_introspection_disabled(app: Any, fields: list[GraphQLField]) -> None:
    disable_introspection = app.config.get("GRAPHQL_DISABLE_INTROSPECTION")
    if disable_introspection is None:
        disable_introspection = not app.config.get("DEBUG", False)
    if disable_introspection and any(f.name in ("__schema", "__type") for f in fields):
        raise ValueError("GraphQL introspection is disabled.")


def _parse_and_validate_query(
    app: Any, tokens: list[str], variables: dict
) -> list[GraphQLField]:
    max_depth = app.config.get("GRAPHQL_MAX_DEPTH", 20)
    parser = GraphQLParser(tokens, variables, max_depth=max_depth)
    fields = parser.parse()

    _check_introspection_disabled(app, fields)

    if not _is_introspection_query(fields):
        max_complexity = app.config.get("GRAPHQL_MAX_COMPLEXITY", 100)
        check_complexity(fields, max_complexity)
    return fields


def _execute_fields(
    app: Any, fields: list[GraphQLField], is_mutation: bool
) -> tuple[dict, list]:
    data = {}
    errors = []
    for field in fields:
        try:
            val = resolve_root_field(app, field, is_mutation)
            data[field.alias or field.name] = val
        except Exception as e:
            logger.error("GraphQL Field Error: %s", e, exc_info=True)
            errors.append({"message": str(e)})
    return data, errors


def execute_graphql(
    app: Any, query_str: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """Execute a GraphQL query or mutation string."""
    tokens = tokenize(query_str)
    if not tokens:
        return {"errors": [{"message": "Empty query"}]}

    try:
        fields = _parse_and_validate_query(app, tokens, variables)
    except Exception as e:
        return {"errors": [{"message": str(e)}]}

    is_mutation = tokens[0] == "mutation"
    data, errors = _execute_fields(app, fields, is_mutation)

    res = {}
    if data:
        res["data"] = data
    if errors:
        res["errors"] = errors
    return res


# ── HTTP Handler & Playground ─────────────────────────


def _graphiql_assets_installed() -> bool:
    import os as _os

    static_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(__file__)), "api", "static", "graphiql"
    )
    for name in (
        "react.min.js",
        "react-dom.min.js",
        "graphiql.min.js",
        "graphiql.min.css",
    ):
        if not _os.path.isfile(_os.path.join(static_dir, name)):
            return False
    return True


def get_graphiql_html() -> str:
    """Return GraphiQL development HTML template, using local assets when available."""
    if _graphiql_assets_installed():
        css_url = "/asok-graphql/graphiql.min.css"
        react_url = "/asok-graphql/react.min.js"
        rdom_url = "/asok-graphql/react-dom.min.js"
        gql_url = "/asok-graphql/graphiql.min.js"
    else:
        css_url = "https://unpkg.com/graphiql@3.0.6/graphiql.min.css"
        react_url = "https://unpkg.com/react@18.3.1/umd/react.production.min.js"
        rdom_url = "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js"
        gql_url = "https://unpkg.com/graphiql@3.0.6/graphiql.min.js"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Asok GraphQL Explorer</title>
    <link href="{css_url}" rel="stylesheet" />
</head>
<body style="margin: 0; background: #0b0f19;">
    <div id="graphiql" style="height: 100vh;"></div>
    <script crossorigin src="{react_url}"></script>
    <script crossorigin src="{rdom_url}"></script>
    <script crossorigin src="{gql_url}"></script>
    <script>
        const fetcher = GraphiQL.createFetcher({{
            url: window.location.pathname,
        }});
        ReactDOM.render(
            React.createElement(GraphiQL, {{ fetcher: fetcher }}),
            document.getElementById('graphiql'),
        );
    </script>
</body>
</html>"""


def _handle_graphql_get(app: Any, request: Request) -> bytes:
    if app.config.get("DEBUG"):
        request.content_type = "text/html"
        return get_graphiql_html().encode("utf-8")
    request.status = "405 Method Not Allowed"
    return json.dumps(
        {"error": "GraphQL GET is only allowed in development mode"}
    ).encode("utf-8")


def _is_mutation_denied(app: Any, tokens: list[str]) -> bool:
    if tokens and tokens[0] == "mutation":
        auth_hook = app.config.get("GRAPHQL_AUTHORIZE")
        allow_unauth = app.config.get("GRAPHQL_ALLOW_UNAUTHENTICATED_MUTATIONS", False)
        return not auth_hook and not allow_unauth
    return False


def _handle_graphql_post(app: Any, request: Request) -> bytes:
    try:
        body_data = json.loads(request.body.decode("utf-8"))
    except Exception:
        request.status = "400 Bad Request"
        return json.dumps({"error": "Invalid JSON payload"}).encode("utf-8")

    query = body_data.get("query", "")
    variables = body_data.get("variables") or {}

    tokens = tokenize(query)
    if _is_mutation_denied(app, tokens):
        request.status = "403 Forbidden"
        request.content_type = "application/json"
        msg = "GraphQL mutations require authentication. Configure GRAPHQL_AUTHORIZE or set GRAPHQL_ALLOW_UNAUTHENTICATED_MUTATIONS=True."
        return json.dumps({"errors": [{"message": msg}]}).encode("utf-8")

    result = execute_graphql(app, query, variables)
    request.content_type = "application/json"
    return json.dumps(result).encode("utf-8")


def handle_graphql_request(app: Any, request: Request) -> Optional[bytes]:
    """Dispatch HTTP request to GraphiQL explorer or the GraphQL resolver."""
    auth_hook = app.config.get("GRAPHQL_AUTHORIZE")
    if auth_hook and not auth_hook(request):
        request.status = "403 Forbidden"
        request.content_type = "application/json"
        return json.dumps(
            {"errors": [{"message": "Unauthorized GraphQL access"}]}
        ).encode("utf-8")

    if request.method == "GET":
        return _handle_graphql_get(app, request)
    if request.method == "POST":
        return _handle_graphql_post(app, request)

    request.status = "405 Method Not Allowed"
    return None


# ── WebSockets Subscriptions ──────────────────────────


def _handle_subscribe(conn: Any, data: dict) -> None:
    sub_id = data.get("id")
    payload = data.get("payload", {})
    query_str = payload.get("query")
    variables = payload.get("variables", {})

    if not (sub_id and query_str):
        return

    try:
        tokens = tokenize(query_str)
        max_depth = conn.server.app.config.get("GRAPHQL_MAX_DEPTH", 20)
        parser = GraphQLParser(tokens, variables, max_depth=max_depth)
        fields = parser.parse()

        # Complexity validation check on subscription query
        max_complexity = conn.server.app.config.get("GRAPHQL_MAX_COMPLEXITY", 100)
        check_complexity(fields, max_complexity)

        if not hasattr(conn, "graphql_subscriptions"):
            conn.graphql_subscriptions = {}
        conn.graphql_subscriptions[sub_id] = (fields, variables)
    except Exception as e:
        conn.send_json(
            {
                "type": "error",
                "id": sub_id,
                "payload": [{"message": str(e)}],
            }
        )


def _handle_complete(conn: Any, data: dict) -> None:
    sub_id = data.get("id")
    if hasattr(conn, "graphql_subscriptions") and sub_id in conn.graphql_subscriptions:
        del conn.graphql_subscriptions[sub_id]


def _handle_connection_init(conn: Any) -> None:
    auth_hook = conn.server.app.config.get("GRAPHQL_AUTHORIZE")
    req = getattr(conn, "request", None)
    if auth_hook and req and not auth_hook(req):
        conn.send_json(
            {
                "type": "connection_error",
                "payload": {"message": "Unauthorized GraphQL access"},
            }
        )
        conn.close()
    else:
        conn.send_json({"type": "connection_ack"})


def _parse_graphql_ws_message(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def on_graphql_ws_message(conn: Any, text: str) -> None:
    """Handle GraphQL WS protocol messages for active client subscription queries."""
    data = _parse_graphql_ws_message(text)
    if data is None:
        return
    msg_type = data.get("type")
    if msg_type == "connection_init":
        _handle_connection_init(conn)
    elif msg_type == "subscribe":
        _handle_subscribe(conn, data)
    elif msg_type == "complete":
        _handle_complete(conn, data)


def setup_graphql_subscriptions(server: Any) -> None:
    """Setup listeners on ORM events to feed real-time pushes to WS subscribers."""
    from ..events import events

    def on_model_created(model_obj: Any) -> None:
        model_name = model_obj.__class__.__name__
        field_name = f"{model_name.lower()}Created"
        for conn in server.connections("/graphql"):
            subs = getattr(conn, "graphql_subscriptions", {})
            for sub_id, (fields, variables) in subs.items():
                for f in fields:
                    if f.name == field_name:
                        resolved = resolve_object(server.app, model_obj, f.selections)
                        conn.send_json(
                            {
                                "type": "next",
                                "id": sub_id,
                                "payload": {"data": {field_name: resolved}},
                            }
                        )

    def on_model_updated(model_obj: Any) -> None:
        model_name = model_obj.__class__.__name__
        field_name = f"{model_name.lower()}Updated"
        for conn in server.connections("/graphql"):
            subs = getattr(conn, "graphql_subscriptions", {})
            for sub_id, (fields, variables) in subs.items():
                for f in fields:
                    if f.name == field_name:
                        resolved = resolve_object(server.app, model_obj, f.selections)
                        conn.send_json(
                            {
                                "type": "next",
                                "id": sub_id,
                                "payload": {"data": {field_name: resolved}},
                            }
                        )

    def on_model_deleted(model_obj: Any) -> None:
        model_name = model_obj.__class__.__name__
        field_name = f"{model_name.lower()}Deleted"
        for conn in server.connections("/graphql"):
            subs = getattr(conn, "graphql_subscriptions", {})
            for sub_id, (fields, variables) in subs.items():
                for f in fields:
                    if f.name == field_name:
                        resolved = resolve_object(server.app, model_obj, f.selections)
                        conn.send_json(
                            {
                                "type": "next",
                                "id": sub_id,
                                "payload": {"data": {field_name: resolved}},
                            }
                        )

    events.on("model:created", on_model_created)
    events.on("model:updated", on_model_updated)
    events.on("model:deleted", on_model_deleted)
