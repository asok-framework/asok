"""Test with real newlines in strings."""

from asok.forms import Form
from asok.templates import render_template_string

# Test with newline inside the string
template = """
{{ form.name.input(class_="w-full bg-white border border-gray-300
                            text-gray-900 text-sm rounded-lg focus:ring-blue-500") }}
"""

form = Form({"name": Form.text("Name", "required")})

print("Test: Newline INSIDE the class_=... string")
print("Template:")
print(template)
print("\nRendered:")

try:
    result = render_template_string(template, {"form": form})
    print(result)
    print("\n✓ It works!")
except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback

    traceback.print_exc()

