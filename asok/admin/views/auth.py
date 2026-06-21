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
        if request.method == "POST":
            res = self._handle_post_login(request, form)
            if res is not None:
                return res
        return self._render(request, "login.html", form=form)

    def _has_admin_access(self, user: Any) -> bool:
        if not user:
            return False
        if getattr(user, "is_admin", False):
            return True
        roles = getattr(user, "roles", None)
        return bool(roles)

    def _handle_login_rate_limit(self, request: Any, remaining: int, form: Form) -> Any:
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

    def _handle_login_success(self, request: Any, user: Any) -> None:
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

    def _handle_login_failure(self, request: Any, email_val: Any) -> None:
        self._login_rate_record_failure(request)
        self._log(
            request,
            "login_failed",
            "User",
            entity_id=None,
            changes={"email": email_val},
        )
        request.flash("error", self.t(request, "Invalid credentials"))

    def _handle_login_exception(self, request: Any, e: Exception) -> None:
        if isinstance(e, SecurityError) or (
            isinstance(e, AbortException) and e.status == 403
        ):
            request.flash(
                "error",
                self.t(request, "Security session expired. Please try again."),
            )
        else:
            raise e

    def _process_login_attempt(self, request: Any, form: Form) -> None:
        try:
            if form.validate():
                user = request.authenticate(
                    email=form.email.value, password=form.password.value
                )
                if self._has_admin_access(user):
                    self._handle_login_success(request, user)
                self._handle_login_failure(request, form.email.value)
        except (AbortException, SecurityError) as e:
            self._handle_login_exception(request, e)

    def _handle_post_login(self, request: Any, form: Form) -> Any:
        allowed, remaining = self._login_rate_check(request)
        if not allowed:
            return self._handle_login_rate_limit(request, remaining, form)
        self._process_login_attempt(request, form)
        return None

    def _cleanup_pending_2fa_session(self, request: Any) -> None:
        try:
            request.session.pop("pending_2fa_uid", None)
        except Exception:
            pass

    def _get_pending_2fa_user(self, request: Any) -> Any:
        try:
            pending_uid = request.session.get("pending_2fa_uid")
        except Exception:
            pending_uid = None
        if not pending_uid:
            raise RedirectException(self.prefix + "/login")
        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        user = None
        if User:
            user = User.find(id=pending_uid)
        if not user:
            self._cleanup_pending_2fa_session(request)
            raise RedirectException(self.prefix + "/login")
        return user

    def _verify_backup_codes(self, user: Any, code_val: str) -> tuple[bool, bool]:
        if not user.backup_codes:
            return False, False
        import json
        try:
            backup_codes = json.loads(user.backup_codes)
            code_input = code_val.strip().upper()
            for i, hashed in enumerate(backup_codes):
                if _verify_backup_code(code_input, hashed):
                    backup_codes.pop(i)
                    user.backup_codes = json.dumps(backup_codes)
                    user.save()
                    return True, True
        except Exception:
            pass
        return False, False

    def _verify_2fa_code(self, user: Any, code_val: str) -> tuple[bool, bool]:
        secret, enabled = self._get_user_2fa(user.id)
        if enabled:
            if _totp_verify(secret, code_val):
                return True, False
        return self._verify_backup_codes(user, code_val)

    def _handle_successful_2fa_login(self, request: Any, user: Any, used_backup: bool) -> None:
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
            import json
            count = len(json.loads(user.backup_codes or "[]"))
            request.flash(
                "warning",
                self.t(
                    request,
                    "Backup code used. You have {count} codes remaining.",
                    count=count,
                ),
            )
        request.flash(
            "success",
            self.t(
                request, "Welcome back, {name}!", name=user.name or user.email
            ),
        )
        raise RedirectException(self.prefix)

    def _handle_failed_2fa_login(self, request: Any, user: Any) -> None:
        self._login_rate_record_failure(request)
        self._log(
            request,
            "login_2fa_failed",
            "User",
            entity_id=user.id,
        )
        request.flash("error", self.t(request, "Invalid code"))

    def _handle_2fa_rate_limit(self, request: Any, remaining: int, form: Form) -> Any:
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

    def _twofa_challenge(self, request: Any) -> Any:
        """Verify a TOTP code for a user mid-login (after password ok)."""
        user = self._get_pending_2fa_user(request)
        form = Form(
            {"code": Form.text("Authentication code", "required", autofocus=True)},
            request,
        )
        if request.method == "POST":
            if form.validate():
                allowed, remaining = self._login_rate_check(request)
                if not allowed:
                    return self._handle_2fa_rate_limit(request, remaining, form)
                code_valid, used_backup = self._verify_2fa_code(user, form.code.value)
                if code_valid:
                    self._handle_successful_2fa_login(request, user, used_backup)
                self._handle_failed_2fa_login(request, user)
        return self._render(request, "2fa.html", form=form)

    def _validate_user_for_2fa(self, request: Any) -> Any:
        u = request.user
        if not u:
            raise RedirectException(self.prefix + "/login")
        secret, enabled = self._get_user_2fa(u.id)
        if enabled:
            request.flash("error", self.t(request, "2FA is already enabled."))
            raise RedirectException(self.prefix + "/me")
        return u

    def _get_pending_2fa_secret(self, request: Any) -> str:
        try:
            secret = request.session.get("pending_2fa_secret") or _totp_new_secret()
            request.session["pending_2fa_secret"] = secret
        except Exception:
            secret = _totp_new_secret()
        return secret

    def _enable_2fa_on_user(self, request: Any, u: Any, secret: str) -> None:
        backup_codes_plain = _generate_backup_codes(10)
        backup_codes_hashed = [
            _hash_backup_code(code) for code in backup_codes_plain
        ]
        import json
        master_key = self.app.config.get("SECRET_KEY", "")
        encrypted_secret = _encrypt_totp_secret(secret, master_key)

        User = MODELS_REGISTRY.get(self.app.config.get("AUTH_MODEL", "User"))
        User.get_engine().execute(
            f"UPDATE {User._table} SET totp_secret = ?, totp_enabled = ?, backup_codes = ? WHERE id = ?",
            (encrypted_secret, 1, json.dumps(backup_codes_hashed), u.id),
        )

        try:
            request.session.pop("pending_2fa_secret", None)
        except Exception:
            pass

        import time
        request.session["_backup_codes_display"] = {
            "codes": backup_codes_plain,
            "expires_at": time.time() + 300,
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

    def _twofa_setup(self, request: Any) -> Any:
        """Enable 2FA for the current user."""
        u = self._validate_user_for_2fa(request)
        secret = self._get_pending_2fa_secret(request)
        account = getattr(u, "email", None) or f"user-{u.id}"
        uri = _totp_uri(secret, account, self.site_name)
        form = Form(
            {"code": Form.text("Verification code", "required", autofocus=True)},
            request,
        )
        if request.method == "POST":
            if form.validate():
                if _totp_verify(secret, form.code.value):
                    self._enable_2fa_on_user(request, u, secret)
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
        User.get_engine().execute(
            f"UPDATE {User._table} SET totp_secret = NULL, totp_enabled = 0, backup_codes = NULL WHERE id = ?",
            (u.id,),
        )

        self._log(request, "2fa_disabled", "User", entity_id=u.id)
        request.flash("success", self.t(request, "Two-factor authentication disabled."))
        raise RedirectException(self.prefix + "/me")

    def _get_backup_codes_to_display(self, request: Any) -> list[str]:
        codes_data = request.session.get("_backup_codes_display")
        if not codes_data:
            request.flash(
                "error", self.t(request, "Backup codes have already been displayed.")
            )
            raise RedirectException(self.prefix + "/me")
        if isinstance(codes_data, dict):
            import time
            if time.time() > codes_data.get("expires_at", 0):
                request.session.pop("_backup_codes_display", None)
                request.flash(
                    "error",
                    self.t(request, "Backup codes expired. Please regenerate 2FA."),
                )
                raise RedirectException(self.prefix + "/me")
            return codes_data.get("codes", [])
        if isinstance(codes_data, list):
            return codes_data
        return []

    def _twofa_backup_codes(self, request: Any) -> Any:
        """Display backup codes once after 2FA setup."""
        if not request.user:
            raise RedirectException(self.prefix + "/login")

        # POST: User confirms they saved the codes → clear from session
        if request.method == "POST":
            request.session.pop("_backup_codes_display", None)
            request.flash("success", self.t(request, "2FA setup complete!"))
            raise RedirectException(self.prefix + "/me")

        codes = self._get_backup_codes_to_display(request)
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

    def _verify_impersonator(self, request: Any, User: Any) -> Any:
        orig_id = request.session.get("impersonator_id")
        if not orig_id:
            if not request.user:
                return None
            orig_id = request.user.id
        admin_user = User.find(id=orig_id)
        if not admin_user:
            return None
        if getattr(admin_user, "is_admin", False):
            return admin_user
        return None

    def _setup_impersonation(self, request: Any, admin_user: Any, target: Any, auth_name: str) -> None:
        request.session["impersonator_id"] = admin_user.id
        request.session["impersonate_started_at"] = time.time()
        request.session["user_id"] = target.id
        request.session_regenerate()

        self._log(request, "impersonate_start", auth_name, entity_id=target.id)
        target_name = target.name or target.email
        request.flash(
            "success",
            self.t(request, "Now acting as {name}", name=target_name),
        )
        raise RedirectException(self.prefix)

    def _impersonate(self, request: Any, target_id: int) -> None:
        # Security: only super-admins (is_admin=True) can start impersonation
        # We check the original user from the session if already impersonating
        auth_name = self.app.config.get("AUTH_MODEL", "User")
        User = MODELS_REGISTRY.get(auth_name)

        admin_user = self._verify_impersonator(request, User)
        if not admin_user:
            return self._forbid(request, "Only admins can impersonate")

        try:
            target_id_int = int(target_id)
        except (ValueError, TypeError):
            request.flash("error", self.t(request, "Target user not found"))
            raise RedirectException(self.prefix + "/users")

        target = User.find(id=target_id_int)
        if not target:
            request.flash("error", self.t(request, "Target user not found"))
            raise RedirectException(self.prefix + "/users")

        if target.id == admin_user.id:
            request.flash("info", self.t(request, "You are already yourself"))
            raise RedirectException(self.prefix + "/users")

        self._setup_impersonation(request, admin_user, target, auth_name)

    def _stop_impersonate(self, request: Any) -> None:
        impersonator_id = request.session.get("impersonator_id")
        if not impersonator_id:
            raise RedirectException(self.prefix)

        request.session["user_id"] = impersonator_id
        request.session.pop("impersonator_id", None)
        request.session.pop("impersonate_started_at", None)
        request.session_regenerate()

        request.flash("info", self.t(request, "Stopped impersonation"))
        raise RedirectException(self.prefix)

