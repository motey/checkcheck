<template>
  <CardPartsItemList
    ref="itemList"
    :items="checklistItems"
    :show-add-row="filterCheckedItems != true"
    @reorder="onReorder"
  >
    <template #item="{ item, registerRef }">
      <CheckListItem class="px-0 py-0 sm:px-0 sm:py-0 md:px-0 md:py-0 lg:px-0 lg:py-0 text-[13px] sm:text-sm"
        :ref="registerRef"
        :parentCheckList="parentCheckList" :checkListItem="item" :parentEditMode="true"
        @add-item-after="addItemAfter" @delete-item="deleteItem" @accept-suggestion="acceptSuggestion"></CheckListItem>
    </template>
    <template #add>
      <CheckListItemCollectionAddNewButton :parentCheckList="parentCheckList" @add-item="addItemAtEnd">
      </CheckListItemCollectionAddNewButton>
    </template>
  </CardPartsItemList>
</template>

<script setup lang="ts">
// The authed open card's item list: a store adapter over the shared, store-free
// CardPartsItemList (issue #11). The list markup, the FormKit drag wiring and the
// focus plumbing are shared with the `/p/<token>` viewer; the store reads, the
// create/delete/uncheck-suggestion choreography and the reorder write stay here.
import { ref } from 'vue';
import { useCheckListsItemStore } from "@/stores/checklist_item";
import type { PropType } from "vue";

const checkListsItemStore = useCheckListsItemStore();

const props = defineProps({
  parentCheckList: { type: Object as PropType<CheckListType>, required: true },
  filterCheckedItems: { type: Boolean, required: false },
  showMaxItems: { type: Number, required: false, watch: true },
});

// computed(), so the list always reflects the store. CardPartsItemList makes its
// own drag copy of this and holds it steady while a drag is in progress.
const checklistItems = computed(() =>
  checkListsItemStore.getCheckListItems(
    props.parentCheckList.id,
    props.filterCheckedItems,
    props.showMaxItems
  )
);

// The shared list tracks the row components; ask it to move focus by item id.
const itemList = ref<{ focusItem: (id: string) => void } | null>(null);

// Enter on an item textarea: insert a new item right after it and focus it.
async function addItemAfter(afterItemId: string) {
  const list = checkListsItemStore.getCheckListItems(props.parentCheckList.id);
  const idx = list.findIndex((i) => i.id === afterItemId);
  if (idx === -1) return;
  const current = list[idx]!;
  const next = list[idx + 1];
  const newIndex = next
    ? (current.position.index + next.position.index) / 2
    : current.position.index + 1;
  const created = await checkListsItemStore.create(props.parentCheckList.id, {
    position: { index: newIndex },
  } as CheckListItemCreateType);
  await nextTick();
  itemList.value?.focusItem(created.id);
}

// "Add new item" button: append an item to the end and focus its textarea.
async function addItemAtEnd() {
  const created = await checkListsItemStore.create(props.parentCheckList.id);
  await nextTick();
  itemList.value?.focusItem(created.id);
}

// Keep-style dedup: the user typed a new item that matches an existing checked
// item, and accepted the suggestion. Uncheck the match (it reactively moves into
// the unchecked section) instead of keeping a duplicate, drop the just-typed
// item (its still-queued create coalesces away in the outbox), and move focus to
// the now-unchecked match.
async function acceptSuggestion(payload: { currentItemId: string; matchedItemId: string }) {
  const { currentItemId, matchedItemId } = payload;
  await checkListsItemStore.updateState(props.parentCheckList.id, matchedItemId, {
    checked: false,
  } as CheckListItemStateUpdateType);
  await deleteItem(currentItemId, false);
  await nextTick();
  itemList.value?.focusItem(matchedItemId);
}

// Delete an item. When triggered by backspace-on-empty, move focus to the end
// of the previous visible item so keyboard editing flows uninterrupted.
async function deleteItem(itemId: string, focusPrev: boolean) {
  const idx = checklistItems.value.findIndex((i) => i.id === itemId);
  const prev = idx > 0 ? checklistItems.value[idx - 1] : undefined;
  await checkListsItemStore.delete(props.parentCheckList.id, itemId);
  if (focusPrev && prev) {
    await nextTick();
    itemList.value?.focusItem(prev.id);
  }
}

function onReorder(newOrder: CheckListItemType[], movedItem: CheckListItemType) {
  (async () => {
    checkListsItemStore.reorderChecklistItems(props.parentCheckList.id, newOrder, movedItem);
  })();
}
</script>
