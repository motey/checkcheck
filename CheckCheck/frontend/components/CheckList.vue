<template>
  <div
    v-if="checkList"
    :style="cardStyle"
    :class="[
      hasColor ? '' : 'bg-elevated',
      previewModeActive ? 'p-3 sm:p-4 sm:min-h-32 shadow-sm hover:shadow-md hover:ring-1 hover:ring-default cursor-pointer' : 'p-5 sm:p-6',
      editModeActive ? 'max-h-[92dvh] overflow-hidden' : '',
    ]"
    class="checklist group/card relative list-drag-handle textareas-inherit-color flex flex-col gap-1 border border-default rounded-xl transition-shadow"
  >

    <!-- In the editor the modal renders its own close button at top-right, so
         shift the pin left of it; on board previews there is no close button. -->
    <CheckListFooterButtonPin :checkListId="checkListId" :scrollIntoViewOnPin="previewModeActive" :class="['absolute top-2 z-10', editModeActive ? 'right-10' : 'right-2']" />
    <!-- Offline-write indicator (WI-11): lights up while this card has queued ops. -->
    <CheckListSyncIndicator :checkListId="checkListId" />

    <div v-if="!editModeActive" data-testid="card-title" class="flex-none pr-8 text-base font-semibold leading-snug break-words line-clamp-2" v-html="highlightText(checkList!.name, searchQuery)" />
    <UTextarea
      v-if="editModeActive"
      :autofocus="autofocusTitle"
      autoresize
      variant="none"
      :rows="0"
      :padded="false"
      :disabled="!canEdit"
      placeholder="Enter a checklist title..."
      v-model="localName"
      class="flex-none w-full pr-16 text-xl sm:text-2xl font-semibold"
      @focus="onFieldFocus('name')"
      @blur="onFieldBlur('name')"
    />
    <!-- In edit mode this region scrolls on its own so the title above and the
         footer below stay pinned within the modal viewport. In preview/board
         mode it uses display:contents and behaves as if it weren't here. -->
    <div :class="editModeActive ? 'flex-1 min-h-0 overflow-y-auto overscroll-contain -mx-1 px-1' : 'contents'">
      <!-- Board preview: rendered Markdown, read-only, line-clamped. -->
      <div
        v-if="!editModeActive && checkList!.text"
        class="md-notes md-clamp flex-none text-sm opacity-80 break-words"
        v-html="renderMarkdown(checkList!.text, { search: searchQuery })"
      />
      <!-- Open card notes: focus-swap edit surface. Rendered Markdown when the
           field is not being edited (and always, for view-only collaborators);
           clicking/tabbing in swaps to the raw textarea to edit the source. -->
      <template v-if="editModeActive">
        <div
          v-if="!editingNotes || !canEdit"
          :class="[
            'md-notes w-full flex-none text-sm opacity-90 break-words',
            canEdit ? 'md-notes-editable cursor-text rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary' : '',
          ]"
          :role="canEdit ? 'textbox' : undefined"
          :tabindex="canEdit ? 0 : undefined"
          :aria-label="canEdit ? 'Notes' : undefined"
          data-testid="card-notes-rendered"
          @click="canEdit && enterNotesEdit()"
          @keydown.enter.prevent="canEdit && enterNotesEdit()"
          @keydown.space.prevent="canEdit && enterNotesEdit()"
        >
          <div v-if="localText" v-html="renderMarkdown(localText)" />
          <span v-else class="text-dimmed">Enter some notes...</span>
        </div>
        <UTextarea
          v-else
          ref="notesTextField"
          autoresize
          variant="none"
          :rows="0"
          :padded="false"
          placeholder="Enter some notes..."
          v-model="localText"
          class="w-full flex-none text-sm opacity-90"
          data-testid="card-notes-textarea"
          @focus="onFieldFocus('text')"
          @blur="onNotesBlur"
        />
        <!-- Unobtrusive hint + Markdown cheat-sheet popup (editable notes only). -->
        <div v-if="canEdit" class="flex-none mt-0.5 text-xs text-dimmed">
          Markdown supported ·
          <UPopover mode="click" :content="{ side: 'top', align: 'start' }">
            <button type="button" class="underline hover:text-muted cursor-pointer" data-testid="markdown-help-trigger">
              Formatting help
            </button>
            <template #content>
              <MarkdownHelp />
            </template>
          </UPopover>
        </div>
      </template>
      <div class="checklist-items-collection mt-1">
        <CheckListItemCollectionSeperated
          v-if="checkList?.checked_items_seperated"
          :parentCheckList="checkList"
          :showMaxItems="showMaxItems"
          :editModeActive="editModeActive"
        />
        <CheckListItemCollection
          v-else-if="editModeActive"
          :parentCheckList="checkList!"
          :showMaxItems="showMaxItems"
          :filterCheckedItems="undefined"
        />
        <CheckListItemCollectionPreview
          v-else
          :parentCheckList="checkList!"
          :showMaxItems="showMaxItems"
          :filterCheckedItems="undefined"
        />
      </div>
    </div>

    <!-- Editor: the full action footer is always visible (Keep's model — card
         actions live in the open card, not on the board). -->
    <div
      v-if="editModeActive"
      class="checklist-footer flex-none mt-3 pt-2 border-t border-current/10"
    >
      <CheckListFooter :checkListId="checkListId" />
    </div>
    <!-- Board preview. Labels stay on the card at all times (Keep keeps label
         chips visible; only the action toolbar is hover-revealed) — they're
         metadata you scan the board by, not an action. SelectedList self-hides
         when the card has no labels, so empty cards get no stray row. -->
    <div v-else-if="previewModeActive" class="flex-none">
      <CheckListFooterLabelsSelectedList :checkListId="checkListId" class="mt-3" />
      <!-- Action toolbar is decluttered off the card: on desktop it fades in on
           hover so quick actions (incl. share-button) stay one hover away; on
           touch (no hover) it is hidden entirely — open the card to act. Kept
           mounted (opacity, not v-if) so share-button stays reachable. -->
      <div
        class="checklist-footer hidden sm:block mt-3 pt-2 border-t border-current/10 opacity-0 group-hover/card:opacity-100 transition-opacity"
      >
        <CheckListFooterToolbar :checkListId="checkListId" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const runtimeConfig = useRuntimeConfig();
