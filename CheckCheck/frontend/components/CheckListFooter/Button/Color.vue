<template>
  <UTooltip @click.stop text="Change Color" :shortcuts="['⌘', 'ToDo']" :popper="{ arrow: true }">
    <UPopover arrow>
      <UButton @click.stop :padded="false" color="neutral" variant="outline" icon="i-lucide-palette" />
      <template #content>
        <UContainer>
          <UButton size="xl"
            variant="solid"
            class="rounded-full bg-red-400 text-white border-2 border-red-400 hover:bg-red-400 hover:text-white hover:border-white"
          >
          &nbsp;&nbsp;&nbsp;&nbsp;
          </UButton>
        </UContainer>
      </template>
    </UPopover>
  </UTooltip>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useCheckListsStore } from "@/stores/checklist";

const checkListsStore = useCheckListsStore();

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
</script>

<style scoped></style>
