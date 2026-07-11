# Documentation

Project documentation, organized by kind. Component-level docs (e.g.
`CheckCheck/backend/README.md`, `CheckCheck/frontend/README.md`) stay next to
the code they describe; this tree holds the cross-cutting plans, guides, and
historical notes that used to clutter the repo root.

## Layout

| Folder | Contents |
|--------|----------|
| [`plans/`](plans/) | Active and in-progress plans. Start with [`improvements-2026-07.md`](plans/improvements-2026-07.md); longer-horizon direction in [`VERSION_2.0_PLAN.md`](plans/VERSION_2.0_PLAN.md). |
| [`testing/`](testing/) | Testing guides — [`E2E_TESTING.md`](testing/E2E_TESTING.md) (the E2E how-to; the single-file selector/API reference lives at `CheckCheck/frontend/tests/e2e/LLM_GUIDE.md`). |
| [`archive/`](archive/) | Superseded or finished docs kept for history: shipped plans (`CARD_SHARING_*`), point-in-time status notes (`STATUS.md`, `*_STATUS.md`), the old `SYNC_PLAN.md` / `UI_POLISH.md`, the legacy repo-local `memory/` notes, and stray scratch files. |

## Living docs

- [`ISSUES.md`](ISSUES.md) — running log of known bugs / rough edges, newest
  first. Add to it when you find an issue that's out of scope for the change
  that surfaced it.
- [`UPGRADING.md`](UPGRADING.md) — self-hoster / developer upgrade notes per
  release; pairs with the root [`CHANGELOG.md`](../CHANGELOG.md).
- [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md) — the 2.0 local-first delta-sync
  contract (`/api/changes` + `/api/sync`).
