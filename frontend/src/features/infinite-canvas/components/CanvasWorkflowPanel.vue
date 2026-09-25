<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { FolderOpen, GitBranch, ListChecks, LoaderCircle, RefreshCw, Save, Trash2, X } from '@lucide/vue';
import type { WorkflowSummary } from '@/features/infinite-canvas/types';

const props = defineProps<{
  open: boolean;
  loading: boolean;
  workflows: WorkflowSummary[];
  activeWorkflowId: string;
  saving: boolean;
  deleting: boolean;
  dirty: boolean;
  lastSavedAt?: Date;
}>();

const emit = defineEmits<{
  close: [];
  refresh: [];
  openWorkflow: [workflow: WorkflowSummary];
  deleteWorkflow: [workflow: WorkflowSummary];
  deleteWorkflows: [workflows: WorkflowSummary[]];
  save: [];
}>();

const selectionMode = ref(false);
const selectedIds = ref<Set<string>>(new Set());
const selectedCount = computed(() => selectedIds.value.size);
const allSelected = computed(() => (
  props.workflows.length > 0 && props.workflows.every((workflow) => selectedIds.value.has(workflow.id))
));
const partlySelected = computed(() => selectedCount.value > 0 && !allSelected.value);

function replaceSelection(ids: Iterable<string>) {
  selectedIds.value = new Set(ids);
}

function setSelectionMode(value: boolean) {
  if (props.deleting) return;
  selectionMode.value = value;
  if (!value) replaceSelection([]);
}

function toggleSelectionMode() {
  setSelectionMode(!selectionMode.value);
}

function toggleWorkflowSelection(workflowId: string) {
  if (props.deleting) return;
  const next = new Set(selectedIds.value);
  if (next.has(workflowId)) next.delete(workflowId);
  else next.add(workflowId);
  replaceSelection(next);
}

function toggleSelectAll() {
  replaceSelection(allSelected.value ? [] : props.workflows.map((workflow) => workflow.id));
}

function activateWorkflow(workflow: WorkflowSummary) {
  if (selectionMode.value) {
    toggleWorkflowSelection(workflow.id);
    return;
  }
  emit('openWorkflow', workflow);
}

function deleteSelectedWorkflows() {
  const selected = props.workflows.filter((workflow) => selectedIds.value.has(workflow.id));
  if (selected.length) emit('deleteWorkflows', selected);
}

watch(() => props.open, (open) => {
  if (!open) setSelectionMode(false);
});

watch(() => JSON.stringify(props.workflows.map((workflow) => workflow.id)), () => {
  const availableIds = new Set(props.workflows.map((workflow) => workflow.id));
  replaceSelection([...selectedIds.value].filter((workflowId) => availableIds.has(workflowId)));
});

watch(() => props.deleting, (deleting, wasDeleting) => {
  if (wasDeleting && !deleting && !selectedIds.value.size) setSelectionMode(false);
});

function formatWorkflowTime(value?: string) {
  if (!value) return '刚刚';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(timestamp);
}
</script>

