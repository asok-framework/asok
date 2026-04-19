# Validation

Asok has a built-in validator. It's used automatically by `Form`, but you can also use it standalone.

## With Form (recommended)

```python
form = Form({
    'email': Form.email('Email', 'required|email'),
}, request)

if form.validate():
    # Valid
```

See [Forms](06-forms.md) for full docs.

## Standalone usage

```python
from asok import Validator

v = Validator(request.form, request.files)

v.rules({
    'name':  'required|min:2',
    'email': 'required|email',
    'age':   'required|numeric',
})

if v.validate():
    # All good
else:
    errors = v.errors  # {'email': 'Invalid email address.'}
```

## Available rules

| Rule | Example | Description |
|---|---|---|
| `required` | `'required'` | Field must not be empty |
| `email` | `'email'` | Must be a valid email |
| `min` | `'min:3'` | Minimum character length |
| `max` | `'max:255'` | Maximum character length |
| `numeric` | `'numeric'` | Must be a number |
| `url` | `'url'` | Must be a valid URL (http/https) |
| `date` | `'date'` | Must be a valid date (YYYY-MM-DD) |
| `in` | `'in:admin,user,guest'` | Must be one of the listed values |
| `regex` | `'regex:^[A-Z]{3}$'` | Must match a regex pattern |
| `confirmed` | `'confirmed'` | Field `{name}_confirmation` must match |
| `same` | `'same:email'` | Must match another field's value |
| `unique` | `'unique:User,email'` | Must not exist in database (Model,field) |
| `ext` | `'ext:jpg,png,pdf'` | File extension whitelist |
| `size` | `'size:2M'` | Max file size (K, M, G) |

## Combining rules

Separate rules with `|`:

```python
'required|email|max:255'
'required|min:8|confirmed'
'numeric|min:0|max:100'
```

## Custom error messages

```python
v.rule('email', 'required|email', {
    'required': 'We need your email.',
    'email': 'That doesn\'t look like an email.',
})
```

Or with `rules()`:

```python
v.rules({
    'email': ('required|email', {
        'required': 'We need your email.',
        'email': 'Invalid email.',
    }),
    'name': 'required|min:2',  # Default messages
})
```

## Password confirmation

```python
# Form fields: password and password_confirmation
v.rules({
    'password': 'required|min:8|confirmed',
})
# Checks that request.form['password_confirmation'] == request.form['password']
```

## File validation

```python
v = Validator(request.form, request.files)
v.rules({
    'avatar': 'required|ext:jpg,png|size:2M',
})
```

## Database uniqueness

```python
v.rules({
    'email': 'required|email|unique:User,email',
})
# Checks User.find(email=value) — fails if found
```

## i18n

When used via `Form.validate()`, error messages are automatically translated using `request.__()`. No extra setup needed.

Asok looks up locale keys with a `v_` prefix. Add these to your locale files:

```json
// src/locales/en.json
{
  "v_required": "This field is required.",
  "v_email": "Invalid email address.",
  "v_min": "Minimum {arg} characters.",
  "v_max": "Maximum {arg} characters.",
  "v_numeric": "Must be a number.",
  "v_url": "Invalid URL.",
  "v_date": "Invalid date format.",
  "v_confirmed": "Confirmation does not match.",
  "v_unique": "This value is already taken."
}
```

```json
// src/locales/fr.json
{
  "v_required": "Ce champ est obligatoire.",
  "v_email": "Adresse email invalide.",
  "v_min": "Minimum {arg} caractères.",
  "v_max": "Maximum {arg} caractères.",
  "v_numeric": "Doit être un nombre.",
  "v_url": "URL invalide.",
  "v_date": "Format de date invalide.",
  "v_confirmed": "La confirmation ne correspond pas.",
  "v_unique": "Cette valeur est déjà prise."
}
```

The `{arg}` placeholder is replaced with the rule argument (e.g., `"Minimum 2 characters."`).

**Resolution order**: custom message per-field > translated default (`v_` key) > hardcoded English default.

## Custom rules

Register a global custom rule using `register_rule(name, fn, message)`.

The function `fn` receives `(value, arg, data)` and should return `True` if the value is valid.

```python
from asok import register_rule

# Register a rule that checks if a string is uppercase
def is_uppercase(value, arg, data):
    return str(value).isupper()

register_rule('uppercase', is_uppercase, 'Must be uppercase.')
```

Usage in rules:

```python
v.rules({
    'code': 'required|uppercase',
})
```

You can also access the `arg` (the value after the `:`):

```python
# Rule: startswith:ABC
def starts_with(value, arg, data):
    return str(value).startswith(arg)

register_rule('startswith', starts_with, 'Must start with {arg}.')
```

---
[← Previous: Forms](06-forms.md) | [Documentation](README.md) | [Next: Authentication →](08-authentication.md)
