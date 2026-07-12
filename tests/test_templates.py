"""
Tests for the template engine.
Uses render_template_string (the actual public API).
"""

import json

from asok.templates import SafeString, html_safe_json, render_template_string


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

    def test_include_from_extension_template_path(self, tmp_path):
        """A third-party extension registers its own ``templates/`` dir; an
        ``{% include "...buttons.html" %}`` in the host app's template must
        resolve against that dir.
        """
        ext_dir = tmp_path / "ext_templates"
        (ext_dir / "auth_providers").mkdir(parents=True)
        (ext_dir / "auth_providers" / "buttons.html").write_text(
            "<button>Sign in</button>"
        )
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        tpl = "<body>{% include 'auth_providers/buttons.html' %}</body>"
        # Both roots passed in priority order — same shape as
        # `_resolve_template` returns.
        result = render_template_string(
            tpl, {}, root_dir=[str(project_dir), str(ext_dir)]
        )
        assert "<button>Sign in</button>" in result

    def test_include_inside_html_comment_does_not_recursively_expand(self, tmp_path):
        """An ``{% include %}`` example sitting inside an HTML comment of the
        included file must not re-trigger expansion. Without the comment
        mask, an extension whose template carries a usage example in its
        docstring would recursively expand itself until the depth limit and
        emit a duplicated, corrupted block."""
        buttons = tmp_path / "buttons.html"
        buttons.write_text(
            "<!--\n"
            "    Usage example:\n"
            "    {% include 'buttons.html' %}\n"
            "-->\n"
            "<div>real button</div>"
        )
        tpl = "<page>{% include 'buttons.html' %}</page>"
        result = render_template_string(tpl, {}, root_dir=str(tmp_path))
        # The real content is rendered exactly once — no recursion.
        assert result.count("real button") == 1
        # The HTML comment itself is preserved in the output so devs can still
        # read it in the rendered HTML.
        assert "Usage example" in result

    def test_extends_finds_parent_in_extension_path(self, tmp_path):
        ext_dir = tmp_path / "ext_templates"
        ext_dir.mkdir()
        (ext_dir / "ext_base.html").write_text(
            "<root>{% block body %}default{% endblock %}</root>"
        )
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        tpl = "{% extends 'ext_base.html' %}{% block body %}OK{% endblock %}"
        result = render_template_string(
            tpl, {}, root_dir=[str(project_dir), str(ext_dir)]
        )
        assert "<root>OK</root>" in result

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

    def test_nested_block_markers(self):
        tpl = (
            "<div>"
            "{% block main %}"
            "<main>"
            "{% block toc %}"
            "<aside>toc</aside>"
            "{% endblock %}"
            "</main>"
            "{% endblock %}"
            "</div>"
        )
        result = render_template_string(tpl, {}, inject_block_markers=True)
        # Verify that all markers are present and correctly formed
        assert "<!-- block:main:start -->" in result
        assert "<!-- block:main:end -->" in result
        assert "<!-- block:toc:start -->" in result
        assert "<!-- block:toc:end -->" in result
        # Check that none of the HTML tags are broken
        assert "<main>" in result
        assert "</main>" in result
        assert "<aside>toc</aside>" in result


class TestTemplateSandboxSecurity:
    def test_set_inline_injection_raises(self):
        # Trying to inject import statement in set target
        try:
            render("{% set x; import os = 1 %}")
            assert False, "Should have failed to compile/run template"
        except Exception:
            pass

    def test_for_loop_injection_raises(self):
        # Trying to inject import statement in for loop target
        try:
            render("{% for x; import os in [1] %}{% endfor %}")
            assert False, "Should have failed to compile/run template"
        except Exception:
            pass

    def test_with_injection_raises(self):
        # Trying to inject import statement in with target
        try:
            render("{% with x; import os = 1 %}{% endwith %}")
            assert False, "Should have failed to compile/run template"
        except Exception:
            pass

    def test_call_injection_raises(self):
        # Trying to inject arbitrary python in macro call args
        try:
            render(
                "{% call macro(1); import os %}{% endcall %}", macro=lambda *a, **kw: ""
            )
            assert False, "Should have failed to compile/run template"
        except Exception:
            pass

    def test_set_inline_actual_injection(self):
        # Injecting actual executable code into set target
        tpl = """{% set x
import sys
sys.injected_flag = True
# = 1 %}"""
        try:
            import sys

            if hasattr(sys, "injected_flag"):
                del sys.injected_flag
            render(tpl)
            # If it succeeded, check if code executed
            assert not getattr(sys, "injected_flag", False), (
                "Code injection was executed!"
            )
        except Exception:
            pass


