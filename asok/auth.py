from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any, Optional

from .exceptions import AsokException
from .mail import Mail

if TYPE_CHECKING:
    from asok.request import Request

logger = logging.getLogger("asok.auth")


class AuthError(AsokException):
    pass


class MagicLink:
    """Provides secure, passwordless authentication via signed email links."""

    @staticmethod
    def create_token(
        request: Request, email: str, expires_in: Optional[int] = None
    ) -> str:
        """Create a signed magic link token valid for a specific duration."""
        if expires_in is None:
            app = request.environ.get("asok.app")
            expires_in = app.config.get("MAGIC_LINK_TTL", 3600) if app else 3600

        exp = int(time.time()) + expires_in
        payload = f"{email}|{exp}"
        # We use the request helper to sign (uses SECRET_KEY)
        return request._sign(payload)

    @staticmethod
    def _parse_payload(payload: Optional[str]) -> tuple[str, int, bool]:
        if not payload or "|" not in payload:
            return "", 0, False
        parts = payload.split("|", 1)
        if len(parts) != 2:
            return "", 0, False
        email, exp_str = parts
        try:
            return email, int(exp_str), True
        except ValueError:
            return "", 0, False

    @staticmethod
    def verify_token(request: Request, token: str) -> Optional[str]:
        """Verify a magic link token and return the associated email if valid and not expired.

        SECURITY: Uses constant-time operations to prevent timing attacks.
        """
        import secrets

        # Unsign the token
        payload = request._unsign(token)
        email, exp_time, is_valid = MagicLink._parse_payload(payload)

        # Check expiration with constant-time comparison
        current_time = int(time.time())
        is_expired = exp_time < current_time

        # SECURITY: Apply consistent delay for ALL paths to prevent timing attacks
        # This prevents attackers from distinguishing valid vs invalid tokens by timing
        base_delay = 0.15  # 150ms minimum
        jitter = secrets.randbelow(50) / 1000  # 0-50ms random jitter
        time.sleep(base_delay + jitter)

        # Combine all checks (after delay, so timing is consistent)
        if not is_valid or is_expired:
            return None

        return email

    @staticmethod
    def send(
        request: Request,
        email: str,
        subject: str = "Login to Asok",
        template: Optional[str] = None,
    ) -> str:
        """Generate a magic link and send it to the specified email address."""
        token = MagicLink.create_token(request, email)

        # Build URL — OBLIGATORY APP_URL in production to prevent Host header injection
        app = request.environ.get("asok.app")
        app_url = app.config.get("APP_URL") if app else None
        is_debug = app.config.get("DEBUG", True) if app else True

        if app_url:
            base = app_url.rstrip("/")
        else:
            if not is_debug:
                raise ValueError(
                    "SECURITY ERROR: 'APP_URL' is mandatory in production to generate secure Magic Links. "
                    "Set it in your .env file: APP_URL=https://yourdomain.com"
                )
            # Fallback to Host header ONLY in DEBUG mode
            host = request.environ.get("HTTP_HOST", "localhost")
            scheme = request.environ.get("wsgi.url_scheme", "http")
            base = f"{scheme}://{host}"

        link = f"{base}/auth/magic/callback?token={token}"

        body = f"Click here to log in to your account:\n\n{link}\n\nThis link will expire in 1 hour."
        html = f'<p>Click the link below to log in to your account:</p><p><a href="{link}">{link}</a></p><p>This link will expire in 1 hour.</p>'

        Mail.send(to=email, subject=subject, body=body, html=html)
        return link


