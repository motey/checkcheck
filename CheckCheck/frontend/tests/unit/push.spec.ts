import { afterEach, describe, it, expect, vi } from "vitest";
import {
  applicationServerKeyMatches,
  canOfferPushEnable,
  deviceLabel,
  isCurrentDevice,
  isIOSPlatform,
  isSecureContextNow,
  isStandaloneDisplay,
  pushApiSupported,
  pushEnableErrorMessage,
  pushUnavailableReason,
  urlBase64ToUint8Array,
} from "@/utils/push";

// Pure logic behind the push column (system-notifications plan chunk P2): key
// shape conversion, iOS/standalone platform detection and the "can we even
// offer this button" decision, plus device-list labelling. The composable
// (composables/usePushSubscription.ts) is the thin, untested-here layer over
// the real browser APIs.

describe("urlBase64ToUint8Array", () => {
  it("round-trips a base64url string without padding", () => {
    // "hello" base64url-encoded, no trailing '=' — the shape the server's
    // vapid_public_key and a real applicationServerKey both come in.
    const bytes = urlBase64ToUint8Array("aGVsbG8");
    expect(Array.from(bytes)).toEqual([104, 101, 108, 108, 111]);
  });

  it("handles the '-' and '_' substitutions standard base64 does not use", () => {
    // Bytes 0xFB 0xEF would be "+-8=" and "b/8=" in the two encodings that
    // collide in URLs; base64url uses '-' and '_' instead of '+' and '/'.
    const bytes = urlBase64ToUint8Array("--8");
    expect(Array.from(bytes)).toEqual([0xfb, 0xef]);
  });
});

describe("isIOSPlatform", () => {
  it("recognises iPhone and iPod user agents directly", () => {
    expect(isIOSPlatform("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")).toBe(true);
    expect(isIOSPlatform("Mozilla/5.0 (iPod touch; CPU iPhone OS 17_0)")).toBe(true);
  });

  it("recognises iPadOS 13+ spoofing as Macintosh only via multi-touch", () => {
    const macUA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)";
    expect(isIOSPlatform(macUA, 0)).toBe(false);
    expect(isIOSPlatform(macUA, 5)).toBe(true);
  });

  it("is false for a plain desktop or Android user agent", () => {
    expect(isIOSPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe(false);
    expect(isIOSPlatform("Mozilla/5.0 (Linux; Android 14)")).toBe(false);
    expect(isIOSPlatform(null)).toBe(false);
  });
});

describe("isStandaloneDisplay", () => {
  it("is true from either the media-query match or the iOS legacy flag", () => {
    expect(isStandaloneDisplay(true)).toBe(true);
    expect(isStandaloneDisplay(false, true)).toBe(true);
    expect(isStandaloneDisplay(false, false)).toBe(false);
    expect(isStandaloneDisplay(false)).toBe(false);
  });
});

describe("canOfferPushEnable", () => {
  it("is false on an unsupported browser regardless of platform", () => {
    expect(canOfferPushEnable({ isIOS: false, isStandalone: false, supported: false })).toBe(
      false
    );
  });

  it("is false on iOS unless the app is installed to the home screen", () => {
    expect(canOfferPushEnable({ isIOS: true, isStandalone: false, supported: true })).toBe(false);
    expect(canOfferPushEnable({ isIOS: true, isStandalone: true, supported: true })).toBe(true);
  });

  it("is true on any other supported platform", () => {
    expect(canOfferPushEnable({ isIOS: false, isStandalone: false, supported: true })).toBe(true);
  });
});

// ── which hint the dialog shows (chunk K2, decision 5) ───────────────────────
//
// The old dialog had one sentence, "This browser does not support push
// notifications", for three separate problems, and it was wrong for two of them.

describe("pushUnavailableReason", () => {
  const ok = { isIOS: false, isStandalone: false, secureContext: true, supported: true };

  it("is null when the button can be offered", () => {
    expect(pushUnavailableReason(ok)).toBe(null);
  });

  it("names the missing certificate rather than blaming the browser", () => {
    // The case the old wording got wrong: a page served over plain http on a LAN
    // address or a hostname. The browser is right to refuse, and no server-side
    // setting can change it, so the sentence has to name https.
    expect(pushUnavailableReason({ ...ok, secureContext: false })).toBe("insecure_context");
  });

  it("prefers the insecure-context reason over the browser's missing APIs", () => {
    // A browser on an insecure page often hides the push APIs, so both signals
    // fire at once and only one of them explains anything.
    expect(
      pushUnavailableReason({ ...ok, secureContext: false, supported: false })
    ).toBe("insecure_context");
  });

  it("asks for the home-screen install on iOS, but only on a secure page", () => {
    expect(pushUnavailableReason({ ...ok, isIOS: true })).toBe("ios_install");
    expect(pushUnavailableReason({ ...ok, isIOS: true, isStandalone: true })).toBe(null);
    // Adding an http page to the home screen would not help either.
    expect(pushUnavailableReason({ ...ok, isIOS: true, secureContext: false })).toBe(
      "insecure_context"
    );
  });

  it("blames the browser only when everything else is in order", () => {
    expect(pushUnavailableReason({ ...ok, supported: false })).toBe("unsupported");
  });
});

