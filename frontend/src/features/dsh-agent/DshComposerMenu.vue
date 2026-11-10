<script setup lang="ts">
import { ArrowUpRight, BarChart3, Box, Check, ChevronRight, FileText, ImagePlus, Images, Link2, Network, Paintbrush, Paperclip, Plus, Search, Shapes, UsersRound, Wrench, X } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import ComposerSelect from "@/components/ComposerSelect.vue";
import { formatImageModel } from "@/lib/image-models";
import type { DshExpert, DshSkill, DshConnector } from "./api";

const props = defineProps<{
  disabled: boolean;
  imageEnabled: boolean;
  imageDisabled: boolean;
  filesEnabled?: boolean;
  fileDisabled: boolean;
  imageModel: string;
  experts?: DshExpert[];
  expertId?: string;
  skills?: DshSkill[];
  skillIds?: string[];
  connectors?: DshConnector[];
  teamsEnabled?: boolean;
  collaboration?: boolean;
  searchEnabled?: boolean;
}>();
const emit = defineEmits<{
  action: [action: "image" | "file" | "generate" | "search"];
  expert: [id: string];
  skill: [id: string];
  collaboration: [enabled: boolean];
}>();
const id = useId();
const trigger = ref<HTMLButtonElement>();
const panel = ref<HTMLElement>();
const open = ref(false);
const selected = ref("attachments");
const compact = ref(false);
const placement = ref("bottom");
const position = ref<Record<string, string>>({});
const query = ref("");
const source = ref("");
const searchInput = ref<HTMLInputElement>();
const content = ref<HTMLElement>();
const selectedSkillCount = computed(() => (props.skills || []).filter(skill => props.skillIds?.includes(skill.id)).length);
const sourceOptions = computed(() => [
  { value: "", label: "全部来源" },
  ...Array.from(new Set((props.skills || []).map(skill => skill.source).filter(Boolean))).map(value => ({ value, label: value })),
]);
const filteredSkills = computed(() => (props.skills || []).filter(skill =>
  (!source.value || skill.source === source.value) && matchesQuery(skill.label, skill.description, skill.source),
));
const filteredExperts = computed(() => (props.experts || []).filter(expert => matchesQuery(expert.label, expert.description)));
const skillIcons: Record<string, typeof Wrench> = {
  "gpt-image-2-style-library": Images,
  "diagram-design": Network,
  "ecommerce-competitor-analysis": BarChart3,
  "customer-research": UsersRound,
  "competitor-profiling": BarChart3,
  "ecommerce-competitor-analyzer": FileText,
};
function matchesQuery(...values: string[]) {
  const keyword = query.value.trim().toLocaleLowerCase();
  return !keyword || values.some(value => value.toLocaleLowerCase().includes(keyword));
}
const availableConnectors = computed<DshConnector[]>(() => props.connectors ?? (props.searchEnabled
  ? [{ id: "public-web", label: "联网搜索", description: "公开网页搜索与来源读取", available: true }] : []));
const sections = computed(() => [
  ...(props.imageEnabled || props.filesEnabled ? [{ id: "attachments", label: "添加图片和文件", icon: Paperclip }] : []),
  ...(props.imageEnabled ? [{ id: "images", label: "生成图片", icon: Images }] : []),
  ...(props.experts?.length ? [{ id: "experts", label: "专家", icon: Paintbrush }] : []),
  ...(props.skills?.length ? [{ id: "skills", label: "技能", icon: Wrench }] : []),
  ...(props.teamsEnabled ? [{ id: "teams", label: "多智能体协作", icon: Network }] : []),
  ...(availableConnectors.value.length ? [{ id: "connectors", label: "连接器", icon: Link2 }] : []),
]);
const unavailable = computed(() => props.disabled || !sections.value.length);
let observer: ResizeObserver | undefined;

