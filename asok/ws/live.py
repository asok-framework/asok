from __future__ import annotations

import inspect
import json
import logging
import os
import re
from typing import Any, Callable

from ..component import COMPONENTS_REGISTRY
from ..utils.minify import minify_js

logger = logging.getLogger("asok.ws")


def on_live_message(server: Any, conn: Any, text: str) -> None:
    """Handle Live Component updates — ops: join, call, sync, etc.

    SECURITY: payload size capped to prevent DoS via giant JSON.
    """
    try:
        data = _decode_message(text)
        if data is None:
            return
        op, cid = _validate_envelope(data)
        if op is None:
            return
        handler = _OP_HANDLERS.get(op)
        if handler is not None:
            handler(server, conn, data, cid)
    except Exception as e:
        logger.error(f"Error handling live message: {e}", exc_info=True)


# ── Envelope validation ────────────────────────────────────────────


def _decode_message(text: str):
    # SECURITY: reject >1MB messages outright.
    if len(text) > 1_000_000:
        logger.warning("Rejected oversized live message: %d bytes", len(text))
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError) as e:
        logger.warning("Invalid JSON in live message: %s", e)
        return None


def _validate_envelope(data: dict):
    op = data.get("op")
    cid = data.get("cid")
    if not _is_valid_op(op):
        return None, None
    if not _is_valid_cid(cid):
        return None, None
    return op, cid


def _is_valid_op(op) -> bool:
    if isinstance(op, str) and len(op) <= 50:
        return True
    logger.warning("Invalid operation format in live message")
    return False


def _is_valid_cid(cid) -> bool:
    if cid is None:
        return True
    if isinstance(cid, str) and len(cid) <= 100:
        return True
    logger.warning("Invalid component ID format in live message")
    return False


# ── Room join ──────────────────────────────────────────────────────


def _handle_join_room(server, conn, data, cid) -> None:
    room = data.get("room")
    if not _is_valid_room_payload(conn, room):
        return
    if not conn.join(room):
        _send_room_join_error(conn, room)


def _is_valid_room_payload(conn, room) -> bool:
    if not (room and isinstance(room, str)):
        return False
    if room.startswith("model:") and not _validate_model_room(conn, room):
        return False
    return True


def _send_room_join_error(conn, room: str) -> None:
    conn.send_json({
        "op": "broadcast",
        "type": "error",
        "room": room,
        "message": f"Unauthorized or invalid room join request for {room}",
    })


def _validate_model_room(conn, room: str) -> bool:
    user = getattr(conn, "user", None)
    if not user:
        logger.warning(
            "Rejected model room subscription without authenticated user: %s", room,
        )
        return False
    if getattr(user, "is_admin", False):
        return True
    return _check_model_room_ownership(user, room)


def _check_model_room_ownership(user, room: str) -> bool:
    match = re.fullmatch(r"model:([A-Za-z0-9_]+)(?::([A-Za-z0-9_-]+))?", room)
    if not match:
        logger.warning("Rejected malformed model room: %s", room)
        return False
    model_name, object_id = match.groups()
    if not _check_model_room_user_owns(user, model_name, object_id, room):
        return False
    return True


def _check_model_room_user_owns(user, model_name, object_id, room) -> bool:
    user_model_name = user.__class__.__name__
    if model_name != user_model_name:
        logger.warning(
            "Rejected cross-model subscription: user=%s room=%s", user_model_name, room,
        )
        return False
    if object_id is None:
        logger.warning(
            "Rejected broad model room subscription for non-admin user: %s", room,
        )
        return False
    if str(getattr(user, "id", "")) != object_id:
        logger.warning(
            "Rejected foreign model room subscription: user=%s room=%s",
            getattr(user, "id", None), room,
        )
        return False
    return True


# ── Lightweight ops ────────────────────────────────────────────────


def _handle_get_presence(server, conn, data, cid) -> None:
    online_users = server.get_online_users() if hasattr(server, "get_online_users") else []
    conn.send_json(
        {"op": "broadcast", "type": "presence_list", "users": online_users}
    )


def _handle_typing(server, conn, data, cid) -> None:
    room = data.get("room")
    if not _can_broadcast_to_room(conn, room):
        return
    payload = {
        "op": "broadcast",
        "type": "typing",
        "room": room,
        "user_id": getattr(conn.user, "id", None),
        "typing": bool(data.get("typing")),
    }
    server.broadcast_to_json(room, payload, exclude=conn)


