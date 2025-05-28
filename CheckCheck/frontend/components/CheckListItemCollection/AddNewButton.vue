<template>
    <div class="flex mb-0 grow break-after-column cursor-pointer" @click="addNewItem()">
        <span class="flex-none w-8"></span>
        <span class="flex-none w-4 font-bold">+</span>
        <span class="flex-1 w-full grow pl-2 text-slate-500">Add New Checklist Item</span>
    </div>
</template>

<script setup lang="ts">
import type { PropType } from "vue";
import { useCheckListsItemStore } from '@/stores/checklist_item'

const props = defineProps({
    parentCheckList: { type: Object as PropType<CheckListType>, required: true },
})




const addNewItem = async () => {
    const checkListsItemStore = useCheckListsItemStore()
    const new_item = await checkListsItemStore.create(props.parentCheckList.id)
}



</script>

<style scoped></style>
