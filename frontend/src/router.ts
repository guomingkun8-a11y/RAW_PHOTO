import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

import { getDefaultRouteForRole } from "@/stores/auth";
import { sessionState, validateSession } from "@/stores/session";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/image" },
  { path: "/login", name: "login", component: () => import("@/pages/AuthPage.vue"), meta: { public: true, auth: true } },
  { path: "/register", name: "register", component: () => import("@/pages/AuthPage.vue"), meta: { public: true, auth: true } },
  { path: "/image", name: "image", component: () => import("@/pages/ImageWorkspacePage.vue") },
  { path: "/image-library", name: "image-library", component: () => import("@/pages/ImageLibraryPage.vue") },
  { path: "/products", redirect: "/image" },
  { path: "/prompt-templates", name: "prompt-templates", component: () => import("@/pages/PromptTemplatesPage.vue") },
  { path: "/monitoring", name: "monitoring", component: () => import("@/pages/MonitoringPage.vue"), meta: { role: "admin" } },
  { path: "/users", name: "users", component: () => import("@/pages/UsersPage.vue"), meta: { role: "admin" } },
  { path: "/:pathMatch(.*)*", redirect: "/image" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
  const session = await validateSession(!sessionState.ready);
  if (to.meta.public) {
    if ((to.name === "login" || to.name === "register") && session) {
      const next = typeof to.query.next === "string" ? to.query.next : getDefaultRouteForRole(session.role);
      return next.startsWith("/") ? next : getDefaultRouteForRole(session.role);
    }
    return true;
  }
  if (!session) {
    return { name: "login", query: { next: to.fullPath } };
  }
  if (to.meta.role && to.meta.role !== session.role) return "/image";
  return true;
});

window.addEventListener("auth-unauthorized", () => {
  sessionState.session = null;
  sessionState.ready = true;
  if (router.currentRoute.value.name !== "login") {
    void router.replace({ name: "login", query: { next: router.currentRoute.value.fullPath } });
  }
});
