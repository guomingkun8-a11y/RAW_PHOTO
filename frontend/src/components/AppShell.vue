<script setup lang="ts">
import {
  Activity,
  Bell,
  Clock3,
  ImageIcon,
  Library,
  LogOut,
  Menu,
  Moon,
  Search,
  Sparkles,
  Sun,
  UserRound,
  Users,
  WandSparkles,
  X,
  Zap,
} from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { logout as logoutApi } from "@/lib/api";
import { clearStoredAuthSession } from "@/stores/auth";
import { listImageConversations } from "@/stores/image-conversations";
import { sessionState, setSession } from "@/stores/session";

const route = useRoute();
const router = useRouter();
const mobileOpen = ref(false);
const searchValue = ref("");
const taskCount = ref(0);
const showNotifications = ref(false);
const showUserMenu = ref(false);
const isDark = ref(document.documentElement.classList.contains("dark"));
let taskTimer = 0;

const navItems = [
  { href: "/image", label: "图片生成", detail: "AI 创作台", icon: WandSparkles },
  { href: "/prompt-templates", label: "模板中心", detail: "提示词资产", icon: Sparkles },
  { href: "/image-library", label: "历史图库", detail: "瀑布流资产", icon: Library },
  { href: "/monitoring", label: "监控看板", detail: "运行状态", icon: Activity, adminOnly: true },
  { href: "/users", label: "成员权限", detail: "团队管理", icon: Users, adminOnly: true },
];

const visibleNavItems = computed(() => navItems.filter((item) => !item.adminOnly || sessionState.session?.role === "admin"));
const userInitial = computed(() => {
  const source = sessionState.session?.name || sessionState.session?.username || "U";
  return source.trim().slice(0, 1).toUpperCase();
});

function isActive(href: string) {
  return route.path === href || route.path.startsWith(`${href}/`);
}

async function loadTaskCount() {
  try {
    taskCount.value = (await listImageConversations()).length;
  } catch {
    taskCount.value = 0;
  }
}

function toggleTheme() {
  isDark.value = !isDark.value;
  document.documentElement.classList.toggle("dark", isDark.value);
  localStorage.setItem("gmkraw-theme", isDark.value ? "dark" : "light");
}

async function submitSearch() {
  const query = searchValue.value.trim();
  mobileOpen.value = false;
  if (route.path === "/image-library") {
    await router.replace({ path: "/image-library", query: query ? { search: query } : {} });
    window.dispatchEvent(new CustomEvent("image-library-search", { detail: { query } }));
    return;
  }
  await router.push({ path: "/image-library", query: query ? { search: query } : {} });
}

async function handleLogout() {
  try {
    await logoutApi();
  } catch {
    // Local logout still clears an expired session.
  }
  await clearStoredAuthSession();
  setSession(null);
  await router.replace("/login");
}

watch(() => route.fullPath, () => {
  mobileOpen.value = false;
  showNotifications.value = false;
  showUserMenu.value = false;
  void loadTaskCount();
});

onMounted(() => {
  void loadTaskCount();
  taskTimer = window.setInterval(() => void loadTaskCount(), 15000);
});

onBeforeUnmount(() => window.clearInterval(taskTimer));
</script>

