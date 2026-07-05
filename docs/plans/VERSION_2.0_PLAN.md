# CheckCheck 2.0 — Offline-First, Multi-Platform Plan

**Status:** Draft / discussion
**Date:** 2026-06-23
**Goal:** Evolve CheckCheck into an offline-capable app that runs as a web/PWA,
a desktop app (Tauri), and — in a second step — a mobile app (Capacitor), from
**one codebase**, using a **Postgres-backed sync engine**.

---

## 0. Verdict — Do We Need a Rewrite?

**No full rewrite. Keep the skin, rewrite the spine.**

| Layer | 2.0 fate | Why |
|---|---|---|
| Vue/Nuxt components, Nuxt UI, Tailwind, pages, layouts | **Keep** (~80–90%) | Presentation is platform-agnostic and already good |
| Pinia stores' *public API* (actions/getters used by components) | **Keep the shape** | Components call `store.update(...)`, `store.create(...)` — keep those signatures |
| Pinia stores' *implementation* (the `$checkapi` REST calls inside each action) | **Rewrite** | This is the server-authoritative spine that blocks offline |
| `useSync.ts` SSE "refetch on ping" | **Replace** | Becomes a real change feed / sync engine subscription |
| Backend entities & business logic | **Keep, extend** | Add versioning + a sync/change-feed surface; auth & permissions stay |

The single biggest risk is **not** the frontend. It is **conflict resolution and
the backend sync surface** once two people (or one person on two devices) can
edit while offline. Your existing collaboration features (shares, invites,
public tokens) make conflicts real, not theoretical.

### Two favourable facts we already have
1. **`ssr: false`** — the frontend is already a SPA. Tauri/Capacitor wrap a SPA
   trivially. No SSR to unwind.
2. **Fractional indexing** (`position.index` via `decimal.js`) — ordering is
   already mergeable; concurrent reorders mostly converge without a server
   referee. This is exactly what you want for offline.

---

## 1. "Are offline-capable and mobile on the same path?"

**Mostly yes — if we choose to make them so.** They are different *kinds* of
problem, but a single architecture can serve both:

- **Offline-capable** = an *architecture* problem: local persistence + a sync
  engine + conflict resolution. Identical on every platform.
- **Mobile app** = mostly a *packaging* problem: wrap the same SPA.

The strategy here deliberately puts them on **one path**:

```
                 ┌──────────────────────────────────────────┐
                 │  ONE Nuxt 4 SPA (ssr:false) + local-first │
                 │  data layer (local SQLite mirror)         │
                 └──────────────────────────────────────────┘
                    │              │                 │
              Web / PWA       Tauri (desktop)   Capacitor (mobile)
            service worker   native SQLite      native SQLite
            + IndexedDB/OPFS  + OS integration   + app stores / push
```

The local-first data layer is the work that makes offline *and* desktop *and*
mobile all viable. Do it once.

> Note: Tauri 2 can also target mobile, so Tauri-only is an option. We keep the
> requested split (**Tauri = desktop, Capacitor = mobile**) because Capacitor is
> more mature for app-store distribution, native plugins, and push. Revisit if
> we'd rather maintain one shell.

---

## 2. Current State Assessment (grounded in the codebase)

**Frontend** (`CheckCheck/frontend`)
- Nuxt 4, **SPA mode** (`nuxt.config.ts: ssr: false`), Pinia, Nuxt UI v4,
  Tailwind v4, `nuxt-open-fetch` typed client (`$checkapi`), VueUse.
- Stores: `checklist`, `checklist_item`, `label`, `share`, `invite`,
  `notification`, `color`, `user`, `publicConfig`. **Each action calls
  `$checkapi(...)` directly** and treats the server as authoritative
  (see `stores/checklist_item.ts`).
- Live updates: `composables/useSync.ts` opens an `EventSource('/api/sync')` and,
  on each notification, **refetches** the affected entity. This is a
  notify-then-pull design, not an offline change feed.

**Backend** (`CheckCheck/backend/checkcheckserver`)
- FastAPI, async SQLAlchemy/SQLModel, **Postgres (prod) / SQLite (dev)**, Alembic,
  OIDC auth, `asyncpg`.
