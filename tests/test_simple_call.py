"""Simple test to debug call macro."""

from asok.templates import _compile_and_run, _preprocess

template = """
{% macro card(title) %}
<div>{{ title }}</div>
<div>{{ caller() }}</div>
{% endmacro %}

{% call card("Test") %}
Content here
{% endcall %}
"""

# Preprocess
context = {}
processed = _preprocess(template, context)
print("Preprocessed:")
print(processed)
print("\nContext after preprocessing:")
print(f"Keys: {list(context.keys())}")
print("\n" + "="*60 + "\n")

# Try to compile
try:
    result = list(_compile_and_run(processed, context))
    print("Result:")
    print(''.join(result))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
