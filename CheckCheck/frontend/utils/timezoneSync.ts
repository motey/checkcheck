import { useNotificationStore } from "@/stores/notification";
import { useUserStore } from "@/stores/user";
import { detectTimezone } from "@/utils/notificationSettings";

// ── Time zone re-sync (system-notifications plan chunk T, revised in S2) ──────
//
// The stored time zone is what the daily digest clock is read in (08:00 "in the
// user's own zone"), and until chunk T the only way it ever got set was the
// picker in the settings dialog. A user who never opened that dialog had their
// digest arrive at 08:00 UTC, and a user who moved country kept the zone they
// picked once, silently, with nothing on screen to explain the wrong hour.
//
// Chunk T pushed the device's zone to the server on every authenticated boot,
// which fixed both of those and broke a third thing: the picker could not hold
// any value except the device's own zone, because the next reload of the same
// machine wrote over it (final-review finding 2). "Saved" was true for about as
// long as the tab stayed open.
//
// So the write is now conditional on the *device* having moved. This module
// remembers, per account and per browser, the device zone it last synced; a boot
// where the device still reports that zone leaves the stored value alone,
// whatever it is. The three cases that matter all come out right:
//
//   * Never opened the dialog: no marker and no stored zone, so the first boot
//     writes the device zone. This is the case chunk T exists for.
//   * Picked a zone deliberately: the device has not moved since the marker was
//     written, so the pick survives every reload, on this device, indefinitely.
//   * Actually travelled: the device reports a new zone, which does not match
//     the marker, so the digest hour follows the user to the new country.
//
// Two things this deliberately does NOT do:
//
//   * It does not sync across devices. The marker is per browser, so a phone in
//     another zone will move the stored value even though the laptop just set
//     it. That is the same trade-off chunk T's decision 4 accepted, narrowed to
//     the case where a zone genuinely differs from what this device last saw.
//   * A re-sync moves the daily digest hour but NOT existing reminders. A
//     reminder snapshots its own zone when it is created (plan decision 5,
//     `routes_reminder.py`), so recurring reminders keep firing on the zone they
//     were made in. Silently shifting an alarm somebody set for 07:00 is a worse
//     outcome than a digest hour that follows the device.
//
// Best-effort throughout. This is a convenience write, not a critical one: it is
// fire-and-forget from the board's boot path, it never toasts, and offline it
// does not happen at all (the store's `assertOnline` throws before any request
// is made, so nothing lands in the outbox for a decision the server resolves).

/** Per-account marker: the device zone this browser last synced for that user. */
const MARKER_PREFIX = "checkcheck.tzsync.";

function markerKey(userId: string | null | undefined): string {
  // An unknown user still gets a key rather than sharing one: better to sync
  // once too often than to let one account's marker silence another's.
  return `${MARKER_PREFIX}${userId ?? "unknown"}`;
}

/** The device zone this browser last synced for *userId*, or null. */
export function lastSyncedDeviceZone(userId: string | null | undefined): string | null {
  try {
    return window.localStorage.getItem(markerKey(userId));
  } catch {
    // Private mode, a disabled store, or no window at all: the caller then
    // behaves exactly like a first boot, which is safe.
    return null;
  }
}

/** Remember that this browser has seen *zone* for *userId*. */
export function rememberDeviceZone(userId: string | null | undefined, zone: string): void {
  try {
    window.localStorage.setItem(markerKey(userId), zone);
  } catch {
    // Nothing to do: without the marker every boot re-evaluates from scratch,
    // which is the pre-S2 behaviour rather than a broken one.
  }
}

/**
 * Whether *detected* is worth writing over *stored*.
 *
 * Stored `null` (or an empty string) is the "no zone chosen" state, which the
 * server reads as UTC. That is why a device that genuinely is on UTC does not
 * trigger a write: it would store an explicit "UTC" that behaves exactly like
 * the empty state it replaced, on every boot, forever.
 *
 * *lastSynced* is what this browser last saw this device report (see above).
 * When it still matches, the device has not moved and a stored zone that differs
 * is somebody's deliberate choice, so it is left alone.
 */
export function shouldSyncTimezone(
  stored: string | null | undefined,
  detected: string | null | undefined,
  lastSynced: string | null | undefined = null
): boolean {
  const device = (detected ?? "").trim();
  if (!device) return false; // An engine that will not say leaves the stored value alone.
  const current = (stored ?? "").trim();
  if (current === device) return false;
  if (!current && device === "UTC") return false;
  if ((lastSynced ?? "").trim() === device) return false;
  return true;
}

/** What `syncTimezoneWithDevice` did, for tests and for a caller that cares. */
export type TimezoneSyncResult = "synced" | "unchanged" | "skipped";

/**
 * Bring the stored time zone in line with this device's, once, at boot.
 *
 * Returns "synced" when a PUT was made, "unchanged" when there was nothing to
 * write (the zones already agree, or the device has not moved since the last
 * sync), and "skipped" when anything at all went wrong (offline, a session that
 * has expired, a zone the server refuses). A caller is expected to ignore the
 * result; it exists so the tests can tell the three apart.
 */
export async function syncTimezoneWithDevice(
  detected: string | null = detectTimezone()
): Promise<TimezoneSyncResult> {
  // An engine that will not name a zone makes the whole comparison pointless:
  // skip before spending a request on it.
  const device = (detected ?? "").trim();
  if (!device) return "skipped";
  try {
    const store = useNotificationStore();
    const userId = useUserStore().me?.id ?? null;
    const settings = await store.fetchSettings();
    const decision = shouldSyncTimezone(
      settings.timezone,
      device,
      lastSyncedDeviceZone(userId)
    );
    // The marker is recorded on every reachable boot, not only when a write
    // happens: it means "this browser has seen the device report this zone for
    // this account", so a user whose stored zone already matched must get one
    // too. Without that, picking a different zone in the dialog afterwards would
    // be overwritten by the next boot, which is the bug this revision fixes.
    // After the PUT in the write case, so a failed save is retried next boot
    // rather than being silently marked as done.
    if (!decision) {
      rememberDeviceZone(userId, device);
      return "unchanged";
    }
    await store.saveSettings({ timezone: device });
    rememberDeviceZone(userId, device);
    return "synced";
  } catch {
    // Offline, unauthenticated, or a zone name this server does not know. None
    // of them is worth a toast: the user did not ask for this write.
    return "skipped";
  }
}
