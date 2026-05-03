"""Debug call with print statements."""

from asok.templates import render_template_string

template = """
{% macro card(title) %}
<div>{{ title }}</div>
<div>{{ caller() }}</div>
{% endmacro %}

{% call card("Test") %}
Content here
{% endcall %}
"""

context = {}
try:
    result = render_template_string(template, context)
    print("Result:")
    print(result)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
