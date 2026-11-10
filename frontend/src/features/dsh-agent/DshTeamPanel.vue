<script setup lang="ts">
import { Check, CircleAlert, Clock3, LoaderCircle, Pencil, Play, Users, X } from "@lucide/vue";
import { computed } from "vue";
import type { DshTeam, DshTeamAction } from "./api";

const props = defineProps<{ team: DshTeam; busy: boolean }>();
const emit = defineEmits<{ action: [action: DshTeamAction]; edit: [] }>();
const hasFailure = computed(() => !!props.team.error || props.team.tasks.some(task => ["failed", "blocked"].includes(task.status)));
const finished = computed(() => props.team.tasks.length > 0 && props.team.tasks.every(task => ["completed", "failed", "cancelled", "blocked"].includes(task.status)));
const title = computed(() => hasFailure.value && finished.value ? "协作未完成" : props.team.halted ? "协作已停止" : props.team.phase === "staged" ? "请确认协作计划" : finished.value ? "协作结果" : "成员正在协作");
const labels: Record<string, string> = { pending: "等待执行", claimed: "已认领", in_progress: "执行中", completed: "已完成", failed: "失败", cancelled: "已取消", blocked: "前置任务失败，未执行" };
</script>

<template>
  <section class="dsh-team" aria-label="协作计划">
    <header><Users class="size-4" /><strong>{{ title }}</strong><span>{{ team.members.length }} 位成员</span></header>
    <p v-if="team.error" class="dsh-team-error" role="status">{{ team.error }}</p>
    <div class="dsh-team-members">
      <span v-for="member in team.members" :key="member.name" :title="member.role">{{ member.name }}<small v-if="member.role">{{ member.role }}</small></span>
    </div>
    <ol>
      <li v-for="task in team.tasks" :key="task.id" :class="{ 'has-error': ['failed', 'blocked'].includes(task.status) }">
        <Check v-if="task.status === 'completed'" class="size-4" />
        <CircleAlert v-else-if="['failed', 'blocked'].includes(task.status)" class="size-4" />
        <LoaderCircle v-else-if="busy && ['claimed', 'in_progress'].includes(task.status)" class="size-4 animate-spin" />
        <Clock3 v-else class="size-4" />
        <div><div class="dsh-team-task-title"><strong>{{ task.subject }}</strong><span>{{ labels[task.status] || task.status }}</span></div>
          <p v-if="task.assignee" class="dsh-team-assignee">{{ task.assignee }}</p>
          <p v-if="task.description && team.phase === 'staged'">{{ task.description }}</p>
          <details v-if="task.output"><summary>查看结果</summary><p>{{ task.output }}</p></details>
        </div>
      </li>
    </ol>
    <footer v-if="!busy">
      <button v-if="team.phase === 'staged' && !team.halted" class="dsh-team-primary" type="button" @click="emit('action', 'approve')"><Play class="size-3.5" />确认执行</button>
      <button v-if="team.phase === 'staged' && !team.halted" type="button" @click="emit('edit')"><Pencil class="size-3.5" />修改计划</button>
      <button v-if="team.halted && !hasFailure" class="dsh-team-primary" type="button" @click="emit('action', 'resume')"><Play class="size-3.5" />继续协作</button>
      <button type="button" @click="emit('action', 'discard')"><X class="size-3.5" />{{ finished ? '结束协作' : '取消计划' }}</button>
    </footer>
  </section>
</template>

<style scoped>
.dsh-team { max-width: 760px; margin: 0 auto 28px; padding: 18px 0; border-block: 1px solid var(--studio-border); font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }
.dsh-team header { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.dsh-team header > span { margin-left: auto; color: var(--muted-foreground); font-size: 12px; }
.dsh-team-members { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-block: 12px 16px; }
.dsh-team-members > span { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }
.dsh-team-members small { color: var(--muted-foreground); font-size: 12px; }
.dsh-team ol { display: grid; gap: 14px; margin: 0; padding: 0; list-style: none; }
.dsh-team li { display: flex; gap: 10px; align-items: flex-start; }
.dsh-team li > svg { flex: none; margin-top: 3px; color: var(--muted-foreground); }
.dsh-team li > div { flex: 1; min-width: 0; }
.dsh-team-task-title { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
.dsh-team-task-title strong { font-weight: 500; }
.dsh-team-task-title > span,.dsh-team-assignee { color: var(--muted-foreground); font-size: 12px; }
.dsh-team p { margin: 3px 0 0; white-space: pre-wrap; }
.dsh-team details { margin-top: 5px; }
.dsh-team summary { width: fit-content; color: var(--muted-foreground); cursor: pointer; }
.dsh-team details p { max-height: 280px; overflow-y: auto; }
.dsh-team footer { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 18px; }
.dsh-team button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; padding: 5px 10px; border: 1px solid var(--studio-border); border-radius: 6px; background: var(--studio-panel); color: var(--foreground); cursor: pointer; font-size: 12px; }
.dsh-team button:hover { background: var(--studio-hover); }
.dsh-team button:focus-visible,.dsh-team summary:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.dsh-team button.dsh-team-primary { background: var(--primary); color: var(--primary-foreground); border-color: var(--primary); }
.dsh-team .has-error > svg { color: var(--destructive); }
.dsh-team .dsh-team-error { margin-top: 10px; color: var(--destructive); }
@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none; } }
</style>
