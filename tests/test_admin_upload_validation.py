"""Regression tests for admin upload validation.

SECURITY: _validate_file_upload used to trust the client-supplied Content-Type,
so a malicious payload with a forged Content-Type: image/png could pass the
admin pre-check. It now validates the actual file content via magic bytes
(upload.validate_mime_type), matching the authoritative check in upload.save().
"""

from unittest.mock import MagicMock

import pytest

from asok import Asok
from asok.admin import Admin
from asok.request.upload import UploadedFile

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret"})
    return Admin(app)


def test_forged_content_type_is_rejected(admin):
    """A non-image payload lying with Content-Type: image/png must be rejected."""
    upload = UploadedFile(
        "evil.png", b"<script>alert(1)</script>", content_type="image/png"
    )
    request = MagicMock()
    assert admin._validate_file_upload(request, upload) is False
    request.flash.assert_called()  # user gets an error


def test_real_png_is_accepted(admin):
    """A genuine PNG (valid magic bytes + matching extension) passes."""
    upload = UploadedFile("logo.png", _PNG, content_type="image/png")
    request = MagicMock()
    assert admin._validate_file_upload(request, upload) is True


def test_extension_mismatch_is_rejected(admin):
    """Real PNG content served under a .php extension must be rejected (anti-polyglot)."""
    upload = UploadedFile("shell.php", _PNG, content_type="image/png")
    request = MagicMock()
    assert admin._validate_file_upload(request, upload) is False