const appConfig = useAppConfig();
import { useDebounceFn, useMediaQuery } from "@vueuse/core";
import { useCheckListsStore } from "@/stores/checklist";
import { useCheckListsItemStore } from "@/stores/checklist_item";
import { highlightText } from "@/utils/highlight";
import { renderMarkdown } from "@/utils/markdown";
import { isLocalFirstEnabled } from "@/utils/localFirst";
import { markEditing, clearEditing } from "@/utils/editGuard";
import { useCreateCheckList } from "~/composables/useCreateCheckList";
const colorMode = useColorMode();

const checkListsStore = useCheckListsStore();
const checkListsItemStore = useCheckListsItemStore();
const route = useRoute();
const searchQuery = computed(() => (route.query.search as string) || null);

const props = defineProps({
  checkListId: {
    type: String,
    required: true,
  },
  editModeActive: { type: Boolean, default: false },
  previewModeActive: { type: Boolean, default: false },
});

// Autofocus the title only when this editor is opening a card that was JUST
// created (empty list → cursor ready to type). Reopening an existing card must
// leave every field unfocused so mobile keyboards don't pop up unprompted.
// Captured once at setup and cleared immediately so a later reopen of the same
// card doesn't re-trigger it. The modal keys CheckList by id, so setup re-runs
// on every open.
const { newlyCreatedCardId } = useCreateCheckList();
const autofocusTitle =
  props.editModeActive && newlyCreatedCardId.value === props.checkListId;
if (autofocusTitle) newlyCreatedCardId.value = null;

// Phones show fewer preview items so a long list stays short in the denser
// two-column grid; the existing "+N items" hint still surfaces the remainder.
const isMobile = useMediaQuery("(max-width: 639px)");
const showMaxItems = computed<number | undefined>(() => {
  if (!props.previewModeActive) return undefined;
  return isMobile.value ? 5 : appConfig.previewItemCount;
});

// computed() so the component always reads the current store object.
// ref() would cache a pointer to the object at mount-time; if the store
// replaces it (e.g. splice in refresh()), the component becomes stale.
const checkList = computed(() => checkListsStore.get(props.checkListId));

// Editing the card name/notes requires edit access (P0.1 / usePermissions).
const { can } = usePermissions();
const canEdit = computed(() => can(checkList.value, "edit"));

// When a card has no color theme, we return undefined and let the default
// theme classes (bg-elevated / border-default / inherited text) take over —
// that keeps uncolored cards subtle instead of forcing a stark black border.
const hasColor = computed(() => !!checkList.value?.color);

