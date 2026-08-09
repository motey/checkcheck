import { expect, type Page } from "@playwright/test";

/**
 * Wait until this page is genuinely subscribed to the sync stream, i.e. a change
 * made elsewhere from now on WILL be poked to it.
 *
 * Waiting on the `GET /api/sync` response is not enough: response headers are
 * written before the server has added the client to its fan-out list, and pokes
 * are never redelivered, so a change published in that window is lost for good
 * (docs/plans/E2E_STABILITY.md, bug B4). The server now sends a `ready` message
 * as the first thing on the stream, after registering the client; the frontend
 * mirrors that onto the navbar chip as `data-sync-live`.
 *
 * Call this AFTER `page.goto(...)` (unlike a response listener, the attribute is
 * a state, not an event, so it cannot be missed by arming it too late).
 */
export async function waitForSyncLive(page: Page, timeout = 20_000): Promise<void> {
  await expect(page.locator('[data-testid=sync-status-chip][data-sync-live="true"]')).toBeAttached({
    timeout,
  });
}
