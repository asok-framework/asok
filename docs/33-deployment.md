# Deployment

Asok is designed to be extremely easy to deploy. The framework focuses on a performance-tuned stack using **Gunicorn**, **Nginx**, and **SystemD**.

## 1. Zero-Config Deployment

The `asok deploy` command automates the generation of a professional production stack.

### Workflow

Run the command in your project root:
```bash
asok deploy
```

Asok will generate a `deployment/` directory containing:
- `gunicorn_conf.py`: Optimized worker settings based on your server's CPU.
- `nginx.conf`: Nginx reverse-proxy with **Gzip compression** and **Security headers**.
- `myapp.service`: SystemD unit file configured with your current `SECRET_KEY`.
- `setup.sh`: A comprehensive installation script for Ubuntu/Debian.

## 2. Server Setup (Ubuntu/Debian)

1. **Upload**: Copy your project (including the `deployment/` folder) to your server (e.g., via `scp` or `git clone`).
2. **Execute**: Run the setup script as root:
   ```bash
   sudo ./deployment/setup.sh
   ```

### What the script does:
- Installs `nginx`, `python3-pip`, and `python3-venv`.
- Creates a virtual environment and installs `gunicorn`.
- **Permissions**: Automatically sets correct permissions for the SQLite database and uploads folder so `www-data` (Nginx/Gunicorn) can write to them.
- **SystemD**: Configures and starts your app as a background service.
- **Nginx**: Configures Nginx to proxy traffic and serve static files with 30-day caching.

## 3. Production Hardening

### SSL (HTTPS)
Asok recommends using **Certbot** for free, automated SSL certificates:
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

### Static Asset Hash
To ensure users always see the latest version of your CSS/JS after an update, Asok supports asset hashing. This is enabled automatically in production (`DEBUG=false`).

## 4. Performance Checklist

Before deploying, ensure you have:
- Ran `asok assets --minify` to enable CSS/JS minification.
- Ran `asok image --optimize` to convert all assets to WebP.
- Configured `DEBUG=false` in your production environment.

---
[← Previous: Advanced Authentication](32-authentication-advanced.md) | [Documentation](README.md) | [Next: Advanced ORM →](34-orm-advanced.md)
