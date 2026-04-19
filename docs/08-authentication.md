# Authentication

Asok has built-in session-based authentication. No external package needed.

## Setup

### 1. Create a User model

```python
# src/models/user.py
from asok import Model, Field

class User(Model):
    name     = Field.String()
    email    = Field.String(unique=True)
    password = Field.Password()
```

`Field.Password()` automatically hashes passwords on save.

### 2. Register page

```python
# src/pages/register/page.py
from asok import Request, Form
from models.user import User

def render(request: Request):
    form = Form({
        'name':     Form.text('Name', 'required|min:2'),
        'email':    Form.email('Email', 'required|email|unique:User,email'),
        'password': Form.password('Password', 'required|min:8|confirmed'),
        'password_confirmation': Form.password('Confirm Password', 'required'),
    }, request)

    if form.validate():
        User.create(
            name=request.form['name'],
            email=request.form['email'],
            password=request.form['password'],
        )
        request.flash('success', 'Account created!')
        request.redirect('/login')

    return request.html('page.html', form=form)
```

### 3. Login page

```python
# src/pages/login/page.py
from asok import Request, Form

def render(request: Request):
    form = Form({
        'email':    Form.email('Email', 'required|email'),
        'password': Form.password('Password', 'required'),
    }, request)

    if form.validate():
        user = request.authenticate(email=request.form['email'], password=request.form['password'])
        if user:
            request.flash('success', 'Welcome back!')
            request.redirect('/dashboard')
        else:
            request.flash('error', 'Invalid credentials.')

    return request.html('page.html', form=form)
```

### 4. Logout

```python
# src/pages/logout/page.py
from asok import Request

def render(request: Request):
    request.logout()
    request.redirect('/')
```

## Protecting pages

```python
def render(request: Request):
    request.require_auth()  # Redirects to /login if not logged in
    # or
    request.require_auth('/custom-login')  # Custom redirect URL

    return request.html('page.html')
```

## Current user

```python
request.user              # User object or None
request.is_authenticated  # True/False
request.user.name         # Access user fields
```

In templates:

```html
{% if request.is_authenticated %}
    <span>Hello, {{ request.user.name }}!</span>
    <a href="/logout">Logout</a>
{% else %}
    <a href="/login">Login</a>
{% endif %}
```

## How it works

1. `request.authenticate()` finds the user by credentials and checks the password
2. `request.login(user)` creates a signed session cookie (`asok_session`)
3. On each request, Asok reads the cookie, verifies the signature, and loads the user
4. `request.logout()` clears the session cookie

Sessions are signed with HMAC-SHA256 using your `SECRET_KEY`. The session cookie is HttpOnly, SameSite=Lax, and Secure in production.

## Configuration

In `.env`:

```env
SECRET_KEY=your-secret-key-here
```

In `wsgi.py` (optional):

```python
app = Asok()
app.config['AUTH_MODEL'] = 'User'           # Default
app.config['SESSION_MAX_AGE'] = 86400 * 30  # 30 days (default)
```

---
[← Previous: Validation](07-validation.md) | [Documentation](README.md) | [Next: Middleware →](09-middleware.md)
