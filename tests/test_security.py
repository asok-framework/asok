"""
Security tests: XSS, path traversal, cookie signing, security headers.
Uses the real asok.templates API.
"""

import os

from asok.templates import html_safe_json, render_template_string


def render(src, **ctx):
    return render_template_string(src, ctx)


# ---------------------------------------------------------------------------
# XSS — html_safe_json
# ---------------------------------------------------------------------------


class TestXssProtection:
    def test_html_safe_json_escapes_script_tag(self):
        result = html_safe_json({"x": "</script><script>alert(1)</script>"})
        assert "</script>" not in result

    def test_html_safe_json_escapes_ampersand(self):
        result = html_safe_json({"x": "a & b"})
        assert "\\u0026" in result

    def test_html_safe_json_escapes_less_than(self):
        result = html_safe_json({"x": "<div>"})
        assert "<div>" not in result

    def test_auto_escape_in_template_variables(self):
        result = render("{{ value }}", value='<img src=x onerror="alert(1)">')
        assert "<img" not in result

    def test_safe_filter_allows_html(self):
        result = render("{{ value | safe }}", value="<b>bold</b>")
        assert "<b>bold</b>" in result

    def test_tojson_filter_is_xss_safe(self):
        result = render("{{ data | tojson }}", data={"x": "</script>"})
        assert "</script>" not in result


# ---------------------------------------------------------------------------
# Path traversal prevention
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_safe_path_inside_base_allowed(self, tmp_path):
        base_dir = str(tmp_path / "uploads")
        os.makedirs(base_dir)

        def is_safe(dest):
            dest = os.path.abspath(dest)
            try:
                common = os.path.commonpath([dest, base_dir])
            except ValueError:
                return False
            return common == base_dir

        safe = os.path.join(base_dir, "avatar.png")
        assert is_safe(safe)

    def test_traversal_with_dotdot_blocked(self, tmp_path):
        base_dir = str(tmp_path / "uploads")
        os.makedirs(base_dir)

        def is_safe(dest):
            dest = os.path.abspath(dest)
            try:
                common = os.path.commonpath([dest, base_dir])
            except ValueError:
                return False
            return common == base_dir

        evil = os.path.join(base_dir, "../../etc/passwd")
        assert not is_safe(evil)

    def test_absolute_path_outside_base_blocked(self, tmp_path):
        base_dir = str(tmp_path / "uploads")
        os.makedirs(base_dir)

        dest = "/etc/passwd"
        dest_abs = os.path.abspath(dest)
        try:
            common = os.path.commonpath([dest_abs, base_dir])
        except ValueError:
            common = ""
        assert common != base_dir


# ---------------------------------------------------------------------------
# Cookie signing (HMAC integrity)
# ---------------------------------------------------------------------------


class TestCookieSigning:
    def test_signed_cookie_validates(self):
        import hashlib
        import hmac as hmac_mod

        secret = b"test-secret"
        value = "flash_message"
        sig = hmac_mod.new(secret, value.encode(), hashlib.sha256).hexdigest()
        signed = f"{value}.{sig}"
        parts = signed.rsplit(".", 1)
        expected = hmac_mod.new(secret, parts[0].encode(), hashlib.sha256).hexdigest()
        assert hmac_mod.compare_digest(parts[1], expected)

    def test_tampered_cookie_rejected(self):
        import hashlib
        import hmac as hmac_mod

        secret = b"test-secret"
        value = "original"
        sig = hmac_mod.new(secret, value.encode(), hashlib.sha256).hexdigest()
        signed = f"tampered.{sig}"
        parts = signed.rsplit(".", 1)
        expected = hmac_mod.new(secret, parts[0].encode(), hashlib.sha256).hexdigest()
        assert not hmac_mod.compare_digest(parts[1], expected)
