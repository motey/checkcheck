# Plan: dependency update round (2026-11)

**Status:** D1, D2 (2026-11-03), D3, D4 and D5 (2026-11-04) done. D6 open. Work branch: `chore/dependency-updates` (off `origin/main` 01514e0).

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

### D3: backend FastAPI 0.162 + Starlette 1.x (done)

- FastAPI 0.118.3 to 0.162.0, Starlette 0.48.0 to 1.10.0. New transitive
  packages: `annotated-doc` and `opentelemetry-api` (FastAPI's own dependencies;
  the API package only, no SDK or exporter, so it is a no-op).
- The pin: FastAPI 0.119.0 to 0.156.x hash the `Annotated` metadata of every
  field during OpenAPI generation and crash on sqlmodel's unhashable
  `FieldInfoMetadata`. Bisected in a throwaway venv (sqlmodel 0.0.48): 0.156.0
  still crashes, 0.157.0 is the first release that dumps the spec. pyproject now
  says `fastapi>=0.157` with that reason; the `development.md` example followed.
- OpenAPI diff: only `ValidationError` gains the optional `ctx` and `input`
  properties (FastAPI now documents what pydantic already returned). The
  frontend does not reference that schema; types regenerated with
  `bun run postinstall`, no frontend change.
- Starlette 1.x surface checked: `SessionMiddleware` (OIDC state cookie),
  `CORSMiddleware`, the pure-ASGI no-store middleware, `StreamingResponse` for
  the SSE stream, `FileResponse` for the SPA files. Nothing uses the APIs 1.0
  removed (`on_event`, `@app.route`, the old `TemplateResponse(name, ctx)`
  order), and the status constants are already the new names
  (`HTTP_422_UNPROCESSABLE_CONTENT`; the old ones are deprecated aliases).
  Router `lifespan_context` (SSE listener, notification dispatcher) is still
  merged by `include_router` in 0.162. Authlib's Starlette client only uses
  `URL`, `RedirectResponse` and `request.session`.
- Found along the way: Authlib 1.9 runs its async OAuth/OIDC client on `httpx2`
  and falls back to plain `httpx` with an `AuthlibDeprecationWarning`. Only the
  dev-only `oidc-provider-mock` pulled `httpx2` in, so the tests ran the OIDC
  client on `httpx2` while the production image (from `requirements.txt`) ran it
  on the fallback. `httpx2` is now a declared runtime dependency (adds
  `httpcore2` and `truststore` to `requirements.txt`; the backend venv already
  had them, so the test runs below used the same set). Our own code (userinfo
  fetch, webhooks, tests) stays on `httpx` 0.28.
  Note for operators: on Linux `httpx2` verifies TLS against OpenSSL's system
  CA paths (or `SSL_CERT_FILE`/`SSL_CERT_DIR`) instead of the certifi bundle.
  Checked in a throwaway `python:3.13` container (the Dockerfile's base): the
  Debian CA store is there (`/usr/lib/ssl/certs`, 297 entries). A deployment that
  mounts a private CA can now point `SSL_CERT_FILE` at it.
- Results: SQLite 624 passed, 10 skipped (invite pass 11 passed, 10 skipped);
  Postgres 625 passed, 9 skipped (invite pass 11 passed, 10 skipped); E2E 148
  passed, 2 skipped. With `PYTHONWARNINGS=default::DeprecationWarning` the server
  log shows only the known sqlmodel `execute()` notices and the pre-existing
  `CheckList.position` SAWarning; no FastAPI or Starlette deprecations.
- Not verified locally: an OIDC login against a real HTTPS provider (the mock
  runs on plain HTTP), so the `httpx2` trust-store path is proven only by the
  container check above.

### D4: frontend in-range updates (done)

- `bun update` inside the caret ranges: nuxt 4.2.1 to 4.6.4, @nuxt/ui 4.1.0 to
  4.12.0, vue 3.5.24 to 3.5.44, vue-router 4.6.4, tailwindcss 4.3.4,
  @playwright/test 1.60 to 1.64.1 (Chromium headless shell 156 installed),
  dompurify 3.4.17, idb 8.0.4, markdown-it 14.3.3, colord 2.10, nuxt-open-fetch
  0.13.9, fake-indexeddb 6.2.7, the iconify packages, @types/node 25.9.9. bun
  raised the caret floors in `package.json` to the installed versions.
- Overrides: `caniuse-lite` and `baseline-browser-mapping` removed. They had
  turned into the opposite of their purpose: they held both packages below what
  consumers now ask for (1.0.30001809 against `^1.0.30001819`, 2.11.13 against
  `^2.11.33`). Without them each resolves to one current copy. `pathe` 2.0.3
  stays: `@vite-pwa/nuxt` 1.1.1 (latest), `@nuxtjs/color-mode` 3 and vitest 2
  still ask for `pathe@^1`, and without the override bun hoists 1.1.2 to the top
  level, the setup that crashed module loading in WI-13. Once D5 and color-mode 4
  are in, only `@vite-pwa/nuxt` keeps it.
- Found: @nuxt/ui 4.12 declares `@nuxt/icon ^2.5.1`, `@nuxt/fonts ^0.14.0` and
  `@nuxtjs/color-mode ^4.0.1`, but Nuxt loads only the copies we list ourselves
  (checked in `.nuxt`), so ui would have run on icon 2.1.0, fonts 0.12.1 and
  color-mode 3.5.2. Fonts was already outside ui's range before (ui 4.1 wanted
  `^0.11.4`). Fixed by moving our own entries into ui's ranges: @nuxt/icon 2.6.2
  and @nuxt/fonts 0.14.0 (still exact pins), @nuxtjs/color-mode `^4.1.0` (pulled
  forward from D6). color-mode 4 only drops the `hid` option (removed from
  `nuxt.config.ts`) and defaults `classSuffix` to `''`, which we already set. The
  rule is now in development.md. @vueuse is not affected: ui imports its own
  nested 14.x as a normal dependency.
