<template>
  <div
    class="sticky z-50 top-0 w-full border-b border-default bg-default/75 backdrop-blur"
    style="padding-top: env(safe-area-inset-top)"
  >
    <div class="flex items-center gap-2 px-3 h-14">
      <!-- Hamburger: mobile only -->
      <UButton
        class="md:hidden shrink-0"
        variant="ghost"
        color="neutral"
        icon="i-lucide-menu"
        aria-label="Open menu"
        @click="emit('toggleSidebar')"
      />

      <Logo class="shrink-0" />

      <div class="flex-1 flex justify-center min-w-0">
        <CreateCheckListBox />
      </div>

      <div class="flex items-center gap-1 shrink-0">
        <NotificationBell />

        <UDropdownMenu
          :items="userMenuItems"
          :content="{ align: 'end' }"
          :ui="{ content: 'min-w-56' }"
        >
          <UButton
            variant="ghost"
            color="neutral"
            data-testid="user-menu"
            aria-label="User menu"
            class="p-1"
          >
            <UAvatar :text="initials" size="sm" />
          </UButton>

          <template #content-top>
            <div class="px-2 py-1.5 border-b border-default">
              <p v-if="displayName" class="text-sm font-medium text-highlighted truncate">{{ displayName }}</p>
              <p v-if="email" class="text-xs text-muted truncate">{{ email }}</p>
            </div>
            <div class="flex items-center justify-between gap-2 px-2 py-1.5 border-b border-default">
              <span class="text-sm text-muted">Theme</span>
              <ColorModeSwitch />
            </div>
          </template>

          <template #api-keys-leading>
            <UIcon name="i-lucide-key-round" class="size-5 shrink-0 text-muted" />
          </template>
          <template #api-keys-label>
            <span data-testid="menu-api-keys">API keys</span>
          </template>

          <template #logout-leading>
            <UIcon name="i-lucide-log-out" class="size-5 shrink-0 text-error" />
          </template>
          <template #logout-label>
            <span data-testid="logout-button">Logout</span>
          </template>
        </UDropdownMenu>
      </div>
    </div>

    <!-- API keys manager, opened from the user menu (declarative v-model:open
         so it mounts once and can't double-dialog). -->
    <ApiKeysModal v-model:open="apiKeysOpen" />
  </div>
</template>

<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { useUserStore } from "@/stores/user";

const emit = defineEmits<{ toggleSidebar: [] }>();

const userStore = useUserStore();
const me = computed(() => userStore.me);
const displayName = computed(() => me.value?.display_name ?? "");
const email = computed(() => me.value?.email ?? "");

const initials = computed(() => {
  const source = displayName.value || email.value;
  if (!source) return "?";
  const parts = source.trim().split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
});

const apiKeysOpen = ref(false);

const userMenuItems = computed(
  () =>
    [
      {
        label: "API keys",
        slot: "api-keys" as const,
        onSelect: () => {
          apiKeysOpen.value = true;
        },
      },
      {
        label: "Logout",
        slot: "logout" as const,
        color: "error" as const,
        onSelect: logout,
      },
    ] satisfies DropdownMenuItem[]
);

async function logout() {
  const { $checkapi } = useNuxtApp();
  try {
    await $checkapi("/api/auth/logout", { method: "POST" });
  } catch {
    // Session may already be invalid; navigate to login regardless.
  }
  window.location.href = "/login";
}
</script>

<style scoped></style>
