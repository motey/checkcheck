# Installing CheckCheck as an app (PWA)

CheckCheck is a Progressive Web App (PWA): from a supported browser you can
**install** it so it launches like a native app, with its own window, no address
bar, its own icon in the launcher/home screen, and it keeps working offline.

This page has two parts:

- [For users](#for-users): how to install it on each platform.
- [For admins / self-hosters](#for-admins--self-hosters): what your instance
  must provide for the install to be offered at all, and how to verify it.

> **Installed app vs. bookmark shortcut.** Some browsers (notably DuckDuckGo and
> Firefox on Android) can only add a *shortcut* to the home screen, not install
> the app. A shortcut opens the site in a normal browser tab, and every time you
> open it from a sub-page it can spawn a **new tab that piles up**. A real
> install runs in a single standalone window and always reopens the same app.
> If you see tabs piling up, you have a shortcut, so install from a supported
> browser instead (see below).

---

## For users

Installing gives you a standalone window (no browser chrome), a launcher/home
icon, and full offline use. Which browser you use matters, because only some can
install a PWA.

### Android

**Supported:** Chrome, Edge, Brave, Samsung Internet.
**Not supported (shortcut only):** DuckDuckGo, Firefox, Opera.

1. Open your CheckCheck URL in **Chrome** (or another supported browser).
2. Either:
   - open the side menu and tap **Install app**, or
   - use the browser's **⋮ menu → Install app** (it must say *Install app*, not
     *Add to Home screen*).
3. Confirm. CheckCheck now appears in your app drawer and launches in its own
   window.

If your browser only offers *Add to Home screen* (a shortcut), switch to a
supported browser. The shortcut is the thing that opens in a tab and piles up.

### iPhone / iPad (iOS / iPadOS)

**Supported:** Safari **only**. On iOS every browser (Chrome, Firefox,
DuckDuckGo, and so on) uses Apple's engine, but only **Safari** can add a real
home-screen app. There is no automatic "Install" button on iOS; it is always a
manual step.

1. Open your CheckCheck URL in **Safari**.
2. Tap the **Share** button (the square with an up-arrow).
3. Choose **Add to Home Screen**.
4. Tap **Add**. It launches full-screen from your home screen.

### Desktop (Windows / macOS / Linux)

**Supported:** Chrome, Edge, Brave (and other Chromium browsers).

1. Open your CheckCheck URL.
2. Click the **install icon** in the address bar (a small monitor/⊕ icon), or
   use **⋮ menu → Install CheckCheck…**, or the **Install app** entry in the
   app's side menu.
3. It opens as a standalone desktop window and gets its own app icon.

Firefox on desktop does not support installing PWAs.

### Browser support at a glance

| Platform | Installs a real app | Shortcut only / not supported |
|---|---|---|
| Android | Chrome, Edge, Brave, Samsung Internet | DuckDuckGo, Firefox, Opera |
| iOS / iPadOS | Safari (Share → Add to Home Screen) | every other browser |
| Desktop | Chrome, Edge, Brave, other Chromium | Firefox, Safari (macOS) |

---

## For admins / self-hosters

A browser only offers to *install* CheckCheck when the instance meets the PWA
installability criteria. The app ships the manifest, service worker, and icons
for you; your job is to serve them correctly. Three things must hold:

### 1. Serve it over HTTPS (a secure context)

Installability and the service worker require a **secure context**: either
`https://` or plain `http://localhost`. A production instance on plain `http://`
with a real hostname **cannot be installed** and has no offline support.

- Put CheckCheck behind a TLS-terminating reverse proxy and set
  `SERVER_PUBLIC_URL` to the external **https** URL, for example
  `SERVER_PUBLIC_URL=https://checklists.example.com`. This also makes the session
  cookie `Secure` automatically. See [deployment.md](deployment.md) for the
  reverse-proxy setup.
- `http://localhost:8181` works for local testing only (localhost is treated as
  secure), but a LAN IP like `http://192.168.x.x` is **not** and will not be
  installable.

### 2. Serve the app at the root of its origin

The web app manifest declares `start_url` and `scope` of `/`, and the service
worker registers at `/sw.js` with root scope. Serve CheckCheck at the **root of
a domain or subdomain** (`https://checklists.example.com/`), not under a
sub-path (`https://example.com/checkcheck/`). A sub-path deployment breaks the
service-worker scope and the manifest URLs, and the install will not be offered.

Make sure your reverse proxy does **not** rewrite or strip paths for these
files. They must be reachable exactly as served by the container:

- `/manifest.webmanifest`
- `/sw.js`
- `/icons/…` (the PWA icons)

### 3. Nothing else to configure

There is no CheckCheck setting to "enable the PWA"; it is built into the image.
Once the instance is on HTTPS at an origin root with those files reachable,
supported browsers offer the install automatically (and the app shows its own
**Install app** button on Chromium).

> The offline/local-first behaviour is separate and already on by default. It can
> be toggled with the `NUXT_PUBLIC_LOCAL_FIRST` build/deploy flag (see
> [administration.md](administration.md) for the offline kill switch), but that
> does **not** affect installability. The service worker and app shell are
> flag-agnostic.

### Verifying installability

From any machine, confirm the shell links the manifest and the files resolve:

```bash
# The served HTML must contain a manifest link:
curl -s https://checklists.example.com/ | grep -o '<link rel="manifest"[^>]*>'
#   Expected: <link rel="manifest" href="/manifest.webmanifest">

# The manifest, service worker, and an icon must all return 200:
curl -sI https://checklists.example.com/manifest.webmanifest | head -1
curl -sI https://checklists.example.com/sw.js                 | head -1
curl -sI https://checklists.example.com/icons/pwa-512x512.png | head -1
```

On a device, use Chrome's DevTools (desktop, or `chrome://inspect` against a
phone) and open **Application → Manifest**. It shows the parsed manifest and an
**Installability** section that lists any blocking errors (no HTTPS, missing
icon, service worker not controlling the page, and so on).

### Troubleshooting: "it opens in a tab / tabs pile up"

That is the signature of a **bookmark shortcut**, not an installed app. Work
through:

1. **Wrong browser.** DuckDuckGo/Firefox on Android only make shortcuts. Reinstall
   from Chrome/Edge/Brave/Samsung Internet (Android) or Safari (iOS).
2. **Old icon still on the home screen.** A previously added shortcut does not
   upgrade itself. **Delete the old icon**, then reinstall from a supported
   browser so a fresh, real install is created.
3. **Instance not installable.** Run the verification above. The most common
   causes are: not on HTTPS, served under a sub-path, or a reverse proxy that
   doesn't pass through `/manifest.webmanifest` / `/sw.js` / `/icons/`.
