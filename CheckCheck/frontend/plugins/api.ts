export default defineNuxtPlugin({
  enforce: 'pre', // Load early so API clients are available to other plugins/stores
  setup() {
    // Get API client configs from nuxt.config.ts runtimeConfig.public.openFetch
    const clients = useRuntimeConfig().public.openFetch
    const router = useRouter()

    if (!clients) return { provide: {} }

    // Handle 401 responses: redirect to login (server handles session cleanup)
    const handleUnauthorized = () => {
      const current = router.currentRoute.value // Must access .value for reactivity
      if (current.fullPath !== '/login' && !current.query.redirect) {
        router.push({ path: '/login', query: { redirect: current.fullPath } })
      }
    }

    // Create API clients with request/response interceptors
    return {
      provide: Object.entries(clients).reduce((acc, [name, options]) => ({
        ...acc,
        [name]: createOpenFetch(localOptions => ({
          ...options,
          ...localOptions,
          onRequest: localOptions?.onRequest,
          onResponse(ctx) {
            if (ctx.response?.status === 401) handleUnauthorized()
            ;(localOptions?.onResponse as any)?.(ctx)
          },
          onResponseError(ctx) {
            if (ctx.response?.status === 401) handleUnauthorized()
            ;(localOptions?.onResponseError as any)?.(ctx)
          }
        }))
      }), {})
    }
  }
})