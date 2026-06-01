from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

from asok.auth import BearerToken, MagicLink, OAuth
from asok.cache import default_cache
from asok.exceptions import RedirectException
from asok.orm import MODELS_REGISTRY


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
        signed_id = self._sign(user.id)
        self.environ["asok.session_cookie"] = self._session_cookie(signed_id, max_age)

        # SECURITY: Rotate the server-side session ID to prevent session fixation
        self.session_regenerate()

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
        if not user_roles:
            return False
        if isinstance(user_roles, str):
            user_roles = [r.strip() for r in user_roles.split(",")]
        return any(r in user_roles for r in roles)

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

    def authenticate(
        self: Any, password_field: str = "password", **credentials: Any
    ) -> Optional[Any]:
        """Verify credentials and login user if successful.

        SECURITY: Implements rate limiting to prevent brute force attacks.
        Maximum 5 failed attempts per IP address within 15 minutes.
        """
        app_ref: Optional[Any] = self.environ.get("asok.app")
        model_name = app_ref.config.get("AUTH_MODEL", "User") if app_ref else "User"
        user_model = MODELS_REGISTRY.get(model_name)
        if not user_model:
            return None

        password = credentials.pop(password_field, None)
        if not password or not credentials:
            return None

        # SECURITY: Rate limiting by IP address
        rate_limit_key = f"auth_attempts:{self.ip}"
        attempts = default_cache.get(rate_limit_key, 0)

        # Block if too many failed attempts
        MAX_ATTEMPTS = 5
        LOCKOUT_DURATION = 900  # 15 minutes in seconds

        if attempts >= MAX_ATTEMPTS:
            logging.getLogger(__name__).warning(
                "SECURITY: Authentication blocked for IP %s: too many failed attempts (%d)",
                self.ip,
                attempts,
            )
            # Slow down attacker
            time.sleep(2)
            return None

        user = user_model.find(**credentials)
        if user and user.check_password(password_field, password):
            # SECURITY: Reset counter on successful login
            default_cache.forget(rate_limit_key)
            self.login(user)
            return user

        # SECURITY: Increment failed attempts counter
        default_cache.set(rate_limit_key, attempts + 1, ttl=LOCKOUT_DURATION)

        # Slow down failed attempts to make brute force impractical
        time.sleep(1)

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

    @property
    def user(self: Any) -> Optional[Any]:
        """Get the authenticated User instance for this request."""
        if self._auth_resolved:
            return self._user_instance

        app_ref: Optional[Any] = self.environ.get("asok.app")
        model_name = app_ref.config.get("AUTH_MODEL", "User") if app_ref else "User"
        user_model = MODELS_REGISTRY.get(model_name)
        if not user_model:
            self._auth_resolved = True
            return None

        user_id = None

        # 1. Try Authorization header (API Token)
        auth_header = self.environ.get("HTTP_AUTHORIZATION")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            user_id = BearerToken.verify(self, token)

        # 2. Try Session (Browser)
        if not user_id:
            try:
                # Prioritize server-side session value (crucial for impersonation)
                user_id = self.session.get("user_id")
            except Exception:
                user_id = None

            if not user_id:
                # Fallback to signed cookie
                user_id = self._unsign(self.cookies_dict.get("asok_session"))

        if user_id:
            try:
                uid = int(user_id)
                user = user_model.find(id=uid)
                if not user:
                    # SECURITY: Log when user ID in session doesn't exist (deleted/disabled user)
                    import logging

                    logger = logging.getLogger("asok.security")
                    logger.warning(
                        "User ID %d found in session but user doesn't exist (deleted?)",
                        uid,
                    )
                    self._user_instance = None
                else:
                    self._user_instance = user
            except (ValueError, TypeError) as e:
                # SECURITY: Log invalid user ID format in session (possible tampering)
                import logging

                logger = logging.getLogger("asok.security")
                logger.warning("Invalid user ID in session: %s (error: %s)", user_id, e)
                self._user_instance = None
            except Exception as e:
                # Unexpected error - log it for monitoring
                import logging

                logger = logging.getLogger("asok.security")
                logger.error("Unexpected error loading user from session: %s", e)
                self._user_instance = None

        self._auth_resolved = True
        return self._user_instance

    @user.setter
    def user(self: Any, value: Optional[Any]) -> None:
        """Set the authenticated user instance."""
        self._user_instance = value
        self._auth_resolved = True
