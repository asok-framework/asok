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
    raw = _read_http_header_block(sock)
    if raw is None:
        return None, None
    head = raw.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
    lines = head.split("\r\n")
    path = _parse_request_line(lines[0])
    if path is None:
        return None, None
    headers = _parse_header_lines(lines[1:])
    return path, headers


def _read_http_header_block(sock: socket.socket) -> Optional[bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return None
        if not chunk:
            return None
        data += chunk
        if len(data) > 16384:
            return None
    return data


def _parse_request_line(line: str) -> Optional[str]:
    try:
        method, path, _ = line.split(" ", 2)
    except ValueError:
        return None
    if method != "GET" or len(path) > 2000:
        return None
    return path


def _parse_header_lines(lines) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().lower()
        value = v.strip()
        if len(key) > 100 or len(value) > 8000:
            continue
        headers[key] = value
    return headers


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
    opcode = header[0] & 0x0F
    masked = header[1] >> 7
    length = _read_frame_length(sock, header[1] & 0x7F)
    if length is None or length > _MAX_FRAME_SIZE:
        return None, None
    payload = _read_frame_payload(sock, masked, length)
    if payload is None:
        return None, None
    return opcode, payload


def _read_frame_payload(sock: socket.socket, masked: int, length: int) -> Optional[bytes]:
    mask_key = _read_mask_key(sock, masked)
    if mask_key is None:
        return None
    payload = _recv_exact(sock, length) if length else b""
    if payload is None:
        return None
    return _unmask_payload(payload, mask_key) if masked else payload


def _read_mask_key(sock: socket.socket, masked: int) -> Optional[bytes]:
    if not masked:
        return b""
    return _recv_exact(sock, 4) or None


def _unmask_payload(payload: bytes, mask_key: bytes) -> bytes:
    return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))


def _read_frame_length(sock: socket.socket, length: int) -> Optional[int]:
    if length == 126:
        ext = _recv_exact(sock, 2)
        return struct.unpack(">H", ext)[0] if ext else None
    if length == 127:
        ext = _recv_exact(sock, 8)
        return struct.unpack(">Q", ext)[0] if ext else None
    return length


def _send_frame(sock: socket.socket, opcode: int, payload: Union[str, bytes]) -> bool:
    """Construct and send one WebSocket frame.

    SECURITY: Frame size is limited by _MAX_FRAME_SIZE in _recv_frame.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if len(payload) > _MAX_FRAME_SIZE:
        return False
    header = bytes([0x80 | opcode]) + _encode_frame_length(len(payload))
    try:
        sock.sendall(header + payload)
        return True
    except OSError:
        return False


def _encode_frame_length(n: int) -> bytes:
    if n < 126:
        return bytes([n])
    if n <= 65535:
        return bytes([126]) + struct.pack(">H", n)
    return bytes([127]) + struct.pack(">Q", n)
