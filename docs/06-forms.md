# Forms

Asok provides a declarative form system that generates HTML and handles validation automatically.

## Basic usage

```python
# src/pages/contact/page.py
from asok import Request, Form

def render(request: Request):
    form = Form({
        'name':    Form.text('Name', 'required|min:2', placeholder='Your name'),
        'email':   Form.email('Email', 'required|email', placeholder='you@example.com'),
        'message': Form.textarea('Message', 'required|min:10'),
    }, request)

    if form.validate():
        # All fields are valid — process the data
        request.flash('success', 'Sent!')
        request.redirect('/contact')

    return request.html('page.html', form=form)
```

## Form constructor

```python
Form(fields_dict: dict, request: Request = None)
```

`fields_dict` is **required**. `request` is optional — if omitted, the form acts as a template that you bind later. There are four equivalent ways to wire a form to a request:

```python
# 1. Bound at construction (most common in pages)
form = Form({'name': Form.text('Name', 'required')}, request)

# 2. Bind later with .bind(request)
form = Form({'name': Form.text('Name', 'required')})
form.bind(request)
if form.validate(): ...

# 3. Pass the request directly to .validate()
form = Form({'name': Form.text('Name', 'required')})
if form.validate(request): ...

# 4. Share globally and let asok auto-bind per request
# (see "Reusable forms" below)
app.share(my_form=Form({'name': Form.text('Name', 'required')}))
```

Calling `form.validate()` without ever binding a request raises `RuntimeError`.

```html
<!-- src/pages/contact/page.html -->
<form method="POST">
    {{ request.csrf_input() }}

    {{ form.name }}
    {{ form.email }}
    {{ form.message }}

    <button type="submit">Send</button>
</form>
```

`{{ form.name }}` generates the full block: label + input + error message.

## Generated HTML

```html
<!-- Without error -->
<div class="form-group">
  <label for="name">Name</label>
  <input type="text" id="name" name="name" value="" placeholder="Your name">
</div>

<!-- With error -->
<div class="form-group">
  <label for="name">Name</label>
  <input type="text" id="name" name="name" value="J" class="input-error" placeholder="Your name">
  <div class="form-error">Minimum 2 characters.</div>
</div>
```

## Render parts separately

For more control over the HTML:

```html
{{ form.name.label }}   → <label for="name">Name</label>
{{ form.name.input }}   → <input type="text" ...>
{{ form.name.error }}   → <div class="form-error">...</div> (or empty)
```

## Custom classes (Tailwind, etc.)

Pass attributes when rendering to override defaults:

```html
<!-- Full field with custom input class -->
{{ form.name(class_="mb-6") }}

<!-- Individual parts -->
{{ form.name.label(class_="text-sm font-bold text-gray-700") }}
{{ form.name.input(class_="border rounded-lg px-4 py-2 w-full") }}
{{ form.name.error(class_="text-red-500 text-xs mt-1") }}
```

Since `class` is a Python reserved word, use `class_` — the trailing underscore is stripped automatically. This works for any attribute: `for_` becomes `for`, etc.

## Field types

```python
Form.text('Label', 'rules', placeholder='...')
Form.email('Label', 'rules')
Form.password('Label', 'rules')
Form.textarea('Label', 'rules')
Form.number('Label', 'rules')
Form.file('Label', 'rules')
Form.hidden('rules')
Form.checkbox('Label', 'rules')
Form.select('Label', [('val', 'Display'), ...], 'rules')
Form.radio('Label', [('val', 'Display'), ...], 'rules')
Form.title('Section Name')   # Renders <h3>, no input
```

### Select example

```python
form = Form({
    'country': Form.select('Country', [
        ('fr', 'France'),
        ('us', 'United States'),
        ('uk', 'United Kingdom'),
    ], 'required'),
}, request)
```

### Radio example

```python
form = Form({
    'plan': Form.radio('Plan', [
        ('free', 'Free'),
        ('pro', 'Pro — $9/mo'),
    ], 'required'),
}, request)
```

### Checkbox example

```python
form = Form({
    'agree': Form.checkbox('I agree to the terms', 'required'),
}, request)
```

### Title (section divider)

```python
form = Form({
    'section1': Form.title('Personal Information'),
    'name':     Form.text('Name', 'required'),
    'section2': Form.title('Account'),
    'email':    Form.email('Email', 'required|email'),
}, request)
```

## Generate a form from a Model

For CRUD pages, you usually don't want to repeat the field schema that's already in your Model. `Form.from_model()` builds the form schema by inspecting the Model's `Field` definitions:

