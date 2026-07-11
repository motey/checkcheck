import { watch } from "vue";

// ── PWA update flow (WI-13) ──────────────────────────────────────────────────
//
// `registerType: "prompt"` (nuxt.config `pwa`) means a freshly deployed service
// worker waits instead of auto-activating, so a user mid-edit is never reloaded
// out from under themselves. `@vite-pwa/nuxt` exposes that waiting state on
// `$pwa.needRefresh`; here we surface it as a single, non-auto-dismissing toast
// with a "Reload" action that activates the new worker and reloads.
//
// `$pwa` is only wired in a real build (devOptions.enabled is false), so this is
// a no-op under `nuxt dev` and never fires on the flag-off legacy path.
export default defineNuxtPlugin(() => {
  const pwa = useNuxtApp().$pwa;
  if (!pwa) return;

  const toast = useToast();

  watch(
    () => pwa.needRefresh,
    (needRefresh) => {
      if (!needRefresh) return;
      toast.add({
        title: "New version available",
        description: "Reload to get the latest CheckCheck.",
        icon: "i-lucide-download",
        color: "primary",
        duration: 0, // stay until the user acts
        actions: [
          {
            label: "Reload",
            onClick: () => pwa.updateServiceWorker(true),
          },
        ],
      });
    },
    { immediate: true },
  );
});
