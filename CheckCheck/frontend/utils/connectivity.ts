// ── Online/offline detection (WI-7) ──────────────────────────────────────────
//
// The connectivity signal the outbox engine drains on. Framework-light (a plain
// module with listeners, no Vue refs) so it stays importable outside a component
// and easy to reason about. Signals, in order of authority:
//
//   1. `navigator.onLine` + the window `online` / `offline` events — the cheap
//      baseline. Note `navigator.onLine === true` only means "has a network
//      interface", not "the server is reachable", so it is necessary but not
//      sufficient.
//   2. The SSE stream state (composables/useSync.ts `onopen`) — a live sync
//      socket is proof of real reachability; it calls `setConnectivity(true)`.
//   3. The engine's own transport: a network-class failure while `navigator`
//      still claims online can trigger a `probe()` to confirm.
//
// All best-effort: the worst case is the engine attempts a drain that fails with
// a network error and backs off — no data is lost, connectivity just corrects
// itself on the next real signal.

type Listener = (online: boolean) => void;

const listeners = new Set<Listener>();
let current = true;
let initialized = false;

function set(online: boolean): void {
  if (online === current) return;
  current = online;
  for (const l of listeners) {
    try {
      l(online);
    } catch (err) {
      console.warn("[connectivity] listener threw", err);
    }
  }
}

/** Wire the browser online/offline events. Idempotent; client-only. */
export function initConnectivity(): void {
  if (initialized) return;
  if (typeof window === "undefined" || typeof navigator === "undefined") return;
  initialized = true;
  current = navigator.onLine;
  window.addEventListener("online", () => set(true));
  window.addEventListener("offline", () => set(false));
}

/** Current best-known connectivity. */
export function isOnline(): boolean {
  return current;
}

/** Feed an external connectivity signal (SSE open/close, a probe result). */
export function setConnectivity(online: boolean): void {
  set(online);
}

/** Subscribe to connectivity changes; returns an unsubscribe fn. */
export function onConnectivityChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Actively confirm reachability with a cheap same-origin request. Used when
 * `navigator.onLine` is optimistic (interface up but server unreachable). Updates
 * the connectivity signal as a side effect and returns the result.
 */
export async function probe(url = "/api/public-config"): Promise<boolean> {
  if (typeof fetch === "undefined") return current;
  try {
    const res = await fetch(url, { method: "GET", cache: "no-store" });
    const ok = res.ok || res.status < 500; // any real HTTP answer ⇒ reachable
    set(ok);
    return ok;
  } catch {
    set(false);
    return false;
  }
}
