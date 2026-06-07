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
    """Recursive-descent parser for GraphQL query syntax."""

    def __init__(self, tokens: list[str], variables: Optional[dict[str, Any]] = None):
        self.tokens = tokens
        self.variables = variables or {}
        self.pos = 0

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

    def parse_value(self) -> Any:
        token = self.peek()
        if token and token.startswith("$"):
            self.consume()
            var_name = token[1:]
            return self.variables.get(var_name)

        token = self.consume()
        if token.startswith('"') and token.endswith('"'):
            return token[1:-1]
        try:
            if "." in token:
                return float(token)
            return int(token)
        except ValueError:
            if token == "true":
                return True
            if token == "false":
                return False
            if token == "null":
                return None
            return token

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
        selections = []
        self.consume("{")
        while self.peek() != "}":
            selections.append(self.parse_field())
        self.consume("}")
        return selections

    def parse_field(self) -> GraphQLField:
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

    def parse(self) -> list[GraphQLField]:
        if self.peek() in ("query", "mutation", "subscription"):
            self.consume()
            if self.peek() != "{" and self.peek() not in ("(",):
                self.consume()  # name
            if self.peek() == "(":
                self.consume("(")
                while self.peek() != ")":
                    self.consume()
                self.consume(")")

        return self.parse_selection_set()


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


# ── Query Resolvers ─────────────────────────────────────


def resolve_object(
    app: Any, obj: Any, selections: list[GraphQLField]
) -> dict[str, Any]:
    """Recursively resolve fields on an ORM model object."""
    if obj is None:
        return {}
    res = {}
    for f in selections:
        if f.name == "__typename":
            res[f.alias or f.name] = obj.__class__.__name__
            continue

        val = getattr(obj, f.name, None)

        model_cls = obj.__class__
        relation = (
            model_cls._relations.get(f.name)
            if hasattr(model_cls, "_relations")
            else None
        )

        if relation:
            if relation.type in ("HasMany", "BelongsToMany", "MorphMany"):
                items = val or []
                res[f.alias or f.name] = [
                    resolve_object(app, item, f.selections) for item in items
                ]
            else:
                res[f.alias or f.name] = resolve_object(app, val, f.selections)
        else:
            if f.selections:
                res[f.alias or f.name] = resolve_object(app, val, f.selections)
            else:
                if hasattr(val, "to_dict"):
                    res[f.alias or f.name] = val.to_dict()
                else:
                    res[f.alias or f.name] = val
    return res


def resolve_root_field(app: Any, field: GraphQLField, is_mutation: bool) -> Any:
    """Resolve a single root-level query or mutation field."""
    if not is_mutation:
        for model_name, model_cls in MODELS_REGISTRY.items():
            plural_name = model_cls._table
            if field.name in (
                plural_name,
                plural_name.lower(),
                model_name.lower() + "s",
            ):
                limit = field.arguments.get("limit")
                offset = field.arguments.get("offset")

                query = model_cls.query()
                if limit is not None:
                    query = query.limit(int(limit))
                if offset is not None:
                    query = query.offset(int(offset))

                for k, v in field.arguments.items():
                    if k not in ("limit", "offset") and k in model_cls._fields:
                        query = query.where(k, v)

                results = query.get()
                return [resolve_object(app, item, field.selections) for item in results]

            if field.name in (model_name, model_name.lower()):
                obj_id = field.arguments.get("id")
                if not obj_id:
                    raise ValueError(
                        f"Argument 'id' is required for query '{field.name}'"
                    )
                item = model_cls.find(id=obj_id)
                if not item:
                    return None
                return resolve_object(app, item, field.selections)

        raise ValueError(f"Unknown root query field '{field.name}'")
    else:
        for model_name, model_cls in MODELS_REGISTRY.items():
            low_model = model_name.lower()
            if (
                field.name == f"create{model_name}"
                or field.name == f"create_{low_model}"
            ):
                obj = model_cls.query().create(**field.arguments)
                from ..events import events

                events.emit(f"model:{model_name}:created", obj)
                return resolve_object(app, obj, field.selections)

            if (
                field.name == f"update{model_name}"
                or field.name == f"update_{low_model}"
            ):
                obj_id = field.arguments.get("id")
                if not obj_id:
                    raise ValueError("Argument 'id' is required for update mutation")
                obj = model_cls.find(id=obj_id)
                if not obj:
                    raise ValueError(f"{model_name} with id {obj_id} not found")

                update_args = {k: v for k, v in field.arguments.items() if k != "id"}
                obj.update(**update_args)
                from ..events import events

                events.emit(f"model:{model_name}:updated", obj)
                return resolve_object(app, obj, field.selections)

            if (
                field.name == f"delete{model_name}"
                or field.name == f"delete_{low_model}"
            ):
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

        raise ValueError(f"Unknown root mutation field '{field.name}'")


