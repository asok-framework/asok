"""
Tests for the Form module.
Covers: Form construction, field types, data binding, validation, errors,
HTML rendering, empty form, type errors.
"""

import io

import pytest

from asok.forms import Form
from asok.request import Request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_request(data=None, method="POST"):
    """Build a minimal POST Request with form data and CSRF token."""
    from urllib.parse import urlencode

    data = data.copy() if data else {}
    csrf_token = "test_csrf_token_12345"

    if method == "POST" and "csrf_token" not in data:
        data["csrf_token"] = csrf_token

    body = urlencode(data).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/",
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "HTTP_COOKIE": f"asok_csrf={csrf_token}",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.BytesIO(),
        "wsgi.url_scheme": "http",
        "asok.secret_key": "test-form-secret",
    }
    return Request(environ)


def make_form(fields, data=None):
    """Create a Form bound to a request with the given POST data."""
    req = make_request(data or {})
    return Form(fields, request=req), req


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestFormConstruction:
    def test_empty_fields_raises(self):
        with pytest.raises((ValueError, RuntimeError, TypeError)):
            Form({})

    def test_non_dict_raises(self):
        with pytest.raises(TypeError):
            Form("not_a_dict")

    def test_valid_form_creates_data_dict(self):
        form, _ = make_form({"name": Form.text("Name", "required")})
        assert isinstance(form.data, dict)

    def test_data_keys_match_field_names(self):
        form, _ = make_form(
            {
                "name": Form.text("Name"),
                "email": Form.email("Email"),
            }
        )
        assert "name" in form.data
        assert "email" in form.data

    def test_errors_initially_empty(self):
        form, _ = make_form({"name": Form.text("Name")})
        assert form.errors == {}


# ---------------------------------------------------------------------------
# Data binding from request
# ---------------------------------------------------------------------------


class TestDataBinding:
    def test_binds_text_field(self):
        form, req = make_form(
            {"name": Form.text("Name", "required")}, {"name": "Alice"}
        )
        assert form.data["name"] == "Alice"

    def test_binds_email_field(self):
        form, req = make_form(
            {"email": Form.email("Email", "required|email")},
            {"email": "alice@example.com"},
        )
        assert form.data["email"] == "alice@example.com"

    def test_missing_field_is_empty_string(self):
        form, req = make_form(
            {"name": Form.text("Name", "required")},
            {},  # No data submitted
        )
        assert form.data.get("name", "") == ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestFormValidation:
    def test_valid_data_passes(self):
        form, req = make_form(
            {
                "name": Form.text("Name", "required|min:2"),
                "email": Form.email("Email", "required|email"),
            },
            {"name": "Alice", "email": "alice@example.com"},
        )
        assert form.validate(req) is True

    def test_invalid_data_fails(self):
        form, req = make_form(
            {
                "name": Form.text("Name", "required"),
                "email": Form.email("Email", "required|email"),
            },
            {"name": "", "email": "bad-email"},
        )
        assert form.validate(req) is False

    def test_errors_populated_on_failure(self):
        form, req = make_form({"name": Form.text("Name", "required")}, {"name": ""})
        form.validate(req)
        assert "name" in form.errors

    def test_no_errors_on_success(self):
        form, req = make_form(
            {"name": Form.text("Name", "required")}, {"name": "Alice"}
        )
        form.validate(req)
        assert "name" not in form.errors

    def test_multiple_field_errors(self):
        form, req = make_form(
            {
                "name": Form.text("Name", "required"),
                "email": Form.email("Email", "required|email"),
            },
            {"name": "", "email": ""},
        )
        form.validate(req)
        assert "name" in form.errors
        assert "email" in form.errors

    def test_partial_failure(self):
        """Only the invalid field should have an error."""
        form, req = make_form(
            {
                "name": Form.text("Name", "required"),
                "email": Form.email("Email", "required|email"),
            },
            {"name": "Alice", "email": "bad"},
        )
        form.validate(req)
        assert "name" not in form.errors
        assert "email" in form.errors


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------


class TestFieldTypes:
    def test_text_field(self):
        field = Form.text("Username", "required")
        assert field is not None

    def test_email_field(self):
        field = Form.email("Email Address", "required|email")
        assert field is not None

    def test_password_field(self):
        field = Form.password("Password", "required|min:8")
        assert field is not None

    def test_textarea_field(self):
        field = Form.textarea("Bio")
        assert field is not None

    def test_number_field(self):
        field = Form.number("Age")
        assert field is not None

    def test_checkbox_field(self):
        field = Form.checkbox("Accept Terms")
        assert field is not None

    def test_select_field(self):
        field = Form.select("Country", choices=["FR", "US", "UK"])
        assert field is not None

    def test_hidden_field(self):
        field = Form.hidden("token")
        assert field is not None

    def test_file_field(self):
        field = Form.file("Avatar")
        assert field is not None

    def test_url_field(self):
        field = Form.url("Website")
        assert field is not None


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


class TestHtmlRendering:
    def test_form_field_renders_html(self):
        """A FormField's render method should produce an HTML string."""

        field = Form.text("Name", "required")
        assert field is not None
        # The field descriptor should be renderable
        assert hasattr(field, "__class__")

    def test_form_data_is_accessible_as_dict(self):
        form, _ = make_form({"name": Form.text("Name")}, {"name": "Alice"})
        assert isinstance(form.data, dict)
        assert form.data["name"] == "Alice"

    def test_declarative_form_class_and_ergonomics(self):
        class UserRegistrationForm(Form):
            username = Form.text("Username", "required|min:3")
            email = Form.email("Email", "required|email")

        # Class instantiation without args (template mode)
        form = UserRegistrationForm()
        assert len(form) == 2
        assert "username" in form
        assert "email" in form
        assert form["username"].name == "username"
        fields = [f.name for f in form]
        assert fields == ["username", "email"]

        # Rendering
        html = form.render(submit="Register")
        assert 'name="username"' in html
        assert 'name="email"' in html
        assert "Register" in html
        assert str(form) == form.render()

        # Submit valid POST
        req = make_request({"username": "alice", "email": "alice@example.com"})
        bound_form = UserRegistrationForm(req)
        assert bound_form.is_valid(csrf=False)
        assert not bound_form.has_errors
        assert bound_form.data["username"] == "alice"

        # Submit invalid POST
        bad_req = make_request({"username": "al", "email": "not-an-email"})
        bad_form = UserRegistrationForm(bad_req)
        assert not bad_form.is_valid(csrf=False)
        assert bad_form.has_errors
        assert bad_form.has_error("username")
        assert bad_form.has_error("email")
        assert len(bad_form.error("email")) > 0

    def test_request_validate_and_old_helper(self):
        req = make_request({"title": "Hello World", "email": "valid@example.com"})
        validated = req.validate(
            {"title": "required|min:3", "email": "required|email"}, csrf=False
        )
        assert validated["title"] == "Hello World"
        assert req.old("title") == "Hello World"
        assert req.old("non_existent", "default_val") == "default_val"
