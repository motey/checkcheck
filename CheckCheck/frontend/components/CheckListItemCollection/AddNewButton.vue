<template>
    <button
        v-if="canEdit"
        type="button"
        data-testid="add-item"
        class="flex items-center gap-1.5 py-1 w-full text-left rounded-md cursor-pointer opacity-70 hover:opacity-100 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"
        @click="onAdd()"
    >
        <UIcon name="i-lucide-plus" class="flex-none size-5" />
        <span class="flex-1 min-w-0 text-sm">Add new item</span>
    </button>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { PropType } from "vue";

const props = defineProps({
    parentCheckList: { type: Object as PropType<CheckListType>, required: true },
})

const emit = defineEmits(["addItem"]);

// Adding items requires edit access (P0.1 / usePermissions).
const { can } = usePermissions();
const canEdit = computed(() => can(props.parentCheckList, "edit"));

// Let the parent collection create the item so it can focus the new textarea,
// mirroring the Enter-to-add-below flow.
const onAdd = () => {
    if (!canEdit.value) return;
    emit("addItem");
}



</script>

<style scoped></style>
