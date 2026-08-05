<template>
  <section class="flex flex-col gap-2" data-testid="card-reminders">
    <div class="flex items-center gap-2">
      <UIcon name="i-lucide-alarm-clock" class="size-3.5 shrink-0 opacity-70" />
      <h3 class="flex-1 text-xs font-semibold uppercase tracking-wide opacity-70">Reminders</h3>
      <UButton
        v-if="!adding"
        size="xs"
        variant="ghost"
        color="neutral"
        icon="i-lucide-plus"
        label="Add"
        :disabled="!online"
        data-testid="card-reminder-add"
        @click.stop="openForm()"
      />
    </div>

    <!-- Offline notice (plan decision 6 / WI-12): a reminder is per-user side
         data that never enters the offline outbox, because a queued reminder
         whose time passes before the queue drains is worse than useless. The
         controls go inert and say why rather than leaving a dead click. -->
    <UAlert
      v-if="!online"
      color="neutral"
      variant="subtle"
      icon="i-lucide-wifi-off"
      title="You're offline"
      description="Reminders need a connection. Reconnect to set or remove one."
      :ui="{ title: 'text-xs', description: 'text-xs' }"
      data-testid="card-reminder-offline-notice"
    />

    <p v-else-if="loadError" class="text-xs text-error" data-testid="card-reminder-load-error">
      Could not load your reminders for this card.
    </p>

    <ul v-if="reminders.length" class="flex flex-col gap-1">
      <li
        v-for="reminder in reminders"
        :key="reminder.id"
        class="flex items-start gap-2 rounded-md border border-current/10 px-2 py-1.5"
        data-testid="card-reminder-row"
      >
        <div class="flex min-w-0 flex-1 flex-col">
          <span class="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span
              class="font-medium"
              :title="formatAbsolute(reminder.remind_at)"
              data-testid="card-reminder-when"
            >
              {{ formatRemindAt(reminder.remind_at) }}
            </span>
            <span v-if="reminder.recurrence !== 'none'" class="text-xs opacity-70">
              {{ recurrenceLabel(reminder.recurrence) }}
            </span>
          </span>
          <span v-if="reminder.note" class="text-xs opacity-80 break-words">{{ reminder.note }}</span>
          <span v-if="zoneNote(reminder)" class="text-xs opacity-60">{{ zoneNote(reminder) }}</span>
        </div>
        <UButton
          icon="i-lucide-x"
          size="xs"
          variant="ghost"
          color="neutral"
          aria-label="Remove reminder"
          :disabled="!online"
          :loading="removingId === reminder.id"
          data-testid="card-reminder-remove"
          @click.stop="remove(reminder)"
        />
      </li>
    </ul>

    <p
      v-else-if="!adding && online && !loading"
      class="text-xs opacity-60"
      data-testid="card-reminder-empty"
    >
      Nothing set. Only you are reminded, and only you can see this.
    </p>

    <!-- The "new reminder" form is inline, NOT a nested dialog: the card editor
         is itself a modal, and stacking a second one is the double-dialog bug
         this codebase has already paid for once. -->
    <form
      v-if="adding"
      ref="formRef"
      class="flex flex-col gap-2 rounded-md border border-current/15 p-2"
      data-testid="card-reminder-form"
      @submit.prevent="submit()"
    >
      <div class="flex flex-wrap items-end gap-2">
        <label class="flex flex-col gap-1">
          <span class="text-xs opacity-70">Date</span>
          <UInput
            v-model="input.date"
            type="date"
            size="sm"
            :disabled="!online || saving"
            data-testid="card-reminder-date"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs opacity-70">Time</span>
          <UInput
            v-model="input.time"
            type="time"
            size="sm"
            :disabled="!online || saving"
            data-testid="card-reminder-time"
          />
        </label>
        <label class="flex flex-col gap-1">
          <span class="text-xs opacity-70">Repeat</span>
          <USelect
            v-model="input.recurrence"
            :items="recurrenceItems"
            size="sm"
            class="w-36"
            :disabled="!online || saving"
            data-testid="card-reminder-recurrence"
          />
        </label>
      </div>

      <UInput
        v-model="input.note"
        size="sm"
        :maxlength="NOTE_MAX_LENGTH"
        placeholder="Note (optional), e.g. call the plumber"
        :disabled="!online || saving"
        data-testid="card-reminder-note"
      />

      <p v-if="zoneHint" class="text-xs opacity-60">{{ zoneHint }}</p>
      <p v-if="error" class="text-xs text-error" data-testid="card-reminder-error">{{ error }}</p>

      <div class="flex justify-end gap-2">
        <UButton
          size="xs"
          variant="ghost"
          color="neutral"
          label="Cancel"
          :disabled="saving"
          data-testid="card-reminder-cancel"
          @click.stop="closeForm()"
        />
        <UButton
          type="submit"
          size="xs"
          color="primary"
          label="Set reminder"
          :disabled="!online"
          :loading="saving"
          data-testid="card-reminder-save"
        />
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useReminderStore } from "@/stores/reminder";
import { useConnectivity } from "@/composables/useConnectivity";
import {
  NOTE_MAX_LENGTH,
  defaultReminderInput,
  deviceZoneHint,
  formatAbsolute,
  formatRemindAt,
  recurrenceLabel,
  recurrenceOptions,
  reminderCreateBody,
  reminderErrorMessage,
  validateReminderInput,
  zoneNote,
  type ReminderInput,
} from "@/utils/reminders";

