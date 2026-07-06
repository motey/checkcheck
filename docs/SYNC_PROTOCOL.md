# CheckCheck Sync Protocol (2.0)

**Status:** Active — the client-facing contract the local-first frontend (WI-6…11)
is built against.
**Consolidates:** decisions made in WI-1…WI-4 (see
[VERSION_2.0_WORK_ITEMS.md](plans/VERSION_2.0_WORK_ITEMS.md) *“Decisions taken
in-session”* blocks). This document invents nothing new; it is the single place a
client author needs to read.

---

## 1. Model

CheckCheck syncs with a **DIY delta-sync** design (no CRDT, no client SQLite):

- The **server is authoritative.** All writes go through the existing
  permission-checked REST endpoints. There is no separate “sync upload” API.
- Each device keeps a **cursor** — a single integer (`server_seq`) — and pulls
  everything that changed since that cursor from one endpoint,
  **`GET /api/changes`**.
- The existing **SSE stream (`GET /api/sync`)** is a *poke*: it tells a connected
  device “there are changes, pull now”. It is never the source of truth and may be
  missed (offline, reconnect) without data loss — the next pull catches up.
- Conflict resolution is **per-field Last-Writer-Wins**, where “last” =
  **server-arrival order** (§4). There is no text CRDT in 2.0.

A client therefore needs three things: a persisted **cursor**, a persisted
**outbox** of pending writes, and the local **store snapshot**. The server keeps
**no per-client state** — the cursor is entirely client-owned.

---

## 2. The cursor (`server_seq`)

- `server_seq` is a **global, strictly-monotonic integer** handed out by a
  single-row counter table (`sync_seq`). It is stamped on **every** insert and
  update of a syncable row (checklist, item, state, positions, label, link rows)
  by mapper events — no CRUD or bulk path can bypass it. A soft-delete (tombstone)
  bumps it too, so deletes surface.
- Committed `server_seq` values are **monotonic in commit order**: a reader that
  has consumed up to `N` can never later miss a row that commits with a seq `< N`.
  (The allocator holds the counter row lock until commit, serialising the commit
  tail of writes. Acceptable at this app’s scale.)
- The cursor is **opaque to the client** beyond “bigger = newer”. Store the
  `next_cursor` you get back; send it as `since` next time. Start a fresh device
  at `since=0`.
- The sequence is **never reset or garbage-collected**, so a normal cursor is
  never “too old”. The only `full_resync` trigger is a cursor that is *ahead* of
  the server (§5).

---

## 3. `GET /api/changes` — the delta feed

### Request

```
GET /api/changes?since=<cursor>&known=<id>,<id>,...
Authorization: Bearer <token>        (or session cookie)
```

| param   | type            | meaning |
|---------|-----------------|---------|
| `since` | int (default 0) | The device’s cursor. `0` = full bootstrap (§6). |
| `known` | csv of uuids    | Optional. The checklist ids the client currently has cached. Used **only** to compute `removed_checklist_ids` (access revocations, §7). Omit on the first pull. Unparseable ids are skipped, not rejected. |

### Response (`ChangesResponse`)

```jsonc
{
  "next_cursor": 1234,          // persist this; send as `since` next time
  "full_resync": false,         // if true: drop your cache, treat this as bootstrap

  // Changed rows, flat per entity, in the SAME shapes the REST endpoints return
  // (nested position/state/labels, and `my_permission` on each checklist).
  "checklists": [ /* CheckListApiWithSubObj */ ],
  "items":      [ /* CheckListItemRead */ ],
  "labels":     [ /* LabelReadAPI */ ],

  // Removals, as id lists:
  "checklist_tombstones": ["…"],   // cards soft-deleted since `since`
  "item_tombstones":      ["…"],   // items soft-deleted since `since`
  "label_tombstones":     ["…"],   // labels soft-deleted since `since`
  "removed_checklist_ids":["…"]    // cards you LOST ACCESS to (revoked share) — see §7
}
```

### Applying a delta (client algorithm)

1. If `full_resync` is true → **drop the entire local cache** and treat the rest of
   this response as a fresh bootstrap.
2. Upsert every row in `checklists` / `items` / `labels` into the matching store by
   `id` (LWW — the server row always wins; see §4 for the focused-edit caveat).
3. Delete every id in the three `*_tombstones` lists **and** in
   `removed_checklist_ids` from the stores.
