from __future__ import annotations

import time
from typing import Any

from ...exceptions import AbortException, RedirectException, SecurityError
from ...forms import Form
from ...orm import MODELS_REGISTRY
from ..utils import (
    _encrypt_totp_secret,
    _generate_backup_codes,
    _hash_backup_code,
    _totp_new_secret,
    _totp_uri,
    _totp_verify,
    _verify_backup_code,
)


class AuthViewsMixin:
    # ── Auth pages / 2FA ─────────────────────────────────────

    def _login(self, request: Any) -> Any:
        form = Form(
            {
                "email": Form.email("Email", "required|email", autofocus=True),
                "password": Form.password("Password", "required"),
            },
            request,
        )
        is_post = request.method == "POST"
        if is_post:
            allowed, remaining = self._login_rate_check(request)
            if not allowed:
                # SECURITY: Regenerate session on rate limit to prevent session fixation
                request.session_regenerate()

                self._log(
                    request,
                    "login_rate_limited",
                    "User",
                    entity_id=None,
                    changes={"ip": self._client_ip(request)},
                )
                request.status_code(429)
                request.flash(
                    "error",
                    f"Too many failed attempts. Try again in {remaining}s.",
                )
                return self._render(request, "login.html", form=form)
        try:
            if form.validate():
                user = request.authenticate(
                    email=form.email.value, password=form.password.value
                )
                if user and (
                    getattr(user, "is_admin", False)
                    or (hasattr(user, "roles") and user.roles)
                ):
                    self._login_rate_reset(request)
                    _, totp_enabled = self._get_user_2fa(user.id)
                    if totp_enabled:
                        # Demote to a pending-2FA state
                        pending_uid = user.id
                        request.logout()
                        try:
                            request.session["pending_2fa_uid"] = pending_uid
                        except Exception:
                            pass
                        raise RedirectException(self.prefix + "/2fa")
                    # Note: request.authenticate() already called request.login(user)
                    # SECURITY: Regenerate session after successful login to prevent session fixation
                    request.session_regenerate()
                    self._log(
                        request,
                        "login",
                        "User",
                        entity_id=getattr(user, "id", None),
                    )
                    request.flash(
                        "success",
                        self.t(
                            request,
                            "Welcome back, {name}!",
                            name=user.name or user.email,
                        ),
                    )
                    raise RedirectException(self.prefix)
                # Failed auth — count it
                self._login_rate_record_failure(request)
                self._log(
                    request,
                    "login_failed",
                    "User",
                    entity_id=None,
                    changes={"email": form.email.value},
                )
                request.flash("error", self.t(request, "Invalid credentials"))
        except (AbortException, SecurityError) as e:
            # Special handling for CSRF failure in login form to avoid 403 pages
            if isinstance(e, SecurityError) or (isinstance(e, AbortException) and e.status == 403):
                request.flash(
                    "error",
                    self.t(request, "Security session expired. Please try again."),
                )
            else:
                raise
        return self._render(request, "login.html", form=form)

    def _twofa_challenge(self, request: Any) -> Any:
        """Verify a TOTP code for a user mid-login (after password ok)."""
        try:
            pending_uid = request.session.get("pending_2fa_uid")
        except Exception:
            pending_uid = None
        if not pending_uid:
            raise RedirectException(self.prefix + "/login")
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        user = User.find(id=pending_uid) if User else None
        if not user:
            try:
                request.session.pop("pending_2fa_uid", None)
            except Exception:
                pass
            raise RedirectException(self.prefix + "/login")
        form = Form(
            {"code": Form.text("Authentication code", "required", autofocus=True)},
            request,
        )
        if request.method == "POST" and form.validate():
            allowed, remaining = self._login_rate_check(request)
            if not allowed:
                request.status_code(429)
                request.flash(
                    "error",
                    self.t(
                        request,
                        "Too many attempts. Try again in {remaining}s.",
                        remaining=remaining,
                    ),
                )
                return self._render(request, "2fa.html", form=form)
            secret, enabled = self._get_user_2fa(user.id)
            code_valid = False
            used_backup = False

            # Try TOTP code first
            if enabled and _totp_verify(secret, form.code.value):
                code_valid = True

            # If TOTP fails, try backup codes
            if not code_valid and user.backup_codes:
                import json

                try:
                    backup_codes_hashed = json.loads(user.backup_codes)
                    code_input = form.code.value.strip().upper()

                    # Try each backup code
                    for i, hashed_code in enumerate(backup_codes_hashed):
                        if _verify_backup_code(code_input, hashed_code):
                            code_valid = True
                            used_backup = True
                            # Remove used backup code
                            backup_codes_hashed.pop(i)
                            user.backup_codes = json.dumps(backup_codes_hashed)
                            user.save()
                            break
                except Exception:
                    pass  # Invalid JSON or other error, fall through to failure

            if code_valid:
                self._login_rate_reset(request)
                try:
                    request.session.pop("pending_2fa_uid", None)
                except Exception:
                    pass
                request.login(user)
                # SECURITY: Regenerate session after successful 2FA to prevent session fixation
                request.session_regenerate()
                self._log(
                    request,
                    "login",
                    "User",
                    entity_id=user.id,
                    changes={"twofa": True, "backup_code": used_backup},
                )
                if used_backup:
                    request.flash(
                        "warning",
                        self.t(
                            request,
                            "Backup code used. You have {count} codes remaining.",
                            count=len(json.loads(user.backup_codes or "[]")),
                        ),
                    )
                request.flash(
                    "success",
                    self.t(
                        request, "Welcome back, {name}!", name=user.name or user.email
                    ),
                )
                raise RedirectException(self.prefix)
            self._login_rate_record_failure(request)
            self._log(
                request,
                "login_2fa_failed",
                "User",
                entity_id=user.id,
            )
            request.flash("error", self.t(request, "Invalid code"))
        return self._render(request, "2fa.html", form=form)

    def _twofa_setup(self, request: Any) -> Any:
        """Enable 2FA for the current user."""
        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        secret, enabled = self._get_user_2fa(u.id)
        if enabled:
            request.flash("error", self.t(request, "2FA is already enabled."))
            raise RedirectException(self.prefix + "/me")
        # Use the existing pending secret in session, or generate a new one
        try:
            secret = request.session.get("pending_2fa_secret") or _totp_new_secret()
            request.session["pending_2fa_secret"] = secret
        except Exception:
            secret = _totp_new_secret()
        account = getattr(u, "email", None) or f"user-{u.id}"
        uri = _totp_uri(secret, account, self.site_name)
        form = Form(
            {"code": Form.text("Verification code", "required", autofocus=True)},
            request,
        )
        if request.method == "POST" and form.validate():
            if _totp_verify(secret, form.code.value):
                # Generate backup codes
                backup_codes_plain = _generate_backup_codes(10)
                backup_codes_hashed = [
                    _hash_backup_code(code) for code in backup_codes_plain
                ]

                # Encrypt TOTP secret
                import json

                master_key = self.app.config.get("SECRET_KEY", "")
                encrypted_secret = _encrypt_totp_secret(secret, master_key)

                # CRITICAL: Activate 2FA BEFORE showing backup codes (atomic SQL update)
                User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
                with User._get_conn() as conn:
                    conn.execute(
                        f"UPDATE {User._table} SET totp_secret = ?, totp_enabled = ?, backup_codes = ? WHERE id = ?",
                        (encrypted_secret, 1, json.dumps(backup_codes_hashed), u.id),
                    )

                try:
                    request.session.pop("pending_2fa_secret", None)
                except Exception:
                    pass

                # Store codes temporarily in session with 5-min expiration
                import time

                request.session["_backup_codes_display"] = {
                    "codes": backup_codes_plain,
                    "expires_at": time.time() + 300,  # 5 minutes
                }

                self._log(request, "2fa_enabled", "User", entity_id=u.id)
                request.flash(
                    "success",
                    self.t(
                        request,
                        "Two-factor authentication enabled. Save your backup codes!",
                    ),
                )
                raise RedirectException(self.prefix + "/2fa-backup-codes")
            request.flash("error", self.t(request, "Invalid code, try again."))
        return self._render(
            request,
            "2fa_setup.html",
            form=form,
            secret=secret,
            uri=uri,
            active=None,
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "My profile", "url": self.prefix + "/me"},
                {"label": "Enable 2FA", "url": None},
            ],
        )

    def _twofa_disable(self, request: Any) -> Any:
        """Disable 2FA for the current user (requires current password)."""
        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        if request.method != "POST":
            raise RedirectException(self.prefix + "/me")
        pw = request.form.get("current_password", "")
        if not pw or not u.check_password("password", pw):
            request.flash("error", self.t(request, "Current password is incorrect."))
            raise RedirectException(self.prefix + "/me")

        # Disable 2FA and clear backup codes (atomic SQL update)
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        with User._get_conn() as conn:
            conn.execute(
                f"UPDATE {User._table} SET totp_secret = NULL, totp_enabled = 0, backup_codes = NULL WHERE id = ?",
                (u.id,),
            )

        self._log(request, "2fa_disabled", "User", entity_id=u.id)
        request.flash("success", self.t(request, "Two-factor authentication disabled."))
        raise RedirectException(self.prefix + "/me")

    def _twofa_backup_codes(self, request: Any) -> Any:
        """Display backup codes once after 2FA setup."""
        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")

        # POST: User confirms they saved the codes → clear from session
        if request.method == "POST":
            request.session.pop("_backup_codes_display", None)
            request.flash("success", self.t(request, "2FA setup complete!"))
            raise RedirectException(self.prefix + "/me")

        # GET: Display codes (keep in session until confirmed, max 5 min)
        import time

        codes_data = request.session.get("_backup_codes_display")

        if not codes_data:
            request.flash(
                "error", self.t(request, "Backup codes have already been displayed.")
            )
            raise RedirectException(self.prefix + "/me")

        # Check expiration (5 minutes timeout)
        if isinstance(codes_data, dict):
            if time.time() > codes_data.get("expires_at", 0):
                request.session.pop("_backup_codes_display", None)
                request.flash(
                    "error",
                    self.t(request, "Backup codes expired. Please regenerate 2FA."),
                )
                raise RedirectException(self.prefix + "/me")
            codes = codes_data.get("codes", [])
        else:
            # Legacy format (list) - accept but warn
            codes = codes_data if isinstance(codes_data, list) else []

        return self._render(
            request,
            "2fa_backup_codes.html",
            codes=codes,
            breadcrumbs=[
                {"label": "Dashboard", "url": self.prefix},
                {"label": "My profile", "url": self.prefix + "/me"},
                {"label": "Backup Codes", "url": None},
            ],
        )

    # ── Impersonation ────────────────────────────────────────

    def _impersonate(self, request: Any, target_id: int) -> None:
        # Security: only super-admins (is_admin=True) can start impersonation
        # We check the original user from the session if already impersonating
        orig_id = request.session.get("impersonator_id") or request.user.id
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)

        # Verify permissions of the ACTUAL user performing the action
        admin_user = User.find(id=orig_id)
        if not admin_user or not getattr(admin_user, "is_admin", False):
            return self._forbid(request, "Only admins can impersonate")

        target = User.find(id=target_id)
        if not target:
            request.flash("error", self.t(request, "Target user not found"))
            raise RedirectException(self.prefix + "/users")

        if target.id == admin_user.id:
            request.flash("info", self.t(request, "You are already yourself"))
            raise RedirectException(self.prefix + "/users")

        # Save the real admin ID in session
        request.session["impersonator_id"] = admin_user.id
        request.session["impersonate_started_at"] = time.time()
        request.session["user_id"] = target.id

        self._log(request, "impersonate_start", auth_name, entity_id=target.id)
        request.flash(
            "success",
            self.t(request, "Now acting as {name}", name=target.name or target.email),
        )
        raise RedirectException(self.prefix)

    def _stop_impersonate(self, request: Any) -> None:
        impersonator_id = request.session.get("impersonator_id")
        if not impersonator_id:
            raise RedirectException(self.prefix)

        request.session["user_id"] = impersonator_id
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonate_started_at", None)

        request.flash("info", self.t(request, "Stopped impersonation"))
        raise RedirectException(self.prefix)
