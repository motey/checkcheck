<template>
  <UModal
    v-model:open="open"
    title="API keys"
    description="Create and manage tokens for programmatic access to your account."
    :ui="{
      content: 'max-w-lg w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default overflow-hidden',
      header: 'hidden',
    }"
  >
    <template #content>
      <div class="max-h-[92dvh] overflow-y-auto overflow-x-hidden p-4 sm:p-6 flex flex-col gap-5">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-semibold">API keys</h2>
            <p class="text-sm text-muted">
              Use these tokens as a <code>Bearer</code> credential for the API.
            </p>
          </div>
          <UButton
            icon="i-lucide-x"
            color="neutral"
            variant="ghost"
            aria-label="Close"
            @click="open = false"
          />
        </div>

        <!-- Create form ------------------------------------------------------ -->
        <div class="flex flex-col gap-2 rounded-md border border-default p-3">
          <div class="flex flex-wrap items-center gap-2">
            <label class="text-xs text-muted w-16">Name</label>
            <UInput
              v-model="name"
              size="sm"
              class="flex-1 min-w-40"
              placeholder="e.g. CI pipeline"
              maxlength="128"
              data-testid="api-key-name-input"
              @keydown.enter="create"
            />
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <label class="text-xs text-muted w-16">Expires</label>
            <USelect
              v-model="expiry"
              :items="expiryOptions"
              size="sm"
              class="w-40"
              data-testid="api-key-expiry"
            />
          </div>

          <UButton
            icon="i-lucide-key-round"
            label="Create key"
            size="sm"
            class="self-start"
            :loading="creating"
            :disabled="!name.trim()"
            data-testid="api-key-create"
            @click="create"
          />
        </div>

        <!-- Freshly-created token: the only time the plaintext is shown ------- -->
        <div
          v-if="freshToken"
          class="flex flex-col gap-2 rounded-md border border-success bg-elevated p-3"
          data-testid="api-key-fresh"
        >
          <p class="text-xs font-medium text-highlighted">
            Copy this token now — it won't be shown again.
          </p>
          <div class="flex items-center gap-2">
            <UInput
              :model-value="freshToken"
              readonly
              size="sm"
              class="flex-1 font-mono"
              data-testid="api-key-token"
              @focus="(e: FocusEvent) => (e.target as HTMLInputElement)?.select()"
            />
            <UButton
              :icon="copied ? 'i-lucide-check' : 'i-lucide-copy'"
              :color="copied ? 'success' : 'primary'"
              size="sm"
              aria-label="Copy token"
              data-testid="api-key-copy"
              @click="copy(freshToken!)"
            />
          </div>
          <p class="text-xs text-muted">
            Store it in a secret manager — anyone with this token has your API
            access until you revoke the key.
          </p>
        </div>

        <!-- Existing keys ---------------------------------------------------- -->
        <ul
          v-if="keys.length"
          class="rounded-md border border-default divide-y divide-default"
        >
          <li
            v-for="key in keys"
            :key="key.id"
            class="flex items-center justify-between gap-2 px-3 py-2"
            data-testid="api-key-row"
          >
            <div class="flex min-w-0 flex-col gap-0.5">
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium text-highlighted truncate">
                  {{ key.display_name || "Unnamed key" }}
                </span>
                <UBadge
                  v-if="isExpired(key)"
                  color="error"
                  variant="subtle"
                  size="sm"
                >expired</UBadge>
              </div>
              <span class="text-xs text-muted truncate font-mono">
                {{ key.api_token_id }}
              </span>
              <span class="text-xs text-muted">
                Created {{ formatDate(key.created_at) }} ·
                {{ key.expires_at_epoch_time
                  ? `Expires ${formatEpoch(key.expires_at_epoch_time)}`
                  : "Never expires" }}
                <template v-if="key.last_used_at">
                  · Last used {{ formatDate(key.last_used_at) }}
                </template>
              </span>
            </div>

            <div class="flex shrink-0 items-center gap-2">
              <!-- Small inline confirm: first click arms, second revokes. -->
              <template v-if="confirmingId === key.api_token_id">
                <span class="text-xs text-muted">Revoke?</span>
                <UButton
                  label="Yes"
                  color="error"
                  variant="soft"
                  size="xs"
                  :loading="busyId === key.api_token_id"
                  data-testid="api-key-revoke-confirm"
                  @click="revoke(key)"
                />
                <UButton
                  label="No"
                  color="neutral"
                  variant="ghost"
                  size="xs"
                  @click="confirmingId = null"
                />
              </template>
              <UButton
                v-else
                icon="i-lucide-trash-2"
                color="error"
                variant="ghost"
                size="xs"
                aria-label="Revoke key"
                data-testid="api-key-revoke"
                @click="confirmingId = key.api_token_id ?? null"
              />
            </div>
          </li>
        </ul>
        <p v-else-if="!loading" class="text-xs text-muted italic">
          No API keys yet.
        </p>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useUserStore } from "@/stores/user";
