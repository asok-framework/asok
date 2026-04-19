# Deployment

## Production checklist

Before deploying, update your `.env`:

```env
DEBUG=false
SECRET_KEY=a-long-random-string-here
```

Generate a secure key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## With Gunicorn

Install Gunicorn:

```bash
pip install gunicorn
```

Run:

```bash
gunicorn wsgi:app -b 0.0.0.0:8000 -w 4
```

- `-w 4` — 4 worker processes (adjust to your CPU count)
- `-b 0.0.0.0:8000` — bind to all interfaces on port 8000

## With Nginx (reverse proxy)

```nginx
server {
    listen 80;
    server_name myapp.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /images/ {
        alias /path/to/myapp/src/partials/images/;
    }
    location /css/ {
        alias /path/to/myapp/src/partials/css/;
    }
    location /js/ {
        alias /path/to/myapp/src/partials/js/;
    }
}
```

Serving static files directly with Nginx is faster than going through Python.

## With systemd

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My Asok App
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/myapp
ExecStart=/path/to/venv/bin/gunicorn wsgi:app -b 127.0.0.1:8000 -w 4
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable myapp
sudo systemctl start myapp
```

## Enable gzip in production

If you're not using Nginx gzip:

```python
# wsgi.py
app = Asok()
app.config['GZIP'] = True
```

## Enable CORS for API

```python
app.config['CORS_ORIGINS'] = ['https://frontend.myapp.com']
```

## Performance in production

When `DEBUG=false`, Asok enables several caching layers automatically:

| What | Effect |
|---|---|
| **Route cache** | File-system walk happens once per URL path, then cached |
| **Module cache** | Page `.py` files loaded once, reused on every request |
| **Template cache** | Template files read once + compiled function cached |
| **Static file cache** | CSS/JS/images served from memory with `Cache-Control` headers |
| **Middleware chain** | Built once at startup, not rebuilt per request |
| **SQLite connections** | Thread-local reuse + WAL mode for concurrent reads |

In development (`DEBUG=true`), all caches are bypassed so changes are reflected instantly.

No configuration needed — just set `DEBUG=false` and everything is fast.

## Environment variables

All config can be set via environment variables or `.env`:

```env
DEBUG=false
SECRET_KEY=...
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=you@gmail.com
MAIL_PASSWORD=app-password
MAIL_FROM=you@gmail.com
LOG_LEVEL=INFO
LOG_FILE=/var/log/myapp/app.log
```

---
[← Previous: CORS & Gzip](15-cors-gzip.md) | [Documentation](README.md) | [Next: Background Tasks →](17-background.md)
