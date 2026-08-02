<template>
  <UModal
    v-model:open="open"
    title="Notification settings"
    description="Choose what you are told about, and where."
    :ui="{
      content: 'max-w-xl w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default overflow-hidden',
      header: 'hidden',
    }"
  >
    <template #content>
      <div
        class="max-h-[92dvh] overflow-y-auto overflow-x-hidden p-4 sm:p-6 flex flex-col gap-5"
        data-testid="notification-settings"
      >
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-semibold">Notification settings</h2>
            <p class="text-sm text-muted">Choose what you are told about, and where.</p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <span
              v-if="saved"
              class="text-xs text-success flex items-center gap-1"
              data-testid="notification-settings-saved"
            >
              <UIcon name="i-lucide-check" class="size-3.5" />
              Saved
            </span>
            <UButton
              icon="i-lucide-x"
              color="neutral"
              variant="ghost"
              aria-label="Close"
              @click="open = false"
            />
          </div>
        </div>

        <!-- Offline notice (WI-12): the matrix is resolved server-side out of
             the user's choices and the instance configuration, so there is
             nothing sensible to queue. The controls below go inert until the
             connection is back. -->
        <UAlert
          v-if="!online"
          color="neutral"
          variant="subtle"
          icon="i-lucide-wifi-off"
          title="You're offline"
          description="Notification settings need a connection. Reconnect to change what you receive."
          data-testid="notification-settings-offline-notice"
        />

        <div v-if="loading" class="flex items-center justify-center py-10 text-muted">
          <UIcon name="i-lucide-loader-circle" class="size-6 animate-spin" />
        </div>

        <UAlert
          v-else-if="loadError"
          color="error"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          title="Could not load your settings"
          description="Close this dialog and try again."
          data-testid="notification-settings-error"
        />

        <div
          v-else-if="settings"
          :inert="!online"
          :class="['flex flex-col gap-5', { 'opacity-50 pointer-events-none': !online }]"
        >
          <!-- One block per notification type, one control per channel. -->
          <div
            v-for="row in rows"
            :key="row.type"
            class="flex flex-col gap-3 rounded-lg border border-default p-3"
            :data-testid="`notification-type-${row.type}`"
          >
            <div class="flex flex-col">
              <h3 class="text-sm font-semibold">{{ row.title }}</h3>
              <p class="text-xs text-muted">{{ row.description }}</p>
            </div>

            <div class="grid gap-3 sm:grid-cols-2">
              <div v-for="cell in row.cells" :key="cell.channel" class="flex flex-col gap-1">
                <label class="text-xs font-medium text-highlighted">{{ cell.title }}</label>
                <USelect
                  :model-value="cell.value"
                  :items="cell.options"
                  size="sm"
                  class="w-full"
                  :disabled="cell.disabled || busyCell === cellKey(row.type, cell.channel)"
                  :data-testid="`notification-mode-${row.type}-${cell.channel}`"
                  @update:model-value="(v: ModeChoice) => onModeChange(row.type, cell.channel, v)"
                />
                <p
                  v-if="cell.hint"
                  class="flex items-start gap-1 text-xs text-muted"
                  :data-testid="`notification-hint-${row.type}-${cell.channel}`"
                >
                  <UIcon
                    v-if="cell.disabled"
                    name="i-lucide-lock"
                    class="mt-0.5 size-3 shrink-0"
                  />
                  {{ cell.hint }}
                </p>
              </div>
            </div>
          </div>

          <!-- Email-only extras: the digest clock and a way to prove delivery.
               Both are meaningless on an instance without mail, where every
               email entry is locked off and the column above is not rendered. -->
          <template v-if="emailEnabled">
            <div class="flex flex-col gap-2 rounded-lg border border-default p-3">
              <div class="flex flex-col">
                <h3 class="text-sm font-semibold">Time zone</h3>
                <p class="text-xs text-muted">
                  When a daily summary goes out ({{ DAILY_DIGEST_WORDING }} in this zone).
                  Hourly summaries and immediate mail ignore it.
                </p>
              </div>
              <USelectMenu
                v-model="timezone"
                :items="timezoneOptions"
                value-key="value"
                size="sm"
                class="w-full sm:w-72"
                :disabled="busyCell === TIMEZONE_KEY"
                data-testid="notification-timezone"
                @update:model-value="onTimezoneChange"
              />
            </div>

            <div class="flex flex-col gap-2 rounded-lg border border-default p-3">
              <div class="flex flex-col">
                <h3 class="text-sm font-semibold">Check your email setup</h3>
                <p class="text-xs text-muted">
                  Queues one message to your own address. Nobody else gets a copy.
                </p>
              </div>
              <UButton
                icon="i-lucide-send"
                label="Send test email"
                size="sm"
                variant="soft"
                class="self-start"
                :loading="testing"
                data-testid="notification-test-email"
                @click="sendTest"
              />
              <p
                v-if="testResult"
                :class="['text-xs', testResult.ok ? 'text-success' : 'text-error']"
                data-testid="notification-test-email-result"
              >
                {{ testResult.message }}
              </p>
            </div>
          </template>

          <p v-if="!emailEnabled" class="text-xs text-muted italic" data-testid="notification-email-disabled">
            This server does not send email, so only the in-app notifications can
            be configured here.
          </p>

          <!-- Webhook extras (E6): where to POST, and a way to prove it works.
               Only on an instance that allows webhooks at all, where the column
               above is rendered too. -->
          <div
            v-if="webhookEnabled"
            class="flex flex-col gap-2 rounded-lg border border-default p-3"
            data-testid="notification-webhook"
          >
            <div class="flex flex-col">
              <h3 class="text-sm font-semibold">Webhook</h3>
              <p class="text-xs text-muted">
                Where the notifications you switched on for the webhook channel are
                POSTed, as a small JSON body. A URL pointing into a private network
                is refused unless this server was configured to allow it.
              </p>
              <p class="text-xs text-muted">
                Requests are not signed, so treat the URL as the secret: if your
                receiver needs to know a request really came from here, give it a
                path or token nobody can guess.
              </p>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <UInput
                v-model="webhookUrl"
                type="url"
                size="sm"
                class="w-full sm:w-96"
                placeholder="https://example.com/hooks/checkcheck"
                autocomplete="off"
                :disabled="busyCell === WEBHOOK_KEY"
                data-testid="notification-webhook-url"
              />
              <UButton
                label="Save"
                size="sm"
                variant="soft"
                :loading="busyCell === WEBHOOK_KEY"
                :disabled="!webhookUrlChanged"
                data-testid="notification-webhook-save"
                @click="onWebhookUrlSave"
              />
              <UButton
                icon="i-lucide-webhook"
                label="Send test webhook"
                size="sm"
                variant="soft"
                :loading="testingWebhook"
                :disabled="!settings?.webhook_url"
                data-testid="notification-webhook-test"
                @click="sendTestWebhook"
              />
            </div>
            <p
              v-if="webhookResult"
              :class="['text-xs', webhookResult.ok ? 'text-success' : 'text-error']"
              data-testid="notification-webhook-result"
            >
              {{ webhookResult.message }}
            </p>
          </div>
        </div>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useNotificationStore } from "@/stores/notification";
