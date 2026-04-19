# Native Form Actions

Native Form Actions allow you to handle form submissions without writing complex routing logic. Instead of manually checking `if request.method == "POST"` in your `render` function, you can define specific action functions that Asok will call automatically.

## The `action_[name]` Convention

If a page module contains a function named `action_XYZ`, Asok will automatically call it when a `POST` request is received with an action identifier `XYZ`.

The action identifier can be provided in two ways:
1.  **Hidden field**: `<input type="hidden" name="_action" value="XYZ">` (Recommended)
2.  **URL Parameter**: `?action=XYZ`

## Why use Form Actions?

- **Reduced Boilerplate**: Separate your rendering logic from your data mutation logic.
- **Type Safety**: Your action functions receive the same `Request` object.
- **Clear Intent**: It's obvious what part of the code handles which action.

## Example: Create a Post

`src/pages/new_post.py`:
```python
from asok import Request

# This function only handles the GET / display
def render(request: Request):
    return request.html("new_post.html")

# This function only handles the data submission
def action_create(request: Request):
    title = request.form.get("title")
    content = request.form.get("content")
    
    if not title:
        return request.html("new_post.html", error="Title is required")
    
    # Save to database...
    # DB.posts.create(title=title, content=content)
    
    return request.redirect("/")
```

`src/pages/new_post.html`:
```html
<h1>New Post</h1>

{% if error %}
    <p style="color: red">{{ error }}</p>
{% endif %}

<form method="POST">
    <input type="hidden" name="_action" value="create">
    
    <div>
        <label>Title</label>
        <input type="text" name="title">
    </div>
    
    <div>
        <label>Content</label>
        <textarea name="content"></textarea>
    </div>
    
    <button type="submit">Publish</button>
</form>
```

## Advanced: Multiple Actions in one Page

You can have multiple actions in the same file to handle different buttons or forms.

```python
def action_save_draft(request: Request):
    # logic...
    return "Draft saved"

def action_publish(request: Request):
    # logic...
    return request.redirect("/finished")
```

In your HTML:
```html
<form method="POST">
    <!-- Action can be set dynamically by buttons -->
    <button type="submit" name="_action" value="save_draft">Save Draft</button>
    <button type="submit" name="_action" value="publish">Publish Now</button>
</form>
```

> [!NOTE]
> Form Actions only trigger for `POST` requests for security reasons (mutation should never happen on GET).

---
[← Previous: Scoped Assets](40-scoped-assets.md) | [Documentation](README.md) | [Next: Prefetching →](42-prefetching.md)
