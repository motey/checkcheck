<template>
  <div class="flex flex-col gap-3">
    <!-- Create link form ----------------------------------------------------- -->
    <div class="flex flex-col gap-2 rounded-md border border-[var(--ui-border)] p-3">
      <div class="flex flex-wrap items-center gap-2">
        <label class="text-xs text-[var(--ui-text-muted)] w-16">Level</label>
        <USelect
          v-model="level"
          :items="LEVEL_OPTIONS"
          size="sm"
          class="w-28"
          data-testid="public-link-level"
        />
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <label class="text-xs text-[var(--ui-text-muted)] w-16">Expires</label>
        <UInput
          v-model="expiry"
          type="date"
          size="sm"
          class="w-40"
          :min="today"
          data-testid="public-link-expiry"
        />
        <span class="text-xs text-[var(--ui-text-muted)]">optional — never if blank</span>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <label class="text-xs text-[var(--ui-text-muted)] w-16">Password</label>
        <UInput
          v-model="password"
          type="password"
          size="sm"
          class="w-40"
          placeholder="optional"
          autocomplete="new-password"
          data-testid="public-link-password"
        />
        <span class="text-xs text-[var(--ui-text-muted)]">optional passphrase</span>
      </div>

      <UButton
        icon="i-lucide-link"
        label="Create link"
        size="sm"
        class="self-start"
        :loading="creating"
        data-testid="public-link-create"
        @click="create"
      />
    </div>

    <!-- Freshly-created link: the only time the token (URL) is shown ---------- -->
    <div
      v-if="freshUrl"
      class="flex flex-col gap-2 rounded-md border border-[var(--ui-success)] bg-[var(--ui-bg-elevated)] p-3"
      data-testid="public-link-fresh"
    >
      <p class="text-xs font-medium text-[var(--ui-text-highlighted)]">
        Copy this link now — the server never returns it again.
      </p>
      <div class="flex items-center gap-2">
        <UInput
          :model-value="freshUrl"
          readonly
          size="sm"
          class="flex-1 font-mono"
          data-testid="public-link-url"
          @focus="(e: FocusEvent) => (e.target as HTMLInputElement)?.select()"
        />
        <UButton
          :icon="copiedKey === 'fresh' ? 'i-lucide-check' : 'i-lucide-copy'"
          :color="copiedKey === 'fresh' ? 'success' : 'primary'"
          size="sm"
          aria-label="Copy link"
          data-testid="public-link-copy"
          @click="freshUrl && copy(freshUrl, 'fresh')"
        />
      </div>
      <p class="text-xs text-[var(--ui-text-muted)]">
        Anyone with this link can open the list at
        <code>/p/&lt;token&gt;</code> — no account needed.
      </p>
    </div>

    <!-- Existing links ------------------------------------------------------- -->
    <p class="text-xs text-[var(--ui-text-muted)]">
      Link URLs aren't stored on the server. You can re-copy a link you created in
      this session below; after a page reload the URL can't be recovered — delete
      the link and create a new one for a fresh URL.
    </p>
    <ul
      v-if="links.length"
      class="rounded-md border border-[var(--ui-border)] divide-y divide-[var(--ui-border)]"
    >
      <li
        v-for="link in links"
        :key="link.id"
        class="flex items-center justify-between gap-2 px-3 py-2"
        data-testid="public-link-row"
      >
        <div class="flex min-w-0 flex-col gap-0.5">
          <div class="flex items-center gap-2">
            <UBadge color="neutral" variant="subtle" size="sm">{{ link.permission }}</UBadge>
            <span
              v-if="link.password_protected"
              class="text-xs text-[var(--ui-text-muted)]"
              title="Passphrase protected"
            >🔒</span>
          </div>
          <span class="text-xs text-[var(--ui-text-muted)]">
            {{ link.expires_at ? `Expires ${formatDate(link.expires_at)}` : "Never expires" }}
          </span>
        </div>

        <div class="flex shrink-0 items-center gap-2">
          <!-- Copy is only possible for links whose token we still hold from
               this session's create call; otherwise the URL is unrecoverable. -->
          <UButton
            v-if="urlFor(link)"
            :icon="copiedKey === link.id ? 'i-lucide-check' : 'i-lucide-copy'"
            :color="copiedKey === link.id ? 'success' : 'neutral'"
            variant="ghost"
            size="xs"
            aria-label="Copy link"
            data-testid="public-link-row-copy"
            @click="copy(urlFor(link)!, link.id)"
          />
          <UIcon
            v-else
            name="i-lucide-link-2-off"
            class="size-4 text-[var(--ui-text-muted)]"
            title="URL not retrievable — delete & recreate for a fresh link"
            data-testid="public-link-row-nolink"
          />
          <USwitch
            :model-value="link.enabled"
            :disabled="busyId === link.id"
            :aria-label="link.enabled ? 'Disable link' : 'Enable link'"
            data-testid="public-link-toggle"
            @update:model-value="(v: boolean) => toggle(link, v)"
          />
          <UButton
            icon="i-lucide-trash-2"
            color="error"
            variant="ghost"
            size="xs"
            aria-label="Delete link"
            :loading="busyId === link.id"
            data-testid="public-link-delete"
            @click="remove(link)"
          />
        </div>
      </li>
    </ul>
    <p v-else class="text-xs text-[var(--ui-text-muted)] italic">No public links yet.</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useShareStore } from "@/stores/share";

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
const toast = useToast();

