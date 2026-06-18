# CheckCheck backend tests

Integration tests that run against a **real** server instance over HTTP. The
pytest harness (`conftest.py`) boots `checkcheckserver/main.py` as a subprocess,
waits until `/api/health` is healthy, logs in as the admin user, and tears
everything down at the end of the session.

## Running

From the repo root (with the backend venv active — `source build_server_dev_env.sh`):

```bash
./run_backend_tests_with_postgres.sh        # Postgres in Docker (primary target)
./run_backend_tests_with_sqlite.sh          # SQLite file (no Docker needed)
```

Both scripts accept extra pytest arguments:

```bash
./run_backend_tests_with_postgres.sh --dev              # stop at first failure, verbose + server logs
./run_backend_tests_with_sqlite.sh tests/tests_auth.py  # a single file
./run_backend_tests_with_sqlite.sh -k checklist         # filter by name
```

Or invoke pytest directly from `CheckCheck/backend`:

```bash
python -m pytest tests                 # SQLite (default)
python -m pytest tests --db=postgres   # Docker Postgres
```

## Database options

- `--db=sqlite` (default): a file at `tests/testdb.sqlite`, recreated on each
  run. Set `CHECKCHECK_TESTS_RESET_DB=false` to keep it across runs.
- `--db=postgres`: a throwaway Docker Postgres container (started and removed
  automatically by the conftest). Requires Docker.

## OIDC tests

`tests_oidc_mapping.py` drives the full OIDC authorization-code flow against an
in-process mock provider (`oidc_provider_mock`, a dev dependency). The conftest
starts the mock, registers test users, and configures the provider before the
server boots. These tests skip automatically if `oidc_provider_mock` is not
installed (`OIDC_MOCK_SERVER_URL` unset).