class OAuth:
    """Lightweight OAuth2 helper that does not require additional runtime packages by default."""

    PROVIDERS = {
        "google": {
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "user_url": "https://www.googleapis.com/oauth2/v3/userinfo",
            "scopes": "openid email profile",
        },
        "github": {
            "auth_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "user_url": "https://api.github.com/user",
            "scopes": "user:email",
        },
    }

    @staticmethod
    def get_auth_url(
        provider_name: str,
        client_id: str,
        redirect_uri: str,
        state: Optional[str] = None,
    ) -> str:
        """Generate the initial authorization URL for the specified OAuth provider."""
        config = OAuth.PROVIDERS.get(provider_name.lower())
        if not config:
            raise AuthError(f"Unknown OAuth provider: {provider_name}")

        if not state:
            state = secrets.token_urlsafe(32)

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": config["scopes"],
            "state": state,
        }

        return f"{config['auth_url']}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _exchange_code(
        config: dict[str, str],
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> str:
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        headers = {"Accept": "application/json"}
        req = urllib.request.Request(
            config["token_url"],
            data=urllib.parse.urlencode(token_data).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                response_data = response.read(100_000)
                res_data = json.loads(response_data.decode())
                access_token = res_data.get("access_token")
                if not access_token:
                    raise AuthError(f"OAuth token exchange failed: {res_data}")
                return access_token
        except Exception as e:
            if isinstance(e, AuthError):
                raise
            raise AuthError(f"OAuth token request failed: {e}")

    @staticmethod
    def _fetch_raw_user_info(config: dict[str, str], access_token: str) -> dict[str, Any]:
        user_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "Asok-Framework",
        }
        user_req = urllib.request.Request(config["user_url"], headers=user_headers)
        with urllib.request.urlopen(user_req, timeout=10) as response:
            response_data = response.read(100_000)
            return json.loads(response_data.decode())

    @staticmethod
    def _normalize_user_info(
        provider_name: str, user_info: dict[str, Any], email: Optional[str]
    ) -> dict[str, Any]:
        return {
            "provider": provider_name,
            "provider_id": str(user_info.get("id") or user_info.get("sub")),
            "email": email,
            "name": user_info.get("name") or user_info.get("login"),
            "picture": user_info.get("picture") or user_info.get("avatar_url"),
            "raw": user_info,
        }

    @staticmethod
    def _fetch_user_info(
        config: dict[str, str],
        provider_name: str,
        access_token: str,
    ) -> dict[str, Any]:
        try:
            user_info = OAuth._fetch_raw_user_info(config, access_token)
            email = user_info.get("email")
            if provider_name == "github" and not email:
                email = OAuth._fetch_github_email(access_token)
            return OAuth._normalize_user_info(provider_name, user_info, email)
        except Exception as e:
            raise AuthError(f"Failed to fetch OAuth user info: {e}")

    @staticmethod
    def callback(
        provider_name: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        state: Optional[str] = None,
        expected_state: Optional[str] = None,
    ) -> dict[str, Any]:
        """Exchange an authorization code for a set of normalized user information."""
        config = OAuth.PROVIDERS.get(provider_name.lower())
        if not config:
            raise AuthError(f"Unknown OAuth provider: {provider_name}")

        if expected_state is None:
            raise AuthError("OAuth expected_state is required")

        if not state or not secrets.compare_digest(state, expected_state):
            raise AuthError("OAuth state validation failed")

        access_token = OAuth._exchange_code(
            config, client_id, client_secret, code, redirect_uri
        )
        return OAuth._fetch_user_info(config, provider_name, access_token)

    @staticmethod
    def _fetch_github_email(access_token: str) -> Optional[str]:
        """Fetch the primary verified email from GitHub's custom endpoint.

        SECURITY: Timeout and size limits prevent DoS.
        """
        req = urllib.request.Request(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "Asok-Framework",
            },
        )
        try:
            # SECURITY: Set timeout to prevent DoS/hang (10 seconds)
            with urllib.request.urlopen(req, timeout=10) as response:
                # SECURITY: Limit response size to prevent DoS (max 100KB)
                response_data = response.read(100_000)
                emails = json.loads(response_data.decode())
                for e in emails:
                    if e.get("primary") and e.get("verified"):
                        return e.get("email")
        except Exception:
            pass
        return None


class BearerToken:
    """Provides stateless API authentication using cryptographically signed tokens."""

    @staticmethod
    def create(request: Request, user_id: Any, expires_in: Optional[int] = None) -> str:
        """Create a signed bearer token for a specific user ID.

        If expires_in is provided (seconds), the token is time-limited.
        """
        exp = int(time.time() + expires_in) if expires_in else 0
        payload = f"{user_id}|{exp}"
        return request._sign(payload)

    @staticmethod
    def _parse_payload(payload: str) -> tuple[Optional[str], int]:
        if not payload or "|" not in payload:
            return None, 0
        try:
            user_id, exp_str = payload.split("|", 1)
            return user_id, int(exp_str)
        except ValueError:
            return None, 0

    @staticmethod
    def verify(request: Request, token: str) -> Optional[str]:
        """Verify a bearer token and return the associated user ID if valid and not expired."""
        payload = request._unsign(token)
        if not payload:
            return None
        user_id, exp = BearerToken._parse_payload(payload)
        if not user_id:
            return None

        current_time = int(time.time())

        # SECURITY: Fixed expiration logic (was allowing exp==0 without validation)
        # exp == 0 means permanent token (should be avoided in production)
        # exp > 0 and exp <= now means expired
        # exp > 0 and exp > now means valid
        is_expired = exp > 0 and exp <= current_time

        if is_expired:
            return None

        return user_id
