import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { LocationQuery } from "vue-router";

/**
 * Central handler for all URL-reflected app state.
 *
 * The app keeps its navigational state in the URL so views are shareable and
 * the browser back/forward buttons work:
 *   - an opened card  -> path   `/card/<cardId>`   (see the alias on pages/index.vue)
 *   - the label editor -> query `?editlabels=true`
 *   - the search text  -> query `?search=<text>`
 *   - the label filter -> query `?label=<labelId>`
 *
 * Every mutation here preserves the rest of the URL (path + remaining query) so
 * the different pieces of state stay independent and composable. Components
 * should go through this composable instead of poking at the router directly.
 */
export function useAppRoute() {
  const route = useRoute();
  const router = useRouter();

  // --- reactive readers ------------------------------------------------------
  const cardId = computed(() => (route.params.cardId as string) || null);
  const editLabels = computed(() => route.query.editlabels === "true");
  const search = computed(() => (route.query.search as string) || null);
  const labelFilter = computed(() => (route.query.label as string) || null);

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
    // card
    openCard,
    closeCard,
    // label editor
    openLabelEditor,
    closeLabelEditor,
    // filters
    setSearch,
    setLabelFilter,
  };
}
