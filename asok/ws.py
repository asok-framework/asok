"""Minimal stdlib WebSocket server for asok (RFC 6455).

Runs in a background daemon thread alongside the WSGI server, on a separate
port. Shares session/auth with asok via the same signed cookie format, so
`conn.user` resolves to the authenticated asok user automatically.

Usage:

    from asok import Asok, WebSocketServer

    app = Asok()
    ws = WebSocketServer(port=8001)

    @ws.on_connect("/chat")
    def on_join(conn):
        name = conn.user.name if conn.user else "guest"
        ws.broadcast("/chat", f"{name} joined")

    @ws.on("/chat")
    def on_message(conn, message):
        name = conn.user.name if conn.user else "guest"
        ws.broadcast("/chat", f"{name}: {message}")

    @ws.on_disconnect("/chat")
    def on_leave(conn):
        name = conn.user.name if conn.user else "guest"
        ws.broadcast("/chat", f"{name} left")

    ws.start()  # spawns daemon threads; returns immediately

In dev, `asok dev` forks a child that imports your app, so the WS server
starts automatically alongside the HTTP server. Ctrl-C and hot-reload stop
and restart both together.

In production, run the same process behind a reverse proxy:

    # nginx
    location /ws/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
"""

from __future__ import annotations

import base64
import hashlib
import inspect as _inspect
import json
import logging
import os
import socket
import struct
import threading
import traceback
from typing import Any, Optional, Union

from .component import COMPONENTS_REGISTRY
from .core import Asok
from .orm import MODELS_REGISTRY, Model
from .session import Session

logger = logging.getLogger("asok.ws")


_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


def _parse_cookies(header):
    out = {}
    if not header:
        return out
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _parse_http_request(sock):
    """Read the HTTP upgrade request. Returns (path, headers_dict) or (None, None)."""
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return None, None
        if not chunk:
            return None, None
        data += chunk
        if len(data) > 16384:
            return None, None
    head = data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    lines = head.split("\r\n")
    try:
        method, path, _ = lines[0].split(" ", 2)
    except ValueError:
        return None, None
    if method != "GET":
        return None, None
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return path, headers


def _handshake_response(client_key):
    """Generate the RFC 6455 upgrade response.

    Security: Explicitly omits CORS headers as they are ignored by standard WS clients
    and can introduce security risks if reflected. Validation is done via Origin header
    BEFORE this handshake.
    """
    accept = base64.b64encode(
        hashlib.sha1((client_key + _WS_MAGIC).encode()).digest()
    ).decode()
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode()


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


_MAX_FRAME_SIZE = 1 * 1024 * 1024  # 1 MB


