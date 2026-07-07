"""
Tests for API Versioning and Native GraphQL Server features.
Covers:
- API Versioning (URL version routing, Accept header versioning, Custom Header versioning).
- Deprecation and Sunset response headers.
- GraphQL query parsing, auto-generated schema, object relationships.
- GraphQL mutations (create, update, delete).
- GraphQL query complexity analysis.
- GraphQL subscriptions over WebSockets.
"""

import json
from typing import Any

import pytest

from asok.api.graphql import on_graphql_ws_message
from asok.orm import Field, Model, Relation
from asok.testing import TestClient
from asok.ws import WebSocketServer

# ── Define Test Models ──────────────────────────────────


class QLUser(Model):
    name = Field.String()
    email = Field.String()
    posts = Relation.HasMany("QLPost", foreign_key="author_id")


class QLPost(Model):
    title = Field.String()
    author_id = Field.Integer()
    author = Relation.BelongsTo("QLUser", foreign_key="author_id")


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    QLUser.close_connections()
    QLPost.close_connections()
    monkeypatch.setattr(QLUser, "_db_path", db_path)
    monkeypatch.setattr(QLPost, "_db_path", db_path)
    QLUser.create_table()
    QLPost.create_table()


# ── Mock WebSocket Connection ───────────────────────────


class MockWebsocketConn:
    def __init__(self, server: Any):
        self.server = server
        self.sent_messages = []
        self.graphql_subscriptions = {}
        self.path = "/graphql"
        self.closed = False

    def send_json(self, data: dict):
        self.sent_messages.append(data)

    def close(self, code: int = 1000, reason: str = ""):
        self.closed = True


# ── Test Cases ──────────────────────────────────────────


def test_api_versioning_and_headers(fresh_app, tmp_path):
    # Setup pages directory
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    fresh_app.root_dir = str(tmp_path)
    fresh_app.dirs["PAGES"] = "pages"

    # Write v1 users controller
    api_v1 = pages_dir / "api" / "v1"
    api_v1.mkdir(parents=True)
    with open(api_v1 / "users.py", "w") as f:
        f.write(
            """
__api_deprecated__ = True
__api_sunset__ = "2026-12-31T00:00:00Z"
def get(request):
    return request.json({"version": "v1"})
"""
        )

    # Write v2 users controller
    api_v2 = pages_dir / "api" / "v2"
    api_v2.mkdir(parents=True)
    with open(api_v2 / "users.py", "w") as f:
        f.write(
            """
def get(request):
    return request.json({"version": "v2"})
"""
        )

    # Write inline versioned controller
    with open(pages_dir / "api" / "inline.py", "w") as f:
        f.write(
            """
from asok.api.versioning import api_version, versioned_response

@api_version("v1", deprecated=True, sunset="2026-10-10T00:00:00Z")
def get_v1(request):
    return request.json({"inline": "v1"})

def get_v2(request):
    return request.json({"inline": "v2"})

def get(request):
    return versioned_response(request, {
        "v1": get_v1,
        "v2": get_v2
    }, default="v2")
"""
        )

    client = TestClient(fresh_app)

    # 1. URL Versioning
    res = client.get("/api/v1/users")
    assert res.status_code == 200
    assert res.json["version"] == "v1"
    assert res.headers.get("Deprecation") == "true"
    assert "Sunset" in res.headers

    # 2. X-API-Version Header Versioning
    res = client.get("/api/users", headers={"X-API-Version": "v1"})
    assert res.status_code == 200
    assert res.json["version"] == "v1"
    assert res.headers.get("Deprecation") == "true"

    # 3. Accept Header Versioning
    res = client.get("/api/users", headers={"Accept": "application/vnd.asok.v2+json"})
    assert res.status_code == 200
    assert res.json["version"] == "v2"
    assert "Deprecation" not in res.headers

    # 4. Inline versioned response - v1
    res = client.get("/api/inline", headers={"X-API-Version": "v1"})
    assert res.status_code == 200
    assert res.json["inline"] == "v1"
    assert res.headers.get("Deprecation") == "true"
    assert "Sunset" in res.headers

    # 5. Inline versioned response - default (v2)
    res = client.get("/api/inline")
    assert res.status_code == 200
    assert res.json["inline"] == "v2"
    assert "Deprecation" not in res.headers


