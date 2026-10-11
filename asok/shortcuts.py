from __future__ import annotations

from typing import Any, Optional

from asok.context import current_request
from asok.exceptions import abort, redirect


class _CurrentUserProxy:
    """Proxy returning the currently authenticated user in request context, or None."""

    def _get_user(self) -> Any:
        return getattr(current_request, "user", None)

    def __getattr__(self, name: str) -> Any:
        u = self._get_user()
        if u is None:
            raise AttributeError(f"'NoneType' object has no attribute '{name}'")
        return getattr(u, name)

    def __bool__(self) -> bool:
        return bool(self._get_user())

    def __repr__(self) -> str:
        u = self._get_user()
        return repr(u) if u is not None else "<AnonymousUser>"

    def __str__(self) -> str:
        u = self._get_user()
        return str(u) if u is not None else ""


current_user = _CurrentUserProxy()


def flash(category_or_message: str, message: Optional[str] = None) -> None:
    """Store a flash message on the current request session."""
    current_request.flash(category_or_message, message)


def back(default: str = "/") -> None:
    """Redirect to the previous page (Referer) or a fallback default."""
    current_request.back(default=default)


def render_template(*args: Any, **kwargs: Any) -> Any:
    """Render the active page template or custom template."""
    return current_request.html(*args, **kwargs)


def render_block(
    filepath: str, block_name: Optional[str] = None, **context: Any
) -> str:
    """Render a single template block."""
    return current_request.block(filepath, block_name=block_name, **context)


def render_blocks(filepath: str, block_names: list[str], **context: Any) -> str:
    """Render multiple template blocks."""
    return current_request.blocks(filepath, block_names, **context)


def login(user: Any, remember: bool = False) -> None:
    """Authenticate a user for the current session."""
    current_request.login(user, remember=remember)


def logout() -> None:
    """Log out the current user and regenerate session."""
    current_request.logout()


def authenticate(password_field: str = "password", **credentials: Any) -> Optional[Any]:
    """Verify credentials and login user if successful."""
    return current_request.authenticate(password_field=password_field, **credentials)


__all__ = [
    "abort",
    "authenticate",
    "back",
    "current_user",
    "flash",
    "login",
    "logout",
    "redirect",
    "render_block",
    "render_blocks",
    "render_template",
]
