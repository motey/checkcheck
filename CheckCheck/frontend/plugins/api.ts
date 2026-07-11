import { initConnectivity, isOnline, decideApiErrorAction } from '@/utils/connectivity'
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

    // Offline auth grace (WI-13): the redirect/toast/suppress decision lives in
    // the pure `decideApiErrorAction` (utils/connectivity.ts, unit-tested); the
    // plugin just injects the live flag + connectivity signals. While the
    // local-first layer is on AND the device is offline, a 401 is suppressed
    // (keep the cached board; the session refreshes on reconnect) and an expected
    // network-class failure is suppressed instead of toasting. Flag off / online
    // ⇒ exactly the legacy path.
    const errorAction = (status: number | undefined) =>
      decideApiErrorAction(status, {
        localFirstEnabled: isLocalFirstEnabled(),
        online: isOnline(),
      })

    // Handle 401 responses: redirect to login (server handles session cleanup).
    // Self-guards on the grace so the onResponse 401 path is covered too.
    const handleUnauthorized = () => {
      if (errorAction(401) !== 'redirect') return
      const current = router.currentRoute.value // Must access .value for reactivity
      if (current.fullPath !== '/login' && !current.query.redirect) {
        router.push({ path: '/login', query: { redirect: current.fullPath } })
      }
    }

    const handleError = (ctx: any) => {
      const status = ctx.response?.status
      const action = errorAction(status)
      if (action === 'redirect') {
        handleUnauthorized()
        return
      }
      if (action === 'suppress') return
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