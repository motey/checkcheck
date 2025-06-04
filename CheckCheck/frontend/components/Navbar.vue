<template>
  <div class="sticky z-[99999] top-0 w-full border-b bg-white/75 dark:bg-gray-900/75 backdrop-blur">
    
    <div class="container mx-auto flex items-center justify-between py-1">
      <div class="flex items-center space-x-4">
        <!-- Text on the left -->

        <!-- Colored square with rounded corners and icon inside -->
        <div class="relative bg-amber-400 rounded-lg p-0 w-16 h-16 flex items-center justify-center">
          <UIcon name="i-lucide-list-checks" class="size-12 text-white" />
          <!-- Folded corner -->
          <div class="absolute top-0 right-0 w-6 h-6 overflow-hidden">
            <div class="w-full h-full bg-white/100 dark:bg-gray-900/100 clip-path-folded-corner"></div>
          </div>
        </div>
        <h1 class="text-5xl font-bold font-sans tracking-tight">CheckCheck</h1>
      </div>
      <div class="flex items-center">
            <div class="min-width33 ">
                <CreateCheckListBox />
            </div>
        </div>
      <div class="flex items-center gap-4">
        <ColorModeSwitch></ColorModeSwitch>
        <button @click="logout()">Logout</button>
      </div>
    </div>
  </div>
  
</template>
<script setup>
const logout = async () => { 
    const { $checkapi } = useNuxtApp()
    await $checkapi("/api/auth/logout",{method:"POST"})
    window.location.href = "/login"
}
</script>

<style scoped>
.clip-path-folded-corner {
  clip-path: polygon(100% 0, 100% 100%, 0 0);
}
.min-width33{
    min-width: 33%;
}
</style>
