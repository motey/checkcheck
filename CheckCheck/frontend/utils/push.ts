// ── Web Push helpers (system-notifications plan chunk P2) ────────────────────
//
// Framework-free on purpose: the interesting logic (VAPID key shape, iOS/
// standalone platform detection, device labelling) is unit-testable without a
// DOM. composables/usePushSubscription.ts is the thin layer over the actual
// browser APIs (Notification, ServiceWorkerRegistration, PushManager) built on
// top of these.

/**
 * `PushManager.subscribe`'s `applicationServerKey` wants raw bytes, not the
 * base64url string `/api/public-config` hands back. Standard web-push
 * conversion.
 */
export function urlBase64ToUint8Array(base64Url: string): Uint8Array {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}

/**
 * iOS/iPadOS, including iPadOS 13+ which reports its UA as "Macintosh" but
 * exposes multi-touch (a real Mac does not). Safari on iOS is the one platform
 * where Web Push needs the home-screen install, not just a granted permission
 * (plan section 2's platform fact).
 */
export function isIOSPlatform(userAgent: string | null | undefined, maxTouchPoints = 0): boolean {
  const ua = userAgent ?? "";
  if (/iPhone|iPad|iPod/.test(ua)) return true;
  return /Macintosh/.test(ua) && maxTouchPoints > 1;
}

/** True once the app is launched from the home screen, not a normal browser tab. */
export function isStandaloneDisplay(matchMediaStandalone: boolean, iosStandaloneFlag?: boolean): boolean {
  return matchMediaStandalone || iosStandaloneFlag === true;
}

/** Whether the browser exposes everything Web Push needs. */
export function pushApiSupported(opts: {
  hasServiceWorker: boolean;
  hasPushManager: boolean;
  hasNotification: boolean;
}): boolean {
  return opts.hasServiceWorker && opts.hasPushManager && opts.hasNotification;
}

/**
 * Whether the "Enable notifications" button should be offered at all. False
 * for an unsupported browser, and for the one platform combination (iOS, not
 * installed) where subscribing cannot work no matter what the button does —
 * offering a button that silently does nothing is worse than not offering one
 * (plan section 7).
 */
export function canOfferPushEnable(opts: {
  isIOS: boolean;
  isStandalone: boolean;
  supported: boolean;
}): boolean {
  if (!opts.supported) return false;
  if (opts.isIOS && !opts.isStandalone) return false;
  return true;
}

// ── device labelling ─────────────────────────────────────────────────────────
//
// The stored `user_agent` is the raw browser string; the settings dialog shows
// a short, human label instead ("Chrome on Windows"), not the whole UA.

const BROWSER_PATTERNS: [RegExp, string][] = [
  [/Edg\//, "Edge"],
  [/OPR\//, "Opera"],
  [/FxiOS\//, "Firefox"],
  [/CriOS\//, "Chrome"],
  [/Firefox\//, "Firefox"],
  [/Chrome\//, "Chrome"],
  [/Safari\//, "Safari"],
];

const OS_PATTERNS: [RegExp, string][] = [
  [/iPhone/, "iPhone"],
  [/iPad/, "iPad"],
  [/Android/, "Android"],
  [/Windows/, "Windows"],
  [/Mac OS X/, "Mac"],
  [/Linux/, "Linux"],
];

function firstMatch(ua: string, patterns: [RegExp, string][]): string | null {
  for (const [re, label] of patterns) if (re.test(ua)) return label;
  return null;
}

/** "Chrome on Windows", or a graceful fallback for a UA this can't parse (or none at all). */
export function deviceLabel(userAgent: string | null | undefined): string {
  const ua = userAgent ?? "";
  const browser = firstMatch(ua, BROWSER_PATTERNS);
  const os = firstMatch(ua, OS_PATTERNS);
  if (browser && os) return `${browser} on ${os}`;
  return browser || os || "Unknown device";
}

/** Whether *subscriptionEndpoint* is this browser's own current subscription. */
export function isCurrentDevice(
  subscriptionEndpoint: string,
  currentEndpoint: string | null | undefined
): boolean {
  return !!currentEndpoint && subscriptionEndpoint === currentEndpoint;
}
