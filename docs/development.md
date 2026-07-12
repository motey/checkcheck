# Developer onboarding

Start here to hack on CheckCheck. This is the on-ramp: it orients you to the
codebase, gets a dev environment running, and points at the deeper docs for each
topic. It does not restate them. When a section links out, follow the link
before you start working in that area.

## What you are working on

CheckCheck is a collaborative, self-hostable checklist app. It ships as a single
container: a Nuxt web UI backed by a FastAPI REST API. Make lists, check things
off, organise with labels, and share individual cards with other people. It is
local-first, so the app keeps working offline and syncs back up when the
connection returns, and it installs as a PWA. The root [README.md](../README.md)
has the user-facing pitch and the Docker quickstart.

The backend is Python 3.13 (FastAPI plus SQLModel/SQLAlchemy async and
pydantic-settings). The frontend is Nuxt 4 in SPA mode, run with
[bun](https://bun.sh). Production runs on PostgreSQL; SQLite is a dev-only
convenience on its way out.

## Repository layout

| Path | What lives here |
|---|---|
| [`CheckCheck/backend/`](../CheckCheck/backend/) | The FastAPI server package `checkcheckserver` plus its tests and Alembic migrations. Its [README.md](../CheckCheck/backend/README.md) is the backend setup doc. |
| [`CheckCheck/backend/checkcheckserver/api/`](../CheckCheck/backend/checkcheckserver/api/) | Routers and the access/permission helpers. |
| [`CheckCheck/backend/checkcheckserver/model/`](../CheckCheck/backend/checkcheckserver/model/) | SQLModel table definitions and API DTOs. |
| [`CheckCheck/backend/checkcheckserver/db/`](../CheckCheck/backend/checkcheckserver/db/) | The async engine, CRUD layer, mapper-event hooks, and migrations. |
| [`CheckCheck/frontend/`](../CheckCheck/frontend/) | The Nuxt app. Its [README.md](../CheckCheck/frontend/README.md) covers the stack and conventions. |
| [`CheckCheck/frontend/utils/`](../CheckCheck/frontend/utils/) | Framework-free local-first cores (outbox, delta apply, edit guard, connectivity). |
| [`CheckCheck/frontend/composables/`](../CheckCheck/frontend/composables/) | Nuxt-bound glue over those cores (`useOutbox`, `useSync`). |
| [`CheckCheck/frontend/stores/`](../CheckCheck/frontend/stores/) | Pinia stores, one per domain. |
| [`docs/`](.) | Cross-cutting docs. Start at [README.md](README.md), the documentation map. |
| [`scripts/`](../scripts/) | Repo tooling, currently the config-docs generator. |

The `*.sh` helpers in the repo root are the entry points for everything below
(building the dev env, running the servers, running the tests, building the
image). Read one before you run it; they are short.

## Getting a dev environment running

### Two virtualenvs, on purpose

The backend uses two separate virtualenvs, and keeping them straight saves
confusion:

- [`CheckCheck/backend/.venv`](../CheckCheck/backend/) is the pdm-managed env
  that runs the **dev server**. Bootstrap it by sourcing
  [`build_server_dev_env.sh`](../build_server_dev_env.sh) from the repo root
  (source it, do not execute it, so the activation sticks in your shell).
- the root `.venv` (uv-managed) runs the **test suite** (pytest).

Keep both in sync when you change dependencies. The
[backend README](../CheckCheck/backend/README.md#dev-environment) has the
framing.

### The three secrets

A fresh instance needs three settings that have no default, or the server
refuses to start: `SERVER_SESSION_SECRET`, `AUTH_JWT_SECRET` (two long random
strings, 64+ chars, different from each other), and `ADMIN_USER_PW` (the first
admin's password). The convenient place to keep them during development is a
`.env` file next to `checkcheckserver/`. Generate the secrets with
`openssl rand -hex 32`. See [configuration.md](configuration.md) for the full
story.

### Start the servers

The backend and frontend run as two processes. Start the backend first; the
frontend dev server proxies `/api` to it on port 8181.

```bash
./run_dev_backend_server_with_oidc.sh              # dev backend on :8181 (SQLite) + a mock OIDC provider
./run_dev_backend_server_with_oidc_on_postgres.sh  # same, against PostgreSQL
```

```bash
./run_dev_frontend.sh          # dev frontend on :3000, proxying /api to :8181
./run_dev_frontend.sh --reset  # same, but wipe node_modules and .nuxt first
```

Open <http://localhost:3000>. One gotcha on plain-HTTP localhost: set
`SET_SESSION_COOKIE_SECURE=false`, or the browser silently drops the session
cookie and login appears to do nothing (see
[configuration.md](configuration.md#https-hostname-and-the-session-cookie)).

## How a request flows

Keep this as a map, not a deep dive. Read the four files in order when you need
the detail:

1. [`main.py`](../CheckCheck/backend/checkcheckserver/main.py) `start()` is the
   entry point: it reads config, configures logging, and launches uvicorn.
2. [`app.py`](../CheckCheck/backend/checkcheckserver/app.py) builds the FastAPI
   container and its lifespan (DB init, migrations, router wiring).
3. [`api/`](../CheckCheck/backend/checkcheckserver/api/) holds the routers. Every
   write goes through a permission-checked endpoint; the single access choke
   point is `_add_user_has_access_query` in
   [`api/access.py`](../CheckCheck/backend/checkcheckserver/api/access.py).
4. [`model/`](../CheckCheck/backend/checkcheckserver/model/) defines the SQLModel
   tables and DTOs, and [`db/`](../CheckCheck/backend/checkcheckserver/db/) holds
   the engine, the CRUD layer, and the mapper-event hooks that stamp `server_seq`
   and `updated_at` (more on those in Gotchas).

## Configuration is generated

Configuration is one pydantic-settings model,
[`config.py`](../CheckCheck/backend/checkcheckserver/config.py), and it is the
single source of truth. Every field can be set through an environment variable, a
`.env` file, or a `config.yml`.

Two files are generated from that model and committed:
[`CONFIG_REFERENCE.md`](CONFIG_REFERENCE.md) and
[`config.example.yml`](../config.example.yml). Never hand-edit them. When you
change a field in `config.py`, regenerate them:

```bash
./gen_config_docs.sh          # rewrite both files
./gen_config_docs.sh --check  # verify they match the model (exit 1 on drift; CI/pre-commit)
```

The generator is [`scripts/gen_config_docs.py`](../scripts/gen_config_docs.py);
it uses psyplus, which is a docs-only dependency (the `docs` group in the backend
`pyproject.toml`), not part of the runtime image. The readable introduction to
configuring an instance is [configuration.md](configuration.md).

## The local-first architecture, briefly

CheckCheck syncs with a DIY delta-sync design: the server is authoritative, each
device keeps a single-integer cursor (`server_seq`) and pulls everything that
changed since it from `GET /api/changes`, and the SSE stream (`GET /api/sync`) is
only a poke that says "pull now". Conflict resolution is per-field
Last-Writer-Wins by server-arrival order. The full client contract is
[SYNC_PROTOCOL.md](SYNC_PROTOCOL.md); read it before touching sync code. What
follows is only enough orientation to find your way around the frontend.

The frontend is layered on purpose, and new offline work should respect the
layers:

- **Framework-free cores** in [`utils/`](../CheckCheck/frontend/utils/): the
  outbox ([`outbox.ts`](../CheckCheck/frontend/utils/outbox.ts)), delta
  application ([`deltaApply.ts`](../CheckCheck/frontend/utils/deltaApply.ts)), the
  focused-field guard
  ([`editGuard.ts`](../CheckCheck/frontend/utils/editGuard.ts)), and connectivity
  ([`connectivity.ts`](../CheckCheck/frontend/utils/connectivity.ts)). These are
  plain modules with plain vitest tests, no Nuxt.
- **Nuxt-bound glue** in [`composables/`](../CheckCheck/frontend/composables/):
  [`useOutbox`](../CheckCheck/frontend/composables/useOutbox.ts) and
  [`useSync`](../CheckCheck/frontend/composables/useSync.ts) wire the cores to the
  running app.
- **Stores** in [`stores/`](../CheckCheck/frontend/stores/) that fork on
  `isLocalFirstEnabled()`
  ([`utils/localFirst.ts`](../CheckCheck/frontend/utils/localFirst.ts)): the
  local-first path optimistically mutates the store and enqueues an op (built in
  [`outboxOps.ts`](../CheckCheck/frontend/utils/outboxOps.ts)); the flag-off path
  stays legacy.

There are **two IndexedDB databases with opposite lifecycles**, and it matters
which is which:

- the **snapshot** (`checkcheck-localfirst`,
  [`snapshotDb.ts`](../CheckCheck/frontend/utils/snapshotDb.ts)) is a disposable
  cache of the board. A schema version bump may drop it.
- the **outbox** (`checkcheck-outbox`,
  [`outboxDb.ts`](../CheckCheck/frontend/utils/outboxDb.ts)) is precious: it holds
  writes the user made offline that have not reached the server. A version bump
  must migrate it, never drop it.

With the flag on, the only trigger that reads the board is the
`changes_available` poke leading to a delta pull; the legacy per-entity SSE
payloads are frozen.

Local-first is the default deploy setting (`NUXT_PUBLIC_LOCAL_FIRST`). Per
browser, `?localFirst=0` is the kill switch; it persists in that browser's
localStorage, so you undo it with `?localFirst=1`, not by removing the param.

## Running the tests

### Backend

From the repo root:

```bash
./run_backend_tests_with_sqlite.sh      # quick, Docker-free
./run_backend_tests_with_postgres.sh    # the run that counts
```

The PostgreSQL run is authoritative; a change is not trusted until it passes
there. Pass a path or `-k` filter to narrow either, for example
`./run_backend_tests_with_sqlite.sh tests/tests_auth.py`. See the
[backend README](../CheckCheck/backend/README.md#tests) for more.

### End-to-end

E2E uses Playwright: the suite builds the frontend into a static bundle, boots
the real backend on port 8182 serving both the API and the static files, and
drives it.

```bash
./run_e2e_tests.sh            # SQLite (the default)
./run_e2e_tests_postgres.sh  # PostgreSQL (needs Docker)
```

Always invoke Playwright through the wrapper scripts or `bun run test:e2e`, which
resolve the **local** `@playwright/test` CLI. A bare `bunx playwright` can pull a
cached standalone package of a different version and abort at collection ("two
different versions", 0 tests found). The full how-to is
[testing/E2E_TESTING.md](testing/E2E_TESTING.md), and the selector/API reference
for writing tests is
[`tests/e2e/LLM_GUIDE.md`](../CheckCheck/frontend/tests/e2e/LLM_GUIDE.md).

Known-flake caveat: a few DnD/sharing/counts specs fail non-deterministically in
parallel (a different disjoint set each run). Re-run before blaming your change.

## Database migrations

The schema is versioned with Alembic and migrated automatically on server start.
When you change a `table=True` model class under
[`model/`](../CheckCheck/backend/checkcheckserver/model/), generate a migration
from the repo root:

```bash
alembic revision --autogenerate -m "short description of the change"
```

Alembic reads the database URL and models from `checkcheckserver.config`, so the
same configuration drives migrations and the app. The
[backend README](../CheckCheck/backend/README.md#database-migrations-alembic) has
the details.

## The gotchas that cost a debugging session

Each of these bit someone once. They are collected, with more context, in the
archived trap list [archive/DOC_NOTES.md](archive/DOC_NOTES.md); this is the
short version.

- **`Field` in `model/_base_model.py` is pydantic's, not sqlmodel's.** So
  `index=` / `nullable=` / `primary_key=` silently no-op there. Real DB columns
  in that file need `SQLField` (sqlmodel's `Field`).
- **`updated_at` is stamped by a `before_update` mapper event, not column
  `onupdate`.** Reaching for `sa_column_kwargs` to do it instead leaks a callable
  into `json_schema_extra` and breaks OpenAPI and server boot.
- **`server_seq` allocation holds the counter-row lock until commit.** That is
  what makes cursors safe, but it serialises the commit tail and can deadlock
  under Postgres into a retryable 5xx by design. The outbox replays it; do not
  "fix" it.
- **Tombstone the parent only** (checklist / item / label); never hard-delete a
  child in the same session (cascade crash). Generic reads auto-mask
  `deleted_at`.
- **A hard delete leaves no seq trace.** Revoke and label-detach compensate by
  touching a position row so the SSE poke advances. Any new hard-delete path
  needs the same treatment, or offline clients miss the change.
- **Tests run `create_all`, not migrations.** So model definitions must be
  correct for a fresh Postgres DB on their own: naive-UTC datetimes and FK
  `ondelete=CASCADE` on the model, not only in a migration.
- **`data-testid` attributes are load-bearing** for the E2E suite. Renaming one
  is a breaking change; treat it as an API.

## Contributing

- Branch off `main`.
- Tests must pass on **PostgreSQL** (`./run_backend_tests_with_postgres.sh`), not
  just SQLite.
- If you changed `config.py`, regenerate the config docs (`./gen_config_docs.sh`)
  and commit the result; CI checks for drift with `--check`.
- Keep `data-testid`s stable, and keep the SSE routing fields
  (`target_user_ids`, `target_tokens`) server-side only, since leaking them is a
  cross-user data leak.
- Update [CHANGELOG.md](../CHANGELOG.md) and, when a change needs operator
  action on upgrade, [UPGRADING.md](UPGRADING.md).
- Known rough edges live in [ISSUES.md](ISSUES.md); check it before filing or
  fixing something, and add to it when you find a bug that is out of scope for
  what you are working on.
