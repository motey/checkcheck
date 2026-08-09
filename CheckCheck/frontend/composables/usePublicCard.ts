import { useDebounceFn } from "@vueuse/core";
import { findNewPlacementForItem } from "~/utils/helpers";
import { fractionalIndexBetween } from "@/utils/outboxOps";

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
// That split is the *data* layer only. The presentation is shared: since issue #11
// the viewer renders the same `components/CardParts/*` the authed card does, so
// features like the separated-checked layout cannot drift out of the public link
// again. Writes here stay deliberately un-optimistic and outbox-free (decision 10):
// call, then patch the local ref from the response. The public surface is online
// only: it is not in the IndexedDB snapshot and not in the `/api/changes` feed.
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

// A view-level visitor's collapse state is theirs alone (see setCollapsed), so it
// lives next to the grant: sessionStorage survives a reload of the viewer tab and
// does not outlive it, which is exactly the rule the grant already follows.
function collapsedStorageKey(token: string): string {
  return `checkcheck:public-collapsed:${token}`;
}

/**
 * Deterministic item order, matching the authed store's `compareByPositionThenId`:
 * by fractional `position.index`, then `id` as the tiebreak. Two items can share
 * an index after a reorder converged on the same midpoint; ordering by id then
 * keeps the list stable instead of flapping.
 */
