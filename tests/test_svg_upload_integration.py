"""Integration tests for SVG upload with automatic sanitization."""

import os

import pytest

from asok.request.upload import UploadedFile


@pytest.fixture
def mock_upload_dir(monkeypatch, tmp_path):
    """Mock the upload directory to use tmp_path."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    # Patch os.getcwd to return tmp_path
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))

    # Create src/partials/uploads structure
    partials_dir = tmp_path / "src" / "partials" / "uploads"
    partials_dir.mkdir(parents=True)

    return str(partials_dir)


def test_svg_upload_sanitizes_dangerous_content(mock_upload_dir):
    """Verify that SVG uploads are automatically sanitized."""
    # Create a malicious SVG with script
    dangerous_svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40" fill="red"/>
        <script>alert('XSS')</script>
    </svg>"""

    # Create UploadedFile
    file = UploadedFile(
        filename="test.svg", content=dangerous_svg, content_type="image/svg+xml"
    )

    # Save to mock directory
    saved_path = file.save(
        "test1.svg",
        validate=True,
        allowed_types=["image/svg+xml"],
        secure_filename=False,
    )

    # Read saved content
    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Verify sanitization
    assert b"<circle" in saved_content  # Circle preserved
    assert b"<script" not in saved_content  # Script removed
    assert b"alert" not in saved_content  # Script content removed
    assert b"XSS" not in saved_content


def test_svg_upload_preserves_valid_content(mock_upload_dir):
    """Verify that valid SVG content is preserved."""
    clean_svg = b"""<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
        <rect x="10" y="10" width="80" height="80" fill="blue" stroke="black"/>
        <circle cx="50" cy="50" r="30" fill="yellow"/>
        <text x="50" y="55" text-anchor="middle" fill="black">Hello</text>
    </svg>"""

    file = UploadedFile(
        filename="clean.svg", content=clean_svg, content_type="image/svg+xml"
    )

    saved_path = file.save(
        "test2.svg",
        validate=True,
        allowed_types=["image/svg+xml"],
        secure_filename=False,
    )

    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Verify all elements preserved
    assert b"<rect" in saved_content
    assert b"<circle" in saved_content
    assert b"<text" in saved_content
    assert b"Hello" in saved_content


def test_svg_upload_removes_event_handlers(mock_upload_dir):
    """Verify that event handlers are removed from SVG."""
    svg_with_handlers = b"""<svg xmlns="http://www.w3.org/2000/svg" onload="malicious()">
        <circle cx="50" cy="50" r="40" onclick="alert('click')" fill="red"/>
    </svg>"""

    file = UploadedFile(
        filename="handlers.svg", content=svg_with_handlers, content_type="image/svg+xml"
    )

    saved_path = file.save(
        "test3.svg",
        validate=True,
        allowed_types=["image/svg+xml"],
        secure_filename=False,
    )

    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Verify handlers removed
    assert b"onload" not in saved_content
    assert b"onclick" not in saved_content
    assert b"malicious" not in saved_content
    # Circle should remain
    assert b"<circle" in saved_content


def test_svg_upload_blocks_javascript_urls(mock_upload_dir):
    """Verify that javascript: URLs are removed."""
    svg_with_js_url = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <a href="javascript:alert('XSS')">
            <text>Click me</text>
        </a>
    </svg>"""

    file = UploadedFile(
        filename="jsurl.svg", content=svg_with_js_url, content_type="image/svg+xml"
    )

    saved_path = file.save(
        "test4.svg",
        validate=True,
        allowed_types=["image/svg+xml"],
        secure_filename=False,
    )

    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Verify javascript: URL removed
    assert b"javascript:" not in saved_content
    assert b"alert" not in saved_content


def test_svg_upload_preserves_gradients(mock_upload_dir):
    """Verify that SVG gradients are preserved."""
    svg_with_gradient = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <defs>
            <linearGradient id="grad1">
                <stop offset="0%" stop-color="red"/>
                <stop offset="100%" stop-color="blue"/>
            </linearGradient>
        </defs>
        <rect width="100" height="100" fill="url(#grad1)"/>
    </svg>"""

    file = UploadedFile(
        filename="gradient.svg", content=svg_with_gradient, content_type="image/svg+xml"
    )

    saved_path = file.save(
        "test5.svg",
        validate=True,
        allowed_types=["image/svg+xml"],
        secure_filename=False,
    )

    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Verify gradient preserved
    assert b"linearGradient" in saved_content or b"lineargradient" in saved_content
    assert b"<stop" in saved_content
    assert b"#grad1" in saved_content


def test_svg_upload_without_svg_in_allowed_types_skips_sanitization(mock_upload_dir):
    """Verify that sanitization only happens when SVG is in allowed types."""
    # This shouldn't happen in practice but test the logic
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40"/>
    </svg>"""

    file = UploadedFile(filename="test.svg", content=svg, content_type="image/svg+xml")

    # If allowed_types doesn't include SVG, validation should fail anyway
    # But if validation is disabled, no sanitization occurs
    saved_path = file.save(
        "test6.svg",
        validate=False,  # Skip validation
        secure_filename=False,
    )

    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Without sanitization (because validate=False and no allowed_types),
    # content should be written as-is
    assert saved_content == svg


def test_svg_upload_malformed_raises_error(mock_upload_dir):
    """Verify that malformed SVG raises an error during sanitization."""
    malformed_svg = b"<svg><circle></svg>"  # Unclosed circle tag

    file = UploadedFile(
        filename="malformed.svg", content=malformed_svg, content_type="image/svg+xml"
    )

    with pytest.raises(ValueError) as excinfo:
        file.save(
            "test7.svg",
            validate=True,
            allowed_types=["image/svg+xml"],
            secure_filename=False,
        )

    assert "sanitization failed" in str(excinfo.value).lower()


def test_svg_upload_sanitizes_when_allowed_types_is_none(mock_upload_dir):
    """Verify that SVG uploads are sanitized even if allowed_types is None, as long as validate is True."""
    dangerous_svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40" fill="red"/>
        <script>alert('XSS')</script>
    </svg>"""

    file = UploadedFile(
        filename="test.svg", content=dangerous_svg, content_type="image/svg+xml"
    )

    # Save to mock directory with allowed_types=None (default) and validate=True (default)
    saved_path = file.save(
        "test_none.svg",
        validate=True,
        allowed_types=None,
        secure_filename=False,
    )

    with open(saved_path, "rb") as f:
        saved_content = f.read()

    # Verify sanitization occurred
    assert b"<circle" in saved_content
    assert b"<script" not in saved_content
    assert b"alert" not in saved_content

