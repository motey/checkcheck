# Cold-start prompt: write the developer onboarding doc

Paste everything below into a fresh session. It is written for an agent that has
not seen this repo before.

---

## Task

Write a new developer onboarding document, `docs/development.md`: the "start here
to hack on CheckCheck" on-ramp. We already have deep, specific developer docs
(the sync contract, the E2E guide); what is missing is the single entry point
that orients a new contributor and links out to them. Write that on-ramp. Link,
do not duplicate, the deep docs.

## Ground rules

- Plain prose. No em dashes, no decorative `---` thematic breaks, no LLM tics.
  Match the tone and Markdown style of the existing `docs/*.md` files.
- Use clickable relative Markdown links for every file/dir you mention.
- This is an on-ramp, not a reference. When a topic already has a doc, give a
  paragraph of orientation and link it, rather than re-explaining it.
- Verify claims against the code before writing them. Paths and script names
  below are starting points; confirm they still exist.
- When done, add `docs/development.md` to the "For developers and integrators"
  table in `docs/README.md`.

## Read these first (in this order)

1. `docs/README.md` - the documentation map.
2. `CheckCheck/backend/README.md` - backend dev setup: the dual-venv layout, the
   test runners, Alembic. Much of the backend on-ramp can lean on this.
3. `docs/archive/DOC_NOTES.md` - a "trap list" of non-obvious, hard-won facts
   (Developers / Designers / Security sections especially). This is the single
   highest-value source for what a newcomer gets wrong. Mine it.
4. `docs/SYNC_PROTOCOL.md` - the local-first delta-sync contract. The onboarding
   doc should orient the reader to it, not restate it.
5. `docs/configuration.md` + `CheckCheck/backend/checkcheckserver/config.py` -
   configuration is a single pydantic-settings model; `docs/CONFIG_REFERENCE.md`
   and `config.example.yml` are generated from it with `./gen_config_docs.sh`.
6. `docs/testing/E2E_TESTING.md` and `CheckCheck/frontend/tests/e2e/LLM_GUIDE.md`
   - the E2E how-to and selector/API reference.
7. `docs/ISSUES.md` - known rough edges.
8. The root helper scripts: `build_server_dev_env.sh`, `run_dev_frontend.sh`,
   `run_dev_backend_server_with_oidc*.sh`, `run_backend_tests_with_sqlite.sh`,
   `run_backend_tests_with_postgres.sh`, `run_e2e_tests*.sh`, `build_docker.sh`.

Also skim the code shape: backend at `CheckCheck/backend/checkcheckserver`
(`main.py` boots via `start()`, `app.py` builds the FastAPI container, `api/`
holds routers, `model/` the SQLModel tables, `db/` the engine/migrations/hooks);
frontend at `CheckCheck/frontend` (Nuxt, run with bun).

## What the doc should cover

Aim for a doc a new contributor can follow end to end. Suggested sections:

- **What you are working on.** One paragraph: web UI + REST API, local-first,
  single container. Link the root README.
- **Repository layout.** The backend/frontend split and the key directories,
  as a short table.
- **Getting a dev environment running.** The two virtualenvs and why (pdm
  `backend/.venv` runs the dev server; root `.venv` runs pytest; keep both in
  sync). How to start the dev backend and the dev frontend. The three required
  secrets and the convenient `.env`.
- **How a request flows.** `main.py start()` -> `app.py` container -> `api/`
  routers -> `model/` + `db/`. Keep it a map, not a deep dive.
- **Configuration is generated.** Edit `config.py`, run `./gen_config_docs.sh`,
  never hand-edit the two generated files. Point at `docs/configuration.md`.
- **The local-first architecture, briefly.** The layered frontend (framework-free
  cores like `utils/outbox.ts`, `deltaApply.ts`, `editGuard.ts`,
  `connectivity.ts`; then composables such as `useOutbox` / `useSync`; then
  stores that fork on `isLocalFirstEnabled()`), the two IndexedDB databases with
  opposite lifecycles (snapshot is disposable, outbox is precious), and the read
  path. Then link `docs/SYNC_PROTOCOL.md` for the contract. Verify these paths.
- **Running the tests.** Backend (SQLite quick vs PostgreSQL authoritative) and
  E2E (run Playwright via the local `@playwright/test` CLI through bun, not bare
  `bunx playwright`). Link the E2E guide. Mention the known-flake caveat.
- **Database migrations.** Alembic autogenerate on model changes; link the
  backend README section.
- **The gotchas that cost a debugging session.** Pull the highest-value items
  from `docs/archive/DOC_NOTES.md` (pydantic-Field alias in `_base_model.py`,
  `updated_at` mapper event, `server_seq` lock, tombstone-parent-only, hard
  deletes need a seq touch, `create_all`-not-migrations in tests, load-bearing
  `data-testid`s). Summarize and link, do not copy the whole file.
- **Contributing.** Branch off `main`, tests must pass on PostgreSQL, regenerate
  config docs if `config.py` changed, keep `data-testid`s stable, update
  `CHANGELOG.md` / `docs/UPGRADING.md` when relevant.

## Verify before finishing

- Every relative link resolves.
- No em dashes or stray `---`.
- The file is linked from `docs/README.md`.
- Do not commit unless asked.

## Context you can trust

- Backend is Python 3.13, FastAPI + SQLModel/SQLAlchemy async, pydantic-settings.
- Production DB is PostgreSQL; SQLite is a dev-only fallback on its way out
  (`CheckCheck/backend/README.md` has the framing).
- Local-first is the default (`NUXT_PUBLIC_LOCAL_FIRST`); `?localFirst=0` is the
  per-browser kill switch (persists in localStorage; undo with `?localFirst=1`).
- The config-docs generator (`scripts/gen_config_docs.py`, `./gen_config_docs.sh`)
  already exists and has a `--check` drift mode; psyplus lives in the backend
  `docs` dependency group.
</content>
