# Google Cloud VM backend deployment

This deploys only the FastAPI backend to a Google Compute Engine VM. Keep the
frontend wherever it already runs, and point `NEXT_PUBLIC_API_URL` at the VM API.

## Recommended VM

For a hackathon demo where single-core speed matters, use a C4 or C3 high-cpu
machine if it is available in your region:

```text
c4-highcpu-2
```

If C4 is unavailable, use:

```text
c3-highcpu-4
```

Use Ubuntu 24.04 LTS, a 30 GB `hyperdisk-balanced` boot disk for C4 machines,
and allow HTTP/HTTPS traffic. The backend process itself runs on
`127.0.0.1:8000`; Nginx exposes it publicly on port 80 or 443.

## Create the VM

Replace the project, zone, and machine type as needed:

```bash
gcloud compute instances create damm-backend \
  --project YOUR_PROJECT_ID \
  --zone europe-west1-b \
  --machine-type c4-highcpu-2 \
  --image-family ubuntu-2404-lts-amd64 \
  --image-project ubuntu-os-cloud \
  --boot-disk-size 30GB \
  --boot-disk-type hyperdisk-balanced \
  --tags http-server,https-server
```

Open port 80 if your project does not already have the default HTTP rule:

```bash
gcloud compute firewall-rules create allow-damm-backend-http \
  --allow tcp:80 \
  --target-tags http-server \
  --source-ranges 0.0.0.0/0
```

SSH into the VM:

```bash
gcloud compute ssh damm-backend --zone europe-west1-b
```

## Install system packages

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip postgresql postgresql-contrib nginx
```

## Get the app onto the VM

Clone the repository, or copy it with `gcloud compute scp`. The commands below
assume the app lives at `/opt/damm-app`:

```bash
sudo mkdir -p /opt/damm-app
sudo chown "$USER:$USER" /opt/damm-app
git clone YOUR_REPO_URL /opt/damm-app
cd /opt/damm-app
```

Create the Python environment:

```bash
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

## Configure Postgres

Create the app database and user:

```bash
sudo -u postgres psql
```

In the `psql` prompt:

```sql
CREATE DATABASE damm;
CREATE USER damm WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON DATABASE damm TO damm;
\c damm
GRANT ALL ON SCHEMA public TO damm;
\q
```

Create the backend env file:

```bash
nano /opt/damm-app/.env
```

Use:

```text
DATABASE_URL=postgresql+psycopg://damm:CHANGE_ME_STRONG_PASSWORD@localhost:5432/damm
ALLOWED_ORIGINS=https://optidamm.ink,https://www.optidamm.ink
ALLOWED_ORIGIN_REGEX=https://.*\.vercel\.app
SEED_DATABASE_ON_STARTUP=false
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GEMINI_MODEL=gemini-3.1-flash
```

For a public demo, keep `ALLOWED_ORIGIN_REGEX` only if Vercel preview URLs need
access to the API.

Seed the database once:

```bash
cd /opt/damm-app
./.venv/bin/python -m backend.factory --force
```

## Create the backend service

Create `/etc/systemd/system/damm-backend.service`:

```bash
sudo nano /etc/systemd/system/damm-backend.service
```

Paste:

```ini
[Unit]
Description=Damm Smart Truck FastAPI backend
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/damm-app
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/damm-app/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

If your VM user is not `ubuntu`, change `User` and `Group`.

Start the backend:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now damm-backend
sudo systemctl status damm-backend
curl http://127.0.0.1:8000/health
```

## Put Nginx in front

Create `/etc/nginx/sites-available/damm-backend`:

```bash
sudo nano /etc/nginx/sites-available/damm-backend
```

Paste:

```nginx
server {
    listen 80;
    server_name _;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/damm-backend /etc/nginx/sites-enabled/damm-backend
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Check from your machine:

```bash
curl http://VM_EXTERNAL_IP/health
```

## Point the frontend at the VM

Set this where the frontend is deployed:

```text
NEXT_PUBLIC_API_URL=http://VM_EXTERNAL_IP
```

If you add a domain and HTTPS later, change it to:

```text
NEXT_PUBLIC_API_URL=https://api.your-domain.com
```

Also add that frontend origin to the VM backend `.env` `ALLOWED_ORIGINS`.

## Updating the backend during the demo

SSH into the VM and run:

```bash
cd /opt/damm-app
git pull
./.venv/bin/pip install -r requirements.txt
sudo systemctl restart damm-backend
curl http://127.0.0.1:8000/health
```

If data files changed and the database needs a full refresh:

```bash
cd /opt/damm-app
./.venv/bin/python -m backend.factory --force
sudo systemctl restart damm-backend
```

## Useful debugging

Backend logs:

```bash
sudo journalctl -u damm-backend -f
```

Nginx logs:

```bash
sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

Database check:

```bash
PGPASSWORD=CHANGE_ME_STRONG_PASSWORD psql -h localhost -U damm -d damm -c "select count(*) from day_cases;"
```
