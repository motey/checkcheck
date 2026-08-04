// Unit tests for the push subscription lifecycle (rework chunk N5, finding 5,
// test gaps 8 and 9).
//
// `composables/usePushSubscription.ts` had no unit test at all: `push.spec.ts`
// covers the pure helpers underneath it, and the E2E suite covers the happy
// path through the dialog, but the reconciliation this chunk adds is exactly
// the logic that is painful to provoke in a browser (a subscription belonging
// to a user who is no longer logged in, a rotated VAPID key, a 409 from the
// registration endpoint). So the browser APIs are faked here the same way the
// Playwright spec stubs them, and the real composable runs against them.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Hoisted so the vi.mock factories and the test bodies share one store.
const h = vi.hoisted(() => {
  const store = {
    pushSubscriptions: [] as any[],
    listPushSubscriptions: vi.fn(),
    registerPushSubscription: vi.fn(),
    deletePushSubscription: vi.fn(),
  };
  const publicConfig = { vapidPublicKey: null as string | null };
  const checkapi = vi.fn();
  return { store, publicConfig, checkapi };
});

vi.mock("@/stores/notification", () => ({ useNotificationStore: () => h.store }));
vi.mock("@/stores/publicConfig", () => ({ usePublicConfigStore: () => h.publicConfig }));

import { usePushSubscription } from "@/composables/usePushSubscription";
import { unregisterPushOnLogout, unsubscribePushLocally } from "@/utils/pushLifecycle";
import { urlBase64ToUint8Array } from "@/utils/push";

// ── the fake browser ─────────────────────────────────────────────────────────

/** The server's key, and one it is not. Both valid base64url. */
const VAPID_KEY = "aGVsbG8tdmFwaWQta2V5";
const OTHER_KEY = "b3RoZXItdmFwaWQta2V5";

function keyBuffer(base64Url: string): ArrayBuffer {
  const bytes = urlBase64ToUint8Array(base64Url);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

class FakeSubscription {
  endpoint: string;
  options: { applicationServerKey: ArrayBuffer | null };
  unsubscribed = false;

  constructor(endpoint: string, key: string | null) {
    this.endpoint = endpoint;
    this.options = { applicationServerKey: key === null ? null : keyBuffer(key) };
  }

  toJSON() {
    return {
      endpoint: this.endpoint,
      keys: { p256dh: "fake-p256dh", auth: "fake-auth" },
    };
  }

  async unsubscribe(): Promise<boolean> {
    this.unsubscribed = true;
    if (browser.subscription === this) browser.subscription = null;
    return true;
  }
}

const browser = {
  subscription: null as FakeSubscription | null,
  subscribeCalls: 0,
  permission: "granted" as NotificationPermission,
};

/** Install `navigator.serviceWorker`, `window` and `Notification` on the node global. */
function installFakeBrowser(): void {
  browser.subscription = null;
  browser.subscribeCalls = 0;
  browser.permission = "granted";

  const pushManager = {
    getSubscription: async () => browser.subscription,
    subscribe: async (_options: unknown) => {
      browser.subscribeCalls += 1;
      browser.subscription = new FakeSubscription(
        `https://push.example/sub-${browser.subscribeCalls}`,
        VAPID_KEY
      );
      return browser.subscription;
    },
  };
  const registration = { pushManager };
  const serviceWorker = {
    getRegistration: async () => registration,
    ready: Promise.resolve(registration),
  };
  const notification = {
    get permission() {
      return browser.permission;
    },
    requestPermission: async () => {
      browser.permission = "granted";
      return "granted";
    },
  };

  vi.stubGlobal("navigator", {
    serviceWorker,
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0",
    maxTouchPoints: 0,
  });
  vi.stubGlobal("Notification", notification);
  // `useNuxtApp` is a Nuxt auto-import (a runtime global under Nuxt). Stubbed
  // per test, because `unstubAllGlobals` in afterEach takes it away again.
  vi.stubGlobal("useNuxtApp", () => ({ $checkapi: h.checkapi }));
  vi.stubGlobal("window", {
    PushManager: class {},
    Notification: notification,
    navigator: {},
    matchMedia: () => ({ matches: false }),
  });
}

/** A server device row, as `GET /api/user/me/push-subscriptions` returns it. */
function deviceRow(endpoint: string, id = "row-1") {
  return {
    id,
    endpoint,
    user_agent: "Chrome on Linux",
    created_at: "2026-08-03T10:00:00",
    last_seen_at: "2026-08-03T10:00:00",
  };
}

/** A FetchError as `$checkapi` surfaces it (`ofetch` shape). */
function fetchError(status: number, detail: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    statusCode: status,
    data: { detail },
  });
}

