// ── Push subscription lifecycle (rework chunk N5, finding 5) ─────────────────
//
// The browser's push subscription and the server's device row are two separate
// pieces of state, and before N5 nothing ever took the browser's half down. So
// after user A logged out of a shared browser, A's card names, actor names and
// reminder text kept arriving on that device's lock screen, where whoever picked
// the phone up next could read them.
//
// These two are the teardown half of that fix. They live here rather than in
// composables/usePushSubscription.ts because their callers are not components:
// Navbar's `logout()` and `localSnapshot.reconcileAccount()`, the second of
// which must not pull the notification pinia store into its import graph. Only
// the browser APIs and the shared `$checkapi` transport are needed, no
// reactivity. utils/push.ts stays the purely computational layer below this.

/** This browser's live push subscription, or null. Never throws. */
export async function browserPushSubscription(): Promise<PushSubscription | null> {
  try {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
    const registration = await navigator.serviceWorker.getRegistration();
    return (await registration?.pushManager.getSubscription()) ?? null;
  } catch {
    return null;
  }
}

/**
 * Drop this browser's push subscription, leaving any server row alone.
 *
 * The account-switch safety net: user A's session ended without a clean logout
 * (crash, closed tab, expired cookie), so the row on the server is still A's and
 * B's cookie cannot delete it, but the *browser* must stop receiving A's pushes
 * now that B is using it. The orphaned row dies on its next delivery, when the
 * push service answers 404/410 and the drain removes it (N3).
 */
export async function unsubscribePushLocally(): Promise<boolean> {
  const subscription = await browserPushSubscription();
  if (!subscription) return false;
  try {
    return await subscription.unsubscribe();
  } catch {
    return false;
  }
}

/** Give up on *work* after *ms* rather than making the caller wait for it. */
function withBudget(work: Promise<unknown>, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    void work.finally(() => {
      clearTimeout(timer);
      resolve();
    });
  });
}

/**
 * Explicit-logout hygiene (plan decision 3): take this device's push with the
 * session it belonged to.
 *
 * The server row goes first, while the session cookie is still valid; the local
 * unsubscribe follows, so failing to reach the server does not leave the browser
 * subscribed to a row nobody will ever delete. Best-effort throughout, and
 * time-boxed: a push service that has stopped answering must delay logout by a
 * couple of seconds at worst, never hold it open.
 */
export function unregisterPushOnLogout(timeoutMs = 3000): Promise<void> {
  return withBudget(unregisterPushNow(), timeoutMs);
}

async function unregisterPushNow(): Promise<void> {
  const subscription = await browserPushSubscription();
  if (!subscription) return;
  try {
    const { $checkapi } = useNuxtApp();
    const rows: PushSubscriptionInfoType[] = await $checkapi("/api/user/me/push-subscriptions", {
      method: "get",
      skipErrorToast: true,
    });
    const mine = rows.find((row) => row.endpoint === subscription.endpoint);
    if (mine) {
      await $checkapi("/api/user/me/push-subscriptions/{subscription_id}", {
        path: { subscription_id: mine.id },
        method: "delete",
        skipErrorToast: true,
      });
    }
  } catch {
    // Session already gone, offline, or the row belongs to somebody else. The
    // local unsubscribe below still stops the notifications on this device.
  }
  await subscription.unsubscribe().catch(() => {});
}
