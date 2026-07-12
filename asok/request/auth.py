from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from asok.auth import BearerToken, MagicLink, OAuth
from asok.cache import default_cache
from asok.exceptions import RedirectException
from asok.orm import MODELS_REGISTRY


def _parse_user_roles(user_roles: Any) -> list[str]:
    if isinstance(user_roles, str):
        return [r.strip() for r in user_roles.split(",")]
    if not user_roles:
        return []
    return list(user_roles)


def _has_matching_role(user_roles: list[str], roles: tuple[str, ...]) -> bool:
    for r in roles:
        if r in user_roles:
            return True
    return False


class AuthMixin:
    """Mixin for authentication, authorization and role checks on Request."""

    def login(self: Any, user: Any, remember: bool = False) -> None:
        """Authenticate a user for the current session."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if remember:
            max_age = 86400 * 365
        else:
            max_age = (
                app_ref.config.get("SESSION_MAX_AGE", 86400 * 30)
                if app_ref
                else 86400 * 30
            )

        # SECURITY: Rotate the server-side session ID to prevent session fixation
        self.session_regenerate()

        # Store in the server-side session
        self.session["user_id"] = user.id

        # Bind the session cookie to the sid for server-side revocation
        signed_id = self._sign(f"{user.id}:{self.session.sid}")
        self.environ["asok.session_cookie"] = self._session_cookie(signed_id, max_age)

        # SECURITY: Rotate CSRF token to prevent CSRF token fixation
        self.csrf_token_value = secrets.token_hex(32)

        self._user_instance = user
        self._auth_resolved = True

    def has_role(self: Any, *roles: str) -> bool:
        """Check if current user has any of the given roles."""
        u = self.user
        if not u:
            return False
        user_roles = getattr(u, "roles", None) or getattr(u, "role", None)
        parsed_roles = _parse_user_roles(user_roles)
        return _has_matching_role(parsed_roles, roles)

    def require_role(self: Any, *roles: str, redirect_url: str = "/") -> None:
        """Redirect if the user doesn't have the required roles."""
        if not self.has_role(*roles):
            raise RedirectException(redirect_url)

    def logout(self: Any) -> None:
        """Clear user session and logout.

        SECURITY: Rotates session ID and CSRF token after logout to prevent session fixation.
        """
        self.environ["asok.session_cookie"] = self._session_cookie("", 0)

        # SECURITY: Regenerate session ID to prevent session fixation
        self.session_regenerate()

        # Clear server-side session as well
        try:
            self.session.clear()
        except Exception:
            pass

        # SECURITY: Rotate CSRF token after logout
        self.csrf_token_value = secrets.token_hex(32)

        self._user_instance = None
        self._auth_resolved = False

    def _is_blocked_by_rate_limit(self: Any, attempts: int) -> bool:
        if attempts >= 5:
            logging.getLogger(__name__).warning(
                "SECURITY: Authentication blocked for IP %s: too many failed attempts (%d)",
                self.ip,
                attempts,
            )
            return True
        return False

    def _verify_and_login_user(
        self: Any, user: Any, password_field: str, password: str, key: str
    ) -> bool:
        if user and user.check_password(password_field, password):
            default_cache.forget(key)
            self.login(user)
            return True
        return False

    def _are_credentials_missing(self: Any, password: Any, credentials: dict) -> bool:
        return not password or not credentials

    def _check_rate_limit_and_incr(self: Any, rate_limit_key: str) -> bool:
        """Verify the rate limit status and atomically increment attempts count."""
        attempts = default_cache.get(rate_limit_key, 0)
        if self._is_blocked_by_rate_limit(attempts):
            return True

        # SECURITY: Increment failed attempts counter atomically *before* verifying
        # to prevent TOCTOU race conditions from bypassing the rate limit.
        attempts = default_cache.incr(rate_limit_key, amount=1, ttl=900)
        if attempts > 5:
            self._is_blocked_by_rate_limit(attempts)
            return True
        return False

    def authenticate(
        self: Any, password_field: str = "password", **credentials: Any
    ) -> Optional[Any]:
        """Verify credentials and login user if successful.

        SECURITY: Implements rate limiting to prevent brute force attacks.
        Maximum 5 failed attempts per IP address within 15 minutes.
        """
        model_name = self._get_auth_model_name()
        user_model = MODELS_REGISTRY.get(model_name)
        if not user_model:
            return None

        password = credentials.pop(password_field, None)
        if self._are_credentials_missing(password, credentials):
            return None

        rate_limit_key = f"auth_attempts:{self.ip}"
        if self._check_rate_limit_and_incr(rate_limit_key):
            return None

        user = user_model.find(**credentials)
        if self._verify_and_login_user(user, password_field, password, rate_limit_key):
            return user

        return None

    @property
    def is_authenticated(self: Any) -> bool:
        """True if the current user is logged in."""
        return self.user is not None

    @property
    def auth(self: Any) -> Any:
        """Access advanced auth helpers (Magic Links, OAuth, Tokens)."""

        class AuthProxy:
            magic = MagicLink
            oauth = OAuth
            token = BearerToken

        return AuthProxy

    def _resolve_user_id_from_bearer(self: Any) -> Optional[Any]:
        """Extract user ID from a Bearer Authorization header."""
        auth_header = self.environ.get("HTTP_AUTHORIZATION")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            return BearerToken.verify(self, token)
        return None

    def _resolve_user_id_from_session(self: Any) -> Optional[Any]:
        """Extract user ID from the server-side session or signed cookie."""
        try:
            user_id = self.session.get("user_id")
            if user_id:
                return user_id
        except Exception:
            pass
        return self._resolve_user_id_from_cookie()

    def _resolve_user_id_from_cookie(self: Any) -> Optional[Any]:
        unsigned = self._unsign(self.cookies_dict.get("asok_session"))
        if not unsigned:
            return None
        if ":" not in unsigned:
            return self._resolve_legacy_cookie(unsigned)
        return self._validate_session_linked_cookie(unsigned)

    def _resolve_legacy_cookie(self: Any, unsigned: str) -> Optional[int]:
        app_ref = self.environ.get("asok.app")
        if app_ref and not app_ref.config.get("DEBUG"):
            return None
        try:
            return int(unsigned)
        except ValueError:
            return None

    def _validate_session_linked_cookie(self: Any, unsigned: str) -> Optional[int]:
        uid_str, sid = unsigned.split(":", 1)
        try:
            uid = int(uid_str)
        except ValueError:
            return None
        return self._check_session_user(uid, sid)

    def _check_session_user(self: Any, uid: int, sid: str) -> Optional[int]:
        app_ref = self.environ.get("asok.app")
        if app_ref and hasattr(app_ref, "_session_store"):
            sess_data = app_ref._session_store.load(sid)
            if sess_data and sess_data.get("user_id") == uid:
                return uid
        return None

    def _load_user_by_id(self: Any, user_id: Any, user_model: Any) -> Optional[Any]:
        """Load a user from the model by ID, logging security events on failure."""
        import logging as _logging

        _logger = _logging.getLogger("asok.security")
        try:
            uid = int(user_id)
            user = user_model.find(id=uid)
            if not user:
                _logger.warning(
                    "User ID %d found in session but user doesn't exist (deleted?)", uid
                )
                return None
            return user
        except (ValueError, TypeError) as e:
            _logger.warning("Invalid user ID in session: %s (error: %s)", user_id, e)
        except Exception as e:
            _logger.error("Unexpected error loading user from session: %s", e)
        return None

    def _get_auth_model_name(self: Any) -> str:
        app_ref = self.environ.get("asok.app")
        if app_ref:
            return app_ref.config.get("AUTH_MODEL", "User")
        return "User"

    def _resolve_user_id(self: Any) -> Optional[Any]:
        user_id = self._resolve_user_id_from_bearer()
        if not user_id:
            return self._resolve_user_id_from_session()
        return user_id

    @property
    def user(self: Any) -> Optional[Any]:
        """Get the authenticated User instance for this request."""
        if self._auth_resolved:
            return self._user_instance

        user_model = MODELS_REGISTRY.get(self._get_auth_model_name())
        if not user_model:
            self._auth_resolved = True
            return None

        user_id = self._resolve_user_id()
        if user_id:
            self._user_instance = self._load_user_by_id(user_id, user_model)

        self._auth_resolved = True
        return self._user_instance

    @user.setter
    def user(self: Any, value: Optional[Any]) -> None:
        """Set the authenticated user instance."""
        self._user_instance = value
        self._auth_resolved = True
