<script setup lang="ts">
import {
  AlertTriangle,
  BellRing,
  CheckCircle2,
  Clock3,
  LoaderCircle,
  Play,
  Plus,
  Save,
  Send,
  Trash2,
} from "@lucide/vue";
import { computed, onMounted, reactive, ref } from "vue";
import { toast } from "vue-sonner";

import { useVisibilityAwareInterval } from "@/composables/useVisibilityAwareInterval";
import BaseModal from "@/components/BaseModal.vue";
import {
  addAlertWebhook,
  deleteAlertWebhook,
  fetchAlertIncidents,
  fetchAlertStatus,
  runAlertCheck,
  sendAlertTest,
  updateAlertWebhook,
  updateAlertSettings,
  type AlertIncident,
  type AlertMonitorStatus,
  type AlertSettings,
  type AlertWebhook,
} from "@/features/alerts/alert-api";

const DEFAULTS: AlertSettings = {
  enabled: false,
  environment: "",
  webhook_configured: false,
  check_interval_secs: 30,
  cooldown_secs: 600,
  repeat_secs: 1800,
  queue_waiting_threshold: 20,
  dead_letter_threshold: 1,
  stale_task_threshold: 1,
  worker_missing_alert: true,
  recovery_notifications: true,
};

const draft = reactive<AlertSettings>({ ...DEFAULTS });
const monitor = ref<AlertMonitorStatus | null>(null);
const webhooks = ref<AlertWebhook[]>([]);
const incidents = ref<AlertIncident[]>([]);
const openCount = ref(0);
const loading = ref(true);
const saving = ref(false);
const testingId = ref("");
const updatingId = ref("");
const checking = ref(false);
const adding = ref(false);
const deleting = ref(false);
const deleteTarget = ref<AlertWebhook | null>(null);
const webhookName = ref("");
const webhookUrl = ref("");
const loadError = ref("");
const savedSettings = ref<AlertSettings>({ ...DEFAULTS });
const { restart } = useVisibilityAwareInterval(() => load(true), 30_000);
const hasChanges = computed(() => JSON.stringify(draft) !== JSON.stringify(savedSettings.value));

const statusView = computed(() => {
  if (!draft.enabled) return { label: "已暂停", tone: "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-stone-300" };
  const status = monitor.value?.last_check_status;
  if (status === "healthy") return { label: "运行正常", tone: "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300" };
  if (status === "unhealthy") return { label: `${openCount.value} 个异常`, tone: "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300" };
  if (status === "error" || status === "unavailable") return { label: "巡检异常", tone: "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300" };
  return { label: "等待首次巡检", tone: "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300" };
});

function applySettings(value: AlertSettings) {
  Object.assign(draft, DEFAULTS, value);
  savedSettings.value = { ...draft };
}

function formatTime(value?: string) {
  if (!value) return "尚未执行";
  const parsed = new Date(value.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function severityTone(severity: string) {
  if (severity === "P0" || severity === "P1") return "bg-rose-50 text-rose-700 dark:bg-rose-400/10 dark:text-rose-300";
  return "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300";
}

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    const [status, history] = await Promise.all([fetchAlertStatus(), fetchAlertIncidents(8)]);
    if (!silent || !hasChanges.value) applySettings(status.settings);
    monitor.value = status.monitor;
    webhooks.value = status.webhooks || [];
    incidents.value = history.items;
    openCount.value = history.open_count;
    loadError.value = "";
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "读取告警设置失败";
    if (!silent) toast.error(loadError.value);
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  try {
    const result = await updateAlertSettings({
      enabled: draft.enabled,
      check_interval_secs: Number(draft.check_interval_secs),
      cooldown_secs: Number(draft.cooldown_secs),
      repeat_secs: Number(draft.repeat_secs),
      queue_waiting_threshold: Number(draft.queue_waiting_threshold),
      dead_letter_threshold: Number(draft.dead_letter_threshold),
      stale_task_threshold: Number(draft.stale_task_threshold),
      worker_missing_alert: draft.worker_missing_alert,
      recovery_notifications: draft.recovery_notifications,
    });
    applySettings(result.settings);
    monitor.value = result.monitor;
    webhooks.value = result.webhooks || webhooks.value;
    toast.success("告警设置已保存");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

async function addWebhook() {
  adding.value = true;
  try {
    await addAlertWebhook({ name: webhookName.value.trim(), url: webhookUrl.value.trim() });
    webhookName.value = "";
    webhookUrl.value = "";
    await load(true);
    toast.success("机器人已添加");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "添加失败");
  } finally {
    adding.value = false;
  }
}

async function toggleWebhook(item: AlertWebhook) {
  try {
    await updateAlertWebhook(item.id, { enabled: !item.enabled });
    await load(false);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "修改失败");
  }
}

async function setPurpose(item: AlertWebhook, purpose: "receive_alerts" | "receive_feedback", value: boolean) {
  updatingId.value = item.id;
  try {
    await updateAlertWebhook(item.id, { [purpose]: value });
    await load(true);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "修改推送用途失败");
  } finally {
    updatingId.value = "";
  }
}

