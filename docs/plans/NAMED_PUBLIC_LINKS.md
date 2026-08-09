# Plan: named public links

**Status:** ✅ Done (2026-08-09), all five chunks, on branch
`feat/issue-9-named-links`. Implements
[issue #9](https://github.com/motey/checkcheck/issues/9).

Implemented as specified, with two deviations worth knowing:

- N1's test bullet ("create three, delete `Link-2`, create again: the new one is
  `Link-3`") contradicts decision 2 and would collide. The implementation and the
  test both say `Link-4`.
- N4 needed one line the plan did not list: `notification_context` copies a
  whitelist of keys out of a notification's payload, so `link_name` had to be
  added there before any wording could read it.

The public-link screenshot in `docs/screenshots.md` was left as it is: the row
gained a name line but still reads as the same surface (plan section 7).

**Scope**, in the issue's words:

> Having a card with many links can be confusing. Also a link can/must be
> referenced in the share by email dialog. Lets have the possiblity to name
> links. have a short text attribute attached to them. If the user leaves it
> empty lets have a default text with an index. like `Link-2`

Today a `CheckListPublicShare` has no human-readable handle at all. The list in
[`PublicLinks.vue`](../../CheckCheck/frontend/components/ShareModal/PublicLinks.vue)
renders each link as a permission badge plus an expiry line, and the token is
redacted after creation, so three "view, never expires" links on one card are
literally indistinguishable. The send-by-email picker has the same problem with
a worse consequence: its options read `view · created 09/08/2026`, and picking
the wrong row mails out a capability the owner did not mean to hand over.

A short name fixes both, and it is the missing piece for a third surface: the
`public_link_opened` notification currently says "your public link to X was
opened" without saying *which* one.

---

## 0. Standing rules

- No em dashes or en dashes anywhere: docs, comments, commit messages, UI copy.
- Anything a user clicks gets vitest and/or Playwright coverage.
- **The maintainer commits, nobody else.** Leave the work dirty in the tree and
  report what changed.
- `CheckCheck/openapi.json` and `pdm.lock` are rewritten as a side effect of
  backend and E2E runs. This plan *does* intend a real `openapi.json` change
  (chunk N1 adds fields to three schemas), so check the diff rather than
  reverting it wholesale, and `git checkout --` it only when the churn is
  version-string noise.
- The 2.0 schema is frozen: this is a real Alembic revision (`0018`) stacked on
  `0017`, not a model edit alone.
- Public links are an **online-only** surface. `stores/share.ts` guards every
  link call with `assertOnline`, they are not in the IndexedDB snapshot and not
  in the `/api/changes` delta feed. Nothing in this plan touches the outbox, the
  sync protocol or `server_seq`.

---

## 1. Decisions settled before chunking

| # | Question | Decision |
|---|---|---|
| 1 | Is the default name stored, or computed for display? | **Stored, at creation time.** The name has to read identically in the link list, in the email picker, in the confirm step and (chunk N4) in a rendered notification, and two of those are server-side. A display-time fallback would mean the same numbering rule written once in TypeScript and once in Python, and it would renumber every link whenever an earlier one is deleted. Storing it means one string, one source, stable for the life of the link. |
| 2 | What is the numbering rule? | **`Link-{n}` where `n` is one above the highest `Link-<digits>` already on that card**, and `Link-1` when there is none. Not "count + 1": create three, delete the second, create again, and count + 1 hands out a second `Link-3`. Scanning the suffixes gives a collision-free next name without a unique constraint or a counter column. Two concurrent creates can still tie, which is cosmetic and is not worth a lock. |
| 3 | Can a link end up with no name? | **No.** Create resolves an empty or whitespace-only name to the generated default, and the migration back-fills every existing row. `PublicLinkRead.name` is therefore a plain `str`, and the client never has to render an "unnamed" state. |
| 4 | What does clearing the name mean? | **Regenerate a fresh default.** `PATCH` with `name: ""` or `name: null` is "I do not want to call it anything", and the honest answer to that is the automatic name, not an empty row. The response carries the new name, so the input the user just blanked refills itself with `Link-4`. |
| 5 | How long may a name be? | **60 characters**, enforced in the request models (trimmed first) with a `maxlength` on the input. The column itself stays an unbounded string, matching `token` and `password_hash` in the same model: a length that lives in the API layer returns a 422 with a message, while a length that lives in the column returns a 500 from the driver. |
| 6 | Is the name secret? | **No, but it is owner-only in practice.** `PublicLinkRead` is only ever returned by the owner-gated management endpoints, and the anonymous surface in `routes_checklist_public.py` returns the checklist, never the link row. Nothing has to be added to keep it that way, but N1 gets a test that pins it, because "the owner's private label for this link" is exactly the kind of field that leaks into a public response later. |
| 7 | Does the name go into the invitation email the recipient gets? | **No.** `notify/invitation.py` writes to somebody who may not have an account and deliberately says as little as possible (`NOTIFY_EMAIL_CONTENT_MODE: minimal` strips even the card name). "Kitchen renovation, contractors" is the owner's note to themself about who holds a link, and mailing it to the holder is the opposite of what it is for. |
| 8 | Does the name go into the `public_link_opened` notification? | **Yes, in chunk N4**, gated on `NOTIFY_EMAIL_CONTENT_MODE` exactly like `_card_name`. That notification goes to the link's creator, who is the one person the name was written for and the one person who cannot currently tell which of their links was opened. This is the only part of the plan beyond the issue's letter; it is a separate chunk so it can be dropped without touching N1 to N3. |
| 9 | Does the client preview the name a new link would get? | **No.** A "leave empty and it gets a name like `Link-3`" hint, not a computed `Link-3`, because computing it means decision 2's rule in TypeScript as well, which decision 1 exists to avoid. |
| 10 | Does renaming need its own endpoint? | **No.** `PATCH /api/checklist/{id}/public-links/{link_id}` already exists and already applies `exclude_unset` semantics. `name` is one more optional field on `PublicLinkUpdateRequest`. |

---

## 2. Chunks

| Chunk | Area | Size |
|---|---|---|
| **N1** | Backend: column, migration, create/read/update, tests, `openapi.json` | medium |
| **N2** | Frontend: name in the create form and the link list, inline rename | medium |
| **N3** | Frontend: the email picker and confirm step reference the link by name | small |
| **N4** | `public_link_opened` says which link (optional) | small |
| **N5** | Docs and the test sweep | small |

Strict order N1 → N2 → N3. N2 cannot start before `openapi.json` and the
generated types carry `name`, and N3 reads the same field N2 renders. N4 is
independent of N2 and N3 and only needs N1. N5 last, once the wording has
settled.

---

## 3. N1: the backend

**Goal:** every public link has a non-empty `name`, settable on create, editable
on patch, returned by list, and absent from the anonymous surface.

### Model

[`model/checklist_public_share.py`](../../CheckCheck/backend/checkcheckserver/model/checklist_public_share.py),
on `CheckListPublicShareCreate`:

```python
name: Optional[str] = Field(
    default=None,
    description=(
        "The owner's short label for this link, so several links on one card "
        "can be told apart. Never shown to anonymous visitors and never mailed "
        "to a link's recipient. Null only on rows written before this column "
        "existed; the API always resolves a name."
    ),
)
```

This file imports `Field` from `sqlmodel`, so it really is a column. (The trap
recorded for `model/_base_model.py`, where `Field` is pydantic's and column
arguments are silently ignored, does not apply here.)

Then the pure numbering helper, in the same module because it is domain logic
about this model and it is the one piece worth unit-testing on its own:

```python
DEFAULT_NAME_PREFIX = "Link"
_DEFAULT_NAME_RE = re.compile(rf"^{DEFAULT_NAME_PREFIX}-(\d+)$")


def next_default_name(existing: Iterable[Optional[str]]) -> str:
    """The automatic name for the next link on a card: one above the highest
    ``Link-<n>`` already there, or ``Link-1``.

    Deliberately not "number of links + 1": create three, delete the second and
    create again, and that hands out a second ``Link-3``."""
```

### Migration

`migrations/versions/0018_public_share_name.py`, `down_revision = "0017"`,
modelled on `0011_add_suggest_existing_items.py`:

1. `op.add_column("checklist_public_share", sa.Column("name", sa.String(), nullable=True))`.
2. Back-fill, so no upgraded instance shows a nameless link: number each card's
   links by `created_at` and write `Link-1`, `Link-2`, and so on. One statement
   with a window function on Postgres; SQLite is dev-only (see the DB-targets
   note) and can use the same `UPDATE ... FROM (SELECT ... row_number() OVER ...)`
   shape, which modern SQLite supports, or a plain Python loop over the rows if
   that turns out to be awkward. Idempotent either way: only rows with
   `name IS NULL` are touched.
3. `downgrade()` drops the column.

Guard the `add_column` against the column already existing, the way `0017` does:
on a fresh database `create_all` builds it from the model before Alembic stamps
head, so the revision has to be a no-op there.

### Routes

[`routes_checklist_share.py`](../../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_share.py),
in the "Public URL links (Phase 5)" block:

- `MAX_PUBLIC_LINK_NAME_LENGTH = 60`, next to the other limits.
- A `_normalize_link_name(value: Optional[str]) -> Optional[str]` shared
  validator: strip, `""` becomes `None`, and over the cap raises a 400 with
  wording that does not echo the name back at full length.
- `PublicLinkCreateRequest.name: Optional[str] = None`.
- `PublicLinkUpdateRequest.name: Optional[str] = None`.
- `PublicLinkRead.name: str`, and `_to_public_link_read` fills it with
  `link.name or DEFAULT_NAME_PREFIX` (a last-ditch fallback for a row written
  outside the API; create and the migration both guarantee a real name).
- `create_public_link`: when the normalized name is `None`, call
  `list_for_checklist` and pass it through `next_default_name`.
- `update_public_link` needs care. The generic CRUD update applies
  `model_dump(exclude_unset=True)`, and `name` **is** a column, unlike
  `password`, so an explicit `null` would write `NULL` straight through and
  produce the nameless row decision 3 rules out. Resolve the value *before* the
  update call:

  ```python
  if "name" in body.model_fields_set:
      body.name = _normalize_link_name(body.name) or next_default_name(
          n.name
          for n in await public_share_crud.list_for_checklist(checklist_id)
          if n.id != link.id
      )
  ```

  Excluding the link being renamed matters: without it, blanking `Link-2` on a
  card whose highest link is `Link-2` returns `Link-3` for no reason.

### Tests

New cases in
[`tests/tests_sharing_public.py`](../../CheckCheck/backend/tests/tests_sharing_public.py),
under a `# ── link names ──` heading next to the existing management block:

- Create without a name yields `Link-1`, then `Link-2`.
- Create with `"  Groceries  "` stores `Groceries`.
- Create with a 61-character name is a 400, and no link is created.
- Create three, delete `Link-2`, create again: the new one is `Link-3`, and no
  two links on the card share a name.
- `PATCH {"name": "Family"}` renames; the list reflects it.
- `PATCH {"name": ""}` and `PATCH {"name": null}` both regenerate a default, and
  a card whose only link is `Link-1` gets `Link-1` back rather than `Link-2`.
- `PATCH` with no `name` key leaves the existing name alone (this is the
  regression that `exclude_unset` exists to prevent).
- Renaming is owner-only, alongside the existing `..._patch_is_owner_only`.
- The anonymous surface never carries it: `GET /public/checklist/{token}` and
  the item list contain no `name` from the link row (decision 6).

Plus a plain unit test for `next_default_name` over the interesting inputs:
empty, `["Link-1", "Link-3"]`, `["Family"]`, `["link-2"]` (different case, does
not count), `["Link-007"]`.

### Regenerate

`openapi.json` per the recorded dump procedure (backend venv, dummy secrets of
64+ characters, `SETUPTOOLS_SCM_PRETEND_VERSION` pinned), then
`bun run postinstall` in the frontend so `components["schemas"]["PublicLinkRead"]`
carries `name`. `types/index.ts` needs no edit: its aliases are structural.

**Verify:** `./run_backend_tests_with_sqlite.sh`, then
`./run_backend_tests_with_postgres.sh` (the migration back-fill is the reason
the Postgres run is not optional here).

---

## 4. N2: the create form and the link list

**Goal:** an owner names a link when creating it, sees the name as the first
thing in each row, and can rename in place.

All in
[`PublicLinks.vue`](../../CheckCheck/frontend/components/ShareModal/PublicLinks.vue).

**Create form.** A Name field above Level, since it is the field the issue is
about and the one people will fill in most:

```
Name     [ optional                    ]  leave empty for an automatic name like Link-3
Level    [ View ▾ ]
Expires  [          ]  optional, never if blank
Password [          ]  optional passphrase
```

`data-testid="public-link-name"`, `maxlength="60"`, cleared after a successful
create like `expiry` and `password` are. The hint is static text (decision 9).

**Rows.** The name becomes the row's first line, with the permission badge and
the lock demoted to beside it:

```
Groceries                    [view] 🔒        [copy] [ ] [delete]
Never expires
```

Keep `data-testid="public-link-row"` on the `li` and add
`data-testid="public-link-row-name"` on the name element, so the existing spec's
row selectors keep working.

**Rename in place.** Click the name to swap it for a `UInput` (the same
focus-swap shape the card notes use), then:

- Enter or blur commits `shareStore.updateLink(checkListId, link.id, { name })`.
- Escape reverts and commits nothing.
- An unchanged value commits nothing, so a stray click is not a PATCH.
- Blanking it commits `""` and lets the server regenerate. The store writes the
  PATCH response back into the list, so the row repaints with `Link-4`. This is
  the payoff for decision 4 living on the server.
- Reuse the existing `busyId` for the disabled and loading state, and the
  existing "Could not update link" toast on failure.

**Fresh-link box.** Its heading names the link that was just created
(`Copy the link for "Groceries" now, the server never returns it again`) so the
name is visible at the one moment the URL is.

**Tests.** No new vitest here: this is template work over the store, and the
repo's unit tests target framework-free utils. Covered by E2E in N5.

---

## 5. N3: the email picker

**Goal:** the owner picks and confirms a link by its name, not by its creation
date.

- [`utils/publicLinkEmail.ts`](../../CheckCheck/frontend/utils/publicLinkEmail.ts)
  gains one framework-free helper, which is where this formatting belongs and
  the reason both surfaces can share it:

  ```ts
  /** How a link is named in the picker and in the confirm step. */
  export function linkLabel(link: { name: string; permission: string; password_protected: boolean }): string
  ```

  Something like `Groceries · view · passphrase`. Unit tests in
  [`tests/unit/publicLinkEmail.spec.ts`](../../CheckCheck/frontend/tests/unit/publicLinkEmail.spec.ts).

- `sendableLinkOptions` uses it, dropping the created-date wording.
- The picker's `v-if="links.length > 1"` becomes an always-visible statement of
  which link is being sent: the select when there is more than one, and a plain
  `Sending: {{ linkLabel(selectedLink) }}` line when there is exactly one. With
  names in play, "which link is this about" should never be implicit.
- The confirm step gains a second line, `Sending: {{ linkLabel(selectedLink) }}`,
  above the existing sentence. `confirmSentence` is **not** changed: it describes
  what the recipient will be able to do and it is matched line for line against
  `notify/invitation.permission_sentence` on the server. The link's name is the
  owner's business and belongs in a line of its own.

---

## 6. N4: the notification says which link (optional)

**Goal:** "Your public link *Contractors* to Kitchen was opened", instead of
leaving an owner with four links to guess.

- [`api/access.py`](../../CheckCheck/backend/checkcheckserver/api/access.py),
  in the `mark_first_opened` branch: add `"link_name": link.name` to the
  `emit_notification` payload.
- [`notify/render.py`](../../CheckCheck/backend/checkcheckserver/notify/render.py):
  a `_link_name(context, config)` helper mirroring `_card_name` (returns `None`
  under `NOTIFY_EMAIL_CONTENT_MODE: minimal`), then the four
  `public_link_opened` sites:
  - `_wording_for_one`, the email subject. Names the link.
  - `_wording_for_many`, the coalesced subject. Stays a bare count, for the same
    reason the plural reminder subject drops the note: several links cannot be
    represented by one of their names.
  - `_line_for`, the plain-text body line. Names the link.
  - `_push_title`, which reads `_minimal_push` rather than `_minimal` (push has
    its own content mode) and whose output is squeezed by
    `_within_push_budget`. Adding a name here spends bytes against that budget,
    so put the name after the card name, where the existing clipping trims it
    first.
- `webhook_body` gets **no new key**. Its flat schema promises every key is
  always present, so a `link_name` would have to be emitted as null on every
  other notification type to keep that promise, and the name already reaches a
  receiver through `text` (which is `_line_for`). Not worth widening the
  contract for one type.
- Every wording stays correct when `link_name` is missing, because rows queued
  before this change and rows from a `minimal` instance will not have it.
- Tests: extend the existing `public_link_opened` coverage with a named link and
  with `minimal` mode.

Drop this chunk if the release is tight. Nothing in N1 to N3 depends on it.

---

## 7. N5: docs and the sweep

- **`docs/UPGRADING.md`**: a newest-first entry for migration `0018`, in the
  house style of the `0017` one. It adds one nullable column and back-fills it,
  touches nothing else, and needs no operator action.
- **`CHANGELOG.md`**: one line.
- **E2E**, [`tests/e2e/sharing-public-links.spec.ts`](../../CheckCheck/frontend/tests/e2e/sharing-public-links.spec.ts):
  create with a name and assert the row shows it; create two without names and
  assert `Link-1` and `Link-2`; rename a link and assert it survives a reopen of
  the dialog; blank a name and assert an automatic one comes back.
- **E2E**, [`tests/e2e/public-link-email.spec.ts`](../../CheckCheck/frontend/tests/e2e/public-link-email.spec.ts):
  the picker's options carry names, and the confirm step names the link.
- **Screenshots**: `docs/screenshots.md` has a public-link shot. Retake it only
  if the row layout change makes the existing one misleading; it is a binary in
  the repo and not worth churning otherwise.

**Verify:** `bun run test:unit` in the frontend, then the E2E suite through the
local Playwright CLI via bun (not bare `bunx playwright`, whose cached version
mismatch breaks collection). A few DnD and sharing specs fail
non-deterministically per run, so re-run a failure once before treating it as a
regression.

---

## 8. Out of scope

- Naming anything other than public links. Collaborator grants are named by the
  person holding them already.
- Making names unique per card. Two links called `Groceries` is the owner's
  choice, and a uniqueness constraint would turn the automatic-name race in
  decision 2 from a cosmetic tie into a failed create.
- Recovering a token so an older link becomes copyable again. That is a
  deliberate property of the capability model and is unrelated to naming, even
  though the two show up in the same row.
- Anything offline. See the standing rules.
