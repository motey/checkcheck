# Improvements Plan — July 2026

Small batch of UX / housekeeping issues, broken into **five independent,
session-sized chunks**. Each chunk is self-contained: it can be implemented,
tested, and committed on its own without depending on the others (ordering
notes call out the one soft dependency).

Decisions locked in with the user:

- **API keys**: lightweight **modal**, API keys only (no sessions, no admin
  user management) — opened from the user menu.
- **Confirmation**: only **permanent delete** confirms. Soft-archive stays
  instant, with an **undo toast**.
- **Docs**: move loose markdown into `docs/`, **archive** stale files (don't
  delete).
- **Counts**: exclude archived cards from per-label/filter counts (Archive has
  its own count).

Repo layout reminder: application code lives under `CheckCheck/backend` and
`CheckCheck/frontend`; the Nuxt app has no `src/` dir (components, stores,
pages, composables at the frontend root).

---

## Chunk 1 — Repo & docs organization (issue #7) — ✅ DONE (2026-07-05)

**Goal:** stop the markdown sprawl at the repo root; give plans/notes a home.

> **Outcome:** root reduced to just `README.md`. All docs moved via `git mv`
> (history preserved) into `docs/{plans,testing,archive}`; `ISSUES.md` kept as a
> living log at `docs/ISSUES.md`; legacy repo-local `memory/` → `docs/archive/
> legacy-memory/`. Fixed relative links in the moved active docs and 3
> source-comment references to the archived plans; added `docs/README.md` index
> and a root-README pointer. `Screenshot.png` stayed at root (live README
> asset). Not yet committed.

Loose root files today: `CARD_SHARING_PLAN.md`, `CARD_SHARING_PLAN_FRONTEND.md`,
`CARD_SHARING_PHASE5_TEST_NOTES.md`, `E2E_TESTING.md`, `E2E_TESTING_STATUS.md`,
`ISSUES.md`, `STATUS.md`, `SYNC_PLAN.md`, `UI_POLISH.md`, `VERSION_2.0_PLAN.md`,
plus duplicates under `memory/` (`e2e_testing_status.md`, `debug_server.md`,
`url-state-handler.md`) and a stray `coding_scratchbook.py`, `Screenshot.png`.

**Target structure**

```
docs/
├─ plans/       active + historical plans (this file lives here)
├─ testing/     E2E_TESTING.md, LLM_GUIDE, e2e notes
├─ archive/     superseded/finished docs (STATUS.md, *_STATUS.md, done plans)
README.md       stays at repo root
CLAUDE.md       (if present) stays at repo root
```

**Steps**

1. Create `docs/plans`, `docs/testing`, `docs/archive` (use `git mv` to keep
   history).
2. Move **active** references → `docs/`: `E2E_TESTING.md` → `docs/testing/`,
   `VERSION_2.0_PLAN.md` → `docs/plans/`.
3. Move **stale/finished** → `docs/archive/`: `STATUS.md`,
   `E2E_TESTING_STATUS.md`, `SYNC_PLAN.md`, `UI_POLISH.md`, the three
   `CARD_SHARING_*` files (card-sharing shipped per memory
   `frontend-e2e-double-dialog`), `ISSUES.md`.
4. Reconcile `memory/` duplicates: the canonical memory dir is
   `~/.claude/.../memory/` (see `MEMORY.md`). The repo-local `memory/*.md`
   duplicates → `docs/archive/` (or delete if byte-identical to canonical).
5. Deal with strays: `coding_scratchbook.py`, `muchdata.sqlite`,
   `Screenshot.png` — confirm they're gitignored or move out of root
   (`muchdata.sqlite` should already be ignored; verify).
6. Add a short `docs/README.md` index describing the three folders.
7. Grep the repo + shell scripts for hard references to any moved file and fix
   paths (`grep -rn "UI_POLISH\|E2E_TESTING\|CARD_SHARING" --include=*.sh
   --include=*.md .`).

**Testing:** `git status` clean review; run `run_e2e_tests.sh --help`-level
sanity that no script pathed a moved doc; CI/docs links resolve.

**Risk:** low. Only doc moves + path fixups.

---

## Chunk 2 — API keys manager modal (issue #1) — ✅ DONE (2026-07-05)

**Goal:** let a user create / copy / revoke their own API keys from the UI.
Backend is **already complete** — this is frontend only.

