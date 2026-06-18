---
name: url-state-handler
description: Frontend navigational state lives in the URL via a central composable
metadata:
  type: project
---

The frontend's guiding rule: app navigational state should be reflected in the URL whenever reasonable, so views are shareable and browser back/forward work.

All URL-reflected state goes through `CheckCheck/frontend/composables/useAppRoute.ts` — do NOT poke `useRouter()`/`route.query` directly in components for these. It owns:
- opened card  -> path `/card/<cardId>` (registered as an alias on `pages/index.vue` so the board + modals stay mounted; the card is a URL-driven overlay)
- label editor -> query `?editlabels=true`
- search text  -> query `?search=`
- label filter -> query `?label=`

The card and label-editor modals are owned in one place: `pages/index.vue` watches `cardId`/`editLabels` and opens/closes the overlays. Components (CheckListBoard, CreateCheckListBox) just call `openCard(id)` instead of opening overlays locally. Every mutation preserves the rest of the URL so the pieces stay composable.

**Why:** user wants every reasonable bit of UI state shareable via URL. **How to apply:** when adding new modal/filter state, add it to `useAppRoute` and drive the overlay from a watcher, rather than a local `useOverlay()` in the triggering component.
