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
