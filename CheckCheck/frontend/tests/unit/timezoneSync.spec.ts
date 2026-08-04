// @vitest-environment jsdom
//
// Time zone re-sync (system-notifications plan chunk T, revised in S2).
// jsdom because the "has this device moved" marker lives in localStorage.
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
  user: { me: { id: "user-1" } as { id: string } | null },
}));

vi.mock("@/stores/notification", () => ({ useNotificationStore: () => h.store }));
vi.mock("@/stores/user", () => ({ useUserStore: () => h.user }));

import {
  lastSyncedDeviceZone,
  shouldSyncTimezone,
  syncTimezoneWithDevice,
} from "@/utils/timezoneSync";

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

  it("leaves a deliberately picked zone alone while the device has not moved", () => {
    // S2, final-review finding 2: this is the whole point of the marker. The
    // user is in Berlin and asked for Auckland time; the picker has to be able
    // to hold that, or it is a control that silently reverts.
    expect(shouldSyncTimezone("Pacific/Auckland", "Europe/Berlin", "Europe/Berlin")).toBe(false);
  });

  it("still follows a device that really has moved", () => {
    // Same pick, but the device now reports a zone this browser has not synced
    // before: the digest hour follows the user to the new country.
    expect(shouldSyncTimezone("Pacific/Auckland", "America/New_York", "Europe/Berlin")).toBe(true);
  });

  it("writes on the first boot, when nothing has been marked yet", () => {
    // No marker is the pre-S2 behaviour, which is also the right answer for a
    // user who has never opened the dialog.
    expect(shouldSyncTimezone(null, "Europe/Berlin", null)).toBe(true);
    expect(shouldSyncTimezone("Pacific/Auckland", "Europe/Berlin", null)).toBe(true);
  });
});

describe("syncTimezoneWithDevice", () => {
  beforeEach(() => {
    h.store.fetchSettings.mockReset();
    h.store.saveSettings.mockReset();
    h.user.me = { id: "user-1" };
    window.localStorage.clear();
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
    // And it is not marked as done, so the next boot tries again rather than
    // deciding this device has already been dealt with.
    expect(lastSyncedDeviceZone("user-1")).toBeNull();
  });

  it("does not overwrite a zone the user picked after the last sync", async () => {
    // The bug this revision fixes (final-review finding 2). Boot once in Berlin,
    // then let the user ask for Auckland time and reload: the second boot must
    // leave that alone, because the device has not moved.
    h.store.fetchSettings.mockResolvedValue({ timezone: null });
    h.store.saveSettings.mockResolvedValue({ timezone: "Europe/Berlin" });
    await expect(syncTimezoneWithDevice("Europe/Berlin")).resolves.toBe("synced");

    h.store.saveSettings.mockClear();
    h.store.fetchSettings.mockResolvedValue({ timezone: "Pacific/Auckland" });
    await expect(syncTimezoneWithDevice("Europe/Berlin")).resolves.toBe("unchanged");
    expect(h.store.saveSettings).not.toHaveBeenCalled();
  });

  it("marks a boot that had nothing to write, so a later pick survives too", async () => {
    // The stored zone already matched, so no PUT happened, but the device has
    // still been seen, and without recording that, the next boot would treat a
    // zone picked in the meantime as a device that had moved.
    h.store.fetchSettings.mockResolvedValue({ timezone: "Europe/Berlin" });
    await expect(syncTimezoneWithDevice("Europe/Berlin")).resolves.toBe("unchanged");

    h.store.fetchSettings.mockResolvedValue({ timezone: "Pacific/Auckland" });
    await expect(syncTimezoneWithDevice("Europe/Berlin")).resolves.toBe("unchanged");
    expect(h.store.saveSettings).not.toHaveBeenCalled();
  });

  it("keeps one account's marker from silencing another's first sync", async () => {
    // Two accounts, one browser (Chunk A1's concern). A's marker must not make B
    // look like a device that has already been synced.
    h.store.fetchSettings.mockResolvedValue({ timezone: null });
    h.store.saveSettings.mockResolvedValue({ timezone: "Europe/Berlin" });
    await expect(syncTimezoneWithDevice("Europe/Berlin")).resolves.toBe("synced");

    h.user.me = { id: "user-2" };
    h.store.saveSettings.mockClear();
    await expect(syncTimezoneWithDevice("Europe/Berlin")).resolves.toBe("synced");
    expect(h.store.saveSettings).toHaveBeenCalledWith({ timezone: "Europe/Berlin" });
  });
});