function placePanel() {
  const bounds = trigger.value?.closest(".dsh-composer")?.getBoundingClientRect();
  if (!bounds) return;
  const viewport = window.visualViewport;
  const leftEdge = (viewport?.offsetLeft || 0) + 12;
  const topEdge = (viewport?.offsetTop || 0) + 12;
  const rightEdge = leftEdge + (viewport?.width || innerWidth) - 24;
  const bottomEdge = topEdge + (viewport?.height || innerHeight) - 24;
  const width = Math.min(880, rightEdge - leftEdge);
  compact.value = width <= 600;
  const below = bottomEdge - bounds.bottom - 8;
  const above = bounds.top - topEdge - 8;
  const height = Math.min(540, bottomEdge - topEdge);
  placement.value = below >= height ? "bottom" : above >= height ? "top" : "center";
  const top = placement.value === "bottom" ? bounds.bottom + 8 : placement.value === "top" ? bounds.top - height - 8
    : topEdge + (bottomEdge - topEdge - height) / 2;
  position.value = {
    width: `${width}px`, height: `${height}px`,
    left: `${Math.max(leftEdge, Math.min(bounds.left, rightEdge - width))}px`,
    top: `${Math.max(topEdge, Math.min(top, bottomEdge - height))}px`,
  };
}

function cleanup() {
  observer?.disconnect();
  window.removeEventListener("resize", placePanel);
  window.removeEventListener("scroll", placePanel, true);
  window.visualViewport?.removeEventListener("resize", placePanel);
  window.visualViewport?.removeEventListener("scroll", placePanel);
}

function focusAction() {
  content.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true });
}

function beforeToggle(event: Event) {
  open.value = (event as ToggleEvent).newState === "open";
  if (open.value) {
    selected.value = sections.value[0]?.id || "attachments";
    query.value = "";
    source.value = "";
    placePanel();
  }
}

async function onToggle() {
  cleanup();
  if (!open.value) return;
  window.addEventListener("resize", placePanel);
  window.addEventListener("scroll", placePanel, true);
  window.visualViewport?.addEventListener("resize", placePanel);
  window.visualViewport?.addEventListener("scroll", placePanel);
  const composer = trigger.value?.closest(".dsh-composer");
  if (composer) { observer = new ResizeObserver(placePanel); observer.observe(composer); }
  await nextTick();
  if (open.value) focusAction();
}

function close(restoreFocus = true) {
  panel.value?.hidePopover();
  if (restoreFocus) trigger.value?.focus({ preventScroll: true });
}

function choose(action: "image" | "file" | "generate" | "search") { close(); emit("action", action); }
function chooseExpert(expert: string) { close(); emit("expert", expert); }
function chooseSkill(skill: string) { emit("skill", skill); }
function chooseMode(enabled: boolean) { close(); emit("collaboration", enabled); }

async function onTabKeydown(event: KeyboardEvent) {
  if (event.key === (compact.value ? "ArrowDown" : "ArrowRight")) { event.preventDefault(); focusAction(); return; }
  const next = compact.value ? "ArrowRight" : "ArrowDown";
  const previous = compact.value ? "ArrowLeft" : "ArrowUp";
  if (![next, previous, "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const current = sections.value.findIndex(section => section.id === selected.value);
  const index = event.key === "Home" ? 0 : event.key === "End" ? sections.value.length - 1
    : (current + (event.key === next ? 1 : -1) + sections.value.length) % sections.value.length;
  selected.value = sections.value[index]!.id;
  await nextTick();
  panel.value?.querySelector<HTMLButtonElement>(`[data-section="${selected.value}"]`)?.focus();
}

function onKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented || (event.target as HTMLElement).closest(".composer-select")) return;
  if (event.key === "Escape") { event.preventDefault(); close(); return; }
  if ((event.target as HTMLElement).closest("input, .composer-select")) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    panel.value?.querySelector<HTMLButtonElement>(`[data-section="${selected.value}"]`)?.focus({ preventScroll: true });
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  if (!(event.target as HTMLElement).closest(".dsh-tools-content")) return;
  const buttons = Array.from(content.value?.querySelectorAll<HTMLButtonElement>(".dsh-tool-action:not(:disabled), .dsh-tools-empty button") || []);
  if (!buttons.length) return;
  const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
  const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
    : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
  buttons[index]?.focus();
}

watch(unavailable, value => { if (value && open.value) close(false); });
watch(sections, sections => {
  if (!sections.some(section => section.id === selected.value)) selected.value = sections[0]?.id || "attachments";
});
watch(selected, () => { query.value = ""; source.value = ""; });
watch([selected, query, source], () => { if (content.value) content.value.scrollTop = 0; });
watch(sourceOptions, options => { if (!options.some(option => option.value === source.value)) source.value = ""; });
onBeforeUnmount(() => { cleanup(); panel.value?.hidePopover(); });
</script>