describe("isSecureContextNow", () => {
  // This suite runs in node, where there is no `window` at all, so the flag is
  // stubbed rather than poked at. That absence is itself one of the cases below.
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads window.isSecureContext", () => {
    vi.stubGlobal("window", { isSecureContext: false });
    expect(isSecureContextNow()).toBe(false);
    vi.stubGlobal("window", { isSecureContext: true });
    expect(isSecureContextNow()).toBe(true);
  });

  it("treats an environment without the flag as secure", () => {
    // A missing global is a harness, not a deployment problem, and claiming
    // otherwise would put a certificate warning in front of a developer who has
    // nothing wrong with their setup.
    vi.stubGlobal("window", {});
    expect(isSecureContextNow()).toBe(true);
    expect(typeof globalThis.window).toBe("object");
  });
});

describe("pushApiSupported", () => {
  it("needs all three browser APIs", () => {
    expect(
      pushApiSupported({ hasServiceWorker: true, hasPushManager: true, hasNotification: true })
    ).toBe(true);
    expect(
      pushApiSupported({ hasServiceWorker: false, hasPushManager: true, hasNotification: true })
    ).toBe(false);
    expect(
      pushApiSupported({ hasServiceWorker: true, hasPushManager: false, hasNotification: true })
    ).toBe(false);
    expect(
      pushApiSupported({ hasServiceWorker: true, hasPushManager: true, hasNotification: false })
    ).toBe(false);
  });
});

describe("deviceLabel", () => {
  it("names the browser and the OS when both are recognised", () => {
    expect(
      deviceLabel(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )
    ).toBe("Chrome on Windows");
    expect(
      deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1")
    ).toBe("Safari on iPhone");
  });

  it("falls back gracefully for an unrecognised or missing user agent", () => {
    expect(deviceLabel("SomeOtherClient/1.0")).toBe("Unknown device");
    expect(deviceLabel(null)).toBe("Unknown device");
    expect(deviceLabel(undefined)).toBe("Unknown device");
  });
});

describe("isCurrentDevice", () => {
  it("matches only when the endpoint equals the browser's own current subscription", () => {
    expect(isCurrentDevice("https://push.example/a", "https://push.example/a")).toBe(true);
    expect(isCurrentDevice("https://push.example/a", "https://push.example/b")).toBe(false);
    expect(isCurrentDevice("https://push.example/a", null)).toBe(false);
    expect(isCurrentDevice("https://push.example/a", undefined)).toBe(false);
  });
});

// ── N5: the two decisions `enable()` makes before it talks to the server ─────

describe("applicationServerKeyMatches", () => {
  const key = "aGVsbG8"; // "hello"
  const bytes = (base64Url: string) => {
    const raw = urlBase64ToUint8Array(base64Url);
    return raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer;
  };

  it("matches a subscription made with the same key", () => {
    expect(applicationServerKeyMatches(bytes(key), key)).toBe(true);
  });

  it("rejects a subscription made with a rotated key", () => {
    // Same length, different bytes: a rotation the server would 403 forever.
    expect(applicationServerKeyMatches(bytes("aGVsbG"), key)).toBe(false);
    expect(applicationServerKeyMatches(bytes("d29ybGQ"), key)).toBe(false);
  });

  it("treats an unknown key as a match, not a mismatch", () => {
    // Browsers that do not expose PushSubscriptionOptions: resubscribing on
    // "don't know" would churn a working device on every enable.
    expect(applicationServerKeyMatches(null, key)).toBe(true);
    expect(applicationServerKeyMatches(undefined, key)).toBe(true);
  });
});

describe("pushEnableErrorMessage", () => {
  it("passes a 409's own detail through, because it is written for the user", () => {
    const detail =
      "This device is already registered to another account. Sign out there, or clear this site's data in this browser, and try again.";
    expect(pushEnableErrorMessage(409, detail)).toBe(detail);
  });

  it("still says something useful for a 409 with no detail", () => {
    expect(pushEnableErrorMessage(409, null)).toContain("another account");
    expect(pushEnableErrorMessage(409, "   ")).toContain("another account");
  });

  it("does not leak an arbitrary failure's message into the dialog", () => {
    const generic = "Could not enable push notifications on this device.";
    expect(pushEnableErrorMessage(500, "Internal Server Error")).toBe(generic);
    expect(pushEnableErrorMessage(undefined, null)).toBe(generic);
  });
});
