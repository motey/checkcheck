<template>
  <VueAwesomeSideBar
    v-model:miniMenu="miniMenu"
    v-model:collapsed="collapsed"
    :overLayerOnOpen="false"
    :menu="myMenu"
    :position="sidebarPosition"
    vueRouterEnabel
    :dark="colorMode.value == 'dark'"
  />
  <UIcon name="lucide:tag" class="hidden" />
  <UIcon name="lucide:house" class="hidden" />
  <UIcon name="lucide:pencil" class="hidden" />
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { MenuItem } from "vue-awesome-sidebar";
import { useCheckListsLabelStore } from "@/stores/label";
import { useCheckListsStore } from "@/stores/checklist";

const colorMode = useColorMode();
const collapsed = defineModel<boolean>("collapsed");
const miniMenu = defineModel<boolean>("miniMenu");
const overlayMenu = defineModel<boolean>("overlayMenu");

const sidebarPosition = computed(() => (overlayMenu.value ? "fixed" : "relative"));

const router = useRouter();
const route = useRoute();

const labelStore = useCheckListsLabelStore();
const checkListStore = useCheckListsStore();

const baseMenu: (MenuItem | { header: string })[] = [
  {
    name: "Home",
    icon: { class: "iconify i-lucide:house shrink-0 size-5", element: "span" },
    href: router.resolve({ query: { } }).href,
  },
  {
    name: "Edit Labels",
    icon: { class: "iconify i-lucide:pencil shrink-0 size-5", element: "span" },
    href: router.resolve({ query: { ...route.query, editlabels: true } }).href,
  },
  { header: "Labels" },
];

const myMenu = ref<(MenuItem | { header: string })[]>(baseMenu);

function updateMenu() {
  const labelMenuItems: MenuItem[] = labelStore.labels.map((label) => ({
    name: label.display_name!,

    href: router.resolve({
      query: { label: label.id },
    }).href,
    icon: { class: `label-bg-${label.id} iconify i-lucide:tag shrink-0 size-5`, element: "span" },
  }));

  myMenu.value = [...baseMenu, ...labelMenuItems];
  injectLabelStyles();
}

// Create styles based on label colors
function injectLabelStyles() {
  const styleTagId = "dynamic-label-styles";
  let styleTag = document.getElementById(styleTagId) as HTMLStyleElement | null;

  if (!styleTag) {
    styleTag = document.createElement("style");
    styleTag.id = styleTagId;
    document.head.appendChild(styleTag);
  }

  let styles = ".vas-menu {height: 100% !important;}\n";
  labelStore.labels.forEach((label) => {
    if (label.color) {
      const bg =
        colorMode.value == "dark" ? label.color.backgroundcolor_dark_hex : label.color.backgroundcolor_light_hex;
      const textColor = colorMode.value == "dark" ? label.color.textcolor_dark_hex : label.color.textcolor_light_hex;
      styles += `.label-bg-${label.id} { background-color: ${bg} !important; color: ${textColor} !important; }\n`;
    }
  });

  styleTag.innerHTML = styles;
}

// Initial setup
onMounted(updateMenu);

// Update on label changes
watch(() => labelStore.labels, updateMenu, { deep: true });


// Update label styles on colorMode change
watch(
  () => colorMode.value,
  () => {
    injectLabelStyles();
  }
);
</script>

<style scoped>
.vas-menu {
  height: 100% !important;
}
</style>
