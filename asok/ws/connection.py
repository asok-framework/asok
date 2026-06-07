from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any, Optional, Union

from ..orm import Model
from ..session import Session
from .protocol import _OP_CLOSE, _OP_TEXT, _send_frame


class _Route:
    """One WS route. Supports [param] segments like file-system routing."""

    def __init__(self, pattern: str):
        self.pattern = pattern
        self.segments = [s for s in pattern.split("/") if s]
        self.is_dynamic = any(
            s.startswith("[") and s.endswith("]") for s in self.segments
        )
        self.on_message = None
        self.on_connect = None
        self.on_disconnect = None

    def match(self, path: str) -> Optional[dict[str, str]]:
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
        # SECURITY: Validate room name format and length
        if not room or not isinstance(room, str):
            return False
        if len(room) > 200:
            return False
        # SECURITY: Only allow safe characters in room names
        if not all(c.isalnum() or c in "._:-" for c in room):
            return False

        # Run room authorization check if server hook is present
        if self.server and hasattr(self.server, "check_room_authorization"):
            if not self.server.check_room_authorization(self, room):
                return False

        self._rooms.add(room)
        return True

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
