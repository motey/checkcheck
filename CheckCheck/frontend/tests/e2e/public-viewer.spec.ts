import {
  test,
  expect,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

// Public/anonymous viewer page (Frontend Phase F4) — `pages/p/[token].vue`.
//
// A logged-out visitor opens `/p/<token>` and sees the card at the link's
// permission level, with live updates, an optional passphrase unlock, and a
// "log in to add to my deck" join. The page owns ALL 4xx handling: plugins/api.ts
// skips the global "Error 4xx" toast + the 401→/login redirect for /api/public
// requests, so a bad/locked link must surface the viewer's own state with ZERO
// error toasts and NO automatic /login bounce.
//
// Setup uses the admin (`page`, pre-authenticated) request context to mint cards
// + public links via the API; the anonymous viewer runs in a FRESH context with
// no storageState. The join-while-logged-in case reuses the testuser01 login
// pattern from sharing-modal.spec.ts.

const TEST_USER = { username: "testuser01", password: "testuserpw_secure1" };

// REQUIRED: every open page holds a live SSE (/api/sync) that blocks teardown.
// Navigate the admin page away; anonymous contexts are torn down per-test below.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("F4 public viewer", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];
  const anonContexts: BrowserContext[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanupChecklists.length = 0;

    // Close anonymous/second contexts — navigate their pages away first so the
    // live SSE doesn't block close().
    for (const ctx of anonContexts) {
      await Promise.all(ctx.pages().map((p) => p.goto("about:blank").catch(() => {})));
      await ctx.close().catch(() => {});
    }
    anonContexts.length = 0;
  });

  // Admin creates a card (one item) and a public link at `level`. Returns the
  // card id/title, the one-time token, and the seed item text.
  async function createSharedLink(
    page: Page,
    level: "view" | "check" | "edit",
    opts: { password?: string; expires_at?: string } = {}
  ): Promise<{ id: string; title: string; token: string; itemText: string }> {
    const tag = Date.now() + Math.floor(Math.random() * 1000);
    const title = `Public-${level}-${tag}`;
    const itemText = `Item-${tag}`;

    const cl = await (
      await page.request.post("/api/checklist", {
        data: { name: title },
        headers: { "Content-Type": "application/json" },
      })
    ).json();
    cleanupChecklists.push(cl.id);

    await page.request.post(`/api/checklist/${cl.id}/item`, {
      data: { text: itemText },
      headers: { "Content-Type": "application/json" },
    });

    const linkRes = await page.request.post(`/api/checklist/${cl.id}/public-links`, {
      data: { permission: level, ...opts },
      headers: { "Content-Type": "application/json" },
    });
    expect(linkRes.ok(), "creating the public link should succeed").toBeTruthy();
    const link = await linkRes.json();
    expect(link.token, "create response should carry the one-time token").toBeTruthy();

    return { id: cl.id, title, token: link.token, itemText };
  }

  async function openAnon(browser: Browser, token: string): Promise<Page> {
    const ctx = await browser.newContext(); // NO storageState → logged-out visitor
    anonContexts.push(ctx);
    const anon = await ctx.newPage();
    await anon.goto(`/p/${token}`);
    return anon;
  }

  // ── card / item parity with the authed open card (issue #11) ───────────────

  /**
   * Admin creates a card whose items are given as [text, checked] pairs, plus a
   * public link at `level`. `collapsed` seeds the card's `checked_items_collapsed`
   * (a new card defaults to collapsed).
   */
  async function createCardWithItems(
    page: Page,
    level: "view" | "check" | "edit",
    items: [string, boolean][],
    opts: { collapsed?: boolean } = {}
  ): Promise<{ id: string; title: string; token: string }> {
    const tag = Date.now() + Math.floor(Math.random() * 1000);
    const title = `Public-parity-${level}-${tag}`;

    const cl = await (
      await page.request.post("/api/checklist", {
        data: { name: title },
        headers: { "Content-Type": "application/json" },
      })
    ).json();
    cleanupChecklists.push(cl.id);

    if (opts.collapsed !== undefined) {
      await page.request.patch(`/api/checklist/${cl.id}`, {
        data: { checked_items_collapsed: opts.collapsed },
        headers: { "Content-Type": "application/json" },
      });
    }

    for (const [text, checked] of items) {
      const item = await (
        await page.request.post(`/api/checklist/${cl.id}/item`, {
          data: { text },
          headers: { "Content-Type": "application/json" },
        })
      ).json();
      if (checked) {
        await page.request.patch(`/api/checklist/${cl.id}/item/${item.id}/state`, {
          data: { checked: true },
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    const link = await (
      await page.request.post(`/api/checklist/${cl.id}/public-links`, {
        data: { permission: level },
        headers: { "Content-Type": "application/json" },
      })
    ).json();

    return { id: cl.id, title, token: link.token };
  }

  async function readCard(page: Page, id: string): Promise<any> {
    return await (await page.request.get(`/api/checklist/${id}`)).json();
  }

  /** Ordered item texts of a card, straight from the API (source of truth). */
  async function apiItemOrder(page: Page, id: string): Promise<string[]> {
    const res = await (
      await page.request.get(`/api/checklist/${id}/item`, { params: { limit: 999 } })
    ).json();
    return [...res.items]
      .sort((a: any, b: any) => a.position.index - b.position.index)
      .map((i: any) => i.text);
  }

  /**
   * Drag via low-level pointer events to reliably pass @formkit/drag-and-drop's
   * activation threshold (copied from item-movement.spec.ts, which drags the same
   * shared list). targetYFraction 0.8 = "release 80% down" = drop after.
   */
  async function drag(page: Page, source: Locator, target: Locator, targetYFraction = 0.8) {
    const srcBox = await source.boundingBox();
    const tgtBox = await target.boundingBox();
    if (!srcBox || !tgtBox) throw new Error("Could not read bounding boxes for drag");
    const srcX = srcBox.x + srcBox.width / 2;
    const srcY = srcBox.y + srcBox.height / 2;
    const tgtX = tgtBox.x + tgtBox.width / 2;
    const tgtY = tgtBox.y + tgtBox.height * targetYFraction;
    await page.mouse.move(srcX, srcY);
    await page.mouse.down();
    await page.mouse.move(srcX + 2, srcY + 6, { steps: 5 });
    await page.mouse.move(tgtX, tgtY, { steps: 30 });
    await page.mouse.up();
  }

  test("a view link renders checked items in their own section, not inline", async ({
    page,
    browser,
  }) => {
    const { token } = await createCardWithItems(
      page,
      "view",
      [
        ["Unticked-item", false],
        ["Ticked-item", true],
      ],
      { collapsed: false }
    );

    const anon = await openAnon(browser, token);
    await expect(anon.locator("[data-testid=public-card]")).toBeVisible({ timeout: 5_000 });

    // The separator header counts the checked items: the layout the issue reported
    // missing from the public link entirely.
    await expect(anon.locator("[data-testid=editor-checked-section]")).toContainText(
      "1 checked items"
    );

    // The ticked item is under the separator, NOT inline with the unticked one.
    const unchecked = anon.locator("[data-testid=public-unchecked-items]");
    const checked = anon.locator("[data-testid=public-checked-items]");
    await expect(unchecked).toContainText("Unticked-item");
    await expect(unchecked).not.toContainText("Ticked-item");
    await expect(checked).toContainText("Ticked-item");
  });

  test("a view link can collapse the checked section for itself only, and it survives a reload", async ({
    page,
    browser,
  }) => {
    const { id, token } = await createCardWithItems(
      page,
      "view",
      [
        ["Unticked-item", false],
        ["Ticked-item", true],
      ],
      { collapsed: false }
    );

    const anon = await openAnon(browser, token);
    const header = anon.locator("[data-testid=editor-checked-section]");
    const checked = anon.locator("[data-testid=public-checked-items]");
    await expect(checked).toBeVisible({ timeout: 5_000 });

    await header.click();
    await expect(checked).toBeHidden();

    // sessionStorage keeps it collapsed across a reload of this tab.
    await anon.reload();
    await expect(anon.locator("[data-testid=public-card]")).toBeVisible({ timeout: 5_000 });
    await expect(anon.locator("[data-testid=public-checked-items]")).toBeHidden();

    // ...but a view link grants no write path, so the owner's card is untouched.
    // Asserted through the API: the UI would look identical either way.
    expect((await readCard(page, id)).checked_items_collapsed).toBe(false);
  });

  test("a check link's collapse toggle persists on the card", async ({ page, browser }) => {
    const { id, token } = await createCardWithItems(
      page,
      "check",
      [
        ["Unticked-item", false],
        ["Ticked-item", true],
      ],
      { collapsed: false }
    );

    const anon = await openAnon(browser, token);
    const checked = anon.locator("[data-testid=public-checked-items]");
    await expect(checked).toBeVisible({ timeout: 5_000 });

    await anon.locator("[data-testid=editor-checked-section]").click();
    await expect(checked).toBeHidden();

    // `checked_items_collapsed` is a column on the card, not per-user, so a
    // check-or-better link writes it for everyone (see the plan's decision 2).
    await expect
      .poll(async () => (await readCard(page, id)).checked_items_collapsed, {
        timeout: 5_000,
      })
      .toBe(true);
  });

  test("an edit link reorders items by drag and the new order survives a reload", async ({
    page,
    browser,
  }) => {
    const { id, token } = await createCardWithItems(page, "edit", [
      ["Alpha-item", false],
      ["Beta-item", false],
    ]);

    const anon = await openAnon(browser, token);
    const rows = anon.locator("[data-testid=public-unchecked-items] [data-testid=item-row]");
    await expect(rows).toHaveCount(2, { timeout: 5_000 });
    expect(await apiItemOrder(page, id)).toEqual(["Alpha-item", "Beta-item"]);

    // Drag Alpha below Beta by its handle (the shared list's `.list-item-drag-handle`).
    await drag(
      anon,
      rows.nth(0).locator(".list-item-drag-handle"),
      rows.nth(1),
      0.9
    );

    await expect
      .poll(() => apiItemOrder(page, id), { timeout: 8_000 })
      .toEqual(["Beta-item", "Alpha-item"]);

    // The client-computed fractional index is what the server stored, so a reload
    // of the viewer comes back in the same order.
    await anon.reload();
    const reloaded = anon.locator(
      "[data-testid=public-unchecked-items] [data-testid=item-text-rendered]"
    );
    await expect(reloaded.nth(0)).toContainText("Beta-item", { timeout: 5_000 });
    await expect(reloaded.nth(1)).toContainText("Alpha-item");
  });

  test("an edit link can rename the card and rewrite its notes", async ({ page, browser }) => {
    const { id, token } = await createCardWithItems(page, "edit", [["Some-item", false]]);

    const anon = await openAnon(browser, token);
    const nameField = anon.locator("[data-testid=public-card-name]");
    await expect(nameField).toBeVisible({ timeout: 5_000 });

    await nameField.fill("Renamed by a visitor");
    // Focus-swap notes: click the rendered region to get the raw textarea.
    await anon.locator("[data-testid=card-notes-rendered]").click();
    await anon.locator("[data-testid=card-notes-textarea]").fill("Notes by a visitor");

    await expect
      .poll(async () => (await readCard(page, id)).name, { timeout: 8_000 })
      .toBe("Renamed by a visitor");
    await expect
      .poll(async () => (await readCard(page, id)).text, { timeout: 8_000 })
      .toBe("Notes by a visitor");
  });

  test("a view link cannot edit the card: no title field and no PATCH goes out", async ({
    page,
    browser,
  }) => {
    const { id, title, token } = await createCardWithItems(page, "view", [
      ["Some-item", false],
    ]);

    const anon = await openAnon(browser, token);

    // Record every card PATCH the page attempts.
    const cardPatches: string[] = [];
    anon.on("request", (req) => {
      if (req.method() === "PATCH" && req.url().includes(`/api/public/checklist/${token}`))
        cardPatches.push(req.url());
    });

    // The title is a plain heading, not a field, and the notes are not editable.
    await expect(anon.locator("[data-testid=public-card-name]")).toHaveText(title);
    await expect(anon.locator("[data-testid=public-card-name] textarea")).toHaveCount(0);
    await expect(anon.locator("[data-testid=card-notes-textarea]")).toHaveCount(0);
    await expect(anon.locator("[data-testid=markdown-help-trigger]")).toHaveCount(0);

    // Collapsing is local-only, so even that must not PATCH.
    await anon.locator("[data-testid=editor-checked-section]").click();
    await anon.waitForTimeout(1_000);
    expect(cardPatches, "a view link must never PATCH the card").toEqual([]);
    expect((await readCard(page, id)).name).toBe(title);
  });

  test("view-level link renders read-only for an anonymous visitor", async ({ page, browser }) => {
    const { token, title, itemText } = await createSharedLink(page, "view");

    const anon = await openAnon(browser, token);

    // Card + items render standalone (no board chrome).
    await expect(anon.locator("[data-testid=public-card-name]")).toHaveText(title);
    await expect(anon.locator("[data-testid=public-items]")).toContainText(itemText);

    // Read-only: checkbox disabled, no editable text, no add affordance.
    await expect(anon.locator('[role="checkbox"]').first()).toBeDisabled();
    await expect(anon.locator("[data-testid=public-item-text]")).toHaveCount(0);
    await expect(anon.locator("[data-testid=public-add-item]")).toHaveCount(0);

    // No global error toast slipped through for the public surface.
    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("check-level link lets an anonymous visitor tick an item", async ({ page, browser }) => {
    const { token } = await createSharedLink(page, "check");

    const anon = await openAnon(browser, token);

    const checkbox = anon.locator('[role="checkbox"]').first();
    await expect(checkbox).toBeEnabled();
    // No edit affordances at the check level.
    await expect(anon.locator("[data-testid=public-add-item]")).toHaveCount(0);

    await checkbox.click();
    await expect(checkbox).toBeChecked({ timeout: 5_000 });

    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("edit-level link lets an anonymous visitor add an item", async ({ page, browser }) => {
    const { token } = await createSharedLink(page, "edit");

    const anon = await openAnon(browser, token);

    // Edit affordances present; text rows are editable textareas.
    await expect(anon.locator('[role="checkbox"]').first()).toBeEnabled();
    await expect(anon.locator("[data-testid=public-item-text]")).toHaveCount(1);

    await anon.locator("[data-testid=public-add-item]").click();
    await expect(anon.locator("[data-testid=public-item-text]")).toHaveCount(2, { timeout: 5_000 });

    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("a bad/disabled token shows the locked branch — no toast, no /login bounce", async ({
    browser,
  }) => {
    const ctx = await browser.newContext();
    anonContexts.push(ctx);
    const anon = await ctx.newPage();
    await anon.goto("/p/this-token-does-not-exist-1234567890");

    // The locked/passphrase branch is shown (can't distinguish bad from protected).
    await expect(anon.locator("[data-testid=public-locked]")).toBeVisible({ timeout: 5_000 });

    // CRITICAL: zero "Error 4xx" toasts and NO bounce to /login.
    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
    expect(anon.url()).toContain("/p/this-token-does-not-exist");
    expect(anon.url()).not.toContain("/login");
  });

  test("password-protected link: wrong passphrase fails, right one unlocks", async ({
    page,
    browser,
  }) => {
    const { token, title } = await createSharedLink(page, "view", { password: "hunter2" });

    const anon = await openAnon(browser, token);

    // Protected → same 404 as a bad link → passphrase form.
    await expect(anon.locator("[data-testid=public-locked]")).toBeVisible({ timeout: 5_000 });

    // Wrong passphrase → error, still locked.
    await anon.locator("[data-testid=public-passphrase]").fill("wrong-pass");
    await anon.locator("[data-testid=public-unlock-submit]").click();
    await expect(anon.locator("[data-testid=public-unlock-error]")).toBeVisible({ timeout: 5_000 });
    await expect(anon.locator("[data-testid=public-card]")).toHaveCount(0);

    // Right passphrase → card loads.
    await anon.locator("[data-testid=public-passphrase]").fill("hunter2");
    await anon.locator("[data-testid=public-unlock-submit]").click();
    await expect(anon.locator("[data-testid=public-card-name]")).toHaveText(title, {
      timeout: 5_000,
    });

    await expect(anon.getByText(/Error 4\d\d/)).toHaveCount(0);
  });

  test("live update: an item added by the owner appears on the open viewer", async ({
    page,
    browser,
  }) => {
    const { id, token } = await createSharedLink(page, "view");

    const anon = await openAnon(browser, token);
    await expect(anon.locator("[data-testid=public-card]")).toBeVisible({ timeout: 5_000 });

    // Owner adds an item via the authed API → SSE fans out to the anon viewer.
    const liveText = `Live-${Date.now()}`;
    await page.request.post(`/api/checklist/${id}/item`, {
      data: { text: liveText },
      headers: { "Content-Type": "application/json" },
    });

    await expect(anon.locator("[data-testid=public-items]")).toContainText(liveText, {
      timeout: 8_000,
    });
  });

  test("join while logged in adds the card and navigates to /card/<id>", async ({
    page,
    browser,
  }) => {
    const { id, token } = await createSharedLink(page, "view");

    // Log in as a non-owner (testuser01) in a fresh context.
    const ctx = await browser.newContext();
    anonContexts.push(ctx);
    const userPage = await ctx.newPage();
    await userPage.goto("/login");
    await userPage.waitForSelector("form");
    await userPage.locator("[data-testid=login-username]").fill(TEST_USER.username);
    await userPage.locator("[data-testid=login-password]").fill(TEST_USER.password);
    await userPage.locator('form button[type="submit"]').click();
    await userPage.waitForURL("/");

    await userPage.goto(`/p/${token}`);
    await expect(userPage.locator("[data-testid=public-card]")).toBeVisible({ timeout: 5_000 });

    await userPage.locator("[data-testid=public-join]").click();

    // Logged-in join → real collaborator added → navigate into the main app.
    await userPage.waitForURL(`**/card/${id}`, { timeout: 8_000 });
    expect(userPage.url()).toContain(`/card/${id}`);
  });
});
