
<template>
    <VueAwesomeSideBar
    v-model:miniMenu="miniMenu"
    v-model:collapsed="collapsed"
    :overLayerOnOpen="false"
    :menu="myMenu"
    :position="sidebarPosition"
    vueRouterEnabel
  ></VueAwesomeSideBar>
</template>
<script setup lang="ts">
import { UButton } from '#components'
import { ref } from 'vue'
const collapsed = defineModel<boolean>('collapsed')
const miniMenu = defineModel<boolean>('miniMenu')
const overlayMenu = defineModel<boolean>('overlayMenu')
const sidebarPosition = computed(() => (overlayMenu.value ? 'fixed' : 'relative'))
const myMenu = [
  {
    name: 'Getting Started',
    icon: { text: 'home' , class: 'material-icons-outlined' },
    children: [
      {
        name: 'level 1.1',
        href: '/a',
        icon: { text: 'home' , class: 'material-icons-outlined'},
        children: [
          {
            href: '/b',
            name: 'level 1.1.1',
          },
        ]
      },
      {
        name: 'level 1.2'
      }
    ],
  },
  {
    header: 'Settings'
  },
  {
    name: 'Dashboard',
    icon: { class: 'material-icons-outlined', text: 'dashboard' },
    children: [
      {
        href: '/c',
        name: 'level 2.1',
      },
    ]
  },
  {
    name: 'close menu',
    icon: { text: 'settings', class: 'material-icons-outlined' },
  },
]

</script>
<style>
.vas-menu{
  height: 100% !important;
}
</style>