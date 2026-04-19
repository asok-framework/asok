# Mail

Send emails using the Python standard library (`smtplib`). No external package.

Emails are sent **in a background thread** by default — your page responds instantly without waiting for the SMTP server.

## Configuration

Add SMTP settings to `.env`:

```env
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=you@gmail.com
MAIL_PASSWORD=your-app-password
MAIL_FROM=you@gmail.com
MAIL_TLS=true
```

## Send an email

```python
from asok import Mail

Mail.send(
    to='user@example.com',
    subject='Welcome!',
    body='Thanks for signing up.',
)
```

## HTML email

```python
Mail.send(
    to='user@example.com',
    subject='Welcome!',
    body='Thanks for signing up.',           # Plain-text fallback
    html='<h1>Welcome!</h1><p>Thanks.</p>',  # HTML version
)
```

## Multiple recipients

```python
Mail.send(
    to=['alice@example.com', 'bob@example.com'],
    subject='Team update',
    body='Here is the latest update.',
)
```

## CC and BCC

```python
Mail.send(
    to='user@example.com',
    subject='Invoice',
    body='Please find your invoice.',
    cc='manager@example.com',
    bcc=['archive@example.com', 'accounting@example.com'],
)
```

## Custom sender

```python
Mail.send(
    to='user@example.com',
    subject='Support',
    body='We received your request.',
    from_addr='support@myapp.com',
)
```

## Usage in a page

```python
# src/pages/contact/page.py
from asok import Request, Form, Mail

def render(request: Request):
    form = Form({
        'email':   Form.email('Email', 'required|email'),
        'message': Form.textarea('Message', 'required|min:10'),
    }, request)

    if form.validate():
        Mail.send(
            to='admin@myapp.com',
            subject=f'Contact from {request.form["email"]}',
            body=request.form['message'],
        )
        request.flash('success', 'Message sent!')
        request.redirect('/contact')

    return request.html('page.html', form=form)
```

## Background vs synchronous

By default, `Mail.send()` runs in a background thread. The user gets an instant response.

If you need to wait for the email to actually be sent (e.g. for error handling):

```python
Mail.send(to='user@example.com', subject='Test', body='Hello', sync=True)
```

Errors in background sends are logged via `asok.mail` logger.

---
[← Previous: Middleware](09-middleware.md) | [Documentation](README.md) | [Next: Cache →](11-cache.md)
