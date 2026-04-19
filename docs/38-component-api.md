# Component API Reference

Detailed reference for the `asok.Component` class and its lifecycle methods.

## Scaffolding

Generate a new component using the CLI:

```bash
asok make component MyComponent
```

## Lifecycle Methods

### `mount(self) -> None`
Called automatically when a component is initialized.
*   **Context**: Available on both initial HTTP render and subsequent WebSocket interactions.

### `render(self) -> str`
**Required**. Must return the HTML string for the component.
*   Usually returns `self.html("template.html")`.
*   All public properties (not starting with `_`) are automatically passed to the template context.

### `html(self, template_name: str) -> str`
Helper to load a template from the same directory as the component class file.
*   Handles path resolution automatically for both package-based and standalone components.

## Properties

### `self.session`
Access to the user's persistent session store.
*   Behaves like a dictionary.
*   **Important**: Set `self.session.modified = True` to persist changes back to the server in WebSocket methods.

### `self._cid`
The unique Component ID. Automatically generated if not provided.

## Frontend Triggers

Asok scans components for specific attributes to wire up event listeners:

| Attribute | Behavior |
|---|---|
| `ws-click` | Call the named method on click. |
| `ws-input` | Call the named method when an input value changes. |
| `ws-submit` | Call the named method when a form is submitted. |

### Parameters
Method calls can include arguments:
```html
<button ws-click="delete_item(5)">Delete</button>
```

## Exposing Methods

Methods must be explicitly decorated with `@exposed` to be callable from the frontend:

```python
from asok import Component
from asok.component import exposed

class MyComponent(Component):
    @exposed
    def my_method(self):
        # This method can be called via ws-click, ws-input, etc.
        pass

    def _private_method(self):
        # This method is internal and cannot be called from frontend
        pass
```

## Security

*   **State Signing**: Component state is serialized and signed with the `SECRET_KEY`. Any alteration by the client results in a rejection of the update.
*   **Method Protection**: Only methods decorated with `@exposed` can be triggered from the frontend via WebSocket directives.
*   **Origin Validation**: The WebSocket server validates the `Origin` header to prevent CSWH attacks.

---
[← Previous: Reactive Components](37-reactive-components.md) | [Documentation](README.md) | [Next: SEO Management →](39-seo-management.md)