def test_graphql_playground_in_dev(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True
    client = TestClient(fresh_app)

    # GET in production should fail
    fresh_app.config["DEBUG"] = False
    res = client.get("/graphql")
    assert res.status_code == 405

    # GET in development should return HTML playground
    fresh_app.config["DEBUG"] = True
    res = client.get("/graphql")
    assert res.status_code == 200
    assert "Asok GraphQL Explorer" in res.text


def test_graphql_disabled_by_default(fresh_app):
    """GraphQL endpoint must NOT be exposed unless explicitly enabled."""
    client = TestClient(fresh_app)
    fresh_app.config["DEBUG"] = True
    res = client.get("/graphql")
    assert res.status_code == 404
    res = client.post("/graphql", json_body={"query": "{ __typename }"})
    assert res.status_code == 404


def test_graphql_queries_and_mutations(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True
    fresh_app.config["GRAPHQL_MAX_COMPLEXITY"] = 1000
    fresh_app.config["GRAPHQL_ALLOW_UNAUTHENTICATED_MUTATIONS"] = True
    client = TestClient(fresh_app)

    # Create test user
    user = QLUser.query().create(name="Alice", email="alice@example.com")
    QLPost.query().create(title="Hello World", author_id=user.id)
    QLPost.query().create(title="Second Post", author_id=user.id)

    # 1. Fetch user + posts relationships
    query = """
    query {
      qlusers {
        id
        name
        posts {
          id
          title
        }
      }
    }
    """
    res = client.post("/graphql", json_body={"query": query})
    assert res.status_code == 200
    data = res.json["data"]
    assert len(data["qlusers"]) == 1
    assert data["qlusers"][0]["name"] == "Alice"
    assert len(data["qlusers"][0]["posts"]) == 2
    assert data["qlusers"][0]["posts"][0]["title"] == "Hello World"

    # 2. Fetch single user by ID
    query_single = f"""
    query {{
      qluser(id: {user.id}) {{
        name
      }}
    }}
    """
    res = client.post("/graphql", json_body={"query": query_single})
    assert res.status_code == 200
    assert res.json["data"]["qluser"]["name"] == "Alice"

    # 3. Create mutation
    mutation_create = """
    mutation {
      createQLUser(name: "Bob", email: "bob@example.com") {
        id
        name
      }
    }
    """
    res = client.post("/graphql", json_body={"query": mutation_create})
    assert res.status_code == 200
    assert res.json["data"]["createQLUser"]["name"] == "Bob"
    assert QLUser.find(name="Bob") is not None

    # 4. Update mutation
    bob = QLUser.find(name="Bob")
    mutation_update = f"""
    mutation {{
      updateQLUser(id: {bob.id}, name: "Bobby") {{
        name
      }}
    }}
    """
    res = client.post("/graphql", json_body={"query": mutation_update})
    assert res.status_code == 200
    assert res.json["data"]["updateQLUser"]["name"] == "Bobby"

    # 5. Delete mutation
    mutation_delete = f"""
    mutation {{
      deleteQLUser(id: {bob.id})
    }}
    """
    res = client.post("/graphql", json_body={"query": mutation_delete})
    assert res.status_code == 200
    assert res.json["data"]["deleteQLUser"] is True
    assert QLUser.find(id=bob.id) is None


def test_graphql_query_complexity(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True
    fresh_app.config["GRAPHQL_MAX_COMPLEXITY"] = 200
    client = TestClient(fresh_app)

    query = """
    query {
      qlusers {
        name
        posts {
          title
        }
      }
    }
    """
    # Allowed under limit of 200
    res = client.post("/graphql", json_body={"query": query})
    assert res.status_code == 200
    assert "errors" not in res.json

    # Lower complexity threshold to block query
    fresh_app.config["GRAPHQL_MAX_COMPLEXITY"] = 10
    # Complexity is: 1 (qlusers) + (10 (default limit) * (1 (name) + 1 (posts) + (10 (default posts limit) * 1 (title)))) = 121
    res = client.post("/graphql", json_body={"query": query})
    assert res.status_code == 200  # GraphQL errors always return 200 per the spec
    assert "errors" in res.json
    assert "complexity" in res.json["errors"][0]["message"]


def test_graphql_subscriptions(fresh_app):
    server = WebSocketServer(app=fresh_app)
    conn = MockWebsocketConn(server)

    # Register the connection on the WebSocket server
    server._connections["/graphql"] = {conn}

    # Connection Ack
    on_graphql_ws_message(conn, json.dumps({"type": "connection_init"}))
    assert conn.sent_messages[-1]["type"] == "connection_ack"

    # Subscribe to qluserCreated events
    on_graphql_ws_message(
        conn,
        json.dumps(
            {
                "type": "subscribe",
                "id": "sub-id-123",
                "payload": {"query": "subscription { qluserCreated { id name } }"},
            }
        ),
    )

    # Emit new QLUser event via ORM create
    QLUser.query().create(name="Charlie", email="charlie@example.com")

    # Connection should receive payload message
    msg = conn.sent_messages[-1]
    assert msg["type"] == "next"
    assert msg["id"] == "sub-id-123"
    assert msg["payload"]["data"]["qluserCreated"]["name"] == "Charlie"


def test_graphql_query_depth_limit(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True
    fresh_app.config["GRAPHQL_MAX_DEPTH"] = 15
    client = TestClient(fresh_app)

    # Construct a deeply nested query (depth > 15)
    nested_part = "posts { author { " * 10
    closing_braces = "} } " * 10
    query = f"query {{ qlusers {{ {nested_part} id {closing_braces} }} }}"

    res = client.post("/graphql", json_body={"query": query})
    assert res.status_code == 200  # GraphQL errors always return 200 per the spec
    assert "errors" in res.json
    assert "depth" in res.json["errors"][0]["message"].lower()


def test_graphql_authorize_http_hook(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True

    # Block all requests
    fresh_app.config["GRAPHQL_AUTHORIZE"] = lambda req: False
    client = TestClient(fresh_app)

    res = client.post("/graphql", json_body={"query": "{ qlusers { id } }"})
    assert res.status_code == 403
    assert res.json["errors"][0]["message"] == "Unauthorized GraphQL access"

    # Allow all requests
    fresh_app.config["GRAPHQL_AUTHORIZE"] = lambda req: True
    res = client.post("/graphql", json_body={"query": "{ qlusers { id } }"})
    assert res.status_code == 200
    assert "errors" not in res.json


def test_graphql_authorize_ws_hook(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True
    fresh_app.config["GRAPHQL_AUTHORIZE"] = lambda req: False

    server = WebSocketServer(app=fresh_app)
    conn = MockWebsocketConn(server)

    from asok.request import Request

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/graphql",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    conn.request = Request(environ)

    server._connections["/graphql"] = {conn}

    on_graphql_ws_message(conn, json.dumps({"type": "connection_init"}))

    assert len(conn.sent_messages) == 1
    assert conn.sent_messages[0]["type"] == "connection_error"
    assert conn.sent_messages[0]["payload"]["message"] == "Unauthorized GraphQL access"
    assert conn.closed is True


def test_graphql_disable_introspection(fresh_app):
    fresh_app.config["GRAPHQL_ENABLED"] = True
    client = TestClient(fresh_app)

    # 1. By default, in DEBUG=True mode, introspection is allowed
    fresh_app.config["DEBUG"] = True
    fresh_app.config["GRAPHQL_DISABLE_INTROSPECTION"] = None
    res = client.post(
        "/graphql", json_body={"query": "{ __schema { types { name } } }"}
    )
    assert res.status_code == 200
    assert "errors" not in res.json
    assert "__schema" in res.json["data"]

    # 2. In non-DEBUG mode, introspection is disabled by default
    fresh_app.config["DEBUG"] = False
    res = client.post(
        "/graphql", json_body={"query": "{ __schema { types { name } } }"}
    )
    assert res.status_code == 200
    assert "errors" in res.json
    assert "disabled" in res.json["errors"][0]["message"].lower()

    # 3. Can explicitly disable introspection even in DEBUG mode
    fresh_app.config["DEBUG"] = True
    fresh_app.config["GRAPHQL_DISABLE_INTROSPECTION"] = True
    res = client.post(
        "/graphql", json_body={"query": "{ __schema { types { name } } }"}
    )
    assert res.status_code == 200
    assert "errors" in res.json

    # 4. Can explicitly enable introspection even in production
    fresh_app.config["DEBUG"] = False
    fresh_app.config["GRAPHQL_DISABLE_INTROSPECTION"] = False
    res = client.post(
        "/graphql", json_body={"query": "{ __schema { types { name } } }"}
    )
    assert res.status_code == 200
    assert "errors" not in res.json

    # 5. Queries like __typename are not blocked even when introspection is disabled
    fresh_app.config["GRAPHQL_DISABLE_INTROSPECTION"] = True
    res = client.post("/graphql", json_body={"query": "{ __typename }"})
    assert res.status_code == 200
    assert "errors" not in res.json