function compareByPositionThenId(a: CheckListItemType, b: CheckListItemType): number {
  return a.position.index - b.position.index || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
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

  // The card's "separate checked items" layout needs the two halves separately,
  // in the same order the authed store puts them in.
  const uncheckedItems = computed(() =>
    items.value.filter((i) => !i.state.checked).sort(compareByPositionThenId)
  );
  const checkedItems = computed(() =>
    items.value.filter((i) => i.state.checked).sort(compareByPositionThenId)
  );

  // Whether the checked section is folded away. Seeded from the card on load, or
  // from this visitor's own session when they have toggled it here before.
  const collapsed = ref(true);
  function readStoredCollapsed(): boolean | null {
    try {
      const raw = sessionStorage.getItem(collapsedStorageKey(token));
      return raw === null ? null : raw === "true";
    } catch {
      return null;
    }
  }

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
    items.value = [...page.items].sort(compareByPositionThenId);
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
    // The card's own flag is the default; a value this visitor stored in their own
    // session (a view-level toggle, which never reaches the server) wins over it.
    const stored = import.meta.client ? readStoredCollapsed() : null;
    collapsed.value = stored ?? card.value.checked_items_collapsed !== false;
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

  // ── Card writes (gated by my_permission via usePermissions) ───────────────

  /**
   * Fold or unfold the checked section.
   *
   * `checked_items_collapsed` is a column on `checklist`, not a per-user row, so a
   * persisted toggle is visible to the owner and to every other visitor on the
   * link. At `check` or better that is fine: such a link already writes to the card
   * every time somebody ticks a box, so one more display flag changes nothing about
   * what the capability means. A `view` link grants no write path at all, so its
   * visitor collapses the section in their own session only, which is the one
   * asymmetry between the levels here.
   */
  async function setCollapsed(value: boolean): Promise<void> {
    collapsed.value = value;
    try {
      sessionStorage.setItem(collapsedStorageKey(token), String(value));
    } catch {
      /* sessionStorage may be unavailable (privacy mode): in-memory is enough */
    }
    if (!canCheck.value) return;
    try {
      card.value = await $checkapi("/api/public/checklist/{token}", {
        method: "patch",
        path: { token },
        query: withGrant(),
        body: { checked_items_collapsed: value },
      });
    } catch (err) {
      console.error("public setCollapsed failed", err);
    }
  }

  /** Rename the card / rewrite its notes. Edit links only. */
  async function updateCard(patch: { name?: string; text?: string }): Promise<void> {
    if (!canEdit.value) return;
    try {
      card.value = await $checkapi("/api/public/checklist/{token}", {
        method: "patch",
        path: { token },
        query: withGrant(),
        body: patch,
      });
    } catch (err) {
      console.error("public updateCard failed", err);
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

  /**
   * Append an item, or (when `afterItem` is given, i.e. Enter was pressed on a row)
   * insert one right below it. Returns the created item so the caller can move focus
   * into it, mirroring the authed editor's add-below flow.
   */
  async function addItem(
    afterItem?: CheckListItemType
  ): Promise<CheckListItemType | null> {
    if (!canEdit.value) return null;
    let body: CheckListItemCreateType = {} as CheckListItemCreateType;
    if (afterItem) {
      const sorted = items.value.slice().sort(compareByPositionThenId);
      const idx = sorted.findIndex((i) => i.id === afterItem.id);
      const next = idx !== -1 ? sorted[idx + 1] : undefined;
      body = {
        position: {
          index: fractionalIndexBetween(
            afterItem.position.index,
            next?.position.index ?? null
          ),
        },
      } as CheckListItemCreateType;
    }
    try {
      const res = await $checkapi("/api/public/checklist/{token}/item", {
        method: "post",
        path: { token },
        query: withGrant(),
        body,
      });
      items.value.push(res);
      items.value.sort(compareByPositionThenId);
      return res;
    } catch (err) {
      console.error("public addItem failed", err);
      return null;
    }
  }

  /**
   * Persist a drag-reorder. The wire format is a plain index PATCH carrying a
   * fractional index the client computed itself, so this surface needs no
   * anonymous twins of the `move/above` / `move/under` routes.
   *
   * The math is the authed local-first path's, reused rather than restated
   * (`findNewPlacementForItem` picks the neighbour the dropped item landed next to,
   * `fractionalIndexBetween` returns the midpoint of the two rows bracketing that
   * slot, or one POSITION_END_GAP past a single neighbour). There is one
   * implementation of it, and it already matches what the server's midpoint math
   * would have produced.
   */
  async function reorderItems(
    newOrder: CheckListItemType[],
    movedItem: CheckListItemType
  ): Promise<void> {
    if (!canEdit.value) return;
    const placement = findNewPlacementForItem(movedItem, newOrder);
    if (!placement.placement || !placement.target_neighbor_item) return;
    const other = placement.target_neighbor_item as CheckListItemType;
    // Neighbours are read out of the *current* (pre-drop) order, exactly as the
    // authed `_localMoveItem` does.
    const sorted = items.value.slice().sort(compareByPositionThenId);
    const otherIdx = sorted.findIndex((i) => i.id === other.id);
    const otherIndex = other.position.index;
    const newIndex =
      placement.placement === "below"
        ? // Land between the neighbour and its next-higher row (or past the end).
          fractionalIndexBetween(otherIndex, sorted[otherIdx + 1]?.position.index ?? null)
        : // Land between the neighbour's next-lower row and it (or before the start).
          fractionalIndexBetween(sorted[otherIdx - 1]?.position.index ?? null, otherIndex);
    try {
      const resPos = await $checkapi(
        "/api/public/checklist/{token}/item/{checklist_item_id}/position",
        {
          method: "patch",
          path: { token, checklist_item_id: movedItem.id },
          query: withGrant(),
          body: { index: newIndex },
        }
      );
      const idx = items.value.findIndex((i) => i.id === movedItem.id);
      if (idx !== -1) items.value[idx]!.position = resPos;
      items.value.sort(compareByPositionThenId);
    } catch (err) {
      console.error("public reorderItems failed", err);
      // The server rejected the move; pull the authoritative order back.
      fetchItems().catch(() => {});
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
            .then((c) => {
              card.value = c;
              // The refetched card carries `checked_items_collapsed`, which another
              // viewer (or the owner) may have just changed. Only adopt it when this
              // visitor is on a check-or-better link: at `view` the collapse state
              // is session-local, so somebody else's toggle must not yank it shut.
              if (canCheck.value) collapsed.value = c.checked_items_collapsed !== false;
            })
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
    uncheckedItems,
    checkedItems,
    collapsed,
    status,
    unlockError,
    unlocking,
    joining,
    canCheck,
    canEdit,
    load,
    unlock,
    setCollapsed,
    updateCard,
    toggleItem,
    updateItemText,
    addItem,
    deleteItem,
    reorderItems,
    join,
    disconnectSync,
  };
}