def _handle_receipt(server, conn, data, cid) -> None:
    room = data.get("room")
    if not _can_broadcast_to_room(conn, room):
        return
    payload = {
        "op": "broadcast",
        "type": "receipt",
        "room": room,
        "message_id": data.get("message_id"),
        "user_id": getattr(conn.user, "id", None),
        "status": data.get("status", "read"),
    }
    server.broadcast_to_json(room, payload, exclude=conn)


def _can_broadcast_to_room(conn, room) -> bool:
    return bool(room and isinstance(room, str) and room in conn._rooms)


def _handle_leave(server, conn, data, cid) -> None:
    # KEEP session state for persistence across SPA navigation; only drop
    # the in-memory component snapshot held on the connection.
    if hasattr(conn, "_live_comps") and cid in conn._live_comps:
        del conn._live_comps[cid]


# ── Component lifecycle: join / call / sync ────────────────────────


def _handle_join(server, conn, data, cid) -> None:
    comp_name = data.get("name")
    state_signed = data.get("state")
    if not _validate_join_payload(comp_name, state_signed):
        return
    cls = COMPONENTS_REGISTRY.get(comp_name)
    if not cls:
        return
    if not hasattr(conn, "_live_comps"):
        conn._live_comps = {}
    # SECURITY: cap live components per connection to bound memory.
    if len(conn._live_comps) >= 100:
        logger.warning(
            "Rejected component join: connection has too many components (%d)",
            len(conn._live_comps),
        )
        return
    conn._live_comps[cid] = (cls, state_signed)


def _validate_join_payload(comp_name, state_signed) -> bool:
    return _validate_join_name(comp_name) and _validate_join_state(state_signed)


def _validate_join_name(comp_name) -> bool:
    if isinstance(comp_name, str) and len(comp_name) <= 100:
        return True
    logger.warning("Invalid component name in join: %r", comp_name)
    return False


def _validate_join_state(state_signed) -> bool:
    if isinstance(state_signed, str) and len(state_signed) <= 100_000:
        return True
    size = len(state_signed) if isinstance(state_signed, str) else 0
    logger.warning("Rejected oversized component state: %d bytes", size)
    return False


def _handle_call_or_sync(server, conn, data, cid) -> None:
    op = data["op"]
    if not _has_live_component(conn, cid):
        return
    cls, state_signed = conn._live_comps[cid]
    req = _build_pseudo_request(server, conn)

    from ..context import request_context

    with request_context(req):
        comp = cls._from_signed_state(state_signed, server.secret_key, cid=cid)
        if not comp:
            return
        if conn.session:
            comp._session = conn.session
        _apply_call_or_sync(op, comp, data)
        _persist_session(server, conn)
        new_state_signed = _resign_component(server, comp)
        conn._live_comps[cid] = (cls, new_state_signed)
        _persist_component_state(server, conn, cid, new_state_signed)
        _send_render(server, conn, comp, cid)


def _has_live_component(conn, cid) -> bool:
    return hasattr(conn, "_live_comps") and cid in conn._live_comps


def _build_pseudo_request(server, conn):
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": conn.path,
        "HTTP_HOST": conn.headers.get("host", "localhost"),
        "QUERY_STRING": "",
        "wsgi.input": None,
        "asok.app": server.app,
        "asok.secret_key": server.secret_key,
    }
    for k, v in conn.headers.items():
        _copy_handshake_header(environ, k, v)
    from ..request import Request

    req = Request(environ)
    if conn.user:
        req.user = conn.user
    if conn.session:
        req._session = conn.session
    return req


def _copy_handshake_header(environ: dict, key: str, value: str) -> None:
    name = key.upper().replace("-", "_")
    if name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
        environ[name] = value
    else:
        environ[f"HTTP_{name}"] = value


def _apply_call_or_sync(op: str, comp, data: dict) -> None:
    if op == "call":
        _apply_call(comp, data)
    elif op == "sync":
        _apply_sync(comp, data)


def _apply_call(comp, data) -> None:
    method_name = data.get("method")
    val = data.get("val")
    method = _resolve_exposed_method(comp, method_name)
    if method is not None:
        _invoke_method(method, val)