- One model + CRUD + route module per entity. Clean seams.
- Entities relevant to sync: `checklist`, `checklist_item`,
  `checklist_item_position` (fractional index), `checklist_item_state` (checked),
  `checklist_label` / `label`, `checklist_collaborator`,
  `checklist_public_share`, `notification`, `sync_notifications`.
- A `SYNC_PLAN.md` already exists for upgrading SSE to Postgres `LISTEN/NOTIFY`.
  **That work is complementary** — it improves real-time fan-out and is a
  stepping stone to the change feed 2.0 needs.

**Implication:** the seams are good. Stores are per-entity and components depend
on store *methods*, not on `$checkapi`. We can slide a local-first repository
*under* the stores without touching components.

---

## 3. Target Architecture — Local-First

```
 Component (unchanged)
     │  store.update(itemId, {...})        ← signature unchanged
     ▼
 Pinia store (thin)                         ← reads/writes LOCAL db, optimistic
     │
     ▼
 Local data layer  ──────────────►  Local SQLite (Tauri/Capacitor)
   - read: reactive queries          or wa-sqlite/OPFS + IndexedDB (web)
   - write: mutate local + enqueue
     │                       ▲
     │ outbox (op queue)     │ change stream (server → client)
     ▼                       │
 Sync engine  ◄──────────────┘
     │  upload ops            download changes
     ▼
 FastAPI write endpoints  ◄──►  Postgres  ──(logical replication / change feed)──┐
   (existing CRUD + perms)                                                       │
     └───────────────────────────────────────────────────────────────────────────┘
```

**Principles**
1. **Local DB is the source of truth for the UI.** Reads are instant and offline.
2. **Writes are optimistic** — mutate locally, enqueue an op in an outbox.
3. **The outbox uploads through your existing FastAPI endpoints**, so your
   permission/collaboration logic stays exactly where it is (server-side).
4. **The server streams authoritative changes back** to all a user's devices.
5. **Conflicts resolve by documented per-field rules** (Section 5), not ad-hoc.

---

## 4. Sync Engine Decision

You said "sync engine via Postgres" — agreed. The realistic options, and a
recommendation:

### Option A — PowerSync *(recommended)*
- Postgres-native; ships a **client SQLite** that works in browser (OPFS),
  Tauri, and Capacitor — one data layer across all three targets.
- Model: client writes to local SQLite → PowerSync **uploads queued mutations to
  *your* backend** (an "upload connector" that calls your FastAPI endpoints) →
  your backend writes Postgres with full permission checks → PowerSync streams
  changes back via Postgres logical replication, filtered by **sync rules**.
- **Why it fits us:** the upload path reuses our existing REST write endpoints,
  so our auth/permissions/collaboration logic is preserved verbatim. Sync rules
  express "a user sees checklists they own + are collaborators on" — which maps
  onto the existing access query.
- Cost: run the PowerSync service (self-hostable) next to Postgres; enable
  logical replication; author sync rules.

### Option B — ElectricSQL (lighter, read-sync only)
- Electric streams Postgres → client via HTTP "shapes" (read path). **You DIY
  the write path** (our outbox → existing FastAPI). Less batteries-included for
  the offline write queue, but fewer moving parts on the server and no
  managed service owning writes.

### Option C — Build-your-own on the existing SSE/notification system
- Extend `sync_notifications` into a real **op-log + version vector**; the
  existing `/api/sync` SSE becomes the change feed; we write the client outbox
  and reconciler ourselves. Maximum control and zero new infra, maximum effort,
  and we own all the edge cases.

**Recommendation:** Prototype with **PowerSync (A)** because it gives the
cross-platform client SQLite *and* lets the upload path stay on our existing
permission-checked endpoints. Keep **C** as the fallback if PowerSync's sync
rules can't express our permission model cleanly (spike this early — it's the
make-or-break question). ElectricSQL (B) is the middle ground if we want read
sync managed but writes fully ours.

---

## 5. Conflict Resolution — Per-Entity Strategy

Good news: this domain is **mostly LWW + tombstones**, not full-CRDT territory.
We do **not** need Yjs/Automerge for checklist data.