```python
from asok import Form
from models.contact import Contact

def render(request):
    form = Form.from_model(Contact, request)
    if form.validate():
        Contact.create(**form.data)
        request.flash('success', 'Saved!')
        request.redirect('/contacts')
    return request.html('page.html', form=form)
```

For an edit page:

```python
def render(request: Request):
    contact = Contact.find(id=request.params.get('id'))
    form = Form.from_model(Contact, request).fill(contact)
    if form.validate():
        contact.update(**form.data)
        request.flash('success', 'Updated!')
        request.redirect(f'/contacts/{contact.id}')
    return request.html('page.html', form=form)
```

### Signature

```python
Form.from_model(
    model,
    request: Request = None,
    include_fields: list = None,
    exclude_fields: list = None,
)
```

- `include_fields=['name', 'email']` — only generate these fields
- `exclude_fields=['internal_notes']` — generate all fields except these

### Auto-mapping rules

| Model field | Form field |
|---|---|
| `Field.String(max_length=N)` | `text` input with `maxlength=N` and `max:N` rule |
| `Field.Text()` | `textarea` |
| `Field.Email()` | `email` input with `email` rule |
| `Field.Integer()` | `number` input |
| `Field.Float(precision=N)` | `number` input with `step="0.01"` (per precision) |
| `Field.Boolean()` | `checkbox` |
| `Field.Date()` | `date` input (when name matches conventions) |
| `Field.ForeignKey(Other)` | `select` with all rows from the related model |
| `Field.File()` | `file` input |
| `Field.Password()` | `password` input (rules cleared so edits don't force re-entry) |

Validation rules are derived automatically: `nullable=False` adds `required`, `Email` adds `email`, `max_length` adds `max:N`.

### Auto-excluded fields

These are skipped automatically (you don't need to put them in `exclude_fields`):

- `id`
- `Field.CreatedAt()` / `Field.UpdatedAt()` (timestamps)
- `Field.SoftDelete()`
- `Field.Slug(populate_from=...)` when auto-populated

### Customizing further

`Form.from_model()` returns a regular `Form`, so you can mutate the schema after creation if needed:

```python
form = Form.from_model(Contact, request, exclude_fields=['internal_notes'])
form._fields['email'].attrs['autofocus'] = True
```

## Translating labels (i18n)

Use `request.__()` to translate form labels:

```python
def render(request: Request):
    __ = request.__

    form = Form({
        'name':    Form.text(__('form_name'), 'required|min:2'),
        'email':   Form.email(__('form_email'), 'required|email'),
        'message': Form.textarea(__('form_message'), 'required|min:10'),
    }, request)
```

```json
// src/locales/en.json
{ "form_name": "Your name", "form_email": "Your email address", "form_message": "Your message" }

// src/locales/fr.json
{ "form_name": "Votre nom", "form_email": "Votre adresse mail", "form_message": "Votre message" }
```

Validation error messages are also translated automatically — see [Validation > i18n](07-validation.md#i18n).

## Custom error messages

```python
Form.text('Name', 'required|min:2', messages={
    'required': 'Please enter your name.',
    'min': 'Name is too short.',
})
```

## Pre-filling for edit forms

Use `form.fill(obj_or_dict)` to populate fields from an existing record. It only applies on non-POST requests, so submitted values are preserved when re-rendering after a failed validation.

```python
def render(request):
    user = User.find(id=request.params['id'])

    form = Form({
        'name':  Form.text('Name', 'required|min:2'),
        'email': Form.email('Email', 'required|email'),
    }, request).fill(user)

    if form.validate():
        user.update(**form.data)
        request.flash('success', 'Updated!')
        request.redirect(f'/users/{user.id}')

    return request.html('page.html', form=form)
```

`fill()` accepts a model instance or a dict, and returns `self` for chaining.

## Accessing values after POST

The cleanest way is `form.data` — a dict of all field values, ready to pass to a model:

```python
if form.validate():
    User.create(**form.data)
```

Field-by-field access works too:

```python
if form.validate():
    email = form.email.value     # via the form object
    name = request.form['name']  # via the raw POST dict
```

## Checking errors

```python
form.errors  # {'name': 'Minimum 2 characters.', 'email': 'Invalid email address.'}
```

## Reset form

`form.reset()` clears all field values and errors. Returns `self` for chaining.

```python
if form.validate():
    request.flash('success', 'Sent!')
    form.reset()
```

After `reset()`, the rendered form is empty, as if the page was loaded for the first time.

## Reusable forms (newsletter, search, etc.)

To embed the same form on multiple pages — e.g. a newsletter form in the footer of every page — declare it once with `app.share()` and add a dedicated page to handle the POST.

### 1. Declare the form globally

```python
# wsgi.py
from asok import App, Form

app = App()

app.share(
    newsletter_form=Form({
        'email': Form.email('Email', 'required|email', placeholder='you@example.com'),
    }),
)
```

`Form({...})` (without a request) creates a **template** — Asok auto-binds a fresh instance per request. Now `newsletter_form` is available in every template.

### 2. Dedicated page that handles submission

```python
# src/pages/newsletter/page.py
from asok import Request
from models.subscriber import Subscriber

def render(request: Request):
    # Using shared_form instead of shared for full IDE autocompletion
    form = request.shared_form('newsletter_form')

    if form.validate():
        Subscriber.first_or_create(**form.data)
        request.flash('success', 'Subscribed!')
        form.reset()
    return request.html('page.html')
```

```html
<!-- src/pages/newsletter/page.html -->
{% extends "html/base.html" %}
{% block main %}
  {% include "html/newsletter_form.html" %}
{% endblock %}
```

### 3. The reusable partial

```html
<!-- src/partials/html/newsletter_form.html -->
<form method="POST" action="/newsletter" data-block="#newsletter-block">
  {{ request.csrf_input() }}
  <div id="newsletter-block">
    {% for msg in get_flashed_messages() %}
      <div class="flash {{ msg.category }}">{{ msg.message }}</div>
    {% endfor %}
    {{ newsletter_form.email }}
    <button type="submit">Subscribe</button>
  </div>
</form>
```

### 4. Drop it anywhere

```html
<!-- src/partials/html/footer.html -->
<footer>
  <h3>Stay updated</h3>
  {% include "html/newsletter_form.html" %}
</footer>
```

### How it works

- `app.share()` registers variables shared across all templates.
- `Form({...})` is a **template** — `request.shared()` and `request.html()` auto-bind a fresh instance per request, so two users never share state.
- The `<form>` posts to `/newsletter` regardless of the current page.
- `data-block` swaps only the inner `<div id="newsletter-block">`, not the whole page.
- Validation errors stay inline because the dedicated handler returns the rendered block with its error state.
- On success, `form.reset()` clears the inputs and the flash message appears.

### `app.share()` — what you can pass

```python
import datetime
from asok import App, Form

app = App()

app.share(
    site_name='My Site',                              # static
    current_year=lambda r: datetime.date.today().year,  # callable, called per request
    newsletter_form=Form({                             # form template, auto-bound
        'email': Form.email('Email', 'required|email'),
    }),
    current_user=lambda r: r.user,                     # access the logged-in user
)
```

| Value type | Behavior |
|---|---|
| Static | Passed through as-is |
| `Form({...})` | Bound to the current request via `_bind()` |
| Callable | Called with `request`, result is used |

## IDE Autocompletion for shared variables

When using `request.shared(name)`, your IDE usually doesn't know the type of the returned object. Asok provides two ways to get full IntelliSense:

### 1. Specialized helper (Preferred for Forms)

Use `request.shared_form(name)` instead of the generic `shared()`. It is explicitly typed to return a `Form` instance.

```python
form = request.shared_form('contact_form')
# Now, form.validate(), form.reset(), etc. are suggested by your IDE
```

### 2. Manual type hint

For other types of objects, pass the expected class as the second argument to `request.shared()`:

```python
from models.user import User

user = request.shared('current_user', User)
# IDE now knows 'user' is an instance of User
```

Page-level `request.html(key=value)` kwargs always override shared values.

## Partial update (no full page reload)

Add `data-block` on the `<form>` to submit via `fetch` and swap only the block content in the DOM:

```html
<!-- page.html -->
{% extends "html/base.html" %}
{% block main %}

{% for msg in get_flashed_messages() %}
    <div class="flash {{ msg.category }}">{{ msg.message }}</div>
{% endfor %}

<form method="POST" data-block="main">
    {{ request.csrf_input() }}
    {{ form.name }}
    {{ form.email }}
    {{ form.message }}
    <button type="submit">Send</button>
</form>

{% endblock %}
```

```python
# page.py
def render(request: Request):
    form = Form({
        'name':    Form.text('Name', 'required|min:2'),
        'email':   Form.email('Email', 'required|email'),
        'message': Form.textarea('Message', 'required|min:10'),
    }, request)

    if form.validate():
        request.flash('success', 'Sent!')
        form.reset()

    return request.html('page.html', form=form)
```

How it works:
- `data-block="main"` tells the framework to submit the form via `fetch` and swap the response into `<main>`
- `request.html()` automatically detects the partial request and returns only the `{% block main %}` content
- The Python code handles both full-page GET and partial POST — no `if/else` needed
- Flash messages set in the same request are visible immediately (no redirect required)

---
[← Previous: SQLite ORM](05-orm.md) | [Documentation](README.md) | [Next: Validation →](07-validation.md)
