<template>
    <NuxtLayout>
        <Navbar />
        <button @click="logout()">Logout</button>
        <div class="flex justify-start">
            <div class="min-width33 ">
                <CreateCheckListBox />
            </div>
        </div>
        <h1>Welcome to the homepage</h1>
        <button @click="hitme()">HIT ME BABY</button>

        NumChecklists:
        <pre>{{ checkLists.length }}/{{ total_backend_count }}</pre>
        </br>
        <CheckListBoard />
        </br>
    </NuxtLayout>
</template>
<script setup lang="ts">
import { useCheckListsStore } from '@/stores/checklist'
import { useCheckListsItemStore } from '@/stores/checklist_item'

const runtimeConfig = useRuntimeConfig()
const checkListStore = useCheckListsStore()
const checkListItemStore = useCheckListsItemStore()
const { checkLists, total_backend_count } = storeToRefs(checkListStore)

onMounted(async () => {
  await checkListStore.fetchNextPage()
})
function hitme() {
    (async () => {
        await checkListStore.fetchNextPage()
    })();

}


const logout = async () => { 
    const { $checkapi } = useNuxtApp()
    await $checkapi("/api/auth/logout",{method:"POST"})
    window.location.href = "/login"
}
</script>

<style scoped>
.min-width33{
    min-width: 33%;
}
</style>