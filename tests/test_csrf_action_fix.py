"""
Regression test: form actions should NOT require CSRF when CSRF is not enabled
in the app config. Previously, req.verify_csrf() was called unconditionally for
actions, causing 403 errors even when CSRF was disabled.
"""
from asok import Asok, Request


def make_app(csrf_enabled: bool) -> Asok:
    app = Asok()
    if csrf_enabled:
        app.config["CSRF"] = True
    return app


def make_environ(action: str, csrf_token: str = "") -> dict:
    body = f"_action={action}"
    if csrf_token:
        body += f"&csrf_token={csrf_token}"
    body_bytes = body.encode()
    return {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/test",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(body_bytes)),
        "wsgi.input": __import__("io").BytesIO(body_bytes),
        "wsgi.errors": __import__("io").StringIO(),
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "HTTP_HOST": "localhost",
    }


def make_module_with_action(result_holder: list):
    """Create a fake page module with action_submit that records calls."""

    class FakeModule:
        @staticmethod
        def action_submit(request: Request):
            result_holder.append("called")
            return request.html("<p>ok</p>")

    return FakeModule


def test_action_works_without_csrf_config():
    """When CSRF is disabled, form actions should execute without 403."""
    app = make_app(csrf_enabled=False)
    called = []

    # Register a fake page
    import types
    mod = types.ModuleType("test_page")
    def action_submit(request: Request):
        called.append(True)
        return request.html("<p>ok</p>")
    mod.action_submit = action_submit

    # Patch _load_module to return our fake module
    original_load = app._load_module

    def patched_load(path):
        if "test" in path:
            return mod, path
        return original_load(path)

    app._load_module = patched_load

    environ = make_environ("submit")
    status_holder = []
    headers_holder = []

    def start_response(status, headers):
        status_holder.append(status)
        headers_holder.extend(headers)

    list(app(environ, start_response))

    # Should NOT get 403 - CSRF is disabled
    assert not any("403" in s for s in status_holder), \
        f"Got 403 when CSRF is disabled! status={status_holder}"


def test_action_requires_csrf_when_enabled():
    """When CSRF is enabled, form actions must have a valid CSRF token."""
    app = make_app(csrf_enabled=True)

    import types
    mod = types.ModuleType("test_page2")
    def action_submit(request: Request):
        return request.html("<p>ok</p>")
    mod.action_submit = action_submit

    original_load = app._load_module

    def patched_load(path):
        if "test" in path:
            return mod, path
        return original_load(path)

    app._load_module = patched_load

    # No CSRF token in request → should raise/return 403
    environ = make_environ("submit", csrf_token="")
    status_holder = []

    def start_response(status, headers):
        status_holder.append(status)

    try:
        list(app(environ, start_response))
    except Exception:
        pass  # SecurityError is also acceptable

    # With CSRF enabled and no token, we expect either a 403 status or an exception
    got_403 = any("403" in s for s in status_holder)
    # At minimum the app should not silently succeed
    # (The exact behaviour depends on error handling)
    assert got_403 or not status_holder or "200" not in status_holder[0], \
        f"Expected 403 or error when CSRF enabled and no token, got {status_holder}"
