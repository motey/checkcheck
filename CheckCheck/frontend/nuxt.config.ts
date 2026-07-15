// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  future: {
    compatibilityVersion: 4,
  },

  compatibilityDate: "2024-11-27",
  devtools: { enabled: true },
  ssr: false,
  modules: [
    "nuxt-open-fetch",
    "@nuxt/ui",
    "@pinia/nuxt",
    "@nuxt/fonts",
    "@nuxt/icon",
    "@nuxtjs/color-mode",
    "@vite-pwa/nuxt",
  ],
  css: ["~/assets/css/main.css"],
  ui: {
    theme: {
      colors: ["primary", "error"],
    },
  },
  colorMode: {
    preference: "system", // default value of $colorMode.preference
    fallback: "light", // fallback value if not system preference found
    hid: "nuxt-color-mode-script",
    globalName: "__NUXT_COLOR_MODE__",
    componentName: "ColorScheme",
    classPrefix: "",
    classSuffix: "",
    storage: "localStorage", // or 'sessionStorage' or 'cookie'
    storageKey: "nuxt-color-mode",
  },
  nitro: {
    devProxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET ?? "http://localhost:8181/api",
        changeOrigin: true,
        headers: {
          Host: "localhost:3000",
        },
      },
      "/docs": {
        target: "http://localhost:8181/docs",
      },
      "/openapi.json": {
        target: "http://localhost:8181/openapi.json",
      },
    },
  },
  vite: {
    server: {
      hmr: {
        protocol: "ws",
        host: "localhost",
        // Use the same port the dev server listens on so HMR works when the
        // server is started on a non-default port (e.g. PORT=3001 for E2E tests).
        port: parseInt(process.env.PORT ?? "3000"),
        clientPort: parseInt(process.env.PORT ?? "3000"),
      },
    },
  },
  openFetch: {
    disableNuxtPlugin: true,
    clients: {
      checkapi: {
        schema: "../openapi.json",
        baseURL: "/api",
      },
    },
  },
  // ── Progressive Web App (WI-13, Phase 3) ──────────────────────────────────
  // Precache the SPA app shell (Workbox) so an airplane-mode cold start renders
  // the board from local data (IndexedDB snapshot, WI-6). The service worker is
  // flag-agnostic — installability/offline-shell is a general PWA feature — but
  // it deliberately NEVER caches `/api/*`: offline reads come from the local
  // snapshot, never a stale HTTP response, and writes queue in the outbox (WI-7).
  pwa: {
    registerType: "prompt", // surface a "new version available" toast (see plugins/pwa.client.ts)
    manifest: {
      name: "CheckCheck",
      short_name: "CheckCheck",
      description: "Shared checklists that work offline.",
      lang: "en",
      display: "standalone",
      start_url: "/",
      scope: "/",
      theme_color: "#FBBF24",
      background_color: "#ffffff",
      icons: [
        { src: "icons/pwa-64x64.png", sizes: "64x64", type: "image/png" },
        { src: "icons/pwa-192x192.png", sizes: "192x192", type: "image/png" },
        { src: "icons/pwa-512x512.png", sizes: "512x512", type: "image/png" },
        {
          src: "icons/pwa-maskable-512x512.png",
          sizes: "512x512",
          type: "image/png",
          purpose: "maskable",
        },
      ],
    },
    workbox: {
      globPatterns: ["**/*.{js,css,html,ico,png,svg,woff,woff2}"],
      // SPA fallback: offline navigations to client-only routes (/login,
      // /card/:id, /p/:token) resolve to the precached shell.
      navigateFallback: "/",
      // …but never let the fallback (or any Workbox route) swallow the API,
      // docs, or the OpenAPI schema — those must always hit the network.
      navigateFallbackDenylist: [/^\/api\//, /^\/docs/, /^\/openapi\.json/],
      cleanupOutdatedCaches: true,
      // No runtimeCaching entry for `/api` on purpose: unmatched requests fall
      // through to the network and fail cleanly offline (never served stale).
    },
    // The service worker only registers in a real build; keep dev untouched so
    // HMR and the flag-off legacy path behave exactly as before.
    devOptions: {
      enabled: false,
    },
  },

  app: {
    head: {
      meta: [{ name: "theme-color", content: "#FBBF24" }],
      link: [
        // SVG favicon (crisp at every size) with an .ico fallback for legacy browsers.
        { rel: "icon", type: "image/svg+xml", href: "/favicon.svg" },
        { rel: "icon", type: "image/x-icon", href: "/favicon.ico" },
        { rel: "apple-touch-icon", href: "/icons/apple-touch-icon-180x180.png" },
      ],
    },
  },

  runtimeConfig: {
    public: {
      // Client-side rollout gate for the local-first layer (Phase 2 / WI-6+).
      // Deploy default; override per-deploy with NUXT_PUBLIC_LOCAL_FIRST=false.
      // At runtime a `?localFirst=0` query param / localStorage override wins
      // (the legacy refetch path is kept one release behind this kill-switch —
      // see utils/localFirst.ts). Flipped ON by default in WI-15 for the 2.0
      // release: local-first (offline PWA) is the shipping default.
      localFirst: true,
      openFetch: {
        checkapi: {
          schema: "../openapi.json",
          baseURL: "/api",
        },
      },
    },
  },
});