<template>
  <div class="dsh-tools-root">
    <button ref="trigger" class="dsh-upload-button" :class="{ 'is-open': open }" type="button" :disabled="unavailable" :popovertarget="id" :aria-controls="id" :aria-expanded="open" aria-haspopup="dialog" aria-label="添加附件" :title="open ? '收起功能面板' : '添加附件与工具'" @keydown.down.prevent="panel?.showPopover()"><Plus aria-hidden="true" /></button>
    <div :id="id" ref="panel" class="dsh-tools-panel" :style="position" :data-placement="placement" popover="auto" role="dialog" aria-label="附件与工具" @beforetoggle="beforeToggle" @toggle="onToggle" @keydown="onKeydown">
      <header class="dsh-tools-heading">
        <span class="dsh-tools-brand"><Shapes aria-hidden="true" /></span>
        <div><h2>添加能力和文件</h2><p>当前对话</p></div>
        <button type="button" class="dsh-tools-close" aria-label="关闭能力面板" title="关闭" @click="close()"><X aria-hidden="true" /></button>
      </header>
      <div class="dsh-tools-layout">
        <div class="dsh-tools-nav" role="tablist" aria-label="工具分类" :aria-orientation="compact ? 'horizontal' : 'vertical'" @keydown="onTabKeydown">
          <button v-for="section in sections" :id="`${id}-${section.id}`" :key="section.id" type="button" role="tab" :data-section="section.id" :aria-selected="selected === section.id" :aria-controls="`${id}-content`" :tabindex="selected === section.id ? 0 : -1" @click="selected = section.id">
            <component :is="section.icon" aria-hidden="true" /><span>{{ section.label }}</span><span v-if="section.id === 'skills' && selectedSkillCount" class="dsh-tools-count" aria-hidden="true">{{ selectedSkillCount }}</span><ChevronRight class="dsh-tools-chevron" aria-hidden="true" />
          </button>
        </div>
        <div :id="`${id}-content`" class="dsh-tools-main" role="tabpanel" :aria-labelledby="`${id}-${selected}`" :tabindex="-1">
          <div v-if="selected === 'skills' || selected === 'experts'" class="dsh-tools-toolbar">
            <div class="dsh-tools-search">
              <Search aria-hidden="true" />
              <input ref="searchInput" v-model="query" type="search" :aria-label="selected === 'skills' ? '搜索技能' : '搜索专家'" :placeholder="selected === 'skills' ? '搜索技能名称、描述或来源' : '搜索专家名称或描述'" autocomplete="off" @keydown.down.prevent="focusAction()" />
              <button v-if="query" type="button" aria-label="清空搜索" title="清空搜索" @click="query = ''; searchInput?.focus()"><X aria-hidden="true" /></button>
            </div>
            <ComposerSelect v-if="selected === 'skills'" v-model="source" label="" aria-label="技能来源" :options="sourceOptions" :menu-min-width="240" />
          </div>
          <div ref="content" class="dsh-tools-content">
          <template v-if="selected === 'attachments'">
            <div class="dsh-tools-list-heading"><h3>图片与文件</h3></div>
            <button v-if="imageEnabled" type="button" class="dsh-tool-action" :disabled="imageDisabled" @click="choose('image')"><span class="dsh-tool-icon"><ImagePlus aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>上传图片</strong><small>PNG、JPG、WebP、GIF · 最多 6 张</small></span><span class="dsh-tool-state"><Plus aria-hidden="true" /><span>上传</span></span></button>
            <button v-if="filesEnabled" type="button" class="dsh-tool-action" :disabled="fileDisabled" @click="choose('file')"><span class="dsh-tool-icon is-file"><Paperclip aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>上传文件</strong><small>PDF、Word、文本等 · 最多 4 个</small></span><span class="dsh-tool-state"><Plus aria-hidden="true" /><span>上传</span></span></button>
          </template>
          <template v-else-if="selected === 'images'">
            <div class="dsh-tools-list-heading"><h3>图片创作</h3></div>
            <p class="dsh-tools-current">{{ formatImageModel(imageModel) }}</p>
            <button type="button" class="dsh-tool-action" aria-label="生成图片" @click="choose('generate')"><span class="dsh-tool-icon"><Images aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>生成图片</strong><small>文字创作与参考图生成</small></span><span class="dsh-tool-state"><ArrowUpRight aria-hidden="true" /><span>使用</span></span></button>
            <button type="button" class="dsh-tool-action" :disabled="imageDisabled" @click="choose('image')"><span class="dsh-tool-icon"><ImagePlus aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>上传参考图片</strong><small>商品、人物与风格参考</small></span><span class="dsh-tool-state"><Plus aria-hidden="true" /><span>上传</span></span></button>
          </template>
          <template v-else-if="selected === 'experts'">
            <div class="dsh-tools-list-heading"><h3>专家</h3><span>{{ filteredExperts.length }} 位</span></div>
            <button v-for="expert in filteredExperts" :key="expert.id" type="button" class="dsh-tool-action" :aria-pressed="expert.id === expertId" @click="chooseExpert(expert.id)"><span class="dsh-tool-icon is-expert"><Paintbrush aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>{{ expert.label }}</strong><small>{{ expert.description }}</small></span><span class="dsh-tool-state"><Check v-if="expert.id === expertId" aria-hidden="true" /><Plus v-else aria-hidden="true" /><span>{{ expert.id === expertId ? '已选择' : '选择' }}</span></span></button>
          </template>
          <template v-else-if="selected === 'skills'">
            <div class="dsh-tools-list-heading"><h3>技能 <span>{{ filteredSkills.length }}</span></h3><span class="dsh-tools-selection" role="status">已选择 {{ selectedSkillCount }} / 4</span></div>
            <button v-for="skill in filteredSkills" :key="skill.id" type="button" class="dsh-tool-action" :data-skill-id="skill.id" :aria-pressed="!!skillIds?.includes(skill.id)" :disabled="!skillIds?.includes(skill.id) && selectedSkillCount >= 4" :title="!skillIds?.includes(skill.id) && selectedSkillCount >= 4 ? '每轮最多选择 4 项技能' : skillIds?.includes(skill.id) ? `取消选择 ${skill.label}` : `添加 ${skill.label}`" @click="chooseSkill(skill.id)"><span class="dsh-tool-icon" :class="{ 'is-research': skill.id.includes('competitor') || skill.id === 'customer-research', 'is-file': skill.id === 'diagram-design' }"><component :is="skillIcons[skill.id] || Wrench" aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>{{ skill.label }}</strong><small>{{ skill.description }}</small><span class="dsh-tool-source"><Box aria-hidden="true" /><span :title="skill.source">{{ skill.source }}</span></span></span><span class="dsh-tool-state"><Check v-if="skillIds?.includes(skill.id)" aria-hidden="true" /><Plus v-else aria-hidden="true" /><span>{{ skillIds?.includes(skill.id) ? '已添加' : '添加' }}</span></span></button>
          </template>
          <template v-else-if="selected === 'connectors'">
            <div class="dsh-tools-list-heading"><h3>连接器</h3></div>
            <button v-for="connector in availableConnectors" :key="connector.id" type="button" class="dsh-tool-action" :aria-label="connector.label" :disabled="!connector.available || connector.id !== 'public-web'" @click="choose('search')"><span class="dsh-tool-icon is-file"><Search aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>{{ connector.label }}</strong><small>{{ connector.description }}</small><small v-if="!connector.available">未配置</small></span><span class="dsh-tool-state"><ArrowUpRight aria-hidden="true" /><span>使用</span></span></button>
          </template>
          <template v-else-if="selected === 'teams'">
            <div class="dsh-tools-list-heading"><h3>对话模式</h3></div>
            <button type="button" class="dsh-tool-action" aria-label="普通对话" :aria-pressed="!collaboration" @click="chooseMode(false)"><span class="dsh-tool-icon"><Shapes aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>普通对话</strong><small>单个智能体处理当前任务</small></span><span class="dsh-tool-state"><Check v-if="!collaboration" aria-hidden="true" /><Plus v-else aria-hidden="true" /><span>{{ !collaboration ? '已选择' : '选择' }}</span></span></button>
            <button type="button" class="dsh-tool-action" aria-label="多智能体协作" :aria-pressed="!!collaboration" @click="chooseMode(true)"><span class="dsh-tool-icon is-expert"><Network aria-hidden="true" /></span><span class="dsh-tool-copy"><strong>多智能体协作</strong><small>多个智能体分工完成复杂任务</small></span><span class="dsh-tool-state"><Check v-if="collaboration" aria-hidden="true" /><Plus v-else aria-hidden="true" /><span>{{ collaboration ? '已选择' : '选择' }}</span></span></button>
          </template>
          <div v-if="(selected === 'skills' && !filteredSkills.length) || (selected === 'experts' && !filteredExperts.length)" class="dsh-tools-empty" role="status"><Search aria-hidden="true" /><strong>没有找到匹配的{{ selected === 'skills' ? '技能' : '专家' }}</strong><button type="button" @click="query = ''; source = ''; searchInput?.focus()">清除筛选</button></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dsh-tools-root { display: contents; }
