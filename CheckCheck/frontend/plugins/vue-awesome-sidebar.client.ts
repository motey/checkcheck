/* https://amirkian007.github.io/vasmenu */

import { defineNuxtPlugin } from '#app'
import VueAwesomeSideBar from 'vue-awesome-sidebar'
import 'vue-awesome-sidebar/dist/vue-awesome-sidebar.css'

export default defineNuxtPlugin((nuxtApp) => {
  nuxtApp.vueApp.use(VueAwesomeSideBar)
})