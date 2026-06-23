<template>
  <UTooltip :text="isPinned ? 'Unpin' : 'Pin'" :popper="{ arrow: true }">
    <CheckListColoredButton
      variant="ghost"
      :padded="false"
      :icon="isPinned ? 'i-lucide-pin-off' : 'i-lucide-pin'"
      :checkListId="checkListId"
      :aria-pressed="isPinned"
      data-testid="pin-button"
      @click.stop="togglePin()"
    />
  </UTooltip>
</template>

<script setup lang="ts">
import { useCheckListsStore } from "@/stores/checklist";

const checkListsStore = useCheckListsStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
  // On the board, the pinned section sits at the top of a scrollable container;
  // a card pinned from far down would otherwise move off-screen with no visible
  // feedback. When true, scroll the pinned section into view after pinning.
  scrollIntoViewOnPin: { type: Boolean, default: false },
});

const checkList = computed(() => checkListsStore.get(props.checkListId));
const isPinned = computed(() => checkList.value?.position.pinned ?? false);

function togglePin() {
  (async () => {
    const next = !isPinned.value;
    await checkListsStore.setPinned(props.checkListId, next);
    if (next && props.scrollIntoViewOnPin) {
      // Wait for the board to re-render (the card moves into the pinned <ul> via
      // FormKit's value splice on the next reactive flush) before scrolling.
      await nextTick();
      requestAnimationFrame(() => {
        document
          .querySelector("[data-testid=pinned-section]")
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  })();
}
</script>

<style scoped></style>
