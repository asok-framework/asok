"""
Tests for the component module.
Covers: Component initialization, state mounting, rendering,
and the @exposed decorator for public methods.
"""

import pytest

from asok.component import COMPONENTS_REGISTRY, Component, exposed

# ---------------------------------------------------------------------------
# Test Component
# ---------------------------------------------------------------------------


class CounterComponent(Component):
    def mount(self, start=0):
        self.count = start
        self.internal_var = "hidden"

    @exposed
    def increment(self):
        self.count += 1

    @exposed
    def decrement(self):
        self.count -= 1

    def _private_method(self):
        pass

    def render(self):
        return f'<div id="counter">{self.count}</div>'


class InvalidComponent(Component):
    # Missing render() implementation
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComponent:
    def test_component_registration(self):
        # The metaclass should automatically register the component
        assert "CounterComponent" in COMPONENTS_REGISTRY
        assert COMPONENTS_REGISTRY["CounterComponent"] is CounterComponent

    def test_initialization_sets_kwargs(self):
        comp = CounterComponent(start=5)
        assert comp.start == 5

    def test_mount_lifecycle(self):
        comp = CounterComponent()
        comp.mount(start=10)
        assert comp.count == 10

    def test_state_extraction(self):
        comp = CounterComponent()
        comp.mount(start=10)
        state = comp._get_state()

        # Public state should include normal attributes but exclude methods/privates
        assert "count" in state
        assert state["count"] == 10
        assert "internal_var" in state
        assert "_private_method" not in state
        assert "increment" not in state

    def test_exposed_methods_tracking(self):
        comp = CounterComponent()
        methods = [
            k
            for k, v in comp.__class__.__dict__.items()
            if getattr(v, "_asok_exposed", False)
        ]

        assert "increment" in methods
        assert "decrement" in methods
        assert "_private_method" not in methods

    def test_html_rendering(self):
        comp = CounterComponent()
        comp.mount(start=42)
        html = str(comp)

        # HTML should include the rendered template and the Asok wrapper
        assert '<div id="counter">42</div>' in html
        assert 'data-asok-component="CounterComponent"' in html
        assert "data-asok-state=" in html

    def test_missing_render_raises_error(self):
        comp = InvalidComponent()
        with pytest.raises(NotImplementedError):
            str(comp)
