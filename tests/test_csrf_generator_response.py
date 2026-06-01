import os

from asok import Asok


def test_csrf_token_in_generator_response(tmp_dir):
    """Verify that CSRF token is present in response headers for generator/streaming responses."""
    # 1. Create directory structure under tmp_dir
    pages_dir = os.path.join(tmp_dir, "src/pages/test_stream")
    os.makedirs(pages_dir, exist_ok=True)

    # 2. Write the controller page.py
    controller_content = """
def get(request):
    return request.stream("page.html")
"""
    with open(os.path.join(pages_dir, "page.py"), "w") as f:
        f.write(controller_content)

    # 3. Write the template page.html
    template_content = """
<html>
<body>
    <p>Hello Stream</p>
</body>
</html>
"""
    with open(os.path.join(pages_dir, "page.html"), "w") as f:
        f.write(template_content)

    # 4. Initialize Asok app with the temp directory
    app = Asok(root_dir=tmp_dir)
    app.config["SECRET_KEY"] = "test-secret-key-for-csrf-stream"
    app.config["CSRF"] = True

    # 5. Build mock WSGI environ for GET request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/test_stream",
        "wsgi.input": __import__("io").BytesIO(b""),
        "wsgi.errors": __import__("io").StringIO(),
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "HTTP_HOST": "localhost",
        "wsgi.url_scheme": "http",
    }

    status_holder = []
    headers_holder = []

    def start_response(status, headers):
        status_holder.append(status)
        headers_holder.extend(headers)

    # 6. Execute WSGI call
    response_body = list(app(environ, start_response))

    # Verify response status is 200 OK
    assert "200" in status_holder[0], f"Expected 200 OK, got {status_holder}"

    # Verify response body content
    body_content = b"".join(response_body)
    assert b"Hello Stream" in body_content

    # Extract headers dict
    headers_dict = {k.lower(): v for k, v in headers_holder}

    # Verify that X-CSRF-Token is in response headers
    assert "x-csrf-token" in headers_dict, "X-CSRF-Token header was not set in generator response!"
    assert len(headers_dict["x-csrf-token"]) == 64, "X-CSRF-Token should be a valid 64-character hex string"

    # Verify Access-Control-Expose-Headers exposes X-CSRF-Token
    assert "access-control-expose-headers" in headers_dict, "Access-Control-Expose-Headers was not set"
    assert "x-csrf-token" in headers_dict["access-control-expose-headers"].lower(), (
        "X-CSRF-Token was not exposed in Access-Control-Expose-Headers"
    )
