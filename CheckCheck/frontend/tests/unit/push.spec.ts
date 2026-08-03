import { describe, it, expect } from "vitest";
import {
  canOfferPushEnable,
  deviceLabel,
  isCurrentDevice,
  isIOSPlatform,
  isStandaloneDisplay,
  pushApiSupported,
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
