<script setup lang="ts">
import {
  AlertTriangle,
  History,
  LoaderCircle,
  Pencil,
  RefreshCw,
  Search,
  ShieldCheck,
  UserPlus,
  UserX,
  Users,
} from "@lucide/vue";
import { computed, onMounted, ref } from "vue";
import { toast } from "vue-sonner";

import BaseModal from "@/components/BaseModal.vue";
import {
  createUser,
  disableUser,
  fetchAuditLogs,
  fetchUsers,
  updateUser,
  resolveApiAssetUrl,
  type AuditLogItem,
  type AuthRole,
  type UserAccount,
} from "@/lib/api";

type Draft = {
  id?: string;
  username: string;
  name: string;
  password: string;
  role: AuthRole;
  enabled: boolean;
  protected?: boolean;
  originalRole?: AuthRole;
  originalEnabled?: boolean;
};

type ConfirmAction = {
  title: string;
  description: string;
  confirmText: string;
  tone: "danger" | "warning";
  run: () => Promise<void>;
};

const RECOVERY_ADMIN_ID = "local-admin";

const emptyDraft = (): Draft => ({
  username: "",
  name: "",
  password: "",
  role: "user",
  enabled: true,
});

const items = ref<UserAccount[]>([]);
const auditItems = ref<AuditLogItem[]>([]);
const query = ref("");
const loading = ref(true);
const saving = ref(false);
const confirming = ref(false);
const editing = ref<Draft | null>(null);
const confirmAction = ref<ConfirmAction | null>(null);

function userRolePriority(role: AuthRole | string | undefined) {
  return role === "admin" ? 0 : 1;
}

function sortUsersByRole(users: UserAccount[]) {
  return [...users].sort((a, b) => {
    return userRolePriority(a.role) - userRolePriority(b.role);
  });
}

const filtered = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  const sorted = sortUsersByRole(items.value);
  if (!keyword) return sorted;
  return sorted.filter((item) =>
    [item.username, item.name, item.role, item.id].some((value) =>
      String(value || "").toLowerCase().includes(keyword),
    ),
  );
});

const enabledAdminCount = computed(() =>
  items.value.filter((item) => item.role === "admin" && item.enabled).length,
);

const recentUserAudits = computed(() =>
  auditItems.value.filter((item) => item.target_type === "user").slice(0, 8),
);

const editingRoleLocked = computed(() => {
  const draft = editing.value;
  return Boolean(draft?.id && (isRecoveryDraft(draft) || isLastEnabledAdminDraft(draft)));
});

const editingEnabledLocked = computed(() => editingRoleLocked.value);

const editingLockMessage = computed(() => {
  const draft = editing.value;
  if (!draft?.id) return "";
  if (isRecoveryDraft(draft)) return "初始管理员作为恢复账号，不能降级或停用。";
  if (isLastEnabledAdminDraft(draft)) return "当前至少需要保留一个启用的管理员账号。";
  return "";
});

async function load() {
  loading.value = true;
  try {
    const [users, audits] = await Promise.all([
      fetchUsers(),
      fetchAuditLogs(50).catch(() => ({ items: [], total: 0 })),
    ]);
    items.value = users.items;
    auditItems.value = audits.items;
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "读取用户失败");
  } finally {
    loading.value = false;
  }
}

function edit(item?: UserAccount) {
  editing.value = item
    ? {
        id: item.id,
        username: item.username,
        name: item.name,
        password: "",
        role: item.role,
        enabled: item.enabled,
        protected: item.protected,
        originalRole: item.role,
        originalEnabled: item.enabled,
      }
    : emptyDraft();
}

function isRecoveryAdmin(item: UserAccount) {
  return Boolean(item.protected || item.id === RECOVERY_ADMIN_ID);
}

function isLastEnabledAdmin(item: UserAccount) {
  return item.role === "admin" && item.enabled && enabledAdminCount.value <= 1;
}

function isRecoveryDraft(draft: Draft) {
  return Boolean(draft.protected || draft.id === RECOVERY_ADMIN_ID);
}

function isLastEnabledAdminDraft(draft: Draft) {
  return Boolean(draft.id && draft.originalRole === "admin" && draft.originalEnabled && enabledAdminCount.value <= 1);
}

function disableLockReason(item: UserAccount) {
  if (isRecoveryAdmin(item)) return "初始管理员作为恢复账号，不能停用。";
  if (isLastEnabledAdmin(item)) return "至少需要保留一个启用的管理员账号。";
  return "";
}

function canDisable(item: UserAccount) {
  return item.enabled && !disableLockReason(item);
}

function roleLabel(role: AuthRole | string | undefined) {
  return role === "admin" ? "管理员" : "员工";
}

function enabledLabel(enabled: boolean | undefined) {
  return enabled ? "启用" : "停用";
}

