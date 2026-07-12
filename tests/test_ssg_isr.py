import os

from asok.core.asok import Asok
from asok.request import Request


# Define dummy page for test
class DummyPage:
    REVALIDATE = 5

    def render(self, req):
        return "<div>Raw Content</div>"


def test_ssg_isr_flow(tmp_path):
    # Setup test workspace structures
    root_dir = str(tmp_path)
    pages_dir = os.path.join(root_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    # Create a dummy static page and a dynamic page
    with open(os.path.join(pages_dir, "about.html"), "w") as f:
        f.write("<h1>About Us</h1>")

    # Instantiate Asok app pointing to mock workspace
    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"

    # Test SSG pre-generation
    app.pre_generate_ssg_site()

    cache_dir = app._get_ssg_cache_dir()
    about_cache = app._get_ssg_cache_file("/about")

    assert os.path.isdir(cache_dir)
    assert os.path.exists(about_cache)

    with open(about_cache, "r", encoding="utf-8") as f:
        cached_content = f.read()
    assert "<h1>About Us</h1>" in cached_content


def test_request_cache_serving(tmp_path):
    root_dir = str(tmp_path)
    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"
    app.config["DEBUG"] = False

    # Create cached file directly
    cache_dir = app._get_ssg_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = app._get_ssg_cache_file("/about")
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write("<h1>About Cached</h1><script>console.log(1)</script>")

    # Mock environment and request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/about",
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
    }
    request = Request(environ)
    request._nonce = "test-nonce-csp-entropy"

    captured_headers = []
    captured_status = ""

    def start_response(status, headers):
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = headers

    # Execute SSG intercept
    # We mock _resolve_route to return our page_file
    app._resolve_route = lambda parts: (
        os.path.join(root_dir, "src/pages/about.html"),
        {},
    )

    response = app._handle_ssg_isr_request(request, environ, start_response)

    assert response is not None
    assert b"<h1>About Cached</h1>" in response[0]
    assert captured_status == "200 OK"

    headers_dict = dict(captured_headers)
    assert headers_dict.get("X-Asok-SSG-Cache") == "HIT"
    # Nonce and CSP should have been dynamically injected
    assert "nonce" in response[0].decode("utf-8")


