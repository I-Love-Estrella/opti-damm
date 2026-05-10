# opti-damm

## Local development

The backend loads `.env` and `.env.local` from the repository root. The frontend
loads `frontend/.env.local` through Next.js.

For local development, these files are already set up:

```text
.env.local              # backend: SQLite database and CORS defaults
frontend/.env.local     # frontend: backend base URL
```

Keep secrets such as `GEMINI_API_KEY` in the root `.env` file. Real env files
are ignored by git; `.env.example` documents the available variables.

Run the backend:

```bash
./.venv/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Run the frontend in a second terminal:

```bash
cd frontend
pnpm dev --hostname 127.0.0.1 --port 3000
```

If port `8000` is already occupied, run the backend on `8001` and set
`frontend/.env.local` to:

```text
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001
```

Open:

```text
http://127.0.0.1:3000/console
```

## Backend database

The backend uses Postgres by default:

```bash
docker compose up -d postgres
python -m backend.factory --force
uvicorn backend.main:app --reload
```

Default connection string:

```text
postgresql+psycopg://damm:damm@localhost:5432/damm
```

Set `DATABASE_URL` to override it.

## Railway + Vercel deployment

Deploy the backend on Railway from the repository root. The existing `Procfile`
starts FastAPI with Railway's `$PORT`:

```bash
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

Set these Railway variables:

```text
DATABASE_URL=<Railway Postgres connection string>
ALLOWED_ORIGINS=https://optidamm.ink
ALLOWED_ORIGIN_REGEX=https://.*\.vercel\.app
```

`ALLOWED_ORIGINS` should include the production frontend URL. The regex is useful
for Vercel preview deployments; remove it if previews should not call the API.

Deploy the frontend on Vercel from the `frontend` directory. Set:

```text
NEXT_PUBLIC_API_URL=https://api.optidamm.ink
```

The older `NEXT_PUBLIC_SIM_API` variable is still supported, but
`NEXT_PUBLIC_API_URL` is the preferred name.

The frontend expects one backend base URL to serve all of these paths:

```text
/health
/routes
/routes/{date}/{ruta}
/pdf/hoja-carga/{date}/{ruta}
/pdf/hoja-ruta/{date}/{ruta}
/pdf/albaran/{date}/{ruta}/{client_id}
/api/algorithms
/api/days
/api/run
/api/bench
```

## Google Cloud backend-only VM

For a backend-only Google Compute Engine deployment, keep the frontend on Vercel
or local development and point `NEXT_PUBLIC_API_URL` at the VM. See
[docs/google-cloud-backend-vm.md](docs/google-cloud-backend-vm.md).
