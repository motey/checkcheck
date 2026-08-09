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

      <!-- Ready: the card, standalone. Inside the page chrome the card renders like
           the authed open card (same colour theme, same notes field, same item rows,
           same separated-checked layout) via components/CardParts/*, issue #11. -->
      <template v-else-if="status === 'ready' && card">
        <UCard
          data-testid="public-card"
          class="border-t-4 border-t-primary textareas-inherit-color"
          :style="cardStyle"
        >
          <template #header>
            <!-- An edit link may rename the card, so the title is the same textarea
                 the open card uses. A view/check link gets a plain heading rather
                 than a disabled field: nothing here is theirs to type in. -->
            <UTextarea
              v-if="canEdit"
              autoresize
              variant="none"
              :rows="0"
              :padded="false"
              placeholder="Enter a checklist title..."
              v-model="localName"
              class="w-full text-xl font-semibold"
              data-testid="public-card-name"
              @focus="nameFocused = true"
              @blur="nameFocused = false"
            />
            <h1 v-else class="text-xl font-semibold break-words" data-testid="public-card-name">
              {{ card.name || "Untitled list" }}
            </h1>
            <CardPartsNotesField
              v-if="canEdit || card.text"
              v-model="localText"
              :can-edit="canEdit"
              class="mt-1"
              @focus="textFocused = true"
              @blur="textFocused = false"
            />
          </template>

          <div class="flex flex-col" data-testid="public-items">
            <CardPartsItemsSection
              :separated="separated"
              :collapsed="collapsed"
              edit-mode
              :checked-count="checkedItems.length"
              :unchecked-count="uncheckedItems.length"
              @toggle-collapsed="setCollapsed(!collapsed)"
            >
              <template #unchecked>
                <CardPartsItemList
                  ref="uncheckedList"
                  data-testid="public-unchecked-items"
                  :items="separated ? uncheckedItems : allItems"
                  :enable-drag="canEdit"
                  :show-add-row="canEdit"
                  @reorder="reorderItems"
                >
                  <template #item="{ item, registerRef }">
                    <PublicChecklistItem
                      :ref="registerRef"
                      :item="item"
                      :can-check="canCheck"
                      :can-edit="canEdit"
                      @toggle="toggleItem(item)"
                      @update-text="(t: string) => updateItemText(item, t)"
                      @delete="deleteItem(item)"
                      @add-after="onAddAfter(item)"
                    />
                  </template>
                  <template #add>
                    <button
                      type="button"
                      class="flex items-center gap-1.5 py-1 w-full text-left rounded-md text-muted hover:text-default transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset"
                      data-testid="public-add-item"
                      @click="onAdd()"
                    >
                      <UIcon name="i-lucide-plus" class="flex-none size-5" />
                      <span class="text-sm">Add new item</span>
                    </button>
                  </template>
                </CardPartsItemList>
              </template>
              <template #checked>
                <CardPartsItemList
                  data-testid="public-checked-items"
                  :items="checkedItems"
                  :enable-drag="canEdit"
                  @reorder="reorderItems"
                >
                  <template #item="{ item, registerRef }">
                    <PublicChecklistItem
                      :ref="registerRef"
                      :item="item"
                      :can-check="canCheck"
                      :can-edit="canEdit"
                      @toggle="toggleItem(item)"
                      @update-text="(t: string) => updateItemText(item, t)"
                      @delete="deleteItem(item)"
                      @add-after="onAddAfter(item)"
                    />
                  </template>
                </CardPartsItemList>
              </template>
            </CardPartsItemsSection>

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
import { useDebounceFn } from "@vueuse/core";
import { usePublicCard } from "~/composables/usePublicCard";

// Standalone, fully public viewer — no auth required. The `/p/<token>` route is a
// capability URL; the page owns all 4xx handling (plugins/api.ts skips the global
// error toast + 401→/login redirect for /api/public requests).
//
// The page keeps its own chrome (logo header, permission badge, "Add to my deck"):
// that framing is what tells a visitor they are looking at something somebody
// shared with them. *Inside* it the card is assembled from the same CardParts
// components the authed open card uses, so the two surfaces cannot drift (issue
// #11). Owner-only surfaces (footer toolbar, labels, reminders, share, pin,
// archive, kebab) stay absent.
definePageMeta({ layout: "default" });

const route = useRoute();
const toast = useToast();
const colorMode = useColorMode();
const token = computed(() => String(route.params.token ?? ""));

const {
  card,
  items,
  uncheckedItems,
  checkedItems,
  collapsed,
  status,
  unlockError,
  unlocking,
  joining,
  canCheck,
  canEdit,
  load,
  unlock,
  setCollapsed,
  updateCard,
  toggleItem,
  updateItemText,
  addItem,
  deleteItem,
  reorderItems,
  join,
  disconnectSync,
} = usePublicCard(token.value);

const passphrase = ref("");

async function submitUnlock() {
  if (!passphrase.value) return;
  await unlock(passphrase.value);
}

// The card's own layout setting, same as the authed card reads it.
const separated = computed(() => card.value?.checked_items_seperated !== false);
// When the card does not separate checked items, one list holds them all.
const allItems = computed(() => [...uncheckedItems.value, ...checkedItems.value]);

// ── Card colour theme ────────────────────────────────────────────────────────
// The `color` object is already in the public response; apply it exactly as
// CheckList.vue does so a themed card looks themed on the public link too.
const textColor = computed(() => {
  const color = card.value?.color;
  if (!color) return undefined;
  return colorMode.value === "dark" ? color.textcolor_dark_hex : color.textcolor_light_hex;
});
const accentColor = computed(() => {
  const color = card.value?.color;
  if (!color) return undefined;
  return colorMode.value === "dark" ? color.accentcolor_dark_hex : color.accentcolor_light_hex;
});
const backgroundColor = computed(() => {
  const color = card.value?.color;
  if (!color) return undefined;
  return colorMode.value === "dark"
    ? color.backgroundcolor_dark_hex
    : color.backgroundcolor_light_hex;
});
const cardStyle = computed(() => {
  const style: Record<string, string> = {};
  if (textColor.value) style.color = textColor.value;
  if (backgroundColor.value) style.backgroundColor = backgroundColor.value;
  if (accentColor.value) style.borderColor = accentColor.value;
  return style;
});

// ── Title / notes (edit links only; the fields are read-only otherwise) ───────
// Local copies decoupled from `card` so an SSE refetch can't wipe what the
// visitor is typing, and the write is debounced at the same numbers the authed
// card uses.
const nameFocused = ref(false);
const textFocused = ref(false);
const localName = ref("");
const localText = ref("");

watch(
  () => card.value?.name,
  (n) => { if (!nameFocused.value) localName.value = n ?? ""; },
  { immediate: true }
);
watch(
  () => card.value?.text,
  (t) => { if (!textFocused.value) localText.value = t ?? ""; },
  { immediate: true }
);

// One debounce PER field, not one shared by both: a single timer would let an
// edit to the notes cancel the title write that was still pending, silently
// dropping it.
const debouncedUpdateName = useDebounceFn(
  (name: string) => updateCard({ name }),
  500,
  { maxWait: 3000 }
);
const debouncedUpdateText = useDebounceFn(
  (text: string) => updateCard({ text }),
  500,
  { maxWait: 3000 }
);

watch(localName, (n) => {
  if (canEdit.value && n !== (card.value?.name ?? "")) debouncedUpdateName(n);
});
watch(localText, (t) => {
  if (canEdit.value && t !== (card.value?.text ?? "")) debouncedUpdateText(t);
});

// ── Adding items (focus the new row, like the authed editor) ──────────────────
const uncheckedList = ref<{ focusItem: (id: string) => void } | null>(null);

async function onAdd() {
  const created = await addItem();
  if (!created) return;
  await nextTick();
  uncheckedList.value?.focusItem(created.id);
}

// Enter on a row inserts a new item right below it.
async function onAddAfter(afterItem: CheckListItemType) {
  const created = await addItem(afterItem);
  if (!created) return;
  await nextTick();
  uncheckedList.value?.focusItem(created.id);
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
