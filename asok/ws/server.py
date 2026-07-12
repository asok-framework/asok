"""
WebSocket Server implementation for the Asok framework.

Manages connections, protocol handshake, frame parsing, routing, and channels broadcasting.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from typing import Any, Optional, Union

from ..core import Asok
from ..events import events
from ..orm import MODELS_REGISTRY, Model
from ..session import Session
from .connection import Connection, _Route
from .live import on_live_message
from .protocol import (
    _OP_CLOSE,
    _OP_PING,
    _OP_PONG,
    _OP_TEXT,
    _handshake_response,
    _parse_cookies,
    _parse_http_request,
    _recv_frame,
    _send_frame,
)

logger = logging.getLogger("asok.ws")


def _is_debug_env() -> bool:
    return (
        os.environ.get("DEBUG") != "false"
        and os.environ.get("ASOK_ENV") != "production"
    )


def _app_cors_origins(app):
    if not app:
        return None
    return app.config.get("CORS_ORIGINS")


def _matches_wildcard_origin(origin: str, pattern: str) -> bool:
    # SECURITY: bound the pattern length and use a bounded quantifier
    # to keep regex matching safe from catastrophic backtracking.
    if len(pattern) > 200:
        return False
    import re

    regex = re.escape(pattern).replace("\\*", "[a-zA-Z0-9.-]{1,200}")
    try:
        return bool(re.match(f"^{regex}$", origin))
    except (re.error, RuntimeError):
        return False


class WebSocketServer:
    """Asynchronous WebSocket server implementing RFC 6455.

    Manage real-time connections, handle broadcasts, and coordinate with Asok
    apps.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8001,
        secret_key: Optional[str] = None,
        auth_model: str = "User",
        allowed_origins: Optional[Union[str, list[str]]] = None,
        max_connections: int = 1000,
        app: Optional[Asok] = None,
    ):
        """Initialize the WebSocket server.

        Args:
            host: Interface to bind to.
            port: Port to listen on.
            secret_key: Key for verifying signed cookies (defaults to env
              SECRET_KEY).
            auth_model: Model name for authentication.
            allowed_origins: List of allowed Origin headers for CORS validation.
                **Security**: Never use ``"*"`` in production — it disables
                Origin checks and allows any website to open WebSocket
                connections to your server using your users' cookies.
            max_connections: Maximum number of simultaneous clients.
            app: Optional Asok application instance for shared state/session.
        """
        self.host = host
        self.port = port
        self.secret_key = secret_key
        self.auth_model = auth_model
        self.max_connections = max_connections
        self._connection_count = 0
        self.app = app
        self.allowed_origins = self._resolve_initial_origins(allowed_origins, app)
        self._extend_origins_with_localhost(app, port)

        self._routes: dict[str, _Route] = {}  # pattern → _Route
        self._connections: dict[str, set[Connection]] = {}
        self._conn_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._running = False
        self.presence_counts: dict[int, int] = {}
        self._room_authorizer = None

        # Internal: auto-register live component handler
        if self.app:
            self.on("/asok/live")(self.on_live_message)

        # GraphQL WebSocket Subscriptions registration
        try:
            from ..api.graphql import on_graphql_ws_message, setup_graphql_subscriptions

            self.on("/graphql")(on_graphql_ws_message)
            setup_graphql_subscriptions(self)
        except ImportError:
            pass

        # Real-time Model Bridge
        def _relay_model_event(event_name: str, model_obj: Any) -> None:
            # Broadcast to rooms: model:Name, model:Name:id
            model_name = model_obj.__class__.__name__
            msg = json.dumps(
                {
                    "op": "model_event",
                    "event": event_name,
                    "model": model_name,
                    "id": getattr(model_obj, "id", None),
                }
            )
            self.broadcast_to(f"model:{model_name}", msg)
            if hasattr(model_obj, "id") and model_obj.id:
                self.broadcast_to(f"model:{model_name}:{model_obj.id}", msg)

        events.on("model:created", lambda obj: _relay_model_event("created", obj))
        events.on("model:updated", lambda obj: _relay_model_event("updated", obj))
        events.on("model:deleted", lambda obj: _relay_model_event("deleted", obj))

    # --- handler registration ---
    def _route(self, pattern: str) -> _Route:
        r = self._routes.get(pattern)
        if r is None:
            r = _Route(pattern)
            self._routes[pattern] = r
        return r

    def on(self, path: str):
        """Decorator to register a message handler for a specific path."""

        def wrap(fn):
            self._route(path).on_message = fn
            return fn

        return wrap

    def on_connect(self, path: str):
        """Decorator to register a connection handler for a specific path."""

        def wrap(fn):
            self._route(path).on_connect = fn
            return fn

        return wrap

    def on_disconnect(self, path: str):
        """Decorator to register a disconnection handler for a specific path."""

        def wrap(fn):
            self._route(path).on_disconnect = fn
            return fn

        return wrap

    def room_authorizer(self, fn):
        """Decorator to register a custom room authorization function."""
        self._room_authorizer = fn
        return fn

    def check_room_authorization(self, conn: Connection, room: str) -> bool:
        """Check if a connection is authorized to join a room."""
        if self._room_authorizer:
            try:
                return bool(self._room_authorizer(conn, room))
            except Exception as e:
                logger.error(f"Error in room authorizer: {e}", exc_info=True)
                return False
        # By default, without a custom authorizer, only "model:" rooms
        # (which possess their own internal validation mechanism) are authorized.
        return room.startswith("model:")

    def get_online_users(self) -> list[int]:
        """Return list of online user IDs."""
        with self._conn_lock:
            return list(self.presence_counts.keys())

    def is_user_online(self, user_id: int) -> bool:
        """Return True if the user is online."""
        with self._conn_lock:
            return self.presence_counts.get(user_id, 0) > 0

    def _match(self, path: str) -> tuple[Optional[_Route], Optional[dict[str, str]]]:
        """Return (route, params) for the first matching route, or (None, None).

        Static routes are tried before dynamic ones.
        """
        r = self._routes.get(path)
        if r is not None:
            return r, {}
        for r in self._routes.values():
            if not r.is_dynamic:
                continue
            params = r.match(path)
            if params is not None:
                return r, params
        return None, None

    # --- broadcast / introspection ---
    def broadcast(
        self,
        path: str,
        message: Union[str, bytes],
        exclude: Optional[Connection] = None,
    ) -> None:
        """Send a text message to every connection on `path`."""
        with self._conn_lock:
            conns = list(self._connections.get(path, ()))
        dead = []
        for c in conns:
            if c is exclude:
                continue
            if not c.send(message):
                dead.append(c)
        for c in dead:
            self._remove(c)

    def broadcast_json(
        self,
        path: str,
        obj: Any,
        exclude: Optional[Connection] = None,
    ) -> None:
        """Send a JSON-encoded message to every connection on `path`."""
        return self.broadcast(path, json.dumps(obj), exclude=exclude)

    def broadcast_to(
        self,
        room: str,
        message: Union[str, bytes],
        exclude: Optional[Connection] = None,
    ) -> None:
        """Send a text message to every connection that has joined `room`."""
        all_conns = self._all_connections()
        dead = [
            c
            for c in all_conns
            if self._should_deliver_to_room(c, room, exclude) and not c.send(message)
        ]
        for c in dead:
            self._remove(c)

    @staticmethod
    def _should_deliver_to_room(conn, room: str, exclude) -> bool:
        if conn is exclude:
            return False
        return hasattr(conn, "_rooms") and room in conn._rooms

    def _all_connections(self) -> list[Connection]:
        with self._conn_lock:
            return [c for s in self._connections.values() for c in s]

    def broadcast_to_json(
        self,
        room: str,
        obj: Any,
        exclude: Optional[Connection] = None,
    ) -> None:
        """Send a JSON message to every connection in a room."""
        return self.broadcast_to(room, json.dumps(obj), exclude=exclude)

    def connections(self, path: Optional[str] = None) -> list[Connection]:
        """Return a list of all currently active WebSocket connections,

        optionally filtered by path.
        """
        with self._conn_lock:
            if path is not None:
                return list(self._connections.get(path, ()))
            return [c for s in self._connections.values() for c in s]

    # --- lifecycle ---
    def start(self) -> WebSocketServer:
        """Start the WebSocket server in a background thread."""
        if os.environ.get("ASOK_CLI") == "true" or self._running:
            return self
        self._ensure_secret_key()
        if not self._bind_listening_socket():
            return self
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        logger.info("WebSocket server listening on ws://%s:%s", self.host, self.port)
        return self

    def _ensure_secret_key(self) -> None:
        if self.secret_key is not None:
            return
        self.secret_key = self._get_secret_key_from_app_or_env()
        if not self.secret_key:
            raise RuntimeError(
                "SECRET_KEY is not configured. This should never happen if Asok() "
                "is properly initialized."
            )

    def _get_secret_key_from_app_or_env(self) -> Optional[str]:
        if not self.app:
            return os.getenv("SECRET_KEY")
        key = getattr(self.app, "secret_key", None)
        if key:
            return key
        config = getattr(self.app, "config", {})
        return config.get("SECRET_KEY") or os.getenv("SECRET_KEY")

    def _bind_listening_socket(self) -> bool:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.host, self.port))
        except OSError as e:
            logger.error("Failed to bind %s:%s: %s", self.host, self.port, e)
            return False
        self._sock.listen(128)
        return True

    def stop(self) -> None:
        """Stop the server and gracefully close all active connections."""
        self._running = False
        for conn in self._all_connections():
            self._close_connection_safe(conn)
        with self._conn_lock:
            self._connections.clear()
        # Close the listening socket
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    @staticmethod
    def _close_connection_safe(conn) -> None:
        try:
            conn.close(1001, "server shutting down")
        except Exception:
            pass

    _DEFAULT_LOCAL_ORIGINS = (
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
    )
    _COMMON_DEV_PORTS = (8000, 3000, 5173, 5000, 8080)

    @staticmethod
    def _resolve_initial_origins(allowed_origins, app):
        if allowed_origins:
            return allowed_origins
        env_origins = os.getenv("WS_ALLOWED_ORIGINS")
        if env_origins:
            return [o.strip() for o in env_origins.split(",")]
        return _app_cors_origins(app)

    def _extend_origins_with_localhost(self, app, port: int) -> None:
        if self.allowed_origins == "*":
            return
        defaults = list(self._DEFAULT_LOCAL_ORIGINS)
        local_variants = self._build_local_variants(defaults, app, port)
        if self.allowed_origins is None:
            self.allowed_origins = list(set(defaults + local_variants))
            return
        if isinstance(self.allowed_origins, (list, set, tuple)):
            self.allowed_origins = list(
                set(list(self.allowed_origins) + defaults + local_variants)
            )

    def _build_local_variants(self, defaults, app, port: int) -> list[str]:
        variants: list[str] = []
        app_port = (
            os.getenv("ASOK_PORT", "8000") if app and hasattr(app, "config") else None
        )
        for d in defaults:
            self._append_origin_variants(variants, d, port, app_port)
        return variants

    def _append_origin_variants(
        self, variants: list[str], d: str, port: int, app_port
    ) -> None:
        variants.append(f"{d}:{port}")
        variants.extend(f"{d}:{p}" for p in self._COMMON_DEV_PORTS)
        if app_port:
            variants.append(f"{d}:{app_port}")

    _LOCALHOST_ORIGIN_PREFIXES = (
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
        "ws://localhost",
        "wss://localhost",
        "ws://127.0.0.1",
        "wss://127.0.0.1",
    )

    def _is_origin_allowed(self, origin: Optional[str]) -> bool:
        """Check if the given Origin header is allowed to connect.

        SECURITY: even in debug mode, only localhost origins pass when no
        explicit allow-list is set.
        """
        if self._is_wildcard_allowed():
            return self._origin_allowed_wildcard(origin)
        if not origin:
            return True
        return self._origin_in_allowlist(origin)

    def _is_wildcard_allowed(self) -> bool:
        return not self.allowed_origins or self.allowed_origins == "*"

    def _origin_allowed_wildcard(self, origin: Optional[str]) -> bool:
        if not _is_debug_env():
            raise RuntimeError(
                "SECURITY ERROR: allowed_origins='*' is forbidden in production for "
                "WebSockets. This disables CSRF protection. Please specify your domain "
                "(e.g., https://yourdomain.com)."
            )
        if not origin:
            return True
        return self._origin_is_localhost(origin)

    def _origin_is_localhost(self, origin: str) -> bool:
        origin_lower = origin.lower().rstrip("/")
        for prefix in self._LOCALHOST_ORIGIN_PREFIXES:
            if not origin_lower.startswith(prefix):
                continue
            remainder = origin_lower[len(prefix) :]
            if not remainder or remainder.startswith(":"):
                logger.debug("DEBUG MODE: Allowing localhost origin: %s", origin)
                return True
        logger.warning(
            "DEBUG MODE: Rejecting non-localhost origin: %s. "
            "Only localhost/127.0.0.1 allowed when allowed_origins='*' in debug. "
            "Set allowed_origins explicitly to allow other origins.",
            origin,
        )
        return False

    def _origin_in_allowlist(self, origin: str) -> bool:
        origin = origin.lower().rstrip("/")
        allowed = self._normalize_allowlist()
        if origin in allowed:
            return True
        return any(_matches_wildcard_origin(origin, p) for p in allowed if "*" in p)

    def _normalize_allowlist(self) -> list[str]:
        if isinstance(self.allowed_origins, str):
            return [
                o.strip().lower().rstrip("/") for o in self.allowed_origins.split(",")
            ]
        return [str(o).lower().rstrip("/") for o in self.allowed_origins]

    # --- internals ---
    def _accept_loop(self) -> None:
        while self._running:
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(client, addr), daemon=True)
            t.start()

    def _resolve_session(self, headers: dict[str, str]) -> Optional[Session]:
        """Resolve the session object from cookies."""
        sid = self._resolve_session_id(headers)
        if sid is None:
            return None
        data = self.app._session_store.load(sid)
        if data is None:
            return None
        sess = Session(data)
        sess.sid = sid
        sess.modified = False
        return sess

    def _resolve_session_id(self, headers: dict[str, str]) -> Optional[str]:
        if not self.app or not hasattr(self.app, "_session_store"):
            return None
        cookies = _parse_cookies(headers.get("cookie", ""))
        signed = cookies.get("asok_sid")
        if not signed:
            return None
        sid = self.app._unsign(signed)
        return sid or None

    def _resolve_user(self, headers: dict[str, str]) -> Optional[Model]:
        if not self.secret_key:
            return None
        signed = self._extract_session_cookie(headers)
        if signed is None:
            return None
        try:
            return self._load_user_from_signed(signed)
        except Exception:
            return None

    @staticmethod
    def _extract_session_cookie(headers: dict[str, str]) -> Optional[str]:
        cookies = _parse_cookies(headers.get("cookie", ""))
        signed = cookies.get("asok_session")
        if not signed or "." not in signed:
            return None
        return signed

    def _load_user_from_signed(self, signed: str) -> Optional[Model]:
        val = self.app._unsign(signed)
        if not val:
            return None
        user_id = self._resolve_user_id_from_val(val)
        if user_id is None:
            return None
        user_model = MODELS_REGISTRY.get(self.auth_model)
        if not user_model:
            return None
        return user_model.find(id=user_id)

    def _resolve_user_id_from_val(self, val: str) -> Optional[int]:
        if ":" in val:
            return self._resolve_session_user_id(val)
        is_prod = self.app and not self.app.config.get("DEBUG")
        if is_prod:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    def _resolve_session_user_id(self, val: str) -> Optional[int]:
        parts = val.split(":", 1)
        uid_str, sid = parts[0], parts[1]
        try:
            uid = int(uid_str)
        except ValueError:
            return None
        return self._check_session_user(uid, sid)

    def _check_session_user(self, uid: int, sid: str) -> Optional[int]:
        if not (self.app and hasattr(self.app, "_session_store")):
            return None
        sess_data = self.app._session_store.load(sid)
        if sess_data and sess_data.get("user_id") == uid:
            return uid
        return None

    def on_live_message(self, conn: Connection, text: str) -> None:
        """Handle Live Component updates — ops: join, call, sync."""
        on_live_message(self, conn, text)

    def _handle(self, sock: socket.socket, addr: tuple[str, int]) -> None:
        if not self._accept_connection_slot(sock, addr):
            return
        try:
            self._handle_client_socket(sock, addr)
        except socket.timeout:
            self._close_quietly(sock)
            self._release_connection_slot()
        except Exception as e:
            logger.error(
                "Error handling WebSocket client connection: %s",
                e,
                exc_info=True,
            )
            self._close_quietly(sock)
            self._release_connection_slot()

    def _accept_connection_slot(self, sock: socket.socket, addr) -> bool:
        with self._conn_lock:
            if self._connection_count >= self.max_connections:
                logger.warning(
                    "Refused connection from %s: Max connections reached (%d)",
                    addr,
                    self.max_connections,
                )
                sock.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                sock.close()
                return False
            self._connection_count += 1
        return True

    def _release_connection_slot(self) -> None:
        with self._conn_lock:
            self._connection_count = max(0, self._connection_count - 1)

    @staticmethod
    def _close_quietly(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass

    def _handle_client_socket(self, sock: socket.socket, addr) -> None:
        sock.settimeout(10)
        path, headers = _parse_http_request(sock)
        # SECURITY: 5-minute read timeout on the live connection prevents zombie
        # clients from holding worker threads forever.
        sock.settimeout(300)
        if not self._handshake_preconditions(sock, path, headers):
            self._release_connection_slot()
            return
        route_info = self._authorize_connection(sock, path, headers)
        if route_info is None:
            self._release_connection_slot()
            return
        route, route_path, params = route_info
        sock.sendall(_handshake_response(headers["sec-websocket-key"]))
        conn = self._build_and_register_connection(
            sock, addr, route_path, headers, params, route
        )
        try:
            self._drive_message_loop(sock, conn, route.on_message)
        finally:
            self._fire_on_disconnect(route, conn)
            self._remove(conn)
            conn.close()

    def _handshake_preconditions(self, sock, path, headers) -> bool:
        if not path or not headers:
            sock.close()
            return False
        if headers.get("upgrade", "").lower() != "websocket":
            sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            sock.close()
            return False
        return True

    def _authorize_connection(self, sock, path, headers):
        route_path = path.split("?", 1)[0]
        route, params = self._match(route_path)
        if route is None:
            logger.info("404: No route matches '%s'", route_path)
            sock.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
            sock.close()
            return None
        origin = headers.get("origin")
        if not self._is_origin_allowed(origin):
            logger.warning("403: Origin '%s' forbidden for '%s'", origin, route_path)
            sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            sock.close()
            return None
        if not headers.get("sec-websocket-key", ""):
            logger.info("400: Missing Sec-WebSocket-Key")
            sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            sock.close()
            return None
        return route, route_path, params

    def _build_and_register_connection(
        self,
        sock,
        addr,
        route_path,
        headers,
        params,
        route,
    ):
        user = self._resolve_user(headers)
        session = self._resolve_session(headers)
        conn = Connection(
            sock,
            addr,
            route_path,
            headers,
            user,
            session=session,
            params=params,
            server=self,
        )
        try:
            from .live import _build_pseudo_request

            conn.request = _build_pseudo_request(self, conn)
        except Exception:
            pass
        self._register(conn)
        self._fire_on_connect(route, conn)
        return conn

    @staticmethod
    def _fire_on_connect(route, conn) -> None:
        if not route.on_connect:
            return
        try:
            route.on_connect(conn)
        except Exception as e:
            logger.error(f"Error in on_connect handler: {e}", exc_info=True)

    @staticmethod
    def _fire_on_disconnect(route, conn) -> None:
        if not route.on_disconnect:
            return
        try:
            route.on_disconnect(conn)
        except Exception as e:
            logger.error(f"Error in on_disconnect handler: {e}", exc_info=True)

    def _drive_message_loop(self, sock, conn, msg_handler) -> None:
        while self._running and not conn._closed:
            opcode, payload = _recv_frame(sock)
            if self._handle_frame(sock, conn, msg_handler, opcode, payload):
                return

    def _handle_frame(self, sock, conn, msg_handler, opcode, payload) -> bool:
        if opcode is None or opcode == _OP_CLOSE:
            return True
        handler = _FRAME_HANDLERS.get(opcode)
        if handler is not None:
            handler(self, sock, conn, msg_handler, payload)
        return False

    @staticmethod
    def _dispatch_text_frame(conn, payload, msg_handler) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return
        if not msg_handler:
            return
        try:
            msg_handler(conn, text)
        except Exception as e:
            logger.error(f"Error in on_message handler: {e}", exc_info=True)

    def _register(self, conn: Connection) -> None:
        with self._conn_lock:
            self._connections.setdefault(conn.path, set()).add(conn)
        if conn.user and hasattr(conn.user, "id"):
            user_id = conn.user.id
            with self._conn_lock:
                old_count = self.presence_counts.get(user_id, 0)
                self.presence_counts[user_id] = old_count + 1
                is_first = old_count == 0
            if is_first:
                payload = {
                    "op": "broadcast",
                    "type": "presence",
                    "user_id": user_id,
                    "status": "online",
                }
                for p in list(self._connections.keys()):
                    self.broadcast_json(p, payload)

    def _remove(self, conn: Connection) -> None:
        self._drop_connection(conn)
        if not (conn.user and hasattr(conn.user, "id")):
            return
        user_id = conn.user.id
        if self._decrement_presence(user_id):
            self._broadcast_presence(user_id, "offline")

    def _drop_connection(self, conn: Connection) -> None:
        with self._conn_lock:
            s = self._connections.get(conn.path)
            if s:
                s.discard(conn)
            self._connection_count = max(0, self._connection_count - 1)

    def _decrement_presence(self, user_id) -> bool:
        with self._conn_lock:
            old_count = self.presence_counts.get(user_id, 0)
            new_count = max(0, old_count - 1)
            if new_count == 0:
                self.presence_counts.pop(user_id, None)
            else:
                self.presence_counts[user_id] = new_count
        return old_count > 0 and new_count == 0

    def _broadcast_presence(self, user_id, status: str) -> None:
        payload = {
            "op": "broadcast",
            "type": "presence",
            "user_id": user_id,
            "status": status,
        }
        for p in list(self._connections.keys()):
            self.broadcast_json(p, payload)


def _frame_ping(server, sock, conn, msg_handler, payload):
    _send_frame(sock, _OP_PONG, payload)


def _frame_pong(server, sock, conn, msg_handler, payload):
    return


def _frame_text(server, sock, conn, msg_handler, payload):
    server._dispatch_text_frame(conn, payload, msg_handler)


_FRAME_HANDLERS = {
    _OP_PING: _frame_ping,
    _OP_PONG: _frame_pong,
    _OP_TEXT: _frame_text,
}