| Entity / field | Strategy | Notes |
|---|---|---|
| `item.state.checked` | Last-writer-wins (per item) | Idempotent toggle; trivially mergeable |
| `item.text` | LWW per field, with edit-protection | Components already guard focused fields; field-level LWW is acceptable for short item text. Upgrade to a text CRDT only if multi-user simultaneous typing becomes a real need |
| `item.position` | Fractional index, LWW per item | We already use `decimal.js`. Add small jitter to the fractional key to avoid identical-key collisions on concurrent inserts at the same slot |
| item create | Add-wins | Client-generated UUIDs so offline-created items have stable IDs before sync |
| item / checklist delete | Tombstone (soft delete + `deleted_at`) | Needed so a delete propagates and isn't resurrected by a stale edit |
| `checklist` fields (title, color, labels) | LWW per field | |
| `checklist_collaborator` / shares | Set semantics, **server-authoritative** | Permission changes must not be client-decided; resolve on server |
| `checklist_public_share` tokens | Server-authoritative, read-only on client | Anonymous viewer stays online-only / read-only |
| `notification` | Server-authoritative | Generated server-side; client only reads/acks |

**Cross-cutting requirements**
- **Client-generated UUIDs** for all new rows (so offline creates have identity).
- **Soft deletes / tombstones** everywhere syncable (no hard `DELETE`).
- **A monotonic version column** (`updated_at` as naive UTC — matches the
  existing model convention — and/or a logical counter) on every syncable row,
  to drive LWW and change-feed cursors.

---

## 6. Backend Changes

1. **Versioning columns** — add `updated_at` (naive UTC, per memory convention)
   and `deleted_at` tombstones to all syncable tables, via Alembic migrations.
   Ensure server sets these on every write.
2. **Stable IDs** — accept client-supplied UUIDs on create endpoints
   (idempotent upsert by id) so offline-created rows keep their identity.
3. **Change feed** — either:
   - PowerSync path: enable Postgres **logical replication**, author **sync
     rules** scoped to the existing access query (owner + collaborators), or
   - DIY path: extend `sync_notifications` into an append-only op-log with a
     per-user cursor, served over the existing SSE endpoint.
4. **Idempotent / upsert write endpoints** — outbox replays must be safe to
   apply twice (network retries). Make PATCH/PUT/POST idempotent by id+version.
5. **Finish `SYNC_PLAN.md`** (`LISTEN/NOTIFY`) first — it removes the polling
   bottleneck and is the natural substrate for the change feed.
6. **Auth for long-lived/offline clients** — see Section 8.

---

## 7. Frontend Data-Layer Changes

The refactor that unlocks everything, **without touching components**:

1. **Introduce a repository layer** beneath the stores (e.g. `data/repositories/*`
   or a `useEntityRepo`): the local-DB read/write + outbox enqueue.
2. **Thin the stores**: each action (`create`, `update`, `delete`,
   `updateState`, `updatePosition`, …) keeps its **signature**, but its body
   changes from `await $checkapi(...)` to `await repo.mutate(...)` (optimistic
   local write + enqueue). Reactivity comes from local-DB reactive queries.
3. **Replace `useSync.ts`**: instead of "ping → refetch", subscribe to the sync
   engine's change stream and apply deltas into the local DB; the stores become
   reactive views over the local DB.
4. **Local DB driver abstraction** so the same store code runs on:
   - Web: wa-sqlite (OPFS) or the engine's web SQLite, + IndexedDB fallback
   - Tauri: native SQLite
   - Capacitor: native SQLite
5. **Offline UX**: sync status indicator, pending-changes badge, conflict toasts,
   "last synced" timestamp.

Because components depend on store *methods* (`store.update(id, {...})`), step 2
is a swap of internals. That is the heart of "keep the skin, rewrite the spine".

---

## 8. Auth & Offline

- Current auth is **OIDC** (online redirect flow). Offline needs:
  - **Token persistence** (secure storage: Tauri keychain / Capacitor secure
    storage; web → httpOnly cookie or guarded localStorage).
  - **Offline grace**: allow read/write to the local DB while the access token is
    expired; refresh on reconnect; only block the sync upload, not local use.
  - **Refresh-token flow** suited to native apps (PKCE; system browser via
    Tauri/Capacitor OAuth plugins).
- **Public viewer tokens** (`/p/[token]`) stay **online-only, read-only** — no
  offline requirement, keeps that surface simple.

