import { useDebounceFn } from "@vueuse/core";

// Anonymous public-card data source for the `/p/<token>` viewer (Frontend F4).
//
// DESIGN: the authed `CheckList`/`CheckListItem` components are tightly coupled
// to the session-backed Pinia stores (`checklist` / `checklist_item` / `useSync`)
// and call the authed `/api/checklist/...` endpoints directly. Rather than
// shoehorn anonymous data through those stores, the public viewer owns its own
// slim, self-contained data source: this composable holds the card + items in
// local refs, talks to the token-authed `/api/public/checklist/{token}/...`
// surface, gates writes through the SAME `usePermissions` ladder over
// `card.my_permission` (the public link's level, P0.1), and drives a dedicated
// anonymous SSE. It is instantiated once per page mount (NOT a shared composable)
// so its EventSource and state are scoped to the single open card.
//
// The token is the capability. When a link is password-protected the backend
// returns the SAME 404 as a bad/expired/disabled link (no oracle), so a 404 on
// the initial load drops us into the passphrase branch. Unlocking yields a
// short-lived `grant` (NEVER the passphrase) that we replay on every subsequent
// public call via `?share_grant=` and persist in sessionStorage keyed by token.

export type PublicCardStatus = "loading" | "ready" | "locked" | "gone";

function grantStorageKey(token: string): string {
  return `checkcheck:public-grant:${token}`;
}

