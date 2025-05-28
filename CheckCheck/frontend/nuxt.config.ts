// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  future: {
    compatibilityVersion: 4
  },

  compatibilityDate: '2024-11-27',
  devtools: { enabled: true },
  ssr: false,
  modules: ["nuxt-open-fetch", "@nuxt/ui", "@pinia/nuxt", "nuxt-lodash", "@nuxt/fonts", "@nuxt/icon"],
  css: ["~/assets/css/main.css"],
  ui: {
    theme: {
      colors: ["primary", "error"],
    },
  },
  nitro: {
    devProxy: {
      "/api": {
        target: "http://localhost:8181/api",
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
        protocol: 'ws',
        host: 'localhost',
        port: 3000,
        clientPort: 3000,
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
  runtimeConfig: {
    public: {
      openFetch: {
        checkapi: {
          schema: "../openapi.json",
          baseURL: "/api",
        },
      },
    },
  },
});