<template>
  <div class="min-h-[100dvh] bg-[#F8FAFC] dark:bg-[#0f1115]">
    <header class="sticky top-0 z-40 flex h-[var(--studio-nav-height)] items-center border-b border-black/[0.06] bg-[#F8FAFC]/88 px-4 backdrop-blur-2xl dark:border-white/10 dark:bg-[#0f1115]/84 sm:px-5">
      <div class="mx-auto grid h-14 w-full max-w-[1680px] grid-cols-[auto_1fr_auto] items-center gap-3">
        <div class="flex min-w-0 items-center gap-3">
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-700 shadow-sm dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200 lg:hidden" aria-label="打开导航" @click="mobileOpen = !mobileOpen">
            <X v-if="mobileOpen" class="size-5" />
            <Menu v-else class="size-5" />
          </button>
          <RouterLink to="/image" class="group flex min-w-0 items-center gap-3 rounded-2xl pr-2 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[#4F7CFF]/20" aria-label="AI Image Studio">
            <span class="relative flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-slate-950 text-white shadow-[0_16px_36px_rgba(17,24,39,0.18)] dark:bg-white dark:text-slate-950">
              <img src="/jiakemei-mark.svg" alt="" class="size-8 rounded-xl" />
            </span>
            <span class="hidden min-w-0 flex-col leading-none sm:flex">
              <span class="truncate text-[16px] font-semibold text-slate-950 dark:text-stone-50">AI Image Studio</span>
              <span class="mt-1 truncate text-[12px] font-medium text-slate-500 dark:text-stone-400">AI Creative Workspace</span>
            </span>
          </RouterLink>
        </div>

        <form class="relative mx-auto hidden h-12 w-full max-w-[560px] items-center rounded-2xl border border-black/[0.06] bg-white px-4 text-slate-700 shadow-[0_12px_30px_rgba(15,23,42,0.05)] focus-within:border-[#4F7CFF]/35 focus-within:ring-[4px] focus-within:ring-[#4F7CFF]/10 dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-200 md:flex" @submit.prevent="submitSearch">
          <Search class="pointer-events-none size-4 text-slate-400" />
          <input v-model="searchValue" placeholder="搜索历史、模板、图片" class="h-full min-w-0 flex-1 bg-transparent px-3 text-[14px] outline-none placeholder:text-slate-500" />
          <span class="rounded-lg border border-black/[0.06] bg-slate-50 px-2 py-1 text-[11px] font-medium text-slate-400 dark:border-white/10 dark:bg-white/[0.06]">⌘ K</span>
        </form>

        <div class="relative flex min-w-0 items-center justify-end gap-2">
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-600 shadow-sm dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300 md:hidden" aria-label="全局搜索" @click="submitSearch">
            <Search class="size-4" />
          </button>
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-600 shadow-sm dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300" aria-label="通知" @click="showNotifications = !showNotifications; showUserMenu = false">
            <Bell class="size-4" />
          </button>
          <div v-if="showNotifications" class="absolute right-24 top-12 z-50 w-[300px] rounded-2xl border border-black/[0.06] bg-white p-2 shadow-[0_24px_70px_rgba(15,23,42,0.16)] dark:border-white/10 dark:bg-[#171a21]">
            <div class="rounded-2xl bg-slate-50 px-4 py-3 dark:bg-white/[0.06]">
              <div class="flex items-center gap-2 text-sm font-semibold text-slate-950 dark:text-stone-50"><span class="size-2 rounded-full bg-[#16C784]" />GPU 与 API 正常</div>
              <p class="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">图片任务会自动同步到历史图库，可随时收藏、下载或重新生成。</p>
            </div>
          </div>
          <RouterLink to="/image-library" class="studio-button hidden h-10 items-center gap-2 rounded-2xl bg-slate-950 px-3 text-[13px] text-white shadow-[0_14px_32px_rgba(17,24,39,0.14)] dark:bg-white dark:text-slate-950 sm:inline-flex">
            <Clock3 class="size-4" />最近任务 {{ taskCount }}
          </RouterLink>
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-2xl border border-black/[0.06] bg-white text-slate-600 shadow-sm dark:border-white/10 dark:bg-white/[0.06] dark:text-stone-300" aria-label="切换主题" @click="toggleTheme">
            <Sun v-if="isDark" class="size-4" />
            <Moon v-else class="size-4" />
          </button>
          <button type="button" class="studio-button inline-flex size-10 items-center justify-center rounded-2xl bg-gradient-to-br from-slate-950 to-slate-800 text-sm font-semibold text-white shadow-[0_16px_36px_rgba(17,24,39,0.18)] dark:from-white dark:to-stone-100 dark:text-slate-950" aria-label="用户菜单" @click="showUserMenu = !showUserMenu; showNotifications = false">
            {{ sessionState.session ? userInitial : '' }}<UserRound v-if="!sessionState.session" class="size-4" />
          </button>
          <div v-if="showUserMenu" class="absolute right-0 top-12 z-50 w-[270px] rounded-2xl border border-black/[0.06] bg-white p-2 shadow-[0_24px_70px_rgba(15,23,42,0.16)] dark:border-white/10 dark:bg-[#171a21]">
            <div class="px-3 py-3">
              <div class="truncate text-sm font-semibold text-slate-950 dark:text-stone-50">{{ sessionState.session?.name || sessionState.session?.username || '用户' }}</div>
              <div class="mt-1 text-xs text-slate-500 dark:text-stone-400">{{ sessionState.session?.role === 'admin' ? '管理员' : '成员' }}</div>
            </div>
            <div class="my-1 h-px bg-slate-200 dark:bg-white/10" />
            <RouterLink to="/image" class="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-slate-700 hover:bg-slate-100 dark:text-stone-200 dark:hover:bg-white/[0.08]"><ImageIcon class="size-4" />创作工作台</RouterLink>
            <button type="button" class="mt-1 flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-950/30" @click="handleLogout"><LogOut class="size-4" />退出登录</button>
          </div>
        </div>
      </div>
    </header>

    <aside class="fixed bottom-5 left-[var(--studio-sidebar-left)] top-[96px] z-20 hidden w-[var(--studio-sidebar-width)] flex-col rounded-[20px] border border-black/[0.06] bg-white/82 p-3 shadow-[0_12px_28px_rgba(15,23,42,0.07)] backdrop-blur-2xl dark:border-white/10 dark:bg-white/[0.055] lg:flex">
      <div class="mb-3 rounded-2xl bg-slate-950 p-3 text-white dark:bg-white dark:text-slate-950">
        <div class="flex items-center gap-2 text-sm font-semibold"><Zap class="size-4 text-[#4F7CFF]" />Creative OS</div>
        <div class="mt-2 text-xs leading-5 text-white/70 dark:text-slate-500">为电商设计师准备的每日 AI 工作台。</div>
      </div>
      <nav class="flex flex-col gap-2">
        <RouterLink v-for="item in visibleNavItems" :key="item.href" :to="item.href" class="group relative flex min-h-[58px] items-center gap-3 rounded-2xl border px-3.5 py-3 text-left transition" :class="isActive(item.href) ? 'border-[#4F7CFF]/20 bg-[#4F7CFF]/10 text-slate-950 shadow-[0_8px_18px_rgba(79,124,255,0.12)] dark:text-white' : 'border-transparent text-slate-600 hover:border-black/[0.04] hover:bg-[#4F7CFF]/[0.08] hover:text-slate-950 dark:text-stone-300 dark:hover:border-white/10 dark:hover:text-white'">
          <span class="flex size-10 shrink-0 items-center justify-center rounded-xl" :class="isActive(item.href) ? 'bg-[#4F7CFF] text-white' : 'bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-stone-300'"><component :is="item.icon" class="size-4" /></span>
          <span class="min-w-0"><span class="block truncate text-[15px] font-semibold">{{ item.label }}</span><span class="mt-0.5 block truncate text-[12px] text-slate-500 dark:text-stone-400">{{ item.detail }}</span></span>
        </RouterLink>
      </nav>
    </aside>

    <div v-if="mobileOpen" class="fixed inset-x-3 top-[86px] z-50 rounded-[20px] border border-black/[0.06] bg-white p-3 shadow-[0_24px_70px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[#171a21] lg:hidden">
      <nav class="flex flex-col gap-2">
        <RouterLink v-for="item in visibleNavItems" :key="item.href" :to="item.href" class="flex items-center gap-3 rounded-2xl px-3.5 py-3 text-slate-700 hover:bg-[#4F7CFF]/10 dark:text-stone-200"><component :is="item.icon" class="size-4" />{{ item.label }}</RouterLink>
      </nav>
    </div>

    <div class="relative z-10 min-h-[calc(100dvh_-_var(--studio-nav-height))] lg:pl-[var(--studio-content-left)]"><slot /></div>
  </div>
</template>
