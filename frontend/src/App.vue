<script setup lang="ts">
import { computed } from "vue";
import { RouterView, useRoute } from "vue-router";
import { Toaster } from "vue-sonner";

import AppShell from "@/components/AppShell.vue";

const route = useRoute();
const isAuth = computed(() => Boolean(route.meta.auth));
</script>

<template>
  <Toaster position="top-center" rich-colors :offset="48" />
  <main class="min-h-[100dvh] overflow-x-hidden bg-[#F8FAFC] text-slate-900 transition-colors duration-200 dark:bg-[#0f1115] dark:text-stone-100">
    <RouterView v-if="isAuth" />
    <AppShell v-else>
      <RouterView v-slot="{ Component, route: currentRoute }">
        <KeepAlive>
          <component :is="Component" v-if="currentRoute.meta.keepAlive" :key="currentRoute.name" />
        </KeepAlive>
        <component :is="Component" v-if="!currentRoute.meta.keepAlive" :key="currentRoute.fullPath" />
      </RouterView>
    </AppShell>
  </main>
</template>
