import { describe, it, expect, vi } from "vitest";
import {
  handleNotificationDeepLink,
  looksLikeCardId,
  parseNotificationDeepLink,
  type DeepLinkQuery,
} from "@/utils/notificationDeepLink";

// The email deep link `/?card=<cl_id>&n=<notification_id>` (chunk E5): the query
// parameters a notification message carries, and what the client does with them.

/** A real card id shape: `?card=` only routes for something that could be one. */
const CARD_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301";

describe("parseNotificationDeepLink", () => {
  it("reads both parameters", () => {
    const link = parseNotificationDeepLink({ card: "cl-1", n: "notif-1" });
    expect(link.cardId).toBe("cl-1");
    expect(link.notificationId).toBe("notif-1");
  });

  it("keeps n on the redirect and drops both once consumed", () => {
    const link = parseNotificationDeepLink({ card: "cl-1", n: "notif-1", search: "milk" });
    expect(link.cardQuery).toEqual({ n: "notif-1", search: "milk" });
    expect(link.strippedQuery).toEqual({ search: "milk" });
  });

  it("ignores empty, blank and absent values", () => {
    expect(parseNotificationDeepLink({}).cardId).toBeNull();
    expect(parseNotificationDeepLink({ card: "", n: "  " }).cardId).toBeNull();
    expect(parseNotificationDeepLink({ n: "  " }).notificationId).toBeNull();
    expect(parseNotificationDeepLink(null).notificationId).toBeNull();
    expect(parseNotificationDeepLink({ n: null }).notificationId).toBeNull();
  });

  it("takes the first value of a repeated parameter", () => {
    // `?n=a&n=b` is a malformed link, not two notifications.
    expect(parseNotificationDeepLink({ n: ["a", "b"] }).notificationId).toBe("a");
    expect(parseNotificationDeepLink({ n: [null, "b"] }).notificationId).toBe("b");
  });
});