def test_ssg_pre_render_generator_response(tmp_path):
    root_dir = str(tmp_path)
    pages_dir = os.path.join(root_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    # Create page template
    with open(os.path.join(pages_dir, "page.html"), "w") as f:
        f.write("<html><body>Home Page</body></html>")

    # Create companion controller returning a generator
    controller_code = """
SSG = True

def render(request):
    def generate():
        yield "<html>"
        yield "<body>"
        yield "Home Page"
        yield "</body>"
        yield "</html>"
    return generate()
"""
    with open(os.path.join(pages_dir, "page.py"), "w") as f:
        f.write(controller_code)

    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"

    # Pre-generate the static site
    app.pre_generate_ssg_site()

    home_cache = app._get_ssg_cache_file("/")
    assert os.path.exists(home_cache)

    with open(home_cache, "r", encoding="utf-8") as f:
        cached_content = f.read()
    assert cached_content == "<html><body>Home Page</body></html>"


def test_ssg_pre_render_resolves_minified_assets(tmp_path):
    root_dir = str(tmp_path)
    pages_dir = os.path.join(root_dir, "src", "pages")
    partials_js_dir = os.path.join(root_dir, "src", "partials", "js")
    os.makedirs(pages_dir, exist_ok=True)
    os.makedirs(partials_js_dir, exist_ok=True)

    # 1. Create a minified asset base.min.js so it exists and resolver can find it
    with open(os.path.join(partials_js_dir, "base.min.js"), "w") as f:
        f.write("console.log(1);")

    # 2. Create a page template that uses static('js/base.js')
    with open(os.path.join(pages_dir, "about.html"), "w") as f:
        f.write("<h1>About</h1><script src=\"{{ static('js/base.js') }}\"></script>")

    # 3. Instantiate Asok app
    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"
    app.config["DEBUG"] = False

    app.pre_generate_ssg_site()

    about_cache = app._get_ssg_cache_file("/about")
    assert os.path.exists(about_cache)

    with open(about_cache, "r", encoding="utf-8") as f:
        cached_content = f.read()

    # It must contain base.min.js and NOT base.js
    assert "js/base.min.js" in cached_content
    assert "js/base.js" not in cached_content


def test_ssg_serving_injects_scoped_assets(tmp_path):
    import io

    root_dir = str(tmp_path)
    pages_dir = os.path.join(root_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    # 1. Create a page template
    with open(os.path.join(pages_dir, "about.html"), "w") as f:
        f.write("<html><head></head><body><h1>About Us</h1></body></html>")

    # 2. Create a scoped CSS file for this page
    with open(os.path.join(pages_dir, "about.css"), "w") as f:
        f.write(".btn { color: red; }")

    # 3. Instantiate Asok app (with DEBUG=False to check prod asset injection)
    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"
    app.config["DEBUG"] = False

    # Pre-generate static cache
    app.pre_generate_ssg_site()

    about_cache = app._get_ssg_cache_file("/about")
    assert os.path.exists(about_cache)

    # 4. Perform a request to /about which will be served from the SSG cache
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/about",
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "8000",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "HTTP_ACCEPT": "text/html",
    }

    status_headers = []

    def start_response(status, headers, exc_info=None):
        status_headers.append((status, headers))

    response = app(environ, start_response)
    body = b"".join(response).decode("utf-8")

    # The served page must contain the dynamically injected scoped CSS!
    assert '<style id="asok-scoped-css"' in body
    assert '[data-page-id="about"]' in body
    assert ".btn" in body


def test_ssg_state_signed_with_real_key_hydrates_in_prod():
    """SSG state signed at build time with the production key hydrates in prod."""
    import os

    from asok.component import Component

    class SimpleComp(Component):
        count = 0

        def render(self):
            return "<div></div>"

    real_key = "real-production-secret-key-32-chars"

    # 1. Simulate the build signing pre-rendered state with the real SECRET_KEY.
    os.environ["ASOK_BUILD"] = "true"
    try:
        comp = SimpleComp(count=42)
        state_signed = comp._sign_state(real_key)
    finally:
        del os.environ["ASOK_BUILD"]

    # 2. The running server (same real key) restores it.
    from asok.ws.live import _restore_component_instance

    restored = _restore_component_instance(
        SimpleComp, state_signed, real_key, comp._cid
    )
    assert restored is not None
    assert restored.count == 42


def test_ssg_zero_timestamp_state_never_expires():
    """Build-time state (_ts == 0) has no expiration but stays key-authenticated."""
    import json
    import os

    from asok.component import Component, _verify_signature

    class SimpleComp(Component):
        count = 0

        def render(self):
            return "<div></div>"

    real_key = "real-production-secret-key-32-chars"

    os.environ["ASOK_BUILD"] = "true"
    try:
        comp = SimpleComp(count=42)
        state_signed = comp._sign_state(real_key)
    finally:
        del os.environ["ASOK_BUILD"]

    # Signed with the real key (not the public build placeholder) and _ts == 0.
    assert (
        _verify_signature(state_signed, "static-build-key-temporary-32-chars-long-key")
        is None
    )
    data_str = _verify_signature(state_signed, real_key)
    assert data_str is not None
    assert json.loads(data_str)["_ts"] == 0

    # Restores regardless of age because _ts == 0 signals no expiration.
    from asok.ws.live import _restore_component_instance

    restored = _restore_component_instance(
        SimpleComp, state_signed, real_key, comp._cid
    )
    assert restored is not None
    assert restored.count == 42


def test_ssg_state_signed_with_public_build_key_is_rejected_in_prod():
    """The public build key must NOT authenticate state against the running key."""
    from asok.component import Component

    class SimpleComp(Component):
        count = 0

        def render(self):
            return "<div></div>"

    # State forged/signed with the well-known public build key.
    comp = SimpleComp(count=42)
    state_signed = comp._sign_state("static-build-key-temporary-32-chars-long-key")

    from asok.ws.live import _restore_component_instance

    restored = _restore_component_instance(
        SimpleComp,
        state_signed,
        "real-production-secret-key-32-chars",
        comp._cid,
    )

    # Must be rejected: the public key is never trusted at runtime.
    assert restored is None


def test_ssg_pre_renders_and_caches_error_pages(tmp_path):
    root_dir = str(tmp_path)
    pages_dir = os.path.join(root_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    # Create a 404 page directory and index.py
    os.makedirs(os.path.join(pages_dir, "404"), exist_ok=True)
    with open(os.path.join(pages_dir, "404", "index.py"), "w") as f:
        f.write(
            "SSG = True\n\ndef render(req):\n    req.status_code(404)\n    return '<h1>Page Not Found</h1>'\n"
        )

    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "test-secret-key-32-chars-length-security"

    app.pre_generate_ssg_site()

    cache_file = app._get_ssg_cache_file("/404")
    assert os.path.exists(cache_file)
    with open(cache_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "<h1>Page Not Found</h1>" in content
