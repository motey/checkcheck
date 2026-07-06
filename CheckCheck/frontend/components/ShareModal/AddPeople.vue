<template>
  <!-- Wrapped in its own autocomplete-off form so Firefox scopes credential
       detection here and can't pair this box with the public-link password
       field elsewhere in the modal. The field also carries no login signals
       (no "user"/"username" in name, id or placeholder) for the same reason. -->
  <form class="flex flex-col gap-2" autocomplete="off" @submit.prevent>
    <div class="flex gap-2">
      <UInput
        v-model="query"
        class="flex-1"
        type="search"
        name="people-search"
        id="people-search"
        icon="i-lucide-search"
        placeholder="Search people by name…"
        :loading="searching"
        autocomplete="off"
        data-1p-ignore
        data-lpignore="true"
        data-testid="share-user-search"
      />
      <USelect
        v-model="level"
        :items="LEVEL_OPTIONS"
        class="w-28"
        data-testid="share-add-level"
      />
    </div>

    <!-- Results dropdown -->
    <ul
      v-if="query.trim().length >= 2"
      class="rounded-md border border-default divide-y divide-default"
    >
      <li v-if="searching" class="px-3 py-2 text-sm text-muted">
        Searching…
      </li>
      <li
        v-else-if="results.length === 0"
        class="px-3 py-2 text-sm text-muted"
      >
        No users found.
      </li>
      <li
        v-for="user in results"
        :key="user.id"
        class="flex items-center justify-between px-3 py-2"
      >
        <span class="text-sm">
          {{ user.display_name || user.user_name || user.id }}
          <span v-if="user.display_name && user.user_name" class="text-muted">
            @{{ user.user_name }}
          </span>
        </span>
        <UButton
          size="xs"
          icon="i-lucide-plus"
          :loading="addingId === user.id"
          label="Add"
          @click="add(user)"
        />
      </li>
    </ul>
  </form>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useDebounceFn } from "@vueuse/core";
import { useShareStore } from "@/stores/share";
import { useUserStore } from "@/stores/user";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});

const LEVEL_OPTIONS = [
  { label: "View", value: "view" },
  { label: "Check", value: "check" },
  { label: "Edit", value: "edit" },
] as const;

const shareStore = useShareStore();
const userStore = useUserStore();
const toast = useToast();

const query = ref("");
const level = ref<SharePermission>("edit");
const results = ref<UserSearchResult[]>([]);
const searching = ref(false);
const addingId = ref<string | null>(null);

// Hide users who are already collaborators, the current user, and the owner.
function filterCandidates(users: UserSearchResult[]): UserSearchResult[] {
  const existing = new Set(shareStore.sharesFor(props.checkListId).map((s) => s.user_id));
  const myId = userStore.myId;
  const ownerId = useCheckListsStore().get(props.checkListId)?.owner_id;
  return users.filter(
    (u) => u.id !== myId && u.id !== ownerId && !existing.has(u.id)
  );
}

const runSearch = useDebounceFn(async (q: string) => {
  searching.value = true;
  try {
    const found = await shareStore.searchUsers(q);
    // Guard against a stale response arriving after the query changed.
    if (query.value.trim() === q.trim()) results.value = filterCandidates(found);
  } finally {
    searching.value = false;
  }
}, 300);

watch(query, (q) => {
  const trimmed = q.trim();
  if (trimmed.length < 2) {
    results.value = [];
    searching.value = false;
    return;
  }
  searching.value = true;
  runSearch(trimmed);
});

async function add(user: UserSearchResult) {
  addingId.value = user.id;
  try {
    await shareStore.upsertShare(props.checkListId, user.id, level.value);
    toast.add({
      title: `Shared with ${user.display_name || user.user_name || "user"}`,
      color: "success",
    });
    // Drop the freshly-added user from the visible results.
    results.value = results.value.filter((u) => u.id !== user.id);
  } catch (err) {
    // The candidate list already filters out the owner, the current user, and
    // existing collaborators, so these 4xx branches are belt-and-suspenders
    // against a stale list / a race — but a clearer message still helps.
    // Backend: 400 = target is the owner (incl. yourself); 404 = no such user.
    const status = (err as any)?.statusCode ?? (err as any)?.response?.status;
    const title =
      status === 400
        ? "The owner already has full access to this list"
        : status === 404
        ? "That user could not be found"
        : "Could not share with this user";
    toast.add({ title, color: "error" });
  } finally {
    addingId.value = null;
  }
}
</script>

<style scoped></style>