import { useConnectivity } from "@/composables/useConnectivity";
import {
  UTC_VALUE,
  looksLikeWebhookUrl,
  prefsPatch,
  testWebhookMessage,
  timezoneItems,
  timezonePatchValue,
  typeRows,
  visibleChannels,
  webhookUrlPatchValue,
  type ModeChoice,
} from "@/utils/notificationSettings";

// Notification preferences (chunk E5), opened from the user menu next to "API
// keys" and built the same way: a declarative `v-model:open` modal that mounts
// once, so it cannot double-dialog the way the old imperative overlays did.
//
// Every change saves on its own, as a patch naming exactly the one cell that
// changed, and the response (the full effective matrix) replaces what we hold.
// That is why there is no Save button and no local draft: the server resolves
// the matrix out of the user's choices *and* the instance configuration, so the
// only honest thing to display is its answer.

const open = defineModel<boolean>("open", { default: false });

const store = useNotificationStore();
const { online } = useConnectivity();
const toast = useToast();

// Matches `notify/schedule.DAILY_DIGEST_HOUR` (08:00 local). A module constant
// server-side, so there is nothing to fetch.
const DAILY_DIGEST_WORDING = "08:00";
const TIMEZONE_KEY = "timezone";
const WEBHOOK_KEY = "webhook_url";

const loading = ref(false);
const loadError = ref(false);
// `${type}:${channel}` (or TIMEZONE_KEY) while that control's PUT is in flight.
const busyCell = ref<string | null>(null);
const saved = ref(false);
const testing = ref(false);
const testResult = ref<{ ok: boolean; message: string } | null>(null);
const timezone = ref<string>(UTC_VALUE);
const webhookUrl = ref<string>("");
const testingWebhook = ref(false);
const webhookResult = ref<{ ok: boolean; message: string } | null>(null);

let savedTimer: ReturnType<typeof setTimeout> | null = null;

