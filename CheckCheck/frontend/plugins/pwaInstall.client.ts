import { initPwaInstall } from "@/composables/usePwaInstall";

// Attach the `beforeinstallprompt` / `appinstalled` listeners at app startup so
// the deferred prompt is captured even if the browser fires it before the
// install button mounts. See composables/usePwaInstall.ts.
export default defineNuxtPlugin(() => {
  initPwaInstall();
});