---

## 9. Platform Packaging

| Target | How | Notes |
|---|---|---|
| **Web / PWA** | `nuxt generate` (already SPA) + service worker (`@vite-pwa/nuxt`/Workbox) for app-shell offline; data offline via the sync engine's web SQLite | Installable PWA is the cheapest "mobile-ish" win and validates offline before native |
| **Desktop** | **Tauri 2** wrapping the static SPA; native SQLite; OS integration (tray, autostart, deep links) | Small binaries, good DX |
| **Mobile** | **Capacitor** wrapping the same SPA; native SQLite; app-store packaging; push notifications | Second step; reuses 100% of the web UI |

Build all three from the same `nuxt generate` output. Platform differences are
isolated to the **local DB driver** and a few native plugins (storage, push,
deep-link).

---

## 10. Phased Roadmap

**Phase 0 — De-risk (spike, ~1 week)**
- Spike PowerSync sync rules against the existing owner+collaborator access
  query. **Go/No-Go on Option A vs C.** This is the riskiest unknown.
- Finish `SYNC_PLAN.md` `LISTEN/NOTIFY` upgrade.

**Phase 1 — Backend sync foundations**
- Versioning columns + tombstones + client-UUID upserts + idempotent writes
  (Alembic migrations). Backend stays online-compatible throughout.

**Phase 2 — Frontend repository layer**
- Introduce local DB + repository under the stores; migrate stores from
  `$checkapi` to repo, **one store at a time** (start with `checklist_item`).
- Replace `useSync.ts` with change-stream application.
- Ship behind a flag; app still works online-first while incomplete.

**Phase 3 — Offline web/PWA**
- Service worker app-shell + offline data; sync status UX; conflict handling.
- This is the first user-visible 2.0 milestone and validates the whole spine.

**Phase 4 — Desktop (Tauri)**
- Wrap SPA, native SQLite driver, OIDC via system browser, OS integration.

**Phase 5 — Mobile (Capacitor)**
- Wrap SPA, native SQLite, push, app-store builds, mobile-specific UX polish.

Each phase is independently shippable; the app remains usable online-first the
entire way.

---

## 11. Risks & Open Questions

- **Permission model vs sync rules** *(highest risk)* — can PowerSync/Electric
  express "owner + collaborators, minus pending invites" cleanly? Spike in
  Phase 0. If not → Option C.
- **Delete/permission revocation while offline** — a user offline-edits a card
  they've since lost access to. Server must reject on upload and the client must
  reconcile (discard local, surface a message). Design this explicitly.
- **Item text concurrent editing** — LWW may lose keystrokes on true
  simultaneous edits. Acceptable for v2.0? If not, scope a text CRDT for `text`
  only.
- **Schema migration on millions of client DBs** — version the local schema and
  ship client migrations; the engine usually handles this but must be planned.
- **SQLite-dev parity** — per project memory, SQLite is dev-only; ensure the sync
  engine's Postgres features (logical replication) aren't relied on in dev tests.
- **Bundle/runtime cost** of a client SQLite (wasm) on web — measure.

---

## 12. Rough Effort Shape

Not committing to numbers before the Phase 0 spike, but the *shape*:

| Block | Relative size |
|---|---|
| Phase 0 spike + finish LISTEN/NOTIFY | Small |
| Backend sync foundations (versioning, tombstones, idempotency) | **Medium** |
| Backend change feed / sync rules | **Medium–Large** (depends on A vs C) |
| Frontend repository layer + store migration | **Large** (but mechanical, per-store) |
| Offline UX (status, conflicts) | Medium |
| Tauri packaging | Small–Medium |
| Capacitor packaging + push + stores | Medium |

The frontend rewrite is large but *mechanical and incremental*. The genuinely
hard, design-heavy work is the **backend change feed + conflict/permission
reconciliation** — fund that thinking first.

---

## TL;DR
Keep the Vue UI. Replace the store internals with a local-first repository over a
client SQLite. Reuse the existing FastAPI endpoints as the sync upload path so
permissions stay server-side. Ship offline-web first, then Tauri desktop, then
Capacitor mobile — all from one SPA. Offline and mobile **are** the same path
here, on purpose. No greenfield rewrite required.
