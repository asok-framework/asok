import pytest

from asok.core import Asok, Request
from asok.forms.render import (
    render_autocomplete,
    render_cascading,
    render_daterange,
    render_dropdown,
    render_dropzone,
    render_files,
    render_image,
    render_otp,
    render_phone,
    render_rating,
    render_signature,
    render_tags,
    render_timerange,
    render_transfer,
    render_treeselect,
    render_wysiwyg,
)


# Mock Field object to satisfy renderer interface
class MockField:
    def __init__(
        self, name, value=None, choices=None, items=None, rules="", attrs=None
    ):
        self.name = name
        self.value = value
        self.choices = choices or []
        self.items = items or []
        self.rules = rules
        self.attrs = attrs or {}
        self.item_meta = {
            "title": "name",
            "subtitle": None,
            "image": None,
            "searchable": True,
        }


def test_form_renderers_directive_security():
    """Verify that all form fields render HTML that passes Asok directive security validation."""
    app = Asok()
    app.directives_enabled = True

    # 1. Prepare render inputs
    fields_to_test = [
        (
            "dropdown",
            lambda: render_dropdown(
                MockField("avatar_dropdown", value="1", choices=[("1", "User 1")]),
                "",
                {},
                {},
            ),
        ),
        (
            "image",
            lambda: render_image(MockField("avatar_image", value="/path.png"), "", {}),
        ),
        (
            "tags",
            lambda: render_tags(
                MockField("avatar_tags", value='["tag1"]', choices=[("tag1", "Tag 1")]),
                "",
                {},
            ),
        ),
        (
            "daterange",
            lambda: render_daterange(
                MockField(
                    "avatar_daterange",
                    value='{"start":"2023-01-01","end":"2023-01-02"}',
                ),
                "",
                {},
            ),
        ),
        ("otp", lambda: render_otp(MockField("avatar_otp", value="123456"), "", {})),
        (
            "rating",
            lambda: render_rating(MockField("avatar_rating", value="4"), "", {}),
        ),
        (
            "timerange",
            lambda: render_timerange(
                MockField("avatar_timerange", value='{"start":"12:00","end":"13:00"}'),
                "",
                {},
            ),
        ),
        ("files", lambda: render_files(MockField("avatar_files"), "", {})),
        (
            "autocomplete",
            lambda: render_autocomplete(
                MockField("avatar_autocomplete", value="opt", choices=["opt1", "opt2"]),
                "",
                {},
            ),
        ),
        (
            "cascading",
            lambda: render_cascading(
                MockField("avatar_cascading", choices={"US": ["NY", "CA"]}), "", {}
            ),
        ),
        ("phone", lambda: render_phone(MockField("avatar_phone"), "", {})),
        (
            "wysiwyg",
            lambda: render_wysiwyg(
                MockField("avatar_wysiwyg", value="<p>Hello</p>"), "", {}
            ),
        ),
        ("dropzone", lambda: render_dropzone(MockField("avatar_dropzone"), "", {})),
        ("signature", lambda: render_signature(MockField("avatar_signature"), "", {})),
        (
            "transfer",
            lambda: render_transfer(
                MockField("avatar_transfer", choices=[("1", "Opt 1")]), "", {}
            ),
        ),
        (
            "treeselect",
            lambda: render_treeselect(
                MockField("avatar_treeselect", choices=[{"id": 1, "name": "Node 1"}]),
                "",
                {},
            ),
        ),
    ]

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.input": None,
    }

    for field_name, render_fn in fields_to_test:
        html_content = render_fn()
        # Verify that rendering produces non-empty output
        assert html_content, f"Renderer for {field_name} produced empty string"

        # Verify that Asok precompiles and validates the output HTML directives without throwing ValueError
        try:
            # We wrap the output in body tags since smart streamer checks body
            full_html = f"<html><head></head><body>{html_content}</body></html>"
            req = Request(environ)
            result = app._inject_assets(full_html, req, "testnonce123")
            assert result, f"Asset injection returned empty string for {field_name}"
            # Ensure the registry mapping is generated
            assert "window.__asok_registry = Object.assign" in result, (
                f"Registry not found in output for {field_name}"
            )
        except ValueError as e:
            pytest.fail(
                f"Form renderer {field_name} failed security check: {e}\nGenerated HTML:\n{html_content}"
            )