export function usePublicCard(token: string) {
  const { $checkapi } = useNuxtApp();

  const card = ref<CheckListType | null>(null);
  const items = ref<CheckListItemType[]>([]);
  const status = ref<PublicCardStatus>("loading");
  // Populated on a failed unlock attempt so the form can show "incorrect passphrase".
  const unlockError = ref<string | null>(null);
  const unlocking = ref(false);
  const joining = ref(false);

  // The short-lived grant proving the passphrase, replayed on every call. Held in
  // memory + sessionStorage (survives a reload of the viewer tab, lost on close).
  let grant: string | null = null;
  if (import.meta.client) {
    try {
      grant = sessionStorage.getItem(grantStorageKey(token));
    } catch {
      grant = null;
    }
  }
  function persistGrant(g: string) {
    grant = g;
    try {
      sessionStorage.setItem(grantStorageKey(token), g);
    } catch {
      /* sessionStorage may be unavailable (privacy mode) — in-memory is enough */
    }
  }

  // Replay the grant on every public call (query param mirrors the backend; the
  // passphrase itself never travels in the URL).
  function withGrant<T extends Record<string, unknown>>(query?: T) {
    return grant ? { ...(query ?? {}), share_grant: grant } : query;
  }

  const { can } = usePermissions();
  const canCheck = computed(() => can(card.value, "check"));
  const canEdit = computed(() => can(card.value, "edit"));

  function statusOf(err: unknown): number | undefined {
    const e = err as { response?: { status?: number }; statusCode?: number };
    return e?.response?.status ?? e?.statusCode;
  }

  async function fetchCard(): Promise<CheckListType> {
    return await $checkapi("/api/public/checklist/{token}", {
      method: "get",
      path: { token },
      query: withGrant(),
    });
  }

  async function fetchItems(): Promise<void> {
    const page = await $checkapi("/api/public/checklist/{token}/item", {
      method: "get",
      path: { token },
      query: withGrant({ limit: 999999 }),
    });
    items.value = [...page.items].sort((a, b) => a.position.index - b.position.index);
  }

  // Initial load (and retry after unlock). 404 → passphrase branch.
  async function load(): Promise<void> {
    status.value = "loading";
    try {
      card.value = await fetchCard();
    } catch (err) {
      if (statusOf(err) === 404) {
        status.value = "locked";
        return;
      }
      status.value = "gone";
      return;
    }
    try {
      await fetchItems();
    } catch {
      items.value = [];
    }
    status.value = "ready";
    connectSync();
  }

  async function unlock(password: string): Promise<boolean> {
    unlockError.value = null;
    unlocking.value = true;
    try {
      const res: UnlockResultType = await $checkapi("/api/public/checklist/{token}/unlock", {
        method: "post",
        path: { token },
        body: { password },
      });
      persistGrant(res.grant);
      await load();
      // load() lands on "ready" when the grant unlocked it; anything else means
      // the grant didn't help (shouldn't normally happen right after a 200 unlock).
      const ok = status.value === "ready";
      if (!ok) unlockError.value = "Incorrect passphrase.";
      return ok;
    } catch (err) {
      // The backend returns the same 404 for a wrong passphrase as for a bad link.
      unlockError.value =
        statusOf(err) === 404 ? "Incorrect passphrase." : "Could not unlock this link.";
      return false;
    } finally {
      unlocking.value = false;
    }
  }

  // ── Item writes (gated by my_permission via usePermissions) ───────────────

  async function toggleItem(item: CheckListItemType): Promise<void> {
    if (!canCheck.value) return;
    const next = !item.state.checked;
    try {
      const resState = await $checkapi(
        "/api/public/checklist/{token}/item/{checklist_item_id}/state",
        {
          method: "patch",
          path: { token, checklist_item_id: item.id },
          query: withGrant(),
          body: { checked: next } as CheckListItemStateUpdateType,
        }
      );
      const idx = items.value.findIndex((i) => i.id === item.id);
      if (idx !== -1) items.value[idx]!.state = resState;
    } catch (err) {
      console.error("public toggleItem failed", err);
    }
  }

  async function updateItemText(item: CheckListItemType, text: string): Promise<void> {
    if (!canEdit.value) return;
    try {
      const res = await $checkapi("/api/public/checklist/{token}/item/{checklist_item_id}", {
        method: "patch",
        path: { token, checklist_item_id: item.id },
        query: withGrant(),
        body: { text } as CheckListItemUpdateType,
      });
      const idx = items.value.findIndex((i) => i.id === item.id);
      if (idx !== -1) items.value.splice(idx, 1, res);
    } catch (err) {
      console.error("public updateItemText failed", err);
    }
  }

  async function addItem(): Promise<void> {
    if (!canEdit.value) return;
    try {
      const res = await $checkapi("/api/public/checklist/{token}/item", {
        method: "post",
        path: { token },
        query: withGrant(),
        body: {} as CheckListItemCreateType,
      });
      items.value.push(res);
      items.value.sort((a, b) => a.position.index - b.position.index);
    } catch (err) {
      console.error("public addItem failed", err);
    }
  }

  async function deleteItem(item: CheckListItemType): Promise<void> {
    if (!canEdit.value) return;
    try {
      await $checkapi("/api/public/checklist/{token}/item/{checklist_item_id}", {
        method: "delete",
        path: { token, checklist_item_id: item.id },
        query: withGrant(),
      });
      const idx = items.value.findIndex((i) => i.id === item.id);
      if (idx !== -1) items.value.splice(idx, 1);
    } catch (err) {
      console.error("public deleteItem failed", err);
    }
  }

  // ── Join ("add to my deck") ───────────────────────────────────────────────
  // 401 → logged out (the global /login redirect is suppressed for /api/public,
  // so the caller routes to /login?redirect=/p/<token> itself). 200 → real
  // collaborator added; returns the card so the caller can open /card/<id>.
  type JoinOutcome = { ok: true; card: CheckListType } | { ok: false; loggedOut: boolean };
  async function join(): Promise<JoinOutcome> {
    joining.value = true;
    try {
      const res: CheckListType = await $checkapi("/api/public/checklist/{token}/join", {
        method: "post",
        path: { token },
        query: withGrant(),
      });
      return { ok: true, card: res };
    } catch (err) {
      return { ok: false, loggedOut: statusOf(err) === 401 };
    } finally {
      joining.value = false;
    }
  }

  // ── Anonymous live updates (SSE) ──────────────────────────────────────────
  // A dedicated EventSource scoped to this single card. Closed on unmount — a
  // live /api/sync blocks Playwright teardown (the specs navigate to about:blank).

  let es: EventSource | null = null;

  const reloadItemsDebounced = useDebounceFn(() => {
    fetchItems().catch(() => {});
  }, 300);

  function connectSync(): void {
    if (es || !import.meta.client || !card.value) return;
    const params = new URLSearchParams({ token });
    if (grant) params.set("share_grant", grant);
    es = new EventSource(`/api/sync?${params.toString()}`);
    es.onmessage = (event: MessageEvent) => {
      let noti: SyncNotificationType;
      try {
        noti = JSON.parse(event.data) as SyncNotificationType;
      } catch {
        return;
      }
      if (!card.value || noti.cl_id !== card.value.id) return;
      switch (noti.upd_prop) {
        case "item_state":
        case "item_text":
        case "item_position":
        case "item_created":
        case "item_deleted":
          reloadItemsDebounced();
          break;
        case "checklist":
        case "checklist_label":
          fetchCard()
            .then((c) => (card.value = c))
            .catch(() => {});
          break;
        case "checklist_deleted":
          status.value = "gone";
          disconnectSync();
          break;
      }
    };
    es.onerror = () => {
      // Browser retries automatically; visibility only.
      console.warn("[public-sync] SSE connection error — browser will retry");
    };
  }

  function disconnectSync(): void {
    es?.close();
    es = null;
  }

  return {
    card,
    items,
    status,
    unlockError,
    unlocking,
    joining,
    canCheck,
    canEdit,
    load,
    unlock,
    toggleItem,
    updateItemText,
    addItem,
    deleteItem,
    join,
    disconnectSync,
  };
}
