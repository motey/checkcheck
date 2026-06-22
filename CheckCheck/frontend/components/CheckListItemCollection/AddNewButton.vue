<template>
    <div v-if="canEdit" class="flex mb-0 grow break-after-column cursor-pointer" @click="addNewItem()">
        <span class="flex-none w-8"></span>
        <span class="flex-none w-4 font-bold">+</span>
        <span class="flex-1 w-full grow pl-2 text-slate-500">Add New Checklist Item</span>
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
