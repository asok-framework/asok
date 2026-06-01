import os

from asok.core import Asok
from asok.testing import TestClient


def test_toolbar_injection_in_html(tmp_dir):
    pages_dir = os.path.join(tmp_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    page_html_path = os.path.join(pages_dir, "page.html")
    with open(page_html_path, "w", encoding="utf-8") as f:
        f.write("<html><body><h1>Hello World</h1></body></html>")

    app = Asok(root_dir=tmp_dir)
    app.config["DEBUG"] = True
    app.config["TOOLBAR"] = True
    app.config["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
    app.config["DATABASE"] = ":memory:"

    client = TestClient(app)
    response = client.get("/")

    print("HTML Response:")
    print(response.text)

    # Assert that the toolbar trigger is present in the response
    assert (
        '<div id="asok-debug-trigger"' in response.text
        or 'id="asok-debug-trigger"' in response.text
    )
    # Assert it is only injected once, not twice!
    assert response.text.count('id="asok-debug-trigger"') == 1


def test_toolbar_injection_in_404(tmp_dir):
    # Set up custom 404 page
    error_404_dir = os.path.join(tmp_dir, "src", "pages", "404")
    os.makedirs(error_404_dir, exist_ok=True)
    with open(os.path.join(error_404_dir, "page.html"), "w", encoding="utf-8") as f:
        f.write("<html><body><h1>404 Custom Error Page</h1></body></html>")

    app = Asok(root_dir=tmp_dir)
    app.config["DEBUG"] = True
    app.config["TOOLBAR"] = True
    app.config["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
    app.config["DATABASE"] = ":memory:"

    client = TestClient(app)
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert 'id="asok-debug-trigger"' in response.text
    assert response.text.count('id="asok-debug-trigger"') == 1


def test_toolbar_injection_in_500(tmp_dir):
    pages_dir = os.path.join(tmp_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    # Set up custom 500 page
    error_500_dir = os.path.join(pages_dir, "500")
    os.makedirs(error_500_dir, exist_ok=True)
    with open(os.path.join(error_500_dir, "page.html"), "w", encoding="utf-8") as f:
        f.write("<html><body><h1>500 Custom Error Page</h1></body></html>")

    # Create page.py that raises an exception to cause a 500 error
    page_py_path = os.path.join(pages_dir, "page.py")
    with open(page_py_path, "w", encoding="utf-8") as f:
        f.write("def get(request):\n    raise RuntimeError('Simulated 500 error')\n")

    app = Asok(root_dir=tmp_dir)
    app.config["DEBUG"] = False
    app.config["TOOLBAR"] = True
    app.config["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
    app.config["DATABASE"] = ":memory:"

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 500
    assert 'id="asok-debug-trigger"' in response.text
    assert response.text.count('id="asok-debug-trigger"') == 1
