import { test, expect, type Page } from "@playwright/test";

// "Send this link to someone without an account" (chunk E6), the field inside
// the public-link block of the owner's ShareModal.
//
// The point of these tests is not that a message goes out (the backend suite
// owns that) but that the *design against the Nextcloud failure mode* actually
// holds in the browser: the field cannot be a first move, the wording separates
// the two audiences, an address on the operator's own domain raises a callout
// whose primary action lands in the collaborator box, and "send link anyway"
// still works. See utils/publicLinkEmail.ts for the failure mode itself.
//
// The E2E backend runs with SHARING_PUBLIC_LINK_EMAIL_ENABLED and one declared
// internal domain (see backend/e2e/start_e2e_server.py). Mail uses the `null`
// transport, so a send is queued and discarded.

// Matches SHARING_INTERNAL_EMAIL_DOMAINS in backend/e2e/start_e2e_server.py.
// Not a `.test` domain: the server validates addresses with email_validator,
// which refuses the special-use TLDs (`test`, `invalid`, `localhost`, …), so a
// send to one would fail for a reason that has nothing to do with this feature.
const INTERNAL_DOMAIN = "internal-e2e.example";

// REQUIRED: the board opens a persistent SSE connection (/api/sync) that blocks
// Playwright teardown if left open. Navigate to about:blank to close it.
test.afterEach(async ({ page }) => {
  await page.goto("about:blank").catch(() => {});
});

