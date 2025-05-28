# Frontend setup log

This document just documents my steps to install the frontend

* Init frontend with `bun x nuxi@latest init frontend --packageManager bun --gitInit false`
* `cd frontend`
*  `bun add -D nuxt-open-fetch` and enable in `nuxt.config.ts`  (https://github.com/enkot/nuxt-open-fetch?tab=readme-ov-file#quick-setup & https://nuxt-open-fetch.vercel.app/setup/configuration)
```ts
// https://nuxt.com/docs/api/configuration/nuxt-config
// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2024-04-03',
  devtools: { enabled: true },
  ssr: false,
  modules: ['nuxt-open-fetch', '@nuxt/ui'],
  openFetch: {
    disableNuxtPlugin: true,
    clients: {
      checkapi: {
        schema: 'http://localhost:8181/openapi.json',
        baseURL: 'http://localhost:8181/',
      } // Register the checkapi client here 
    }
  },
  runtimeConfig: {
    public: {
      openFetch: {
        checkapi: {
          schema: 'http://localhost:8181/openapi.json',
          baseURL: 'http://localhost:8181/',
        }
      }
    }
  },
});

```





