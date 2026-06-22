# E2E Test Writing Guide (for LLMs)

Everything a new session needs to write a Playwright E2E test for CheckCheck
without reading the rest of the codebase.  Read this file, then write the test.

---

## What the app is

CheckCheck is a Kanban-style checklist app.  The main page (`/`) shows a grid
of checklist **cards**.  Each card has a title, optional notes, and a list of
**items** (checkboxes).  Cards can be reordered by drag-and-drop.  Items inside
a card can also be reordered by drag-and-drop when the card is open in its edit
modal.  Cards can be tagged with **labels**; labels appear in the sidebar and
act as board filters.  A search box in the navbar filters cards in real time.
All mutations are propagated to other open tabs via **SSE** (`GET /api/sync`).

---

## Test infrastructure

| Thing | Value |
|---|---|
| E2E backend + frontend port | **8182** (one port — backend serves both) |
| Base URL in tests | `http://localhost:8182` |
| Admin credentials | `admin3` / `password123` |
| Regular user | `testuser01` / `testuserpw_secure1` |
| Auth saved to | `tests/e2e/.auth/state.json` |
| Run all tests | `cd CheckCheck/frontend && bunx playwright test` |
| Run one file | `cd CheckCheck/frontend && bunx playwright test tests/e2e/card-movement.spec.ts` |
| Run & inspect one test | `./run_e2e_tests.sh --pick` (interactive picker → debug mode) |
| HTML report + traces | `cd CheckCheck/frontend && bunx playwright show-report` |

The test suite builds a static frontend bundle (`nuxt generate`) and lets the
backend serve it — no dev server, no Vite, no HMR.  This is exactly the
production stack.  Traces are recorded for every test run (`trace: "on"`) and
are viewable in the HTML report.

All tests in the `chromium` project start pre-authenticated as `admin3` — no
login step needed.  Tests run **sequentially** (`fullyParallel: false`) and
share the same backend database.  Always clean up data you create in `afterEach`.

---

## File map

```
tests/e2e/
  global-setup.ts        build frontend, start backend on port 8182
  global-teardown.ts     kill backend process group
  auth.setup.ts          log in once, persist admin session
  LLM_GUIDE.md          ← you are here
  auth.spec.ts           login page, error handling, logout
  checklist.spec.ts      board smoke tests (grid, modal, search URL update)
  card-movement.spec.ts  drag-and-drop card reordering
  item-movement.spec.ts  drag-and-drop item reordering (inside edit modal)
  filter-search.spec.ts  label filter, search, combined label+search
  sync.spec.ts           two-context SSE sync
```

---

## DOM selectors — everything you need

```
[data-testid="checklist-board"]    the <ul> grid of cards
.checklist-preview                 each card's <li> wrapper (draggable)
[data-testid="card-title"]          the card title div (click this to open modal)
[role="dialog"]                    the edit modal (UModal)
.list-item-drag-handle             drag handle span inside each item row
                                   (opacity 0.3 until hovered; only in edit mode)
[data-testid="login-username"]     login form username <input>
[data-testid="login-password"]     login form password <input>
[data-testid="login-error"]        login error UAlert
```

Nuxt UI 4 quirks:
- `UModal` → `role="dialog"` ✓
- `UButton` → `<button>` ✓ — use `getByRole("button", { name: "..." })`
- `UInput` search box → `page.getByPlaceholder("Search...")`
- `UCheckbox` → **use `getByRole('checkbox')`**, NOT `locator('[type="checkbox"]')`.
  Nuxt UI 4 hides the native `<input>` behind a styled button with `role="checkbox"`;
  `[type="checkbox"]` finds the invisible native input and `toBeVisible()` fails.
- `UTextarea` in edit mode → items render as `<textarea>` inside `<li>`.
  `getByText()` and `filter({ hasText })` do **not** match textarea *values* (they
  check text content, not the reactive DOM property).  Use `locator("li").nth(n)`
  and `toHaveValue(/pattern/)` instead.

Labels in sidebar — the label name also appears as a badge on any card that
carries it.  Scope sidebar selectors to `<aside>` to avoid strict-mode violations:
```ts
// ✗ may also find the label badge on the card
await page.getByText("MyLabel", { exact: true }).click();

// ✓ scoped to the sidebar nav
await page.locator("aside").getByText("MyLabel", { exact: true }).click();
```

---

## API patterns for test setup / cleanup

`page.request` shares the authenticated browser context (session cookie), so
API calls are automatically authenticated.  The backend is at the same origin
as the page (`http://localhost:8182`), so use relative paths.