- @formkit/drag-and-drop 0.6.1 to 0.6.2: the diff is one bugfix in sort and
  transfer (dragged values are removed by index instead of every equal value).
  The pointer, touch and long-press paths are unchanged, so the mobile reason for
  the pin still holds and the touch specs pass; on-device drag stays unverified
  as before.
- Nuxt 4.6 now scans `types/` for auto-imports, and unimport read the
  `export { }` in `types/index.ts` as an export with an empty name. The generated
  `.nuxt/types/imports.d.ts` then had a syntax error
  (`const : typeof import(...)['']`), which hides every other type error. The
  line was redundant (the file has top-level imports) and is gone, with a comment.
- Typecheck (`vue-tsc --noEmit -p .nuxt/tsconfig.app.json`, with a throwaway
  vue-tsc, the project does not ship one): 87 errors before, 70 after, none new.
  The 17 that went away are `@click` handlers returning values and the
  Playwright config overloads, both accepted by the newer types.
- Nuxt 4.6 declares `node ^22.22.3 || ^24.15.0 || >=26` and prints "Current
  version of Node.js (24.3.0) is unsupported" on every run. That is bun 1.3.14
  identifying itself as Node 24.3.0; there is no real Node in the toolchain
  (local, CI and the `oven/bun` image all run on bun), so it is cosmetic until
  bun reports a newer Node version.
- E2E: one flake in the full run, `archive.spec.ts` "delete forever": the board
  drops the card optimistically, and the trace shows the test's GET went out
  0.4 ms after the app's DELETE, while it was still in flight, so it read 200
  instead of 410. A race in the test, not caused by the update (the new
  versions only change the timing). The test now waits for the app's DELETE
  response first; no other spec checks the backend right after an optimistic
  removal. Verified with `--repeat-each=10 --retries=0`: 31 passed.
