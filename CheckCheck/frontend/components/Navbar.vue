<template>
  <div class="sticky z-[99999] top-0 w-full border-b bg-white/75 dark:bg-gray-900/75 backdrop-blur">
    <div class="flex items-center gap-2 px-3 h-14">
      <!-- Hamburger: mobile only -->
      <UButton
        class="md:hidden shrink-0"
        variant="ghost"
        color="neutral"
        icon="i-lucide-menu"
        @click="emit('toggleSidebar')"
      />

      <Logo class="shrink-0" />

      <div class="flex-1 flex justify-center">
        <CreateCheckListBox />
      </div>

      <div class="flex items-center gap-2 shrink-0">
        <ColorModeSwitch />
        <UButton variant="ghost" color="neutral" size="sm" @click="logout">Logout</UButton>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const emit = defineEmits<{ toggleSidebar: [] }>();

const logout = async () => {
  const { $checkapi } = useNuxtApp();
  await $checkapi("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
};
</script>

<style scoped></style>
