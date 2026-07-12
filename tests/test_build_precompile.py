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

    # Check that directives_registry.js (or .min.js if minified) was generated
    registry_file_path = os.path.join(
        build_root, "src", "partials", "js", "directives_registry.js"
    )
    registry_min_file_path = os.path.join(
        build_root, "src", "partials", "js", "directives_registry.min.js"
    )

    actual_path = (
        registry_min_file_path
        if os.path.exists(registry_min_file_path)
        else registry_file_path
    )
    assert os.path.exists(actual_path)

    with open(actual_path, "r", encoding="utf-8") as f:
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


def test_template_interpolated_directive_not_baked_into_registry(tmp_path):
    """A directive expression interpolating template vars ({{ }}) must NOT be
    baked into the static registry (which would produce invalid JS and crash
    esbuild). It stays raw at build time and is compiled per-request instead."""
    from asok.core._asset_injector import (
        _content_has_raw_expr_directive,
        precompile_directives,
    )

    class _FakeApp:
        def _validate_directive_expression(self, v):
            return True

        def _is_async_expression_cached(self, e):
            return False

    app = _FakeApp()

    raw = (
        '<div asok-state="{ count : {{count}} }">'
        '<button asok-on:click="count++">'
        '<span asok-text="count"></span></button></div>'
    )
    processed, registry = precompile_directives(app, raw)

    # The template placeholder must never leak into the compiled registry.
    assert not any("{{" in expr for expr in registry.values())
    # Static expressions are still compiled; the dynamic asok-state stays raw.
    assert 'asok-state="{ count : {{count}} }"' in processed
    assert "asok-on-ref:click=" in processed
    assert "asok-text-ref=" in processed

    # After the template renders ({{count}} -> 5), the leftover raw directive is
    # detected and compiled per-request into a supplementary registry.
    rendered = '<div asok-state="{ count : 5 }"><span asok-text-ref="x"></span></div>'
    assert _content_has_raw_expr_directive(rendered) is True
    _, supp = precompile_directives(app, rendered)
    assert any("{ count : 5 }" in expr for expr in supp.values())

    # Fully-precompiled content must not trigger the extra runtime pass.
    assert _content_has_raw_expr_directive('<div asok-state-ref="h"></div>') is False


def test_build_assets_minification(tmp_path):
    import shutil

    root_dir = str(tmp_path)
    src_dir = os.path.join(root_dir, "src")
    partials_js_dir = os.path.join(src_dir, "partials", "js")
    pages_dir = os.path.join(src_dir, "pages")

    os.makedirs(partials_js_dir, exist_ok=True)
    os.makedirs(pages_dir, exist_ok=True)

    # Copy esbuild binary from the project root
    real_root = os.path.dirname(os.path.dirname(__file__))
    real_bin = os.path.join(real_root, ".asok", "bin")
    dest_bin = os.path.join(root_dir, ".asok", "bin")
    if os.path.exists(real_bin):
        shutil.copytree(real_bin, dest_bin)

    # Create unminified global static asset
    with open(os.path.join(partials_js_dir, "base.js"), "w", encoding="utf-8") as f:
        f.write("function hello() {\n  console.log('hello world');\n}")

    # Create pre-existing minified global static asset
    with open(
        os.path.join(partials_js_dir, "other.min.js"), "w", encoding="utf-8"
    ) as f:
        f.write("function other(){console.log('other');}")

    # Create unminified scoped asset
    with open(os.path.join(pages_dir, "home.js"), "w", encoding="utf-8") as f:
        f.write("function home() {\n  console.log('home');\n}")

    # Run build
    run_build(root=root_dir, keep_source=True, output="dist")

    dist_root = os.path.join(root_dir, "dist")
    dist_partials_js = os.path.join(dist_root, "src", "partials", "js")
    dist_pages = os.path.join(dist_root, "src", "pages")

    # Verify global static asset compilation
    assert os.path.exists(os.path.join(dist_partials_js, "base.min.js"))
    assert not os.path.exists(os.path.join(dist_partials_js, "base.js"))
    assert os.path.exists(os.path.join(dist_partials_js, "other.min.js"))
    assert not os.path.exists(os.path.join(dist_partials_js, "other.min.min.js"))

    # Verify scoped asset in-place minification
    assert os.path.exists(os.path.join(dist_pages, "home.js"))
    assert not os.path.exists(os.path.join(dist_pages, "home.min.js"))

    # Verify that the scoped asset home.js and compiled base.min.js are minified
    with open(os.path.join(dist_pages, "home.js"), "r", encoding="utf-8") as f:
        home_content = f.read()
    assert (
        "\n" not in home_content.strip()
        or "function home(){console.log" in home_content
    )

    with open(
        os.path.join(dist_partials_js, "base.min.js"), "r", encoding="utf-8"
    ) as f:
        base_content = f.read()
    assert (
        "\n" not in base_content.strip()
        or "function hello(){console.log" in base_content
    )