> **Outcome:** frontend-only, all four planned pieces landed.
> `stores/user.ts` gained `apiKeys` state + `listApiKeys` / `createApiKey` /
> `revokeApiKey` (all `skipErrorToast:true`, re-throw; the plaintext `token` is
> handed back to the caller and never stored — only the redacted view lands in
> state; revoke keys on `api_token_id`, the delete endpoint's identifier prefix,
> **not** the row `id`). New `components/ApiKeysModal.vue` mirrors
> `ShareModal/PublicLinks.vue` (one-time copy-to-clipboard token box with a
> "won't be shown again" warning + `useToast`); revoke uses a small two-click
> inline confirm. Wired into `components/Navbar.vue` via a new "API keys"
> `userMenuItems` entry (above Logout) opening the modal with a declarative
> `v-model:open` ref. Types added to `types/index.ts`
> (`ApiKeyType`/`ApiKeyCreatedType`/`ApiKeyCreateReq`). All planned
> `data-testid`s present. E2E: `tests/e2e/api-keys.spec.ts` (create → token
> shown & copyable → reload → key listed, token box gone → revoke → gone) —
> passing. Typecheck clean (pre-existing unrelated errors only).
>
> **Expiry refinement (per user):** dropped the abstract "Server default"
> option. The concrete server default duration is now surfaced via the existing
> `/api/public-config` endpoint (new `api_token_default_expiry_days`, derived
> from `API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES`) and pre-selected. Added a
> "Never expires" option gated by a new `config.py` flag
> `API_TOKEN_ALLOW_NEVER_EXPIRE` (default true, also on public-config as
> `api_token_allow_never_expire`); the option is hidden client-side **and**
> enforced server-side (422). `APIKeyCreateRequest` gained a `never_expires`
> boolean (mutually exclusive with `expires_in_days`, validated). Regenerated
> `CheckCheck/openapi.json`. Backend tests added
> (`tests_auth.py`: never-expires + conflict-422; `tests_sharing_prereqs.py`:
> the two new public-config fields) and E2E extended (Never option → key stored
> with null expiry) — all passing. Not yet committed.

Backend endpoints (self-service, in `routes_user.py`):
- `GET  /api/user/me/api-keys` → `List[UserAuthPublic]`
- `POST /api/user/me/api-keys` → `APIKeyCreatedResponse` (**plaintext `token`
  returned once**; optional `display_name`, `expires_in_days`)
- `DELETE /api/user/me/api-keys/{api_token_id}` → 204

**Frontend work**

1. **Store** (`stores/user.ts`): add `apiKeys` state + actions
   `listApiKeys()`, `createApiKey({display_name, expires_in_days})`,
   `revokeApiKey(id)`, calling `$checkapi` with the typed OpenAPI paths.
2. **Component** `components/ApiKeysModal.vue`:
   - List existing keys (name, prefix/id, created, expiry, last-used if
     present).
   - "New key" form: name + optional expiry → on submit show the **one-time
     plaintext token** in a copy-to-clipboard box with a clear "you won't see
     this again" warning (mirror the pattern in `ShareModal/PublicLinks.vue`,
     which already handles copy + not-retrievable warnings and uses `useToast`).
   - Revoke button per key with inline confirm (small — this is the *key*
     revoke, distinct from the checklist delete-confirm work in Chunk 4).
3. **Entry point** (`components/Navbar.vue`): add a "API keys" item to
   `userMenuItems` (above Logout) that opens the modal via a `v-model:open`
   ref, matching the existing declarative-modal pattern noted in memory
   `frontend-e2e-double-dialog`.
4. Add `data-testid`s (`user-menu`, `menu-api-keys`, `api-key-name-input`,
   `api-key-create`, `api-key-token`, `api-key-revoke`) for E2E.

**Testing:** E2E spec — open menu → create key → assert token shown & copyable
→ reload → key listed → revoke → gone. Verify with `/verify` driving the flow.

**Risk:** low–medium. Main care point: never persist/refetch the plaintext
token; show once.

---

## Chunk 3 — Two small UI fixes: list-view text wrap + mobile sidebar space (issues #2, #3) — ✅ DONE (2026-07-05)

Two small, unrelated frontend fixes bundled into one session.