- CI: actions/checkout 6 to 7, setup-python 5 to 7, upload-artifact 4 to 7,
  download-artifact 4 to 8. Release notes read: the breaking parts are Node 24
  runners (GitHub-hosted runners are fine), checkout blocking fork PRs under
  `pull_request_target`/`workflow_run` (not used), setup-python dropping the
  `pip-install` input (not used), download-artifact path changes for downloads
  by ID and digest mismatches now failing (we download by name pattern, and a
  mismatch should fail). Not verifiable locally; the next push shows it. The
  docker/* actions and setup-bun were already on their current majors.
- Build output checked: 14 self-hosted Inter files in `/_fonts/`, all in the
  service worker precache; the color-mode inline script still uses
  `nuxt-color-mode` and `__NUXT_COLOR_MODE__`.
- Results: vitest 417 passed; E2E 147 passed, 2 skipped, 1 flaky (the archive
  race above, fixed); backend unchanged and green: SQLite 624
  passed, 10 skipped; Postgres 625 passed, 9 skipped (invite passes 11 passed, 10
  skipped each).

### D5: frontend test tooling majors (done)

- vitest 2.1.9 to 5.1.0, @types/node 25.9.9 to 26.6.6. jsdom stays on 29.1.1
  (see below).
- Migration guides for 3.0, 4.0 and 5.0 read (5.0 shipped 2026-09-03) and the
  specs checked for every pattern they flag: no unawaited `.resolves`/`.rejects`,
  no `toThrow("")`, no `vi.mock`/`vi.hoisted` outside the top level, no `new`
  on an arrow-function mock, no snapshots or `test.each` titles, no
  `performance.now()`/`Temporal` in faked code paths. The behaviour changes
  (fake timers now fake everything, `clearMocks` on by default, `restoreAllMocks`
  only restoring `vi.spyOn` spies, DOM global assignments reaching the jsdom
  window) needed no change: the suite passes with the same 417 tests. The config
  needed nothing either (explicit `include`, no pool options, no workspace). CI's
  `--reporter=json --outputFile=vitest-report.json` still writes the file the
  badge step reads; only the reporters' defaults moved to `.vitest/`.
- Typecheck: since vitest 4 an untyped `vi.fn()` is
  `Mock<Procedure | Constructable>` (mocks can be constructors), which no longer
  fits a typed callback parameter. Five new errors, all in
  `debouncedCardFields.spec.ts`; the mock is typed now
  (`vi.fn<WriteFn>()`). Afterwards the error list is identical to D4's (70,
  compared with the same throwaway vue-tsc 3.3.14 against a worktree of the D4
  commit). @types/node 26 adds no errors.
- jsdom 30: every `@vitest-environment jsdom` spec (4 files) died at worker
  start with "'addEventListener' called on an object that is not a valid
  instance of EventTarget". Isolated in scratch projects: vitest 5 + jsdom 29
  works on bun; vitest 5 + jsdom 30 fails on bun and passes on real Node 24
  (throwaway `node:24` container). The cause is outside vitest: on bun, a plain
  `new JSDOM(html, { runScripts })` window rejects its own `addEventListener`
  whenever `runScripts` is set (the window then lives in a `vm` context; vitest
  always sets it), and works without it. jsdom 30's only listed breaking change
  is the engine floor `^22.22.2 || ^24.15.0 || >=26`, and bun 1.3.14 reports
  Node 24.3.0, so it does not support our runtime anyway. Kept jsdom 29 (vitest
  5 accepts any jsdom; jsdom 29 declares `>=24.0.0`) rather than patching
  `runScripts` or adding a real Node to the local and CI toolchain for four spec
  files. The rule is in development.md. Retry jsdom 30 when bun reports Node
  `^24.15` or newer (`bun -e 'console.log(process.version)'`). Not reported
  upstream.
- Side effect on the build: vitest 2 had pulled `vite` 5.4.21 to the top level
  while Nuxt's builder ran its nested vite 8.3.5, so every Vite plugin that
  resolves `vite` from the top level (vite-plugin-pwa, @vitejs/plugin-vue,
  @tailwindcss/vite, unhead, unplugin, fontless) imported vite 5 next to a vite 8
  build; unhead even declares `vite >=6.4.2`. Now there is one vite (8.3.5), and
  the old vite 5 tree (esbuild 0.21, rollup, vite-node 2) is gone from the lock.
- `pathe` override: after this step only `@vite-pwa/nuxt` (1.1.1, latest) still
  asks for `pathe@^1`, as D4 predicted. The override stays.
- Build output checked as in D4: 14 Inter files in `/_fonts/`, all in the
  service worker precache; the color-mode inline script is there.
- Results: vitest 417 passed (23 files, same as before); E2E 148 passed, 2
  skipped, no flakes; backend unchanged and green: SQLite 624 passed, 10
  skipped; Postgres 625 passed, 9 skipped (invite passes 11 passed, 10 skipped
  each).

### D6: frontend runtime majors

- One package per step, each with its own test run: @vueuse 13 to 15, pinia 4 +
  @pinia/nuxt 1.0, @nuxt/fonts 1.x, markdown-it 15 (card notes and the inline
  item renderer), vue-router 5 (only if nuxt 4.6 supports it; nuxt pins the
  router it needs).
- @nuxtjs/color-mode 4 already moved in D4 (@nuxt/ui 4.12 requires it).
- @nuxt/fonts 1.x: @nuxt/ui 4.12 declares `^0.14.0`, so stay on 0.14 until a
  @nuxt/ui release accepts 1.x (see the D4 note on module copies).
- @vueuse: @nuxt/ui 4.12 brings its own `^14.4.0`. Going to 14 instead of 15
  would let both share one copy; decide when doing the step.
- Anything that turns out to be a rewrite moves into its own plan.
