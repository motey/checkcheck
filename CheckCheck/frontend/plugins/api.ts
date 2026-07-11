import { initConnectivity, isOnline } from '@/utils/connectivity'
import { isLocalFirstEnabled } from '@/utils/localFirst'

export default defineNuxtPlugin({
  enforce: 'pre', // Load early so API clients are available to other plugins/stores
  setup() {
    // Get API client configs from nuxt.config.ts runtimeConfig.public.openFetch
    const clients = useRuntimeConfig().public.openFetch
    const router = useRouter()
    const toast = useToast()

    // Wire the browser online/offline signal early so the offline-auth grace
    // below can consult it regardless of whether the outbox/connectivity
    // composables have booted yet. Idempotent.
    initConnectivity()

    if (!clients) return { provide: {} }

    // The anonymous public-share surface (`/p/<token>` viewer, F4) owns its own
    // 4xx handling: a 404 on `GET /api/public/checklist/{token}` is the EXPECTED
    // "bad/expired/disabled OR password-protected" branch (→ passphrase form), and
    // a 401 on `.../join` means "log in first" — neither should toast, and a
    // logged-out visitor must NOT be bounced to /login on the initial load. The
    // global handler runs BEFORE any per-call onResponseError, so a call-site
    // override can't suppress it — we have to skip these requests here.
    const isPublicShareRequest = (ctx: any): boolean => {
      const url = ctx?.request ? String(ctx.request) : ''
      return url.includes('/api/public/')
    }

    // Per-call opt-out (F7): a call site that owns its own error UX (a friendly,
    // status-specific toast, or a deliberate silent swallow) passes
    // `skipErrorToast: true` in its fetch options. The global handler runs BEFORE
    // any per-call onResponseError, so without this opt-out a handled 4xx would
    // stack a generic "Error <code>" toast ON TOP of the call site's friendly one.
    // This generalises the `/api/public/` path-suppression above into an explicit,
    // intentional flag (see docs/archive/CARD_SHARING_PLAN_FRONTEND.md "Possible changes → #1").
    // It does NOT suppress the 401→/login redirect (handled in onResponse) — a
    // session expiry should always bounce to login regardless of this flag.
    const skipsErrorToast = (ctx: any): boolean => Boolean(ctx?.options?.skipErrorToast)

    // Offline auth grace (WI-13): while the device is offline AND the local-first
    // layer is on, a returning user lives off the IndexedDB snapshot (WI-6) and
    // queues writes in the outbox (WI-7). A 401 in that window must NOT bounce
    // them to /login (or an OIDC redirect) and tear down the usable offline board
    // — the session simply refreshes when connectivity returns. Gated on the flag
    // so the legacy path (no local data to fall back to) keeps redirecting.
    const offlineAuthGrace = () => isLocalFirstEnabled() && !isOnline()

    // Handle 401 responses: redirect to login (server handles session cleanup)
    const handleUnauthorized = () => {
      if (offlineAuthGrace()) return
      const current = router.currentRoute.value // Must access .value for reactivity
      if (current.fullPath !== '/login' && !current.query.redirect) {
        router.push({ path: '/login', query: { redirect: current.fullPath } })
      }
    }

    const handleError = (ctx: any) => {
      const status = ctx.response?.status
      if (status === 401) {
        handleUnauthorized()
        return
      }
      // A network-class failure (no HTTP response) while offline is expected —
      // the outbox is holding the write and the board runs on local data. Don't
      // stack a generic "Error request failed" toast on top of that. Only
      // suppress under the local-first flag; the legacy path still surfaces it.
      if (status === undefined && offlineAuthGrace()) return
      const method = ctx.request ? String(ctx.request).split('?')[0] : 'request'
      toast.add({
        title: `Error ${status ?? ''}`.trim(),
        description: `${ctx.options?.method?.toUpperCase() ?? 'Request'} ${method} failed`,
        color: 'error',
      })
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
            if (ctx.response?.status === 401 && !isPublicShareRequest(ctx)) handleUnauthorized()
            ;(localOptions?.onResponse as any)?.(ctx)
          },
          onResponseError(ctx) {
            if (!isPublicShareRequest(ctx) && !skipsErrorToast(ctx)) handleError(ctx)
            ;(localOptions?.onResponseError as any)?.(ctx)
          }
        }))
      }), {})
    }
  }
})