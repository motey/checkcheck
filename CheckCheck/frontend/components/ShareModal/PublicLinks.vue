<template>
  <div class="flex flex-col gap-3">
    <!-- Create link form ----------------------------------------------------- -->
    <div class="flex flex-col gap-2 rounded-md border border-default p-3">
      <div class="flex flex-wrap items-center gap-2">
        <label class="text-xs text-muted w-16">Level</label>
        <USelect
          v-model="level"
          :items="LEVEL_OPTIONS"
          size="sm"
          class="w-28"
          data-testid="public-link-level"
        />
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <label class="text-xs text-muted w-16">Expires</label>
        <UInput
          v-model="expiry"
          type="date"
          size="sm"
          class="w-40"
          :min="today"
          data-testid="public-link-expiry"
        />
        <span class="text-xs text-muted">optional — never if blank</span>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <label class="text-xs text-muted w-16">Password</label>
        <!-- Plain text (not type="password"): this is an optional link secret
             the owner is setting to share, not their own credential — showing
             it is helpful, and it keeps the browser password manager away. -->
        <UInput
          v-model="password"
          type="text"
          size="sm"
          class="w-40"
          placeholder="optional"
          autocomplete="off"
          data-testid="public-link-password"
        />
        <span class="text-xs text-muted">optional passphrase</span>
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
      class="flex flex-col gap-2 rounded-md border border-success bg-elevated p-3"
      data-testid="public-link-fresh"
    >
      <p class="text-xs font-medium text-highlighted">
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
      <p class="text-xs text-muted">
        Anyone with this link can open the list at
        <code>/p/&lt;token&gt;</code> — no account needed.
      </p>
    </div>

    <!-- Existing links ------------------------------------------------------- -->
    <p class="text-xs text-muted">
      Link URLs aren't stored on the server. You can re-copy a link you created in
      this session below; after a page reload the URL can't be recovered — delete
      the link and create a new one for a fresh URL.
    </p>
    <ul
      v-if="links.length"
      class="rounded-md border border-default divide-y divide-default"
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
              class="text-xs text-muted"
              title="Passphrase protected"
            >🔒</span>
          </div>
          <span class="text-xs text-muted">
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
            class="size-4 text-muted"
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
    <p v-else class="text-xs text-muted italic">No public links yet.</p>

    <!-- Send an existing link to somebody without an account (chunk E6) ------
         Only rendered once a link exists, and only inside this block: the whole
         point is that this can never be a first move, and never a sibling of the
         "Invite specific people" box. See utils/publicLinkEmail.ts for the
         mistake the wording here is designed against. -->
    <div
      v-if="emailEnabled && links.length"
      class="flex flex-col gap-3 rounded-md border border-default p-3"
      data-testid="public-link-email"
    >
      <div class="flex flex-col">
        <h4 class="text-sm font-semibold">People outside {{ appName }}</h4>
        <p class="text-xs text-muted">
          Send this link to someone without an account. They can work on the list
          straight from the message, without signing up.
        </p>
      </div>

      <!-- The difference people actually get wrong, side by side. -->
      <dl class="grid gap-2 sm:grid-cols-2 text-xs">
        <div class="rounded-md bg-elevated p-2">
          <dt class="font-medium text-highlighted">{{ AUDIENCE_COMPARISON.collaborator.title }}</dt>
          <dd class="text-muted">{{ AUDIENCE_COMPARISON.collaborator.line }}</dd>
        </div>
        <div class="rounded-md bg-elevated p-2">
          <dt class="font-medium text-highlighted">{{ AUDIENCE_COMPARISON.link.title }}</dt>
          <dd class="text-muted">{{ AUDIENCE_COMPARISON.link.line }}</dd>
        </div>
      </dl>

      <!-- Step 1: who, which link, and an optional note. -->
      <template v-if="!confirming">
        <div v-if="links.length > 1" class="flex flex-wrap items-center gap-2">
          <label class="text-xs text-muted w-16">Link</label>
          <USelect
            v-model="emailLinkId"
            :items="sendableLinkOptions"
            size="sm"
            class="w-56"
            data-testid="public-link-email-select"
          />
        </div>

        <UInput
          v-model="emailAddress"
          type="email"
          size="sm"
          placeholder="name@example.com"
          autocomplete="off"
          data-testid="public-link-email-address"
        />

        <UTextarea
          v-model="emailMessage"
          :rows="2"
          size="sm"
          placeholder="Add a short note (optional)"
          data-testid="public-link-email-message"
        />
        <p class="text-xs text-muted">
          {{ emailMessage.trim().length }}/{{ maxMessageLength }} characters
        </p>

        <!-- The soft hint. Never a block: a colleague's private address, or a
             device they are not signed in on, is a real case. -->
        <UAlert
          v-if="internalHint"
          color="warning"
          variant="subtle"
          icon="i-lucide-user-round-search"
          :title="internalHint.title"
          :description="internalHint.body"
          data-testid="public-link-email-hint"
        >
          <template #actions>
            <UButton
              v-if="canAddCollaborator"
              size="xs"
              label="Add as collaborator"
              data-testid="public-link-email-hint-collaborator"
              @click="addAsCollaborator"
            />
            <UButton
              size="xs"
              color="neutral"
              variant="subtle"
              label="Send link anyway"
              data-testid="public-link-email-hint-anyway"
              @click="review"
            />
          </template>
        </UAlert>

        <UButton
          v-else
          icon="i-lucide-mail"
          label="Send link"
          size="sm"
          class="self-start"
          :disabled="!emailAddress.trim()"
          data-testid="public-link-email-send"
          @click="review"
        />
      </template>

      <!-- Step 2: say what is about to happen, in the words the recipient gets. -->
      <div
        v-else
        class="flex flex-col gap-2 rounded-md border border-warning p-3"
        data-testid="public-link-email-confirm"
      >
        <p class="text-sm">{{ confirmLine }}</p>
        <p class="text-xs text-muted">
          They will not need to sign in, and anyone they pass the link on to gets
          the same access.
        </p>
        <div class="flex gap-2">
          <UButton
            icon="i-lucide-send"
            label="Send it"
            size="sm"
            :loading="sending"
            data-testid="public-link-email-confirm-send"
            @click="send"
          />
          <UButton
            label="Back"
            size="sm"
            color="neutral"
            variant="ghost"
            :disabled="sending"
            data-testid="public-link-email-confirm-back"
            @click="confirming = false"
          />
        </div>
      </div>

      <p
        v-if="emailResult"
        :class="['text-xs', emailResult.ok ? 'text-success' : 'text-error']"
        data-testid="public-link-email-result"
      >
        {{ emailResult.message }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useShareStore } from "@/stores/share";
