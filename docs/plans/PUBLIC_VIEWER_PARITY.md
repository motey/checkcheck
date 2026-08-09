# Plan: public viewer / card parity

**Status:** done (P1 to P4 landed). Implements
[issue #11](https://github.com/motey/checkcheck/issues/11).

Three deviations from the plan as written, each recorded where it happens:

- **`PublicChecklistItem.vue` was kept, not deleted** (decision 11). It is now the
  public *adapter* over `CardParts/ItemRow.vue`, exactly mirroring what
  `CheckListItem.vue` is on the authed side, rather than a second row
  implementation. Inlining the same wrapper twice in the page (the unchecked list
  and the checked list) would have been the only alternative. Its `data-testid`
  values are down to one, `public-item-text`, which now marks an *editable row*
  rather than a raw textarea: the shared row is a focus-swap surface, so the
  textarea only exists while a row is being edited. `public-item-checkbox` and
  `public-item-delete` were referenced by no spec and are gone.
- **`CardParts/ItemList.vue` and `ItemsSection.vue` take slots, not item-plus-callback
  props.** The list renders each row through an `#item` scoped slot (with the ref
  registration handed down), and the section takes `#unchecked` / `#checked`; that
  is what lets the authed side keep injecting its store-coupled `CheckListItem`
  wrapper and the public side its own, instead of pushing the store logic up into
  the collection. The shared components still read no store and write nothing,
  which is what decision 1 was for.
- **No screenshot needed regenerating.** `docs/screenshots.md` has no shot of the
  `/p/<token>` viewer; its two public-link images are of the owner's share dialog
  and mail-a-link dialog, neither of which this change touches.

**Scope**, in the issue's words:

> Things like "Seperate checked items" seem not be supported by the public link
> editor. Therefore also open or unopen checked items is also not available.

The issue names one symptom. The cause is broader: `/p/<token>` is not the card,
it is a second, much smaller reimplementation of the card that was written
alongside the sharing work and has not tracked the card since. This plan closes
the layout gap the issue names, closes the two other gaps that come from the
same cause, and puts a shared layer underneath both surfaces so the gap does not
reopen with the next card feature.

---

## 0. Standing rules

- No em dashes or en dashes anywhere: docs, comments, commit messages, UI copy.
- Anything a user clicks gets vitest and/or Playwright coverage.
- **The maintainer commits, nobody else.** Leave the work dirty in the tree and
  report what changed.
- `CheckCheck/openapi.json` is rewritten as a side effect of backend runs. This
  plan *does* intend a real change to it (chunk P1 adds two routes), so review
  the diff rather than reverting it wholesale, and `git checkout --` it only
  when the churn is version-string noise.
- The public surface is **online only**. It has no outbox, is not in the
  IndexedDB snapshot, and is not in the `/api/changes` delta feed. Nothing here
  touches the outbox, the sync protocol, `server_seq`, or the `localFirst` flag.
- No schema change: every column this plan reads or writes
  (`checked_items_seperated`, `checked_items_collapsed`, `name`, `text`,
  `checklist_item_position.index`) already exists, so there is no Alembic
  revision. The current head stays `0018`.

---

## 1. Where the gap actually is

The authed open card is assembled from
[CheckList.vue](../../CheckCheck/frontend/components/CheckList.vue) ->
[CheckListItemCollection/Seperated.vue](../../CheckCheck/frontend/components/CheckListItemCollection/Seperated.vue)
-> [CheckListItemCollection/index.vue](../../CheckCheck/frontend/components/CheckListItemCollection/index.vue)
-> [CheckListItem.vue](../../CheckCheck/frontend/components/CheckListItem.vue),
all of which read the Pinia stores directly.

The public viewer is
[pages/p/[token].vue](../../CheckCheck/frontend/pages/p/[token].vue) plus
[composables/usePublicCard.ts](../../CheckCheck/frontend/composables/usePublicCard.ts)
and [PublicChecklistItem.vue](../../CheckCheck/frontend/components/PublicChecklistItem.vue).
The composable's own header comment states the reason: the authed components are
tightly coupled to the session-backed stores, so the viewer was given a slim
self-contained data source instead. That decision was right for the data layer
and wrong for the presentation layer, which is where all of this drift lives.

The concrete deltas, and whether the backend can already serve them:

| Feature | Backend today | In this plan |
|---|---|---|
| `checked_items_seperated` layout | already in the public `GET` response, ignored by the client | **yes** (P3) |
| Collapse / expand the checked section | field already in the response; persisting it needs a write route | **yes** (P1 + P3) |
| Item drag-reorder | no anonymous position route exists | **yes** (P1 + P3) |
| Edit the card title and notes | no anonymous checklist `PATCH` exists | **yes** (P1 + P3) |
| Markdown-rendered item text, plus the click-to-edit focus swap | frontend only | **yes**, falls out of P2 |
| Card color theme | `color` already in the response, ignored by the client | **yes** (P3) |
| Untick all / Delete ticked | no anonymous bulk routes | **no**, see decision 3 |
| Uncheck-suggestions while typing | frontend only | **no**, see decision 4 |

---

## 2. Decisions settled before chunking

| # | Question | Decision |
|---|---|---|
| 1 | How do the two surfaces stop drifting? | **Extract shared presentational components.** The separated-checked layout, the collapse header and the item row move into components that take items and callbacks as props and import no store. The authed collection components and the public page each become a thin adapter over them. The alternative that was considered and rejected is injecting a data source into `CheckList.vue` itself: it would give one code path forever, but it puts the board, the editor, the DnD wiring and the whole offline path in the blast radius of a layout bug fix. The presentational split gets most of the benefit for a fraction of the risk, and leaves the store-coupled machinery (outbox, `editGuard`, `useSync`) exactly where it belongs. |
| 2 | Does the anonymous collapse toggle write to the card? | **At `check` and `edit`, yes. At `view`, no.** `checked_items_collapsed` is a column on `checklist`, not a per-user row, so a persisted toggle is visible to the owner and to every other visitor on the link. A check-level link already writes to the card every time somebody ticks a box, so one more display flag changes nothing about what that capability means. A view-level link grants no write path at all today and does not gain one here: its visitor still expands and collapses, but only in their own session. See decision 8 for how the client seeds and holds that local state. |
| 3 | Are the bulk operations (untick all, delete ticked) in scope? | **No.** They are a separate capability, not a layout gap: they need two more anonymous routes plus a confirm modal, and "delete every ticked item" behind an anonymous capability URL deserves its own decision about whether public links should be able to do it at all. The issue does not ask for them. Leaving them out keeps this plan about parity of the reading and editing surface. |
| 4 | Are the uncheck-suggestions in scope? | **No**, but note that `findMatchingCheckedItems` in [utils/normalizeItemText.ts](../../CheckCheck/frontend/utils/normalizeItemText.ts) is already a pure function over an item array, so once P2 lands this is a handful of lines on the shared row plus a `suggest_existing_items` read. Deliberately deferred so P2 stays a no-behaviour-change refactor. |
| 5 | How far does "full card fidelity" go? | The public page keeps its own page chrome (logo header, permission badge, "Add to my deck" footer): that framing is what tells a visitor they are looking at something somebody shared with them. Inside that chrome the card renders like the open card: the color theme applied to the card surface, the title and notes typography and spacing, the item rows. Owner-only surfaces (footer toolbar, labels, reminders, share, pin, archive, kebab) stay absent. |
| 6 | Which wire format does the public reorder use? | **A plain `PATCH .../position` carrying a client-computed fractional index**, not anonymous twins of the `move/above/{id}` and `move/under/{id}` routes. The client math already exists and is already tested: `findNewPlacementForItem` in [utils/helpers.ts](../../CheckCheck/frontend/utils/helpers.ts) plus `fractionalIndexBetween` in [utils/outboxOps.ts](../../CheckCheck/frontend/utils/outboxOps.ts), which is documented as computing exactly what the server's midpoint math would. One route instead of three, and the same shape the local-first authed path already uses. |
| 7 | Which permission does each new write need? | Item position: `edit`, matching `update_checklist_item_position`. Card `name` and `text`: `edit`, matching `update_checklist`. Card `checked_items_collapsed`: `check` (decision 2). One route, two field groups, so the level is resolved per field rather than per route. |
| 8 | Where does a view-level visitor's collapse state live? | A `ref` in the composable, seeded from the card's `checked_items_collapsed` on load, mirrored into `sessionStorage` under `checkcheck:public-collapsed:<token>` next to the existing grant key. `sessionStorage` because it should survive the reload of a viewer tab and not outlive it, which is exactly the rule the grant already follows, and because writing it is best-effort: the existing `persistGrant` already swallows the privacy-mode throw and this does the same. |
| 9 | Does the public item text become rendered Markdown? | **Yes**, as a consequence of P2 rather than as its own feature. The public page already renders the card notes through `renderMarkdown` and `v-html`, so the trust boundary does not move: [utils/markdown.ts](../../CheckCheck/frontend/utils/markdown.ts) renders a restricted subset and runs DOMPurify over it, and no second unsanitized path is added. |
| 10 | Does the public surface get optimistic writes or an outbox? | **No.** Every write stays what it is today: call, then patch the local ref from the response. The composable's SSE already reconciles anything that arrives from elsewhere. |
| 11 | Does `PublicChecklistItem.vue` survive? | **No.** After P3 the public row is the shared row, so the component is deleted and its `data-testid` values (`public-item-checkbox`, `public-item-text`, `public-item-delete`) move onto the shared row's public-side wrapper, since [tests/e2e/public-viewer.spec.ts](../../CheckCheck/frontend/tests/e2e/public-viewer.spec.ts) selects on them. Keeping both names on one element is the cheap way to leave the existing specs untouched. |

---

## 3. Chunks

| Chunk | Area | Size |
|---|---|---|
| **P1** | Backend: anonymous checklist `PATCH` and item-position `PATCH`, tests, `openapi.json` | medium |
| **P2** | Frontend: extract the shared presentational components, rewire the authed path, no behaviour change | large |
| **P3** | Frontend: the public viewer adopts them, plus the three new features | medium |
| **P4** | Tests and docs | small |

Order: P1 and P2 are independent and can be done in either order. P3 needs both
(the generated types from P1, the components from P2). P4 last.

P2 is the risky chunk and it is deliberately isolated: it must land with every
existing Playwright spec green and no visible change to the authed card. If it
cannot, the useful fallback is to stop after P1 and implement P3 against the
public viewer's own components, which delivers the issue at the cost of the
duplication decision 1 exists to remove.

---

## 4. P1: the two anonymous write routes

Both go in
[routes_checklist_public.py](../../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_public.py),
which already holds every anonymous route and every helper they need.

### 4.1 `PATCH /public/checklist/{token}`

A restricted request model, deliberately **not** `CheckListUpdate`: that model
carries `color_id` and `suggest_existing_items` as well, and an anonymous
capability should not be able to recolor somebody's card or flip a per-card
behaviour toggle. Declared locally next to `UnlockRequest`:

```python
class PublicCheckListUpdate(BaseModel):
    """The fields an anonymous visitor may change on a publicly shared card.

    Deliberately narrower than ``CheckListUpdate``: a link is a capability handed
    to whoever holds the URL, so it may edit the content it exists to share
    (``name``, ``text``) and the one display flag that is stored on the card
    rather than per-user (``checked_items_collapsed``), and nothing else.
    ``color_id``, ``checked_items_seperated`` and ``suggest_existing_items`` are
    the owner's settings for the card and stay owner-only.
    """

    name: Optional[str] = None
    text: Optional[str] = None
    checked_items_collapsed: Optional[bool] = None
```

The route resolves at `check` (the lowest level allowed to write anything here)
and re-checks per field, because `name` and `text` need `edit` while
`checked_items_collapsed` needs only `check` (decision 7):

```python
@fast_api_checklist_public_router.patch(
    "/public/checklist/{token}",
    response_model=CheckListApiWithSubObj,
    description=(
        "Update a publicly shared checklist (anonymous). 'name' and 'text' "
        "require an edit link; 'checked_items_collapsed' requires a check link."
    ),
)
async def update_public_checklist(
    body: PublicCheckListUpdate,
    checklist_access: UserChecklistAccess = Security(
        require_public_checklist_permission(ChecklistAccessLevel.check)
    ),
    ...
):
    fields = body.model_fields_set
    if ("name" in fields or "text" in fields) and not checklist_access.has_at_least(
        ChecklistAccessLevel.edit
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This public link does not grant 'edit' permission.",
        )
```

Then `checklist_crud.update(id_=..., update_obj=body)` with
`exclude_unset` semantics (which the shared CRUD update already applies, the
same property `update_public_link` relies on), and a
`SyncNotification(cl_id=..., upd_prop="checklist")` so the owner's board, other
collaborators and other anonymous viewers all refresh. The public composable
already handles that `upd_prop` by refetching the card.

**The trap this route must not step on.** `update_checklist` on the authed side
carries a long comment about `scope_position_to_caller`: `CheckList.position` is
a scalar joined relationship over a per-user table with delete-orphan cascade,
so the arbitrarily loaded row must be re-pointed with `set_committed_value`, and
this route **commits**, which is precisely the case the helper's docstring calls
out as producing persistent corruption. So, in this exact order:

1. `checklist_crud.update(...)`, which commits.
2. `owner_position = await checklist_position_crud.get(checklist_id=..., user_id=owner_id)`.
3. `scope_position_to_caller(result, owner_position)`, scoped to the **owner**,
   as `get_public_checklist` does, since an anonymous visitor has no position row.
4. `result.labels = await checklist_label_crud.list_labels_for_user(..., user_id=owner_id)`.
5. `attach_my_permission(result, checklist_access.permission_level())`, which for
   an anonymous caller is the link's level and never `owner`.

Steps 2 to 5 are a copy of the tail of `get_public_checklist`; if the diff looks
like duplication, factoring them into a small `_public_card_response(...)` helper
used by `get_public_checklist`, `update_public_checklist` and `join_public_checklist`
is welcome, as long as `join`'s owner-vs-joiner scoping stays intact.

### 4.2 `PATCH /public/checklist/{token}/item/{checklist_item_id}/position`

A near-copy of `update_checklist_item_position` in
[routes_checklist_item_pos.py](../../CheckCheck/backend/checkcheckserver/api/routes/routes_checklist_item_pos.py),
with three substitutions: `require_public_checklist_permission(ChecklistAccessLevel.edit)`
for the authed guard, `verify_item_belongs_to_public_checklist` (already used by
the item state / update / delete routes in this file) for its authed twin, and
`checklist_access.checklist.id` for the path `checklist_id` this surface does not
have. Body `CheckListItemPositionApiUpdate`, response
`CheckListItemPositionPublicWithoutChecklistID` (the public-facing model the
authed GET already uses, rather than the full row), and a
`SyncNotification(cl_id=..., cli_id=..., upd_prop="item_position")` which the
public composable already handles by reloading the item list.

No `move/above` or `move/under` twins: decision 6.

### 4.3 Tests

New cases in
[tests/tests_sharing_public.py](../../CheckCheck/backend/tests/tests_sharing_public.py),
grouped under a `# -- anonymous writes: card fields and item order --` heading:

- View link: `PATCH` with `{"name": "x"}` is 403, `{"checked_items_collapsed": true}`
  is 403, and the card is unchanged after both.
- Check link: `{"checked_items_collapsed": true}` succeeds and persists;
  `{"name": "x"}` is 403 and does not partially apply (assert the name is
  untouched, since a route that validated after updating would pass a
  status-code-only assertion).
- Edit link: name, text and collapse all succeed, and a `PATCH` carrying only
  `text` leaves `name` alone (the `exclude_unset` regression).
- Edit link: `color_id` and `suggest_existing_items` in the body are ignored
  rather than applied (they are not fields on the model, so this pins the
  narrower model against somebody later swapping in `CheckListUpdate`).
- Disabled, expired and password-protected-without-grant links: `PATCH` is 404,
  matching every other route on this surface, and never 401 or 403.
- Position: an edit link reorders an item and a subsequent item list comes back
  in the new order; a check link is 403; an item belonging to a different card
  is 404 via `verify_item_belongs_to_public_checklist`.
- The owner's `CheckListPosition` row is intact after an anonymous `PATCH`, and
  so is a second collaborator's. This is the `scope_position_to_caller` trap
  from 4.1, and it is invisible to every other assertion in the file.
- A card `PATCH` and a position `PATCH` each emit exactly one sync notification
  with the expected `upd_prop`.

### 4.4 Regenerate

`openapi.json` per the recorded dump procedure (backend venv, dummy secrets of
64+ characters, `SETUPTOOLS_SCM_PRETEND_VERSION` pinned), then `bun run postinstall`
in the frontend so the two new paths reach `types/`.

**Verify:** `./run_backend_tests_with_sqlite.sh`, then
`./run_backend_tests_with_postgres.sh`.

---

## 5. P2: the shared presentational layer

**Goal:** the layout and the row markup live in components that know nothing
about Pinia, and the authed card renders **identically** to how it renders today.
This chunk adds no feature and changes no behaviour. Its acceptance criterion is
the existing Playwright suite, unchanged.

Four new components under `components/CardParts/` (Nuxt auto-imports them as
`CardPartsItemRow` and so on). Rules for all four: props in, events out, no
`useCheckListsStore`, no `useCheckListsItemStore`, no `useOutbox`, no
`editGuard`.

### `CardParts/ItemRow.vue`

Lifted from [CheckListItem.vue](../../CheckCheck/frontend/components/CheckListItem.vue),
minus everything store-shaped.

- Props: `item`, `canCheck`, `canEdit`, `editMode`, `showDragHandle`,
  `searchQuery` (optional, `null` on the public page).
- Emits: `toggle`, `update:text`, `delete` (with the `focusPrev` flag),
  `add-after`, `focus`, `blur`.
- Keeps: the rendered-Markdown / textarea focus swap, `renderMarkdownInline`,
  the strikethrough styling, the `a.ext-link` click guard, the Enter and
  backspace handlers, the local-copy-decoupled-from-server `watch` (the public
  row already implements the same idea for the same reason), the `defineExpose`d
  `focusTextarea`, every `data-testid` (`item-row`, `item-text-rendered`,
  `item-text-editor`, `delete-item`) and every class, including the `@media (hover: none)`
  touch rules and the drag-handle padding.
- Drops: the store `update` call, `markEditing` / `clearEditing`, the debounce,
  the suggestion list, `usePermissions` (the parent computes `canCheck` and
  `canEdit` from `my_permission` and passes booleans down).
- The suggestion list becomes a `#below` slot so the authed wrapper can keep
  rendering it in place.

**Two traps when moving this markup.** First, the debounce: the authed side
debounces the text write at 500ms with a 3000ms `maxWait`, and the public side
debounces at exactly the same numbers, so the debounce belongs to neither and
stays in each adapter, not in the row. Second, drag focus: per the recorded
`formkit-draggable-focus-cancels-drag` finding, keyboard a11y attributes on a
draggable node must sit on the FormKit `<li>`, not on a child, or focus-on-mousedown
kills the drag. The rendered-text div carries `tabindex` today and works because
the drag handle is a separate element matched by `dragHandle: ".list-item-drag-handle"`.
That arrangement must survive the move exactly as it is.

### `CardParts/ItemList.vue`

Lifted from [CheckListItemCollection/index.vue](../../CheckCheck/frontend/components/CheckListItemCollection/index.vue),
keeping only the FormKit wiring.

- Props: `items`, `canEdit`, `enableDrag`, `showAddButton`.
- Emits: `reorder` (`newOrder`, `movedItem`), plus the row events forwarded up
  with the item id attached.
- Keeps: `useDragAndDrop` with `dragHandle: ".list-item-drag-handle"`,
  `longPress: true` / `longPressDuration: 250` / `longPressClass` (the recorded
  `mobile-dnd-longpress` fix), the `dragInProgress` guard that must not reset the
  list mid-drag and the comment explaining why, `draggable: (el) => !el.classList.contains('no-drag')`,
  `animations()`, the `.list-item-longpress` scoped style, and the
  `registerItemRef` map plus a `focusItem(id)` on the exposed API.
- The "add new item" row stays a slot, so the authed side keeps passing its
  color-aware `CheckListItemCollectionAddNewButton` and the public side passes
  its own button with `data-testid="public-add-item"`.

### `CardParts/ItemsSection.vue`

Lifted from [CheckListItemCollection/Seperated.vue](../../CheckCheck/frontend/components/CheckListItemCollection/Seperated.vue),
which today reaches into two stores for the counts and writes the collapse flag
itself.

- Props: `uncheckedItems`, `checkedItems`, `separated`, `collapsed`,
  `editMode`, `canCheck`, `canEdit`, `showMaxItems`, `checkedCount`,
  `uncheckedCount`.
- Emits: `toggle-collapsed`, plus everything the two lists emit.
- Keeps: the `USeparator` variants (the editor's "N checked items" header with
  the chevron, the preview's `+ N checked items` label), `vue-collapsed`'s
  `<Collapse>`, the phone rule that a preview does not expand the checked
  section, the `showCheckedItemCount` arithmetic, and `data-testid="editor-checked-section"`.
- Drops: the store lookups and the `checkListStore.update` call. The parent
  decides what a toggle means, which is the whole point on the public side
  (decision 2).

### `CardParts/NotesField.vue`

Lifted from the notes block of
[CheckList.vue](../../CheckCheck/frontend/components/CheckList.vue).

- Props: `modelValue`, `canEdit`, `placeholder`.
- Emits: `update:modelValue`, `focus`, `blur`.
- Keeps: the rendered / raw focus swap, the "Markdown supported, Formatting
  help" popover with `MarkdownHelp`, `data-testid="card-notes-rendered"` and
  `card-notes-textarea`, and the `markdown-help-trigger` id.
- Drops: `markEditing` / `clearEditing` and the store write, both of which move
  to the authed adapter's `focus` and `blur` handlers.

### Rewiring the authed path

- `CheckListItem.vue` becomes a thin wrapper: `usePermissions`, the debounced
  store write, `markEditing` / `clearEditing`, the suggestion computed rendered
  into the `#below` slot, and `CardPartsItemRow` for everything else. It keeps
  its name and its props, so its ~9 call sites do not move.
- `CheckListItemCollection/index.vue` keeps the `watchEffect` that pulls from the
  store, `addItemAfter` / `addItemAtEnd` / `acceptSuggestion` / `deleteItem` and
  the focus choreography, and delegates the `<ul>` to `CardPartsItemList`,
  forwarding `reorder` into `reorderChecklistItems`.
- `CheckListItemCollection/Seperated.vue` becomes the store adapter over
  `CardPartsItemsSection`: it computes the two item arrays and the two counts and
  turns `toggle-collapsed` back into the `checkListStore.update` it does today.
- `CheckListItemCollection/Preview.vue` stays as it is. It is the board preview,
  it has no DnD and no editing, and folding it in buys nothing.
- `CheckList.vue` swaps its notes block for `CardPartsNotesField` and keeps
  everything else.

### Verify

The whole point of this chunk is that nothing moves, so: `bun run test:unit`,
then the full Playwright suite. `card-editor`, `add-item`, `delete-item`,
`item-movement`, `checked-items-count`, `markdown-notes` and `bulk-item-ops` are
the specs that would catch a regression here. Per the recorded flakiness note, a
few DnD and sharing specs fail non-deterministically per run with disjoint
failure sets, so re-run before concluding the refactor broke something.

---

## 6. P3: the public viewer adopts them

### `usePublicCard.ts`

Add, in the style of the existing methods (guard on permission, call, patch the
local ref, log and swallow on failure):

- `uncheckedItems` and `checkedItems` computed off the existing `items` ref,
  each sorted by `position.index` then `id` so the order matches the store's
  `compareByPositionThenId` tiebreak.
- `collapsed`: a `ref` seeded from `card.checked_items_collapsed` on load, and
  from `sessionStorage` when a value is stored for this token (decision 8).
- `setCollapsed(value)`: sets the ref, writes `sessionStorage` best-effort, and,
  when `canCheck` is true, `PATCH`es `checked_items_collapsed`. When `canCheck`
  is false it stops at the local write. This is the only asymmetry between the
  levels and it gets a comment saying why.
- `updateCard({ name?, text? })`: gated on `canEdit`, `PATCH`es, and assigns the
  response to `card`.
- `reorderItems(newOrder, movedItem)`: gated on `canEdit`. Computes the target
  index with `findNewPlacementForItem` plus `fractionalIndexBetween` exactly as
  `_localMoveItem` does (read the neighbours out of the sorted list, midpoint
  between them, `POSITION_END_GAP` past a single neighbour), `PATCH`es the
  position, and reorders `items` locally. Reuse the two utils, do not restate
  the math: the point of decision 6 is that there is one implementation of it.
- The SSE handler needs one addition: `checklist` currently refetches the card,
  which now also carries a `checked_items_collapsed` another viewer may have
  changed. Apply it to `collapsed` **only** when this visitor is on a
  `check`-or-better link, so a view-level visitor's local expansion is not
  yanked shut by somebody else's toggle.

### `pages/p/[token].vue`

- Replace the `PublicChecklistItem` loop with `CardPartsItemsSection`, passing
  `separated: card.checked_items_seperated`, `collapsed`, the two item arrays and
  the two counts, `editMode: true` (the public viewer is always the open card,
  never a board preview), and `canCheck` / `canEdit` from the composable.
- Move the "Add new item" button into the section's add slot so it sits under the
  unchecked list where the authed editor puts it, rather than under everything.
- Title: a `UTextarea` bound to a local `name` ref, `:disabled="!canEdit"`,
  debounced into `updateCard`, matching `CheckList.vue`'s title field. Keep
  `data-testid="public-card-name"` on it so the existing spec still resolves,
  and keep the plain heading for the view-level case if the disabled textarea
  reads wrong there.
- Notes: `CardPartsNotesField` bound to a local `text` ref, debounced into
  `updateCard`. This replaces the current read-only `v-html` block, and for a
  view-level link the component already renders exactly that.
- Color: apply the same `cardStyle` computation `CheckList.vue` uses (text,
  background and accent, each picked by `colorMode`) to the `UCard`, and add
  `textareas-inherit-color` so the title and item textareas inherit it. The
  `color` object is already in the public response.
- Delete `PublicChecklistItem.vue`, after moving its three `data-testid` values
  onto the public wrapper of the shared row (decision 11).

**Watch for:** the viewer sits inside a `max-w-2xl` column rather than a modal,
so there is no transformed ancestor and no nested scroll container. The
`longPress` touch behaviour still applies (it is a touch-scroll-versus-drag
tradeoff, not a modal artifact), but verify the drag clone positions correctly
on the plain page before assuming the modal tuning transfers.

---

## 7. P4: tests and docs

### Playwright

New cases in
[tests/e2e/public-viewer.spec.ts](../../CheckCheck/frontend/tests/e2e/public-viewer.spec.ts),
following the file's existing pattern (admin request context mints the card, the
items and the link; the visitor runs in a fresh context with no `storageState`;
every page is navigated to `about:blank` in `afterEach` so the live SSE does not
block teardown):

- A card with one ticked and one unticked item, opened on a view link, renders
  the checked item under the separator and not inline.
- Collapse on a view link hides the checked section, and a reload restores the
  collapsed state (the `sessionStorage` path) **without** the owner's card
  having changed. Assert the second half through the API, not the UI.
- Collapse on a check link persists: assert `checked_items_collapsed` through the
  API afterwards.
- An edit link reorders two items by drag, and the new order survives a reload.
- An edit link renames the card and edits the notes; both reach the API.
- A view link cannot: the title is not editable and no `PATCH` reaches the server.

### Vitest

The reorder index math on the public side is the piece worth a unit test, since
it is the one place a wrong number is invisible until two items collide. Follow
the `localSnapshot` seam pattern already used for store-free unit tests (mock
`$checkapi`, drive `reorderItems`, assert the index handed to the `PATCH`).

### Docs

- [docs/screenshots.md](../screenshots.md) has a public-link viewer shot
  that will now show the separated layout. Regenerate it with `gen_screenshots.sh`.
- The public-link section of [docs/administration.md](../administration.md)
  describes what each link level lets a visitor do. Level `check` gaining "and
  collapse the checked section for everyone on the card" is a real change to what
  an administrator is agreeing to when they enable public links, so it belongs
  there in one sentence.
- `CHANGELOG.md` per the existing format, referencing issue #11.

---

## 8. Out of scope, recorded so it is a decision and not an oversight

- Untick all and delete ticked on the public surface (decision 3).
- Uncheck-suggestions while typing (decision 4).
- Labels, reminders, pin, archive, color picking and the share modal on the
  public page: all owner or collaborator surfaces, none of them layout drift.
- Any offline or outbox behaviour for `/p/<token>`. It stays online only.
- Indentation and any other item feature that is not currently rendered on
  either surface.