```ts
// Create a checklist
const res = await page.request.post("/api/checklist", {
  data: { name: "My Checklist" },
  headers: { "Content-Type": "application/json" },
});
const checklist = await res.json();  // { id, name, position, labels, … }

// Create an item
const itemRes = await page.request.post(`/api/checklist/${checklist.id}/item`, {
  data: { text: "Item text" },
  headers: { "Content-Type": "application/json" },
});
const item = await itemRes.json();  // { id, text, position, state, … }

// Create a label
const labelRes = await page.request.post("/api/label", {
  data: { display_name: "MyLabel" },
  headers: { "Content-Type": "application/json" },
});
const label = await labelRes.json();  // { id, display_name, … }

// Assign label to checklist
await page.request.put(`/api/checklist/${checklist.id}/label/${label.id}`);

// Search for checklists (useful to recover IDs created via UI)
const { items } = await (await page.request.get("/api/checklist", {
  params: { search: "My Checklist", limit: 5 },
})).json();

// Cleanup
await page.request.delete(`/api/checklist/${checklist.id}`);
await page.request.delete(`/api/label/${label.id}`);
```

**Full API path list (E2E-relevant):**
```
POST   /api/checklist
GET    /api/checklist                  ?search=&label_id=&offset=&limit=&archived=
PATCH  /api/checklist/{id}
DELETE /api/checklist/{id}
PUT    /api/checklist/{id}/move/above/{other_id}
PUT    /api/checklist/{id}/move/under/{other_id}

POST   /api/checklist/{id}/item
PATCH  /api/checklist/{id}/item/{item_id}
DELETE /api/checklist/{id}/item/{item_id}
PATCH  /api/checklist/{id}/item/{item_id}/state   body: { checked: bool }
PUT    /api/checklist/{id}/item/{item_id}/move/above/{other_item_id}
PUT    /api/checklist/{id}/item/{item_id}/move/under/{other_item_id}

POST   /api/label
DELETE /api/label/{label_id}
PUT    /api/checklist/{id}/label/{label_id}        assign label to checklist
DELETE /api/checklist/{id}/label/{label_id}        remove label
```

---

## Data sorting rules (critical for order assertions)

**Cards (checklists)** sort **descending** by `position.index`.
→ The most recently created card has the highest index and appears **first** on the board.

**Items** sort **ascending** by `position.index`.
→ First item created → lowest index → appears **first** (top).
→ Last item created → highest index → appears **last** (bottom).

```ts
// Create card A then B → B appears BEFORE A on the board.
const clA = await apiPost(page, "/api/checklist", { name: nameA });
const clB = await apiPost(page, "/api/checklist", { name: nameB });
// Board: [B, A, …]

// Create item 1 then 2 → item 1 appears ABOVE item 2.
await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "Item 1" });
await apiPost(page, `/api/checklist/${cl.id}/item`, { text: "Item 2" });
// Modal: [Item 1, Item 2]
```

---

## Filtering — how it works

**Label-only filter** (no search active):
URL: `?label=<label_id>` — filtering is **client-side** in the Pinia store.
The store already has checklists from `fetchNextPage()`.  New checklists have
the highest index → land in the first batch → client-side filter works.

**Search** (text active, label optional):
URL: `?search=<query>` or `?search=<query>&label=<id>` — 300 ms debounced
watcher fires `GET /api/checklist?search=<query>[&label_id=<id>]` (server-side).
Wait for the URL to update before asserting results:
```ts
await expect(page).toHaveURL(/search=/, { timeout: 2_000 });
```

**Combined label + search:** click sidebar label first (sets `?label=<id>`),
then type in search box.  The watcher picks up both params, sends one combined
server-side request.

---

## Drag-and-drop implementation details

Both card and item reordering use `@formkit/drag-and-drop` (v0.5.x) which
listens for **`pointerdown` + `pointermove`**.

**Cards (board):**
- Drag target: `<li class="checklist-preview">` (the whole li, no separate handle).
- Use a **narrow viewport (420 px)** to collapse the grid to a single column:
  ```ts
  await page.setViewportSize({ width: 420, height: 900 });
  ```

**Items (inside the edit modal):**
- Drag handle: `.list-item-drag-handle` span (only in edit mode, opacity 0.3
  until hovered).  Always `hover()` the row first.

