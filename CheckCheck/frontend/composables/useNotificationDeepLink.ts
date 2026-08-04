import { nextTick, onMounted, watch, type Ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useNotificationStore } from "@/stores/notification";
import { handleNotificationDeepLink } from "@/utils/notificationDeepLink";

// The Vue half of the email deep link (chunk E5). All the decisions live in
// `utils/notificationDeepLink.ts`; this supplies the live route, the store and
// the waiting, and owns the per-page set of notification ids already consumed.
//
// *cardRendered* is the caller's answer to "is the card the link pointed at
// actually on screen": the read only happens once it is true, because that is
// what makes marking it read honest.

/** How long to wait for the card before marking the notification read anyway.
 *  A card that was deleted, revoked, or simply fails to load must not leave the
 *  notification unread forever: the user did follow the link. */
const CARD_RENDER_TIMEOUT_MS = 5000;

export function useNotificationDeepLink(cardRendered: Ref<boolean>): void {
  const route = useRoute();
  const router = useRouter();
  const store = useNotificationStore();
  const handled = new Set<string>();

  async function waitForCard(): Promise<void> {
    if (cardRendered.value) {
      // Still yield a tick, so the overlay has painted before the badge moves.
      await nextTick();
      return;
    }
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => {
        stop();
        resolve();
      }, CARD_RENDER_TIMEOUT_MS);
      const stop = watch(cardRendered, (ready) => {
        if (!ready) return;
        clearTimeout(timer);
        stop();
        resolve();
      });
    });
    await nextTick();
  }

  async function run(): Promise<void> {
    await handleNotificationDeepLink({
      current: () => ({ path: route.path, query: route.query }),
      hasCard: !!route.params.cardId,
      waitForCard,
      markRead: async (id) => {
        await store.markRead(id);
        // The feed is usually not loaded when a link is followed from an inbox,
        // so the store's own optimistic decrement has nothing to decrement and
        // the bell would keep its badge until something else refreshed it.
        await store.refreshUnread();
      },
      replace: async (to) => {
        await router.replace(to);
      },
      handled,
    });
  }

  // Client-side only, and after mount: a redirect issued while the page is still
  // setting up would fight the router's own initial navigation, and there is no
  // "the card rendered" to wait for before there is a DOM.
  onMounted(() => {
    void run();
    // A second pass for the URL the `?card=` rewrite produced, and for a link
    // followed while the app is already open.
    watch(() => route.fullPath, () => void run());
  });
}
