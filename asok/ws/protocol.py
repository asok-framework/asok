from __future__ import annotations

import base64
import hashlib
import socket
import struct
from typing import Optional, Union

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA
_MAX_FRAME_SIZE = 1 * 1024 * 1024  # 1 MB


def _parse_cookies(header: Optional[str]) -> dict[str, str]:
    """Parse HTTP Cookie header string into a dictionary."""
    out = {}
    if not header:
        return out
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _parse_http_request(
    sock: socket.socket,
) -> tuple[Optional[str], Optional[dict[str, str]]]:
    """Read the HTTP upgrade request.

    Returns (path, headers_dict) or (None, None).
    SECURITY: Limits request header size to 16KB to prevent DoS.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return None, None
        if not chunk:
            return None, None
        data += chunk
        # SECURITY: Limit total header size to prevent memory exhaustion
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

    # SECURITY: Validate path length and format
    if len(path) > 2000:
        return None, None

    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip().lower()
            value = v.strip()
            # SECURITY: Limit header value length to prevent DoS
            if len(key) > 100 or len(value) > 8000:
                continue
            headers[key] = value
    return path, headers


def _handshake_response(client_key: str) -> bytes:
    """Generate the RFC 6455 upgrade response.

    Security: Explicitly omits CORS headers as they are ignored by standard WS
    clients and can introduce security risks if reflected. Validation is done
    via Origin header BEFORE this handshake.
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


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes from a socket, or return None on EOF/error."""
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


def _recv_frame(sock: socket.socket) -> tuple[Optional[int], Optional[bytes]]:
    """Read one WebSocket frame.

    Returns (opcode, payload) or (None, None) on close.
    """
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


def _send_frame(sock: socket.socket, opcode: int, payload: Union[str, bytes]) -> bool:
    """Construct and send one WebSocket frame.

    SECURITY: Frame size is limited by _MAX_FRAME_SIZE in _recv_frame.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    # SECURITY: Enforce max frame size on outgoing frames too
    if len(payload) > _MAX_FRAME_SIZE:
        return False

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