import { usePublicConfigStore } from "@/stores/publicConfig";
import {
  AUDIENCE_COMPARISON,
  confirmSentence,
  internalDomainHint,
  sendErrorMessage,
  validateSend,
} from "@/utils/publicLinkEmail";

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
  // Whether the "Add people" collaborator box exists in this dialog at all
  // (it follows SHARING_USER_SEARCH_ENABLED). The internal-address hint's
  // primary action points at it, so it only offers that when there is one.
  canAddCollaborator: {
    type: Boolean,
    default: false,
  },
});

// Asks the parent to focus the collaborator search and pre-fill it. Emitted by
// the "Add as collaborator" action of the internal-address hint, which is the
// whole point of that hint: it has to land somewhere, not just scold.
const emit = defineEmits<{ addCollaborator: [term: string] }>();

const LEVEL_OPTIONS = [
  { label: "View", value: "view" },
  { label: "Check", value: "check" },
  { label: "Edit", value: "edit" },
] as const;

const shareStore = useShareStore();
const publicConfig = usePublicConfigStore();
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
  // The instance's limits and the operator's own email domains, for the "send
  // this link to someone without an account" field below. Cached in the store,
  // so opening a second card's dialog does not ask again.
  if (publicConfig.publicLinkEmailEnabled) {
    shareStore.fetchPublicLinkEmailOptions().catch(() => {});
  }
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