// The card editor's reminder section (chunk R4): the caller's own pending
// reminders on this card, and an inline form to add one. Opened either by the
// "Add" button here or by the kebab menu's "Set a reminder" item, which reaches
// this component through the handle provided below.
//
// A thin template over utils/reminders.ts (the E5 pattern): every decision worth
// testing (how a time reads, what the form pre-fills, what it sends, what a
// rejection means) is a pure function there.
//
// Reminders are personal (plan decision 1). Nothing here is shared with the
// card's collaborators, and the empty state says so, because a control on a
// shared card that silently notified everybody would be a nasty surprise.

const props = defineProps({
  checkListId: { type: String, required: true },
});

const store = useReminderStore();
const { online } = useConnectivity();

const adding = ref(false);
const formRef = ref<HTMLFormElement | null>(null);
const loading = ref(false);
const loadError = ref(false);
const saving = ref(false);
const removingId = ref<string | null>(null);
const error = ref<string | null>(null);
const input = ref<ReminderInput>(defaultReminderInput());

const reminders = computed(() => store.forCard(props.checkListId));
const recurrenceItems = recurrenceOptions();
const zoneHint = deviceZoneHint();

// The kebab menu's entry point. The menu is a sibling (it lives in the card
// footer), so the open card provides the handle and forwards it here rather
// than this component providing it to a subtree the menu is not in.
defineExpose({ open: openForm });

// Fetched when the editor opens rather than kept fresh: reminders are not part
// of the delta feed, so there is no push to reconcile against, and a card is
// open for seconds at a time.
onMounted(() => {
  if (online.value) void load();
});

// Reconnecting with the card still open fills the list in, rather than leaving
// an empty section that only a close-and-reopen would fix.
watch(online, (isOnline) => {
  if (isOnline && !store.loadedFor(props.checkListId)) void load();
});

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = false;
  try {
    await store.fetchForCard(props.checkListId);
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

function openForm(): void {
  if (!online.value) return;
  // A fresh default every time: "the next 09:00 that has not passed" is only
  // true at the moment it is computed.
  input.value = defaultReminderInput();
  error.value = null;
  adding.value = true;
  // The kebab menu's entry point can fire while the user is scrolled to the
  // top of a tall card, where the form that just opened is below the fold of
  // the editor's scroll region; reveal it. "nearest" keeps this a no-op when
  // the Add button opened the form already in view.
  nextTick(() => formRef.value?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
}

function closeForm(): void {
  adding.value = false;
  error.value = null;
}

async function submit(): Promise<void> {
  if (saving.value) return;
  // The browser check answers instantly and says exactly what the server would
  // have said; the server still decides (its clock is the one that counts).
  const invalid = validateReminderInput(input.value);
  if (invalid) {
    error.value = invalid;
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    await store.create(props.checkListId, reminderCreateBody(input.value) as ReminderCreateType);
    adding.value = false;
  } catch (err) {
    error.value = reminderErrorMessage(errorStatus(err), errorDetail(err));
  } finally {
    saving.value = false;
  }
}

async function remove(reminder: ReminderReadType): Promise<void> {
  removingId.value = reminder.id;
  try {
    await store.remove(props.checkListId, reminder.id);
  } catch (err) {
    // A 404 means somebody already removed it (another tab, or it was pruned),
    // so the list is what is wrong here, not the click.
    if (errorStatus(err) === 404) await load().catch(() => {});
    else error.value = reminderErrorMessage(errorStatus(err), errorDetail(err));
  } finally {
    removingId.value = null;
  }
}

function errorStatus(err: unknown): number | undefined {
  return (err as any)?.statusCode ?? (err as any)?.response?.status;
}

function errorDetail(err: unknown): string | null {
  const detail = (err as any)?.data?.detail;
  return typeof detail === "string" ? detail : null;
}
</script>

<style scoped></style>
