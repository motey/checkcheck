<template>
  <UModal
    v-model:open="open"
    title="Opened Checklist Editor"
    :fullscreen="isMobile"
    :ui="{ content: isMobile ? MOBILE_CONTENT_CLASS : DESKTOP_CONTENT_CLASS }"
  >
    <template #content>
      <div
        :class="isMobile ? 'h-full' : 'max-h-[92dvh]'"
        class="relative flex flex-col overflow-hidden overscroll-contain"
      >
        <!-- Explicit close affordance (the editor otherwise fills the modal).
             On phones the editor's own header bar carries a back arrow instead. -->
        <UButton
          v-if="!isMobile"
          icon="i-lucide-x"
          color="neutral"
          variant="ghost"
          size="sm"
          aria-label="Close"
          class="absolute top-2 right-2 z-10 bg-default/60 backdrop-blur-sm rounded-full"
          @click="open = false"
        />
        <!-- CheckList has a top-level await (loads the card + its items on
             mount), so it needs its own Suspense boundary. Keyed by id so
             reopening a different card always remounts with fresh data. -->
        <Suspense>
          <CheckList :key="checkListId" :checkListId="checkListId" :editModeActive="true" :fullscreen="isMobile">
            <template #header-start>
              <UButton
                icon="i-lucide-arrow-left"
                color="neutral"
                variant="ghost"
                aria-label="Close"
                data-testid="editor-back"
                class="size-11 justify-center rounded-full"
                :ui="{ leadingIcon: 'size-6' }"
                :style="{ color: 'inherit' }"
                @click="open = false"
              />
            </template>
          </CheckList>
          <template #fallback>
            <div class="flex items-center justify-center min-h-48 p-10 text-muted">
              <UIcon name="i-lucide-loader-circle" class="size-6 animate-spin" />
            </div>
          </template>
        </Suspense>
      </div>
    </template>
  </UModal>
</template>

<script setup lang="ts">
import { useMediaQuery } from "@vueuse/core";

const open = defineModel<boolean>("open", { default: false });

// Mirror the visual viewport into --vv-height/--vv-top while the editor is
// open, so it can size itself against the on-screen keyboard (mobile editor
// plan, M1). The modal stays mounted after closing, hence the getter.
useVisualViewport(() => open.value);

// Below `sm` the editor is a full-screen page with a header bar (mobile editor
// plan, M2). It is pinned to the visual viewport, so with the keyboard open it
// ends at the keyboard edge instead of reaching behind it. The variables are
// removed when nothing holds them, hence the fallbacks.
const isMobile = useMediaQuery("(max-width: 639px)");
const DESKTOP_CONTENT_CLASS =
  "max-w-2xl w-[calc(100vw-1rem)] sm:w-full max-h-[92dvh] rounded-2xl ring ring-default overflow-hidden";
const MOBILE_CONTENT_CLASS =
  "top-[var(--vv-top,0px)] bottom-auto h-[var(--vv-height,100dvh)] w-full max-w-none rounded-none overflow-hidden";

defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
</script>
