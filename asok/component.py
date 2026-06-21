from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from typing import Any, Optional, Union

from .templates import SafeString, render_template_string

COMPONENTS_REGISTRY = {}


def _is_safe_list(val: Union[list, tuple]) -> bool:
    return all(_is_safe_value(item) for item in val)


def _is_safe_dict(val: dict) -> bool:
    for k, v in val.items():
        if not isinstance(k, str) or not _is_safe_value(v):
            return False
    return True


def _is_safe_value(val: Any) -> bool:
    """Check if a value is safe to serialize."""
    SAFE_TYPES = (str, int, float, bool, type(None), list, dict, tuple)
    if not isinstance(val, SAFE_TYPES):
        return False
    if isinstance(val, (list, tuple)):
        return _is_safe_list(val)
    if isinstance(val, dict):
        return _is_safe_dict(val)
    return True


def _has_file_attr(module: Optional[Any]) -> bool:
    return bool(module and hasattr(module, "__file__"))


def _find_sys_module(cls_module: str) -> Optional[Any]:
    import sys
    for m in sys.modules.values():
        if hasattr(m, "__name__") and m.__name__.endswith(cls_module):
            return m
    return None


def _resolve_swapped_path(path: str) -> Optional[str]:
    base_path, current_ext = os.path.splitext(path)
    if current_ext == ".html" and os.path.exists(base_path + ".asok"):
        return base_path + ".asok"
    if current_ext == ".asok" and os.path.exists(base_path + ".html"):
        return base_path + ".html"
    return None


def _verify_signature(signed: str, secret_key: str) -> Optional[str]:
    if not signed or "." not in signed:
        return None
    try:
        data_str, sig = signed.rsplit(".", 1)
        expected = hmac.new(
            secret_key.encode(), data_str.encode(), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(sig, expected):
            return data_str
    except Exception:
        pass
    return None


def _verify_state_timestamp(state: dict[str, Any]) -> bool:
    ts = state.pop("_ts", 0)
    if time.time() - ts > 3600:
        import logging
        logging.getLogger(__name__).warning(
            "Expired state signature (age: %d seconds)", time.time() - ts
        )
        return False
    return True


def exposed(fn):
    """Decorator to mark a component method as callable via WebSockets (Alive engine)."""
    fn._asok_exposed = True
    return fn


class ComponentMeta(type):
    """Metaclass for all Asok Components.
    Automatically registers components in the global registry for server-side lookup.
    """

    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        if name != "Component":
            COMPONENTS_REGISTRY[name] = cls
        return cls


class Component(metaclass=ComponentMeta):
    """Base class for isomorphic reactive components.

    Components manage their own state and can be re-rendered on the fly via WebSockets.
    """

    def __init__(self, **kwargs: Any):
        """Initialize a component with optional initial state."""
        # SECURITY: Use 128-bit CID to prevent brute-force attacks (was 32-bit)
        self._cid: str = kwargs.pop("_cid", secrets.token_hex(16))
        self._session: dict[str, Any] = kwargs.pop("_session", {})
        self._client: Optional[str] = kwargs.pop("_client", None)
        self._slot: Optional[str] = None
        # Initial state
        for k, v in kwargs.items():
            setattr(self, k, v)

    @property
    def session(self) -> dict[str, Any]:
        """Access the user session associated with this component."""
        return self._session

    def mount(self) -> None:
        """Lifecycle hook called when the component is initially mounted or connected."""
        pass

    def _find_dir_path(self) -> str:
        import inspect
        module = inspect.getmodule(self.__class__)
        if not _has_file_attr(module):
            module = _find_sys_module(self.__class__.__module__)
        if not _has_file_attr(module):
            return os.path.dirname(os.path.abspath(inspect.getfile(self.__class__)))
        return os.path.dirname(os.path.abspath(module.__file__))

    def _resolve_template_path(self, dir_path: str, name: str) -> Optional[str]:
        path = os.path.join(dir_path, name)
        if os.path.exists(path):
            return path
        for ext in (".html", ".asok"):
            if os.path.exists(path + ext):
                return path + ext
        return _resolve_swapped_path(path)

    def _read_template_file(self, path: str) -> str:
        try:
            file_size = os.path.getsize(path)
            if file_size > 1_000_000:
                return "<!-- Template file too large -->"
        except OSError:
            return "<!-- Error reading template -->"
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def html(self, name: str) -> str:
        """Load a raw HTML template file relative to the component's file location.

        SECURITY: File size limits prevent DoS via extremely large templates.
        """
        dir_path = self._find_dir_path()
        path = self._resolve_template_path(dir_path, name)
        if not path:
            return f"<!-- Template {name} not found in {dir_path} -->"
        return self._read_template_file(path)

    def render(self) -> str:
        """Return the template string for this component. Must be implemented by subclasses."""
        raise NotImplementedError

    def _collect_attrs(self, source: dict[str, Any], target: dict[str, Any]) -> None:
        for k, v in source.items():
            if not k.startswith("_") and not callable(v):
                if _is_safe_value(v):
                    target[k] = v

    def _get_state(self) -> dict[str, Any]:
        """Extract all public, serializable state from the component.

        SECURITY: Only JSON-serializable types are allowed to prevent
        deserialization attacks (no pickle).
        """
        state = {}
        for cls in reversed(type(self).__mro__):
            if cls not in (Component, object):
                self._collect_attrs(cls.__dict__, state)
        self._collect_attrs(self.__dict__, state)
        return state

    def _sign_state(self, secret_key: str) -> str:
        """Sign the current component state with a secret key for secure transmission."""
        state = self._get_state()
        # SECURITY: Add timestamp and nonce to prevent replay attacks
        state["_ts"] = int(time.time())
        state["_nonce"] = secrets.token_hex(8)
        dump = json.dumps(state, sort_keys=True)
        sig = hmac.new(secret_key.encode(), dump.encode(), hashlib.sha256).hexdigest()
        return f"{dump}.{sig}"

    @classmethod
    def _from_signed_state(
        cls: type[Component], signed: str, secret_key: str, cid: Optional[str] = None
    ) -> Optional[Component]:
        """Reconstruct a component instance from a signed state string."""
        data_str = _verify_signature(signed, secret_key)
        if not data_str:
            return None
        try:
            state = json.loads(data_str)
            if not _verify_state_timestamp(state):
                return None

            state.pop("_nonce", None)
            if cid:
                state["_cid"] = cid
            return cls(**state)
        except Exception:
            return None

    def __str__(self) -> str:
        """Render the component to an HTML string including its signed state."""
        # We need the secret key to sign the state
        secret = os.getenv("SECRET_KEY")
        if not secret:
            raise RuntimeError(
                "SECRET_KEY is not configured. This should never happen if Asok() is properly initialized."
            )
        # Collect state for context (including class defaults)
        ctx = self._get_state()
        ctx["session"] = self.session
        ctx["slot"] = getattr(self, "_slot", None)

        rendered_html = render_template_string(self.render(), ctx)
        state_str = self._sign_state(secret)

        client_attr = (
            f" client:{self._client}" if getattr(self, "_client", None) else ""
        )

        return SafeString(
            f'<div id="asok-{self._cid}" '
            f'data-asok-component="{html.escape(self.__class__.__name__)}" '
            f'data-asok-state="{html.escape(state_str)}"{client_attr}>'
            f"{rendered_html}"
            f"</div>"
        )