const textColor = computed(() => {
  const { color } = checkList.value!;
  if (!color) return undefined;
  return colorMode.value === "dark" ? color.textcolor_dark_hex : color.textcolor_light_hex;
});
const accentColor = computed(() => {
  const { color } = checkList.value!;
  if (!color) return undefined;
  return colorMode.value === "dark" ? color.accentcolor_dark_hex : color.accentcolor_light_hex;
});
const backgroundColor = computed(() => {
  const { color } = checkList.value!;
  if (!color) return undefined;
  return colorMode.value === "dark" ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex;
});

const cardStyle = computed(() => {
  const style: Record<string, string> = {};
  if (textColor.value) style.color = textColor.value;
  if (backgroundColor.value) style.backgroundColor = backgroundColor.value;
  if (accentColor.value) style.borderColor = accentColor.value;
  return style;
});

const notesTextField = ref();
const nameFocused = ref(false);
const textFocused = ref(false);

// Focus-swap: the notes region shows rendered Markdown until the user edits it.
// `editingNotes` mounts the raw textarea; we focus it on the next tick so the
// caret lands there. Blur (onNotesBlur) swaps back to the rendered view.
const editingNotes = ref(false);
function enterNotesEdit() {
  if (!canEdit.value) return;
  editingNotes.value = true;
  nextTick(() => notesTextField.value?.textareaRef?.focus?.());
}
function onNotesBlur() {
  onFieldBlur("text");
  editingNotes.value = false;
}

// Focus/blur handlers keep the local textarea guard AND the WI-10 store-apply
// guard in sync, so a remote delta never clobbers the field mid-edit (SYNC §4).
function onFieldFocus(field: "name" | "text") {
  if (field === "name") nameFocused.value = true;
  else textFocused.value = true;
  markEditing("checklist", props.checkListId, field);
}
function onFieldBlur(field: "name" | "text") {
  if (field === "name") nameFocused.value = false;
  else textFocused.value = false;
  clearEditing("checklist", props.checkListId, field);
}
onBeforeUnmount(() => {
  // A card closed mid-edit must not leave a stale editing mark pinned.
  clearEditing("checklist", props.checkListId, "name");
  clearEditing("checklist", props.checkListId, "text");
});

if (!props.previewModeActive && props.checkListId) {
  // Ensure the card itself is loaded (supports deep-linking a card that isn't
  // on the current board page yet) before refreshing its items.
  await checkListsStore.fetch(props.checkListId).catch(() => {});
  // Reconcile the card's items. Flag-off always refetches (legacy). Flag-on
  // (WI-10) reconciles via the delta pull instead — and only does a full item
  // GET the FIRST time a card is opened (never fully loaded locally), to backfill
  // a card we'd only seen as a board preview. Once loaded, deltas keep it fresh,
  // so we don't refetch — which also avoids clobbering undrained optimistic edits.
  if (isLocalFirstEnabled()) {
    if (!checkListsItemStore.checklistWasFullLoadedOnce[props.checkListId]) {
      await checkListsItemStore.refreshAllCheckListItems(props.checkListId).catch(() => {});
    }
  } else {
    await checkListsItemStore.refreshAllCheckListItems(props.checkListId).catch(() => {});
  }
}

// Local copies decoupled from the store so SSE/update patches don't wipe
// text the user is currently typing.
const localName = ref(checkList.value?.name ?? '');
const localText = ref(checkList.value?.text ?? '');

// Sync FROM store only when the respective field is not focused.
watch(() => checkList.value?.name, (n) => { if (!nameFocused.value) localName.value = n ?? ''; });
watch(() => checkList.value?.text, (t) => { if (!textFocused.value) localText.value = t ?? ''; });

const debouncedUpdateCheckListText = useDebounceFn(
  (updatedAttrName: string, updatedAttrVal: string) => {
    if (!checkList.value || !canEdit.value) return;
    (async () => {
      await checkListsStore.update(checkList.value!.id, { [updatedAttrName]: updatedAttrVal });
    })();
  },
  500,
  { maxWait: 3000 }
);

watch(localName, (n) => debouncedUpdateCheckListText("name", n));
watch(localText, (t) => debouncedUpdateCheckListText("text", t));

</script>

<style scoped>
.checklist {
  text-align: left;
}

.viewport95 {
  max-height: 95vh;
}
.textareas-inherit-color :is(textarea) {
  color: inherit !important;
}
/* Edit-mode placeholders (title / notes / items) read as guidance, not content. */
:deep(textarea::placeholder) {
  color: var(--ui-text-dimmed);
  opacity: 1;
}

:deep(.ring-accented) {
  --tw-ring-color: currentColor !important;
}
:deep(.border-dashed) {
  border-color: currentColor !important;
}
</style>
