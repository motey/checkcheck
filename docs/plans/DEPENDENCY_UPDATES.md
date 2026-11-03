# Plan: dependency update round (2026-11)

**Status:** D1 and D2 done (2026-11-03). D3 to D6 open. Work branch: `chore/dependency-updates` (off `origin/main` 01514e0).

The process itself (loose `pyproject.toml`, `build_requirement_files.sh`, both
venvs, bun ranges) is documented in
[development.md, Updating dependencies](../development.md#updating-dependencies).
This file only tracks the current round.

Every chunk ends with the backend suites (SQLite and Postgres) and, for frontend
chunks, `bunx vitest run` plus the full E2E run. A test that fails after an update
is a finding to explain, not something to re-run until it is green.

## Inventory at the start (2026-11-03)

Backend, `pdm outdated`: 19 direct packages behind. The large jumps were FastAPI
0.118 to 0.162 (held by our `<0.119` pin), Starlette 0.48 to 1.10 (held by
FastAPI), SQLAlchemy 2.0 to 2.1, sqlmodel 0.0.39 to 0.0.48, Authlib 1.7 to 1.9,
oauthlib 3 to 4. httpx 1.0 exists only as a pre-release (`1.0.dev6`); stay on
0.28 until it is final.

Frontend, `bun outdated`: 26 packages behind. In range: nuxt 4.2 to 4.6, @nuxt/ui
4.1 to 4.12, vue 3.5.24 to 3.5.44, tailwindcss 4.1 to 4.3, @playwright/test 1.60
to 1.64 and many small ones. Majors: vitest 2 to 5, jsdom 29 to 30, @types/node
25 to 26, @vueuse/* 13 to 15, pinia 3 to 4 with @pinia/nuxt 0.11 to 1.0,
vue-router 4 to 5, @nuxtjs/color-mode 3 to 4, @nuxt/fonts 0.12 to 1.2,
markdown-it 14 to 15. Exact pins behind: @formkit/drag-and-drop 0.6.1 (0.6.2),
@nuxt/icon 2.1.0 (2.6.2), @nuxt/fonts 0.12.1.

## Chunks

### D1: backend patch and minor updates, process, harness (done)

- `./build_requirement_files.sh --upgrade`: everything the constraints allow,
  for example Authlib 1.9.3, pydantic 2.14.2, alembic 1.20.1, uvicorn 0.54.0,
  pywebpush 2.5.0, asyncpg 0.32.0.
- Tried SQLAlchemy 2.1.7 with sqlmodel 0.0.48: the server crashes at boot (see
  D2). Fell back with a `sqlmodel<0.0.45` pin, which also keeps SQLAlchemy on
  2.0.x (2.0.56).
- Removed `oauthlib` and `pip-tools` from the runtime dependencies: nothing
  imports them (Authlib does OIDC; the Dockerfile stopped using pip-tools in 0.4.0).
- `build_requirement_files.sh` reworked: locks once instead of three times, keeps
  existing pins by default (`--update-reuse`), full re-resolve only with
  `--upgrade`.
- Test harness (`tests/conftest.py`): a server crash during startup now prints
  the server's last output lines in the exit message (they were DEBUG-only),
  and pytest exits right away instead of hanging on the OIDC mock's non-daemon
  thread (the `yield` teardown never ran on that path).
- The root uv `.venv` is retired: `run_backend_tests_with_*.sh` call
  `CheckCheck/backend/.venv/bin/python` directly, like the E2E setup and the
  `gen_*.sh` helpers already did. CI already used that venv.
- Process documented in development.md and the backend README.

### D2: backend sqlmodel 0.0.48 + SQLAlchemy 2.1 (done)

- The problem: since 0.0.45 sqlmodel maps a bare `datetime` field to its own
  tz-aware `UTCDateTime` (`timestamptz`), which raises on naive values. The
  project stores naive UTC everywhere, so the server died on the first insert.
  SQLAlchemy 2.1 is only allowed by sqlmodel >= 0.0.48, so both moved together
  (sqlmodel 0.0.44 to 0.0.48, SQLAlchemy 2.0.56 to 2.1.7; nothing else in the
  lock changed). pyproject now says `sqlmodel>=0.0.48`.
- Decision: `NaiveDatetime`, not tz-aware values. The 12 timestamp field
  declarations behind the 48 table columns (`created_at`, `updated_at`,
  `deleted_at` on the base models, plus `read_at`, `remind_at`, `last_fired_at`,
  `last_used_at`, `expires_at`, `first_opened_at`, `last_seen_at`,
  `last_oidc_login_at`, `not_before`) are annotated `NaiveDatetime`. It maps to
  `DateTime(timezone=False)`, the exact type the columns already had. Pydantic
  now also rejects aware values for these fields; that is safe because every
  internal write already uses `naive_utc_now()` and the two request bodies that
  take client timestamps (reminders, public-link expiry) are separate models that
  normalise to naive UTC first. Plain response models keep `datetime`.
- Verified: Postgres DDL for every table, the datetime column list and the
  OpenAPI spec are byte-identical before and after. A Postgres DB built by the old
  code with the old libraries (alembic 0020) boots with the new code and alembic
  autogenerate against it is empty, so existing installs need no migration.
- SQLAlchemy 2.1 migration notes read. Nothing that applies: greenlet still comes
  with the `[asyncio]` extra; the new autoflush before `text()`/Core statements
  only touches the `pg_notify` and `sync_seq` reads, which are in the same
  transaction anyway; the `postgresql://` default driver change does not matter
  because the URL is `postgresql+asyncpg` and `POSTGRES_DSN` goes to raw asyncpg;
  the native `user_auth` enum survives `create_all` on an existing DB (covered by
  the old-DB check). Both suites ran with `PYTHONWARNINGS=default::DeprecationWarning`
  passed into the server subprocess: no SQLAlchemy or sqlmodel deprecations.
- The only warning left was alembic's `No path_separator found` (alembic 1.16
  renamed `version_path_separator`), fixed in `alembic.ini`.
- New guard test `tests/tests_model_datetime_columns.py`: fails if any table
  column is tz-aware, i.e. a new field was declared as a bare `datetime`. The
  rule is also in development.md (gotchas) and next to `naive_utc_now()`.
- Results on the final state: SQLite 624 passed, 10 skipped; Postgres 625
  passed, 9 skipped.
- Found along the way, not caused by D2 (same warning with SQLAlchemy 2.0.56 and
  sqlmodel 0.0.44, checked with the old code): `SAWarning: Multiple rows returned
  with uselist=False for eagerly-loaded attribute 'CheckList.position'`, once per
  run, in `tests_bulk_item_ops.py::test_uncheck_all_requires_check_permission`
  (the viewer's 403 request). `CheckList.position` is a `lazy="joined"`,
  single-valued relationship, but `checklist_position` has one row per user, so
  on a shared card the eager load picks an arbitrary collaborator's row. Worth
  its own look (scope the relationship to the caller, or drop the eager load where
  the per-user position is fetched separately); out of scope for this round.
- Harness, done in the same session: the backend tests needed a gitignored,
  undocumented `tests/.env` (CI wrote its own copy inline in `tests.yml`), so a
  fresh clone could not run them. Now the throwaway defaults are tracked in
  `tests/test_defaults.env`; conftest layers environment > optional
  `tests/.env` > defaults, and the CI step that wrote the file is gone.
  Checked in a copy of the tree with no `tests/.env` and no secrets in the env,
  and the precedence order checked directly.

### D3: backend FastAPI 0.162 + Starlette 1.x
- The `fastapi<0.119` pin exists because FastAPI 0.119 hashes `Annotated`
  metadata during OpenAPI generation and sqlmodel's `FieldInfoMetadata` is an
  unhashable (non-frozen) dataclass. Still true in sqlmodel 0.0.48 (checked
  2026-11-03). First step: try the newest FastAPI in a throwaway venv and dump
  the OpenAPI spec; FastAPI may have changed that code path since. If it still
  crashes, look for the upstream issue or a local workaround before lifting.
- Starlette 1.x is a major: check `StarletteOAuth2App` / Authlib's Starlette
  integration, `SessionMiddleware`, `StaticFiles` and the SSE endpoint.
- Diff the regenerated `CheckCheck/openapi.json`; a schema change there means a
  frontend type change.

### D4: frontend in-range updates

- `bun update` (stays inside the caret ranges): nuxt 4.6, @nuxt/ui 4.12, vue,
  tailwindcss 4.3, @playwright/test 1.64 (then `bun run test:e2e:install` for the
  matching Chromium), iconify, dompurify, idb, nuxt-open-fetch, fake-indexeddb.
- Re-check whether the `overrides` block (pathe, caniuse-lite,
  baseline-browser-mapping) is still needed.
- Exact pins: @nuxt/icon 2.6.2 is likely safe; @formkit/drag-and-drop 0.6.2
  needs the mobile long-press drag checked again (it was pinned for that).
- CI: `actions/upload-artifact@v4`, `download-artifact@v4`, `setup-python@v5`
  have newer majors; bump them here.

### D5: frontend test tooling majors

- vitest 2 to 5 (config and fake-timer behaviour; the localSnapshot specs use fake
  timers with an in-memory cursor), jsdom 29 to 30, @types/node 26.

### D6: frontend runtime majors

- One package per step, each with its own test run: @vueuse 13 to 15, pinia 4 +
  @pinia/nuxt 1.0, @nuxtjs/color-mode 4 (check whether @nuxt/ui still expects
  v3), @nuxt/fonts 1.x, markdown-it 15 (card notes and the inline item renderer),
  vue-router 5 (only if nuxt 4.6 supports it; nuxt pins the router it needs).
- Anything that turns out to be a rewrite moves into its own plan.