const links = computed(() => shareStore.linksFor(props.checkListId));

const today = new Date().toISOString().slice(0, 10);

const level = ref<SharePermission>("view");
const expiry = ref<string>("");
const password = ref<string>("");
const creating = ref(false);
const busyId = ref<string | null>(null);

const freshUrl = ref<string | null>(null);
// Which URL was last copied (link id, or "fresh" for the just-created box) — for
// per-button "✓ copied" feedback.
const copiedKey = ref<string | null>(null);

onMounted(() => {
  // Owner-only endpoint — the parent only mounts this section for owners.
  shareStore.listLinks(props.checkListId).catch(() => {});
});

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

// The shareable URL for a link, IF we still hold its token from this session's
// create call. The server redacts tokens on list, so older links return null.
function urlFor(link: PublicLinkReadType): string | null {
  const token = shareStore.tokenFor(link.id);
  return token ? `${location.origin}/p/${token}` : null;
}

async function create() {
  creating.value = true;
  try {
    const body: PublicLinkCreateReq = { permission: level.value };
    // A date input gives a local "YYYY-MM-DD"; send it as an ISO timestamp.
    // The backend normalises tz to naive UTC.
    if (expiry.value) body.expires_at = new Date(expiry.value).toISOString();
    if (password.value) body.password = password.value;

    const res = await shareStore.createLink(props.checkListId, body);
    freshUrl.value = `${location.origin}/p/${res.token}`;
    copiedKey.value = null;
    // Reset the form for the next link (leave the level as-is for convenience).
    expiry.value = "";
    password.value = "";
    toast.add({ title: "Public link created", color: "success" });
  } catch {
    toast.add({ title: "Could not create public link", color: "error" });
  } finally {
    creating.value = false;
  }
}

async function copy(url: string, key: string) {
  try {
    await navigator.clipboard.writeText(url);
    copiedKey.value = key;
    setTimeout(() => {
      if (copiedKey.value === key) copiedKey.value = null;
    }, 2000);
  } catch {
    // Clipboard may be blocked (e.g. headless / insecure context) — the URL is
    // still visible and selectable in the field, so just nudge the user.
    toast.add({ title: "Copy failed — select and copy the link manually", color: "warning" });
  }
}

async function toggle(link: PublicLinkReadType, enabled: boolean) {
  busyId.value = link.id;
  try {
    await shareStore.updateLink(props.checkListId, link.id, { enabled });
  } catch {
    toast.add({ title: "Could not update link", color: "error" });
  } finally {
    busyId.value = null;
  }
}

async function remove(link: PublicLinkReadType) {
  busyId.value = link.id;
  try {
    await shareStore.deleteLink(props.checkListId, link.id);
    toast.add({ title: "Public link deleted", color: "success" });
  } catch {
    toast.add({ title: "Could not delete link", color: "error" });
  } finally {
    busyId.value = null;
  }
}
</script>

<style scoped></style>
