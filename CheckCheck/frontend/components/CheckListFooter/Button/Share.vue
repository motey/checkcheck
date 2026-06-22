<template>
  <UTooltip v-if="publicConfig.sharingEnabled" text="Collaborate" :popper="{ arrow: true }">
    <CheckListColoredButton
        variant="ghost"
        :padded="false"
        icon="i-lucide-users"
        data-testid="share-button"
        :checkListId="checkListId"
        @click.stop="openShareDialog()"
      />
  </UTooltip>
</template>

<script setup lang="ts">
import { ShareModal } from "#components";
import { usePublicConfigStore } from "@/stores/publicConfig";

const publicConfig = usePublicConfigStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const overlay = useOverlay();
const shareModal = overlay.create(ShareModal);

function openShareDialog() {
  shareModal.open({ checkListId: props.checkListId });
}
</script>

<style scoped></style>
