# CheckCheck backend

The CheckCheck server: a FastAPI application (`checkcheckserver`) that serves the
REST API and the built frontend. This file is the developer setup for the
backend. For running a real instance see [docs/deployment.md](../../docs/deployment.md);
for settings see [docs/configuration.md](../../docs/configuration.md).

## Dev environment

All Python tooling runs on the pdm-managed `CheckCheck/backend/.venv`: the dev
server, the test runners and the E2E backend. Bootstrap it with
`source build_server_dev_env.sh` from the repo root.

Dependencies are declared loosely in `pyproject.toml` and pinned via `pdm.lock`
plus the exported `requirements*.txt` (run `../../build_requirement_files.sh`);
the full workflow is in
[Updating dependencies](../../docs/development.md#updating-dependencies).

## Configuration

Configuration is a pydantic-settings model in
[`checkcheckserver/config.py`](checkcheckserver/config.py). It is the single
source of truth; `docs/CONFIG_REFERENCE.md` and `config.example.yml` are
generated from it with `./gen_config_docs.sh`. Every field can be set through an
environment variable, a `.env` file, or a `config.yml`.

The three required settings with no default are `SERVER_SESSION_SECRET`,
`AUTH_JWT_SECRET`, and `ADMIN_USER_PW`. A local `.env` next to
`checkcheckserver/` is the convenient place to keep them during development.

## Database (development)

Production runs on PostgreSQL (see [docs/deployment.md](../../docs/deployment.md)).
For local development the default `SQL_DATABASE_URL`
(`sqlite+aiosqlite:///./local.sqlite`) uses a bundled SQLite file so the server
boots with no database to set up.

SQLite is development-only: it is single-process (the real-time sync fan-out
that PostgreSQL does through `pg_notify` falls back to an in-process loop) and is
on track to be removed. Do not run a real instance on it. Test against
PostgreSQL before trusting a change; SQLite is the convenience path, not the
target.

## Tests

From the repo root:

```bash
./run_backend_tests_with_sqlite.sh      # quick, Docker-free
./run_backend_tests_with_postgres.sh    # the run that counts
```

The Postgres run is the authoritative one. Pass a path or `-k` filter to narrow
it, for example `./run_backend_tests_with_sqlite.sh tests/tests_auth.py`.
The test config comes from the tracked `tests/test_defaults.env`; personal
overrides go in a gitignored `tests/.env` or the environment (see
[development.md](../../docs/development.md#backend)).

## Database migrations (Alembic)

The schema is versioned with Alembic and migrated automatically on server start.
When you change a `table=True` model class under `checkcheckserver/model`,
generate a migration from the repo root:

```bash
alembic revision --autogenerate -m "short description of the change"
```

Alembic reads the database URL and models from `checkcheckserver.config`, so the
same configuration drives migrations and the app.
