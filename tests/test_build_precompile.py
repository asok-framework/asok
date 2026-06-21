import os

from asok.cli.build import run_build
from asok.core.asok import Asok
from asok.request import Request


def test_build_time_directives_precompilation(tmp_path):
    # Setup temporary project workspace
    root_dir = str(tmp_path)
    src_dir = os.path.join(root_dir, "src")
    pages_dir = os.path.join(src_dir, "pages")
    partials_js_dir = os.path.join(src_dir, "partials", "js")

    os.makedirs(pages_dir, exist_ok=True)
    os.makedirs(partials_js_dir, exist_ok=True)

    # 1. Create a template page with various directives
    template_content = """
    <div asok-state="{ isVisible: true, clickCount: 0 }">
        <p asok-show="isVisible">Hello World</p>
        <button asok-on:click="clickCount++">Click Me</button>
        <div asok-class:active="clickCount > 5">Active State</div>
    </div>
    """

    with open(os.path.join(pages_dir, "home.html"), "w", encoding="utf-8") as f:
        f.write(template_content)

    # 2. Run the build compiler (output named "dist")
    run_build(root=root_dir, keep_source=True, output="dist")

    # 3. Verify files generated in build distribution folder
    build_root = os.path.join(root_dir, "dist")

    # Check that template was transformed (compiled/rewritten)
    build_template_path = os.path.join(build_root, "src", "pages", "home.html")
    assert os.path.exists(build_template_path)

    with open(build_template_path, "r", encoding="utf-8") as f:
        transformed_html = f.read()

    # The attributes should be rewritten to -ref versions
    assert "asok-state-ref=" in transformed_html
    assert "asok-show-ref=" in transformed_html
    assert "asok-on-ref:click=" in transformed_html
    assert "asok-class-ref:active=" in transformed_html

    # The original attributes should not be present anymore
    assert 'asok-show="isVisible"' not in transformed_html
    assert 'asok-on:click="clickCount++"' not in transformed_html

    # Check that directives_registry.js was generated
    registry_file_path = os.path.join(
        build_root, "src", "partials", "js", "directives_registry.js"
    )
    assert os.path.exists(registry_file_path)

    with open(registry_file_path, "r", encoding="utf-8") as f:
        registry_js = f.read()

    # Verify that the registry contains the compiled JS functions for our expressions
    assert "window.__asok_registry" in registry_js
    assert "isVisible" in registry_js
    assert "clickCount++" in registry_js or "clickCount += 1" in registry_js
    assert "clickCount > 5" in registry_js


def test_runtime_precompiled_asset_injection(tmp_path):
    # Setup temporary project representing the production distribution
    root_dir = str(tmp_path)
    src_dir = os.path.join(root_dir, "src")
    pages_dir = os.path.join(src_dir, "pages")
    partials_js_dir = os.path.join(src_dir, "partials", "js")

    os.makedirs(pages_dir, exist_ok=True)
    os.makedirs(partials_js_dir, exist_ok=True)

    # Create directives_registry.js directly
    registry_file_path = os.path.join(partials_js_dir, "directives_registry.js")
    with open(registry_file_path, "w", encoding="utf-8") as f:
        f.write('window.__asok_registry = { "hash123": function() { return true; } };')

    # Instantiate the application in non-debug mode (production)
    app = Asok(root_dir=root_dir)
    app.config["DEBUG"] = False
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"

    # Verify app configuration and path setup
    assert app.config.get("DEBUG") is False
    assert os.path.exists(
        os.path.join(app._partials_path, "js", "directives_registry.js")
    )

    # Mock request and environment
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
    }
    request = Request(environ)
    request._nonce = "test-nonce-csp-entropy"

    # Test html content with a precompiled template marker
    transformed_content = '<p asok-show-ref="hash123">Content</p>'

    # Inject assets
    result_html = app._inject_assets(
        content=transformed_content, request=request, nonce="test-nonce-csp-entropy"
    )

    # Ensure runtime precompilation was bypassed (no inline functions generated)
    # The output should NOT have an inline window.__asok_registry definition
    assert "window.__asok_registry = Object.assign" not in result_html

    # It should inject a link to the static registry file
    assert "/js/directives_registry.js?v=" in result_html
    assert 'nonce="test-nonce-csp-entropy"' in result_html
    # It should still include the directives runner script
    assert (
        "asok_directives.min.js" in request._asok_pending_scripts
        or "window.AsokDirectives" in result_html
    )