async function sendTest(item: AlertWebhook) {
  testingId.value = item.id;
  try {
    await sendAlertTest(item.id);
    toast.success(`测试消息已发送至${item.name}`);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "测试消息发送失败");
  } finally {
    testingId.value = "";
  }
}

async function removeWebhook() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await deleteAlertWebhook(deleteTarget.value.id);
    deleteTarget.value = null;
    await load(false);
    toast.success("机器人已删除");
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "删除失败");
  } finally {
    deleting.value = false;
  }
}

async function checkNow() {
  checking.value = true;
  try {
    const result = await runAlertCheck();
    if (result.status === "paused") toast.info("请先启用自动告警");
    else toast.success(result.findings ? `巡检完成，发现 ${result.findings} 个异常` : "巡检完成，系统运行正常");
    await load(true);
  } catch (error) {
    toast.error(error instanceof Error ? error.message : "巡检失败");
  } finally {
    checking.value = false;
  }
}

onMounted(() => {
  void load();
  restart(false);
});
</script>

<template>
  <section aria-labelledby="alert-settings-title">
    <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
      <div class="flex min-w-0 items-start gap-3">
        <span class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-[#4F7CFF]/10 text-[#315be8] dark:text-[#8FA9FF]">
          <BellRing class="size-5" />
        </span>
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h2 id="alert-settings-title" class="text-[20px] font-semibold text-slate-950 dark:text-stone-50">企业微信告警</h2>
            <span class="rounded-lg px-2.5 py-1 text-[11px] font-semibold" :class="statusView.tone">{{ statusView.label }}</span>
          </div>
          <p class="mt-1 text-[13px] leading-5 text-slate-500 dark:text-stone-400">自动巡检数据库、Redis、Worker 和生成队列。</p>
        </div>
      </div>

      <div class="flex flex-wrap gap-2">
        <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl border border-black/[0.08] px-3 text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10" :disabled="checking || !draft.enabled" @click="checkNow">
          <LoaderCircle v-if="checking" class="size-4 animate-spin" />
          <Play v-else class="size-4" />
          立即巡检
        </button>
        <button type="button" class="studio-button inline-flex h-10 items-center gap-2 rounded-xl bg-slate-950 px-4 text-[13px] font-semibold text-white disabled:cursor-wait disabled:opacity-60 dark:bg-white dark:text-slate-950" :disabled="saving || loading" @click="save">
          <LoaderCircle v-if="saving" class="size-4 animate-spin" />
          <Save v-else class="size-4" />
          保存设置
        </button>
      </div>
    </div>

    <div class="mt-6 border-t border-black/[0.06] pt-5 dark:border-white/10">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 class="text-[15px] font-semibold text-slate-900 dark:text-stone-100">企业微信机器人</h3>
          <p class="mt-1 text-[12px] text-slate-500 dark:text-stone-400">分别指定接收监控告警、反馈消息的机器人。</p>
        </div>
        <span class="text-[12px] text-slate-500">已启用 {{ webhooks.filter((item) => item.enabled).length }} 个</span>
      </div>
      <div v-if="webhooks.length" class="mt-3 divide-y divide-black/[0.06] border-y border-black/[0.06] dark:divide-white/10 dark:border-white/10">
        <div v-for="item in webhooks" :key="item.id" class="flex flex-wrap items-center gap-3 py-3">
          <span class="size-2 shrink-0 rounded-full" :class="item.enabled ? 'bg-emerald-500' : 'bg-slate-300'" />
          <div class="min-w-[160px] flex-1">
            <div class="truncate text-[13px] font-semibold text-slate-900 dark:text-stone-100">{{ item.name }}</div>
            <div class="mt-0.5 text-[11px] text-slate-500 dark:text-stone-400">{{ item.url_hint }}</div>
          </div>
          <label class="inline-flex items-center gap-2 text-[12px] text-slate-600 dark:text-stone-300">
            <input type="checkbox" class="size-4 accent-[#315be8]" :checked="item.enabled" :aria-label="`${item.name}启用机器人`" :disabled="updatingId === item.id" @change="toggleWebhook(item)" />
            启用
          </label>
          <label class="inline-flex items-center gap-2 text-[12px] text-slate-600 dark:text-stone-300">
            <input type="checkbox" class="size-4 accent-[#315be8]" :checked="item.receive_alerts" :disabled="!item.enabled || updatingId === item.id" :aria-label="`${item.name}接收监控告警`" @change="setPurpose(item, 'receive_alerts', ($event.target as HTMLInputElement).checked)" />
            监控告警
          </label>
          <label class="inline-flex items-center gap-2 text-[12px] text-slate-600 dark:text-stone-300">
            <input type="checkbox" class="size-4 accent-[#315be8]" :checked="item.receive_feedback" :disabled="!item.enabled || updatingId === item.id" :aria-label="`${item.name}接收反馈消息`" @change="setPurpose(item, 'receive_feedback', ($event.target as HTMLInputElement).checked)" />
            反馈消息
          </label>
          <button type="button" class="inline-flex size-9 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:text-stone-300 dark:hover:bg-white/10" :disabled="testingId === item.id" :aria-label="`向${item.name}发送测试消息`" :title="`向${item.name}发送测试消息`" @click="sendTest(item)">
            <LoaderCircle v-if="testingId === item.id" class="size-4 animate-spin" /><Send v-else class="size-4" />
          </button>
          <button type="button" class="inline-flex size-9 items-center justify-center rounded-md text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-400/10" :aria-label="`删除${item.name}`" :title="`删除${item.name}`" @click="deleteTarget = item"><Trash2 class="size-4" /></button>
        </div>
      </div>
      <p v-else class="mt-3 py-4 text-[13px] text-slate-500">尚未添加机器人。</p>
      <form class="mt-4 flex flex-col gap-3 md:flex-row md:items-end" @submit.prevent="addWebhook">
        <label class="min-w-0 md:w-48 text-[12px] font-medium text-slate-600 dark:text-stone-300">名称
          <input v-model="webhookName" class="studio-input mt-1.5 h-10 px-3" maxlength="80" placeholder="例如：运维告警群" required autocomplete="off" />
        </label>
        <label class="min-w-0 flex-1 text-[12px] font-medium text-slate-600 dark:text-stone-300">Webhook 地址
          <input v-model="webhookUrl" type="url" class="studio-input mt-1.5 h-10 px-3" placeholder="粘贴企业微信机器人 Webhook 地址" required autocomplete="off" />
        </label>
        <button type="submit" class="studio-button inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-md bg-slate-950 px-4 text-[13px] font-semibold text-white disabled:opacity-50 dark:bg-white dark:text-slate-950" :disabled="adding">
          <LoaderCircle v-if="adding" class="size-4 animate-spin" /><Plus v-else class="size-4" />添加机器人
        </button>
      </form>
    </div>

    <div v-if="loading" class="mt-5 grid gap-3 md:grid-cols-3">
      <div v-for="index in 3" :key="index" class="studio-skeleton h-20 rounded-xl" />
    </div>
    <div v-else class="mt-5 grid gap-x-5 gap-y-4 border-t border-black/[0.06] pt-5 dark:border-white/10 lg:grid-cols-3">
      <div class="space-y-4">
        <label class="flex min-h-11 items-center justify-between gap-4">
          <span>
            <span class="block text-[13px] font-semibold text-slate-900 dark:text-stone-100">自动告警</span>
            <span class="mt-0.5 block text-[12px] text-slate-500">关闭后 Worker 保持在线但不执行检查</span>
          </span>
          <input v-model="draft.enabled" type="checkbox" role="switch" aria-label="自动告警" class="peer sr-only" :disabled="!webhooks.some((item) => item.enabled && item.receive_alerts)" />
          <span aria-hidden="true" class="relative h-6 w-11 shrink-0 cursor-pointer rounded-full bg-slate-200 transition-colors duration-200 peer-focus-visible:ring-[3px] peer-focus-visible:ring-[#4F7CFF]/20 peer-checked:bg-[#4F7CFF] peer-disabled:cursor-not-allowed peer-disabled:opacity-50 motion-reduce:transition-none dark:bg-white/15">
            <span class="absolute left-1 top-1 size-4 rounded-full bg-white transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none" :class="draft.enabled ? 'translate-x-5' : 'translate-x-0'" />
          </span>
        </label>
        <label class="block text-[12px] font-medium text-slate-600 dark:text-stone-300">
          检查频率
          <select v-model.number="draft.check_interval_secs" class="studio-input mt-1.5 h-10 px-3">
            <option :value="30">每 30 秒</option>
            <option :value="60">每 1 分钟</option>
            <option :value="120">每 2 分钟</option>
            <option :value="300">每 5 分钟</option>
          </select>
        </label>
        <label class="block text-[12px] font-medium text-slate-600 dark:text-stone-300">
          队列等待告警阈值
          <input v-model.number="draft.queue_waiting_threshold" type="number" min="1" max="100000" class="studio-input mt-1.5 h-10 px-3 tabular-nums" />
        </label>
      </div>

      <div class="space-y-4 lg:border-l lg:border-black/[0.06] lg:pl-5 lg:dark:border-white/10">
        <label class="block text-[12px] font-medium text-slate-600 dark:text-stone-300">
          故障恢复冷却
          <select v-model.number="draft.cooldown_secs" class="studio-input mt-1.5 h-10 px-3">
            <option :value="300">5 分钟</option>
            <option :value="600">10 分钟</option>
            <option :value="1800">30 分钟</option>
          </select>
        </label>
        <label class="block text-[12px] font-medium text-slate-600 dark:text-stone-300">
          持续故障重复提醒
          <select v-model.number="draft.repeat_secs" class="studio-input mt-1.5 h-10 px-3">
            <option :value="900">15 分钟</option>
            <option :value="1800">30 分钟</option>
            <option :value="3600">1 小时</option>
            <option :value="10800">3 小时</option>
          </select>
        </label>
        <div class="grid grid-cols-2 gap-3">
          <label class="block text-[12px] font-medium text-slate-600 dark:text-stone-300">死信阈值<input v-model.number="draft.dead_letter_threshold" type="number" min="1" max="100000" class="studio-input mt-1.5 h-10 px-3 tabular-nums" /></label>
          <label class="block text-[12px] font-medium text-slate-600 dark:text-stone-300">卡住任务阈值<input v-model.number="draft.stale_task_threshold" type="number" min="1" max="100000" class="studio-input mt-1.5 h-10 px-3 tabular-nums" /></label>
        </div>
      </div>

      <div class="space-y-4 lg:border-l lg:border-black/[0.06] lg:pl-5 lg:dark:border-white/10">
        <label class="flex min-h-10 items-center justify-between gap-4 text-[13px] font-medium text-slate-700 dark:text-stone-200">
          Worker 离线告警
          <input v-model="draft.worker_missing_alert" type="checkbox" class="size-4 accent-[#315be8]" />
        </label>
        <label class="flex min-h-10 items-center justify-between gap-4 text-[13px] font-medium text-slate-700 dark:text-stone-200">
          故障恢复通知
          <input v-model="draft.recovery_notifications" type="checkbox" class="size-4 accent-[#315be8]" />
        </label>
        <dl class="border-y border-black/[0.06] py-3 text-[12px] dark:border-white/10">
          <div class="flex items-center justify-between gap-3">
            <dt class="flex items-center gap-1.5 text-slate-500"><Clock3 class="size-3.5" />最近巡检</dt>
            <dd class="font-medium text-slate-800 dark:text-stone-200">{{ formatTime(monitor?.last_check_at) }}</dd>
          </div>
          <div class="mt-2 flex items-center justify-between gap-3">
            <dt class="text-slate-500">耗时</dt>
            <dd class="tabular-nums text-slate-700 dark:text-stone-300">{{ monitor?.last_check_duration_ms || 0 }} ms</dd>
          </div>
        </dl>
        <p v-if="monitor?.last_check_error || loadError" class="flex items-start gap-2 text-[12px] leading-5 text-rose-600 dark:text-rose-300">
          <AlertTriangle class="mt-0.5 size-3.5 shrink-0" />{{ monitor?.last_check_error || loadError }}
        </p>
      </div>
    </div>

    <div class="mt-5 border-t border-black/[0.06] pt-4 dark:border-white/10">
      <div class="flex items-center justify-between gap-3">
        <div>
          <h3 class="text-[15px] font-semibold text-slate-900 dark:text-stone-100">最近告警事件</h3>
          <p class="mt-0.5 text-[12px] text-slate-500">当前未恢复 {{ openCount }} 项，同类故障已自动合并。</p>
        </div>
      </div>
      <div v-if="incidents.length" class="mt-3 divide-y divide-black/[0.06] dark:divide-white/10">
        <div v-for="item in incidents" :key="item.incident_id" class="grid gap-2 py-3 md:grid-cols-[auto_minmax(0,1fr)_auto] md:items-start">
          <span class="mt-0.5 w-fit rounded-md px-2 py-0.5 text-[11px] font-semibold" :class="item.state === 'resolved' ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300' : severityTone(item.severity)">
            {{ item.state === "resolved" ? "已恢复" : item.severity }}
          </span>
          <div class="min-w-0">
            <div class="truncate text-[13px] font-semibold text-slate-900 dark:text-stone-100">{{ item.title }}</div>
            <div class="mt-0.5 line-clamp-2 text-[12px] leading-5 text-slate-500 dark:text-stone-400">{{ item.summary }}</div>
          </div>
          <div class="text-left text-[11px] text-slate-400 md:text-right">
            <div>{{ formatTime(item.last_seen_at) }}</div>
            <div v-if="item.occurrence_count > 1" class="mt-1">累计 {{ item.occurrence_count }} 次</div>
          </div>
        </div>
      </div>
      <div v-else class="mt-3 flex items-center gap-2 py-3 text-[13px] text-slate-500">
        <CheckCircle2 class="size-4 text-emerald-600" />暂无告警事件
      </div>
    </div>
  </section>
  <BaseModal :open="!!deleteTarget" title="删除企业微信机器人" description="删除后这个群将不再收到告警；删除最后一个已启用机器人会自动暂停告警。" width-class="max-w-[420px]" :show-close="!deleting" @close="deleteTarget = null">
    <div class="flex justify-end gap-2 p-5">
      <button type="button" class="h-10 rounded-md border border-black/10 px-4 text-[13px] dark:border-white/10" :disabled="deleting" @click="deleteTarget = null">取消</button>
      <button type="button" class="h-10 rounded-md bg-rose-600 px-4 text-[13px] font-semibold text-white disabled:opacity-50" :disabled="deleting" @click="removeWebhook">确认删除</button>
    </div>
  </BaseModal>
</template>