**Reliable drag helper** — don't use `dragTo()`; use the pointer API:
```ts
async function drag(page, source, target, targetYFraction = 0.8) {
  const src = await source.boundingBox();
  const tgt = await target.boundingBox();
  await page.mouse.move(src.x + src.width / 2, src.y + src.height / 2);
  await page.mouse.down();
  await page.mouse.move(src.x + src.width / 2 + 2, src.y + src.height / 2 + 6, { steps: 5 });
  await page.mouse.move(tgt.x + tgt.width / 2, tgt.y + tgt.height * targetYFraction, { steps: 30 });
  await page.mouse.up();
}
```

`targetYFraction = 0.8` → drop AFTER the target.  `0.2` → drop BEFORE.

After a drag always wait for the move API:
```ts
const moved = page.waitForResponse(
  r => r.url().includes("/move/") && r.request().method() === "PUT",
  { timeout: 10_000 }
);
await drag(page, source, target);
await moved;
```

---

## SSE sync (two-context tests)

The board opens `GET /api/sync` (EventSource) on mount.  Events:

| `upd_prop` | What the store does |
|---|---|
| `checklist_created` | `checkListStore.refresh(clId)` |
| `checklist_deleted` | removes from store |
| `checklist` / `checklist_position` / `checklist_label` | `checkListStore.refresh(clId)` |
| `item_state` | `itemStore.refreshState(clId, cliId)` |
| `item_text` | `itemStore.refresh(clId, cliId)` |
| `item_position` / `item_created` | debounced `itemStore.refreshAllCheckListItems(clId)` |
| `item_deleted` | removes item from store |

Open the second context using the saved auth file:
```ts
import { resolve } from "path";
const AUTH_FILE = resolve(__dirname, ".auth/state.json");

test("sync test", async ({ page, context }) => {
  await page.goto("/");
  await page.waitForSelector("[data-testid=checklist-board]");

  const ctx2 = await context.browser()!.newContext({ storageState: AUTH_FILE });
  const page2 = await ctx2.newPage();
  await page2.goto("/");
  await page2.waitForSelector("[data-testid=checklist-board]");

  // … action on page, assert on page2 …

  // afterEach closes ctx2 — see SSE cleanup note below
});
```

SSE events arrive within ~400 ms.  Use `expect` with a generous timeout:
```ts
await expect(page2.getByText(newName, { exact: true })).toBeVisible({ timeout: 8_000 });
```

---

## Boilerplate for a new spec file

```ts
import { test, expect, type Page } from "@playwright/test";

// REQUIRED in every spec that navigates to "/": the board opens an SSE
// connection (/api/sync) that blocks Playwright's browser teardown if still
// open.  Navigating to about:blank closes it cleanly.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("feature name", () => {
  // Set a tight timeout so failures surface quickly (default 30 s can stack).
  test.setTimeout(15_000); // use 25_000 for DnD / SSE tests

  const cleanupChecklists: string[] = [];
  const cleanupLabels: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    for (const id of cleanupLabels)
      await page.request.delete(`/api/label/${id}`).catch(() => {});
    cleanupChecklists.length = 0;
    cleanupLabels.length = 0;
  });

  test("does the thing", async ({ page }) => {
    const tag = Date.now();  // unique suffix to avoid cross-test collisions

    // 1. Set up test data via API
    const res = await page.request.post("/api/checklist", {
      data: { name: `MyTest-${tag}` },
      headers: { "Content-Type": "application/json" },
    });
    const cl = await res.json();
    cleanupChecklists.push(cl.id);

    // 2. Navigate to the board
    await page.goto("/");
    await page.waitForSelector("[data-testid=checklist-board]");

    // 3. Interact and assert
    await expect(page.getByText(`MyTest-${tag}`, { exact: true })).toBeVisible();
  });
});
```

---

## Gotchas

**Every spec that visits `/` must have a file-level `afterEach` navigating to
`about:blank`.**  The board opens a persistent SSE connection (`/api/sync`).
Playwright's browser teardown waits for all HTTP connections to settle — the
backend never closes SSE proactively, so the runner hangs indefinitely without
this.  Put it at file level (outside all `describe` blocks) so it applies to
every test in the file:
```ts
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});
```

**Never `await` closing a second context that holds an SSE connection.**
`await context.close()` blocks forever on an open EventSource.  Navigate the
context's pages to `about:blank` first (triggers browser-level connection abort),
then close:
```ts
// In afterEach for two-context tests:
await Promise.all(secondCtx.pages().map(p => p.goto("about:blank").catch(() => {})));
await secondCtx.close().catch(() => {});
secondCtx = null;
```

**`UCheckbox` hides the native `<input>` — use `getByRole('checkbox')`.**
```ts
// ✗ finds the hidden native input → toBeVisible() fails
card.locator('[type="checkbox"]').first()

// ✓ finds the visible ARIA wrapper
card.getByRole('checkbox').first()
```

