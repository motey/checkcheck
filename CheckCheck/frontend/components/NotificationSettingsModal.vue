<template>
  <UModal
    v-model:open="open"
    title="Notification settings"
    description="Choose what you are told about, and where."
    :ui="{
      content: 'max-w-5xl w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default overflow-hidden',
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
          <!-- The matrix: what you are told about (rows) crossed with where you
               are told (columns). Two layouts over the one `rows` computed, and
               only ever one of them in the DOM (`v-if`, not a CSS `hidden`), so
               a testid means exactly one element whichever is on screen.

               Wide: a real grid, so each channel's title and description are
               written once at the top of their column instead of once per type.
               That repetition was the finding this chunk exists for. -->
          <div v-if="wide" class="flex flex-col gap-1" data-testid="notification-matrix">
            <div class="matrix-row grid gap-2 px-3 pb-1" :style="matrixColumns">
              <div></div>
              <div
                v-for="column in columns"
                :key="column.channel"
                class="flex flex-col"
                :data-testid="`notification-channel-${column.channel}`"
              >
                <span class="text-xs font-semibold text-highlighted">{{ column.title }}</span>
                <span class="text-[11px] leading-tight text-muted">{{ column.description }}</span>
              </div>
            </div>

            <div
              v-for="row in rows"
              :key="row.type"
              class="matrix-row grid items-center gap-2 rounded-lg border border-default px-3 py-2"
              :style="matrixColumns"
            >
              <!-- The type description is a tooltip here, not a second line:
                   four of them stacked was a third of the dialog's height. The
                   stacked layout below still shows it, where there is room. -->
              <div
                class="min-w-0 text-sm font-medium text-highlighted"
                :title="row.description"
                :data-testid="`notification-type-${row.type}`"
              >
                {{ row.title }}
              </div>
              <div
                v-for="column in columns"
                :key="column.channel"
                class="flex min-w-0 items-center gap-1"
              >
                <template v-if="cellFor(row, column.channel)">
                  <USelect
                    :model-value="cellFor(row, column.channel)!.value"
                    :items="cellFor(row, column.channel)!.options"
                    size="sm"
                    class="min-w-0 flex-1"
                    :aria-label="`${row.title}: ${column.title}`"
                    :disabled="
                      cellFor(row, column.channel)!.disabled ||
                      busyCell === cellKey(row.type, column.channel)
                    "
                    :data-testid="`notification-mode-${row.type}-${column.channel}`"
                    @update:model-value="(v: ModeChoice) => onModeChange(row.type, column.channel, v)"
                  />
                  <!-- Decision 5: the reason moves into the cell as an icon with
                       a tooltip, and the wording stays in the DOM (and in the
                       accessibility tree) on the element carrying the testid. -->
                  <span
                    v-if="cellFor(row, column.channel)!.hint"
                    class="shrink-0 text-muted"
                    :title="cellFor(row, column.channel)!.hint!"
                    :data-testid="`notification-hint-${row.type}-${column.channel}`"
                  >
                    <UIcon
                      :name="
                        cellFor(row, column.channel)!.disabled ? 'i-lucide-lock' : 'i-lucide-info'
                      "
                      class="block size-3.5"
                    />
                    <span class="sr-only">{{ cellFor(row, column.channel)!.hint }}</span>
                  </span>
                </template>
                <!-- A type the server did not send this channel for: the column
                     stays, empty, so the grid keeps its alignment. -->
              </div>
            </div>
          </div>

          <!-- Narrow: one block per type, the channel named per control. A four
               column grid at 360 px is unreadable and a settings table that
               scrolls sideways is worse than a stack. -->
          <template v-else>
            <div
              v-for="row in rows"
              :key="row.type"
              class="flex flex-col gap-3 rounded-lg border border-default p-3"
            >
              <div class="flex flex-col" :data-testid="`notification-type-${row.type}`">
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
          </template>

          <!-- Channel setup, below the matrix rather than between the types.
               The matrix answers "what am I told about, and where"; these answer
               "is that channel set up at all", and they are in the same order as
               the columns above. -->
          <h3
            v-if="emailEnabled || webhookEnabled || pushEnabled"
            class="text-xs font-semibold uppercase tracking-wide text-muted"
          >
            Channel setup
          </h3>

          <!-- Email: the digest clock and a way to prove delivery. Both are
               meaningless on an instance without mail, where every email entry
               is locked off and the column above is not rendered. -->
          <div
            v-if="emailEnabled"
            class="flex flex-col gap-2 rounded-lg border border-default p-3"
            data-testid="notification-email"
          >
            <h4 class="text-sm font-semibold">Email</h4>
            <div class="flex flex-col gap-1">
              <p class="text-xs text-muted">
                A daily summary goes out at {{ DAILY_DIGEST_WORDING }} in this zone. Hourly
                summaries and immediate mail ignore it.
              </p>
              <USelectMenu
                v-model="timezone"
                :items="timezoneOptions"
                value-key="value"
                size="sm"
                class="w-full sm:w-72"
                aria-label="Time zone"
                :disabled="busyCell === TIMEZONE_KEY"
                data-testid="notification-timezone"
                @update:model-value="onTimezoneChange"
              />
              <!-- S2: the zone now follows the device only when the *device's*
                   zone changes, so a deliberate pick survives a reload of the
                   same machine and a trip still moves the digest. Said out loud
                   either way, because the alternative is a user discovering it
                   from a digest landing at the wrong hour. -->
              <p class="text-xs text-muted" data-testid="notification-timezone-sync-note">
                Follows this device when it moves to another zone. A zone you pick
                here stays until then, and reminders keep the zone they were
                created in.
              </p>
            </div>
            <div class="flex flex-col gap-1">
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
              <p class="text-xs text-muted">
                Queues one message to your own address. Nobody else gets a copy.
              </p>
              <p
                v-if="testResult"
                :class="['text-xs', testResult.ok ? 'text-success' : 'text-error']"
                data-testid="notification-test-email-result"
              >
                {{ testResult.message }}
              </p>
            </div>
          </div>

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
              <h4 class="text-sm font-semibold">Webhook</h4>
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

          <!-- Push extras (P2): enable this device, manage the device list, and a
               way to prove delivery. Only on an instance with push switched on,
               where the column above is rendered too. -->
          <div
            v-if="pushEnabled"
            class="flex flex-col gap-2 rounded-lg border border-default p-3"
            data-testid="notification-push"
          >
            <div class="flex flex-col">
              <h4 class="text-sm font-semibold">Push notifications</h4>
              <p class="text-xs text-muted">
                A notification on this device's lock screen or notification tray,
                even when CheckCheck is not open in a tab.
              </p>
            </div>

            <p
              v-if="pushIsIOS && !pushIsStandalone"
              class="text-xs text-muted"
              data-testid="notification-push-ios-hint"
            >
              Add CheckCheck to your home screen to enable notifications on this
              iPhone.
            </p>
            <p
              v-else-if="!pushSupported"
              class="text-xs text-muted"
              data-testid="notification-push-unsupported-hint"
            >
              This browser does not support push notifications.
            </p>
            <UButton
              v-else
              :icon="pushIsSubscribedHere ? 'i-lucide-check' : 'i-lucide-bell-plus'"
              :label="
                pushIsSubscribedHere
                  ? 'Enabled on this device'
                  : 'Enable notifications on this device'
              "
              size="sm"
              variant="soft"
              class="self-start"
              :loading="pushEnabling"
              :disabled="pushIsSubscribedHere"
              data-testid="notification-push-enable"
              @click="enablePush"
            />
            <p v-if="pushError" class="text-xs text-error" data-testid="notification-push-error">
              {{ pushError }}
            </p>

            <ul
              v-if="pushDevices.length"
              class="rounded-md border border-default divide-y divide-default"
              data-testid="notification-push-devices"
            >
              <li
                v-for="device in pushDevices"
                :key="device.id"
                class="flex items-center justify-between gap-2 px-3 py-2"
                data-testid="notification-push-device"
              >
                <div class="flex min-w-0 flex-col gap-0.5">
                  <span class="text-sm font-medium text-highlighted truncate">
                    {{ deviceLabel(device.user_agent) }}
                    <span v-if="isThisPushDevice(device)" class="text-xs text-muted">
                      (this device)
                    </span>
                  </span>
                  <span class="text-xs text-muted">Added {{ formatDate(device.created_at) }}</span>
                </div>
                <UButton
                  icon="i-lucide-trash-2"
                  color="error"
                  variant="ghost"
                  size="xs"
                  aria-label="Remove device"
                  :loading="pushBusyId === device.id"
                  data-testid="notification-push-remove"
                  @click="disablePush(device)"
                />
              </li>
            </ul>

            <UButton
              icon="i-lucide-send"
              label="Send test push"
              size="sm"
              variant="soft"
              class="self-start"
              :loading="testingPush"
              :disabled="!pushDevices.length"
              data-testid="notification-test-push"
              @click="sendTestPush"
            />
            <p
              v-if="pushTestResult"
              :class="['text-xs', pushTestResult.ok ? 'text-success' : 'text-error']"
              data-testid="notification-test-push-result"
            >
              {{ pushTestResult.message }}
            </p>
          </div>
        </div>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMediaQuery } from "@vueuse/core";
