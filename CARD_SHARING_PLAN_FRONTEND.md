# Card Sharing — Frontend Implementation Plan & Tracker

Companion to `CARD_SHARING_PLAN.md`. The **backend is complete** (Phases 1–10: per-user
shares, user/group search, public URL links with password + expiry, public-link join,
invite/accept flow, and an in-app notification feed). This document plans the **Nuxt 4
frontend** that surfaces all of it.

Stack recap (so future sessions don't re-derive it):
- **Nuxt 4** (`compatibilityVersion: 4`), **SSR off**, **Pinia** stores, **@nuxt/ui** v3
  components, **nuxt-open-fetch** typed client.
- The typed API client is `$checkapi` (`useNuxtApp().$checkapi`) / `useCheckapi()`, generated
  from `CheckCheck/openapi.json` (already regenerated — it contains every sharing endpoint).
  Path params go in `path: {...}`, query in `query: {...}`, body in `body`.
- Global response/body types are aliased in `types/index.ts` from `components["schemas"][...]`.
- URL-reflected app state lives in `composables/useAppRoute.ts` (opened card = `/card/:cardId`,
  label editor = `?editlabels`, search = `?search`, filter = `?label`).
- Live updates: `composables/useSync.ts` is a single shared `EventSource("/api/sync")`; it
  dispatches `SyncNotificationType.upd_prop` into store mutations.
- Modals use `useOverlay()` + `overlay.create(Component)` (see `pages/index.vue`).
- 401 handling is centralised in `plugins/api.ts` (redirect to `/login`).

> **Convention for this tracker:** mark phases `📋 PLANNED` / `🚧 IN PROGRESS` / `✅ DONE` as
> the backend file did. Keep both the dev server (`./run_dev_frontend.sh`) and the Playwright
> E2E suite green (see `E2E_TESTING.md`).

---

## Backend prerequisites (do these FIRST — the UI cannot be permission-aware without them) — ✅ DONE

These are the **only** backend changes the frontend needs. They are small and additive.
Both landed together; `CheckCheck/openapi.json` has been regenerated (the test server
dumps it on boot). **Before the first frontend phase, run `bunx nuxi prepare` in
`CheckCheck/frontend` so the open-fetch types pick up the new fields/endpoint.**

> **Implementation notes (what actually shipped):**
> - **`my_permission` is attached, not stored.** A shared helper
>   `api/access.py::attach_my_permission(checklist, level)` normalises a
>   `ChecklistAccessLevel` / `SharePermission` / plain string to the ladder's string
>   value and writes it into the ORM instance's `__dict__`. It can't be a plain
>   attribute set (`checklist.my_permission = …`) — SQLModel's Pydantic `__setattr__`
>   rejects assigning a non-field attribute (`"CheckList" object has no field …`). The
>   response serialiser reads it back via `from_attributes`, and SQLAlchemy's unit of
>   work ignores the unmapped key.
> - Every route returning a `CheckListApiWithSubObj` calls the helper: list / create /
>   get / update (`routes_checklist.py`), accept-invite (`routes_checklist_share.py`),
>   and public read / join (`routes_checklist_public.py`).
> - The grid list resolves all collaborators' levels in **one** query via
>   `CheckListCollaboratorCRUD.permissions_for_user_by_checklist(...)` (owner →
>   `"owner"`; a non-owned listed card is always an accepted collaboration, so a missing
>   entry defensively falls back to `"view"` rather than 500-ing the whole grid).
> - **Tests:** `tests/tests_sharing_prereqs.py` (9 tests: owner_id/my_permission on
>   create, single GET for view/check/edit, grid list, update, public read level-capping,
>   public join, owner-joins-own-link, and the public-config flags+values). The
>   invite-mode accept case is asserted in `tests/tests_sharing_invites.py` instead —
>   that's the module the invite-flow test pass boots with
>   `SHARING_REQUIRE_INVITE_ACCEPT=1`. Default pass + invite pass both green.

### P0.1 — Expose ownership + the caller's effective permission on the card read model ⚠️ REQUIRED — ✅ DONE
`CheckListApiWithSubObj` (the `CheckListType` every card in the grid is rendered from) currently
exposes **only** `name, text, color_id, checked_items_seperated, checked_items_collapsed, id,
color, position, labels`. It does **not** include `owner_id`, and there is no field telling the
client what the **current user may do** with the card. Without this the frontend cannot decide
whether to show owner-only controls (manage shares, transfer, public links, delete) vs a
collaborator's "leave list", nor whether to disable check/edit affordances for a `view`/`check`
collaborator.

Add to the card read serialisation (in `routes_checklist.py` GET `/checklist` and
`/checklist/{id}`, and anywhere a `CheckListApiWithSubObj` is returned — accept-invite,
join, etc.):
- `owner_id: uuid.UUID`
- `my_permission: Literal["view","check","edit","owner"]` — derived from the same
  `UserChecklistAccess.permission_level()` the route guards already compute (owner → `"owner"`).
  For the **anonymous public** card read, set this to the public link's level.

This is the single source of truth the whole UI gates on. Everything below assumes it exists.

### P0.2 — Expose the relevant server feature flags ⚠️ REQUIRED (small) — ✅ DONE
The sharing endpoints return **404** when a feature is switched off server-side
(`SHARING_ENABLED`, `SHARING_PUBLIC_LINKS_ENABLED`, `SHARING_USER_SEARCH_ENABLED`,
`SHARING_REQUIRE_INVITE_ACCEPT`). The frontend should hide the corresponding UI rather than
render a button that 404s.

**Shipped:** a tiny **unauthenticated** `GET /api/public-config`
(`api/routes/routes_public_config.py`, mounted in `routers_map.py`, tag `Config`) returning a
`PublicConfig` model with exactly those four booleans, named to mirror the `Config` switches:
```jsonc
{
  "sharing_enabled": true,
  "sharing_public_links_enabled": true,
  "sharing_user_search_enabled": true,
  "sharing_require_invite_accept": false
}
```
No secrets, so it is safe without a session. Frontend: fetch once on app mount and gate the
sharing UI on it (no feature-detection-by-404 needed).

> P0.1 + P0.2 landed together: `CheckCheck/openapi.json` has been regenerated. Run
> `bunx nuxi prepare` in `CheckCheck/frontend` so the open-fetch types pick up the new fields
> and the new endpoint. **Every frontend phase below assumes the schema types are current.**

---

## Cross-cutting foundations (Phase F0) — ✅ DONE

Shared plumbing every feature phase builds on. Land this first.

> **Shipped:** `types/index.ts` extended with every sharing schema alias + the
> four new sync `upd_prop` values. `stores/user.ts` (new) holds `me` + `myId` /
> `isOwnerOf(card)` and `fetchMe()` (`GET /api/user/me`), loaded once in
> `pages/index.vue` `onMounted` alongside `useSync().connect()`.
> `composables/usePermissions.ts` (new) exposes `can(card, "check"|"edit")` and
> `isOwner(card)` over the `view<check<edit<owner` ladder, reading
> `card.my_permission` (defaults to `view` when absent so a missing field never
> unlocks an action). `useSync` now handles `share_added`/`share_removed`
> (re-`refresh(clId)` so `my_permission` re-gates) with `share_invited` /
> `notification` left as documented no-ops until F5/F6 ship their stores.

### F0.1 — Current-user store
There is **no** current-user state today (only `login.vue` calls `/api/auth/list`). Add
`stores/user.ts`:
- `me: User | null`, `fetchMe()` → `GET /api/user/me`, a `isOwnerOf(card)` / id getter.
- Loaded once on app mount (in `pages/index.vue` `onMounted`, alongside `useSync().connect()`).
- Needed by: notification/invite payloads, "is this my card" checks (belt-and-suspenders next to
  `my_permission`), and the share list (don't offer to share with yourself).

### F0.2 — Global types
Extend `types/index.ts` with the new schemas (all already in `openapi.json`):
```ts
type ShareReadType        = components["schemas"]["ShareRead"]
type ShareUpsertType      = components["schemas"]["ShareUpsertRequest"]
type SharePermission      = components["schemas"]["SharePermission"]   // "view"|"check"|"edit"
type ShareStatus          = components["schemas"]["ShareStatus"]        // pending|accepted|declined
type PublicLinkReadType   = components["schemas"]["PublicLinkRead"]
type PublicLinkCreateRes  = components["schemas"]["PublicLinkCreateResult"]  // carries token ONCE
type PublicLinkCreateReq  = components["schemas"]["PublicLinkCreateRequest"]
type PublicLinkUpdateReq  = components["schemas"]["PublicLinkUpdateRequest"]
type GroupShareResult     = components["schemas"]["GroupShareResult"]
type InviteReadType       = components["schemas"]["InviteRead"]
type NotificationReadType = components["schemas"]["NotificationRead"]
type UserSearchResult     = components["schemas"]["UserSearchResult"]
type UserType             = components["schemas"]["User"]
type PublicConfigType     = components["schemas"]["PublicConfig"]   // P0.2 feature flags
```
> `CheckListType` (`CheckListApiWithSubObj`) now also carries `owner_id: string` and
> `my_permission: "view"|"check"|"edit"|"owner"` (P0.1) — these are required fields on
> every card the API returns, so the grid/card components can read them directly.
Extend the hand-maintained sync union — the backend now emits these too:
```ts
type SyncNotificationUpdateProp =
  | "item_state" | "item_text" | "item_position" | "item_created" | "item_deleted"
  | "checklist" | "checklist_position" | "checklist_created" | "checklist_deleted"
  | "checklist_label"
  | "share_added" | "share_removed" | "share_invited" | "notification"   // ← new
```

### F0.3 — Permission helper
A small composable `composables/usePermissions.ts` (or a getter on the checklist store):
- `can(card, "check"|"edit") => boolean` and `isOwner(card)`, reading `card.my_permission`
  against the `view < check < edit < owner` ladder. One place so gating never drifts.
- Use everywhere an affordance must be disabled (Phase F1).

### F0.4 — Extend `useSync` for the new events
In `composables/useSync.ts::handle`, add cases:
- `share_added` / `share_removed` → `checkListStore.refresh(clId)` (the card's `my_permission`,
  and for the owner the share list, may have changed; a collaborator who was just added gets
  `checklist_created` separately, an existing one just re-reads). If a Share modal is open for
  `clId`, refresh its collaborator list too.
- `share_invited` → bump the **invite** store (Phase 4): `inviteStore.refresh()`.
- `notification` → bump the **notification** store (Phase 5): `notificationStore.refreshUnread()`
  (and the list if the dropdown is open).
- `checklist_deleted` is **already** handled and correctly covers revoke / leave / owner-delete
  (the backend pins `checklist_deleted` to the removed user).

---

## Phase F1 — Permission-aware card UI gating — ✅ DONE
**Goal:** a `view`/`check` collaborator (or anonymous viewer) sees the card but cannot perform
actions above their level. Driven entirely by `card.my_permission` (P0.1) via `usePermissions`.

> **Shipped:** item checkbox gated on `check`; item text + add-new + item drag
> handle, card name/notes, and the footer **Color** button gated on `edit` (all
> via `usePermissions`, with the relevant store-write paths also guarded so a
> stale UI can't bypass the disable). Per-user controls (archive, labels,
> MoreOptions/separate-checked, card drag) intentionally left ungated. The Share
> button is untouched (its dialog is permission-scoped in F2).
>
> **Tests:** `tests/e2e/sharing-gating.spec.ts` (new) — admin shares a card with
> `testuser01` via API at `view` then `edit`; asserts the collaborator's editor
> is read-only at `view` (checkbox + textarea disabled, no add-new) and fully
> editable at `edit`. Both pass. Notes for future test authors: Nuxt UI renders
> `<button role="checkbox">` which **`getByRole('checkbox')` does not match** —
> use the CSS selector `[role="checkbox"]`. Also, the card-editor modal opens as
> **two** `[role="dialog"]` roots (a **pre-existing** flake — `checklist.spec` /
> `item-movement.spec` / `sync.spec` hit the same strict-mode clash on a clean
> tree, unrelated to sharing); scope to `[role="dialog"]:has(.checklist)` and
> avoid re-`goto("/")` after login.

Audit and gate (disable + hide-on-no-permission, with a tooltip "read-only"/"view only"):
- **Item checkbox** (`components/CheckListItem.vue`): toggling requires `check`. Disable for `view`.
- **Item text edit / add-new** (`CheckListItem.vue`, `CheckListItemCollection/AddNewButton.vue`):
  require `edit`. Disable text inputs + hide the add button for `view`/`check`.
- **Item delete / drag-reorder**: require `edit`.
- **Card name / text / color edits** (`CheckListEditModal.vue`, footer `Color`): require `edit`.
- **Per-user controls stay enabled at any level** — archive (`Button/Archive.vue`), pin,
  collapse/separate-checked (`MoreOptionsMenu.vue`), labels (the whole `CheckListFooter/Labels/*`),
  and card position/drag in the grid. These are the *viewer's own* layer (backend allows them with
  any access). Do **not** gate these on `edit`.
- **Footer Share button** (`Button/Share.vue`): visible to everyone with access, but the dialog it
  opens is permission-scoped (Phase 2). Owner sees full management; non-owner sees read-only
  collaborators + their own "Leave list".

Acceptance: log in as a `view` collaborator → checkboxes and text are read-only, archive/labels
still work; as `edit` → everything but share-management/transfer works.

---

## Phase F2 — Share-management dialog (backend Phases 3, 4, 10) — ✅ DONE
**Goal:** wire the currently-stubbed `Button/Share.vue` (`console.log("NOT IMPLEMENTED")`) to a
real dialog. Owner-only management; collaborators get a reduced view.

> **Shipped:**
> - **`stores/publicConfig.ts` (new)** — fetches the unauthenticated
>   `GET /api/public-config` **once** (loaded in `pages/index.vue` `onMounted`
>   alongside `fetchMe()`/`connect()`) and exposes `sharingEnabled` /
>   `publicLinksEnabled` / `userSearchEnabled` / `requireInviteAccept` getters
>   (all default `false` until loaded, so no button flashes before the flags
>   arrive).
> - **`stores/share.ts` (new)** — keyed by checklist id: `listShares`,
>   `upsertShare`, `revokeShare` (also self = leave), `transferOwnership`,
>   `searchUsers` (2-char min; callers debounce), `listMyGroups` (cached),
>   `shareWithGroup` (re-reads the list after). Plus `setOpen`/`refreshIfOpen`
>   so `useSync` can live-refresh the open dialog's list on
>   `share_added`/`share_removed`. `types/index.ts` gained
>   `TransferOwnershipResultType`.
> - **`components/ShareModal/*` (new)** — entry `ShareModal.vue` (auto-import
>   name `ShareModal` via Nuxt prefix-dedupe) opened from `Button/Share.vue` via
>   `useOverlay()`. Owner view: `AddPeople` (gated on `userSearchEnabled`),
>   `PeopleList` (editable — level `USelect` + revoke + status badge; synthetic
>   "you (Owner)" row since the backend list excludes the owner). **`GET /shares`
>   is owner-only (403 for collaborators)**, so the modal only calls `listShares`
>   when `my_permission === "owner"` (incl. the `useSync` `refreshIfOpen` guard,
>   and the transfer flow does **not** re-list after demoting the caller). The
>   non-owner view therefore shows a plain "you're a collaborator with X access"
>   notice + Leave list, not a (non-fetchable) collaborator list. This matters
>   because `plugins/api.ts` toasts **every** non-2xx, so a stray owner-only call
>   from a non-owner surfaces a visible "Error 403" toast.
>   `ShareWithGroup` (only when `listMyGroups()` non-empty; toasts the
>   added/skipped/total summary), `PublicLinks` (**F3 stub**, gated on
>   `publicLinksEnabled`), `TransferOwnership` (inline confirm → refreshes the
>   card so `my_permission` flips to `edit` and the dialog swaps to the
>   collaborator view). Non-owner view: read-only `PeopleList` + a prominent
>   **Leave list** button (`revokeShare(clId, myId)` → backend pins
>   `checklist_deleted`, `useSync` drops the card, modal closes).
> - **Feature-gating:** the footer Share button is hidden entirely when
>   `sharing_enabled` is false; the user-search section is hidden when
>   `sharing_user_search_enabled` is false (group share / public links still
>   render).
> - **`useSync`** now also calls `shareStore.refreshIfOpen(clId)` on
>   `share_added`/`share_removed` to live-refresh the open collaborator list.
> - **Tests:** `tests/e2e/sharing-modal.spec.ts` (new, 4 tests, all green) —
>   owner adds + revokes a collaborator through the dialog; owner transfers
>   ownership and is demoted to a collaborator **without an Error-4xx toast**
>   (regression test for the owner-only `GET /shares` 403); non-owner gets the
>   read-only view (no Add-people search) and Leave-list removes the card from
>   their grid. The footer renders on the grid *preview* card, so the dialog is
>   driven straight from the board (no card-editor open → sidesteps the
>   double-`[role=dialog]` flake). Gotcha for future authors: `testuser01` has a
>   `display_name` ("Test User 01"), so collaborator rows render that, **not** the
>   username — filter rows by display name. Also: run the suite with the **local**
>   `@playwright/test` CLI (`bun node_modules/@playwright/test/cli.js test …`);
>   a bare `bunx playwright` can resolve a cached standalone `playwright` package
>   of a *different* version and trip its "two different versions" guard at
>   collection time.

### Store: `stores/share.ts`
Keyed by checklist id. Actions:
- `listShares(clId)` → `GET /api/checklist/{checklist_id}/shares` → `ShareReadType[]`.
- `upsertShare(clId, userId, permission)` → `PUT /api/checklist/{checklist_id}/shares/{user_id}`.
- `revokeShare(clId, userId)` → `DELETE .../shares/{user_id}` (also self = "leave list").
- `transferOwnership(clId, newOwnerId)` → `POST /api/checklist/{checklist_id}/transfer-ownership`.
- `searchUsers(q)` → `GET /api/user/search?q=` (min 2 chars; debounce ~300 ms).
- `listMyGroups()` → `GET /api/user/me/groups` (empty for local users → hide group UI).
- `shareWithGroup(clId, group, permission)` → `PUT .../shares/group/{group}` → `GroupShareResult`
  (toast the `added/skipped/total` summary).

### Component: `components/ShareModal/*` (opened via `useOverlay`)
Sections, gated by `my_permission === "owner"`:
1. **Add people** — a `UInputMenu`/search field hitting `searchUsers`; pick a user + a level
   (`view`/`check`/`edit` segmented control) → `upsertShare`. Never list the current user or the
   owner.
2. **People with access** — list from `listShares`. Each row: name + a level dropdown (changes via
   `upsertShare`) + a revoke (✕) button. Show a `status` badge when `pending`/`declined`
   (invite mode). The owner row is labelled "Owner".
3. **Transfer ownership** — a guarded action (confirm dialog) → `transferOwnership`; on success the
   current user becomes an `edit` collaborator (refresh card → `my_permission` flips to `edit`,
   owner controls disappear).
4. **Share with a group** — only when `listMyGroups()` is non-empty: pick a group + level →
   `shareWithGroup`.
5. **Public links** — Phase 3 (sub-section in the same modal).

**Non-owner view:** read-only "People with access" + a prominent **Leave list** button
(`revokeShare(clId, myId)`); on success the backend pins `checklist_deleted`, so `useSync` already
removes the card from the grid — just close the modal.

Feature-gating: if `SHARING_ENABLED` is false (P0.2), hide the Share button entirely. If
`SHARING_USER_SEARCH_ENABLED` is false, hide the "Add people" search (but still allow group share /
public links). Refresh the share list on the `share_added`/`share_removed` SSE while open.

---

## Phase F3 — Public URL links: owner management (backend Phases 5, 7) — ✅ DONE
**Goal:** owner creates/manages anonymous share links from within the Share modal.
Gated by `SHARING_PUBLIC_LINKS_ENABLED` (hide section when off).

> **Shipped:**
> - **`stores/share.ts` (extended)** — same store as F2 (the modal already uses it),
>   now with a `links: Record<clId, PublicLinkReadType[]>` cache + `linksFor`
>   getter and four owner-only actions keyed by checklist id: `listLinks`
>   (`GET …/public-links`), `createLink` (`POST …/public-links` → returns the
>   `PublicLinkCreateResult` carrying the **one-time token**; the cached copy is
>   stored **token-stripped** via destructuring so a token never lingers in
>   state), `updateLink` (`PATCH …/public-links/{id}` — `enabled` toggle,
>   `permission`/`expires_at`, `password:string` (re)protects / `password:null`
>   clears / `expires_at:null` clears), and `deleteLink` (`DELETE …`). All mirror
>   the established `$checkapi` path/query/body idiom and reconcile the local list
>   in place.
> - **`components/ShareModal/PublicLinks.vue`** — replaced the F3 stub (parent
>   `ShareModal.vue` already renders it gated on `publicConfig.publicLinksEnabled`
>   in the owner branch). A create form (level `USelect`, optional `type="date"`
>   expiry → ISO via `new Date(d).toISOString()`, optional password `UInput`),
>   then on create the **full `${location.origin}/p/<token>` URL is surfaced once**
>   in a read-only field with a copy-to-clipboard button (success-tick feedback;
>   falls back to a "select & copy manually" toast if `navigator.clipboard` is
>   blocked) and a "won't be shown again / viewer route is F4, link won't resolve
>   yet" note. The existing-links list shows the `permission` badge, a 🔒
>   `password_protected` indicator (never the token), the `expires_at`
>   ("Never expires" when null), a `USwitch` enabled toggle, and a delete button —
>   prefixed by the "links are write-only after creation; delete & recreate for a
>   fresh URL" caveat. `listLinks` runs `onMounted` (owner-only section, so no
>   stray 4xx).
> - **In-session token retention (UX follow-up):** the server never returns a
>   token after create, so a listed link's URL is genuinely unrecoverable. To
>   soften the "shown once" cliff, the store keeps a `linkTokens` in-memory map
>   (`tokenFor` getter) populated on create and cleared on delete — so a link
>   created while the app is open stays **copyable from its row** (a per-row copy
>   button), while older/list-loaded links show a muted `i-lucide-link-2-off`
>   indicator instead. Tokens are never persisted (lost on reload — by design).
>   The card layout was also reworked: each sharing method (people / group /
>   public link / transfer) is now a self-contained bordered card with an icon +
>   title + description, making the "use whichever you need" relationship obvious
>   rather than one long form.
> - **Tests:** `tests/e2e/sharing-public-links.spec.ts` (new, 2 tests, green) —
>   owner creates a link from the board-driven dialog, asserts the one-time
>   `/p/<token>` URL appears + clipboard copy round-trips (with a granted
>   clipboard permission), the redacted row shows (and does **not** leak the
>   token), the `USwitch` toggles, and delete clears it; a second test creates a
>   password+expiry link and asserts the 🔒 indicator and a non-"Never" expiry.
>   Both assert **`Error 4xx` toast count is 0** (F2 learning — `plugins/api.ts`
>   toasts every non-2xx). `data-testid` forwards onto the inner `<input>` (so
>   selectors target the testid directly, no ` input` suffix). Restored
>   `CheckCheck/openapi.json` after the run (the e2e server rewrites it on boot).

