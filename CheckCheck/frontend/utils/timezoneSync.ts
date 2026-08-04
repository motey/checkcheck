import { useNotificationStore } from "@/stores/notification";
import { detectTimezone } from "@/utils/notificationSettings";

// ── Time zone re-sync on login (system-notifications plan chunk T) ────────────
//
// The stored time zone is what the daily digest clock is read in (08:00 "in the
// user's own zone"), and until now the only way it ever got set was the picker
// in the settings dialog. A user who never opened that dialog had their digest
// arrive at 08:00 UTC, and a user who moved country kept the zone they picked
// once, silently, with nothing on screen to explain the wrong hour.
//
// So the device's zone is pushed to the server on every authenticated boot
// (plan decision 4). That decision is a trade-off, not a free win, and the two
// halves of it are worth stating where the code lives:
//
//   * A deliberately pinned zone cannot survive this. Somebody keeping home-city
//     time while travelling loses that pin at the next login on the travelling
//     device. The picker says so in one line ("kept in sync with this device"),
//     which is the price of the behaviour being predictable rather than a
//     surprise discovered by a digest landing at breakfast in the wrong country.
//   * A re-sync moves the daily digest hour but NOT existing reminders. A
//     reminder snapshots its own zone when it is created (plan decision 5,
//     `routes_reminder.py`), so recurring reminders keep firing on the zone they
//     were made in. Nothing here changes that, deliberately: silently shifting
//     an alarm somebody set for 07:00 is a worse outcome than a digest hour that
//     follows the device.
//
// Best-effort throughout. This is a convenience write, not a critical one: it is
// fire-and-forget from the board's boot path, it never toasts, and offline it
// does not happen at all (the store's `assertOnline` throws before any request
// is made, so nothing lands in the outbox for a decision the server resolves).

/**
 * Whether *detected* is worth writing over *stored*.
 *
 * Stored `null` (or an empty string) is the "no zone chosen" state, which the
 * server reads as UTC. That is why a device that genuinely is on UTC does not
 * trigger a write: it would store an explicit "UTC" that behaves exactly like
 * the empty state it replaced, on every boot, forever.
 */
export function shouldSyncTimezone(
  stored: string | null | undefined,
  detected: string | null | undefined
): boolean {
  const device = (detected ?? "").trim();
  if (!device) return false; // An engine that will not say leaves the stored value alone.
  const current = (stored ?? "").trim();
  if (current === device) return false;
  if (!current && device === "UTC") return false;
  return true;
}

/** What `syncTimezoneWithDevice` did, for tests and for a caller that cares. */
export type TimezoneSyncResult = "synced" | "unchanged" | "skipped";

/**
 * Bring the stored time zone in line with this device's, once, at boot.
 *
 * Returns "synced" when a PUT was made, "unchanged" when the stored zone already
 * matched, and "skipped" when anything at all went wrong (offline, a session
 * that has expired, a zone the server refuses). A caller is expected to ignore
 * the result; it exists so the tests can tell the three apart.
 */
export async function syncTimezoneWithDevice(
  detected: string | null = detectTimezone()
): Promise<TimezoneSyncResult> {
  // An engine that will not name a zone makes the whole comparison pointless:
  // skip before spending a request on it.
  if (!(detected ?? "").trim()) return "skipped";
  try {
    const store = useNotificationStore();
    const settings = await store.fetchSettings();
    if (!shouldSyncTimezone(settings.timezone, detected)) return "unchanged";
    await store.saveSettings({ timezone: detected });
    return "synced";
  } catch {
    // Offline, unauthenticated, or a zone name this server does not know. None
    // of them is worth a toast: the user did not ask for this write.
    return "skipped";
  }
}
