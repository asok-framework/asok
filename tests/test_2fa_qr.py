import base64

from asok.admin import Admin
from asok.request import Request
from asok.utils.qr import generate_qr_data_uri, generate_qr_svg
from tests.test_v017_fixes import DummyApp, MockUser


def test_generate_qr_svg():
    data = "otpauth://totp/Asok%20Admin:test@example.com?secret=JBSWY3DPEHPK3PXP"
    svg = generate_qr_svg(data)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert 'viewBox="0 0 ' in svg
    assert '<rect width="100%" height="100%" fill="#FFFFFF"/>' in svg
    assert '<path d="' in svg


def test_generate_qr_data_uri():
    data = "otpauth://totp/Asok%20Admin:test@example.com?secret=JBSWY3DPEHPK3PXP"
    data_uri = generate_qr_data_uri(data)
    assert data_uri.startswith("data:image/svg+xml;base64,")

    b64_part = data_uri.split(",", 1)[1]
    decoded_svg = base64.b64decode(b64_part).decode("utf-8")
    assert decoded_svg.startswith("<svg")
    assert decoded_svg.endswith("</svg>")


def test_qr_custom_border_and_ecc():
    data = "hello world"
    svg_border_2 = generate_qr_svg(data, border=2)
    svg_border_6 = generate_qr_svg(data, border=6)
    assert 'viewBox="0 0 25 25"' in svg_border_2
    assert 'viewBox="0 0 33 33"' in svg_border_6


def test_2fa_setup_template_renders_local_qr_code(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    MockUser.create_table()
    user = MockUser.create(username="admin_user", is_admin=True)
    user.email = "admin@example.com"
    user.totp_enabled = False
    user.save()

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/2fa-setup",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.user = user
    req._session = {}
    req._flashes = []
    req.flash = lambda c, m: None

    response = admin_instance._twofa_setup(req)
    content = (
        response.body.decode("utf-8") if hasattr(response, "body") else str(response)
    )

    assert "data:image/svg+xml;base64," in content
    assert "api.qrserver.com" not in content

    MockUser.close_connections()