def _recv_frame(sock):
    """Read one WebSocket frame. Returns (opcode, payload) or (None, None) on close."""
    header = _recv_exact(sock, 2)
    if not header:
        return None, None
    b0, b1 = header[0], header[1]
    opcode = b0 & 0x0F
    masked = b1 >> 7
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None, None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None, None
        length = struct.unpack(">Q", ext)[0]
    if length > _MAX_FRAME_SIZE:
        return None, None
    mask_key = b""
    if masked:
        mask_key = _recv_exact(sock, 4)
        if not mask_key:
            return None, None
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None, None
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _send_frame(sock, opcode, payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n <= 65535:
        header += bytes([126]) + struct.pack(">H", n)
    else:
        header += bytes([127]) + struct.pack(">Q", n)
    try:
        sock.sendall(header + payload)
        return True
    except OSError:
        return False


class _Route:
    """One WS route. Supports [param] segments like file-system routing."""

    def __init__(self, pattern):
        self.pattern = pattern
        self.segments = [s for s in pattern.split("/") if s]
        self.is_dynamic = any(
            s.startswith("[") and s.endswith("]") for s in self.segments
        )
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None

    def match(self, path):
        """Return params dict if `path` matches this route, else None."""
        segs = [s for s in path.split("/") if s]
        if len(segs) != len(self.segments):
            return None
        params = {}
        for ps, s in zip(self.segments, segs):
            if ps.startswith("[") and ps.endswith("]"):
                params[ps[1:-1]] = s
            elif ps != s:
                return None
        return params


class Connection:
    """Represents a single active WebSocket client connection."""

    def __init__(
        self,
        sock: socket.socket,
        addr: tuple[str, int],
        path: str,
        headers: dict[str, str],
        user: Optional[Model] = None,
        session: Optional[Session] = None,
        params: Optional[dict[str, str]] = None,
    ):
        """Initialize the connection object."""
        self.sock = sock
        self.addr = addr
        self.path = path
        self.headers = headers
        self.user = user
        self.session = session
        self.params = params or {}
        self._closed = False
        self._lock = threading.Lock()
        self._rooms: set[str] = set()

    def send(self, message: Union[str, bytes]) -> bool:
        """Send a message to the client. Returns True if successful."""
        with self._lock:
            if self._closed:
                return False
            return _send_frame(self.sock, _OP_TEXT, message)

    def send_json(self, obj: Any) -> bool:
        """Serialize an object to JSON and send it to the client."""
        return self.send(json.dumps(obj))

    def join(self, room: str) -> None:
        """Join a named room for targeted broadcasting."""
        self._rooms.add(room)

    def leave(self, room: str) -> None:
        """Leave a named room."""
        self._rooms.discard(room)

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the WebSocket connection cleanly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            payload = struct.pack(">H", code) + reason.encode("utf-8")
            try:
                _send_frame(self.sock, _OP_CLOSE, payload)
            except OSError:
                pass
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass


class WebSocketServer:
    """Asynchronous WebSocket server implementing RFC 6455.

    Manage real-time connections, handle broadcasts, and coordinate with Asok apps.
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
            secret_key: Key for verifying signed cookies (defaults to env SECRET_KEY).
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

        self._routes = {}  # pattern → _Route
        self._connections = {}
        self._conn_lock = threading.Lock()
        self._sock = None
        self._running = False

        # Internal: auto-register live component handler
        if self.app:
            self.on("/asok/live")(self.on_live_message)

    # --- handler registration ---
    def _route(self, pattern):
        r = self._routes.get(pattern)
        if r is None:
            r = _Route(pattern)
            self._routes[pattern] = r
        return r

    def on(self, path):
        def wrap(fn):
            self._route(path).on_message = fn
            return fn

        return wrap

    def on_connect(self, path):
        def wrap(fn):
            self._route(path).on_connect = fn
            return fn

        return wrap

    def on_disconnect(self, path):
        def wrap(fn):
            self._route(path).on_disconnect = fn
            return fn

        return wrap

    def _match(self, path):
        """Return (route, params) for the first matching route, or (None, None).
        Static routes are tried before dynamic ones."""
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
    def broadcast(self, path, message, exclude=None):
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

    def broadcast_json(self, path, obj, exclude=None):
        return self.broadcast(path, json.dumps(obj), exclude=exclude)

    def broadcast_to(self, room, message, exclude=None):
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

    def broadcast_to_json(self, room, obj, exclude=None):
        """Send a JSON message to every connection in a room."""
        return self.broadcast_to(room, json.dumps(obj), exclude=exclude)

    def connections(self, path=None):
        with self._conn_lock:
            if path is not None:
                return list(self._connections.get(path, ()))
            return [c for s in self._connections.values() for c in s]

    # --- lifecycle ---
    def start(self):
        if self._running:
            return self
        if self.secret_key is None:
            self.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.host, self.port))
        except OSError as e:
            print(f"  [WS] Failed to bind {self.host}:{self.port}: {e}")
            return self
        self._sock.listen(128)
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        print(f"  [WS] WebSocket server listening on ws://{self.host}:{self.port}")
        return self

    def stop(self):
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

    def _is_origin_allowed(self, origin):
        """Check if the given Origin header is allowed to connect."""
        if not self.allowed_origins or self.allowed_origins == "*":
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

                regex = re.escape(pattern).replace("\\*", ".*")
                if re.match(f"^{regex}$", origin):
                    return True

        return False

    # --- internals ---
    def _accept_loop(self):
        while self._running:
            try:
                client, addr = self._sock.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(client, addr), daemon=True)
            t.start()

    def _resolve_session(self, headers):
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

    def _resolve_user(self, headers):
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

    def on_live_message(self, conn, text):
        """Handle Live Component updates — ops: join, call, sync."""
        try:
            data = json.loads(text)
            op = data.get("op")
            cid = data.get("cid")

            # ── JOIN: browser tells server which component instance just connected ──
            if op == "join":
                comp_name = data.get("name")
                state_signed = data.get("state")
                cls = COMPONENTS_REGISTRY.get(comp_name)
                if not cls:
                    return
                # Store the mapping cid → (cls, signed_state) on the connection
                if not hasattr(conn, "_live_comps"):
                    conn._live_comps = {}
                conn._live_comps[cid] = (cls, state_signed)
                return  # no re-render needed on join

            # ── CALL / SYNC: browser triggers a method or two-way bind update ──
            if op in ("call", "sync"):
                if not hasattr(conn, "_live_comps") or cid not in conn._live_comps:
                    return

                cls, state_signed = conn._live_comps[cid]
                comp = cls._from_signed_state(state_signed, self.secret_key, cid=cid)
                if not comp:
                    return

                # Inject session
                if conn.session:
                    comp._session = conn.session

                if op == "call":
                    method_name = data.get("method")
                    val = data.get("val")
                    if method_name and not method_name.startswith("_"):
                        method = getattr(comp, method_name, None)
                        # Security: only allow methods explicitly marked with @exposed
                        if callable(method) and getattr(method, "_asok_exposed", False):
                            # Pass val as arg if method accepts it
                            sig = _inspect.signature(method)
                            if len(sig.parameters) > 0:
                                method(val)
                            else:
                                method()
                        else:
                            logger.warning(
                                "Attempted to call unexposed method '%s' on component '%s'",
                                method_name,
                                comp.__class__.__name__,
                            )

                elif op == "sync":
                    prop = data.get("prop")
                    val = data.get("val")
                    if prop and not prop.startswith("_") and hasattr(comp, prop):
                        # Security: if the component declares a _bindable whitelist,
                        # only those properties may be modified via WebSocket sync.
                        bindable = getattr(comp.__class__, "_bindable", None)
                        if bindable is not None and prop not in bindable:
                            logger.warning(
                                "Blocked sync of non-bindable prop '%s' on '%s'",
                                prop,
                                comp.__class__.__name__,
                            )
                        else:
                            setattr(comp, prop, val)

                # Persist session if modified
                if conn.session and getattr(conn.session, "modified", False):
                    self.app._session_store.save(conn.session.sid, conn.session)

                # Re-render and update stored signed state
                secret = self.secret_key or os.getenv("SECRET_KEY", "dev-secret-key")
                new_state_signed = comp._sign_state(secret)
                conn._live_comps[cid] = (cls, new_state_signed)

                # Persist updated state to session so page refresh restores it
                if conn.session is not None:
                    conn.session[f"_comp_{cid}"] = new_state_signed
                    self.app._session_store.save(conn.session.sid, conn.session)

                new_html = str(comp)
                # Invalidate SPA cache so navigation shows updated state
                conn.send_json(
                    {
                        "op": "render",
                        "cid": cid,
                        "html": new_html,
                        "invalidate_cache": True,
                    }
                )

        except Exception:
            traceback.print_exc()

    def _handle(self, sock, addr):
        # 1. Connection limit check
        with self._conn_lock:
            if self._connection_count >= self.max_connections:
                print(
                    f"  [WS] Refused connection from {addr}: Max connections reached ({self.max_connections})"
                )
                sock.sendall(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")
                sock.close()
                return
            self._connection_count += 1

        try:
            sock.settimeout(10)
            path, headers = _parse_http_request(sock)
            # Security: Set a 60s read timeout for the active connection to prevent
            # inactive/zombie clients from consuming server threads indefinitely.
            sock.settimeout(60)
            if not path or not headers:
                sock.close()
                return
            if headers.get("upgrade", "").lower() != "websocket":
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return
            route_path = path.split("?", 1)[0]
            route, params = self._match(route_path)
            if route is None:
                print(f"  [WS] 404: No route matches '{route_path}'")
                sock.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
                sock.close()
                return

            # Security: Origin validation
            origin = headers.get("origin")
            if not self._is_origin_allowed(origin):
                print(f"  [WS] 403: Origin '{origin}' forbidden for '{route_path}'")
                sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                sock.close()
                return

            key = headers.get("sec-websocket-key", "")
            if not key:
                print("  [WS] 400: Missing Sec-WebSocket-Key")
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                sock.close()
                return

            sock.sendall(_handshake_response(key))

            user = self._resolve_user(headers)
            session = self._resolve_session(headers)
            conn = Connection(
                sock, addr, route_path, headers, user, session=session, params=params
            )
            self._register(conn)

            if route.on_connect:
                try:
                    route.on_connect(conn)
                except Exception:
                    traceback.print_exc()

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
                            except Exception:
                                traceback.print_exc()
                    # binary frames are ignored by default
            finally:
                if route.on_disconnect:
                    try:
                        route.on_disconnect(conn)
                    except Exception:
                        traceback.print_exc()
                self._remove(conn)
                conn.close()
        except socket.timeout:
            # Client was inactive for too long, close quietly
            try:
                sock.close()
            except OSError:
                pass
        except Exception:
            traceback.print_exc()
            try:
                sock.close()
            except OSError:
                pass

    def _register(self, conn):
        with self._conn_lock:
            self._connections.setdefault(conn.path, set()).add(conn)

    def _remove(self, conn):
        with self._conn_lock:
            s = self._connections.get(conn.path)
            if s:
                s.discard(conn)
            self._connection_count = max(0, self._connection_count - 1)