.dsh-upload-button { display: grid; width: 36px; height: 36px; flex: none; place-items: center; border: 0; border-radius: 50%; background: var(--secondary); color: var(--muted-foreground); cursor: pointer; transition: color 180ms ease-out, background-color 180ms ease-out; }
.dsh-upload-button svg { width: 20px; height: 20px; transform: rotate(0deg); transition: transform 240ms cubic-bezier(.22, 1, .36, 1); }
.dsh-upload-button.is-open svg { transform: rotate(45deg); }
.dsh-upload-button:hover:not(:disabled), .dsh-upload-button.is-open { background: var(--studio-hover); color: var(--foreground); }
.dsh-upload-button:disabled { opacity: .5; cursor: not-allowed; }
.dsh-upload-button:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.dsh-tools-panel { position: fixed; inset: auto; container-type: inline-size; box-sizing: border-box; margin: 0; padding: 0; overflow: hidden; border: 1px solid var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--foreground); box-shadow: 0 3px 8px rgb(0 0 0 / .12); opacity: 0; transform: translateY(-6px) scale(.985); transform-origin: top left; transition: opacity 180ms ease-out, transform 220ms cubic-bezier(.22, 1, .36, 1), display 220ms allow-discrete, overlay 220ms allow-discrete; }
.dsh-tools-panel:not(:popover-open) { display: none; }
.dsh-tools-panel[data-placement="top"] { transform: translateY(6px) scale(.985); transform-origin: bottom left; }
.dsh-tools-panel[data-placement="center"] { transform-origin: center; }
.dsh-tools-panel:popover-open { opacity: 1; transform: translateY(0) scale(1); }
.dsh-tools-panel::backdrop { background: transparent; }
.dsh-tools-heading { box-sizing: border-box; display: flex; align-items: center; gap: 12px; height: 72px; padding: 14px 20px; border-bottom: 1px solid var(--studio-border); }
.dsh-tools-brand { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 8px; color: var(--accent); background: var(--studio-hover); }
.dsh-tools-heading h2 { margin: 0; font-size: 16px; line-height: 24px; font-weight: 600; }
.dsh-tools-heading p { margin: 1px 0 0; color: var(--muted-foreground); font-size: 12px; line-height: 18px; }
.dsh-tools-close { display: grid; width: 32px; height: 32px; flex: none; place-items: center; margin-left: auto; border: 0; border-radius: 6px; color: var(--muted-foreground); background: transparent; cursor: pointer; }
.dsh-tools-close:hover { color: var(--foreground); background: var(--secondary); }
.dsh-tools-layout { display: grid; grid-template-columns: 204px minmax(0, 1fr); height: calc(100% - 72px); min-height: 0; }
.dsh-tools-nav { display: flex; flex-direction: column; gap: 5px; min-width: 0; min-height: 0; padding: 16px 10px; overflow: auto; border-right: 1px solid var(--studio-border); background: color-mix(in srgb, var(--secondary) 42%, var(--studio-panel)); scrollbar-width: thin; }
.dsh-tools-nav button { display: flex; width: 100%; min-height: 44px; flex: none; align-items: center; gap: 10px; border: 0; border-radius: 6px; padding: 10px; background: transparent; color: var(--muted-foreground); font: inherit; font-size: 13px; line-height: 20px; text-align: left; cursor: pointer; transition: background-color 160ms ease-out, color 160ms ease-out; }
.dsh-tools-nav button > span { min-width: 0; overflow-wrap: anywhere; }
.dsh-tools-nav button:hover { background: var(--secondary); color: var(--foreground); }
.dsh-tools-nav button[aria-selected="true"] { background: var(--studio-hover); color: var(--foreground); font-weight: 600; }
.dsh-tools-nav button[aria-selected="true"] > svg:first-child { color: var(--accent); }
.dsh-tools-count { display: grid; min-width: 18px; height: 18px; place-items: center; border-radius: 4px; background: var(--studio-hover); color: var(--foreground); font-size: 11px; }
.dsh-tools-chevron { margin-left: auto; }
.dsh-tools-panel svg { width: 16px; height: 16px; flex: none; }
.dsh-tools-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.dsh-tools-toolbar { display: flex; flex: none; align-items: center; gap: 10px; padding: 18px 20px 10px; }
.dsh-tools-search { display: flex; flex: 1; align-items: center; min-width: 0; height: 38px; gap: 9px; padding: 0 10px; border: 1px solid transparent; border-radius: 6px; background: var(--secondary); color: var(--muted-foreground); }
.dsh-tools-search:focus-within { border-color: var(--ring); }
.dsh-tools-search input { width: 100%; min-width: 0; padding: 0; border: 0; outline: 0; background: transparent; color: var(--foreground); font: inherit; font-size: 13px; line-height: 20px; }
.dsh-tools-search input::placeholder { color: var(--muted-foreground); }
.dsh-tools-search input::-webkit-search-cancel-button { display: none; }
.dsh-tools-search button { display: grid; width: 24px; height: 24px; flex: none; place-items: center; border: 0; border-radius: 4px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-tools-search button:hover { color: var(--foreground); }
.dsh-tools-toolbar :deep(.composer-select) { max-width: 160px; flex: none; }
.dsh-tools-toolbar :deep(.composer-select__trigger) { grid-template-columns: minmax(0, 1fr) auto; height: 38px; border-radius: 6px; background: var(--studio-panel); color: var(--foreground); border-color: var(--studio-border); }
.dsh-tools-panel .dsh-tools-toolbar :deep(.composer-select__menu) { border-radius: 6px; border-color: var(--studio-border); background: var(--studio-panel); color: var(--foreground); }
.dsh-tools-content { flex: 1; min-width: 0; min-height: 0; overflow: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-gutter: stable; padding: 14px 14px 18px; }
.dsh-tools-list-heading { display: flex; min-height: 24px; align-items: center; justify-content: space-between; gap: 12px; margin: 0 10px 10px; color: var(--muted-foreground); font-size: 12px; line-height: 18px; }
.dsh-tools-list-heading h3 { display: flex; align-items: center; gap: 8px; margin: 0; color: var(--foreground); font-size: 14px; line-height: 20px; font-weight: 600; }
.dsh-tools-list-heading h3 span { color: var(--muted-foreground); font-weight: 400; font-size: 12px; }
.dsh-tools-selection { white-space: nowrap; }
.dsh-tools-current { margin: -3px 10px 12px; color: var(--muted-foreground); font-size: 12px; overflow-wrap: anywhere; }
.dsh-tool-action { display: flex; width: 100%; min-height: 80px; align-items: flex-start; gap: 14px; margin: 0 0 5px; padding: 14px 10px; border: 0; border-radius: 6px; background: transparent; color: var(--foreground); text-align: left; font: inherit; cursor: pointer; transition: background-color 160ms ease-out; }
.dsh-tool-icon { display: grid; width: 42px; height: 42px; flex: none; place-items: center; border-radius: 8px; background: var(--studio-hover); color: var(--accent); }
.dsh-tool-icon.is-research { background: color-mix(in srgb, #159b84 12%, var(--studio-panel)); color: color-mix(in srgb, #159b84 75%, var(--foreground)); }
.dsh-tool-icon.is-file { background: color-mix(in srgb, #b68022 12%, var(--studio-panel)); color: color-mix(in srgb, #b68022 75%, var(--foreground)); }
.dsh-tool-icon.is-expert { background: color-mix(in srgb, #8b63d0 12%, var(--studio-panel)); color: color-mix(in srgb, #8b63d0 75%, var(--foreground)); }
.dsh-tool-icon svg { width: 21px; height: 21px; }
.dsh-tool-copy { display: flex; flex-direction: column; min-width: 0; flex: 1; gap: 5px; }
.dsh-tool-action strong { font-size: 14px; line-height: 21px; font-weight: 600; overflow-wrap: anywhere; }
.dsh-tool-action small { color: var(--muted-foreground); font-size: 12px; line-height: 19px; overflow-wrap: anywhere; }
.dsh-tool-source { display: flex; align-items: flex-start; gap: 5px; color: var(--muted-foreground); font-size: 11px; line-height: 17px; }
.dsh-tool-source > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dsh-tool-source svg { width: 13px; height: 13px; margin-top: 2px; color: var(--accent); }
.dsh-tool-state { display: flex; flex: none; align-items: center; justify-content: center; gap: 5px; min-width: 76px; min-height: 32px; padding: 4px 8px; border: 1px solid var(--studio-border); border-radius: 6px; color: var(--muted-foreground); font-size: 12px; line-height: 18px; }
.dsh-tool-action[aria-pressed="true"] .dsh-tool-state { border-color: transparent; background: var(--studio-hover); color: var(--foreground); }
.dsh-tool-action[aria-pressed="true"] .dsh-tool-state svg { color: var(--accent); }
.dsh-tool-action:hover:not(:disabled) { background: color-mix(in srgb, var(--secondary) 65%, transparent); }
.dsh-tool-action:disabled { opacity: .5; cursor: not-allowed; }
.dsh-tools-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; min-height: 150px; padding: 20px; color: var(--muted-foreground); text-align: center; }
.dsh-tools-empty > svg { width: 24px; height: 24px; }
.dsh-tools-empty strong { color: var(--foreground); font-size: 13px; line-height: 20px; font-weight: 500; }
.dsh-tools-empty button { border: 0; padding: 5px 8px; border-radius: 4px; background: var(--secondary); color: var(--foreground); font: inherit; font-size: 12px; cursor: pointer; }
.dsh-tools-panel button:focus-visible { outline: 2px solid var(--ring); outline-offset: -2px; }
@starting-style {
  .dsh-tools-panel:popover-open { opacity: 0; transform: translateY(-6px) scale(.985); }
  .dsh-tools-panel[data-placement="top"]:popover-open { transform: translateY(6px) scale(.985); }
}
@container (max-width: 600px) {
  .dsh-tools-heading { height: 62px; padding: 10px 14px; }
  .dsh-tools-brand { width: 32px; height: 32px; }
  .dsh-tools-heading h2 { font-size: 14px; line-height: 21px; }
  .dsh-tools-heading p { display: none; }
  .dsh-tools-layout { height: calc(100% - 62px); }
  .dsh-tools-layout { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); }
  .dsh-tools-nav { flex-direction: row; gap: 4px; padding: 6px; border-right: 0; border-bottom: 1px solid var(--studio-border); }
  .dsh-tools-nav button { width: auto; min-height: 36px; gap: 7px; padding: 8px; white-space: nowrap; font-size: 12px; }
  .dsh-tools-chevron { display: none; }
  .dsh-tools-toolbar { gap: 6px; padding: 10px 10px 4px; }
  .dsh-tools-search { gap: 6px; padding: 0 8px; }
  .dsh-tools-search input { font-size: 12px; }
  .dsh-tools-toolbar :deep(.composer-select) { max-width: 116px; }
  .dsh-tools-content { padding: 10px 6px; }
  .dsh-tools-list-heading { margin: 0 8px 6px; }
  .dsh-tool-action { gap: 10px; padding: 12px 8px; }
  .dsh-tool-icon { width: 34px; height: 34px; border-radius: 6px; }
  .dsh-tool-icon svg { width: 18px; height: 18px; }
  .dsh-tool-state { min-width: 32px; width: 32px; height: 32px; padding: 0; }
  .dsh-tool-state > span { display: none; }
  .dsh-tool-action strong { font-size: 13px; line-height: 20px; }
}
@container (max-width: 440px) { .dsh-upload-button { grid-column: 1; grid-row: 2; } }
@media (pointer: coarse) { .dsh-upload-button { width: 44px; height: 44px; } .dsh-tools-nav button { min-height: 44px; } }
@media (prefers-reduced-motion: reduce) { .dsh-upload-button, .dsh-upload-button svg, .dsh-tools-panel, .dsh-tools-nav button, .dsh-tool-action { transition: none; } }
</style>
