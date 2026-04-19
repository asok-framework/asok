# Testing

Asok includes a WSGI test client. Simulate requests without starting a server.

## Setup

```python
# tests/test_app.py
from asok import Asok
from asok.testing import TestClient

app = Asok()
client = TestClient(app)
```

## GET request

```python
resp = client.get('/about')

resp.status_code  # 200
resp.text         # HTML string
'About' in resp   # True (supports `in` operator)
```

## POST request

```python
resp = client.post('/contact', data={
    'name': 'Alice',
    'email': 'alice@example.com',
    'message': 'Hello world!',
    'csrf_token': client.cookies.get('asok_csrf', ''),
})
```

## JSON request

```python
resp = client.post('/api/users', json_body={'name': 'Alice'})

resp.status_code  # 201
resp.json         # {"id": 1, "name": "Alice"}
```

## Custom headers

```python
resp = client.get('/api/data', headers={
    'Authorization': 'Bearer token123',
    'Accept': 'application/json',
})
```

## All HTTP methods

```python
client.get('/path')
client.post('/path', data={...})
client.put('/path', data={...})
client.patch('/path', data={...})
client.delete('/path')
```

## Cookies

The test client tracks cookies automatically (session, CSRF, flash):

```python
client.cookies  # {'asok_csrf': '...', 'asok_session': '...'}
```

## Response object

| Property | Type | Description |
|---|---|---|
| `resp.status_code` | int | HTTP status code (200, 404, etc.) |
| `resp.status` | str | Full status string ("200 OK") |
| `resp.text` | str | Response body as string |
| `resp.body` | bytes | Raw response body |
| `resp.json` | dict | Parsed JSON (raises if not JSON) |
| `resp.headers` | dict | Response headers |
| `'text' in resp` | bool | Check if text appears in body |

## Example test file

```python
# tests/test_pages.py
from asok import Asok
from asok.testing import TestClient

app = Asok()
client = TestClient(app)

def test_homepage():
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'Welcome' in resp

def test_404():
    resp = client.get('/nonexistent')
    assert resp.status_code == 404

def test_contact_form():
    # GET should show the form
    resp = client.get('/contact')
    assert resp.status_code == 200
    assert 'form' in resp.text.lower()

    # POST with valid data should redirect
    csrf = client.cookies.get('asok_csrf', '')
    resp = client.post('/contact', data={
        'name': 'Alice',
        'email': 'a@b.com',
        'message': 'Hello World!',
        'csrf_token': csrf,
    })
    assert resp.status_code == 302
```

Run with:

```bash
python -m pytest tests/
```

---
[← Previous: i18n](12-i18n.md) | [Documentation](README.md) | [Next: CLI →](14-cli.md)
