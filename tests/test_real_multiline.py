"""Test avec de vrais retours à la ligne dans les chaînes."""

from asok.forms import Form
from asok.templates import render_template_string

# Test avec retour à la ligne DANS la chaîne
template = """
{{ form.name.input(class_="w-full bg-white border border-gray-300
                            text-gray-900 text-sm rounded-lg focus:ring-blue-500") }}
"""

form = Form({"name": Form.text("Name", "required")})

print("Test: Retour à la ligne DANS la chaîne class_=...")
print("Template:")
print(template)
print("\nRendu:")

try:
    result = render_template_string(template, {"form": form})
    print(result)
    print("\n✓ Ça marche !")
except Exception as e:
    print(f"\n✗ Erreur: {e}")
    import traceback
    traceback.print_exc()
