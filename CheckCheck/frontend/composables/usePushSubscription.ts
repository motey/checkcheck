import { computed, ref } from "vue";
import { useNotificationStore } from "@/stores/notification";
import { usePublicConfigStore } from "@/stores/publicConfig";
import {
  applicationServerKeyMatches,
  canOfferPushEnable,
  isCurrentDevice,
  isIOSPlatform,
  isStandaloneDisplay,
  pushApiSupported,
  pushEnableErrorMessage,
  urlBase64ToUint8Array,
} from "@/utils/push";
import { browserPushSubscription, unsubscribePushLocally } from "@/utils/pushLifecycle";

// ── Subscribe/unsubscribe/list glue for the push column (system-notifications
// plan chunk P2) ──────────────────────────────────────────────────────────────
//
// utils/push.ts holds the pure logic (platform detection, key conversion,
// labelling); this composable is the thin layer over the real browser APIs
// (Notification, ServiceWorkerRegistration, PushManager) plus the store's HTTP
// calls, so NotificationSettingsModal.vue stays declarative like its email and
// webhook counterparts.
//
// Subscribing is only ever triggered from `enable()`, itself only ever called
// from a click handler (plan section 7): requesting permission outside a user
// gesture is exactly the pattern users have learned to reflexively deny.
//
// Chunk N5 (finding 5) adds the missing half: the browser's subscription and the
// server's device list are two independent pieces of state, and nothing used to
// reconcile them. `refresh()` now does; utils/pushLifecycle.ts holds the
// teardown its non-component callers (logout, account switch) need.

function errorStatus(err: unknown): number | undefined {
  return (err as any)?.statusCode ?? (err as any)?.response?.status;
}

function errorDetail(err: unknown): string | null {
  const detail = (err as any)?.data?.detail;
  return typeof detail === "string" ? detail : null;
}

