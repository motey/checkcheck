<template>
  <div class="min-h-screen w-full flex flex-col bg-muted px-3 py-6">
    <!-- Standalone header (no board/sidebar chrome) -->
    <header class="w-full max-w-2xl mx-auto flex items-center justify-between mb-8">
      <NuxtLink to="/" class="flex items-center gap-2">
        <Logo size="full" />
      </NuxtLink>
      <ColorModeSwitch />
    </header>

    <main class="w-full max-w-2xl mx-auto flex-1 flex flex-col justify-center">
      <!-- Loading -->
      <div v-if="status === 'loading'" class="flex justify-center py-24" data-testid="public-loading">
        <UIcon name="i-lucide-loader-circle" class="animate-spin w-8 h-8 text-dimmed" />
      </div>

      <!-- Locked / bad link → passphrase form (can't distinguish; same 404) -->
      <UCard v-else-if="status === 'locked'" data-testid="public-locked">
        <template #header>
          <div class="flex items-center gap-2">
            <UIcon name="i-lucide-lock" class="w-5 h-5" />
            <h1 class="text-lg font-semibold">This list is protected</h1>
          </div>
        </template>
        <form class="flex flex-col gap-3" @submit.prevent="submitUnlock">
          <p class="text-sm text-muted">
            Enter the passphrase to view this list. If the link is wrong, expired or disabled,
            the passphrase won't unlock it.
          </p>
          <UInput
            v-model="passphrase"
            type="password"
            placeholder="Passphrase"
            autocomplete="off"
            :disabled="unlocking"
            data-testid="public-passphrase"
          />
          <p v-if="unlockError" class="text-sm text-error" data-testid="public-unlock-error">
            {{ unlockError }}
          </p>
          <UButton
            type="submit"
            block
            :loading="unlocking"
            :disabled="passphrase.length === 0"
            data-testid="public-unlock-submit"
          >
            Unlock
          </UButton>
        </form>
      </UCard>

      <!-- Gone: bad/expired/disabled link, or the card was deleted live -->
      <UCard v-else-if="status === 'gone'" data-testid="public-gone">
        <div class="flex flex-col items-center text-center gap-3 py-8">
          <UIcon name="i-lucide-unlink" class="w-10 h-10 text-dimmed" />
          <h1 class="text-lg font-semibold">Link not available</h1>
          <p class="text-sm text-muted">
            This share link is invalid, expired, or has been disabled.
          </p>
          <UButton to="/" variant="subtle">Go to CheckCheck</UButton>
        </div>
      </UCard>

      <!-- Ready: the card, standalone -->
      <template v-else-if="status === 'ready' && card">
        <UCard data-testid="public-card" class="border-t-4 border-t-primary">
          <template #header>
            <h1 class="text-xl font-semibold break-words" data-testid="public-card-name">
              {{ card.name || "Untitled list" }}
            </h1>
            <p v-if="card.text" class="mt-1 text-sm text-muted whitespace-pre-wrap break-words">
              {{ card.text }}
            </p>
          </template>

          <div class="flex flex-col" data-testid="public-items">
            <PublicChecklistItem
              v-for="item in items"
              :key="item.id"
              :item="item"
              :can-check="canCheck"
              :can-edit="canEdit"
              @toggle="toggleItem(item)"
              @update-text="(t) => updateItemText(item, t)"
              @delete="deleteItem(item)"
            />

            <button
              v-if="canEdit"
              type="button"
              class="flex items-center gap-1.5 mt-1 py-1 rounded-lg text-muted hover:text-default transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
              data-testid="public-add-item"
              @click="addItem()"
            >
              <UIcon name="i-lucide-plus" class="flex-none size-5" />
              <span class="text-sm">Add new item</span>
            </button>

            <p v-if="items.length === 0 && !canEdit" class="text-sm text-dimmed py-2">
              This list has no items yet.
            </p>
          </div>

          <template #footer>
            <div class="flex items-center justify-between gap-3">
              <UBadge
                variant="subtle"
                color="neutral"
                size="sm"
                :icon="permissionIcon"
                data-testid="public-permission"
              >
                {{ permissionLabel }}
              </UBadge>
              <UButton
                :loading="joining"
                icon="i-lucide-copy-plus"
                data-testid="public-join"
                @click="onJoin"
              >
                Add to my deck
              </UButton>
            </div>
          </template>
        </UCard>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { usePublicCard } from "~/composables/usePublicCard";

// Standalone, fully public viewer — no auth required. The `/p/<token>` route is a
// capability URL; the page owns all 4xx handling (plugins/api.ts skips the global
// error toast + 401→/login redirect for /api/public requests).
definePageMeta({ layout: "default" });

const route = useRoute();
const toast = useToast();
const token = computed(() => String(route.params.token ?? ""));

const {
  card,
  items,
  status,
  unlockError,
  unlocking,
  joining,
  canCheck,
  canEdit,
  load,
  unlock,
  toggleItem,
  updateItemText,
  addItem,
  deleteItem,
  join,
  disconnectSync,
} = usePublicCard(token.value);

const passphrase = ref("");

async function submitUnlock() {
  if (!passphrase.value) return;
  await unlock(passphrase.value);
}

const permissionLabel = computed(() => {
  switch (card.value?.my_permission) {
    case "edit":
    case "owner":
      return "You can view and edit this list";
    case "check":
      return "You can view and tick items";
    default:
      return "You're viewing a read-only list";
  }
});

const permissionIcon = computed(() => {
  switch (card.value?.my_permission) {
    case "edit":
    case "owner":
      return "i-lucide-pencil";
    case "check":
      return "i-lucide-check-square";
    default:
      return "i-lucide-eye";
  }
});

async function onJoin() {
  const res = await join();
  if (res.ok) {
    toast.add({ title: "Added to your deck", color: "success" });
    await navigateTo(`/card/${res.card.id}`);
    return;
  }
  if (res.loggedOut) {
    toast.add({ title: "Log in to add this card to your deck", color: "info" });
    await navigateTo({ path: "/login", query: { redirect: `/p/${token.value}` } });
    return;
  }
  toast.add({ title: "Could not add this card", color: "error" });
}

onMounted(load);
onUnmounted(disconnectSync);
</script>
