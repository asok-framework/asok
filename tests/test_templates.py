"""
Tests for the template engine.
Uses render_template_string (the actual public API).
"""

import json

from asok.templates import (
    SafeString,
    html_safe_json,
    render_template_string,
)


def render(src, **ctx):
    """Helper: render a template string with the given context."""
    return render_template_string(src, ctx)


# ---------------------------------------------------------------------------
# Variable rendering
# ---------------------------------------------------------------------------


class TestVariableRendering:
    def test_simple_variable(self):
        assert render("Hello {{ name }}!", name="Asok") == "Hello Asok!"

    def test_integer_variable(self):
        assert render("Count: {{ n }}", n=42) == "Count: 42"

    def test_missing_variable_renders_empty(self):
        result = render("Hello {{ name }}!")
        assert result == "Hello !" or "Hello" in result  # graceful degradation

    def test_dict_access(self):
        result = render("{{ data.key }}", data={"key": "value"})
        assert "value" in result


# ---------------------------------------------------------------------------
# Auto-escaping (XSS prevention)
# ---------------------------------------------------------------------------


class TestAutoEscaping:
    def test_script_tag_is_escaped(self):
        result = render("{{ value }}", value="<script>alert(1)</script>")
        assert "<script>" not in result

    def test_html_special_chars_escaped(self):
        result = render("{{ value }}", value='<img src=x onerror="xss">')
        assert "<img" not in result

    def test_safe_filter_bypasses_escaping(self):
        result = render("{{ value | safe }}", value="<b>bold</b>")
        assert "<b>bold</b>" in result


# ---------------------------------------------------------------------------
# html_safe_json (XSS-safe JSON serialization)
# ---------------------------------------------------------------------------


class TestHtmlSafeJson:
    def test_escapes_less_than(self):
        result = html_safe_json({"x": "<div>"})
        assert "<div>" not in result
        assert "\\u003c" in result

    def test_escapes_greater_than(self):
        result = html_safe_json({"x": ">div<"})
        assert ">" not in result or "\\u003e" in result

    def test_escapes_ampersand(self):
        result = html_safe_json({"x": "a & b"})
        assert "\\u0026" in result

    def test_escapes_script_injection(self):
        result = html_safe_json({"x": "</script><script>alert(1)</script>"})
        assert "</script>" not in result

    def test_valid_json_structure(self):
        result = html_safe_json({"key": "value", "num": 42})
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42


# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_upper_filter(self):
        result = render("{{ 'hello' | upper }}")
        assert result == "HELLO"

    def test_lower_filter(self):
        result = render("{{ 'HELLO' | lower }}")
        assert result == "hello"

    def test_length_filter(self):
        result = render("{{ items | length }}", items=[1, 2, 3])
        assert result == "3"

    def test_tojson_filter_produces_valid_json(self):
        result = render("{{ data | tojson }}", data={"key": "val"})
        parsed = json.loads(result)
        assert parsed == {"key": "val"}

    def test_tojson_filter_is_xss_safe(self):
        result = render("{{ data | tojson }}", data={"x": "</script>"})
        assert "</script>" not in result


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------


class TestControlFlow:
    def test_if_true(self):
        assert render("{% if show %}yes{% endif %}", show=True) == "yes"

    def test_if_false(self):
        assert render("{% if show %}yes{% endif %}", show=False) == ""

    def test_if_else(self):
        assert render("{% if x %}yes{% else %}no{% endif %}", x=False) == "no"

    def test_for_loop(self):
        result = render("{% for i in items %}{{ i }},{% endfor %}", items=[1, 2, 3])
        assert result == "1,2,3,"

    def test_nested_if_in_for(self):
        tmpl = "{% for i in items %}{% if i > 1 %}{{ i }}{% endif %}{% endfor %}"
        result = render(tmpl, items=[1, 2, 3])
        assert result == "23"


# ---------------------------------------------------------------------------
# SafeString
# ---------------------------------------------------------------------------


class TestSafeString:
    def test_safe_string_is_str(self):
        s = SafeString("<b>bold</b>")
        assert isinstance(s, str)

    def test_safe_string_not_double_escaped(self):
        s = SafeString("<b>bold</b>")
        result = render("{{ content }}", content=s)
        assert result == "<b>bold</b>"


# ---------------------------------------------------------------------------
# More Filters (replace, join, truncate, default, abs, first, last, escape)
# ---------------------------------------------------------------------------


class TestMoreFilters:
    def test_replace_filter(self):
        assert render("{{ 'hello world' | replace('world', 'Asok') }}") == "hello Asok"

    def test_join_filter(self):
        assert render("{{ items | join(', ') }}", items=["a", "b", "c"]) == "a, b, c"

    def test_truncate_filter(self):
        long_text = "This is a very long text that needs to be truncated"
        assert render("{{ text | truncate(10) }}", text=long_text) == "This is a ..."
        assert render("{{ text | truncate(100) }}", text=long_text) == long_text

    def test_default_filter(self):
        assert render("{{ missing | default('N/A') }}") == "N/A"
        assert render("{{ value | default('N/A') }}", value="Present") == "Present"

    def test_abs_filter(self):
        assert render("{{ n | abs }}", n=-42) == "42"

    def test_first_last_filters(self):
        assert render("{{ items | first }}", items=[1, 2, 3]) == "1"
        assert render("{{ items | last }}", items=[1, 2, 3]) == "3"

    def test_escape_filter(self):
        assert render("{{ '<b>' | e | safe }}") == "&lt;b&gt;"


# ---------------------------------------------------------------------------
# Inheritance and Includes
# ---------------------------------------------------------------------------


class TestTemplateInheritance:
    def test_include(self, tmp_path):
        header = tmp_path / "header.html"
        header.write_text("<h1>Header</h1>")

        tpl = "<body>{% include 'header.html' %}<p>Content</p></body>"
        result = render_template_string(tpl, {}, root_dir=str(tmp_path))
        assert "<h1>Header</h1>" in result
        assert "<p>Content</p>" in result

    def test_extends_and_blocks(self, tmp_path):
        base = tmp_path / "base.html"
        base.write_text(
            "<html><body>{% block content %}default{% endblock %}</body></html>"
        )

        tpl = "{% extends 'base.html' %}{% block content %}override{% endblock %}"
        result = render_template_string(tpl, {}, root_dir=str(tmp_path))

        assert "<html><body>override</body></html>" in result

    def test_macros(self, tmp_path):
        macros = tmp_path / "macros.html"
        macros.write_text(
            "{% macro input(name, type='text') %}<input name='{{name}}' type='{{type}}'>{% endmacro %}"
        )

        tpl = "{% from 'macros.html' import input %}{{ input('username') }}"
        result = render_template_string(tpl, {}, root_dir=str(tmp_path))

        assert "<input name='username' type='text'>" in result