// ── Sending a link to someone without an account (chunk E6) ─────────────────
//
// Two steps on purpose. The first collects the address and can warn that it
// looks like a colleague's; the second states, in plain words, what the person
// on the other end will be able to do. An inline confirm rather than a nested
// dialog: a modal inside a modal is exactly the shape that produced the
// double-dialog trouble this codebase already fixed once.

const emailEnabled = computed(() => publicConfig.publicLinkEmailEnabled);
const appName = "CheckCheck";

const emailAddress = ref("");
const emailMessage = ref("");
const emailLinkId = ref<string>("");
const confirming = ref(false);
const sending = ref(false);
const emailResult = ref<{ ok: boolean; message: string } | null>(null);

const options = computed(() => shareStore.publicLinkEmailOptions);
const maxMessageLength = computed(() => options.value?.max_message_length ?? 500);

// Only a link that would actually open for the recipient is worth sending; the
// server refuses a disabled or expired one anyway.
const sendableLinks = computed(() =>
  links.value.filter(
    (link) => link.enabled && (!link.expires_at || new Date(link.expires_at) > new Date())
  )
);
const sendableLinkOptions = computed(() =>
  sendableLinks.value.map((link) => ({
    label: `${link.permission}${link.password_protected ? " (passphrase)" : ""} · created ${formatDate(link.created_at)}`,
    value: link.id,
  }))
);
const selectedLink = computed(
  () => sendableLinks.value.find((link) => link.id === emailLinkId.value) ?? sendableLinks.value[0]
);

const internalHint = computed(() =>
  internalDomainHint(emailAddress.value, options.value?.internal_email_domains)
);
const confirmLine = computed(() =>
  confirmSentence(emailAddress.value, selectedLink.value?.permission ?? "view")
);

// Keep the picker pointing at a link that still exists (one may have just been
// deleted, or the first one may have only just been created).
watch(
  sendableLinks,
  (list) => {
    if (!list.some((link) => link.id === emailLinkId.value)) {
      emailLinkId.value = list[0]?.id ?? "";
    }
  },
  { immediate: true }
);

// Any edit invalidates the confirm step: the sentence it showed was about the
// address and level as they were when it opened. A failure message goes with it
// (it was about what was just typed), while a success one stays: sending clears
// the fields itself, and that must not wipe the only confirmation there is.
watch([emailAddress, emailMessage, emailLinkId], () => {
  confirming.value = false;
  if (emailResult.value && !emailResult.value.ok) emailResult.value = null;
});

function addAsCollaborator() {
  const term = internalHint.value?.searchTerm;
  if (term) emit("addCollaborator", term);
}

function review() {
  const check = validateSend(emailAddress.value, emailMessage.value, options.value);
  if (!check.ok) {
    emailResult.value = { ok: false, message: check.error! };
    return;
  }
  emailResult.value = null;
  confirming.value = true;
}

async function send() {
  const link = selectedLink.value;
  if (!link) return;
  sending.value = true;
  try {
    await shareStore.sendPublicLinkEmail(props.checkListId, {
      link_id: link.id,
      to: emailAddress.value.trim(),
      message: emailMessage.value.trim() || null,
    });
    // "Queued", not "sent": the dispatcher delivers it, so promising delivery
    // would be a promise this button cannot keep.
    emailResult.value = { ok: true, message: "The link is on its way." };
    emailAddress.value = "";
    emailMessage.value = "";
  } catch (err) {
    const status = (err as any)?.statusCode ?? (err as any)?.response?.status;
    emailResult.value = { ok: false, message: sendErrorMessage(status) };
  } finally {
    sending.value = false;
    confirming.value = false;
  }
}
</script>

<style scoped></style>