def _resolve_exposed_method(comp, method_name):
    if not _is_valid_member_name(method_name, "method name"):
        return None
    if not method_name or method_name.startswith("_"):
        return None
    method = getattr(comp, method_name, None)
    if not _is_exposed_method(method):
        logger.warning(
            "Attempted to call unexposed method '%s' on component '%s'",
            method_name, comp.__class__.__name__,
        )
        return None
    return method


def _is_exposed_method(method) -> bool:
    return callable(method) and bool(getattr(method, "_asok_exposed", False))


def _invoke_method(method, val) -> None:
    sig = inspect.signature(method)
    if len(sig.parameters) > 0:
        method(val)
    else:
        method()


def _apply_sync(comp, data) -> None:
    prop = data.get("prop")
    val = data.get("val")
    if not _is_syncable_prop(comp, prop):
        return
    setattr(comp, prop, val)


def _is_syncable_prop(comp, prop) -> bool:
    if not _prop_passes_basic_checks(comp, prop):
        return False
    # SECURITY: opt-in bindable allow-list — refuse to mutate anything unlisted.
    if prop not in getattr(comp.__class__, "_bindable", []):
        logger.warning(
            "Blocked sync of non-bindable prop '%s' on '%s' (not in whitelist)",
            prop, comp.__class__.__name__,
        )
        return False
    return True


def _prop_passes_basic_checks(comp, prop) -> bool:
    if not _is_valid_member_name(prop, "property name"):
        return False
    return bool(prop) and not prop.startswith("_") and hasattr(comp, prop)


def _is_valid_member_name(name, label: str) -> bool:
    if not isinstance(name, str) or len(name) > 100:
        logger.warning("Invalid %s format in call/sync", label)
        return False
    return True


def _persist_session(server, conn) -> None:
    if conn.session and getattr(conn.session, "modified", False):
        server.app._session_store.save(conn.session.sid, conn.session)


def _resign_component(server, comp) -> str:
    secret = server.secret_key or os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY is not configured. This should never happen if Asok() is "
            "properly initialized."
        )
    return comp._sign_state(secret)


def _persist_component_state(server, conn, cid, new_state_signed) -> None:
    if conn.session is None:
        return
    conn.session[f"_comp_{cid}"] = new_state_signed
    server.app._session_store.save(conn.session.sid, conn.session)


def _send_render(server, conn, comp, cid) -> None:
    new_html = str(comp)
    registry_js = _precompile_component_registry(server, new_html)
    if registry_js is None:
        registry_js = {}
    else:
        new_html, registry_js = registry_js
    conn.send_json({
        "op": "render",
        "cid": cid,
        "name": comp.__class__.__name__,
        "html": new_html,
        "registry": registry_js,
        "state": comp._get_state(),
        "invalidate_cache": True,
    })


def _precompile_component_registry(server, new_html: str):
    if not server.app:
        return None
    new_html, registry = server.app._precompile_directives(new_html)
    return new_html, _registry_to_js(registry)


def _registry_to_js(registry: dict) -> dict:
    if not registry:
        return {}
    return {h: _make_registry_js_entry(expr) for h, expr in registry.items()}


def _make_registry_js_entry(expr: str) -> str:
    body = _expression_to_js_body(expr)
    body = minify_js(body)
    return (
        "function($, $store, $el, $event, $refs, $nextTick) "
        f"{{ with($||{{}}) {{ {body} }} }}"
    )


def _expression_to_js_body(expr: str) -> str:
    is_stmt = _looks_like_statement(expr)
    if expr.strip().startswith("{") and not is_stmt:
        expr = f"({expr})"
    return f"return ({expr})" if not is_stmt else expr


def _looks_like_statement(expr: str) -> bool:
    return ";" in expr or "if " in expr or "return " in expr


# ── Dispatch table ────────────────────────────────────────────────


_OP_HANDLERS: dict[str, Callable] = {
    "join_room": _handle_join_room,
    "get_presence": _handle_get_presence,
    "typing": _handle_typing,
    "receipt": _handle_receipt,
    "leave": _handle_leave,
    "join": _handle_join,
    "call": _handle_call_or_sync,
    "sync": _handle_call_or_sync,
}
