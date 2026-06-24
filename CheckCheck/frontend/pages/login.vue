<template>
  <div class="flex justify-center items-center min-h-screen bg-muted">
    <UCard class="w-full max-w-md space-y-6">
      <template #header>
        <h1 class="text-xl font-semibold text-center">Login</h1>
      </template>

      <UAlert
        v-if="errorMessage"
        data-testid="login-error"
        color="error"
        icon="i-lucide-triangle-alert"
        class="mb-4"
        :description="errorMessage"
        variant="subtle"
      />

      <div v-if="authSchemes.length === 0">
        <p class="text-center text-muted">Loading login methods...</p>
      </div>

      <div
        v-for="(method, index) in authSchemes"
        :key="index"
        class="space-y-4 border border-default rounded-lg p-4"
      >
        <h2 class="text-lg font-medium text-center">{{ method.display_name }}</h2>

        <!-- Basic Auth Form -->
        <form v-if="method.auth_type === 'basic'" @submit.prevent="() => basicLogin()" class="space-y-4">
          <UFormField label="Username">
            <UInput v-model="username" data-testid="login-username" required icon="i-lucide-user" class="w-full" size="xl" />
          </UFormField>
          <UFormField label="Password">
            <UInput
              v-model="password"
              data-testid="login-password"
              type="password"
              required
              icon="i-lucide-key-round"
              class="w-full"
              size="xl"
            />
          </UFormField>
          <UButton type="submit" block>Login</UButton>
          <UButton
            v-if="method.registration_endpoint"
            color="neutral"
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
    // useFetch resolves (it doesn't reject) on a failed request, surfacing the
    // failure via `error` — so check it explicitly rather than relying on catch.
    const { data, error } = await useCheckapi("/api/auth/list");
    if (error.value) throw error.value;
    authSchemes.value = data.value!;
  } catch (err) {
    console.error("Failed to fetch login methods:", err);
    errorMessage.value = "Failed to load login options.";
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
