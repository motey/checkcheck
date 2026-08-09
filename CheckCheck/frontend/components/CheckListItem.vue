<template>
  <CardPartsItemRow
    ref="rowRef"
    :item="checkListItem!"
    :can-check="canCheck"
    :can-edit="canEdit"
    :edit-mode="parentEditMode"
    :search-query="searchQuery"
    :text-color="textColor"
    @toggle="toggleCheck()"
    @update:text="onTextInput"
    @delete="(focusPrev: boolean) => emit('deleteItem', checkListItem!.id, focusPrev)"
    @add-after="emit('addItemAfter', checkListItem!.id)"
    @focus="onTextFocus"
    @blur="onTextBlur"
  >
    <template #below>
      <!-- Keep-style autocomplete: while typing a new item, list existing *checked*
           items whose text starts with what's been typed so far, so the user can
           uncheck one instead of creating a duplicate. Detection is fully
           client-side (reads the in-memory item store); accepting is the only
           write. Gated per-card via suggest_existing_items; mousedown.prevent keeps
           the textarea focused so clicking a row doesn't dismiss the list mid-click. -->
      <ul
        v-if="parentEditMode && suggestions.length"
        data-testid="uncheck-suggestions"
        class="ml-8 mb-1 flex flex-col rounded-md border border-current/10 bg-current/5 overflow-hidden"
      >
        <li v-for="s in suggestions" :key="s.id">
          <button
            type="button"
            data-testid="uncheck-suggestion"
            class="flex w-full items-center gap-1.5 px-2 py-1 text-xs text-left opacity-80 hover:opacity-100 hover:bg-current/10 transition cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"
            @mousedown.prevent.stop="onAcceptSuggestion(s)"
          >
            <UIcon name="i-lucide-corner-down-left" class="flex-none size-4" />
            <span class="min-w-0 truncate">Uncheck &ldquo;{{ s.text }}&rdquo;</span>
          </button>
        </li>
      </ul>
    </template>
  </CardPartsItemRow>
</template>

<script setup lang="ts">
// The authed card's item row: a thin store adapter over the shared, store-free
// CardPartsItemRow (issue #11). Everything session-coupled lives here (the
// debounced store write, the WI-10 editGuard marks, the uncheck-suggestion list
// and the permission ladder), while all the layout, Markdown rendering and
// keyboard behaviour is the shared row's, which the `/p/<token>` viewer renders too.
import { ref } from "vue";
import { useDebounceFn } from "@vueuse/core";
import type { PropType } from "vue";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { markEditing, clearEditing } from "@/utils/editGuard";
import { findMatchingCheckedItems } from "@/utils/normalizeItemText";

const props = defineProps({
  checkListItem: { type: Object as PropType<CheckListItemType>, required: false },
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  parentEditMode: { type: Boolean, watch: true },
});

const rowRef = ref();
const textFocused = ref(false);
const checkListsItemStore = useCheckListsItemStore();
const route = useRoute();
const searchQuery = computed(() => (route.query.search as string) || null);
const emit = defineEmits(["checkedItem", "addItemAfter", "deleteItem", "acceptSuggestion"]);

// Keep the local textarea guard AND the WI-10 store-apply guard in sync so an
// incoming delta never clobbers the item text mid-edit (SYNC §4).
function onTextFocus() {
  textFocused.value = true;
  if (props.checkListItem) markEditing("item", props.checkListItem.id, "text");
}
function onTextBlur() {
  textFocused.value = false;
  if (props.checkListItem) clearEditing("item", props.checkListItem.id, "text");
}
onBeforeUnmount(() => {
  if (props.checkListItem) clearEditing("item", props.checkListItem.id, "text");
});

// Mirrors the shared row's local text so the suggestion match narrows live as the
// user types (the row owns the field; we only need to read what is in it).
const localText = ref(props.checkListItem?.text ?? "");
watch(
  () => props.checkListItem?.text,
  (serverText) => {
    if (!textFocused.value) localText.value = serverText ?? "";
  }
);

// Keep-style autocomplete: the *checked* items in this card whose normalized
// text starts with what the user is currently typing into this (unchecked)
// item, so the list narrows live as they type. Fully client-side — reads the
// already-loaded item store, no network. Only shown while the field is focused,
// the per-card toggle is on, and this item is itself unchecked (we never offer
// to uncheck a match while editing a checked item).
const suggestions = computed<CheckListItemType[]>(() => {
  if (!props.parentEditMode) return [];
  if (props.parentCheckList.suggest_existing_items === false) return [];
  if (!canEdit.value) return [];
  if (!textFocused.value) return [];
  if (props.checkListItem?.state.checked) return [];
  const checkedItems = checkListsItemStore.getCheckListItems(props.parentCheckList.id, true);
  return findMatchingCheckedItems(checkedItems, props.checkListItem?.id, localText.value);
});

// Accept a suggestion: hand the pair up to the collection, which unchecks the
// chosen item, removes this just-typed duplicate, and refocuses the match.
function onAcceptSuggestion(match: CheckListItemType) {
  if (!match || !props.checkListItem) return;
  emit("acceptSuggestion", { currentItemId: props.checkListItem.id, matchedItemId: match.id });
}

// Called by the parent collection after it inserts the freshly created item (and
// on add-after / accept-suggestion / backspace-merge). Delegated to the shared
// row, which owns the focus-swap textarea.
function focusTextarea() {
  rowRef.value?.focusTextarea?.();
}
defineExpose({ focusTextarea });

// Permission gating — driven by the parent card's my_permission (P0.1).
const { can } = usePermissions();
const canCheck = computed(() => can(props.parentCheckList, "check"));
const canEdit = computed(() => can(props.parentCheckList, "edit"));

function toggleCheck() {
  if (!canCheck.value) return;
  (async () => {
    await checkListsItemStore.updateState(props.parentCheckList.id, props.checkListItem!.id, {
      checked: !props.checkListItem!.state.checked,
    } as CheckListItemStateUpdateType);
  })();
  emit("checkedItem");
}

let textColor = props.parentCheckList!.color?.dark_text ? "#fff" : "#000";

emit("checkedItem");

// The shared row is deliberately undebounced (both surfaces debounce at the same
// numbers around different write paths), so the store write is debounced here.
const debouncedUpdateCheckListItemText = useDebounceFn(
  (val: string) => {
    if (!props.checkListItem || !canEdit.value) return;
    (async () => {
      await checkListsItemStore.update(props.parentCheckList.id, props.checkListItem!.id, { text: val } as CheckListUpdateType);
    })();
  },
  500,
  { maxWait: 3000 }
);

function onTextInput(val: string) {
  localText.value = val;
  debouncedUpdateCheckListItemText(val);
}
</script>
