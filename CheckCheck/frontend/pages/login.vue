<template>
  <div class="flex justify-center items-center min-h-screen bg-gray-50 dark:bg-gray-900">
    <UCard class="w-full max-w-md space-y-6">
      <template #header>
        <h1 class="text-xl font-semibold text-center">Login</h1>
      </template>

      <UAlert
        v-if="errorMessage"
        color="red"
        icon="i-heroicons-exclamation-triangle"
        class="mb-4"
        :description="errorMessage"
        variant="subtle"
      />

      <div v-if="authSchemes.length === 0">
        <p class="text-center text-gray-500">Loading login methods...</p>
      </div>

      <div
        v-for="(method, index) in authSchemes"
        :key="index"
        class="space-y-4 border border-gray-200 dark:border-gray-800 rounded-lg p-4"
      >
        <h2 class="text-lg font-medium text-center">{{ method.display_name }}</h2>

        <!-- Basic Auth Form -->
        <form v-if="method.auth_type === 'basic'" @submit.prevent="() => basicLogin()" class="space-y-4">
          <UInput v-model="username" label="Username" required icon="i-lucide-user" class="w-full" size="xl" />
          <UInput
            v-model="password"
            label="Password"
            type="password"
            required
            icon="i-lucide-key-round"
            class="w-full"
            size="xl"
          />
          <UButton type="submit" block>Login</UButton>
          <UButton
            v-if="method.registration_endpoint"
            color="gray"
            variant="link"
            :to="method.registration_endpoint"
            block
          >
            Register
          </UButton>
        </form>

        <!-- OIDC Auth Button -->
        <div v-else-if="method.auth_type === 'oidc'" class="flex flex-col gap-2">
          <UButton @click="() => redirectToOIDC(method.login_endpoint)" block>
            Login with {{ method.display_name }}
          </UButton>
        </div>
      </div>
    </UCard>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter, useCookie } from "#imports";
import { useCheckapi } from "#imports";

const username = ref<string>("");
const password = ref<string>("");
const errorMessage = ref<string | null>(null);
const authSchemes = ref<AuthSchemeInfo[]>([]);
const router = useRouter();

onMounted(async () => {
  try {
    const { data } = await useCheckapi("/api/auth/list");
    authSchemes.value = data.value!;
  } catch (err) {
    console.error("Failed to fetch login methods:", err);
    error.value = "Failed to load login options.";
  }
});

const basicLogin = async () => {
  const { $checkapi } = useNuxtApp();
  const loginPayload: BasicLoginBody = {
    username: username.value,
    password: password.value,
  };

  try {
    const data = await $checkapi("/api/auth/basic/login/session", {
      method: "POST",
      body: loginPayload,
    });

    const redirectPath = (router.currentRoute.value.query.redirect as string) || "/";
    router.push(redirectPath);
  } catch (err: any) {
    // Handle 401 errors with detail field
    if (err?.response?.status === 401) {
      const detail = err.response._data?.detail || "Login failed";
      errorMessage.value = detail;
      return;
    }

    // Other unexpected errors
    console.error("Unexpected login error:", err);
    errorMessage.value = "An unexpected error occurred. Please try again.";
  }
};

const redirectToOIDC = (url: string) => {
  window.location.href = url;
};
</script>
