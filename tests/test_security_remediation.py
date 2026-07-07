import logging

from asok.admin import Admin
from asok.admin.utils import _decrypt_totp_secret, _encrypt_totp_secret
from asok.core import Asok
from asok.exceptions import TemplateError
from asok.request import Request


# 1. TOTP Encryption Tests
def test_totp_encryption_roundtrip():
    master_key = "my-super-secret-master-key"
    secret = "JBSWY3DPEHPK3PXP"

    # Encrypt
    encrypted = _encrypt_totp_secret(secret, master_key)
    assert encrypted is not None
    assert encrypted.count("$") == 3

    # Decrypt
    decrypted = _decrypt_totp_secret(encrypted, master_key)
    assert decrypted == secret

    # Verify wrong key fails
    assert _decrypt_totp_secret(encrypted, "wrong-key") is None

    # Verify tampered ciphertext fails integrity check
    parts = encrypted.split("$")
    # Tamper the ciphertext part
    parts[2] = parts[2][:-2] + "00"
    tampered = "$".join(parts)
    assert _decrypt_totp_secret(tampered, master_key) is None


def test_totp_legacy_decryption_fallback():
    import hashlib
    import hmac
    import secrets

    master_key = "my-super-secret-master-key"
    secret = "JBSWY3DPEHPK3PXP"

    # Encrypt using the old legacy method
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(16)

    key = hashlib.pbkdf2_hmac("sha256", master_key.encode(), salt, 600000)

    plaintext = secret.encode()
    keystream = (key * ((len(plaintext) // len(key)) + 1))[: len(plaintext)]
    ciphertext = bytes(p ^ k for p, k in zip(plaintext, keystream))

    mac = hmac.new(key, salt + iv + ciphertext, hashlib.sha256).digest()
    legacy_encrypted = f"{salt.hex()}${iv.hex()}${ciphertext.hex()}${mac.hex()}"

    # Decrypt using the new updated function (should hit legacy fallback)
    decrypted = _decrypt_totp_secret(legacy_encrypted, master_key)
    assert decrypted == secret

    # Wrong key fails
    assert _decrypt_totp_secret(legacy_encrypted, "wrong-key") is None


# 2. Template Error leak prevention tests
def test_template_error_hide_details_in_production(caplog):
    from asok.exceptions import AsokException, SecurityError, ValidationError

    app = Asok()
    app.config["SECRET_KEY"] = "test-secret-at-least-32-chars-long"

    # 1. DEBUG mode: error details exposed
    app.config["DEBUG"] = True
    e = TemplateError("Syntax error in template\n\nCode:\nprint('hello')")

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/page",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }

    def start_response(status, headers):
        pass

    request = Request(environ)
    res = app._map_known_exception(
        request, e, SecurityError, ValidationError, TemplateError, AsokException
    )
    assert "print" in res and "hello" in res

    # 2. Production mode (DEBUG=False): details hidden, generic message shown
    app.config["DEBUG"] = False
    with caplog.at_level(logging.ERROR):
        res_prod = app._map_known_exception(
            request, e, SecurityError, ValidationError, TemplateError, AsokException
        )

    assert b"print('hello')" not in res_prod.encode("utf-8")
    assert b"An error occurred while rendering the template" in res_prod.encode("utf-8")
    # Verify the error was logged
    assert any(
        "Template rendering error" in record.message for record in caplog.records
    )


# 3. Request scheme proxy trust tests
def test_request_scheme_proxy_trust():
    # 1. Without trusted proxy, ignore X-Forwarded-Proto
    app = Asok()
    app.config["TRUSTED_PROXIES"] = None

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "REMOTE_ADDR": "1.2.3.4",
        "HTTP_X_FORWARDED_PROTO": "https",
        "wsgi.url_scheme": "http",
        "asok.app": app,
    }
    req = Request(environ)
    assert req.scheme == "http"

    # 2. With trusted proxy matching, respect X-Forwarded-Proto
    app.config["TRUSTED_PROXIES"] = ["1.2.3.4"]
    req = Request(environ)
    assert req.scheme == "https"

    # 3. With wildcard trusted proxy, respect X-Forwarded-Proto
    app.config["TRUSTED_PROXIES"] = "*"
    req = Request(environ)
    assert req.scheme == "https"

    # 4. With trusted proxy, but REMOTE_ADDR not matching, ignore X-Forwarded-Proto
    app.config["TRUSTED_PROXIES"] = ["5.6.7.8"]
    req = Request(environ)
    assert req.scheme == "http"


# 4. Response header sanitization tests
def test_response_header_sanitization():
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.url_scheme": "http",
    }
    req = Request(environ)

    # Inject CR/LF in custom header
    req.header("X-Injected\r\nHeader", "value\nwith\r\nnewlines")

    # Verify they were stripped
    header_names = [h[0] for h in req.response_headers]
    header_values = [h[1] for h in req.response_headers]

    assert "X-InjectedHeader" in header_names
    assert "valuewithnewlines" in header_values
    assert not any("\r" in name or "\n" in name for name in header_names)
    assert not any("\r" in val or "\n" in val for val in header_values)


# 5. Cache-backed Admin rate limiting tests
class DummyApp:
    def __init__(self):
        self.config = {
            "AUTH_MODEL": "MockUser",
            "SECRET_KEY": "test-secret-at-least-32-chars-long",
            "DEBUG": True,
        }
        self.root_dir = "/tmp"
        self.models = []


class MockUser:
    id = 42


def test_admin_rate_limiting_uses_cache():
    from asok.cache import default_cache

    app = DummyApp()
    admin = Admin(app, login_rate_limit=(3, 60))

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/login",
        "REMOTE_ADDR": "9.9.9.9",
        "HTTP_USER_AGENT": "Mozilla/5.0",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.user = MockUser()

    # 1. Verify fresh login rate limit check allows request
    allowed, remaining = admin._login_rate_check(req)
    assert allowed is True

    # 2. Record 3 failures
    admin._login_rate_record_failure(req)
    admin._login_rate_record_failure(req)
    admin._login_rate_record_failure(req)

    # Verify they exist in the cache with the expected prefix keys
    expected_count_key = f"admin_login_count:{admin._login_rate_key(req)}"
    expected_reset_key = f"admin_login_reset:{admin._login_rate_key(req)}"
    assert default_cache.has(expected_count_key)
    assert default_cache.has(expected_reset_key)

    # Check rate limit blocks
    allowed, remaining = admin._login_rate_check(req)
    assert allowed is False
    assert remaining > 0

    # Reset limit
    admin._login_rate_reset(req)
    assert not default_cache.has(expected_count_key)
    assert not default_cache.has(expected_reset_key)
    allowed, remaining = admin._login_rate_check(req)
    assert allowed is True

    # 3. Test CSV export rate limit uses cache
    allowed_exp, remaining_exp = admin._export_rate_check(req)
    assert allowed_exp is True

    # Record 5 exports
    for _ in range(5):
        admin._export_rate_record(req)

    expected_export_key = f"admin_export:{req.user.id}"
    assert default_cache.has(expected_export_key)

    # Check export limit blocks
    allowed_exp, remaining_exp = admin._export_rate_check(req)
    assert allowed_exp is False
    assert remaining_exp > 0


# 6. Template Sandbox escape/bypass tests
def test_template_sandbox_request_exfiltration_blocked():
    from asok.templates import render_template_string

    class DummyForm:
        _request = "some-request-object"

    result = render_template_string("{{ form._request }}", {"form": DummyForm()})
    assert result == ""


def test_template_sandbox_selectattr_getattr_bypass_blocked():
    from asok.templates import render_template_string

    class DummyItem:
        pass

    items = [DummyItem()]
    # Trying to select attr '__class__' should be blocked by the sandbox and return empty list
    result = render_template_string(
        "{{ items | selectattr('__class__', None) }}", {"items": items}
    )
    assert result == "[]"