const settings = computed(() => store.settings);
// The response's own flags, not the public-config ones: they are what actually
// decided whether the email and webhook entries are locked in the matrix we are
// rendering.
const emailEnabled = computed(() => settings.value?.email_enabled ?? false);
const webhookEnabled = computed(() => settings.value?.webhook_enabled ?? false);
const rows = computed(() =>
  typeRows(
    settings.value?.types,
    visibleChannels({
      email_enabled: emailEnabled.value,
      webhook_enabled: webhookEnabled.value,
    })
  )
);
// Nothing to save until the field differs from what the server holds, which also
// keeps the button from re-sending the same URL on every click.
const webhookUrlChanged = computed(
  () => webhookUrl.value.trim() !== (settings.value?.webhook_url ?? "")
);
const timezoneOptions = computed(() => timezoneItems(settings.value?.timezone ?? null));

function cellKey(type: string, channel: string): string {
  return `${type}:${channel}`;
}

// (Re)load each time the dialog opens: an administrator may have changed the
// caps, and another device may have changed the preferences.
watch(open, (isOpen) => {
  if (!isOpen) {
    testResult.value = null;
    webhookResult.value = null;
    return;
  }
  void load();
});

// Reconnecting with the dialog still open fills it in, rather than leaving an
// empty dialog that only a close-and-reopen would fix.
watch(online, (isOnline) => {
  if (isOnline && open.value && !settings.value) void load();
});

async function load(): Promise<void> {
  if (!online.value) return;
  loading.value = true;
  loadError.value = false;
  try {
    const res = await store.fetchSettings();
    timezone.value = res.timezone ?? UTC_VALUE;
    webhookUrl.value = res.webhook_url ?? "";
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

function flashSaved(): void {
  saved.value = true;
  if (savedTimer) clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (saved.value = false), 2000);
}

function errorStatus(err: unknown): number | undefined {
  return (err as any)?.statusCode ?? (err as any)?.response?.status;
}

async function onModeChange(type: string, channel: string, choice: ModeChoice): Promise<void> {
  await save(cellKey(type, channel), prefsPatch(type, channel, choice));
}

async function onTimezoneChange(value: string): Promise<void> {
  await save(TIMEZONE_KEY, { timezone: timezonePatchValue(value) });
}

async function onWebhookUrlSave(): Promise<void> {
  const url = webhookUrl.value.trim();
  if (url && !looksLikeWebhookUrl(url)) {
    webhookResult.value = {
      ok: false,
      message: "A webhook URL has to start with http:// or https://.",
    };
    return;
  }
  webhookResult.value = null;
  await save(WEBHOOK_KEY, { webhook_url: webhookUrlPatchValue(webhookUrl.value) });
}

async function sendTestWebhook(): Promise<void> {
  testingWebhook.value = true;
  webhookResult.value = null;
  try {
    const res = await store.sendTestWebhook();
    // Queued, not delivered, and deliberately so: a URL this server refuses to
    // call fails in the queue with the reason in the server log, which is the
    // only place it belongs.
    webhookResult.value = { ok: true, message: testWebhookMessage(undefined, res.url) };
  } catch (err) {
    webhookResult.value = { ok: false, message: testWebhookMessage(errorStatus(err)) };
  } finally {
    testingWebhook.value = false;
  }
}

async function save(key: string, update: NotificationSettingsUpdateType): Promise<void> {
  busyCell.value = key;
  try {
    const res = await store.saveSettings(update);
    timezone.value = res.timezone ?? UTC_VALUE;
    webhookUrl.value = res.webhook_url ?? "";
    flashSaved();
  } catch (err) {
    const status = errorStatus(err);
    toast.add({
      title:
        status === 409
          ? "Your administrator decides this one"
          : status === 400
          ? "That setting is not valid"
          : "Could not save your notification settings",
      color: "error",
    });
    // Show what the server actually holds rather than the control's guess.
    await store.fetchSettings().catch(() => {});
    timezone.value = settings.value?.timezone ?? UTC_VALUE;
    webhookUrl.value = settings.value?.webhook_url ?? "";
  } finally {
    busyCell.value = null;
  }
}

async function sendTest(): Promise<void> {
  testing.value = true;
  testResult.value = null;
  try {
    const res = await store.sendTestEmail();
    testResult.value = {
      ok: true,
      // 202 means queued: the dispatcher sends it on its next tick, so promising
      // "sent" would be a promise this button cannot keep.
      message: `Queued a message to ${res.to}. It should arrive shortly.`,
    };
  } catch (err) {
    const status = errorStatus(err);
    testResult.value = {
      ok: false,
      message:
        status === 409
          ? "There is nowhere to send it: your account has no email address, or this server does not send email."
          : status === 429
          ? "A test message was queued less than a minute ago. Try again shortly."
          : "Could not queue the test message.",
    };
  } finally {
    testing.value = false;
  }
}
</script>

<style scoped></style>