**`UCheckbox` v-model + `@click.stop` double-flip bug — don't click to drive
SSE state tests.**  `CheckListItem.vue` has both `v-model="item.state.checked"`
and `@click.stop="toggleCheck()"` on the same `UCheckbox`.  When clicked,
`v-model` flips `state.checked` *synchronously* before `toggleCheck()` runs, so
`toggleCheck()` reads `!state.checked` = the *original* value and sends that to
the backend — a no-op that produces no SSE notification.  To test that a state
change propagates via SSE, drive it through the API instead:
```ts
await page.request.patch(
  `/api/checklist/${cl.id}/item/${item.id}/state`,
  { data: { checked: true }, headers: { "Content-Type": "application/json" } }
);
// Then assert the checkbox disappears from the unchecked list (filter removes it)
await expect(previewCheckbox2).not.toBeVisible({ timeout: 8_000 });
```

**`getByText` / `hasText` do NOT match `<textarea>` values in edit mode.**
Items in the edit modal render as `<textarea>` elements (`v-model` sets the DOM
property, not text content).  Always use index-based locators:
```ts
// ✗ won't find the item
dialog.locator("li").filter({ hasText: "My item text" })

// ✓ use nth() and toHaveValue
const row = dialog.locator("li").nth(0);
await expect(dialog.locator("li textarea").nth(0)).toHaveValue(/My item text/);
```

**`getByText` without `exact: true` does substring matching.**
`page.getByText("Label-123")` also matches `"WithLabel-123"` and `"NoLabel-123"`.
Strict mode throws if multiple elements match.  Always pass `{ exact: true }`.

**Label name appears in both the sidebar AND as a badge on labeled cards.**
When `getByText(labelName, { exact: true })` finds both, it throws a strict-mode
violation.  Scope sidebar lookups to `<aside>`:
```ts
await page.locator("aside").getByText(labelName, { exact: true }).click();
```

**Card clicks — never click the card center when it has items.**
Preview-mode items include a checkbox with `@click.stop`.  A center click lands
on the checkbox and the edit modal never opens.  Click the title instead:
```ts
await card.locator("[data-testid=card-title]").click();
```

**Open tab 1 fully before opening tab 2 in sync tests.**
`GET /api/item` (the multi-checklist preview endpoint) does an inner-join on
`CheckListPosition`.  Two tabs calling it concurrently for a freshly-created
checklist can race the SQLite write and one request returns 403/500.  Wait until
tab 1's item preview checkbox is visible before opening tab 2.

**Backend race condition — item position may be `None` briefly after creation.**
`POST /api/checklist/{id}/item` sometimes commits the position record slightly
after returning 200.  Poll before navigating if any concurrent tab will also hit
`GET /api/item`:
```ts
await expect.poll(
  async () => (await page.request.get(`/api/checklist/${cl.id}/item`)).ok(),
  { timeout: 4_000, intervals: [100, 200, 400] }
).toBeTruthy();
await page.goto("/");
```

**Backend bug — `GET /api/item` used to crash on 403 with `TypeError: 'int'
object is not callable`.**  This was fixed (the incorrect `status.HTTP_403_FORBIDDEN(...)`
call was corrected to `status_code=status.HTTP_403_FORBIDDEN`).  The bug is now
a real 403 response, not a 500 crash.

**Items are only in edit mode inside the open modal.**
`CheckListItemCollection` (with drag handles and textareas) only renders when
`editModeActive=true`.  To interact with items, click the card title first:
```ts
await card.locator("[data-testid=card-title]").click();
const dialog = page.locator('[role="dialog"]');
await expect(dialog).toBeVisible({ timeout: 5_000 });
```
Close with `await page.keyboard.press("Escape")`.

**Item drag handle is low-opacity until hover.**  Always `hover()` the row:
```ts
await item1Row.hover();
await expect(item1Row.locator(".list-item-drag-handle")).toBeVisible({ timeout: 3_000 });
```

**Search debounce is 300 ms.**  After `fill(...)`, wait for the URL:
```ts
await expect(page).toHaveURL(/search=/, { timeout: 2_000 });
```

**Label filter is client-side (no search active).**  Clicking a sidebar label
does NOT re-fetch — it filters the already-loaded store.  Your test checklist
must be in the first batch (limit 5).  New checklists always land there.

**Set `test.setTimeout` per describe to fail fast.**
```ts
test.describe("DnD tests", () => {
  test.setTimeout(25_000);  // default 30 s stacks assertion timeouts
  // ...
});
```
