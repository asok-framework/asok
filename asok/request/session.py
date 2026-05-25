from __future__ import annotations

from typing import Any, Optional

from asok.session import Session


class SessionMixin:
    """Mixin for session management on Request."""

    @property
    def session(self: Any) -> Session:
        """Access the current request's session."""
        if self._session is not None:
            return self._session
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if not app_ref or not hasattr(app_ref, "_session_store"):
            self._session = Session()
            return self._session
        store = app_ref._session_store
        signed_sid = self.cookies_dict.get("asok_sid")
        sid = self._unsign(signed_sid) if signed_sid else None
        data = store.load(sid) if sid else None
        if data is not None:
            self._session = Session(data)
            self._session.sid = sid
        else:
            self._session = Session()
            self._session.sid = store.generate_sid()
        self._session.modified = False
        return self._session

    def session_regenerate(self: Any) -> None:
        """Rotate the session ID while preserving all existing data.

        Crucial for preventing session fixation attacks after successful login.
        """
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if not app_ref or not hasattr(app_ref, "_session_store"):
            return

        store = app_ref._session_store
        sess = self.session  # Ensure session is loaded/created
        if sess.sid:
            new_sid = store.regenerate(sess.sid)
            sess.sid = new_sid
            sess.modified = True
