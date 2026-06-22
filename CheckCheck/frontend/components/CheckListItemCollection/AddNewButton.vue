<template>
    <div
        v-if="canEdit"
        class="flex items-center gap-1.5 py-1 cursor-pointer text-muted hover:text-default transition-colors"
        @click="addNewItem()"
    >
        <UIcon name="i-lucide-plus" class="flex-none size-5" />
        <span class="flex-1 min-w-0 text-sm">Add new item</span>
    </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { PropType } from "vue";
import { useCheckListsItemStore } from '@/stores/checklist_item'

const props = defineProps({
    parentCheckList: { type: Object as PropType<CheckListType>, required: true },
})

// Adding items requires edit access (P0.1 / usePermissions).
const { can } = usePermissions();
const canEdit = computed(() => can(props.parentCheckList, "edit"));

const addNewItem = async () => {
    if (!canEdit.value) return;
    const checkListsItemStore = useCheckListsItemStore()
    const new_item = await checkListsItemStore.create(props.parentCheckList.id)
}



</script>

<style scoped></style>
