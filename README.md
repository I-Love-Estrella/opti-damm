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