4. Persist `next_cursor`.

Application is **idempotent**: re-applying the same delta yields the same state, so
delivering a row twice is harmless.

### Grouping / emission rules

- A checklist is emitted at **card level** when its own row, *this user’s* position
  for it, or *this user’s* label set for it changed. Item edits surface
  **independently** — an item change does **not** re-emit its parent card.
- Nested `labels` on a checklist are the **caller’s own** per-user set;
  `my_permission` is the caller’s effective permission.

### Delivery guarantee: at-least-once, never at-most-once

`next_cursor` is the server high-water mark read **before** the entity queries run.
A row that commits *mid-pull* is therefore delivered in this response **and**
re-delivered on the next one — never skipped. Clients must tolerate duplicates
(step 2 above already does).

### Pagination

There is **no server-side pagination**: a pull returns the whole delta in one
response. Deliberately deferred — account sizes are bounded (Postgres is the
production target at modest scale; SQLite is dev-only). `next_cursor` is exposed
and a client should walk it to empty, but a single healthy pull is expected to
converge. Revisit only if a real account needs page limiting.

---

## 4. Conflict resolution — LWW by server-arrival order

- Every write stamps `updated_at` (naive UTC) and a fresh `server_seq`
  **server-side**. **Client clocks are never trusted for ordering.**
- The **last upload to arrive at the server wins**, per field. Two devices editing
  different fields of the same row both survive (each write re-stamps the row); two
  devices editing the *same* field → the later arrival wins and the earlier value is
  gone.
- **Delete beats edit:** a write to a tombstoned row is rejected (410, §8); the
  tombstone wins. WI-11 surfaces this to the losing editor.
- **Focused-edit protection (client duty):** when applying a remote `item.text`
  change to a field the local user is actively editing, the client must not clobber
  the in-progress edit (the legacy path already guards focused fields; the
  local-first path must preserve this). This is a UX guard, not a change to the LWW
  rule — on blur/submit the local edit becomes a normal write and LWW applies.

---

## 5. `full_resync`

`full_resync: true` is returned **only** when `since` is unusable:

- `since < 0`, or
- `since > current server high-water mark` — i.e. the client is *ahead* of the
  server, which means the server DB was reset/restored (there is no per-client
  state to consult).

The server then computes the response **as if `since=0`** (full accessible state)
and flags it. The client must **drop its cache** and rebuild from the response.

A cursor that is merely old (small) is **not** a resync trigger — the sequence is
never GC’d, so old cursors always resolve to a normal delta.

---

## 6. Bootstrap (new device / first load)

Bootstrap is **not a separate endpoint** — it is `GET /api/changes?since=0`:

- Returns the caller’s **entire accessible state** (owned + accepted-collaborator
  cards, their items, and the caller’s labels) with `full_resync: false` and a
  fresh `next_cursor`.
- Omit `known` on the first pull (nothing is cached yet, so there is nothing to
  report as removed).
- The client persists the rows and the `next_cursor`, then switches to incremental
  pulls.

Cost: one response, no pagination (§3). This is acceptable for the largest realistic
account at this app’s scale; if that ever changes, add page limiting behind
`next_cursor` without changing this contract.

---

## 7. Access changes

### Access gained (a card is shared to you)

When a user gains access to a checklist, the **whole tree predates the grant** (its
rows carry a lower `server_seq` than the collaborator row). The feed handles this by
keying delivery off the caller’s **accepted collaborator row’s** `server_seq`: when
that is `> since`, the card **and all its live items** are shipped in full,
regardless of their own seq. No client action needed beyond normal delta
application. (A later permission change re-delivers the tree — minor, documented
waste.)

### Access lost (a share is revoked)

Collaborator revoke is a **hard delete** (WI-2), so there is no tombstone/seq signal
for it. Because the server is stateless, the client must tell the server what it
still holds:

- The client passes its cached checklist ids as `known=<id>,<id>,…`.
- The server returns `removed_checklist_ids = known − currently-accessible −
  already-tombstoned`.
- The client removes those cards from its stores.

Online clients also learn of a revoke **immediately** via the SSE poke (the revoke
emits `share_removed` / `checklist_deleted` to the removed user, §9); the `known`
diff is the **offline catch-up** path for a device that was away during the revoke.

---

## 8. Error semantics — terminal vs retryable

This is the outbox’s drop-vs-retry contract (enforced by WI-2/WI-3, consumed by the
WI-7 outbox).

