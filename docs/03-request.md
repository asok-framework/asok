# Request

Every `render()` function receives a `Request` object. It handles input, output, auth, and more.

## Reading input

```python
def render(request: Request):
    # URL: /search?q=hello
    request.args['q']              # "hello"
    or request.args.get('q')
    # POST form data
    request.form['email']          # "user@example.com"

    # JSON body (POST with Content-Type: application/json)
    request.json_body              # {"key": "value"}

    # Route params (/user/[id])
    request.params['id']           # "42"

    # HTTP method
    request.method                 # "GET" or "POST"

    # Current path
    request.path                   # "/contact"
```

## Metadata

Access information about the client and the connection.

```python
def render(request: Request):
    # Client IP address (handles X-Forwarded-For if behind a proxy)
    request.ip                     # "192.168.1.1"

    # Raw User-Agent string
    request.user_agent             # "Mozilla/5.0..."

    # Parsed browser info
    request.browser.name           # "Chrome", "Firefox", "Safari", etc.
    request.browser.os             # "macOS", "Windows", "Linux", "iOS", "Android"
    request.browser.is_mobile      # True or False

    # Geolocation (requires .asok/geo.csv)
    request.location['city']       # "Paris"
    request.location['country']    # "France"
    request.location['lat']        # 48.8566
    request.location['lon']        # 2.3522
```

## Rendering responses

### HTML template

```python
def render(request: Request):
    return request.html('page.html', name='World', items=[1, 2, 3])
```

The template receives all kwargs as variables.

### JSON response

```python
def render(request: Request):
    return request.json({'status': 'ok', 'count': 42})
```

### Block (partial rendering)

`request.block(filepath, block_name, **context)` renders only a specific `{% block %}` from a template. The `block_name` argument is **required**.

```python
def render(request: Request):
    return request.block('page.html', 'main', form=form)
    #                     ^^^^^^^^     ^^^^
    #                     template     block name (required)
```

This returns only the content inside `{% block main %}...{% endblock %}`, without the surrounding layout (`<html>`, `<head>`, `<nav>`, etc.).

#### Native block swap (no HTMX needed)

Add `data-block` on a `<form>` to enable automatic partial updates. The form will submit via `fetch` and swap only the target block in the DOM — no full page reload.

```html
<!-- The framework finds the target by matching [data-block="main"] or id="main" -->
<form method="POST" data-block="main">
    {{ request.csrf_input() }}
    {{ form.name }}
    <button type="submit">Send</button>
</form>
```

When `data-block="main"` is present:
1. **Client**: JS intercepts the submit, sends via `fetch` with a `X-Block: main` header
2. **Server**: `request.html()` detects the header and calls `request.block()` automatically
3. **Client**: the response replaces the `innerHTML` of `<main>`

This means **the Python code is the same** for both full-page GET and partial POST:

```python
def render(request: Request):
    form = Form({ ... }, request)

    if form.validate():
        request.flash('success', 'Sent!')
        form.reset()

    return request.html('page.html', form=form)
```

For a custom target selector, add `data-target`:

```html
<form method="POST" data-block="main" data-target="#my-container">
```

### Response status code

```python
# Set the status code (method chaining)
request.status_code(201)
return request.json({'created': True})

# Get the current status code as an integer
if request.status_code == 404:
    print("Not found!")
```

### File download

```python
def render(request: Request):
    return request.send_file('/path/to/report.pdf')

    # Or inline (display in browser)
    return request.send_file('/path/to/image.png', as_attachment=False)

    # Custom filename
    return request.send_file('/path/to/file.csv', filename='export.csv')
```

## Redirect

```python
def render(request: Request):
    request.redirect('/dashboard')
    # Raises RedirectException — stops execution, sends 302

### Redirect back

Useful for shared forms (newsletter, search) that can be submitted from any page.


```python
def post(request: Request):
    # Process something...
    request.back()  # Redirects to Referer or '/'
    
    # Custom default fallback if Referer is missing
    request.back(default='/home')
```

## Flash messages

```python
# Set a flash message
request.flash('success', 'Your message has been sent!')
request.redirect('/contact')
```

```html
<!-- Display flash messages in template -->
{% for msg in get_flashed_messages() %}
    <div class="flash {{ msg.category }}">{{ msg.message }}</div>
{% endfor %}
```

Categories are free-form strings: `success`, `error`, `info`, `warning`, etc.

## CSRF protection

CSRF is enabled by default on POST, PUT, DELETE requests. Add the hidden input in your forms:

```html
<form method="POST">
    {{ request.csrf_input() }}
    <!-- your fields -->
</form>
```

For AJAX requests, send the token as a header:

```javascript
fetch('/api/data', {
    method: 'POST',
    headers: { 'X-CSRF-Token': '{{ request.csrf_token_value }}' }
})
```

## Cookies

```python
request.cookies_dict              # All cookies as dict
request.cookies_dict.get('key')   # Read a cookie
```

## Environment variables

```python
request.env('SECRET_KEY')         # Read from os.environ
request.env('MISSING', 'default') # With default value
request.env('DEBUG')              # Auto-cast: "true" → True
```

## Internationalization

```python
# In Python
request.lang                      # Current language ("en", "fr", ...)
request.__('welcome')             # Translated string

# In templates
{{ __('welcome') }}
{{ request.lang }}
```

The language is detected from: query param `?lang=fr` > cookie > Accept-Language header > config default.

## Variable Sharing

Inject global variables that are available in every template without passing them manually to `request.html()`.

### Global values

```python
# wsgi.py
app.share(site_name="My Blog", version="1.0")

# In any template
<h1>{{ site_name }}</h1>
```

### Dynamic values

If you provide a callable, it is executed per request with the `request` object as its only argument.

```python
# wsgi.py
app.share(user=lambda r: r.user)

# In any template
{% if user %}
  Hello, {{ user.name }}
{% endif %}
```

### Accessing shared variables in Python

Use `request.shared(name)` to retrieve a shared variable's value for the current request context.

```python
def render(request):
    user = request.shared('user')  # Result of the lambda(request)
```

---
[← Previous: Routing](02-routing.md) | [Documentation](README.md) | [Next: Template Engine →](04-templates.md)