import { usePublicConfigStore } from "@/stores/publicConfig";

const open = defineModel<boolean>("open", { default: false });

const userStore = useUserStore();
const publicConfig = usePublicConfigStore();
const toast = useToast();

const keys = computed(() => userStore.apiKeys);

// The expiry dropdown offers concrete durations plus (when the server allows it)
// "Never". `"never"` maps to the request's `never_expires` flag; a number maps to
// `expires_in_days`. The server's configured default duration is always present
// and pre-selected (see `defaultExpiry`), so there's no abstract "server default"
// entry — the user sees the real value.
const EXPIRY_LABELS: Record<number, string> = {
  7: "7 days",
  30: "30 days",
  90: "90 days",
  365: "1 year",
};
function labelForDays(days: number): string {
  return EXPIRY_LABELS[days] ?? `${days} days`;
}

type ExpiryChoice = number | "never";

const expiryOptions = computed(() => {
  // Base durations, plus the server default so it can always be selected.
  const days = new Set<number>([7, 30, 90, 365]);
  const defaultDays = publicConfig.apiTokenDefaultExpiryDays;
  if (defaultDays != null) days.add(defaultDays);

  const options: { label: string; value: ExpiryChoice }[] = [...days]
    .sort((a, b) => a - b)
    .map((d) => ({ label: labelForDays(d), value: d }));

  if (publicConfig.apiTokenAllowNeverExpire) {
    options.push({ label: "Never expires", value: "never" });
  }
  return options;
});

// The option pre-selected when the form opens: the server's configured default
// duration, or "Never" when the server default is no-expiry (and it's allowed).
const defaultExpiry = computed<ExpiryChoice>(() => {
  const defaultDays = publicConfig.apiTokenDefaultExpiryDays;
  if (defaultDays != null) return defaultDays;
  if (publicConfig.apiTokenAllowNeverExpire) return "never";
  return expiryOptions.value[0]?.value ?? 30;
});

const name = ref("");
const expiry = ref<ExpiryChoice>(30);
const creating = ref(false);
const loading = ref(false);
const busyId = ref<string | null>(null);
const confirmingId = ref<string | null>(null);

// The plaintext token from the most recent create — shown once, held only in
// this component's local state, never in the store.
const freshToken = ref<string | null>(null);
const copied = ref(false);

// (Re)load keys each time the modal opens.
watch(
  open,
  async (isOpen) => {
    if (!isOpen) {
      // Reset transient state so a re-open starts clean (and the one-time token
      // never lingers).
      freshToken.value = null;
      confirmingId.value = null;
      return;
    }
    // Ensure feature flags are loaded so the expiry options / default are right,
    // then reset the selection to the server default for a fresh form.
    await publicConfig.fetch().catch(() => {});
    expiry.value = defaultExpiry.value;
    loading.value = true;
    try {
      await userStore.listApiKeys();
    } catch {
      toast.add({ title: "Could not load API keys", color: "error" });
    } finally {
      loading.value = false;
    }
  },
  { immediate: true }
);

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function formatEpoch(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

function isExpired(key: ApiKeyType): boolean {
  return (
    key.expires_at_epoch_time != null &&
    key.expires_at_epoch_time * 1000 < Date.now()
  );
}

async function create() {
  const displayName = name.value.trim();
  if (!displayName || creating.value) return;
  creating.value = true;
  try {
    const body: ApiKeyCreateReq = { display_name: displayName };
    if (expiry.value === "never") body.never_expires = true;
    else body.expires_in_days = expiry.value;
    const res = await userStore.createApiKey(body);
    freshToken.value = res.token;
    copied.value = false;
    name.value = "";
    toast.add({ title: "API key created", color: "success" });
  } catch {
    toast.add({ title: "Could not create API key", color: "error" });
  } finally {
    creating.value = false;
  }
}

async function copy(token: string) {
  try {
    await navigator.clipboard.writeText(token);
    copied.value = true;
    setTimeout(() => (copied.value = false), 2000);
  } catch {
    toast.add({
      title: "Copy failed — select and copy the token manually",
      color: "warning",
    });
  }
}

async function revoke(key: ApiKeyType) {
  if (!key.api_token_id) return;
  busyId.value = key.api_token_id;
  try {
    await userStore.revokeApiKey(key.api_token_id);
    // If we just revoked the key whose plaintext was on screen, hide it.
    toast.add({ title: "API key revoked", color: "success" });
  } catch {
    toast.add({ title: "Could not revoke API key", color: "error" });
  } finally {
    busyId.value = null;
    confirmingId.value = null;
  }
}
</script>

<style scoped></style>
