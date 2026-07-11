import { onScopeDispose, readonly, ref } from "vue";
import { initConnectivity, isOnline, onConnectivityChange } from "@/utils/connectivity";

// ── Reactive connectivity for components (WI-12) ─────────────────────────────
//
// A thin Vue wrapper over the framework-light `utils/connectivity` signal so
// online-only surfaces (share / invite / notification) can disable their
// affordances and show a hint while offline. Flag-agnostic on purpose: these
// surfaces stay server-authoritative in both the legacy and localFirst worlds,
// so the gate must not depend on the localFirst rollout flag (that is what
// `useOutbox().online` keys off).
//
// The underlying signal is fed by the browser online/offline events, the SSE
// stream state (useSync onopen/onerror), and the outbox probe — see
// utils/connectivity.ts.
export function useConnectivity() {
  initConnectivity();
  const online = ref(isOnline());
  const stop = onConnectivityChange((v) => {
    online.value = v;
  });
  onScopeDispose(stop);
  return { online: readonly(online) };
}
