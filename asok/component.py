from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
from typing import Any, Optional

from .templates import SafeString, render_template_string

COMPONENTS_REGISTRY = {}


def exposed(fn):
    """Decorator to mark a component method as callable via WebSockets (Alive engine)."""
    fn._asok_exposed = True
    return fn


class ComponentMeta(type):
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
        self._cid: str = kwargs.pop("_cid", secrets.token_hex(4))
        self._session: dict[str, Any] = kwargs.pop("_session", {})
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

    def html(self, name: str) -> str:
        """Load a raw HTML template file relative to the component's file location."""
        import inspect

        module = inspect.getmodule(self.__class__)
        # Fallback for dynamic modules not in sys.modules
        if not module or not hasattr(module, "__file__"):
            import sys

            for m in sys.modules.values():
                if hasattr(m, "__name__") and m.__name__.endswith(
                    self.__class__.__module__
                ):
                    module = m
                    break

        if not module or not hasattr(module, "__file__"):
            # Last ditch: try to find by class file
            dir_path = os.path.dirname(os.path.abspath(inspect.getfile(self.__class__)))
        else:
            dir_path = os.path.dirname(os.path.abspath(module.__file__))

        path = os.path.join(dir_path, name)
        if not os.path.exists(path):
            return f"<!-- Template {name} not found in {dir_path} -->"

        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def render(self) -> str:
        """Return the template string for this component. Must be implemented by subclasses."""
        raise NotImplementedError

    def _get_state(self) -> dict[str, Any]:
        """Extract all public, serializable state from the component."""
        state = {}
        # 1. Collect class-level non-callable, non-private attributes
        #    Walk the MRO in reverse so more derived classes win.
        for cls in reversed(type(self).__mro__):
            if cls in (Component, object):
                continue
            for k, v in cls.__dict__.items():
                if not k.startswith("_") and not callable(v):
                    state[k] = v
        # 2. Overlay with instance-level overrides (set via __init__ or methods)
        for k, v in self.__dict__.items():
            if not k.startswith("_") and not callable(v):
                state[k] = v
        return state

    def _sign_state(self, secret_key: str) -> str:
        """Sign the current component state with a secret key for secure transmission."""
        state = self._get_state()
        dump = json.dumps(state, sort_keys=True)
        sig = hmac.new(secret_key.encode(), dump.encode(), hashlib.sha256).hexdigest()
        return f"{dump}.{sig}"

    @classmethod
    def _from_signed_state(
        cls: type[Component], signed: str, secret_key: str, cid: Optional[str] = None
    ) -> Optional[Component]:
        """Reconstruct a component instance from a signed state string."""
        if not signed or "." not in signed:
            return None
        try:
            data_str, sig = signed.rsplit(".", 1)
            expected = hmac.new(
                secret_key.encode(), data_str.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            state = json.loads(data_str)
            if cid:
                state["_cid"] = cid
            return cls(**state)
        except Exception:
            return None

    def __str__(self) -> str:
        """Render the component to an HTML string including its signed state."""
        # We need the secret key to sign the state
        secret = os.getenv("SECRET_KEY", "dev-secret-key")
        # Collect state for context (including class defaults)
        ctx = self._get_state()
        ctx["session"] = self.session
        ctx["slot"] = getattr(self, "_slot", None)

        rendered_html = render_template_string(self.render(), ctx)
        state_str = self._sign_state(secret)

        return SafeString(
            f'<div id="asok-{self._cid}" '
            f'data-asok-component="{html.escape(self.__class__.__name__)}" '
            f'data-asok-state="{html.escape(state_str)}">'
            f"{rendered_html}"
            f"</div>"
        )
