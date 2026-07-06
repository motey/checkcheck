import { isLocalFirstEnabled } from "@/utils/localFirst";
import { useOutbox } from "@/composables/useOutbox";

// ── Outbox boot (WI-7) ───────────────────────────────────────────────────────
//
// When `localFirst` is on, instantiate the shared outbox so it loads any writes
// that were queued in a previous (offline) session from IndexedDB and starts
// draining them the moment connectivity allows — the "survive restarts, replay
// on reconnect" guarantee. Flag-off, the outbox never starts and no stores
// enqueue to it, so the legacy online-first path is untouched.
//
// Registered after the API plugin (which is `enforce: 'pre'`) so `$checkapi` is
// available for the transport.
export default defineNuxtPlugin(() => {
  if (!isLocalFirstEnabled()) return;
  // Touching the shared composable constructs the engine and kicks `init()`.
  useOutbox();
});
