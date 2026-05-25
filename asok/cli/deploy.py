from __future__ import annotations

import os

from .style import Style


def run_deploy(root: str) -> None:
    """Generate professional, generic production deployment configurations."""
    app_name = os.path.basename(root)
    deploy_dir = os.path.join(root, "deployment")
    os.makedirs(deploy_dir, exist_ok=True)

    Style.heading("GENERATING PRODUCTION DEPLOYMENT STACK")

    # Try to grab SECRET_KEY from current .env
    secret_key = "CHANGE_ME_TO_A_LONG_SECURE_STRING"
    env_path = os.path.join(root, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith("SECRET_KEY="):
                    secret_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    # 1. Gunicorn Config (Optimized)
    gunicorn_conf = f"""# Gunicorn configuration for {app_name}
import multiprocessing

bind = "unix:{root}/{app_name}.sock"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 30
keepalive = 2
accesslog = "-"
errorlog = "-"
loglevel = "info"
"""
    with open(os.path.join(deploy_dir, "gunicorn_conf.py"), "w") as f:
        f.write(gunicorn_conf)
    print(
        f"  {Style.GREEN}✓{Style.RESET} Generated gunicorn_conf.py (Optimized Unix Socket)"
    )

    # 2. Nginx Config (High Performance)
    nginx_conf = f"""server {{
    listen 80;
    server_name yourdomain.com; # <--- UPDATE THIS

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Content-Type-Options "nosniff";

    # Gzip Compression
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/xml+rss image/svg+xml;

    location / {{
        proxy_pass http://unix:{root}/{app_name}.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location /static/ {{
        alias {os.path.join(root, "src/partials/")};
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }}

    # WebSocket support (Asok native)
    location /ws/ {{
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }}
}}
"""
    with open(os.path.join(deploy_dir, "nginx.conf"), "w") as f:
        f.write(nginx_conf)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated nginx.conf (Gzip + Security)")

    # 3. SystemD Service
    service_conf = f"""[Unit]
Description=Asok Application: {app_name}
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory={root}
# Automatically detect virtualenv
Environment="PATH={root}/venv/bin"
Environment="SECRET_KEY={secret_key}"
Environment="DEBUG=false"
Environment="PYTHONPATH={root}"
ExecStart={root}/venv/bin/gunicorn wsgi:app -c deployment/gunicorn_conf.py

[Install]
WantedBy=multi-user.target
"""
    with open(os.path.join(deploy_dir, f"{app_name}.service"), "w") as f:
        f.write(service_conf)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated {app_name}.service (Stateless)")

    # 4. Setup Script (Automated)
    setup_sh = f"""#!/bin/bash
# Universal Asok Setup Script for Ubuntu/Debian
set -e

echo "--------------------------------------------------------"
echo "  ASOK PRODUCTION SETUP: {app_name}"
echo "--------------------------------------------------------"

# 1. System Dependencies
echo "[1/5] Installing system dependencies..."
sudo apt update
sudo apt install -y nginx python3-pip python3-venv

# 2. Virtual Environment
echo "[2/5] Setting up virtual environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install gunicorn asok

# Attempt to install requirements if they exist
if [ -f "requirements.txt" ]; then
    ./venv/bin/pip install -r requirements.txt
fi

# 3. Permissions (Crucial for SQLite/Uploads)
echo "[3/5] Setting up permissions for www-data..."
sudo chown -R $USER:www-data .
# Allow group write on the directory itself for SQLite WAL/SHM files
sudo chmod 775 .
sudo chmod -R 775 src/partials/uploads || true
if [ -f "db.sqlite3" ]; then
    sudo chown www-data:www-data db.sqlite3
    sudo chmod 664 db.sqlite3
fi

# 4. SystemD Config
echo "[4/5] Configuring SystemD service..."
sudo cp deployment/{app_name}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable {app_name}
sudo systemctl restart {app_name}

# 5. Nginx Config
echo "[5/5] Configuring Nginx reverse-proxy..."
sudo cp deployment/nginx.conf /etc/nginx/sites-available/{app_name}
sudo ln -sf /etc/nginx/sites-available/{app_name} /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

echo "--------------------------------------------------------"
echo "  SUCCESS! YOUR APP IS NOW LIVE."
echo "--------------------------------------------------------"
echo "Next steps:"
echo "1. Update yourdomain.com in /etc/nginx/sites-available/{app_name}"
echo "2. Run: sudo apt install certbot python3-certbot-nginx"
echo "3. Run: sudo certbot --nginx -d yourdomain.com"
echo "--------------------------------------------------------"
"""
    with open(os.path.join(deploy_dir, "setup.sh"), "w") as f:
        f.write(setup_sh)
    os.chmod(os.path.join(deploy_dir, "setup.sh"), 0o755)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated setup.sh (Automated)")

    Style.success("\nDeployment stack generated successfully in: deployment/")
    print(
        f"  To deploy, copy the folder to your server and run: {Style.BOLD}sudo ./deployment/setup.sh{Style.RESET}\n"
    )