### Store additions (`stores/share.ts` or a `stores/public_link.ts`)
- `listLinks(clId)` → `GET /api/checklist/{checklist_id}/public-links` → `PublicLinkReadType[]`
  (tokens are **redacted** here — by design).
- `createLink(clId, {permission, expires_at?, password?})` →
  `POST .../public-links` → `PublicLinkCreateResult`. **The `token` is returned exactly once.**
- `updateLink(clId, linkId, patch)` → `PATCH .../public-links/{link_id}` (toggle `enabled`, change
  `permission`/`expires_at`; `password: string` to (re)protect, `password: null` to clear).
- `deleteLink(clId, linkId)` → `DELETE .../public-links/{link_id}`.

### UI (`components/ShareModal/PublicLinks.vue`)
- "Create link" form: level (`view`/`check`/`edit`), optional expiry (date picker → ISO string —
  backend normalises tz to naive UTC), optional password.
- On create, surface the full shareable URL **once** with a copy-to-clipboard button:
  `https://<host>/p/<token>` (the public viewer route, Phase 4). Make clear it won't be shown again.
- List existing links: show `permission`, `enabled` toggle, `expires_at`, a
  `password_protected` 🔒 indicator (never the token), and delete. Because the token is redacted
  on list, the only place to copy the URL is right after create — note this in the UI ("links are
  write-only after creation; delete & recreate to get a fresh URL").

---

## Phase F4 — Public/anonymous viewer page (backend Phases 5, 6, 7) — ✅ DONE
**Goal:** a logged-out visitor opens `/p/<token>` and sees the card at the link's level, with live
updates, optional passphrase unlock, and a "log in to add to my deck" join.

> **Shipped:**
> - **Design decision (noted up front, per the plan):** the authed
>   `CheckList`/`CheckListItem` components are tightly coupled to the
>   session-backed Pinia stores (`checklist` / `checklist_item` / `useSync`) and
>   call the authed `/api/checklist/...` endpoints directly. Rather than shoehorn
>   anonymous data through those stores, the viewer owns a **dedicated, slim data
>   source** — `composables/usePublicCard.ts` (new) — that holds the card + items
>   in local refs, talks only to the token-authed `/api/public/checklist/{token}/…`
>   surface, gates writes through the **same** `usePermissions` ladder over
>   `card.my_permission` (the link's level, P0.1), and drives its own anonymous
>   SSE. It's instantiated **once per page mount** (not a shared composable) so its
>   EventSource + state are scoped to the single open card. Item rendering is a
>   small standalone `components/PublicChecklistItem.vue` (new) — checkbox gated on
>   `check`, text-edit/delete gated on `edit` — instead of the board's components.
> - **`plugins/api.ts` (modified) — the critical fix.** The global handler toasts
>   every non-2xx AND redirects to `/login` on 401, and runs **before** any
>   per-call `onResponseError` (so a call-site override can't suppress it). Added
>   `isPublicShareRequest(ctx)` (URL contains `/api/public/`) and guarded BOTH the
>   `onResponse` 401-redirect and the `onResponseError` toast with it. So a 404
>   (bad/expired/disabled **or** password-protected link) and a 401 (join while
>   logged out) are now silent branches the viewer page owns — no "Error 4xx"
>   toast, no `/login` bounce on initial load.
> - **`pages/p/[token].vue` (new)** — standalone (default layout, no board/sidebar
>   chrome; just a Logo + `ColorModeSwitch` header). `onMounted` → `load()` does
>   `GET /api/public/checklist/{token}`: **200** renders the card + items with the
>   permission ladder (view = read-only, check = tick state, edit = add/edit/delete);
>   **404** drops to a passphrase form (`UInput type=password`) → `POST …/unlock`
>   → the short-lived `grant` is stored in memory **and** `sessionStorage` (keyed
>   by token; the passphrase itself NEVER touches the URL) and replayed on every
>   subsequent call via `?share_grant=` → retry the load; a wrong passphrase comes
>   back as the same 404 → "Incorrect passphrase." A separate **gone** state
>   covers a non-404 failure / live `checklist_deleted`.
> - **Live updates:** `usePublicCard` opens an anonymous
>   `EventSource("/api/sync?token=<token>[&share_grant=…]")` scoped to this one
>   card — item-level events debounce-reload the item list, `checklist`/`label`
>   events re-read the card, `checklist_deleted` flips to the gone state. **Closed
>   on unmount** (`disconnectSync`) so the live SSE doesn't block Playwright
>   teardown.
> - **Join / "add to my deck":** a footer button → `POST …/join` (grant replayed).
>   **401** (logged out) → toast + `navigateTo('/login?redirect=/p/<token>')` from
>   the page (the global redirect is suppressed for `/api/public` now); **200**
>   (logged in) → real collaborator added (`share_added` fans out) → navigate to
>   `/card/<id>` in the main app.
> - **`types/index.ts`** gained `UnlockRequestType` / `UnlockResultType` aliases
>   (the rest already existed); re-ran `bunx nuxi prepare`. Typecheck clean except
>   the 2 pre-existing errors (`nuxt.config.ts:77`, `stores/color.ts:29`).
> - **Tests:** `tests/e2e/public-viewer.spec.ts` (new, 7 tests, all green) — view
>   renders read-only (checkbox disabled, no editable text/add), check ticks an
>   item, edit adds an item, a **bad token shows the locked branch with ZERO
>   `Error 4xx` toasts and NO `/login` bounce** (URL stays on `/p/…`), a
>   password-protected link rejects the wrong passphrase and unlocks with the
>   right one, an item the owner adds via the authed API appears live on the open
>   anon viewer (SSE), and a logged-in join navigates to `/card/<id>`. The
>   anonymous viewer runs in a fresh `browser.newContext()` with **no
>   storageState**; the join test reuses the testuser01 login. Every context's
>   pages are navigated to `about:blank` in `afterEach` (live SSE blocks
>   teardown). Restored `CheckCheck/openapi.json` after the run (the e2e server
>   rewrites it on boot). `Error 4xx`-count-is-0 asserted on every expected path
>   (the F2/F3 lesson).

### Anonymous API access
The authed `$checkapi` client assumes a session. For the public surface, either:
- (a) call the `/api/public/checklist/{token}/...` endpoints with the **same** `$checkapi`
  (they don't need a session — they auth by token in the path), passing the grant via
  `query: { share_grant }` / `headers: { 'x-share-grant' }` when the link is protected; **or**
- (b) a thin dedicated client.
Prefer (a) — the open-fetch types already cover these paths. Do **not** let `plugins/api.ts`'s
401→`/login` redirect fire on the public page (a logged-out 401 from `/join` should prompt login
inline, not bounce the anonymous viewer). Add a per-call `onResponseError` override on the public
calls, or guard the redirect when `route.path` starts with `/p/`.

### Route + page: `pages/p/[token].vue`
- On mount: `GET /api/public/checklist/{token}` →
  - **200** → render the card (read-only/check/edit per `my_permission` from P0.1, reusing
    `usePermissions` + the same `CheckList`/`CheckListItem` components as the grid, but standalone —
    no sidebar/board chrome). Items via `GET /public/checklist/{token}/item`; check via
    `PATCH .../item/{id}/state`; edit via `POST/PATCH/DELETE .../item[...]`.
  - **404** → either the link is bad/expired/disabled **or** it's password-protected. Show a
    passphrase form (can't distinguish — backend deliberately returns the same 404). On submit:
    `POST /public/checklist/{token}/unlock {password}` → `{grant, expires_in}`. Store the grant in
    memory (or `sessionStorage`) and replay it on every subsequent public call; retry the load.
    Wrong passphrase → same 404 → "incorrect passphrase" message.
- **Live updates:** open an anonymous SSE: `new EventSource("/api/sync?token=<token>" + grant)`
  (the `/api/sync` endpoint accepts `token` + `share_grant` query params — see openapi). Reuse the
  `useSync` dispatch logic, but scoped to this single card. (Consider parameterising `useSync` to
  accept an optional `{token, grant}` so the same handler serves both; or a slimmer
  `usePublicSync`.)
- **Join / "add to my deck":** a button → `POST /api/public/checklist/{token}/join` (passing the
  grant if protected). If logged out → **401**: route to `/login?redirect=/p/<token>` with a
  message "log in to add this card". If logged in → the card is added as a real collaborator
  (`share_added` fans out); navigate to `/card/<id>` in the main app.

Security/UX notes: never put the **passphrase** in the URL (only the short-lived grant travels in
the query, mirroring the backend's design); the token itself is the capability — treat the page as
public. Anonymous edits emit sync so the owner sees them live.

---

## Phase F5 — Notifications feed (backend Phase 9) — 📋 PLANNED
**Goal:** a navbar bell with an unread badge and a dropdown feed (card shared / invited / public
link opened).

### Store: `stores/notification.ts`
- `unreadCount`, `items: NotificationReadType[]`.
- `refreshUnread()` → `GET /api/user/me/notifications/unread-count`.
- `list({unread_only?, limit?})` → `GET /api/user/me/notifications`.
- `markRead(id)` → `POST /api/user/me/notifications/{id}/read`.
- `markAllRead()` → `POST /api/user/me/notifications/read-all`.
- On the `notification` SSE event (F0.4) → `refreshUnread()` (+ `list()` if the dropdown is open).

### UI (`components/NotificationBell.vue` in `Navbar.vue`)
- Bell icon + `UChip`/badge with `unreadCount`. Dropdown (`UPopover`/`UDropdownMenu`) lists items
  newest-first; each row renders from `payload` (`actor_display_name` / `checklist_name`) and the
  `type` (`card_shared` / `card_invited` / `public_link_opened`). Clicking a card-related
  notification marks it read and opens `/card/<cl_id>`. "Mark all read" action.
- Load `refreshUnread()` on mount. Hide the bell if `SHARING_ENABLED` is off (no notifications are
  ever produced).

---

## Phase F6 — Invite inbox (backend Phase 8) — 📋 PLANNED
**Goal:** when the server runs in invite mode (`SHARING_REQUIRE_INVITE_ACCEPT`), a user can
accept/decline cards shared with them. (When off, the inbox is always empty — harmless.)

### Store: `stores/invite.ts`
- `pending: InviteReadType[]`, `refresh()` → `GET /api/user/me/invites`.
- `accept(clId)` → `POST /api/checklist/{checklist_id}/invites/accept` → returns the card
  (`CheckListApiWithSubObj`); push it into the checklist store + fetch its item preview so it
  appears in the grid, then drop it from `pending`.
- `decline(clId)` → `POST /api/checklist/{checklist_id}/invites/decline`; drop from `pending`.
- On the `share_invited` SSE event (F0.4) → `refresh()`.

### UI
- Surface pending invites either in the same notification bell (a distinct section with
  Accept/Decline buttons) or a dedicated inbox entry in `SideMenuNav.vue`. Recommended: an
  **Invites** section at the top of the notification dropdown (accept/decline inline), since
  `card_invited` already produces a notification — keep one surface.
- Show a count. Accept → card animates into the grid; Decline → row removed (owner sees `declined`
  in their share list).
- Feature-gate: only fetch/show when invites are possible (P0.2 flag, or just always call — an
  empty list is cheap and correct when the flag is off).

---

## Phase F7 — Polish & E2E — 📋 PLANNED
- **Empty/disabled states:** every section degrades gracefully when its server flag is off (P0.2) —
  no dead buttons.
- **Error toasts:** reuse the central `plugins/api.ts` handler; add targeted messages for the
  share flows (e.g. "You can only share with groups you belong to" on a 403 from group-share).
- **Optimistic vs refetch:** follow the existing store idiom (mutate local array, reconcile via
  the returned object / SSE). Share/permission changes should re-read `my_permission` so the UI
  re-gates immediately.
- **Playwright E2E** (extend `CheckCheck/frontend/tests`, see `E2E_TESTING.md`):
  - Owner shares a card with a second user → it appears in their grid; level enforcement (view
    can't check, check can't edit) in the UI.
  - Revoke / leave list removes the card live (SSE) in both tabs.
  - Public link: create → open `/p/<token>` in an anonymous context → view/check/edit per level;
    password unlock flow; join-while-logged-in adds the card.
  - Notification bell shows a count when a card is shared; mark-read clears it.
  - (Invite mode is a server-flag pass — mirror the backend's "second pytest pass" approach: an
    E2E project/run booted with `SHARING_REQUIRE_INVITE_ACCEPT=1` exercising accept/decline.)

---

## Suggested build order
F0 (foundations + the two P0 backend prereqs) → **F1** (gating, immediately visible value) →
**F2** (share dialog — the core feature) → **F3** (public-link management) → **F4** (public viewer
page — the largest, most isolated piece) → **F5** (notifications) → **F6** (invites) → **F7**
(polish + E2E). F5/F6 can swap; F4 can be deferred if user-to-user sharing is the priority.

## File map (new / touched)
| Area | Files |
|---|---|
| Backend prereq ✅ | `model/checklist.py` (+ fields), `api/access.py` (`attach_my_permission`), `db/checklist_collaborator.py` (`permissions_for_user_by_checklist`), routes `routes_checklist.py` / `routes_checklist_share.py` / `routes_checklist_public.py` (attach call); `routes_public_config.py` (new `GET /public-config`) + `routers_map.py`; tests `tests/tests_sharing_prereqs.py` (+ `tests_sharing_invites.py`); regenerated `openapi.json` |
| Foundations | `types/index.ts` (extend), `stores/user.ts` (new), `composables/usePermissions.ts` (new), `composables/useSync.ts` (extend) |
| Gating | `CheckListItem.vue`, `CheckListItemCollection/AddNewButton.vue`, `CheckListEditModal.vue`, footer buttons |
| Share dialog ✅ | `stores/share.ts` (new), `stores/publicConfig.ts` (new), `components/ShareModal/*` (new), `components/CheckListFooter/Button/Share.vue` (wire up), `composables/useSync.ts` (refreshIfOpen), `pages/index.vue` (load config), `types/index.ts` (TransferOwnershipResultType); tests `tests/e2e/sharing-modal.spec.ts` |
| Public links ✅ | `components/ShareModal/PublicLinks.vue` (fleshed out from stub), `stores/share.ts` (links cache + `linksFor`/`listLinks`/`createLink`/`updateLink`/`deleteLink`); tests `tests/e2e/sharing-public-links.spec.ts` |
| Public viewer ✅ | `pages/p/[token].vue` (new), `composables/usePublicCard.ts` (new — public data source + anonymous SSE + write actions), `components/PublicChecklistItem.vue` (new — standalone item row), `plugins/api.ts` (skip toast + 401→/login redirect for `/api/public/` requests), `types/index.ts` (`UnlockRequestType`/`UnlockResultType`); tests `tests/e2e/public-viewer.spec.ts` |
| Notifications | `stores/notification.ts` (new), `components/NotificationBell.vue` (new), `Navbar.vue` (mount it) |
| Invites | `stores/invite.ts` (new), invite UI in the bell / `SideMenuNav.vue` |
| Tests | `CheckCheck/frontend/tests/*` (Playwright) |

---

## Possible changes later (not committed — revisit before/while building F4+)

Ideas surfaced during F2/F3 that are **not** part of the agreed plan yet. They're parked here so we
don't forget them and don't churn the committed phases. None of these block current work.

### 1. Make the global API error handling opt-out per call (frontend — recommended before F4)
**Smell:** `plugins/api.ts` runs *globally* on every response — it toasts **every** non-2xx and
redirects to `/login` on **every** 401, and the global handler runs **before** any per-call
`onResponseError`, so a call site can't suppress it. This assumes "any 4xx = unexpected error."
The sharing features break that assumption:
- F2 had to **avoid** calling owner-only endpoints (`GET /shares` 403) purely to dodge a stray
  "Error 403" toast.
- F4's viewer has flows where a 4xx is a **normal, handled branch**: a `404` on
  `GET /api/public/checklist/{token}` means "bad/expired/disabled **or** password-protected" (the
  passphrase form), and a `401` on `…/join` means "log in first" — neither should toast, and the
  logged-out visitor must **not** be bounced to `/login` on initial load.

**Proposed change (frontend-only, no backend/plan impact):** make the toast + 401-redirect
**opt-out** per request — e.g. a `skipErrorToast` / `skipAuthRedirect` fetch option the public
calls pass, or path-based suppression for `/api/public/…`. Do it as a small prerequisite
(call it **F3.5**) so F4 doesn't hack around the plugin from inside `pages/p/[token].vue`. Pays back
across F4/F5/F6. **Acceptance:** opening a bad/locked token shows the viewer's own state with **zero**
"Error 4xx" toasts and **no** `/login` navigation.

### 2. Distinguish "needs password" from "gone" in the public viewer (backend tradeoff — lean NO)
**Observation:** the backend deliberately returns the **same 404** for a bad/expired/disabled link
and a password-protected one (so there's no oracle confirming a token exists or is merely locked).
That makes the F4 UX slightly clumsy — we show a generic "not found / enter passphrase" because we
can't tell the two apart.
**Option:** have the backend signal "needs password" distinctly (e.g. `401` with a marker) vs `404`
"gone", so the viewer only shows the passphrase form when it's actually warranted.
**Tradeoff:** this reintroduces exactly the existence/password oracle the current design hides.
**Recommendation: keep the identical-404 as-is** unless we decide viewer polish outweighs that
hardening. Listed only so the choice is conscious, not accidental.

### 3. "Regenerate link URL" without losing the link (backend feature gap — low priority)
**Observation:** because a token is shown once and never re-emitted (correct capability hygiene),
the only way to get a fresh URL today is **delete + recreate**, which loses the link's identity and
settings (level/expiry/password). F3 softens this client-side by keeping created tokens copyable for
the current session, but they're gone after a reload (by design).
**Option:** a backend `POST …/public-links/{id}/rotate` that mints a new token in place (invalidating
the old URL) and returns it once, preserving the link row + settings.
**Status:** functionally covered by delete+recreate; only worth doing if users ask for URL rotation
that preserves settings. Not planned.
</content>
