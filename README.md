# Beerantir

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
