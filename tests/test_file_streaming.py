from asok.core import Asok
from asok.testing import TestClient


def test_file_streaming(tmp_path):
    # Create a "large" file (> 5 MB)
    file_path = tmp_path / "large_file.txt"
    content = b"0" * (6 * 1024 * 1024)  # 6 MB
    file_path.write_bytes(content)

    app = Asok()
    app.config["DEBUG"] = True

    # Mock the uploads directory to point to our tmp_path
    # send_file uses src/partials/uploads relative to app.root_dir
    uploads_dir = tmp_path / "src" / "partials" / "uploads"
    uploads_dir.mkdir(parents=True)

    # Copy file to mock uploads
    target_file = uploads_dir / "test.txt"
    target_file.write_bytes(content)

    app.root_dir = str(tmp_path)

    client = TestClient(app)

    # Create a page.py to handle the download
    download_dir = tmp_path / "src" / "pages" / "download"
    download_dir.mkdir(parents=True)
    page_py = download_dir / "page.py"
    page_py.write_text("""
def get(request):
    return request.send_file("test.txt")
""")

    client = TestClient(app)
    response = client.get("/download")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/plain"
    assert response.headers["Content-Length"] == str(len(content))
    assert "Content-Security-Policy" in response.headers
    assert response.body == content

    # Test file size limit (100 MB)
    large_file = uploads_dir / "too_large.bin"
    # Create a 101 MB file (simulated by seeking)
    with open(large_file, "wb") as f:
        f.seek(101 * 1024 * 1024 - 1)
        f.write(b"0")

    response = client.get("/download?file=too_large.bin")
    # Need to update page.py to accept filename from param for testing
    page_py.write_text("""
def get(request):
    fname = request.args.get('file', 'test.txt')
    return request.send_file(fname)
""")
    response = client.get("/download?file=too_large.bin")
    assert response.status_code == 413

    # Verify it was actually streamed
    # (Checking internal environ flag if possible, but TestClient might hide it)
    # Actually, TestClient calls the app, so we can check if it returned a generator
    # but TestClient's get() method consumes the generator.


def test_file_streaming_subfolder(tmp_path):
    app = Asok()
    app.config["DEBUG"] = True

    # Mock the uploads directory
    uploads_dir = tmp_path / "src" / "partials" / "uploads"
    images_dir = uploads_dir / "images"
    images_dir.mkdir(parents=True)

    # Write a test SVG file inside images sub-folder
    logo_file = images_dir / "logo.svg"
    logo_content = b"<svg></svg>"
    logo_file.write_bytes(logo_content)

    app.root_dir = str(tmp_path)
    client = TestClient(app)

    # Create a page.py to handle the download from subfolder
    download_dir = tmp_path / "src" / "pages" / "logo"
    download_dir.mkdir(parents=True)
    page_py = download_dir / "page.py"
    page_py.write_text("""
def get(request):
    return request.send_file("images/logo.svg")
""")

    response = client.get("/logo")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/svg+xml"
    assert response.body == logo_content

    # Test path traversal attack rejection
    escape_dir = tmp_path / "src" / "pages" / "escape"
    escape_dir.mkdir(parents=True)
    escape_page = escape_dir / "page.py"
    escape_page.write_text("""
def get(request):
    return request.send_file("../../../etc/passwd")
""")

    response = client.get("/escape")
    assert response.status_code == 403
