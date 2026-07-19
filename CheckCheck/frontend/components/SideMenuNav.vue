<template>
  <nav class="flex flex-col h-full overflow-hidden">
    <div class="flex flex-col gap-0.5 p-2 flex-1 overflow-y-auto">

      <!-- Home -->
      <UTooltip :text="'Home'" :disabled="!collapsed" side="right">
        <NuxtLink
          :to="{ path: '/', query: {} }"
          class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          :class="isHome ? 'bg-elevated font-medium' : 'text-muted'"
        >
          <span
            v-if="isHome"
            class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-primary"
          />
          <UIcon name="i-lucide-house" class="shrink-0 size-5" :class="{ 'text-primary': isHome }" />
          <span v-if="!collapsed" class="truncate flex-1 min-w-0">Home</span>
          <span v-if="!collapsed && countBadge(counts?.home)" data-testid="sidebar-count-home" class="shrink-0 text-xs tabular-nums text-muted">{{ countBadge(counts?.home) }}</span>
        </NuxtLink>
      </UTooltip>

      <!-- Shared section -->
      <div v-if="!collapsed" class="px-2 pt-4 pb-1">
        <span class="text-xs font-semibold text-muted uppercase tracking-wider">Shared</span>
      </div>
      <div v-else class="border-t my-2 mx-1" />

      <UTooltip
        v-for="opt in sharedOptions"
        :key="opt.value"
        :text="opt.label"
        :disabled="!collapsed"
        side="right"
      >
        <NuxtLink
          :data-testid="`shared-filter-${opt.value}`"
          :to="sharedLinkTo(opt.value)"
          class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          :class="route.query.shared === opt.value ? 'bg-elevated font-medium' : 'text-muted'"
        >
          <span
            v-if="route.query.shared === opt.value"
            class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-primary"
          />
          <UIcon
            :name="opt.icon"
            class="shrink-0 size-5"
            :class="{ 'text-primary': route.query.shared === opt.value }"
          />
          <span v-if="!collapsed" class="truncate flex-1 min-w-0">{{ opt.label }}</span>
          <span v-if="!collapsed && countBadge(sharedCount(opt.value))" :data-testid="`sidebar-count-shared-${opt.value}`" class="shrink-0 text-xs tabular-nums text-muted">{{ countBadge(sharedCount(opt.value)) }}</span>
        </NuxtLink>
      </UTooltip>

      <!-- Archive — soft-archived cards live here; the trash action in this view
           becomes a permanent delete (see Archive.vue). Clears other filters. -->
      <div class="border-t my-2 mx-1" />
      <UTooltip text="Archive" :disabled="!collapsed" side="right">
        <NuxtLink
          data-testid="sidebar-archive-filter"
          :to="{ path: '/', query: { archived: 'true' } }"
          class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          :class="isArchive ? 'bg-elevated font-medium' : 'text-muted'"
        >
          <span
            v-if="isArchive"
            class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full bg-primary"
          />
          <UIcon name="i-lucide-archive" class="shrink-0 size-5" :class="{ 'text-primary': isArchive }" />
          <span v-if="!collapsed" class="truncate flex-1 min-w-0">Archive</span>
          <span v-if="!collapsed && countBadge(counts?.archived)" data-testid="sidebar-count-archive" class="shrink-0 text-xs tabular-nums text-muted">{{ countBadge(counts?.archived) }}</span>
        </NuxtLink>
      </UTooltip>

      <!-- Labels section -->
      <template v-if="labelStore.labels.length">
        <div v-if="!collapsed" class="px-2 pt-4 pb-1">
          <span class="text-xs font-semibold text-muted uppercase tracking-wider">Labels</span>
        </div>
        <div v-else class="border-t my-2 mx-1" />

        <UTooltip
          v-for="label in labelStore.labels"
          :key="label.id"
          :text="label.display_name ?? ''"
          :disabled="!collapsed"
          side="right"
        >
          <NuxtLink
            :to="{ path: '/', query: { ...route.query, label: label.id } }"
            class="relative flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
            :class="route.query.label === label.id ? 'font-medium' : 'text-muted'"
            :style="route.query.label === label.id ? labelRowStyle(label) : undefined"
          >
            <span
              v-if="route.query.label === label.id"
              class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-full"
              :style="{ backgroundColor: labelColors(label).accent }"
            />
            <span class="shrink-0 size-4 rounded-full border" :style="labelDotStyle(label)" />
            <span v-if="!collapsed" class="truncate flex-1 min-w-0">{{ label.display_name }}</span>
            <span v-if="!collapsed && countBadge(counts?.labels?.[label.id])" :data-testid="`sidebar-count-label-${label.id}`" class="shrink-0 text-xs tabular-nums text-muted">{{ countBadge(counts?.labels?.[label.id]) }}</span>
          </NuxtLink>
        </UTooltip>
      </template>

    </div>

    <!-- Footer: Install app + Edit Labels -->
    <div class="p-2 border-t">
      <!-- Install app: only rendered when the browser offers a real PWA install
           (Chromium `beforeinstallprompt`). Hidden on iOS/DuckDuckGo/Firefox and
           when already installed. See composables/usePwaInstall.ts. -->
      <UTooltip v-if="canInstall" text="Install app" :disabled="!collapsed" side="right">
        <button
          type="button"
          data-testid="sidebar-install-app"
          class="w-full flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
          @click="promptInstall()"
        >
          <UIcon name="i-lucide-download" class="shrink-0 size-5" />
          <span v-if="!collapsed" class="truncate">Install app</span>
        </button>
      </UTooltip>

      <UTooltip text="Edit Labels" :disabled="!collapsed" side="right">
        <NuxtLink
          :to="{ path: '/', query: { ...route.query, editlabels: 'true' } }"
          class="flex items-center gap-3 px-2 py-1.5 rounded-lg text-sm transition-colors hover:bg-elevated text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-default"
        >
          <UIcon name="i-lucide-pencil" class="shrink-0 size-5" />
          <span v-if="!collapsed" class="truncate">Edit Labels</span>
        </NuxtLink>
      </UTooltip>

      <!-- Project stamp: running server version (GET /api/public-config), repo
           link, authorship and license. Hidden until the config resolves. The
           collapsed rail shows just the GitHub mark to keep the footer compact. -->
      <div v-if="!collapsed && serverVersion" class="px-2 pt-2 space-y-1.5">
        <div class="flex items-center gap-1.5 text-xs text-muted/70">
          <span
            data-testid="sidebar-server-version"
            class="tabular-nums"
            :title="`CheckCheck server ${serverVersion}`"
          >
            v{{ serverVersion }}
          </span>
          <span
            v-if="versionMismatch"
            data-testid="sidebar-client-version"
            class="tabular-nums rounded-full border border-warning/40 px-1.5 text-[10px] text-warning"
            :title="`This tab is running client ${clientVersion} while the server is ${serverVersion}. It updates automatically on the next reload.`"
          >
            client v{{ clientVersion }}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            by
            <a
              :href="authorUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
            >motey</a>
          </span>
        </div>
        <div class="flex items-center gap-2">
          <a
            :href="repoUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1 text-xs text-muted/70 hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
          >
            <UIcon name="i-lucide-github" class="size-3.5" />
            GitHub
          </a>
          <a
            :href="`${repoUrl}/blob/main/LICENSE`"
            target="_blank"
            rel="noopener noreferrer"
            title="MIT licensed — open source"
            class="inline-flex items-center gap-1 rounded-full border border-default px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted/70 hover:text-primary hover:border-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <UIcon name="i-lucide-scale" class="size-3" />
            MIT
          </a>
        </div>
      </div>
      <div v-else-if="collapsed && serverVersion" class="pt-2 flex justify-center">
        <UTooltip :text="`CheckCheck v${serverVersion} · MIT · by motey`" side="right">
          <a
            :href="repoUrl"
            target="_blank"
            rel="noopener noreferrer"
            data-testid="sidebar-server-version"
            :title="`CheckCheck server ${serverVersion}`"
            class="inline-flex text-muted/70 hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
          >
            <UIcon name="i-lucide-github" class="size-5" />
          </a>
        </UTooltip>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { useCheckListsLabelStore } from "@/stores/label";
