"""Test des trois dernières fonctionnalités du moteur de template:
1. {% set x %}...{% endset %} - Block assignment
2. {% call macro() %} - Callable macro blocks
3. {% break %} / {% continue %} - Loop control
"""

from asok.templates import render_template_string


def test_set_block():
    """Test {% set x %}...{% endset %} block assignment."""

    template = """
    {% set greeting %}
        Hello <strong>{{ name }}</strong>!
        Welcome to {{ site }}.
    {% endset %}
    Greeting: {{ greeting }}
    """

    result = render_template_string(template, {"name": "Alice", "site": "Asok"})

    print("Test 1: Set block")
    print(result)

    assert "Hello" in result
    assert "Alice" in result
    assert "Asok" in result
    assert "Greeting:" in result
    print("✓ Set block works!\n")


def test_break_continue():
    """Test {% break %} and {% continue %} in loops."""

    # Test break
    template_break = """
    {% for i in numbers %}
        {% if i == 3 %}{% break %}{% endif %}
        {{ i }}
    {% endfor %}
    """

    result_break = render_template_string(template_break, {"numbers": [1, 2, 3, 4, 5]})
    print("Test 2a: Break")
    print(f"Result: {result_break.strip()}")

    # Should only have 1 and 2
    assert "1" in result_break
    assert "2" in result_break
    assert "4" not in result_break
    assert "5" not in result_break
    print("✓ Break works!\n")

    # Test continue
    template_continue = """
    {% for i in numbers %}
        {% if i == 3 %}{% continue %}{% endif %}
        {{ i }}
    {% endfor %}
    """

    result_continue = render_template_string(template_continue, {"numbers": [1, 2, 3, 4, 5]})
    print("Test 2b: Continue")
    print(f"Result: {result_continue.strip()}")

    # Should have 1, 2, 4, 5 but not 3
    assert "1" in result_continue
    assert "2" in result_continue
    assert "4" in result_continue
    assert "5" in result_continue
    # 3 might appear in whitespace, so check more carefully
    nums_found = [c for c in result_continue if c.isdigit()]
    assert '3' not in nums_found
    print("✓ Continue works!\n")


def test_call_macro():
    """Test {% call macro() %} with caller."""

    template = """
    {% macro card(title) %}
        <div class="card">
            <h3>{{ title }}</h3>
            <div class="content">
                {{ caller() }}
            </div>
        </div>
    {% endmacro %}

    {% call card("My Card") %}
        This is the card content!
        <p>With HTML and {{ dynamic }} content.</p>
    {% endcall %}
    """

    try:
        result = render_template_string(template, {"dynamic": "dynamic"})
    except Exception as e:
        print(f"Error: {e}")
        # Try to see what went wrong
        import traceback
        traceback.print_exc()
        raise

    print("Test 3: Call macro with caller")
    print(result)

    assert "My Card" in result
    assert "This is the card content!" in result
    assert "dynamic" in result
    assert '<div class="card">' in result
    print("✓ Call macro works!\n")


def test_complex_combinations():
    """Test combinations of new features."""

    template = """
    {% set items_html %}
        <ul>
        {% for item in items %}
            {% if item.skip %}{% continue %}{% endif %}
            {% if item.id > 3 %}{% break %}{% endif %}
            <li>{{ item.name }}</li>
        {% endfor %}
        </ul>
    {% endset %}

    Final HTML: {{ items_html }}
    """

    items = [
        {"id": 1, "name": "Item 1", "skip": False},
        {"id": 2, "name": "Item 2", "skip": True},  # Will be skipped
        {"id": 3, "name": "Item 3", "skip": False},
        {"id": 4, "name": "Item 4", "skip": False},  # Will cause break
        {"id": 5, "name": "Item 5", "skip": False},  # Never reached
    ]

    result = render_template_string(template, {"items": items})

    print("Test 4: Complex combination")
    print(result)

    assert "Item 1" in result
    assert "Item 2" not in result  # skipped
    assert "Item 3" in result
    assert "Item 4" not in result  # break before this
    assert "Item 5" not in result
    print("✓ Complex combination works!\n")


if __name__ == "__main__":
    print("=" * 60)
    print("Template Engine - Final Features Test")
    print("=" * 60)
    print()

    try:
        test_set_block()
        test_break_continue()
        test_call_macro()
        test_complex_combinations()

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
