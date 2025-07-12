<template>
  <UContainer>
    {{ label }}

  </UContainer>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useDebounceFn } from "@vueuse/core";
import type { PropType } from "vue";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { useTextareaAutosize } from '@vueuse/core'

const props = defineProps({
  label: { type: Object as PropType<LabelType>, required: false },
});


</script>

<style scoped>

</style>
