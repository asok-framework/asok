"""Regression: a Form built without an explicit request (e.g. Form.from_model(User))
inside a view must auto-bind to the ongoing request, so it renders the CSRF token
and validates on submit — previously it silently had no token and every POST
failed CSRF validation ("impossible to submit").
"""

import io

import pytest

from asok import Asok, Field, Model
from asok.context import request_context
from asok.forms import Form
from asok.orm import MODELS_REGISTRY
from asok.request import Request


class Contact(Model):
    __tablename__ = "contacts_autobind"
    name = Field.String(nullable=False)


@pytest.fixture
def app():
    a = Asok()
    a.config.update({"SECRET_KEY": "test-secret", "DEBUG": True})
    MODELS_REGISTRY["Contact"] = Contact
    return a


def _req(app, method, body="", cookies=""):
    raw = body.encode()
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/c",
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "asok.app": app,
    }
    if cookies:
        env["HTTP_COOKIE"] = cookies
    return Request(env)


def test_from_model_autobinds_and_renders_csrf(app):
    get_req = _req(app, "GET")
    with request_context(get_req):
        form = Form.from_model(Contact)  # no request passed
        html = str(form.csrf)
    assert 'name="csrf_token"' in html
    assert "value=" in html and get_req.csrf_token_value in html


def test_from_model_autobinds_and_validates_on_post(app):
    tok = "a" * 40
    post_req = _req(
        app, "POST", f"name=Alice&csrf_token={tok}", cookies=f"asok_csrf={tok}"
    )
    with request_context(post_req):
        form = Form.from_model(Contact)  # no request passed
        assert form.validate() is True
        assert form.data["name"] == "Alice"


def test_no_request_context_stays_unbound(app):
    # Outside any request context there is nothing to bind to — no regression.
    form = Form.from_model(Contact)
    assert str(form.csrf) == ""