test.describe("E6 mailing a public link", () => {
  test.setTimeout(30_000);

  const cleanupChecklists: string[] = [];

  test.afterEach(async ({ page }) => {
    for (const id of cleanupChecklists)
      await page.request.delete(`/api/checklist/${id}`).catch(() => {});
    cleanupChecklists.length = 0;
  });

  async function createCard(page: Page): Promise<{ id: string; title: string }> {
    const tag = Date.now();
    const title = `LinkEmail-${tag}`;
    const cl = await (
      await page.request.post("/api/checklist", {
        data: { name: title },
        headers: { "Content-Type": "application/json" },
      })
    ).json();
    cleanupChecklists.push(cl.id);
    return { id: cl.id, title };
  }

  async function openShareModal(page: Page, title: string) {
    await page.waitForSelector("[data-testid=checklist-board]");
    const card = page.locator(".checklist-preview").filter({ hasText: title });
    await expect(card, "card should be on the board").toBeVisible({ timeout: 8_000 });
    await card.locator("[data-testid=share-button]").click();
    const dialog = page.locator('[role="dialog"]').filter({ hasText: "Share this list" });
    await expect(dialog).toBeVisible({ timeout: 5_000 });
    return dialog;
  }

  test("the field only appears once a public link exists", async ({ page }) => {
    // Placement is the primary defence: because a link has to exist first, this
    // can never be somebody's first move, which is exactly what makes the
    // confusion possible elsewhere.
    const { title } = await createCard(page);
    await page.goto("/");
    const dialog = await openShareModal(page, title);

    await expect(dialog.locator("[data-testid=public-link-email]")).toHaveCount(0);

    await dialog.locator("[data-testid=public-link-create]").click();
    const block = dialog.locator("[data-testid=public-link-email]");
    await expect(block).toBeVisible({ timeout: 5_000 });

    // Named by audience, not by mechanism, and the consequence of each way of
    // sharing is spelled out next to it.
    await expect(block).toContainText("People outside");
    await expect(block).toContainText("Someone with an account");
    await expect(block).toContainText("Someone without an account");
    await expect(block).toContainText("without signing in");

    await page.keyboard.press("Escape");
  });

  test("an outside address goes through a confirm step that states the level", async ({
    page,
  }) => {
    const { title } = await createCard(page);
    await page.goto("/");
    const dialog = await openShareModal(page, title);

    // An edit link, so the confirm line has the strongest thing to say.
    await dialog.locator("[data-testid=public-link-level]").click();
    await page.getByRole("option", { name: "Edit", exact: true }).click();
    await dialog.locator("[data-testid=public-link-create]").click();

    const block = dialog.locator("[data-testid=public-link-email]");
    await expect(block).toBeVisible({ timeout: 5_000 });

    await block.locator("[data-testid=public-link-email-address]").fill("stranger@example.org");
    // No callout for an address that is not on the operator's own domain.
    await expect(block.locator("[data-testid=public-link-email-hint]")).toHaveCount(0);

    await block.locator("[data-testid=public-link-email-send]").click();

    const confirm = block.locator("[data-testid=public-link-email-confirm]");
    await expect(confirm).toBeVisible();
    await expect(confirm).toContainText("stranger@example.org");
    await expect(confirm).toContainText("add, change and tick off items");
    await expect(confirm).toContainText("without signing in");

    await confirm.locator("[data-testid=public-link-email-confirm-send]").click();
    await expect(block.locator("[data-testid=public-link-email-result]")).toContainText(
      "on its way",
      { timeout: 5_000 }
    );
    // The field empties itself, so a second send cannot go out by accident.
    await expect(block.locator("[data-testid=public-link-email-address]")).toHaveValue("");

    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);
    await page.keyboard.press("Escape");
  });

  test("an internal address raises the callout, which lands in the collaborator box", async ({
    page,
  }) => {
    const { title } = await createCard(page);
    await page.goto("/");
    const dialog = await openShareModal(page, title);
    await dialog.locator("[data-testid=public-link-create]").click();

    const block = dialog.locator("[data-testid=public-link-email]");
    await expect(block).toBeVisible({ timeout: 5_000 });

    await block
      .locator("[data-testid=public-link-email-address]")
      .fill(`anna.analyst@${INTERNAL_DOMAIN}`);

    const hint = block.locator("[data-testid=public-link-email-hint]");
    await expect(hint).toBeVisible();
    await expect(hint).toContainText("colleague");
    // While the callout is up, the plain Send button is not the obvious move.
    await expect(block.locator("[data-testid=public-link-email-send]")).toHaveCount(0);

    // The primary action has to actually get somewhere, not just scold: it
    // starts the collaborator search from the address's local part (the search
    // never matches on email, by design).
    await hint.locator("[data-testid=public-link-email-hint-collaborator]").click();
    await expect(dialog.locator("[data-testid=share-user-search]")).toHaveValue(
      "anna.analyst"
    );

    await page.keyboard.press("Escape");
  });

  test("'send link anyway' still works, because sometimes it is the right thing", async ({
    page,
  }) => {
    // Never a hard block: a colleague's private address, or a device they are
    // not signed in on, is a real case.
    const { title } = await createCard(page);
    await page.goto("/");
    const dialog = await openShareModal(page, title);
    await dialog.locator("[data-testid=public-link-create]").click();

    const block = dialog.locator("[data-testid=public-link-email]");
    await expect(block).toBeVisible({ timeout: 5_000 });
    await block
      .locator("[data-testid=public-link-email-address]")
      .fill(`bob@${INTERNAL_DOMAIN}`);

    await block.locator("[data-testid=public-link-email-hint-anyway]").click();
    const confirm = block.locator("[data-testid=public-link-email-confirm]");
    await expect(confirm).toBeVisible();
    await confirm.locator("[data-testid=public-link-email-confirm-send]").click();

    await expect(block.locator("[data-testid=public-link-email-result]")).toContainText(
      "on its way",
      { timeout: 5_000 }
    );
    await expect(page.getByText(/Error 4\d\d/)).toHaveCount(0);

    await page.keyboard.press("Escape");
  });

  test("a typo is caught before anything is queued", async ({ page }) => {
    const { title } = await createCard(page);
    await page.goto("/");
    const dialog = await openShareModal(page, title);
    await dialog.locator("[data-testid=public-link-create]").click();

    const block = dialog.locator("[data-testid=public-link-email]");
    await expect(block).toBeVisible({ timeout: 5_000 });
    await block.locator("[data-testid=public-link-email-address]").fill("anna@example");
    await block.locator("[data-testid=public-link-email-send]").click();

    await expect(block.locator("[data-testid=public-link-email-result]")).toContainText(
      "does not look like an email address"
    );
    await expect(block.locator("[data-testid=public-link-email-confirm]")).toHaveCount(0);

    await page.keyboard.press("Escape");
  });
});
