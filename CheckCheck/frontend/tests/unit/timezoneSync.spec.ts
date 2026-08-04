// Time zone re-sync on login (system-notifications plan chunk T).
//
// Two things are worth pinning down here, and neither is visible in the browser
// without changing the machine's clock: which pairs of (stored, detected) zones
// are worth a write at all, and that the write is genuinely best-effort, so a
// boot that is offline or unauthenticated stays silent instead of toasting or
// throwing into the board's mounted hook.
import { describe, it, expect, vi, beforeEach } from "vitest";

// Hoisted so the vi.mock factory and the test bodies share one store.
const h = vi.hoisted(() => ({
  store: {
    fetchSettings: vi.fn(),
    saveSettings: vi.fn(),
  },
}));

vi.mock("@/stores/notification", () => ({ useNotificationStore: () => h.store }));

import { shouldSyncTimezone, syncTimezoneWithDevice } from "@/utils/timezoneSync";

describe("shouldSyncTimezone", () => {
  it("writes when the device has moved away from the stored zone", () => {
    expect(shouldSyncTimezone("Europe/Berlin", "Pacific/Auckland")).toBe(true);
  });

  it("writes the device zone over a user who has never chosen one", () => {
    // Decision 4 is explicit that this is not a "only when unset" sync, but the
    // unset case is the one that fixes a digest arriving at 08:00 UTC.
    expect(shouldSyncTimezone(null, "Europe/Berlin")).toBe(true);
    expect(shouldSyncTimezone("", "Europe/Berlin")).toBe(true);
  });

  it("leaves a matching zone alone", () => {
    expect(shouldSyncTimezone("Europe/Berlin", "Europe/Berlin")).toBe(false);
    expect(shouldSyncTimezone("UTC", "UTC")).toBe(false);
  });

  it("does not store an explicit UTC over the empty state that already means UTC", () => {
    // Otherwise every boot on a UTC device writes a value that changes nothing.
    expect(shouldSyncTimezone(null, "UTC")).toBe(false);
    expect(shouldSyncTimezone("", "UTC")).toBe(false);
  });

  it("leaves the stored zone alone when the engine will not name one", () => {
    expect(shouldSyncTimezone("Europe/Berlin", null)).toBe(false);
    expect(shouldSyncTimezone("Europe/Berlin", "")).toBe(false);
    expect(shouldSyncTimezone(null, "   ")).toBe(false);
  });
});

describe("syncTimezoneWithDevice", () => {
  beforeEach(() => {
    h.store.fetchSettings.mockReset();
    h.store.saveSettings.mockReset();
  });

  it("saves the device zone when it differs from the stored one", async () => {
    h.store.fetchSettings.mockResolvedValue({ timezone: "Europe/Berlin" });
    h.store.saveSettings.mockResolvedValue({ timezone: "Pacific/Auckland" });

    await expect(syncTimezoneWithDevice("Pacific/Auckland")).resolves.toBe("synced");
    expect(h.store.saveSettings).toHaveBeenCalledWith({ timezone: "Pacific/Auckland" });
  });

  it("sends nothing when the stored zone already matches", async () => {
    h.store.fetchSettings.mockResolvedValue({ timezone: "Pacific/Auckland" });

    await expect(syncTimezoneWithDevice("Pacific/Auckland")).resolves.toBe("unchanged");
    expect(h.store.saveSettings).not.toHaveBeenCalled();
  });

  it("spends no request at all when the engine names no zone", async () => {
    await expect(syncTimezoneWithDevice(null)).resolves.toBe("skipped");
    expect(h.store.fetchSettings).not.toHaveBeenCalled();
    expect(h.store.saveSettings).not.toHaveBeenCalled();
  });

  it("swallows an offline (or expired-session) read instead of throwing at its caller", async () => {
    // The store's assertOnline throws before any request is made; the board's
    // mounted hook calls this fire-and-forget and must not see it.
    h.store.fetchSettings.mockRejectedValue(new Error("Notification settings can't be loaded offline."));

    await expect(syncTimezoneWithDevice("Pacific/Auckland")).resolves.toBe("skipped");
    expect(h.store.saveSettings).not.toHaveBeenCalled();
  });

  it("swallows a rejected write, such as a zone name this server does not know", async () => {
    h.store.fetchSettings.mockResolvedValue({ timezone: null });
    h.store.saveSettings.mockRejectedValue(new Error("Unknown time zone 'Etc/Unknown'."));

    await expect(syncTimezoneWithDevice("Etc/Unknown")).resolves.toBe("skipped");
    expect(h.store.saveSettings).toHaveBeenCalledOnce();
  });
});
