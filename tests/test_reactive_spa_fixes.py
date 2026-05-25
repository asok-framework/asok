"""Tests for proactive template scanning and lightweight registry-only SPA block asset injection."""

import os

from asok.core import Asok, Request


def test_proactive_template_scan_detects_directives(tmp_path):
    """Test that Asok proactively scans all template files for reactive directives at startup."""
    # Create a temporary directory structure for src
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # 1. Create a page template with a reactive directive
    contact_page = src_dir / "contact.html"
    contact_page.write_text(
        '<div><div asok-state="{ isOpen: true }">Flash Message</div></div>',
        encoding="utf-8",
    )

    # 2. Create another page with no reactive directives
    about_page = src_dir / "about.html"
    about_page.write_text("<div><h1>About Us</h1></div>", encoding="utf-8")

    # Change CWD to the temp path so Asok finds src/contact.html
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        app = Asok()
        # Verify that directives_enabled is set to True due to contact.html having a directive
        assert app.directives_enabled is True
    finally:
        os.chdir(old_cwd)


def test_proactive_template_scan_no_directives(tmp_path):
    """Test that directives_enabled is False if no templates use reactive directives."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    about_page = src_dir / "about.html"
    about_page.write_text("<div><h1>About Us</h1></div>", encoding="utf-8")

    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        app = Asok()
        assert app.directives_enabled is False
    finally:
        os.chdir(old_cwd)


def test_lightweight_registry_injection_for_spa_block_requests():
    """Test that block AJAX requests (with X-Block) only receive the registry script and not the full runtime."""
    app = Asok()
    app.directives_enabled = True

    # 1. Simulate a block AJAX request (HTTP_X_BLOCK header is set)
    environ_block = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/contact",
        "HTTP_X_BLOCK": "essai",
        "wsgi.input": None,
    }
    request_block = Request(environ_block)

    content_block = (
        '<div asok-state="{ isOpen: false }" asok-show="isOpen">'
        '  <button asok-on:click="isOpen = !isOpen">Toggle</button>'
        "</div>"
    )

    nonce = "testnonce123"
    result_block = app._inject_assets(content_block, request_block, nonce)

    # It MUST contain the registry injection (because of the reactive directives in content)
    assert "window.__asok_registry = Object.assign" in result_block
    assert "Toggle" in result_block

    # It MUST NOT contain the full IIFE runtime script
    assert "Asok Reactive Engine" not in result_block
    assert "WeakMap" not in result_block

    # It MUST NOT contain the heavy CSS styles
    assert "asok-dropdown" not in result_block
    assert "asok-table-container" not in result_block


def test_full_runtime_injection_for_normal_requests():
    """Test that normal full-page requests (no X-Block) receive the full directives runtime and stylesheet."""
    app = Asok()
    app.directives_enabled = True

    # 1. Simulate a normal GET request
    environ_normal = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/contact",
        "wsgi.input": None,
    }
    request_normal = Request(environ_normal)

    content_normal = (
        "<html><head></head><body>"
        '<div asok-state="{ isOpen: false }" asok-show="isOpen">'
        '  <button asok-on:click="isOpen = !isOpen">Toggle</button>'
        "</div>"
        "</body></html>"
    )

    nonce = "testnonce123"
    result_normal = app._inject_assets(content_normal, request_normal, nonce)

    # It MUST contain the registry injection
    assert "window.__asok_registry = Object.assign" in result_normal

    # It MUST contain the full IIFE runtime script (minified files use AsokDirectives and WeakMap)
    assert "AsokDirectives" in result_normal or "WeakMap" in result_normal

    # It MUST contain the core stylesheet
    assert "asok-cloak" in result_normal
    assert "asok-dropdown" in result_normal


def test_open_variable_expression_is_allowed():
    """Test that directives can safely use 'open' as a variable or property name (like `{ open: true }` or `open = !open`)."""
    app = Asok()
    app.directives_enabled = True

    # 1. Normal GET request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/contact",
        "wsgi.input": None,
    }
    request = Request(environ)

    # Content using open as state property and toggling it
    content = (
        "<html><head></head><body>"
        '<div asok-state="{ open: true }" asok-show="open">'
        "  <span>Message</span>"
        '  <button asok-on:click="open = !open">Close</button>'
        "</div>"
        "</body></html>"
    )

    # This should not raise a ValueError (SECURITY: Unsafe expression in asok-state)
    result = app._inject_assets(content, request, "testnonce123")

    # Assert that it compiled and created registry functions for the expressions
    assert "window.__asok_registry = Object.assign" in result
    assert (
        '"open = !open"' in result
        or "f28c548fd9b1" in result
        or "open = !open" in result
    )


def test_production_mode_minification_and_assets():
    """Test that in production mode (DEBUG=False):
    1. HTML is minified automatically by the SmartStreamer/response lifecycle.
    2. Reactive directives are successfully pre-compiled and registered.
    3. Minified HTML has correctly injected assets and CSP nonces.
    """
    app = Asok()
    app.config["DEBUG"] = False
    app.directives_enabled = True

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/contact",
        "wsgi.input": None,
    }
    request = Request(environ)

    # Multi-line template that should be minified, with multiple spaces and comments
    raw_html = """
    <html>
        <head>
            <!-- A comment that should be minified away -->
            <title>Test Page</title>
        </head>
        <body>
            <div asok-state="{ open: true }" asok-show="open">
                <span>Hello, Production!</span>
                <button type="button" asok-on:click="open = !open">
                    Toggle State
                </button>
            </div>
        </body>
    </html>
    """

    # We want to run this through the SmartStreamer to simulate full WSGI response
    from asok.core import SmartStreamer

    generator = [raw_html]
    streamer = SmartStreamer(generator, request, app)

    # Collect the yielded chunks
    chunks = list(streamer)
    assert len(chunks) == 1

    content_bytes = chunks[0]
    content_str = content_bytes.decode("utf-8")

    # 1. Verify standard comments are minified away
    assert "A comment that should be minified away" not in content_str

    # 2. Verify HTML whitespace is collapsed/minified
    assert "    <html>" not in content_str
    assert "        <body>" not in content_str

    # 3. Verify reactive directives are compiled and registered under Zero-Eval
    assert "window.__asok_registry = Object.assign" in content_str

    # 4. Verify that we didn't lose the HTML structure and it contains our dynamic nonce
    assert '<script nonce="' in content_str


def test_template_with_state_and_if_directive():
    """Test that a <template> tag carrying both `asok-state` and `asok-if` has both compiled and registered correctly."""
    app = Asok()
    app.directives_enabled = True

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/contact",
        "wsgi.input": None,
    }
    request = Request(environ)

    content = (
        "<html><head></head><body>"
        '<template asok-state="{ open: true }" asok-if="open">'
        "  <div>"
        '    <button type="button" asok-on:click="open = !open">Close</button>'
        "  </div>"
        "</template>"
        "</body></html>"
    )

    result = app._inject_assets(content, request, "testnonce123")

    # Verify that BOTH asok-state and asok-if were processed
    assert "asok-state-ref=" in result
    assert "asok-if-ref=" in result
    assert "window.__asok_registry = Object.assign" in result


def test_template_conditional_chain_directives():
    """Test that a chain of <template> tags (asok-if, asok-elif, asok-else) are all parsed, compiled, and registered correctly."""
    app = Asok()
    app.directives_enabled = True

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/test-chain",
        "wsgi.input": None,
    }
    request = Request(environ)

    content = (
        "<html><head></head><body>"
        '<template asok-state="{ status: 2 }" asok-if="status == 1">'
        "  <div>One</div>"
        "</template>"
        '<template asok-elif="status == 2">'
        "  <div>Two</div>"
        "</template>"
        "<template asok-else>"
        "  <div>Other</div>"
        "</template>"
        "</body></html>"
    )

    result = app._inject_assets(content, request, "testnonce123")

    # Verify that all conditional directives in the chain were compiled to ref versions
    assert "asok-state-ref=" in result
    assert "asok-if-ref=" in result
    assert "asok-elif-ref=" in result
    assert "asok-else" in result
    assert "window.__asok_registry = Object.assign" in result


def test_spa_js_targeted_cleanup_and_init():
    """Verify that asok_spa.min.js uses targeted cleanup and initialization instead of forceInit."""
    app = Asok()
    spa_js = app.get_asset("asok_spa.min.js")

    # It should not call forceInit on the entire parent container
    assert "forceInit" not in spa_js

    # It should call cleanupOld
    assert "cleanupOld" in spa_js

    # It should call window.AsokDirectives.init or window.Asok.init
    assert "init" in spa_js


def test_nested_arrow_functions_and_setTimeout():
    """Test that nested arrow functions and client-side setTimeout are allowed and correctly validated."""
    app = Asok()
    app.directives_enabled = True

    # 1. Normal GET request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.input": None,
    }
    request = Request(environ)

    # Valid expression containing nested arrow functions and setTimeout
    content = (
        "<html><head></head><body>"
        '<button type="button" asok-on:click="navigator.clipboard.writeText(\'pip install asok\').then(() => { copied = true; setTimeout(() => copied = false, 2000) })">'
        "Copy"
        "</button>"
        "</body></html>"
    )

    # This should not raise a ValueError
    result = app._inject_assets(content, request, "testnonce123")
    assert "window.__asok_registry = Object.assign" in result
    assert "copied = true" in result
    assert "copied = false" in result

    # 2. Test that dangerous functions are still blocked inside arrow function bodies
    unsafe_content = (
        "<html><head></head><body>"
        '<button type="button" asok-on:click="navigator.clipboard.writeText(\'pip install asok\').then(() => { eval(\'unsafe\') })">'
        "Copy"
        "</button>"
        "</body></html>"
    )
    import pytest
    with pytest.raises(ValueError, match="SECURITY: Unsafe expression"):
        app._inject_assets(unsafe_content, request, "testnonce123")


def test_ternary_operator_validation():
    """Test that JS ternary operators are allowed, converted to Python if-else expressions, and validated."""
    app = Asok()
    app.directives_enabled = True

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.input": None,
    }
    request = Request(environ)

    # Valid expression containing JS ternary operator
    content = (
        "<html><head></head><body>"
        '<div asok-bind:class="\'p-3 text-xs \' + (activeTab === \'basic\' ? \'active font-bold\' : \'font-medium\')">'
        "Content"
        "</div>"
        "</body></html>"
    )

    # This should not raise a ValueError
    result = app._inject_assets(content, request, "testnonce123")
    assert "window.__asok_registry = Object.assign" in result
    assert "activeTab == 'basic'" in result or "activeTab" in result

    # Test that dangerous functions are still blocked inside ternary operands
    unsafe_content = (
        "<html><head></head><body>"
        '<div asok-bind:class="activeTab === \'basic\' ? eval(\'unsafe\') : \'font-medium\'">'
        "Content"
        "</div>"
        "</body></html>"
    )
    import pytest
    with pytest.raises(ValueError, match="SECURITY: Unsafe expression"):
        app._inject_assets(unsafe_content, request, "testnonce123")