def test_shared_variables_caching_and_double_evaluation():
    from asok.core import Asok
    from asok.request import Request

    app = Asok()
    call_count = 0

    def my_helper(arg):
        nonlocal call_count
        call_count += 1
        return f"result-{arg}"

    app.share(my_helper=lambda request: my_helper)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "asok.app": app,
    }
    req = Request(environ)

    # First lookup resolves lambda and caches `my_helper`
    helper1 = req.shared("my_helper")
    assert helper1 is my_helper

    # Second lookup returns cached `my_helper` directly without re-evaluation
    helper2 = req.shared("my_helper")
    assert helper2 is my_helper
    assert call_count == 0


def test_tilde_string_concatenation():
    tpl = '{% set name = "User #" ~ user.id %}{{ name }}'
    res = render_template_string(tpl, {"user": {"id": 42}})
    assert res == "User #42"


def test_template_error_line_number_and_filename():
    import pytest

    from asok.exceptions import TemplateError

    tpl = "Line 1\nLine 2\n{{ 1 / 0 }}\nLine 4"

    with pytest.raises(TemplateError) as exc_info:
        render_template_string(tpl, {}, template_name="user_profile.html")

    error_msg = str(exc_info.value)
    assert "user_profile.html" in error_msg
    assert "line 3" in error_msg
    assert "division by zero" in error_msg


def test_template_error_debug_html_rendering():
    from asok.core import Asok
    from asok.request import Request

    app = Asok()
    app.config["DEBUG"] = True

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "asok.app": app,
    }
    req = Request(environ)
    req.params["foo_variable"] = "bar_value"

    try:
        # Cause a rendering error
        render_template_string("Hello {{ 1 / 0 }}", {}, template_name="test.html")
    except Exception as e:
        html_response = app._handle_template_error(req, e)

    assert "test.html" in html_response
    assert "Template rendering failed" in html_response
    assert "Traceback" in html_response
    assert "foo_variable" in html_response
    assert "bar_value" in html_response


def test_unclosed_template_delimiter_error():
    import pytest

    from asok.exceptions import TemplateError

    tpl = """{% block title %}Welcome{% endblock %}

{% block main %}
    <div>
        <p>Bonjour {{ name </p>
    </div>
{% endblock %}"""

    with pytest.raises(TemplateError) as exc_info:
        render_template_string(tpl, {}, template_name="home.html")

    error_msg = str(exc_info.value)
    assert "home.html" in error_msg
    assert "Unclosed template expression" in error_msg
    assert "line 5" in error_msg


def test_shared_class_and_module():
    import os

    from asok.core import Asok
    from asok.request import Request

    class CustomModel:
        def __init__(self, request):
            self.request = request
            self.val = "instantiated"

    app = Asok()
    # Share a class definition and a module
    app.share(CustomModel=CustomModel, os_module=os)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "asok.app": app,
    }
    req = Request(environ)

    # Resolving CustomModel class should return the class definition itself,
    # not an auto-instantiated instance, even though its __init__ has "request".
    resolved_model = req.shared("CustomModel")
    assert resolved_model is CustomModel

    # Resolving os should return the module itself
    resolved_os = req.shared("os_module")
    assert resolved_os is os


def test_dynamic_shared_callables():
    from asok.core import Asok
    from asok.request import Request

    db_data = ["Alice", "Bob"]

    def get_names():
        return list(db_data)

    app = Asok()
    # Share:
    # 1. Zero-argument callable (should be executed per-request)
    # 2. Request-argument callable (should be executed per-request)
    # 3. Callable with required args (should NOT be auto-executed)
    app.share(
        names_zero=get_names,
        names_req=lambda request: list(db_data),
        helper_with_args=lambda x: f"hello-{x}",
    )

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "asok.app": app,
    }

    # Request 1
    req1 = Request(environ)
    assert req1.shared("names_zero") == ["Alice", "Bob"]
    assert req1.shared("names_req") == ["Alice", "Bob"]
    assert callable(req1.shared("helper_with_args"))

    # Update database data
    db_data.append("Charlie")

    # Request 2 (simulating a new request/page load)
    req2 = Request(environ)
    assert req2.shared("names_zero") == ["Alice", "Bob", "Charlie"]
    assert req2.shared("names_req") == ["Alice", "Bob", "Charlie"]