beforeEach(() => {
  installFakeBrowser();
  h.publicConfig.vapidPublicKey = VAPID_KEY;
  h.store.pushSubscriptions = [];
  h.store.listPushSubscriptions.mockReset().mockImplementation(async () => h.store.pushSubscriptions);
  h.store.registerPushSubscription.mockReset().mockImplementation(async (body: any) => {
    const row = deviceRow(body.endpoint, "row-new");
    h.store.pushSubscriptions = [row, ...h.store.pushSubscriptions];
    return row;
  });
  h.store.deletePushSubscription.mockReset().mockImplementation(async (id: string) => {
    h.store.pushSubscriptions = h.store.pushSubscriptions.filter((row: any) => row.id !== id);
  });
  h.checkapi.mockReset();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("usePushSubscription.refresh (reconciliation)", () => {
  it("unsubscribes a browser subscription the server does not know about, leaving Enable available", async () => {
    // The shared-browser case: A enabled push here and is gone; the row on the
    // server is A's, so B's device list does not contain this endpoint.
    const stale = new FakeSubscription("https://push.example/user-a", VAPID_KEY);
    browser.subscription = stale;
    h.store.pushSubscriptions = [];

    const push = usePushSubscription();
    await push.refresh();

    expect(stale.unsubscribed).toBe(true);
    expect(browser.subscription).toBeNull();
    // Which is the point: the button is offered to B rather than reading
    // "Enabled on this device" and being disabled.
    expect(push.isSubscribedHere.value).toBe(false);
  });

  it("leaves a subscription the server lists alone", async () => {
    const mine = new FakeSubscription("https://push.example/mine", VAPID_KEY);
    browser.subscription = mine;
    h.store.pushSubscriptions = [deviceRow("https://push.example/mine")];

    const push = usePushSubscription();
    await push.refresh();

    expect(mine.unsubscribed).toBe(false);
    expect(push.isSubscribedHere.value).toBe(true);
    expect(push.isThisDevice(deviceRow("https://push.example/mine") as any)).toBe(true);
  });

  it("reports not-subscribed when the browser holds nothing, whatever the server lists", async () => {
    // Another device of the same user: a row exists, but not for this browser.
    h.store.pushSubscriptions = [deviceRow("https://push.example/other-device")];

    const push = usePushSubscription();
    await push.refresh();

    expect(push.isSubscribedHere.value).toBe(false);
    expect(push.devices.value).toHaveLength(1);
  });
});

describe("usePushSubscription.enable", () => {
  it("subscribes and registers when the browser holds nothing", async () => {
    const push = usePushSubscription();
    await push.enable();

    expect(browser.subscribeCalls).toBe(1);
    expect(h.store.registerPushSubscription).toHaveBeenCalledTimes(1);
    expect(push.error.value).toBeNull();
    expect(push.isSubscribedHere.value).toBe(true);
  });

  it("reuses a subscription that already carries the server's current key", async () => {
    browser.subscription = new FakeSubscription("https://push.example/mine", VAPID_KEY);

    const push = usePushSubscription();
    await push.enable();

    expect(browser.subscribeCalls).toBe(0);
    expect(h.store.registerPushSubscription).toHaveBeenCalledWith(
      expect.objectContaining({ endpoint: "https://push.example/mine" })
    );
  });

  it("resubscribes when the existing subscription was made with a different VAPID key", async () => {
    // The operator rotated the pair: pushes to this subscription would 403
    // forever, and N3 (correctly) does not delete a row over a 403.
    const stale = new FakeSubscription("https://push.example/old-key", OTHER_KEY);
    browser.subscription = stale;

    const push = usePushSubscription();
    await push.enable();

    expect(stale.unsubscribed).toBe(true);
    expect(browser.subscribeCalls).toBe(1);
    expect(h.store.registerPushSubscription).toHaveBeenCalledWith(
      expect.objectContaining({ endpoint: "https://push.example/sub-1" })
    );
  });

  it("keeps a subscription whose key the browser does not expose", async () => {
    // No `applicationServerKey` is not evidence of a mismatch; churning here
    // would resubscribe a working device on every enable.
    const opaque = new FakeSubscription("https://push.example/opaque", null);
    browser.subscription = opaque;

    const push = usePushSubscription();
    await push.enable();

    expect(opaque.unsubscribed).toBe(false);
    expect(browser.subscribeCalls).toBe(0);
  });

  it("shows the server's own words on a 409 and frees the endpoint so 'try again' works", async () => {
    const stolen = new FakeSubscription("https://push.example/user-a", VAPID_KEY);
    browser.subscription = stolen;
    h.store.registerPushSubscription.mockRejectedValueOnce(
      fetchError(
        409,
        "This device is already registered to another account. Sign out there, or clear this site's data in this browser, and try again."
      )
    );

    const push = usePushSubscription();
    await push.enable();

    expect(push.error.value).toContain("already registered to another account");
    expect(push.error.value).toContain("try again");
    // The retry the message asks for only works because the endpoint was freed.
    expect(stolen.unsubscribed).toBe(true);
    expect(push.isSubscribedHere.value).toBe(false);

    await push.enable();
    expect(browser.subscribeCalls).toBe(1);
    expect(push.error.value).toBeNull();
    expect(push.isSubscribedHere.value).toBe(true);
  });

  it("falls back to a generic message for a failure that is not a conflict", async () => {
    h.store.registerPushSubscription.mockRejectedValueOnce(fetchError(500, "boom"));

    const push = usePushSubscription();
    await push.enable();

    expect(push.error.value).toBe("Could not enable push notifications on this device.");
  });

  it("refuses when permission is denied, without touching the push manager", async () => {
    browser.permission = "denied";

    const push = usePushSubscription();
    await push.enable();

    expect(push.error.value).toContain("blocked");
    expect(browser.subscribeCalls).toBe(0);
    expect(h.store.registerPushSubscription).not.toHaveBeenCalled();
  });
});

describe("usePushSubscription.disable", () => {
  it("clears both sides for this device", async () => {
    const mine = new FakeSubscription("https://push.example/mine", VAPID_KEY);
    browser.subscription = mine;
    const row = deviceRow("https://push.example/mine");
    h.store.pushSubscriptions = [row];

    const push = usePushSubscription();
    await push.refresh();
    await push.disable(row as any);

    expect(h.store.deletePushSubscription).toHaveBeenCalledWith(row.id);
    expect(mine.unsubscribed).toBe(true);
    expect(push.isSubscribedHere.value).toBe(false);
  });

  it("leaves this browser's subscription alone when removing another device", async () => {
    const mine = new FakeSubscription("https://push.example/mine", VAPID_KEY);
    browser.subscription = mine;
    const other = deviceRow("https://push.example/phone", "row-phone");
    h.store.pushSubscriptions = [deviceRow("https://push.example/mine"), other];

    const push = usePushSubscription();
    await push.refresh();
    await push.disable(other as any);

    expect(h.store.deletePushSubscription).toHaveBeenCalledWith("row-phone");
    expect(mine.unsubscribed).toBe(false);
    expect(push.isSubscribedHere.value).toBe(true);
  });
});

describe("pushLifecycle teardown", () => {
  it("unsubscribePushLocally drops the browser subscription and nothing else", async () => {
    const mine = new FakeSubscription("https://push.example/mine", VAPID_KEY);
    browser.subscription = mine;

    expect(await unsubscribePushLocally()).toBe(true);
    expect(mine.unsubscribed).toBe(true);
    expect(h.checkapi).not.toHaveBeenCalled();
  });

  it("unsubscribePushLocally is a no-op when there is nothing subscribed", async () => {
    expect(await unsubscribePushLocally()).toBe(false);
  });

  it("deletes the server row before unsubscribing on logout", async () => {
    const mine = new FakeSubscription("https://push.example/mine", VAPID_KEY);
    browser.subscription = mine;
    const order: string[] = [];
    h.checkapi.mockImplementation(async (path: string, opts: any) => {
      if (opts.method === "get") {
        order.push("list");
        return [deviceRow("https://push.example/mine", "row-mine")];
      }
      order.push(`delete:${opts.path.subscription_id}`);
      return undefined;
    });

    await unregisterPushOnLogout();

    // The DELETE has to precede both the local unsubscribe and (in Navbar) the
    // logout POST that invalidates the cookie it needs.
    expect(order).toEqual(["list", "delete:row-mine"]);
    expect(mine.unsubscribed).toBe(true);
  });

  it("still unsubscribes locally when the server call fails", async () => {
    const mine = new FakeSubscription("https://push.example/mine", VAPID_KEY);
    browser.subscription = mine;
    h.checkapi.mockRejectedValue(fetchError(401, "Not authenticated"));

    await unregisterPushOnLogout();

    expect(mine.unsubscribed).toBe(true);
  });

  it("gives up on a push service that never answers rather than blocking logout", async () => {
    const hung = new FakeSubscription("https://push.example/hung", VAPID_KEY);
    hung.unsubscribe = () => new Promise<boolean>(() => {});
    browser.subscription = hung;
    h.checkapi.mockResolvedValue([]);

    // Resolves on the budget, not on the unsubscribe.
    await unregisterPushOnLogout(20);
  });
});