function actionLabel(action: string) {
  const labels: Record<string, string> = {
    create_user: "新建成员",
    update_user: "编辑成员",
    disable_user: "停用成员",
  };
  return labels[action] || action;
}

function buildSaveConfirmation(draft: Draft): ConfirmAction | null {
  const changes: string[] = [];
  const original = draft.id ? items.value.find((item) => item.id === draft.id) : null;

  if (!draft.id && draft.role === "admin") {
    changes.push(`将新账号 ${draft.username} 创建为管理员`);
  }
  if (original && original.role !== draft.role) {
    changes.push(`角色从 ${roleLabel(original.role)} 改为 ${roleLabel(draft.role)}`);
  }
  if (original && original.enabled !== draft.enabled) {
    changes.push(`状态从 ${enabledLabel(original.enabled)} 改为 ${enabledLabel(draft.enabled)}`);
  }
  if (draft.password && draft.id) {
    changes.push("重置这个账号的登录密码");
  }

  if (!changes.length) return null;

  return {
    title: draft.id ? "确认修改成员" : "确认创建管理员",
    description: `${changes.join("，")}。保存后会写入权限操作记录。`,
    confirmText: "确认保存",
    tone: changes.some((item) => item.includes("停用") || item.includes("管理员")) ? "warning" : "danger",
    run: () => performSave(draft),
  };
}

async function save() {
  const draft = editing.value ? { ...editing.value } : null;
  if (!draft) return;
  if (!draft.username.trim() || (!draft.id && draft.password.length < 6)) {
    toast.error("请输入用户名，新用户密码至少 6 位");
    return;
  }
  if (editingRoleLocked.value && draft.role !== draft.originalRole) {
    toast.error(editingLockMessage.value || "这个账号的角色不能修改");
    return;
  }
  if (editingEnabledLocked.value && draft.enabled !== draft.originalEnabled) {
    toast.error(editingLockMessage.value || "这个账号的状态不能修改");
    return;
  }

  const confirmation = buildSaveConfirmation(draft);
  if (confirmation) {
    confirmAction.value = confirmation;
    return;
  }
  await performSave(draft);
}

async function performSave(draft: Draft) {
  saving.value = true;
  try {
    if (draft.id) {
      await updateUser(draft.id, {
        name: draft.name.trim(),
        role: draft.role,
        enabled: draft.enabled,
        ...(draft.password ? { password: draft.password } : {}),
      });
    } else {
      await createUser({
        username: draft.username.trim(),
        password: draft.password,
        name: draft.name.trim(),
        role: draft.role,
        enabled: draft.enabled,
      });
    }
    editing.value = null;
    await load();
    toast.success("用户已保存");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "保存用户失败");
  } finally {
    saving.value = false;
  }
}

function requestDisable(item: UserAccount) {
  const reason = disableLockReason(item);
  if (reason) {
    toast.error(reason);
    return;
  }
  confirmAction.value = {
    title: "确认停用成员",
    description: `停用 ${item.name || item.username} 后，该账号将不能继续登录。这个操作会写入权限操作记录。`,
    confirmText: "确认停用",
    tone: "danger",
    run: () => performDisable(item),
  };
}

async function performDisable(item: UserAccount) {
  saving.value = true;
  try {
    await disableUser(item.id);
    await load();
    toast.success("用户已停用");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "停用用户失败");
  } finally {
    saving.value = false;
  }
}

async function confirmPendingAction() {
  const action = confirmAction.value;
  if (!action) return;
  confirming.value = true;
  try {
    await action.run();
    confirmAction.value = null;
  } finally {
    confirming.value = false;
  }
}

