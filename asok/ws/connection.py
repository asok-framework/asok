"""
WebSocket Connection wrapper and routing matching classes for Asok.

Defines the Route representation and the active Connection object,
managing thread-safe frame writing and channels subscription.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any, Optional, Union

from ..orm import Model
from ..session import Session
from .protocol import _OP_CLOSE, _OP_TEXT, _send_frame


def _is_valid_room_name(room) -> bool:
    # SECURITY: bounded length + safe charset to keep room names sanitised.
    if not _is_short_string(room):
        return False
    return all(c.isalnum() or c in "._:-" for c in room)


def _is_short_string(value, limit: int = 200) -> bool:
    return bool(value) and isinstance(value, str) and len(value) <= limit


class _Route:
    """One WS route. Supports [param] segments like file-system routing."""

    def __init__(self, pattern: str):
        """Initialize the WS route with a route pattern string."""
        self.pattern = pattern
        self.segments = _split_path_segments(pattern)
        self.is_dynamic = any(_is_dynamic_segment(s) for s in self.segments)
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None

    def match(self, path: str) -> Optional[dict[str, str]]:
        """Return params dict if `path` matches this route, else None."""
        segs = _split_path_segments(path)
        if len(segs) != len(self.segments):
            return None
        params: dict[str, str] = {}
        if not _match_all_segments(self.segments, segs, params):
            return None
        return params


def _split_path_segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _match_all_segments(segments, segs, params) -> bool:
    for ps, s in zip(segments, segs):
        if not _match_one_segment(ps, s, params):
            return False
    return True


def _match_one_segment(ps: str, s: str, params: dict[str, str]) -> bool:
    if _is_dynamic_segment(ps):
        params[ps[1:-1]] = s
        return True
    return ps == s


def _is_dynamic_segment(ps: str) -> bool:
    return ps.startswith("[") and ps.endswith("]")


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
        server: Optional[Any] = None,
    ):
        """Initialize the connection object."""
        self.sock = sock
        self.addr = addr
        self.path = path
        self.headers = headers
        self.user = user
        self.session = session
        self.params = params or {}
        self.server = server
        self._closed = False
        self._lock = threading.Lock()
        self._rooms: set[str] = set()
        self._live_comps: dict[str, Any] = {}

    def send(self, message: Union[str, bytes]) -> bool:
        """Send a message to the client. Returns True if successful."""
        with self._lock:
            if self._closed:
                return False
            return _send_frame(self.sock, _OP_TEXT, message)

    def send_json(self, obj: Any) -> bool:
        """Serialize an object to JSON and send it to the client."""
        return self.send(json.dumps(obj))

    def join(self, room: str) -> bool:
        """Join a named room for targeted broadcasting.

        SECURITY: Room names are validated to prevent injection and DoS attacks.
        """
        if not _is_valid_room_name(room):
            return False
        if not self._authorize_room(room):
            return False
        self._rooms.add(room)
        return True

    def _authorize_room(self, room: str) -> bool:
        if not (self.server and hasattr(self.server, "check_room_authorization")):
            return True
        return self.server.check_room_authorization(self, room)

    def leave(self, room: str) -> None:
        """Leave a named room.

        SECURITY: Room names are validated to prevent injection attacks.
        """
        # SECURITY: Validate room name format and length
        if not room or not isinstance(room, str):
            return
        if len(room) > 200:
            return
        self._rooms.discard(room)

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the WebSocket connection cleanly.

        SECURITY: Reason is limited to 123 bytes (RFC 6455 limit for close frame).
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # SECURITY: RFC 6455 limits close reason to max 123 bytes (125 - 2 for code)
            reason_bytes = reason.encode("utf-8")[:123]
            payload = struct.pack(">H", code) + reason_bytes
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
