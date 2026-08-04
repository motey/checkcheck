import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { LocationQuery } from "vue-router";

/**
 * Central handler for all URL-reflected app state.
 *
 * THE RULE: every place a user can sit in has a URL, and that URL lives here.
 * A surface the user can stay in, share a link to, or expect the back button to
 * close is a place, and adding one means adding it to this file and to the list
 * below. A transient confirmation (one question, one answer, gone) is not a
 * place and stays a local ref.
 *
 * The places, so views are shareable and back/forward work:
 *   - an opened card     -> path  `/card/<cardId>`    (see the aliases on pages/index.vue)
 *   - a settings pane    -> path  `/settings/<pane>`  (`notifications`, `api-keys`)
 *   - the label editor   -> query `?editlabels=true`
 *   - the search text    -> query `?search=<text>`
 *   - the label filter   -> query `?label=<labelId>`
 *
 * Every mutation here preserves the rest of the URL (path + remaining query) so
 * the different pieces of state stay independent and composable. Components
 * should go through this composable instead of poking at the router directly.
 */
export type SettingsPane = "notifications" | "api-keys";

/**
 * The panes `/settings/:pane` opens. Anything else in that slot opens nothing.
 *
 * The alias on pages/index.vue carries a `:pane` parameter rather than being two
 * literal paths, and that is load-bearing rather than tidy. Vue Router treats an
 * alias as the same route record as the page it aliases (`isSameRouteRecord`
 * compares `aliasOf`), so pushing a *parameterless* alias of the page you are
 * already on is a redundant navigation: `router.push` resolves with a
 * `duplicated` failure, the URL never changes, and nothing on screen reacts.
 * `/card/:cardId` never hit this because its parameter always differs. With
 * `:pane` in the path, "/" and "/settings/notifications" differ by their params
 * and the navigation goes through.
 */
const SETTINGS_PANES = ["notifications", "api-keys"] as const;

export function useAppRoute() {
  const route = useRoute();
  const router = useRouter();

  // --- reactive readers ------------------------------------------------------
  const cardId = computed(() => (route.params.cardId as string) || null);
  const editLabels = computed(() => route.query.editlabels === "true");
  const search = computed(() => (route.query.search as string) || null);
  const labelFilter = computed(() => (route.query.label as string) || null);
  // Which settings pane the URL is asking for, or null for none. Validated
  // against the list above, so `/settings/whatever` lands on the board with no
  // dialog rather than opening an empty one.
  const settingsPane = computed<SettingsPane | null>(() => {
    const pane = route.params.pane as string | undefined;
    return SETTINGS_PANES.includes(pane as SettingsPane) ? (pane as SettingsPane) : null;
  });

  // --- helpers ---------------------------------------------------------------
  function withoutKeys(query: LocationQuery, ...keys: string[]): LocationQuery {
    const next = { ...query };
    for (const key of keys) delete next[key];
    return next;
  }

  // --- card ------------------------------------------------------------------
  // Open pushes a history entry (so Back closes the card); close replaces so the
  // closed card isn't re-opened when navigating back afterwards.
  function openCard(id: string) {
    if (route.params.cardId === id) return;
    router.push({ path: `/card/${id}`, query: route.query });
  }
  function closeCard() {
    if (!route.params.cardId) return;
    router.replace({ path: "/", query: route.query });
  }

  // --- settings panes --------------------------------------------------------
  // Same push/replace split as the card: opening adds a history entry so Back
  // closes the pane, closing replaces so Back afterwards does not reopen it.
  // Closing returns to the board rather than to whatever path was underneath:
  // a settings pane is a destination, not a layer over an opened card.
  function openSettings(pane: SettingsPane) {
    if (route.params.pane === pane) return;
    router.push({ path: `/settings/${pane}`, query: route.query });
  }
  function closeSettings() {
    if (!route.params.pane) return;
    router.replace({ path: "/", query: route.query });
  }

  // --- label editor ----------------------------------------------------------
  function openLabelEditor() {
    if (route.query.editlabels === "true") return;
    router.push({ path: route.path, query: { ...route.query, editlabels: "true" } });
  }
  function closeLabelEditor() {
    if (route.query.editlabels !== "true") return;
    router.replace({ path: route.path, query: withoutKeys(route.query, "editlabels") });
  }

  // --- search ----------------------------------------------------------------
  function setSearch(value: string | null) {
    const query = value
      ? { ...route.query, search: value }
      : withoutKeys(route.query, "search");
    router.replace({ path: route.path, query });
  }

  // --- label filter ----------------------------------------------------------
  function setLabelFilter(id: string | null) {
    const query = id
      ? { ...route.query, label: id }
      : withoutKeys(route.query, "label");
    router.replace({ path: route.path, query });
  }

  return {
    // readers
    cardId,
    editLabels,
    search,
    labelFilter,
    settingsPane,
    // card
    openCard,
    closeCard,
    // settings
    openSettings,
    closeSettings,
    // label editor
    openLabelEditor,
    closeLabelEditor,
    // filters
    setSearch,
    setLabelFilter,
  };
}
