# Version 2.5 — Android Client (brainstorm)

> Rough notes only. Not a real plan. 2.5 = wrap the PWA into an Android app,
> distributed **without Google** (F-Droid + optional download from the web client).
> API-key-baking idea is **dropped** — see auth section.

## Goal / scope

- Ship an Android app that is basically the CheckCheck PWA in a native shell.
- No Play Store, no Google dependencies (FCM, Play Services, Firebase, proprietary SDKs).
- Two distribution channels:
  1. **F-Droid** — primary, trust-anchored.
  2. **Download from web client** — convenience, same APK.

## Wrapping approach — decision needed

| Option | Pros | Cons |
|---|---|---|
| **TWA / Bubblewrap** | thinnest, reuses installed browser, tiny APK | needs Chrome + Digital Asset Links; **bad fit for F-Droid** (users may not have Chrome → ugly fallback) |
| **Capacitor / WebView wrapper** | full control, server-agnostic, no browser dependency | a real app to maintain; ships a WebView |
| **GeckoView wrapper** | Mozilla engine, Google-free | heavier APK |

- **Leaning: Capacitor/WebView.** Best fit for "no Google + F-Droid + point at any server".
- Open Q: does the PWA already work fully inside a plain WebView (service worker, IndexedDB, offline auth)? Needs testing — WebView SW support has historically been patchy.

## F-Droid constraints (design around these NOW)

- Builds **from source, reproducibly** — we can't hand them a binary.
- **No proprietary deps** — no Play Services / FCM / Firebase / proprietary analytics/crash.
- **One canonical APK for everyone** → per-user/per-server customized builds are *impossible* on F-Droid.
  - ⇒ F-Droid build must be **generic**: user enters/scans server URL on first launch (Nextcloud/Mastodon/Matrix pattern).
- Push notifications: if we ever want them, plan for **UnifiedPush** or web-push, not FCM.

## Distribution: download-from-web-client

- Good UX (skip "what's your server URL?" friction), but:
  - Ship the **same generic APK**; only pre-fill the **server URL** (via filename or a deep link / QR), never a credential.
  - Do **not** rebuild+re-sign per download if avoidable — brittle. Prefer first-launch deep link / QR handoff.
  - Requires user to enable "install from unknown sources" — needs clear instructions.
  - Show signing fingerprint so users can verify it matches the F-Droid build.
- **Use the same signing key for both channels** so updates cross-flow (a user who sideloads can later update from F-Droid and vice versa). Decision: who holds/manages the key?

## Auth — no baked-in API key

- APKs are trivially decompilable → any embedded secret is a *published* secret. Dropped.
- Instead: **device-pairing handoff** (also fits the offline-first per-device token model we already need):
  1. User logged into web client clicks "Get Android app".
  2. Server issues a **short-lived, single-use pairing token** (server URL + token, ~60–120s TTL, scoped to "register one device").
  3. Deliver via **QR code** (robust — works even for F-Droid installs) or a deep link the download page fires.
  4. App exchanges pairing token → **own long-lived per-device credential/refresh token**, stored in Android Keystore / EncryptedSharedPreferences.
- Works identically for F-Droid and web-download builds.
- Open Q: how does this map onto the current 2.0 device/token model? (check backend auth before designing.)

## Decisions to make

- [ ] Wrapper tech: Capacitor vs TWA vs GeckoView.
- [ ] Does the PWA run correctly in a plain WebView? (SW / IndexedDB / offline auth)
- [ ] Signing key ownership + reproducible-build setup for F-Droid.
- [ ] Server URL provisioning mechanism (QR vs deep link vs manual).
- [ ] Pairing-token endpoint + per-device credential lifecycle.
- [ ] Push notifications: skip for 2.5? or commit to UnifiedPush?
- [ ] Min Android API level / WebView baseline.

## Advantages / disadvantages of the whole effort

**Pros**
- Reaches Android users without Google entanglement.
- Reuses ~all existing PWA/offline work — thin shell.
- F-Droid gives free, trusted, auto-updating distribution.

**Cons / risks**
- WebView quirks vs real Chrome (SW, storage eviction, offline auth).
- F-Droid reproducible-build + review process has a learning curve and lead time.
- Maintaining a native shell + signing key is ongoing overhead.
- No push without extra UnifiedPush work.

## Out of scope for 2.5

- iOS.
- Baked-in API keys / per-user custom builds.
- Play Store.
