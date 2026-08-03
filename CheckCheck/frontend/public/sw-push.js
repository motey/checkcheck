// Push notifications and their click behaviour (system-notifications plan
// chunk P2). Imported into the Workbox-generated service worker via
// `pwa.workbox.importScripts` (nuxt.config.ts), so this runs inside the same
// worker that already precaches the app shell — no second registration, no
// second service-worker file to keep in sync with an update flow.

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  const title = data.title || "CheckCheck";
  const options = {
    body: data.body || "",
    icon: "/icons/pwa-192x192.png",
    tag: data.tag || undefined,
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Focus (and navigate) an already-open tab when there is one, so a click does
// not pile up a duplicate window; otherwise open one at the deep-link URL, the
// same `/?card=&n=` shape the email deep link already uses — the app's own
// `useNotificationDeepLink` composable does the actual mark-as-read once it
// hydrates. No new deep-link contract for push.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    (async () => {
      const clientsList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of clientsList) {
        if ("focus" in client) {
          if ("navigate" in client) {
            await client.navigate(url).catch(() => {});
          }
          await client.focus();
          return;
        }
      }
      await self.clients.openWindow(url);
    })()
  );
});

// A push service can rotate a subscription's endpoint on its own at any time,
// independent of anything the user does. Without re-subscribing and re-POSTing
// here, a device silently and permanently stops receiving push after a
// rotation with no user action to blame it on. `fetch` from a service worker
// on the same origin carries the session cookie by default, so this needs no
// extra credentials handling.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      try {
        const oldKey = event.oldSubscription?.options?.applicationServerKey;
        const subscription = oldKey
          ? await self.registration.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: oldKey,
            })
          : await self.registration.pushManager.getSubscription();
        if (!subscription) return;
        const json = subscription.toJSON();
        if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) return;
        await fetch("/api/user/me/push-subscriptions", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            endpoint: json.endpoint,
            keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
            user_agent: self.navigator?.userAgent,
          }),
        });
      } catch {
        // Nothing to surface from inside a service worker. A device that lost
        // its subscription this way just stops receiving push silently; the
        // settings dialog's device list is the place a user would notice.
      }
    })()
  );
});