function formatTime(value?: string) {
  if (!value) return "暂无";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

onMounted(load);
</script>

<template>
  <section class="min-h-[calc(100dvh_-_var(--studio-nav-height))] bg-[#F8FAFC] p-4 dark:bg-[#0f1115] sm:p-5">
    <div class="mx-auto flex max-w-[1680px] flex-col gap-5">
      <div class="studio-card bg-white px-5 py-5 dark:bg-[#171a21]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div class="inline-flex rounded-full bg-[#4F7CFF]/10 px-3 py-1 text-[13px] font-semibold text-[#4F7CFF]">Team Access</div>
            <h1 class="mt-3 text-[30px] font-semibold text-slate-950 dark:text-stone-50">成员权限</h1>
            <p class="mt-2 text-[15px] leading-7 text-slate-600 dark:text-stone-300">管理管理员与员工账号，控制登录、角色和账号状态。</p>
          </div>
          <div class="flex gap-2">
            <button type="button" class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl border border-black/[0.06] bg-white px-4 text-sm dark:border-white/10 dark:bg-white/[0.06]" @click="load">
              <RefreshCw class="size-4" :class="loading ? 'animate-spin' : ''" />
              刷新
            </button>
            <button type="button" class="studio-button inline-flex h-11 items-center gap-2 rounded-2xl bg-slate-950 px-4 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" @click="edit()">
              <UserPlus class="size-4" />
              新建成员
            </button>
          </div>
        </div>
        <div class="relative mt-5 max-w-[620px]">
          <Search class="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
          <input v-model="query" class="studio-input h-12 bg-[#F8FAFC] pl-11 pr-4 dark:bg-white/[0.04]" placeholder="搜索用户名、姓名、角色或 ID" />
        </div>
      </div>

      <div v-if="loading" class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="index in 6" :key="index" class="studio-skeleton h-[220px] rounded-[20px]" />
      </div>

      <div v-else-if="!filtered.length" class="studio-card grid min-h-[340px] place-items-center bg-white text-center dark:bg-[#171a21]">
        <div>
          <Users class="mx-auto size-9 text-slate-400" />
          <h2 class="mt-3 text-lg font-semibold">暂无成员</h2>
          <p class="mt-1 text-sm text-slate-500">创建成员后即可使用图片工作台。</p>
        </div>
      </div>

      <div v-else class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <article v-for="item in filtered" :key="item.id" class="studio-card bg-white p-5 dark:bg-[#171a21]">
          <div class="flex items-start justify-between gap-3">
            <div class="flex min-w-0 items-center gap-3">
              <span class="grid size-12 shrink-0 place-items-center overflow-hidden rounded-2xl bg-slate-950 text-lg font-semibold text-white dark:bg-white dark:text-slate-950">
                <img v-if="item.avatar_url" :src="resolveApiAssetUrl(item.avatar_url)" alt="" class="h-full w-full object-cover" loading="lazy" decoding="async" />
                <template v-else>{{ (item.name || item.username).slice(0, 1).toUpperCase() }}</template>
              </span>
              <div class="min-w-0">
                <h2 class="truncate text-lg font-semibold text-slate-950 dark:text-stone-50">{{ item.name || item.username }}</h2>
                <p class="mt-1 truncate text-xs text-slate-500">{{ item.username }} / {{ item.id }}</p>
              </div>
            </div>
            <span class="rounded-full px-2.5 py-1 text-[11px] font-semibold" :class="item.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300' : 'bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300'">
              {{ item.enabled ? '启用' : '停用' }}
            </span>
          </div>

          <div class="mt-4 flex flex-wrap gap-2">
            <span v-if="isRecoveryAdmin(item)" class="inline-flex h-7 items-center gap-1.5 rounded-full bg-[#4F7CFF]/10 px-2.5 text-xs font-semibold text-[#315BD7] dark:text-[#9FB4FF]">
              <ShieldCheck class="size-3.5" />
              恢复账号
            </span>
            <span v-else-if="isLastEnabledAdmin(item)" class="inline-flex h-7 items-center gap-1.5 rounded-full bg-amber-50 px-2.5 text-xs font-semibold text-amber-700 dark:bg-amber-400/10 dark:text-amber-300">
              <AlertTriangle class="size-3.5" />
              唯一管理员
            </span>
          </div>

          <div class="mt-4 grid grid-cols-2 gap-2 text-xs">
            <div class="rounded-2xl bg-[#F8FAFC] px-3 py-2 dark:bg-white/[0.04]">
              <span class="text-slate-500">角色</span>
              <strong class="mt-1 block">{{ roleLabel(item.role) }}</strong>
            </div>
            <div class="rounded-2xl bg-[#F8FAFC] px-3 py-2 dark:bg-white/[0.04]">
              <span class="text-slate-500">最近登录</span>
              <strong class="mt-1 block truncate">{{ formatTime(item.last_login_at) }}</strong>
            </div>
          </div>

          <div class="mt-4 flex gap-2">
            <button type="button" class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-black/[0.06] px-3 text-xs font-semibold dark:border-white/10" @click="edit(item)">
              <Pencil class="size-3.5" />
              编辑
            </button>
            <button
              v-if="item.enabled"
              type="button"
              class="studio-button inline-flex h-9 items-center gap-1.5 rounded-xl border border-rose-100 px-3 text-xs font-semibold text-rose-600 disabled:cursor-not-allowed disabled:opacity-45 dark:border-rose-400/20"
              :disabled="!canDisable(item)"
              :title="disableLockReason(item) || '停用账号'"
              @click="requestDisable(item)"
            >
              <UserX class="size-3.5" />
              停用
            </button>
          </div>
        </article>
      </div>

      <div class="studio-card bg-white p-5 dark:bg-[#171a21]">
        <div class="flex items-center justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold text-slate-950 dark:text-stone-50">最近权限操作</h2>
            <p class="mt-1 text-sm text-slate-500 dark:text-stone-400">记录成员创建、角色调整、停用和密码重置。</p>
          </div>
          <History class="size-5 text-slate-400" />
        </div>
        <div v-if="recentUserAudits.length" class="mt-4 grid gap-2">
          <div v-for="item in recentUserAudits" :key="item.id" class="flex flex-col gap-1 rounded-2xl bg-[#F8FAFC] px-3 py-2 text-sm dark:bg-white/[0.04] sm:flex-row sm:items-center sm:justify-between">
            <div class="min-w-0">
              <span class="font-semibold text-slate-900 dark:text-stone-100">{{ actionLabel(item.action) }}</span>
              <span class="ml-2 text-slate-600 dark:text-stone-300">{{ item.detail }}</span>
            </div>
            <span class="shrink-0 text-xs text-slate-500">{{ formatTime(item.created_at) }}</span>
          </div>
        </div>
        <p v-else class="mt-4 rounded-2xl bg-[#F8FAFC] px-3 py-3 text-sm text-slate-500 dark:bg-white/[0.04] dark:text-stone-400">暂无权限操作记录。</p>
      </div>
    </div>
  </section>

  <BaseModal :open="Boolean(editing)" :title="editing?.id ? '编辑成员' : '新建成员'" description="管理员可以访问监控与成员管理，员工使用创作与资产页面。" width-class="max-w-[600px]" @close="editing = null">
    <div v-if="editing" class="grid gap-4 p-5">
      <label class="grid gap-1.5 text-sm font-medium">
        用户名
        <input v-model="editing.username" class="studio-input h-11 px-3" :disabled="Boolean(editing.id)" />
      </label>
      <label class="grid gap-1.5 text-sm font-medium">
        姓名
        <input v-model="editing.name" class="studio-input h-11 px-3" />
      </label>
      <label class="grid gap-1.5 text-sm font-medium">
        密码
        <input v-model="editing.password" type="password" class="studio-input h-11 px-3" :placeholder="editing.id ? '留空则不修改密码' : '至少 6 位密码'" />
      </label>
      <div class="grid gap-4 sm:grid-cols-2">
        <label class="grid gap-1.5 text-sm font-medium">
          角色
          <select v-model="editing.role" class="studio-input h-11 px-3 disabled:cursor-not-allowed disabled:opacity-60" :disabled="editingRoleLocked">
            <option value="user">员工</option>
            <option value="admin">管理员</option>
          </select>
        </label>
        <label class="mt-6 inline-flex h-11 items-center gap-2 rounded-xl border border-black/[0.08] px-3 text-sm dark:border-white/10" :class="editingEnabledLocked ? 'cursor-not-allowed opacity-70' : ''">
          <input v-model="editing.enabled" type="checkbox" class="size-4 accent-[#4F7CFF]" :disabled="editingEnabledLocked" />
          启用账号
        </label>
      </div>
      <p v-if="editingLockMessage" class="rounded-2xl bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-400/10 dark:text-amber-200">{{ editingLockMessage }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" @click="editing = null">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2 text-sm font-semibold text-white dark:bg-white dark:text-slate-950" :disabled="saving" @click="save">
          <LoaderCircle v-if="saving" class="size-4 animate-spin" />
          保存
        </button>
      </div>
    </div>
  </BaseModal>

  <BaseModal :open="Boolean(confirmAction)" :title="confirmAction?.title || ''" width-class="max-w-[520px]" @close="confirmAction = null">
    <div class="grid gap-4 p-5">
      <div class="flex items-start gap-3 rounded-2xl px-3 py-3" :class="confirmAction?.tone === 'danger' ? 'bg-rose-50 text-rose-800 dark:bg-rose-400/10 dark:text-rose-200' : 'bg-amber-50 text-amber-800 dark:bg-amber-400/10 dark:text-amber-200'">
        <AlertTriangle class="mt-0.5 size-5 shrink-0" />
        <p class="text-sm leading-6">{{ confirmAction?.description }}</p>
      </div>
      <div class="flex justify-end gap-2">
        <button type="button" class="studio-button rounded-xl border border-black/[0.08] px-4 py-2 text-sm dark:border-white/10" @click="confirmAction = null">取消</button>
        <button type="button" class="studio-button inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white" :class="confirmAction?.tone === 'danger' ? 'bg-rose-600' : 'bg-slate-950 dark:bg-white dark:text-slate-950'" :disabled="confirming || saving" @click="confirmPendingAction">
          <LoaderCircle v-if="confirming || saving" class="size-4 animate-spin" />
          {{ confirmAction?.confirmText || '确认' }}
        </button>
      </div>
    </div>
  </BaseModal>
</template>
