# Documentation build-out plan

Goal: give CheckCheck real user/operator documentation. The config model is the
single source of truth; the reference and example config are generated from it
(psyplus), never hand-written.

House rule for all of this: plain prose. No em dashes, no `---`-as-emphasis, no
LLM tics.

## Chunk 1 - Config foundation (source of truth)

- Rewrite `CheckCheck/backend/checkcheckserver/config.py`: a `title`,
  `description`, and (where useful) `examples` on every field. Group fields under
  section banners. Keep field names, defaults, and runtime behaviour intact.
- Add YAML config-file support so `config.example.yml` is a real, loadable
  artifact: env vars keep precedence, a `config.yml` (path from
  `CHECKCHECK_CONFIG_FILE`) is layered underneath.
- `scripts/gen_config_docs.py` + `gen_config_docs.sh` render two committed files
  from the model with psyplus:
  - `docs/CONFIG_REFERENCE.md` (human reference)
  - `config.example.yml` (fillable template)
  `--check` mode fails on drift (for CI/pre-commit).
- psyplus goes in a docs-only dependency group, not runtime.

## Chunk 2 - Top-level docs

- `README.md`: what CheckCheck is, the single easiest way to run it (Docker),
  the limitations section, and a table linking the detailed docs.
- Two distinct config documents, do not merge them:
  - `docs/configuration.md` (hand-written): a readable introduction to sensible
    configuration. How config is loaded (file + env precedence), the handful of
    secrets you must set, common scenarios, and the OIDC provider block
    (hand-written because the generator cannot illustrate a list-of-objects).
    Points at the reference for the exhaustive list.
  - `docs/CONFIG_REFERENCE.md` (generated): the exhaustive per-field reference.
- `docs/deployment.md`: Docker / compose, Postgres vs SQLite, reverse proxy +
  SSE buffering, backups-are-not-neutral warning.
- `docs/administration.md`: first admin, roles, user provisioning, sharing
  switches, the localFirst kill switch.

## Chunk 3 - Tidy existing notes

- Refresh `docs/README.md` to list the new docs.
- Fold the durable facts from `DOC_NOTES.md` into the real docs, then retire it
  to `archive/`.
- Leave the plans/archive trees otherwise as-is.

Status: all three chunks done (2026-07-12).

- Chunk 1: config.py rewritten with titles/descriptions/examples + YAML
  config-file source; `scripts/gen_config_docs.py` + `gen_config_docs.sh`;
  generated `docs/CONFIG_REFERENCE.md` + `config.example.yml`; psyplus in the
  backend `docs` dependency group.
- Chunk 2: README rewritten; `docs/configuration.md`, `docs/deployment.md`,
  `docs/administration.md` written. Deployment is PostgreSQL-only; SQLite is
  documented as a dev-only fallback in `CheckCheck/backend/README.md`.
- Chunk 3: `docs/README.md` refreshed; `DOC_NOTES.md` moved to `archive/`.
  `plans/` reduced to forward-looking/in-progress docs only; the shipped 2.0
  cluster (`VERSION_2.0_PLAN`, `VERSION_2.0_WORK_ITEMS`, `PHASE_1_2_REVIEW`,
  `2.0_REVIEW_FINDINGS`) moved to `archive/` with inbound links fixed
  (`SYNC_PROTOCOL.md`, `ISSUES.md`, `docs/README.md`). The stray
  `archive/coding_scratchbook.py` was left in place (abandoned scratch, already
  quarantined in archive).

Not done (candidate follow-ups): wire `./gen_config_docs.sh --check` into CI /
pre-commit; a pytest drift test would need psyplus in the root test venv.
