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

        # Determine allowed origins
        if allowed_origins:
            self.allowed_origins = allowed_origins
        else:
            # Try env var
            env_origins = os.getenv("WS_ALLOWED_ORIGINS")
            if env_origins:
                self.allowed_origins = [o.strip() for o in env_origins.split(",")]
            elif app and app.config.get("CORS_ORIGINS"):
                self.allowed_origins = app.config["CORS_ORIGINS"]
            else:
                self.allowed_origins = None

        # Ensure localhost/127.0.0.1 are always allowed by default if not "*"
        if self.allowed_origins != "*":
            defaults = [
                "http://localhost",
                "https://localhost",
                "http://127.0.0.1",
                "https://127.0.0.1",
            ]
            # Add variants with ports
            local_variants = []
            for d in defaults:
                local_variants.append(f"{d}:{port}")
                # Always allow common development ports
                for p in [8000, 3000, 5173, 5000, 8080]:
                    local_variants.append(f"{d}:{p}")

                if app and hasattr(app, "config"):
                    # Add common dev ports if different
                    app_port = os.getenv("ASOK_PORT", "8000")
                    local_variants.append(f"{d}:{app_port}")

            if self.allowed_origins is None:
                self.allowed_origins = list(set(defaults + local_variants))
            elif isinstance(self.allowed_origins, (list, set, tuple)):
                self.allowed_origins = list(
                    set(list(self.allowed_origins) + defaults + local_variants)
                )

        self._routes: dict[str, _Route] = {}  # pattern → _Route
        self._connections: dict[str, set[Connection]] = {}
        self._conn_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._running = False

        # Internal: auto-register live component handler
        if self.app:
            self.on("/asok/live")(self.on_live_message)

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
        with self._conn_lock:
            all_conns = [c for s in self._connections.values() for c in s]
        dead = []
        for c in all_conns:
            if c is exclude:
                continue
            if room in c._rooms:
                if not c.send(message):
                    dead.append(c)
        for c in dead:
            self._remove(c)

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
        if os.environ.get("ASOK_CLI") == "true":
            return self
        if self._running:
            return self
        if self.secret_key is None:
            if self.app and hasattr(self.app, "secret_key"):
                self.secret_key = self.app.secret_key
            else:
                self.secret_key = os.getenv("SECRET_KEY")
                if not self.secret_key:
                    raise RuntimeError(
                        "SECRET_KEY is not configured. This should never happen if Asok() is properly initialized."
                    )
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.host, self.port))
        except OSError as e:
            logger.error("Failed to bind %s:%s: %s", self.host, self.port, e)
            return self
        self._sock.listen(128)
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        logger.info("WebSocket server listening on ws://%s:%s", self.host, self.port)
        return self

    def stop(self) -> None:
        """Stop the server and gracefully close all active connections."""
        self._running = False
        # Close all active connections
        with self._conn_lock:
            all_conns = [c for s in self._connections.values() for c in s]
        for conn in all_conns:
            try:
                conn.close(1001, "server shutting down")
            except Exception:
                pass
        with self._conn_lock:
            self._connections.clear()
        # Close the listening socket
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def _is_origin_allowed(self, origin: Optional[str]) -> bool:
        """Check if the given Origin header is allowed to connect.

        SECURITY: Even in debug mode, only localhost origins are allowed by
        default.
        """
        is_debug = (
            os.environ.get("DEBUG") != "false"
            and os.environ.get("ASOK_ENV") != "production"
        )

        if not self.allowed_origins or self.allowed_origins == "*":
            # SECURITY: In production, this is forbidden
            if not is_debug:
                raise RuntimeError(
                    "SECURITY ERROR: allowed_origins='*' is forbidden in production for WebSockets. "
                    "This disables CSRF protection. Please specify your domain (e.g., https://yourdomain.com)."
                )

            # SECURITY: Even in debug mode, only allow localhost origins
            if origin:
                origin_lower = origin.lower().rstrip("/")
                # Allow localhost and 127.0.0.1 with any port
                localhost_origins = [
                    "http://localhost",
                    "https://localhost",
                    "http://127.0.0.1",
                    "https://127.0.0.1",
                    "ws://localhost",
                    "wss://localhost",
                    "ws://127.0.0.1",
                    "wss://127.0.0.1",
                ]
                # Check if origin starts with any allowed localhost pattern (to allow any port)
                for allowed_origin in localhost_origins:
                    if origin_lower.startswith(allowed_origin):
                        # Verify it's just adding a port or nothing
                        remainder = origin_lower[len(allowed_origin) :]
                        if not remainder or remainder.startswith(":"):
                            logger.debug(
                                "DEBUG MODE: Allowing localhost origin: %s",
                                origin,
                            )
                            return True

                # Reject non-localhost origins even in debug
                logger.warning(
                    "⚠️  DEBUG MODE: Rejecting non-localhost origin: %s. "
                    "Only localhost/127.0.0.1 allowed when allowed_origins='*' in debug. "
                    "Set allowed_origins explicitly to allow other origins.",
                    origin,
                )
                return False

            # No origin header (non-browser client)
            return True
        if not origin:
            # Allow non-browser clients (standard behavior)
            return True

        # Normalize origin for comparison
        origin = origin.lower().rstrip("/")

        if isinstance(self.allowed_origins, str):
            allowed = [
                o.strip().lower().rstrip("/") for o in self.allowed_origins.split(",")
            ]
        else:
            allowed = [str(o).lower().rstrip("/") for o in self.allowed_origins]

        if origin in allowed:
            return True

        # Support wildcard subdomains (e.g. *.example.com)
        for pattern in allowed:
            if "*" in pattern:
                import re

                # SECURITY: Limit pattern length to prevent ReDoS
                if len(pattern) > 200:
                    continue

                # SECURITY: Use bounded quantifier to prevent catastrophic backtracking
                # Replace .* with .{1,200} to limit matching
                regex = re.escape(pattern).replace("\\*", "[a-zA-Z0-9.-]{1,200}")
                try:
                    if re.match(f"^{regex}$", origin):
                        return True
                except (re.error, RuntimeError):
                    # SECURITY: Catch regex compilation or execution errors
                    continue

        return False

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
        if not self.app or not hasattr(self.app, "_session_store"):
            return None
        cookies = _parse_cookies(headers.get("cookie", ""))
        signed = cookies.get("asok_sid")
        if not signed:
            return None
        sid = self.app._unsign(signed)
        if not sid:
            return None

        store = self.app._session_store
        data = store.load(sid)
        if data is None:
            return None
        sess = Session(data)
        sess.sid = sid
        sess.modified = False
        return sess

    def _resolve_user(self, headers: dict[str, str]) -> Optional[Model]:
        if not self.secret_key:
            return None
        cookies = _parse_cookies(headers.get("cookie", ""))
        signed = cookies.get("asok_session")
        if not signed or "." not in signed:
            return None
        try:
            val = self.app._unsign(signed)
            if not val:
                return None
            user_model = MODELS_REGISTRY.get(self.auth_model)
            if not user_model:
                return None
            return user_model.find(id=int(val))
        except Exception:
            return None

    def on_live_message(self, conn: Connection, text: str) -> None:
        """Handle Live Component updates — ops: join, call, sync."""
        on_live_message(self, conn, text)

    def _handle(self, sock: socket.socket, addr: tuple[str, int]) -> None:
        # 1. Connection limit check
        connection_accepted = False
        with self._conn_lock:
            if self._connection_count >= self.max_connections:
                logger.warning(
                    "Refused connection from %s: Max connections reached (%d)",
                    addr,
                    self.max_connections,
                )
                sock.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                sock.close()
                return
            self._connection_count += 1
            connection_accepted = True

        try:
            sock.settimeout(10)
            path, headers = _parse_http_request(sock)
            # SECURITY: Set a 5-minute read timeout for the active connection to prevent
            # inactive/zombie clients from consuming server threads indefinitely.
            # This disconnects clients that send no messages (including pings) for 5 minutes.
            sock.settimeout(300)
            if not path or not headers:
                sock.close()
                # SECURITY: Decrement connection count on early exit
                if connection_accepted:
                    with self._conn_lock:
                        self._connection_count = max(0, self._connection_count - 1)
                return
            if headers.get("upgrade", "").lower() != "websocket":
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                # SECURITY: Decrement connection count on early exit
                if connection_accepted:
                    with self._conn_lock:
                        self._connection_count = max(0, self._connection_count - 1)
                return
            route_path = path.split("?", 1)[0]
            route, params = self._match(route_path)
            if route is None:
                logger.info("404: No route matches '%s'", route_path)
                sock.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
                sock.close()
                # SECURITY: Decrement connection count on early exit
                if connection_accepted:
                    with self._conn_lock:
                        self._connection_count = max(0, self._connection_count - 1)
                return

            # Security: Origin validation
            origin = headers.get("origin")
            if not self._is_origin_allowed(origin):
                logger.warning("403: Origin '%s' forbidden for '%s'", origin, route_path)
                sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                sock.close()
                # SECURITY: Decrement connection count on early exit
                if connection_accepted:
                    with self._conn_lock:
                        self._connection_count = max(0, self._connection_count - 1)
                return

            key = headers.get("sec-websocket-key", "")
            if not key:
                logger.info("400: Missing Sec-WebSocket-Key")
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                # SECURITY: Decrement connection count on early exit
                if connection_accepted:
                    with self._conn_lock:
                        self._connection_count = max(0, self._connection_count - 1)
                return

            sock.sendall(_handshake_response(key))

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
            )
            self._register(conn)

            if route.on_connect:
                try:
                    route.on_connect(conn)
                except Exception as e:
                    logger.error(f"Error in on_connect handler: {e}", exc_info=True)

            msg_handler = route.on_message
            try:
                while self._running and not conn._closed:
                    opcode, payload = _recv_frame(sock)
                    if opcode is None or opcode == _OP_CLOSE:
                        break
                    if opcode == _OP_PING:
                        _send_frame(sock, _OP_PONG, payload)
                        continue
                    if opcode == _OP_PONG:
                        continue
                    if opcode == _OP_TEXT:
                        try:
                            text = payload.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                        if msg_handler:
                            try:
                                msg_handler(conn, text)
                            except Exception as e:
                                logger.error(
                                    f"Error in on_message handler: {e}",
                                    exc_info=True,
                                )
                    # binary frames are ignored by default
            finally:
                if route.on_disconnect:
                    try:
                        route.on_disconnect(conn)
                    except Exception as e:
                        logger.error(
                            f"Error in on_disconnect handler: {e}",
                            exc_info=True,
                        )
                self._remove(conn)
                conn.close()
        except socket.timeout:
            # Client was inactive for too long, close quietly
            try:
                sock.close()
            except OSError:
                pass
            # SECURITY: Decrement connection count on timeout
            if connection_accepted:
                with self._conn_lock:
                    self._connection_count = max(0, self._connection_count - 1)
        except Exception as e:
            logger.error(
                "Error handling WebSocket client connection: %s",
                e,
                exc_info=True,
            )
            try:
                sock.close()
            except OSError:
                pass
            # SECURITY: Decrement connection count on error
            if connection_accepted:
                with self._conn_lock:
                    self._connection_count = max(0, self._connection_count - 1)

    def _register(self, conn: Connection) -> None:
        with self._conn_lock:
            self._connections.setdefault(conn.path, set()).add(conn)

    def _remove(self, conn: Connection) -> None:
        with self._conn_lock:
            s = self._connections.get(conn.path)
            if s:
                s.discard(conn)
            self._connection_count = max(0, self._connection_count - 1)