describe("handleNotificationDeepLink", () => {
  function context(
    query: DeepLinkQuery,
    overrides: Partial<Parameters<typeof handleNotificationDeepLink>[0]> = {}
  ) {
    const url = { path: "/", query };
    const replace = vi.fn(async (to: { path: string; query: DeepLinkQuery }) => {
      url.path = to.path;
      url.query = to.query;
    });
    return {
      url,
      replace,
      ctx: {
        current: () => url,
        hasCard: false,
        waitForCard: vi.fn(async () => {}),
        markRead: vi.fn(async () => {}),
        replace,
        handled: new Set<string>(),
        ...overrides,
      },
    };
  }

  it("turns ?card= into the app's card route, keeping n for the next pass", async () => {
    const { ctx, replace, url } = context({ card: CARD_ID, n: "notif-1" });
    const outcome = await handleNotificationDeepLink(ctx);
    expect(outcome).toBe("redirected");
    expect(replace).toHaveBeenCalledWith({ path: `/card/${CARD_ID}`, query: { n: "notif-1" } });
    // Nothing is marked read on the redirect pass: the card is not on screen yet.
    expect(ctx.markRead).not.toHaveBeenCalled();
    expect(url.query).toEqual({ n: "notif-1" });
  });

  // Finding 10: `card` is whatever the URL carried, and it reaches a router
  // path. A crafted value cannot leave the origin, but it must not produce a
  // route either: the user stays on the page the link landed them on.
  it("drops a ?card= value that cannot be a card id, leaving the user on the board", async () => {
    const { ctx, replace, url } = context({ card: "../../admin", n: "notif-1" });
    const outcome = await handleNotificationDeepLink(ctx);
    expect(outcome).toBe("redirected");
    expect(replace).toHaveBeenCalledWith({ path: "/", query: { n: "notif-1" } });
    expect(url.path).toBe("/");
    // And `n` survives, so the second pass still marks the notification read.
    expect(await handleNotificationDeepLink(ctx)).toBe("marked");
    expect(ctx.markRead).toHaveBeenCalledWith("notif-1");
  });

  it("marks the notification read after the card rendered, then strips n", async () => {
    const { ctx, replace } = context({ n: "notif-1" }, { hasCard: true });
    const outcome = await handleNotificationDeepLink({ ...ctx, current: ctx.current });
    expect(outcome).toBe("marked");
    expect(ctx.waitForCard).toHaveBeenCalledTimes(1);
    expect(ctx.markRead).toHaveBeenCalledWith("notif-1");
    expect(replace).toHaveBeenCalledWith({ path: "/", query: {} });
  });

  it("marks read in the right order: never before the card is on screen", async () => {
    const order: string[] = [];
    const { ctx } = context(
      { n: "notif-1" },
      {
        hasCard: true,
        waitForCard: vi.fn(async () => {
          order.push("rendered");
        }),
        markRead: vi.fn(async () => {
          order.push("read");
        }),
      }
    );
    await handleNotificationDeepLink(ctx);
    expect(order).toEqual(["rendered", "read"]);
  });

  it("does not wait when the link carries no card", async () => {
    const { ctx } = context({ n: "notif-1" });
    await handleNotificationDeepLink(ctx);
    expect(ctx.waitForCard).not.toHaveBeenCalled();
    expect(ctx.markRead).toHaveBeenCalledWith("notif-1");
  });

  it("still strips n when marking read fails", async () => {
    const { ctx, replace } = context(
      { n: "notif-1" },
      {
        markRead: vi.fn(async () => {
          throw new Error("offline");
        }),
      }
    );
    // No retry loop and no error UI: the badge reconciles from the server.
    expect(await handleNotificationDeepLink(ctx)).toBe("mark-failed");
    expect(replace).toHaveBeenCalledWith({ path: "/", query: {} });
  });

  it("consumes each notification once, however often it runs", async () => {
    const handled = new Set<string>();
    const first = context({ n: "notif-1" }, { handled });
    expect(await handleNotificationDeepLink(first.ctx)).toBe("marked");
    const second = context({ n: "notif-1" }, { handled });
    expect(await handleNotificationDeepLink(second.ctx)).toBe("already-handled");
    expect(second.ctx.markRead).not.toHaveBeenCalled();
  });

  it("does nothing for a URL without either parameter", async () => {
    const { ctx, replace } = context({ search: "milk" });
    expect(await handleNotificationDeepLink(ctx)).toBe("none");
    expect(replace).not.toHaveBeenCalled();
    expect(ctx.markRead).not.toHaveBeenCalled();
  });

  it("leaves the URL alone when the user navigated away while the card loaded", async () => {
    const url = { path: "/", query: { n: "notif-1" } as DeepLinkQuery };
    const replace = vi.fn();
    const markRead = vi.fn(async () => {});
    const ctx = {
      current: () => url,
      hasCard: true,
      // The user opens something else during the wait.
      waitForCard: vi.fn(async () => {
        url.path = "/card/other";
        url.query = {};
      }),
      markRead,
      replace,
      handled: new Set<string>(),
    };
    expect(await handleNotificationDeepLink(ctx)).toBe("marked");
    // The notification is read (the link was followed), but nothing drags the
    // user back to where the link pointed.
    expect(markRead).toHaveBeenCalledWith("notif-1");
    expect(replace).not.toHaveBeenCalled();
  });
});

// Finding 10's shape check on its own: the ids the mail links carry are UUIDs.
describe("looksLikeCardId", () => {
  it("accepts a UUID in either case", () => {
    expect(looksLikeCardId(CARD_ID)).toBe(true);
    expect(looksLikeCardId(CARD_ID.toUpperCase())).toBe(true);
  });

  it("rejects anything else, including path fragments and empty values", () => {
    for (const value of [
      "cl-1",
      "../../admin",
      `${CARD_ID}/../login`,
      `  ${CARD_ID}`,
      "",
      null,
      undefined,
    ]) {
      expect(looksLikeCardId(value)).toBe(false);
    }
  });
});