import { useCheckListsColorSchemeStore } from "@/stores/color";
import { useCheckListsStore } from "@/stores/checklist";
import { usePublicConfigStore } from "@/stores/publicConfig";

const props = defineProps<{ collapsed?: boolean }>();

const route = useRoute();
const colorMode = useColorMode();
const labelStore = useCheckListsLabelStore();
const colorStore = useCheckListsColorSchemeStore();
const checkListStore = useCheckListsStore();
const publicConfig = usePublicConfigStore();

// PWA install affordance — surfaces a native "Install app" prompt on Chromium
// browsers (hidden everywhere the browser can't install; see usePwaInstall).
const { canInstall, promptInstall } = usePwaInstall();

// Running server version, shown in the sidebar footer (null until config loads).
const serverVersion = computed(() => publicConfig.serverVersion);

// The client *bundle* version, baked in at build time (runtimeConfig.public,
// from the APP_VERSION Docker build-arg). Empty in dev / a plain build. Unlike
// serverVersion (a live API value), this is the JS actually executing, so when
// it differs from the server the running bundle is stale (a new version is
// deploying / the SW hasn't reloaded yet). autoUpdate reloads it automatically;
// this just makes the state visible instead of guessable.
const clientVersion = computed(() => useRuntimeConfig().public.clientVersion || "");
const versionMismatch = computed(
  () => !!clientVersion.value && !!serverVersion.value && clientVersion.value !== serverVersion.value,
);

