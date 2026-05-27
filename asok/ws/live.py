from __future__ import annotations

import inspect
import json
import logging
import os
import re
from typing import Any

from ..component import COMPONENTS_REGISTRY
from ..utils.minify import minify_js

logger = logging.getLogger("asok.ws")


def on_live_message(server: Any, conn: Any, text: str) -> None:
    """Handle Live Component updates — ops: join, call, sync.

    SECURITY: Message size limits prevent DoS via large JSON payloads.
    """
    try:
        # SECURITY: Reject excessively large messages to prevent DoS (max 1MB)
        if len(text) > 1_000_000:
            logger.warning("Rejected oversized live message: %d bytes", len(text))
            return

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, RecursionError) as e:
            logger.warning("Invalid JSON in live message: %s", e)
            return
        op = data.get("op")
        cid = data.get("cid")

        # SECURITY: Validate operation and component ID
        if not isinstance(op, str) or len(op) > 50:
            logger.warning("Invalid operation format in live message")
            return
        if cid is not None and (not isinstance(cid, str) or len(cid) > 100):
            logger.warning("Invalid component ID format in live message")
            return

        # ── JOIN_ROOM: subscribe to a specific broadcast room ──
        if op == "join_room":
            room = data.get("room")
            # SECURITY: Room name validation happens in conn.join()
            if room and isinstance(room, str):
                if room.startswith("model:"):
                    user = getattr(conn, "user", None)
                    if not user:
                        logger.warning(
                            "Rejected model room subscription without authenticated user: %s",
                            room,
                        )
                        return

                    if not getattr(user, "is_admin", False):
                        match = re.fullmatch(
                            r"model:([A-Za-z0-9_]+)(?::([A-Za-z0-9_-]+))?",
                            room,
                        )
                        if not match:
                            logger.warning("Rejected malformed model room: %s", room)
                            return

                        model_name, object_id = match.groups()
                        user_model_name = user.__class__.__name__
                        if model_name != user_model_name:
                            logger.warning(
                                "Rejected cross-model subscription: user=%s room=%s",
                                user_model_name,
                                room,
                            )
                            return

                        if object_id is None:
                            logger.warning(
                                "Rejected broad model room subscription for non-admin user: %s",
                                room,
                            )
                            return

                        if str(getattr(user, "id", "")) != object_id:
                            logger.warning(
                                "Rejected foreign model room subscription: user=%s room=%s",
                                getattr(user, "id", None),
                                room,
                            )
                            return

                conn.join(room)
            return

        # ── LEAVE: browser tells server a component was removed from DOM ──
        if op == "leave":
            if hasattr(conn, "_live_comps") and cid in conn._live_comps:
                # Remove component state from connection memory only
                # KEEP session state for persistence across SPA navigation
                del conn._live_comps[cid]
            return

        # ── JOIN: browser tells server which component instance just connected ──
        if op == "join":
            comp_name = data.get("name")
            state_signed = data.get("state")

            # SECURITY: Validate component name and state
            if not isinstance(comp_name, str) or len(comp_name) > 100:
                logger.warning("Invalid component name in join: %r", comp_name)
                return
            if not isinstance(state_signed, str) or len(state_signed) > 100_000:
                logger.warning(
                    "Rejected oversized component state: %d bytes",
                    len(state_signed) if isinstance(state_signed, str) else 0,
                )
                return

            cls = COMPONENTS_REGISTRY.get(comp_name)
            if not cls:
                return
            # Store the mapping cid → (cls, signed_state) on the connection
            if not hasattr(conn, "_live_comps"):
                conn._live_comps = {}

            # SECURITY: Limit number of live components per connection to prevent DoS
            if len(conn._live_comps) >= 100:
                logger.warning(
                    "Rejected component join: connection has too many components (%d)",
                    len(conn._live_comps),
                )
                return

            conn._live_comps[cid] = (cls, state_signed)
            return  # no re-render needed on join

        # ── CALL / SYNC: browser triggers a method or two-way bind update ──
        if op in ("call", "sync"):
            if not hasattr(conn, "_live_comps") or cid not in conn._live_comps:
                return

            cls, state_signed = conn._live_comps[cid]

            # Construct a mock/dummy Request from the WebSocket connection's handshake properties
            environ = {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": conn.path,
                "HTTP_HOST": conn.headers.get("host", "localhost"),
                "QUERY_STRING": "",
                "wsgi.input": None,
                "asok.app": server.app,
                "asok.secret_key": server.secret_key,
            }
            # Copy all connection headers as HTTP_ headers
            for k, v in conn.headers.items():
                name = k.upper().replace("-", "_")
                if name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                    environ[name] = v
                else:
                    environ[f"HTTP_{name}"] = v

            from ..context import request_context
            from ..request import Request

            req = Request(environ)
            if conn.user:
                req.user = conn.user
            if conn.session:
                req._session = conn.session

            with request_context(req):
                comp = cls._from_signed_state(state_signed, server.secret_key, cid=cid)
                if not comp:
                    return

                # Inject session
                if conn.session:
                    comp._session = conn.session

                if op == "call":
                    method_name = data.get("method")
                    val = data.get("val")

                    # SECURITY: Validate method name format and length
                    if not isinstance(method_name, str) or len(method_name) > 100:
                        logger.warning("Invalid method name format in call")
                        return
                    if method_name and not method_name.startswith("_"):
                        method = getattr(comp, method_name, None)
                        # Security: only allow methods explicitly marked with @exposed
                        if callable(method) and getattr(method, "_asok_exposed", False):
                            # Pass val as arg if method accepts it
                            sig = inspect.signature(method)
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

                    # SECURITY: Validate property name format and length
                    if not isinstance(prop, str) or len(prop) > 100:
                        logger.warning("Invalid property name format in sync")
                        return
                    if prop and not prop.startswith("_") and hasattr(comp, prop):
                        # SECURITY: Require explicit _bindable whitelist (opt-in, not opt-out)
                        # Components must declare _bindable = ["prop1", "prop2"] to allow sync
                        bindable = getattr(comp.__class__, "_bindable", [])
                        if prop not in bindable:
                            logger.warning(
                                "Blocked sync of non-bindable prop '%s' on '%s' (not in whitelist)",
                                prop,
                                comp.__class__.__name__,
                            )
                        else:
                            setattr(comp, prop, val)

                # Persist session if modified
                if conn.session and getattr(conn.session, "modified", False):
                    server.app._session_store.save(conn.session.sid, conn.session)

                # Re-render and update stored signed state
                secret = server.secret_key or os.getenv("SECRET_KEY")
                if not secret:
                    raise RuntimeError(
                        "SECRET_KEY is not configured. This should never happen if Asok() is properly initialized."
                    )
                new_state_signed = comp._sign_state(secret)
                conn._live_comps[cid] = (cls, new_state_signed)

                # Persist updated state to session so page refresh restores it
                if conn.session is not None:
                    conn.session[f"_comp_{cid}"] = new_state_signed
                    server.app._session_store.save(conn.session.sid, conn.session)

                new_html = str(comp)

                # Pre-compile directives for Zero-Eval Security
                if server.app:
                    new_html, registry = server.app._precompile_directives(new_html)
                    # Convert registry functions to JS strings
                    registry_js = {}
                    if registry:
                        for h, expr in registry.items():
                            is_stmt = ";" in expr or "if " in expr or "return " in expr
                            if expr.strip().startswith("{") and not is_stmt:
                                expr = f"({expr})"
                            body = f"return ({expr})" if not is_stmt else expr
                            # Minify the function body to remove newlines/comments that break script injection
                            body = minify_js(body)
                            registry_js[h] = (
                                "function($, $store, $el, $event, $refs, $nextTick) "
                                f"{{ with($||{{}}) {{ {body} }} }}"
                            )
                else:
                    registry_js = {}

                # Invalidate SPA cache so navigation shows updated state
                conn.send_json(
                    {
                        "op": "render",
                        "cid": cid,
                        "name": comp.__class__.__name__,
                        "html": new_html,
                        "registry": registry_js,
                        "state": comp._get_state(),
                        "invalidate_cache": True,
                    }
                )

    except Exception as e:
        logger.error(f"Error handling live message: {e}", exc_info=True)
