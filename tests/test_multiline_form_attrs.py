"""Test multiline attributes in form rendering."""

from asok.forms import Form
from asok.templates import render_template_string


def test_multiline_class_attribute():
    """Test that form inputs can have multiline class attributes."""

    form = Form(
        {
            "name": Form.text("Name", "required"),
            "email": Form.email("Email", "required|email"),
        }
    )

    # Test 1: Multiline with newlines
    template1 = """
    {{ form.name.input(
        class_="bg-white px-4 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
    ) }}
    """

    result = render_template_string(template1, {"form": form})
    assert "bg-white" in result
    assert "focus:ring-indigo-500" in result

    # Test 2: Very long class on single line
    template2 = """
    {{ form.email.input(class_="w-full bg-white border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white dark:focus:ring-blue-500 dark:focus:border-blue-500") }}
    """

    result = render_template_string(template2, {"form": form})
    assert "focus:ring-blue-500" in result

    # Test 3: Multiple attributes multiline
    template3 = """
    {{ form.name.input(
        class_="bg-white px-4 py-2",
        placeholder="Enter your name",
        data_test="name-input"
    ) }}
    """

    result = render_template_string(template3, {"form": form})
    assert "bg-white" in result
    assert "placeholder" in result
    assert (
        "data_test" in result
    )  # data_test stays as-is (only trailing _ are transformed)


if __name__ == "__main__":
    test_multiline_class_attribute()
