<template>
  <!-- F3 placeholder: anonymous public-URL link management lands in the next
       phase (create/list/revoke links with optional password + expiry). The
       section is feature-gated on `sharing_public_links_enabled` by the parent,
       so it only renders when the server allows public links at all. -->
  <section class="flex flex-col gap-1 border-t border-[var(--ui-border)] pt-4">
    <h3 class="text-sm font-semibold">Public links</h3>
    <p class="text-xs text-[var(--ui-text-muted)] italic">
      Anonymous share links are coming soon.
    </p>
  </section>
</template>

<script setup lang="ts">
// Stub — see CARD_SHARING_PLAN_FRONTEND.md Phase F3. Kept as its own component
// so wiring it up later is a drop-in.
defineProps({
  checkListId: {
    type: String,
    required: true,
  },
});
</script>

<style scoped></style>
