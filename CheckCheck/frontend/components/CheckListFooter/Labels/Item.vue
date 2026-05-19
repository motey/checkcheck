<template>
        <UButton :key="label!.id" size="xs" class="font-bold rounded-full" :style="getLabelStyle()">
            {{ label!.display_name }}{{ getLabelStyle() }}
        </UButton>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, guardReactiveProps } from "vue";
import { useColorMode } from "#imports";
import { useCheckListsLabelStore } from "@/stores/label";
import { useCheckListsStore } from "@/stores/checklist";

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

// Wait for labels to load immediately
const label = ref(labelStore.getLabel(props.labelId));

// Check if colors are loaded
const colorsReady = computed(() => checkListColorSchemeStore.colors.length > 0);

// Style helper
const getLabelStyle = () => {
    var labelColorScheme: ChecklistColorSchemeType | null | undefined = checkListColorSchemeStore.colors.find(
        (c) => c.id === label.value?.color_id
    );
    if (!labelColorScheme) {
        labelColorScheme = props.fallbackColor;
    }
    if (colorMode.value === "dark") {
        return {
            backgroundColor: labelColorScheme?.backgroundcolor_dark_hex ?? 'transparent',
            color: labelColorScheme?.textcolor_dark_hex ?? 'unset',
            borderColor: labelColorScheme?.accentcolor_dark_hex ?? 'gray',
            borderWidth: '1px',
            paddingBlock: 'unset',
            // transition: 'border-color 0.3s',
        };
    } else {
        return {
            backgroundColor: labelColorScheme?.backgroundcolor_light_hex ?? 'transparent',
            color: labelColorScheme?.textcolor_light_hex ?? 'unset',
            borderColor: labelColorScheme?.accentcolor_light_hex ?? 'gray',
            borderWidth: '1px',
            paddingBlock: 'unset',
            // transition: 'border-color 0.3s',
        };
    }
};
</script>
