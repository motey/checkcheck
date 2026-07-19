<template>
  <!-- Static cheat-sheet for the Markdown subset supported in card notes. Shown
       from the "Formatting help" hint under the notes field. Presentational only,
       so it can be reused if another surface ever needs it. -->
  <div class="p-3 max-w-xs text-sm" data-testid="markdown-help">
    <p class="text-muted mb-2">
      Markdown is a simple way to add formatting using plain text symbols.
    </p>
    <table class="w-full border-collapse">
      <thead>
        <tr class="text-dimmed text-xs">
          <th class="text-left font-medium pb-1">You type</th>
          <th class="text-left font-medium pb-1">You get</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.type">
          <td class="py-0.5 pr-3 align-top">
            <code class="text-xs">{{ row.type }}</code>
          </td>
          <td class="py-0.5 align-top md-notes" v-html="renderMarkdown(row.type)" />
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { renderMarkdown } from "@/utils/markdown";

const rows = [
  { type: "**bold**" },
  { type: "*italic*" },
  { type: "~~strike~~" },
  { type: "`code`" },
  { type: "- list" },
  { type: "1. list" },
  { type: "[link](https://...)" },
  { type: "> quote" },
  { type: "# heading" },
];
</script>
