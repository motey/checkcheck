<template>
    <UButton :key="label!.id" size="xs" class="font-bold rounded-full" :style="labelStyle">
        {{ label!.display_name }}
    </UButton>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useColorMode } from "#imports";
import { useCheckListsLabelStore } from "@/stores/label";

const colorMode = useColorMode();
const labelStore = useCheckListsLabelStore();
const checkListColorSchemeStore = useCheckListsColorSchemeStore();

const props = defineProps({
    labelId: {
        type: String,
        required: true,
    },
    fallbackColor: { type: Object as PropType<ChecklistColorSchemeType>, required: false },
});

const label = computed(() => labelStore.getLabel(props.labelId));

const labelStyle = computed(() => {
    const colorScheme: ChecklistColorSchemeType | undefined =
        checkListColorSchemeStore.colors.find((c) => c.id === label.value?.color_id)
        ?? props.fallbackColor;
    const dark = colorMode.value === "dark";
    return {
        backgroundColor: dark ? (colorScheme?.backgroundcolor_dark_hex ?? 'transparent') : (colorScheme?.backgroundcolor_light_hex ?? 'transparent'),
        color: dark ? (colorScheme?.textcolor_dark_hex ?? 'unset') : (colorScheme?.textcolor_light_hex ?? 'unset'),
        borderColor: dark ? (colorScheme?.accentcolor_dark_hex ?? 'gray') : (colorScheme?.accentcolor_light_hex ?? 'gray'),
        borderWidth: '1px',
        paddingBlock: 'unset',
    };
});
</script>