// Project stamp links (footer). Static — the repo/author are compile-time facts.
const repoUrl = "https://github.com/motey/checkcheck";
const authorUrl = "https://github.com/motey";

// Sidebar count badges (fetched once on mount, kept fresh via useSync). null
// until the first fetch resolves — badges just stay hidden until then.
const counts = computed(() => checkListStore.counts);

// Render a badge only for positive counts (hide 0 / undefined to avoid clutter);
// cap the label at 99+ so a large archive doesn't blow out the rail width.
function countBadge(n: number | null | undefined): string | null {
  if (!n || n <= 0) return null;
  return n > 99 ? "99+" : String(n);
}

function sharedCount(value: string): number | undefined {
  if (value === "with_me") return counts.value?.shared_with_me;
  if (value === "by_me") return counts.value?.shared_by_me;
  return undefined;
}

const isHome = computed(
  () =>
    route.path === "/" &&
    !route.query.label &&
    !route.query.editlabels &&
    !route.query.search &&
    !route.query.shared &&
    !route.query.archived
);

const isArchive = computed(() => route.query.archived === "true");

// Mutually-exclusive share filters (a card is either owned by you or not, so it
// can never be both). Driven by ?shared=with_me|by_me, ANDing with any label.
const sharedOptions = [
  { value: "with_me", label: "Shared with me", icon: "i-lucide-users" },
  { value: "by_me", label: "Shared by me", icon: "i-lucide-share-2" },
] as const;

// Toggle: clicking the active filter clears it; otherwise activate it (keeping
// the rest of the query, e.g. an active label, so the filters add up).
function sharedLinkTo(value: string) {
  const query = { ...route.query };
  if (query.shared === value) {
    delete query.shared;
  } else {
    query.shared = value;
  }
  return { path: "/", query };
}

// Resolve a label's background + accent hexes for the current color mode, with a
// neutral fallback for labels that have no color assigned.
function labelColors(label: LabelType) {
  const color = colorStore.colors.find((c) => c.id === label.color_id);
  const dark = colorMode.value === "dark";
  if (!color) {
    return {
      bg: dark ? "#555" : "#ddd",
      accent: dark ? "#666" : "#ccc",
    };
  }
  return {
    bg: dark ? color.backgroundcolor_dark_hex : color.backgroundcolor_light_hex,
    accent: dark ? color.accentcolor_dark_hex : color.accentcolor_light_hex,
  };
}

function labelDotStyle(label: LabelType) {
  const { bg, accent } = labelColors(label);
  return { backgroundColor: bg, borderColor: accent };
}

// When a label filter is active, tint the whole row with that label's own color
// so the active cue matches the card colors on the board.
function labelRowStyle(label: LabelType) {
  return { backgroundColor: labelColors(label).bg };
}
</script>
