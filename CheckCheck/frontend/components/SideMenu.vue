<template>
  <VueAwesomeSideBar
    v-model:miniMenu="miniMenu"
    v-model:collapsed="collapsed"
    :overLayerOnOpen="false"
    :menu="myMenu"
    :position="sidebarPosition"
    vueRouterEnabel
  />
  <div class="bg-amber-500"></div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from "vue";
import type { MenuItem, MenuHeaderItem, MenuLine } from 'vue-awesome-sidebar'
const collapsed = defineModel<boolean>("collapsed");
const miniMenu = defineModel<boolean>("miniMenu");
const overlayMenu = defineModel<boolean>("overlayMenu");
const sidebarPosition = computed(() => (overlayMenu.value ? "fixed" : "relative"));
import { useRouter } from 'vue-router'

const router = useRouter()
const labelStore = useCheckListsLabelStore();
const checkListStore = useCheckListsStore();

// https://amirkian007.github.io/vasmenu/#/
// Reactive menu
const baseMenu = [
  {
    name: "Home",
    icon: { text: "home", class: "house" },
  } as MenuItem,
  { header: "Labels" },
];
const myMenu = ref(baseMenu);

// Function to update the menu based on current labels
function updateMenu() {
  const labelMenuItems = labelStore.labels.map((label) => ({
    name: label.display_name,
    class: `bg-[${label.color?.backgroundcolor_dark_hex}]`,
    href: router.resolve({
      query: { label: label.id }
    }).href,
    icon: { class: "material-icons-outlined", text: "dashboard" },
  } as MenuItem));

  myMenu.value = [...baseMenu, ...labelMenuItems];
}

// Initial population
updateMenu();

// Watch for changes in labelStore.labels
watch(() => labelStore.labels, updateMenu, { deep: true });


// Add label to pinia checkLIstStore to filter checklist
const route = useRoute()
watch(
  () => route.query.label,
  (label) => {
    if (typeof label === 'string') {
      checkListStore.setFilterLabel(label)
    }
  },
  { immediate: true }
)
</script>

<style>
.vas-menu {
  height: 100% !important;
}
</style>
