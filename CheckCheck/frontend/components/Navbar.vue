<template>
  <div class="sticky z-[99999] top-0 w-full border-b bg-white/75 dark:bg-gray-900/75 backdrop-blur">
    <div class="flex flex-nowrap justify-evenly">
      <div class=""><Logo /></div>
      <div class="content-center"><CreateCheckListBox /></div>
      <div class="content-center flex"><ColorModeSwitch></ColorModeSwitch> <button @click="logout()">Logout</button></div>
    </div>
  </div>
</template>
<script setup>
const logout = async () => {
  const { $checkapi } = useNuxtApp();
  await $checkapi("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
};
</script>

<style scoped></style>
