<template>
    <UApp>
      <NuxtLayout>
        <NuxtPage />
      </NuxtLayout>
    </UApp>
</template>

<script setup>
import { useCheckapi } from "#open-fetch";
const checkApi = useCheckapi()
const runtimeConfig = useRuntimeConfig()
</script>