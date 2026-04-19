from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any, Optional

from .mail import Mail

if TYPE_CHECKING:
    from asok.request import Request

logger = logging.getLogger("asok.auth")


class AuthError(Exception):
    pass


class MagicLink:
    """Provides secure, passwordless authentication via signed email links."""

    @staticmethod
    def create_token(request: Request, email: str, expires_in: int = 3600) -> str:
        """Create a signed magic link token valid for a specific duration (default 1 hour)."""
        exp = int(time.time()) + expires_in
        payload = f"{email}|{exp}"
        # We use the request helper to sign (uses SECRET_KEY)
        return request._sign(payload)

    @staticmethod
    def verify_token(request: Request, token: str) -> Optional[str]:
        """Verify a magic link token and return the associated email if valid and not expired."""
        payload = request._unsign(token)
        if not payload or "|" not in payload:
            return None

        email, exp = payload.split("|", 1)
        try:
            if int(exp) < time.time():
                return None
        except ValueError:
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

        # Build URL — prefer configured APP_URL to prevent Host header injection
        app = request.environ.get("asok.app")
        app_url = None
        if app is not None:
            app_url = app.config.get("APP_URL")
        if app_url:
            base = app_url.rstrip("/")
        else:
            host = request.environ.get("HTTP_HOST", "localhost")
            scheme = request.environ.get("wsgi.url_scheme", "http")
            base = f"{scheme}://{host}"
        link = f"{base}/auth/magic/callback?token={token}"

        body = f"Click here to log in to your account:\n\n{link}\n\nThis link will expire in 1 hour."
        html = f'<p>Click the link below to log in to your account:</p><p><a href="{link}">{link}</a></p><p>This link will expire in 1 hour.</p>'

        Mail.send(to=email, subject=subject, body=body, html=html)
        return link


class OAuth:
    """Zero-dependency OAuth2 client for common providers."""

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

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": config["scopes"],
        }
        if state:
            params["state"] = state

        return f"{config['auth_url']}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def callback(
        provider_name: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Exchange an authorization code for a set of normalized user information."""
        config = OAuth.PROVIDERS.get(provider_name.lower())
        if not config:
            raise AuthError(f"Unknown OAuth provider: {provider_name}")

        # 1. Exchange code for access token
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
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode())
                access_token = res_data.get("access_token")
                if not access_token:
                    raise AuthError(f"OAuth token exchange failed: {res_data}")
        except Exception as e:
            raise AuthError(f"OAuth token request failed: {e}")

        # 2. Fetch user info
        user_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "Asok-Framework",
        }
        user_req = urllib.request.Request(config["user_url"], headers=user_headers)

        try:
            with urllib.request.urlopen(user_req) as response:
                user_info = json.loads(response.read().decode())

                # Normalise common fields
                email = user_info.get("email")
                # GitHub specific: primary email might be in a different endpoint or hidden
                if provider_name == "github" and not email:
                    email = OAuth._fetch_github_email(access_token)

                return {
                    "provider": provider_name,
                    "provider_id": str(user_info.get("id") or user_info.get("sub")),
                    "email": email,
                    "name": user_info.get("name") or user_info.get("login"),
                    "picture": user_info.get("picture") or user_info.get("avatar_url"),
                    "raw": user_info,
                }
        except Exception as e:
            raise AuthError(f"Failed to fetch OAuth user info: {e}")

    @staticmethod
    def _fetch_github_email(access_token: str) -> Optional[str]:
        """Fetch the primary verified email from GitHub's custom endpoint."""
        req = urllib.request.Request(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "Asok-Framework",
            },
        )
        try:
            with urllib.request.urlopen(req) as response:
                emails = json.loads(response.read().decode())
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
    def verify(request: Request, token: str) -> Optional[str]:
        """Verify a bearer token and return the associated user ID if valid and not expired."""
        payload = request._unsign(token)
        if not payload or "|" not in payload:
            return None

        try:
            user_id, exp_str = payload.split("|", 1)
            exp = int(exp_str)
            if exp > 0 and exp < time.time():
                return None
            return user_id
        except ValueError:
            return None