> **Outcome:** both fixes landed, frontend-only, verified visually at real
> viewports.
> **3a:** dropped the non-standard aggressive `word-break: break-word` from the
> `CheckListItem.vue` edit `<textarea>` `:deep` CSS, keeping `overflow-wrap:
> break-word` + `white-space: pre-wrap`. The display node already used
> `break-words` (= `overflow-wrap`), so display/edit wrapping now match. Verified
> in the card editor: short words no longer break mid-word, a long URL wraps only
> where it overflows, authored newlines preserved, no textarea overflows its
> container.
> **3a follow-up (per user):** the board *preview* display node also carried
> `line-clamp-1`, which collapsed each item to a single visual line and cut
> multi-line items at their first newline (a short item `Tim\nmit zeilen
> unmbruch` previewed as `Tim…`). Final behaviour is **content-dependent** (one
> CSS rule can't do both): a `previewHasNewline` computed toggles the clamp — a
> **one-liner** stays on a single line truncated at card width
> (`line-clamp-1`, ellipsis, no wrap, as before), while an item **with an
> authored newline** honours the break up to **two lines**
> (`whitespace-pre-wrap line-clamp-2`, 3rd+ lines truncated with ellipsis).
> Verified all four cases (2-line item shows both lines; short & long one-liners
> stay one line; 3-line item → 2 lines + ellipsis).
> **3b:** the reported "empty space" was **horizontal, not the bottom** as
> guessed. `UDrawer` renders its content as a `flex flex-row-reverse` container;
> the `SideMenuDrawer.vue` inner `<div class="h-full flex flex-col">` had no
> width, so it shrank to its content (166px) and got packed to the right edge,
> leaving a ~58px empty gutter down the **left** of the 224px drawer. The footer
> was already correctly pinned to the bottom. Fix: added `w-full` to that inner
> div so the column fills the drawer width (measured 166px@left-58 → 224px@left-0
> after). Not yet committed.

### 3a. List-view text wrapping (issue #2) — Keep-like wrapping

**Symptom:** line breaks / short words wrap oddly in item text.

**Where:** `components/CheckListItem.vue`. The display node (line ~15-20) uses
`break-words whitespace-pre-wrap line-clamp-1`; the edit `<textarea>` `:deep`
CSS (line ~165-177) uses `overflow-wrap: break-word` **plus**
`word-break: break-word` (non-standard, aggressive — breaks inside short words).

**Fix direction:**
- Drop `word-break: break-word`; keep `overflow-wrap: break-word` +
  `white-space: pre-wrap` (Keep breaks only overflowing long words, preserves
  authored newlines).
- Audit the display node too: `line-clamp-1` + `whitespace-pre-wrap` are in
  tension (clamps to one line yet preserves newlines) — confirm intended board
  preview behavior and align display vs edit CSS so wrapping matches.
- Verify long unbroken URLs still wrap (don't overflow the card).

**Testing:** manually enter text with short words + hard newlines + one very
long word/URL in both board preview and edit modal; compare against Keep.

### 3b. Mobile sidebar empty space (issue #3)

**Where:** `components/SideMenuDrawer.vue` (`UDrawer` `:ui="{ content: 'w-56' }"`)
wrapping `SideMenuNav`. Investigate the empty space at drawer bottom on mobile
— likely `SideMenuNav`'s `flex-1 overflow-y-auto` nav not filling height, or the
footer/border layout. Ensure the nav column fills the drawer and the "Edit
Labels" footer sits at the bottom cleanly.

**Testing:** device-emulation in Playwright / browser; check short and long
label lists.

**Risk:** low. Pure CSS/layout.

---

## Chunk 4 — Archive filter + permanent delete + archive undo (issues #4, #5)

**Goal:** archived cards get a dedicated home; the trash action becomes a
two-stage flow (soft-archive → permanent delete only from Archive).

**Current state (confirmed):**
- Trash button (`CheckListFooter/Button/Archive.vue`) calls
  `checkListsStore.archive()` → sets `position.archived=true` only.
- The board only ever fetches `archived: false`
  (`CheckListBoard.vue:271`) — archived cards silently vanish, with **no way**
  to view or permanently delete them.
- Backend `DELETE /api/checklist/{id}` exists but is **not wired** into the
  frontend store at all.
- List endpoint already supports `?archived=true`.

**Backend:** none required (delete endpoint + archived filter both exist).

**Frontend work**

1. **Sidebar** (`SideMenuNav.vue`): add an **Archive** entry (icon
   `i-lucide-archive`) driven by a query flag, e.g. `?archived=true`, mutually
   exclusive with the shared filters. Update `isHome` so Archive isn't treated
   as home.
2. **Board** (`CheckListBoard.vue`): when the archive filter is active, fetch
   and show `archived: true` cards instead of `archived: false`. Decide paging:
   simplest is to route archived through the existing filtered-view path
   (`searchChecklists` already sends `archived:false` — generalize it to accept
   the archived flag) **or** add an archived branch to the client-side
   `getCheckLists({ archived:true })` source. Prefer extending the store's
   filtered view so paging works for large archives.
3. **Store** (`stores/checklist.ts`): add `async delete(checkListId)` calling
   `DELETE /api/checklist/{checklist_id}`, remove from `checkLists` /
   `searchResults` on success. (SSE already broadcasts `checklist_deleted`, so
   guard against double-removal.)
4. **Trash button behavior:**
   - **Home / normal view:** archive (as today) but add an **undo toast**
     (`useToast`) — "List archived · Undo" that calls `archive(id, false)`.
   - **Archive view:** the trash button = **permanent delete** and shows a
     confirm dialog ("Delete forever? This can't be undone.") before calling
     `store.delete(id)`. Pass the current view context into
     `Archive.vue` (prop like `mode: 'archive' | 'normal'`) or read the route.
5. Add an **un-archive** affordance in the Archive view (restore button) so
   users can pull a card back out — natural companion to permanent delete.
6. `data-testid`s: `sidebar-archive-filter`, `card-restore`,
   `card-delete-forever`, `confirm-delete`, `undo-archive`.

**Testing:** E2E — archive a card (assert it leaves home + undo toast restores
it) → open Archive filter (card present) → restore (back on home) → archive
again → delete-forever with confirm (gone from Archive + backend). Note memory
`flaky-e2e-dnd-sharing`: re-run before blaming flakiness.

**Ordering:** do before Chunk 5 so the Archive sidebar entry exists to receive
its count badge.

**Risk:** medium. Touches the board's fetch/paging source and DnD `watchEffect`
(don't splice archived cards into the DnD boards while a drag is in progress —
respect the existing `checklistDragInProgress` guard).

---

## Chunk 5 — Label / filter counts (issue #6)

**Goal:** show a total count behind every sidebar entry (Home, Shared with me,
Shared by me, each Label, Archive). Counts **exclude archived** except the
Archive entry itself.

**Backend work** (`routes_checklist.py` + `db/checklist.py`)

- `db/checklist.count()` already exists with `archived / label_id / search /
  shared` filters. Add **one aggregate endpoint** to avoid N+1:
  `GET /api/checklist/counts` → returns
  ```json
  {
    "home": 12,
    "shared_with_me": 3,
    "shared_by_me": 1,
    "archived": 8,
    "labels": { "<label_id>": 5, ... }
  }
  ```
  Implement `labels` as a single grouped query (`JOIN CheckListLabel ...
  GROUP BY label_id`, access-scoped via the existing
  `_add_user_has_access_query`, `archived=false`). `home`/`shared_*` reuse
  `count()`. Keep it access-scoped to the caller.
- Add a Pydantic response model (`CheckListCountsPublic`).

**Frontend work**

1. **Store** (`stores/checklist.ts` or a small dedicated getter): fetch counts,
   hold in state, expose per-label + per-filter lookups.
2. **Sidebar** (`SideMenuNav.vue`): render a right-aligned count badge on Home,
   each shared option, each label, and the Archive entry (added in Chunk 4).
   Hide when collapsed (icon-only rail).
3. **Freshness:** refetch counts on create/archive/delete/label-change. Simplest
   reliable trigger: recompute on the same SSE events that already mutate the
   board (`checklist_created`, `checklist_deleted`, position/label updates via
   `useSync`). Debounce to avoid a request per rapid event.

**Testing:** E2E — assert Home count matches rendered cards; archive a card →
Home count drops, Archive count rises; add/remove a label → label count moves.

**Risk:** low–medium. Watch the grouped-count query's access scoping (must match
the board's visibility rules exactly) and the SSE-driven refetch debounce.

---

## Suggested order

1. ~~**Chunk 1** (docs) — clears the decks, zero code risk.~~ ✅ done
2. **Chunk 4** (archive/delete) — biggest, establishes the Archive sidebar
   entry that Chunk 5 decorates.
3. **Chunk 5** (counts) — needs the Archive entry from #4.
4. ~~**Chunk 2** (API keys) — independent, any time.~~ ✅ done
5. ~~**Chunk 3** (text wrap + mobile) — independent polish, any time.~~ ✅ done

(2, 3 have no dependencies and can be reordered/parallelized freely.)
