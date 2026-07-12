# Documentation

Project documentation, organized by kind. Component-level docs (for example
`CheckCheck/backend/README.md`, `CheckCheck/frontend/README.md`) stay next to the
code they describe; this tree holds the cross-cutting guides, references, and
historical notes.

## For operators and self-hosters

| Document | What it covers |
|--------|----------|
| [configuration.md](configuration.md) | Readable introduction to configuring an instance: source precedence, required secrets, common scenarios, OIDC. |
| [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) | Generated reference for every config field. Do not hand-edit; regenerate with `./gen_config_docs.sh`. |
| [deployment.md](deployment.md) | Running with Docker and compose, PostgreSQL, reverse proxies, backups, building the image. |
| [administration.md](administration.md) | First admin, roles, adding users, sharing switches, the offline kill switch. |
| [UPGRADING.md](UPGRADING.md) | Per-release upgrade notes; pairs with the root [CHANGELOG.md](../CHANGELOG.md). |

## For developers and integrators

| Document | What it covers |
|--------|----------|
| [development.md](development.md) | Developer onboarding: the start-here on-ramp for hacking on CheckCheck. Repo layout, dev environment, request flow, tests, and the gotchas. |
| [SYNC_PROTOCOL.md](SYNC_PROTOCOL.md) | The 2.0 local-first delta-sync contract (`/api/changes` + `/api/sync`). |
| [ISSUES.md](ISSUES.md) | Running log of known bugs and rough edges, newest first. |
| [testing/E2E_TESTING.md](testing/E2E_TESTING.md) | The E2E how-to. The selector/API reference lives at `CheckCheck/frontend/tests/e2e/LLM_GUIDE.md`. |

## Layout

| Folder | Contents |
|--------|----------|
| [`plans/`](plans/) | Forward-looking and in-progress plans only: this session's [`DOCUMENTATION_PLAN.md`](plans/DOCUMENTATION_PLAN.md), the [`improvements-2026-07.md`](plans/improvements-2026-07.md) batch, and the 2.5 / 3.0 idea docs. |
| [`testing/`](testing/) | Testing guides. |
| [`archive/`](archive/) | Superseded or finished docs kept for history: the shipped 2.0 plan and work items, the 2.0 / phase reviews, the card-sharing plans, point-in-time status notes, and the legacy repo-local `memory/` notes. |