def execute_graphql(
    app: Any, query_str: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """Execute a GraphQL query or mutation string."""
    tokens = tokenize(query_str)
    if not tokens:
        return {"errors": [{"message": "Empty query"}]}

    try:
        parser = GraphQLParser(tokens, variables)
        fields = parser.parse()

        max_complexity = app.config.get("GRAPHQL_MAX_COMPLEXITY", 100)
        check_complexity(fields, max_complexity)
    except Exception as e:
        return {"errors": [{"message": str(e)}]}

    data = {}
    errors = []

    is_mutation = tokens[0] == "mutation"

    for field in fields:
        try:
            val = resolve_root_field(app, field, is_mutation)
            data[field.alias or field.name] = val
        except Exception as e:
            logger.error("GraphQL Field Error: %s", e, exc_info=True)
            errors.append({"message": str(e)})

    res = {}
    if data:
        res["data"] = data
    if errors:
        res["errors"] = errors
    return res


# ── HTTP Handler & Playground ─────────────────────────


def get_graphiql_html() -> str:
    """Return GraphiQL development HTML template."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Asok GraphQL Explorer</title>
    <link href="https://unpkg.com/graphiql/graphiql.min.css" rel="stylesheet" />
</head>
<body style="margin: 0; overflow: hidden; background: #0b0f19;">
    <div id="graphiql" style="height: 100vh;"></div>
    <script crossorigin src="https://unpkg.com/react/umd/react.production.min.js"></script>
    <script crossorigin src="https://unpkg.com/react-dom/umd/react-dom.production.min.js"></script>
    <script crossorigin src="https://unpkg.com/graphiql/graphiql.min.js"></script>
    <script>
        const fetcher = GraphiQL.createFetcher({
            url: window.location.pathname,
            wsClient: new GraphiQL.SubscriptionClient(
                (window.location.protocol === 'https:' ? 'wss://' : 'ws://') + window.location.host + window.location.pathname
            )
        });
        ReactDOM.render(
            React.createElement(GraphiQL, { fetcher: fetcher }),
            document.getElementById('graphiql'),
        );
    </script>
</body>
</html>"""


def handle_graphql_request(app: Any, request: Request) -> Optional[bytes]:
    """Dispatch HTTP request to GraphiQL explorer or the GraphQL resolver."""
    if request.method == "GET":
        if app.config.get("DEBUG"):
            request.content_type = "text/html"
            return get_graphiql_html().encode("utf-8")
        else:
            request.status = "405 Method Not Allowed"
            return json.dumps(
                {"error": "GraphQL GET is only allowed in development mode"}
            ).encode("utf-8")

    if request.method == "POST":
        try:
            body_data = json.loads(request.body.decode("utf-8"))
        except Exception:
            request.status = "400 Bad Request"
            return json.dumps({"error": "Invalid JSON payload"}).encode("utf-8")

        query = body_data.get("query", "")
        variables = body_data.get("variables", {})

        result = execute_graphql(app, query, variables)
        if "errors" in result and not result.get("data"):
            request.status = "400 Bad Request"
        request.content_type = "application/json"
        return json.dumps(result).encode("utf-8")

    request.status = "405 Method Not Allowed"
    return None


# ── WebSockets Subscriptions ──────────────────────────


def on_graphql_ws_message(conn: Any, text: str) -> None:
    """Handle GraphQL WS protocol messages for active client subscription queries."""
    try:
        data = json.loads(text)
    except Exception:
        return

    msg_type = data.get("type")
    if msg_type == "connection_init":
        conn.send_json({"type": "connection_ack"})
    elif msg_type == "subscribe":
        sub_id = data.get("id")
        payload = data.get("payload", {})
        query_str = payload.get("query")
        variables = payload.get("variables", {})

        if sub_id and query_str:
            try:
                tokens = tokenize(query_str)
                parser = GraphQLParser(tokens, variables)
                fields = parser.parse()

                # Complexity validation check on subscription query
                max_complexity = conn.server.app.config.get(
                    "GRAPHQL_MAX_COMPLEXITY", 100
                )
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
    elif msg_type == "complete":
        sub_id = data.get("id")
        if (
            hasattr(conn, "graphql_subscriptions")
            and sub_id in conn.graphql_subscriptions
        ):
            del conn.graphql_subscriptions[sub_id]


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