| HTTP | when | outbox action |
|------|------|---------------|
| `409 Conflict` | client-supplied create id collides with a **different** existing row | **terminal** — drop the op |
| `410 Gone` | write (or create-replay) targets a **tombstoned** row | **terminal** — drop; the delete won (WI-11 notifies) |
| `403 Forbidden` | access was revoked / insufficient permission | **terminal** — drop; surface “you lost access” |
| `404 Not Found` | row never existed | **terminal** — drop |
| network / `5xx` | transport or server error | **retryable** — keep the op, back off, retry |

Terminal errors must be distinguishable from retryable ones so a dropped op is a
deliberate, surfaced event (WI-11), never a silent loss.

### Idempotent writes (safe replay)

- **Create** accepts an optional client-supplied UUID `id`. Replaying the same
  create returns the **existing row** (200, no duplicate). A create replay against a
  tombstoned id is `410` (never resurrects); against a *different* owner’s/card’s row
  it is `409`.
- **PATCH/PUT** are replay-safe by construction (field-level LWW: applying the same
  op twice yields the same row, only `updated_at`/`server_seq` re-stamp).
- **Re-issuing a delete** is idempotent success (not `410`) — a safe outbox replay.

Because every op carries a client-generated id and every endpoint is replay-safe,
the outbox can retry freely without duplicating or corrupting state.

---

## 9. SSE poke — `GET /api/sync`

The SSE stream is a **notification channel, not a data channel**. Two kinds of
message flow over it; both are `data: {json}\n\n` SSE frames.

### 9a. Legacy per-entity events (unchanged)

Each board mutation emits a targeted `SyncNotification`:

```jsonc
{ "cl_id": "…", "cli_id": "…"|null, "upd_prop": "item_text", "timestamp": 172… }
```

`upd_prop` ∈ `item_state | item_text | item_position | item_created | item_deleted
| checklist | checklist_position | checklist_created | checklist_deleted |
checklist_label | share_added | share_invited | share_removed | notification`.

The **legacy (flag-off) frontend** switches on `upd_prop` to do a targeted refetch.
These payloads are **frozen** — the local-first work does not change or remove them
while `localFirst` is flag-off (until WI-15).

Routing: an event reaches a connected client if its principal (logged-in `user.id`
or anonymous public-link `token`) is in the event’s target set. Server-side routing
fields (`target_user_ids`, `target_tokens`) are **never** shipped to clients.

### 9b. `changes_available` poke (WI-5) — the local-first signal

Alongside **every board-mutating** per-entity event (i.e. all of the above **except**
`notification`), the server emits an **additional** lightweight message to the same
recipients:

```jsonc
{ "cl_id": "…", "cli_id": null, "upd_prop": "changes_available",
  "server_seq": 1234, "timestamp": 172… }
```

- `server_seq` is the server’s global high-water mark at emit time.
- The **local-first (flag-on) client** subscribes to **only** this event as its
  single “pull `GET /api/changes`” trigger. It may **skip** the pull when
  `server_seq <= its stored cursor` (already caught up).
- The **legacy client ignores it** (unknown `upd_prop` → no-op branch), so both
  paths coexist during the transition.

The poke is a *hint*, not a guarantee — a client that never sees it (was offline)
still converges via a normal pull on reconnect. On SSE **reconnect** a client should
always pull once, since events emitted during the gap were missed.

---

## 10. What a client must implement (summary)

1. Persist a **cursor** (`next_cursor`); pull `GET /api/changes?since=<cursor>` on
   boot, on `changes_available` poke (when `server_seq >` cursor), and on SSE
   reconnect.
2. On each delta: handle `full_resync`, upsert rows, delete tombstones +
   `removed_checklist_ids`, persist the new cursor (§3).
3. Send cached ids as `known=` so revocations are reported (§7).
4. Make all writes through the REST endpoints with **client-generated UUIDs**,
   queued in a persisted **outbox**; drain sequentially, retry on network/5xx, drop
   on 403/404/409/410 and surface it (§8).
5. Preserve the **focused-edit** guard when applying remote text (§4).

---

## Deferred (not in this contract yet)

- Server-side pagination of `/api/changes` (§3).
- Tombstone garbage collection (tombstones accumulate; revisit with a GC job).
- Text CRDT for `item.text` (LWW + conflict toast for now; WI-11).