import { useNotificationStore } from "@/stores/notification";
import { useConnectivity } from "@/composables/useConnectivity";
import { usePushSubscription } from "@/composables/usePushSubscription";
import { deviceLabel } from "@/utils/push";
import {
  UTC_VALUE,
  channelWording,
  looksLikeWebhookUrl,
  prefsPatch,
  testWebhookMessage,
  timezoneItems,
  timezonePatchValue,
  timezoneSelectValue,
  typeRows,
  visibleChannels,
  webhookUrlPatchValue,
  type ModeChoice,
  type TypeRow,
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
const {
  devices: pushDevices,
  enabling: pushEnabling,
  busyId: pushBusyId,
  error: pushError,
  supported: pushSupported,
  isIOS: pushIsIOS,
  isStandalone: pushIsStandalone,
  isSubscribedHere: pushIsSubscribedHere,
  isThisDevice: isThisPushDevice,
  refresh: refreshPush,
  enable: enablePush,
  disable: disablePush,
} = usePushSubscription();

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
const testingPush = ref(false);
const pushTestResult = ref<{ ok: boolean; message: string } | null>(null);

let savedTimer: ReturnType<typeof setTimeout> | null = null;

const settings = computed(() => store.settings);
// The response's own flags, not the public-config ones: they are what actually
// decided whether the email and webhook entries are locked in the matrix we are
// rendering.
const emailEnabled = computed(() => settings.value?.email_enabled ?? false);
const webhookEnabled = computed(() => settings.value?.webhook_enabled ?? false);
const pushEnabled = computed(() => settings.value?.push_enabled ?? false);
const rows = computed(() =>
  typeRows(
    settings.value?.types,
    visibleChannels({
      email_enabled: emailEnabled.value,
      webhook_enabled: webhookEnabled.value,
      push_enabled: pushEnabled.value,
    })
  )
);
// Nothing to save until the field differs from what the server holds, which also
// keeps the button from re-sending the same URL on every click.
const webhookUrlChanged = computed(
  () => webhookUrl.value.trim() !== (settings.value?.webhook_url ?? "")
);
const timezoneOptions = computed(() => timezoneItems(settings.value?.timezone ?? null));

// --- the matrix layout -------------------------------------------------------
// Tailwind's `sm` breakpoint, read as a signal rather than applied as a class:
// the grid and the stack are two templates over the same rows, and rendering
// both (one CSS-hidden) would put every `data-testid` in the DOM twice. Safe as
// a `v-if` because this app is an SPA (`ssr: false`), so there is no server
// render to disagree with.
const wide = useMediaQuery("(min-width: 640px)");

// The columns, resolved once for the header instead of once per type. This is
// the finding: the channel titles and their descriptions used to be repeated in
// every one of the four type blocks.
const columns = computed(() =>
  visibleChannels({
    email_enabled: emailEnabled.value,
    webhook_enabled: webhookEnabled.value,
    push_enabled: pushEnabled.value,
  }).map((channel) => ({ channel, ...channelWording(channel) }))
);

// A CSS variable rather than a `sm:grid-cols-[...]` class, because the column
// count is data (one to four channels) and Tailwind can only generate classes it
// can see in the source. The media query lives in this component's <style>.
const matrixColumns = computed(() => ({
  "--matrix-cols": `minmax(0, 1.2fr) repeat(${columns.value.length}, minmax(0, 1fr))`,
}));

function cellFor(row: TypeRow, channel: string) {
  return row.cells.find((cell) => cell.channel === channel) ?? null;
}

function cellKey(type: string, channel: string): string {
  return `${type}:${channel}`;
}

// (Re)load each time the dialog opens: an administrator may have changed the
// caps, and another device may have changed the preferences. `immediate` because
// this is a place now (S1): a cold load of /settings/notifications mounts the
// dialog already open, so a watcher that only fires on a *change* would leave it
// empty forever.
watch(open, (isOpen) => {
  if (!isOpen) {
    testResult.value = null;
    webhookResult.value = null;
    pushTestResult.value = null;
    // Including the registration error (N5): reopening the dialog re-reconciles
    // the device list, so last time's "already registered to another account"
    // must not be sitting under a button that now works.
    pushError.value = null;
    return;
  }
  void load();
}, { immediate: true });

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
    timezone.value = timezoneSelectValue(res.timezone);
    webhookUrl.value = res.webhook_url ?? "";
    if (res.push_enabled) await refreshPush().catch(() => {});
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
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

async function sendTestPush(): Promise<void> {
  testingPush.value = true;
  pushTestResult.value = null;
  try {
    const res = await store.sendTestPush();
    pushTestResult.value = {
      ok: true,
      message: `Queued to ${res.subscription_count} device${
        res.subscription_count === 1 ? "" : "s"
      }. It should arrive shortly.`,
    };
  } catch (err) {
    const status = errorStatus(err);
    pushTestResult.value = {
      ok: false,
      message:
        status === 409
          ? "No device is subscribed yet, so there is nowhere to send it."
          : status === 429
          ? "A test push was already queued less than a minute ago. Try again shortly."
          : "Could not queue the test push.",
    };
  } finally {
    testingPush.value = false;
  }
}

async function save(key: string, update: NotificationSettingsUpdateType): Promise<void> {
  busyCell.value = key;
  try {
    const res = await store.saveSettings(update);
    timezone.value = timezoneSelectValue(res.timezone);
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
    timezone.value = timezoneSelectValue(settings.value?.timezone);
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

<style scoped>
/* The matrix's column template. It is here rather than in a Tailwind class
   because the column count depends on what the instance can deliver, and
   `--matrix-cols` is set inline from that. Only applies from `sm` up, which is
   also the breakpoint `wide` watches: below it there is no grid at all, just the
   stacked layout. */
@media (min-width: 640px) {
  .matrix-row {
    grid-template-columns: var(--matrix-cols);
  }
}
</style>