export function usePushSubscription() {
  const store = useNotificationStore();
  const publicConfig = usePublicConfigStore();

  const loading = ref(false);
  const enabling = ref(false);
  const busyId = ref<string | null>(null);
  const error = ref<string | null>(null);
  // This browser's own subscription endpoint, *as reconciled against the
  // server's device list* (N5), used to label "this device" in the list, to
  // decide whether Enable has anything left to do, and to avoid re-subscribing
  // needlessly. It never holds an endpoint the server does not know about:
  // `refresh()` unsubscribes those locally instead of remembering them, which
  // is what keeps the previous user's dead subscription from disabling the
  // button for the next one (finding 5.1).
  const currentEndpoint = ref<string | null>(null);

  const devices = computed(() => store.pushSubscriptions);

  const supported = computed(() =>
    pushApiSupported({
      hasServiceWorker: typeof navigator !== "undefined" && "serviceWorker" in navigator,
      hasPushManager: typeof window !== "undefined" && "PushManager" in window,
      hasNotification: typeof window !== "undefined" && "Notification" in window,
    })
  );

  const isIOS = computed(() =>
    typeof navigator === "undefined"
      ? false
      : isIOSPlatform(navigator.userAgent, navigator.maxTouchPoints)
  );

  const isStandalone = computed(() => {
    if (typeof window === "undefined") return false;
    const iosFlag = (window.navigator as unknown as { standalone?: boolean }).standalone;
    return isStandaloneDisplay(
      window.matchMedia?.("(display-mode: standalone)").matches ?? false,
      iosFlag
    );
  });

  const canEnable = computed(() =>
    canOfferPushEnable({
      isIOS: isIOS.value,
      isStandalone: isStandalone.value,
      supported: supported.value,
    })
  );

  // Reconciled state, not raw browser state: "this browser holds a subscription
  // the server knows about", which is the only condition under which Enable
  // genuinely has nothing left to do.
  const isSubscribedHere = computed(() => !!currentEndpoint.value);

  function isThisDevice(subscription: PushSubscriptionInfoType): boolean {
    return isCurrentDevice(subscription.endpoint, currentEndpoint.value);
  }

  /**
   * Reconcile the browser's subscription against the server's device list (N5).
   *
   * A subscription the server has never heard of belongs to somebody else, or to
   * a row that was deleted on another device or evicted by the per-user cap
   * (N4). Either way it is dead weight for *this* account: keeping it would mark
   * the device as already enabled and dead-end the button, and re-registering it
   * is exactly the takeover N4 now answers with a 409. So it is unsubscribed
   * here, which both frees the endpoint and lets Enable mint a fresh one.
   */
  async function reconcileCurrentEndpoint(
    devices: PushSubscriptionInfoType[]
  ): Promise<void> {
    if (!supported.value) {
      currentEndpoint.value = null;
      return;
    }
    const subscription = await browserPushSubscription();
    if (!subscription) {
      currentEndpoint.value = null;
      return;
    }
    if (devices.some((device) => device.endpoint === subscription.endpoint)) {
      currentEndpoint.value = subscription.endpoint;
      return;
    }
    await subscription.unsubscribe().catch(() => {});
    currentEndpoint.value = null;
  }

  /** Loads the device list and figures out which of them (if any) is this browser. */
  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      const devices = await store.listPushSubscriptions();
      await reconcileCurrentEndpoint(devices);
    } finally {
      loading.value = false;
    }
  }

  async function enable(): Promise<void> {
    if (enabling.value) return;
    enabling.value = true;
    error.value = null;
    try {
      if (!supported.value) {
        error.value = "This browser does not support push notifications.";
        return;
      }
      if (Notification.permission === "denied") {
        error.value = "Notifications are blocked for this site in your browser's settings.";
        return;
      }
      const permission =
        Notification.permission === "granted"
          ? "granted"
          : await Notification.requestPermission();
      if (permission !== "granted") {
        error.value = "Permission was not granted.";
        return;
      }
      const vapidKey = publicConfig.vapidPublicKey;
      if (!vapidKey) {
        error.value = "This server has not configured push notifications.";
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (
        subscription &&
        !applicationServerKeyMatches(subscription.options?.applicationServerKey, vapidKey)
      ) {
        // The operator rotated the VAPID pair. Reusing this subscription would
        // keep the device on a key the server no longer signs with, and every
        // push to it would fail with a 403 that N3 (correctly) refuses to treat
        // as "gone": a row that fails forever and an operator wondering why.
        await subscription.unsubscribe().catch(() => {});
        subscription = null;
      }
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidKey),
        });
      }
      const json = subscription.toJSON();
      if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
        error.value = "The browser returned an incomplete subscription.";
        return;
      }
      await store.registerPushSubscription({
        endpoint: json.endpoint,
        keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
        user_agent: navigator.userAgent,
      });
      currentEndpoint.value = json.endpoint;
    } catch (err) {
      console.error("Could not enable push notifications", err);
      const status = errorStatus(err);
      error.value = pushEnableErrorMessage(status, errorDetail(err));
      if (status === 409) {
        // N4's ownership conflict: the endpoint this browser holds belongs to
        // another account's row. Drop it so the "try again" the message asks for
        // actually works: the next subscribe mints an endpoint nobody owns.
        await unsubscribePushLocally().catch(() => {});
        currentEndpoint.value = null;
      }
    } finally {
      enabling.value = false;
    }
  }

  async function disable(subscription: PushSubscriptionInfoType): Promise<void> {
    busyId.value = subscription.id;
    try {
      await store.deletePushSubscription(subscription.id);
      if (isThisDevice(subscription)) {
        await unsubscribePushLocally();
        currentEndpoint.value = null;
      }
    } finally {
      busyId.value = null;
    }
  }

  return {
    devices,
    loading,
    enabling,
    busyId,
    error,
    supported,
    isIOS,
    isStandalone,
    canEnable,
    isSubscribedHere,
    isThisDevice,
    refresh,
    enable,
    disable,
  };
}
