<template>
  <div class="flex justify-center">
    <UButtonGroup>
      <CheckListFooterButtonArchive :checkListId="checkListId" />
      <CheckListFooterButtonColor :checkListId="checkListId" />
      <CheckListFooterButtonShare :checkListId="checkListId" />
      <CheckListFooterLabelsSelect :checkListId="checkListId" />
      <CheckListFooterButtonMoreOptionsMenu :checkListId="checkListId" />
    </UButtonGroup>
  </div>
</template>

<script setup lang="ts">
defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
</script>

<style scoped></style>
