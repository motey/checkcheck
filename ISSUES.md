# Known issues

Running log of known bugs / rough edges that are out of scope for the change
that discovered them. Newest first.

---

## Shared-card listing eager-loads an arbitrary user's `CheckListPosition`

**Status:** open · **Severity:** low-to-medium · **Discovered:** 2026-06-23

**Symptom**

Listing a shared checklist logs:

```
SAWarning: Multiple rows returned with uselist=False for eagerly-loaded
attribute 'CheckList.position'
```

**Where**

- `CheckListCRUD.list(...)` eager-loads the position with
  `selectinload(CheckList.position)` —
  [CheckCheck/backend/checkcheckserver/db/checklist.py:254](CheckCheck/backend/checkcheckserver/db/checklist.py#L254).
- `CheckList.position` is a one-to-one (`uselist=False`) relationship, but
  `CheckListPosition` is **per-user**: a card shared with N users has N position
  rows (one per collaborator + the owner).

**Root cause**

The `selectinload(CheckList.position)` is **not user-scoped**, so for a shared
card it loads every user's position row and then collapses them into the single
`uselist=False` slot — SQLAlchemy warns and picks one row arbitrarily.

The access query (`_add_user_has_access_query`) *does* inner-join
`CheckListPosition` scoped to the current user (for filtering/ordering), but that
scoping is not carried into the eager-load of the `position` relationship.

**Impact**

For a shared card, the `position` returned to the caller (archived / pinned /
index) may be **another user's** position rather than the caller's. This can
surface as wrong pinned/archived state or ordering on shared cards. It is
pre-existing (any shared-card list triggers it) and was only made more visible
by the new `?shared=with_me|by_me` filters, which list shared cards directly.

**Suggested fix**

Scope the position eager-load to the current user, e.g. replace the unscoped
`selectinload(CheckList.position)` with a per-user loader criteria
(`with_loader_criteria(CheckListPosition, CheckListPosition.user_id == user_id)`)
or load the caller's position explicitly and attach it, mirroring how labels are
already re-scoped per user in the `list_checklists` route. Add a regression test
that lists a card shared with two users and asserts each caller sees **their own**
position (distinct pinned/archived/index).

## Re-ordering labels does not work

one can re-order labels in the label editor. but is has no effect on the actuall label list.