<template>
  <Transition name="workflow-panel">
    <aside v-if="open" class="workflow-panel" aria-label="画布记录" @pointerdown.stop>
      <header class="workflow-panel-header">
        <div>
          <h2>画布记录</h2>
          <span>{{ workflows.length }} 个已保存画布</span>
        </div>
        <div class="workflow-panel-actions">
          <button
            v-if="workflows.length"
            class="icon-button workflow-bulk-toggle"
            :class="{ active: selectionMode }"
            type="button"
            title="批量删除画布记录"
            aria-label="批量删除画布记录"
            :aria-pressed="selectionMode"
            :disabled="loading || deleting"
            @click="toggleSelectionMode"
          >
            <ListChecks :size="17" />
          </button>
          <button class="icon-button" type="button" title="刷新画布记录" aria-label="刷新画布记录" :disabled="loading || deleting" @click="$emit('refresh')">
            <RefreshCw :class="{ spin: loading }" :size="17" />
          </button>
          <button class="icon-button" type="button" title="关闭" aria-label="关闭画布记录" :disabled="deleting" @click="$emit('close')"><X :size="18" /></button>
        </div>
      </header>

      <div class="workflow-list">
        <div v-if="selectionMode && workflows.length" class="workflow-bulk-toolbar">
          <label>
            <input
              type="checkbox"
              :checked="allSelected"
              :indeterminate="partlySelected"
              :disabled="deleting"
              aria-label="全选画布记录"
              @change="toggleSelectAll"
            />
            <span>全选</span>
          </label>
          <span>已选 {{ selectedCount }} 项</span>
        </div>
        <div v-if="loading && !workflows.length" class="workflow-loading" aria-live="polite">
          <span v-for="index in 3" :key="index" />
          <p>正在读取画布记录</p>
        </div>
        <div v-else-if="!workflows.length" class="workflow-empty">
          <FolderOpen :size="24" aria-hidden="true" />
          <strong>还没有保存记录</strong>
          <p>保存当前画布后会显示在这里。</p>
        </div>
        <template v-else>
          <article
            v-for="workflow in workflows"
            :key="workflow.id"
            class="workflow-row"
            :class="{
              active: workflow.id === activeWorkflowId,
              'is-selection-mode': selectionMode,
              'is-checked': selectedIds.has(workflow.id),
            }"
          >
            <label v-if="selectionMode" class="workflow-select-check" @click.stop>
              <input
                type="checkbox"
                :checked="selectedIds.has(workflow.id)"
                :disabled="deleting"
                :aria-label="`选择画布 ${workflow.title}`"
                @change="toggleWorkflowSelection(workflow.id)"
              />
            </label>
            <button
              class="workflow-open"
              type="button"
              :aria-label="selectionMode ? `${selectedIds.has(workflow.id) ? '取消选择' : '选择'}画布 ${workflow.title}` : `打开画布 ${workflow.title}`"
              :disabled="deleting"
              @click="activateWorkflow(workflow)"
            >
              <span class="workflow-cover">
                <img v-if="workflow.coverUrl" :src="workflow.coverUrl" alt="" />
                <GitBranch v-else :size="17" aria-hidden="true" />
              </span>
              <span class="workflow-copy">
                <strong :title="workflow.title">{{ workflow.title }}</strong>
                <small>{{ workflow.nodeCount }} 个节点 · {{ formatWorkflowTime(workflow.updatedAt) }}</small>
              </span>
            </button>
            <button v-if="!selectionMode" class="workflow-delete" type="button" title="永久删除" :aria-label="`永久删除画布 ${workflow.title}`" :disabled="deleting" @click.stop="$emit('deleteWorkflow', workflow)">
              <Trash2 :size="15" aria-hidden="true" />
            </button>
          </article>
        </template>
      </div>

      <footer class="workflow-panel-footer" :class="{ 'is-bulk': selectionMode }">
        <template v-if="selectionMode">
          <button class="secondary-button" type="button" :disabled="deleting" @click="setSelectionMode(false)">
            <X :size="16" />取消
          </button>
          <button
            class="workflow-bulk-delete"
            type="button"
            :aria-label="`永久删除已选 ${selectedCount} 个画布`"
            :disabled="!selectedCount || deleting"
            @click="deleteSelectedWorkflows"
          >
            <LoaderCircle v-if="deleting" class="spin" :size="16" />
            <Trash2 v-else :size="16" />
            {{ deleting ? '删除中' : `删除 ${selectedCount} 项` }}
          </button>
        </template>
        <button v-else class="primary-button" type="button" :disabled="saving" @click="$emit('save')">
          <LoaderCircle v-if="saving" class="spin" :size="16" />
          <Save v-else :size="16" />
          {{ saving ? '保存中' : dirty || !lastSavedAt ? '保存当前画布' : '已保存' }}
        </button>
      </footer>
    </aside>
  </Transition>
</template>
