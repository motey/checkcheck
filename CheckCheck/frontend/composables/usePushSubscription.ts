import { computed, ref } from "vue";
import { useNotificationStore } from "@/stores/notification";
import { usePublicConfigStore } from "@/stores/publicConfig";
import {
  canOfferPushEnable,
  isCurrentDevice,
  isIOSPlatform,
  isStandaloneDisplay,
  pushApiSupported,
  urlBase64ToUint8Array,
} from "@/utils/push";

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

export function usePushSubscription() {
  const store = useNotificationStore();
  const publicConfig = usePublicConfigStore();

  const loading = ref(false);
  const enabling = ref(false);
  const busyId = ref<string | null>(null);
  const error = ref<string | null>(null);
  // This browser's own current subscription endpoint, once known — used to
  // label "this device" in the list and to avoid re-subscribing needlessly.
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

  const isSubscribedHere = computed(() => !!currentEndpoint.value);

  function isThisDevice(subscription: PushSubscriptionInfoType): boolean {
    return isCurrentDevice(subscription.endpoint, currentEndpoint.value);
  }

  async function syncCurrentEndpoint(): Promise<void> {
    if (!supported.value) return;
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      const subscription = await registration?.pushManager.getSubscription();
      currentEndpoint.value = subscription?.endpoint ?? null;
    } catch {
      currentEndpoint.value = null;
    }
  }

  /** Loads the device list and figures out which of them (if any) is this browser. */
  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      await store.listPushSubscriptions();
      await syncCurrentEndpoint();
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
      error.value = "Could not enable push notifications on this device.";
    } finally {
      enabling.value = false;
    }
  }

  async function disable(subscription: PushSubscriptionInfoType): Promise<void> {
    busyId.value = subscription.id;
    try {
      await store.deletePushSubscription(subscription.id);
      if (isThisDevice(subscription)) {
        const registration = await navigator.serviceWorker.getRegistration();
        const sub = await registration?.pushManager.getSubscription();
        await sub?.unsubscribe().catch(() => {});
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
