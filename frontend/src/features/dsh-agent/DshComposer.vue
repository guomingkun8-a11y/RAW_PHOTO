<script setup lang="ts">
import { ArrowUp, FileText, LoaderCircle, Paintbrush, Square, Wrench, X } from "@lucide/vue";
import { computed, nextTick, ref, useId, watch } from "vue";
import ComposerSelect, { type ComposerSelectOption } from "@/components/ComposerSelect.vue";
import { formatImageModel } from "@/lib/image-models";
import DshModelLogo from "./DshModelLogo.vue";
import DshContext from "./DshContext.vue";
import DshSlashCommands from "./DshSlashCommands.vue";
import DshComposerMenu from "./DshComposerMenu.vue";
import type { DshImageMedia, DshImageModel, DshAgentModel, DshContextSnapshot, DshExpert, DshSkill, DshConnector } from "./api";
import type { DshUpload } from "./useDshImages";

const props = defineProps<{
  modelValue: string;
  model: string;
  options: ComposerSelectOption[];
  disabled: boolean;
  sending: boolean;
  canStop?: boolean;
  stopping?: boolean;
  savingModel: boolean;
  imageModels?: DshImageModel[];
  imageModel: string;
  references: DshImageMedia[];
  uploading: boolean;
  uploads?: DshUpload[];
  filesEnabled?: boolean;
  contextModel?: DshAgentModel;
  context?: DshContextSnapshot | null;
  teamsEnabled?: boolean;
  collaboration?: boolean;
  experts?: DshExpert[];
  expertId?: string;
  skills?: DshSkill[];
  skillIds?: string[];
  connectors?: DshConnector[];
  welcome?: boolean;
  searchEnabled?: boolean;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
  "selectModel": [model: string];
  "send": [];
  "stop": [];
  "upload": [files: File[]];
  "removeReference": [item: DshImageMedia];
  "dismissUpload": [id: number];
  "update:imageModel": [model: string];
  "update:collaboration": [enabled: boolean];
  "update:expertId": [id: string];
  "update:skillIds": [ids: string[]];
}>();
const fileInput = ref<HTMLInputElement>();
const textarea = ref<HTMLTextAreaElement>();
const slashCommands = ref<InstanceType<typeof DshSlashCommands>>();
const slashMenuId = useId();
const documentInput = ref<HTMLInputElement>();
const dragDepth = ref(0);
const uploadDisabled = computed(() => props.disabled || props.uploading || (!props.imageModels?.length && !props.filesEnabled));
const imageDisabled = computed(() => uploadDisabled.value || props.references.filter(item => item.kind === "reference").length >= 6);
const fileDisabled = computed(() => uploadDisabled.value || props.references.filter(item => item.kind === "file").length >= 4);
const selectedExpert = computed(() => props.experts?.find(item => item.id === props.expertId));
const selectedSkills = computed(() => (props.skills || []).filter(item => props.skillIds?.includes(item.id)));
const imageModelOptions = computed(() => (props.imageModels || []).map(({ id }) => ({
  value: id,
  label: formatImageModel(id).replace(/^gpt-image-/, "GPT Image ").replace(/\b(flare|sunburst)\b/g, name => name[0]!.toUpperCase() + name.slice(1)),
})));
const selectedImageLabel = computed(() => imageModelOptions.value.find(option => option.value === props.imageModel)?.label);
const modelCategory = ref<"chat" | "image">("chat");
const selectedChatLabel = computed(() => props.options.find(option => option.value === props.model)?.label || props.model);
const modelMenuOptions = computed(() => modelCategory.value === "image" ? imageModelOptions.value : props.options);
const modelSummary = computed(() => `对话：${selectedChatLabel.value}${props.imageModels?.length ? `；生图：${selectedImageLabel.value || props.imageModel}` : ""}`);

function selectMenuModel(value: string) {
  if (modelCategory.value === "image") emit("update:imageModel", value);
  else emit("selectModel", value);
}

function onModelCategoryKeydown(event: KeyboardEvent) {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    modelCategory.value = event.key === "Home" ? "chat" : event.key === "End" ? "image" : modelCategory.value === "chat" ? "image" : "chat";
    (event.currentTarget as HTMLElement).querySelector<HTMLButtonElement>(`[data-category="${modelCategory.value}"]`)?.focus();
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    const menu = (event.currentTarget as HTMLElement).closest(".composer-select__menu");
    (menu?.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]') || menu?.querySelector<HTMLButtonElement>('[role="option"]:not(:disabled)'))?.focus();
  }
}

watch(() => props.imageModels?.length, count => { if (!count) modelCategory.value = "chat"; });

async function focusInput() { await nextTick(); textarea.value?.focus({ preventScroll: true }); }

function onToolAction(action: "image" | "file" | "generate" | "search") {
  if (action === "image") { fileInput.value?.click(); return; }
  if (action === "file") { documentInput.value?.click(); return; }
  const prefix = action === "generate" ? "请生成图片：" : "请联网搜索并核对资料：";
  if (!props.modelValue.startsWith(prefix)) emit("update:modelValue", prefix + props.modelValue);
  void focusInput();
}

function selectExpert(id: string) { emit("update:expertId", id); void focusInput(); }
function toggleSkill(id: string, focus = true) {
  const ids = props.skillIds || [];
  emit("update:skillIds", ids.includes(id) ? ids.filter(item => item !== id) : [...ids, id]);
  if (focus) void focusInput();
}
function selectCollaboration(enabled: boolean) { emit("update:collaboration", enabled); void focusInput(); }

function uploadFiles(files: File[]) {
  if (!uploadDisabled.value && files.length) emit("upload", files);
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  uploadFiles(Array.from(input.files || []));
  input.value = "";
}

function onPaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || []);
  if (!files.length) return;
  event.preventDefault();
  uploadFiles(files);
}

function onDrag(event: DragEvent) {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = uploadDisabled.value ? "none" : "copy";
  if (event.type === "dragenter" && !uploadDisabled.value) dragDepth.value++;
}

function onDrop(event: DragEvent) {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  dragDepth.value = 0;
  uploadFiles(Array.from(event.dataTransfer.files));
}

watch(uploadDisabled, () => { dragDepth.value = 0; });

function onKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  if (slashCommands.value?.onKeydown(event)) return;
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    emit("send");
  }
}
</script>

<template>
  <div
    class="dsh-composer"
    :class="{ 'is-dragging': dragDepth > 0, 'is-welcome': welcome }"
    @paste="onPaste"
    @dragenter="onDrag"
    @dragover="onDrag"
    @dragleave="dragDepth = Math.max(0, dragDepth - 1)"
    @drop="onDrop"
  >
    <div v-if="references.length || uploads?.length" class="dsh-upload-previews">
      <div v-for="item in references" :key="item.media_id" class="dsh-attachment">
        <img v-if="item.kind === 'reference'" :src="item.url" :alt="item.filename || '参考图片'" /><FileText v-else class="dsh-attachment-icon size-6" />
        <div class="dsh-attachment-copy"><strong :title="item.filename">{{ item.filename || '参考图片' }}</strong><small>{{ item.size_bytes ? `${Math.max(1, Math.round(item.size_bytes / 1024))} KB` : '图片' }}<span v-if="item.truncated"> · 内容已截取</span></small></div>
        <button type="button" :disabled="disabled || uploading" :aria-label="item.kind === 'file' ? '移除文件' : '移除参考图片'" title="移除附件" @click="emit('removeReference', item)"><X class="size-3" /></button>
      </div>
      <div v-for="item in uploads" :key="item.id" class="dsh-attachment" :class="{ 'has-error': item.error }" :role="item.error ? 'alert' : 'status'">
        <FileText v-if="item.error" class="dsh-attachment-icon size-6" /><LoaderCircle v-else class="dsh-attachment-icon size-6 animate-spin" />
        <div class="dsh-attachment-copy"><strong :title="item.filename">{{ item.filename }}</strong><small :title="item.error">{{ item.error || '正在上传并读取…' }}</small></div>
        <button v-if="item.error" type="button" aria-label="移除失败附件" @click="emit('dismissUpload', item.id)"><X class="size-3" /></button>
      </div>
    </div>
    <div v-if="selectedExpert || selectedSkills.length" class="dsh-capability-chips">
    <div v-if="selectedExpert" class="dsh-expert-chip dsh-capability-chip" :title="selectedExpert.description">
      <Paintbrush class="size-3.5" /><span>{{ selectedExpert.label }}</span>
      <button type="button" :disabled="disabled" aria-label="移除电商设计专家" title="移除专家" @click="emit('update:expertId', '')"><X class="size-3.5" /></button>
    </div>
    <div v-for="skill in selectedSkills" :key="skill.id" class="dsh-skill-chip dsh-capability-chip" :title="skill.description">
      <Wrench class="size-3.5" /><span>{{ skill.label }}</span>
      <button type="button" :disabled="disabled" :aria-label="`移除技能 ${skill.label}`" title="移除技能" @click="toggleSkill(skill.id)"><X class="size-3.5" /></button>
    </div>
    </div>
    <textarea
      ref="textarea"
      :value="modelValue"
      maxlength="12000"
      rows="2"
      :disabled="disabled"
      :placeholder="welcome ? '描述你想完成的任务…' : '发送消息，描述你的问题或任务'"
      aria-label="发送消息"
      aria-autocomplete="list"
      :aria-controls="slashCommands?.isOpen ? slashMenuId : undefined"
      :aria-activedescendant="slashCommands?.activeId"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      @keydown="onKeydown"
    />
    <DshSlashCommands ref="slashCommands" :menu-id="slashMenuId" :input="textarea" :model-value="modelValue" :disabled="disabled" :experts="experts" :image-enabled="!!imageModels?.length" :teams-enabled="teamsEnabled" @update:model-value="emit('update:modelValue', $event)" @expert="emit('update:expertId', $event)" @collaboration="emit('update:collaboration', $event)" />
    <div class="dsh-composer-actions">
      <div class="dsh-composer-tools">
        <input v-if="imageModels?.length" ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden :disabled="imageDisabled" aria-label="上传参考图片" @change="onFileChange" />
        <input v-if="filesEnabled" ref="documentInput" type="file" accept=".txt,.md,.csv,.json,.log,.pdf,.docx" multiple hidden :disabled="fileDisabled" aria-label="上传文件" @change="onFileChange" />
        <DshComposerMenu v-if="imageModels?.length || filesEnabled || experts?.length || skills?.length || connectors?.length || teamsEnabled || searchEnabled" :disabled="disabled || uploading" :image-enabled="!!imageModels?.length" :image-disabled="imageDisabled" :files-enabled="filesEnabled" :file-disabled="fileDisabled" :image-model="imageModel" :experts="experts" :expert-id="expertId" :skills="skills" :skill-ids="skillIds" :connectors="connectors" :teams-enabled="teamsEnabled" :collaboration="collaboration" :search-enabled="searchEnabled" @action="onToolAction" @expert="selectExpert" @skill="id => toggleSkill(id, false)" @collaboration="selectCollaboration" />
        <div class="dsh-composer-models">
          <ComposerSelect v-if="teamsEnabled" :model-value="collaboration ? 'teams' : 'normal'" label="" aria-label="对话模式" :options="[{ value: 'normal', label: '普通对话' }, { value: 'teams', label: '多智能体协作' }]" :disabled="disabled" @update:model-value="emit('update:collaboration', $event === 'teams')" />
          <ComposerSelect
            class="dsh-composer-model"
            :model-value="modelCategory === 'image' ? imageModel : model"
            label=""
            aria-label="模型选择"
            :title="modelSummary"
            :options="modelMenuOptions"
            :menu-min-width="264"
            :disabled="disabled"
            @update:model-value="selectMenuModel"
          >
            <template v-if="imageModels?.length" #header>
              <div class="dsh-model-categories" role="tablist" aria-label="模型类型" @keydown="onModelCategoryKeydown">
                <button type="button" role="tab" data-category="chat" :aria-selected="modelCategory === 'chat'" :tabindex="modelCategory === 'chat' ? 0 : -1" @click="modelCategory = 'chat'">对话模型</button>
                <button type="button" role="tab" data-category="image" :aria-selected="modelCategory === 'image'" :tabindex="modelCategory === 'image' ? 0 : -1" @click="modelCategory = 'image'">生图模型</button>
              </div>
            </template>
            <template #value>
              <span class="dsh-model-option">
                <LoaderCircle v-if="savingModel" class="dsh-model-saving animate-spin" role="status" aria-label="正在保存模型" />
                <DshModelLogo v-else :model="model" />
                <span>{{ selectedChatLabel || '选择模型' }}</span>
              </span>
            </template>
            <template #option="{ option }">
              <span class="dsh-model-option"><DshModelLogo :model="option.value" /><span>{{ option.label }}</span></span>
            </template>
          </ComposerSelect>
        </div>
      </div>
      <div class="dsh-composer-submit">
        <DshContext :model="contextModel" :snapshot="context" :draft="modelValue" :images="references.filter(item => item.kind === 'reference').length" :file-chars="references.reduce((sum, item) => sum + (item.text_chars || 0), 0)" />
        <button v-if="canStop" class="dsh-composer-send dsh-composer-stop" type="button" :disabled="stopping" :aria-label="stopping ? '正在停止' : '停止回答'" :title="stopping ? '正在停止' : '停止回答'" @click="emit('stop')"><LoaderCircle v-if="stopping" class="size-4 animate-spin" /><Square v-else class="size-3.5" fill="currentColor" /></button>
        <button v-else class="dsh-composer-send" type="button" :disabled="disabled || uploading || (!modelValue.trim() && !references.length)" aria-label="发送" title="发送" @click="emit('send')">
          <LoaderCircle v-if="sending" class="size-4 animate-spin" /><ArrowUp v-else class="size-5" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dsh-composer { container-type: inline-size; min-width: 0; border: 1px solid var(--input); border-radius: 16px; background: var(--studio-panel); padding: 12px; transition: border-color 160ms ease-out, box-shadow 160ms ease-out; }
.dsh-composer.is-welcome { padding: 16px 14px 12px; box-shadow: 0 2px 8px rgb(0 0 0 / .025); }
.dsh-composer:focus-within { border-color: var(--ring); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 10%, transparent); }
.dsh-composer.is-dragging { border-color: var(--accent); background: var(--studio-hover); box-shadow: none; }
.dsh-composer textarea { display: block; width: 100%; min-height: 52px; max-height: 180px; field-sizing: content; resize: none; border: 0; outline: 0; background: transparent; padding: 0 4px 8px; color: var(--foreground); font: inherit; font-size: 14px; line-height: 1.6; }
.dsh-composer textarea::placeholder { color: var(--muted-foreground); }
.dsh-composer.is-welcome textarea { min-height: 72px; font-size: 16px; }
.dsh-composer-actions { display: grid; grid-template-columns: minmax(0, 1fr) auto; min-width: 0; align-items: end; gap: 8px; }
.dsh-composer-submit { display: flex; align-items: center; gap: 4px; }
.dsh-composer-tools { display: flex; min-width: 0; flex-wrap: wrap; align-items: flex-start; gap: 8px; }
.dsh-capability-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 4px 10px; }
.dsh-capability-chip { display: flex; width: fit-content; max-width: 100%; align-items: center; gap: 6px; padding: 3px 4px 3px 8px; border: 1px solid var(--input); border-radius: 6px; background: var(--secondary); color: var(--foreground); font-size: 12px; }
.dsh-capability-chip span { min-width: 0; overflow-wrap: anywhere; }
.dsh-capability-chip > svg { flex: none; }
.dsh-capability-chip button { display: grid; width: 28px; height: 28px; flex: none; place-items: center; border: 0; border-radius: 4px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-capability-chip button:hover:not(:disabled) { background: var(--studio-hover); color: var(--foreground); }
.dsh-capability-chip button:focus-visible { outline: 2px solid var(--ring); }
.dsh-capability-chip button:disabled { cursor: not-allowed; opacity: .5; }
.dsh-upload-previews { display: flex; gap: 8px; overflow-x: auto; padding: 0 1px 10px; scrollbar-width: thin; }
.dsh-attachment { position: relative; display: flex; align-items: center; gap: 8px; width: 208px; min-width: 0; min-height: 58px; flex: none; padding: 7px 28px 7px 7px; border: 1px solid var(--input); border-radius: 8px; }
.dsh-upload-previews img { width: 40px; height: 40px; flex: none; object-fit: cover; border-radius: 4px; }
.dsh-attachment-icon { flex: none; margin: 8px; color: var(--muted-foreground); }
.dsh-attachment-copy { display: grid; min-width: 0; gap: 4px; }
.dsh-attachment-copy strong, .dsh-attachment-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dsh-attachment-copy strong { color: var(--foreground); font-size: 12px; font-weight: 500; }
.dsh-attachment-copy small { color: var(--muted-foreground); font-size: 10px; }
.dsh-upload-previews button { position: absolute; right: 3px; top: 3px; display: grid; width: 24px; height: 24px; place-items: center; border: 0; border-radius: 4px; background: transparent; color: var(--muted-foreground); cursor: pointer; }
.dsh-upload-previews button:hover:not(:disabled) { background: var(--studio-hover); color: var(--foreground); }
.dsh-upload-previews button:focus-visible { outline: 2px solid var(--ring); }
.dsh-attachment.has-error { border-color: var(--destructive); }
.dsh-attachment.has-error small { color: var(--destructive); white-space: normal; overflow-wrap: anywhere; }
.dsh-model-saving { width: 15px; height: 15px; flex: none; color: var(--muted-foreground); }
.dsh-composer-models { display: flex; min-width: 0; flex: 1 1 160px; flex-wrap: wrap; align-items: flex-start; gap: 6px; }
.dsh-composer-models :deep(.composer-select) { min-width: 0; max-width: 100%; }
.dsh-composer-model { width: max-content; max-width: 100%; min-width: 0; flex: 0 1 auto; }
.dsh-composer-models :deep(.composer-select__trigger) { display: flex; min-height: 36px; height: auto; align-items: center; gap: 6px; border: 0; border-radius: 6px; background: transparent; padding: 8px; color: var(--foreground); box-shadow: none; text-align: left; }
.dsh-composer-models :deep(.composer-select__trigger:hover:not(:disabled)), .dsh-composer-models :deep(.composer-select__trigger.is-open) { background: var(--studio-hover); }
.dsh-composer-models :deep(.composer-select__trigger:focus-visible) { outline: 2px solid var(--ring); outline-offset: 1px; }
.dsh-composer-models :deep(.composer-select__trigger:disabled) { cursor: not-allowed; opacity: .6; }
.dsh-composer-models :deep(.composer-select__value) { display: flex; flex: 1; overflow: visible; font-size: 12px; font-weight: 500; }
.dsh-model-option { display: flex; min-width: 0; align-items: center; gap: 6px; }
.dsh-model-option > span { min-width: 0; white-space: normal; overflow-wrap: anywhere; line-height: 1.4; }
.dsh-composer-model :deep(.composer-select__menu) { border-color: var(--studio-border); border-radius: 8px; background: var(--studio-panel); color: var(--foreground); box-shadow: var(--studio-shadow); }
.dsh-model-categories { display: flex; gap: 4px; margin-bottom: 5px; padding: 3px 3px 8px; border-bottom: 1px solid var(--studio-border); }
.dsh-model-categories button { flex: 1; min-height: 34px; border: 0; border-radius: 5px; background: transparent; color: var(--muted-foreground); font: inherit; font-size: 12px; cursor: pointer; }
.dsh-model-categories button[aria-selected="true"] { background: var(--secondary); color: var(--foreground); font-weight: 600; }
.dsh-model-categories button:hover { background: var(--studio-hover); color: var(--foreground); }
.dsh-model-categories button:focus-visible { outline: 2px solid var(--ring); outline-offset: -2px; }
.dsh-composer-model :deep(.composer-select__option) { grid-template-columns: minmax(0, 1fr) auto; border-radius: 5px; color: var(--foreground); }
.dsh-composer-model :deep(.composer-select__chevron) { color: var(--muted-foreground); }
.dsh-composer-model :deep(.composer-select__option:hover), .dsh-composer-model :deep(.composer-select__option:focus-visible), .dsh-composer-model :deep(.composer-select__option.is-selected) { background: var(--studio-hover); color: color-mix(in srgb, var(--accent) 80%, var(--foreground)); }
.dsh-composer-send { display: grid; width: 36px; height: 36px; place-items: center; border: 0; border-radius: 50%; background: var(--accent); color: var(--accent-foreground); cursor: pointer; transition: background-color 160ms ease-out; }
.dsh-composer-send:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 85%, #000); }
.dsh-composer-send:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.dsh-composer-send:disabled { cursor: not-allowed; background: var(--secondary); color: var(--muted-foreground); opacity: .65; }
.dsh-composer-stop { background: var(--primary); color: var(--primary-foreground); }
.dsh-composer-stop:hover:not(:disabled) { background: var(--muted-foreground); }
.dsh-composer-stop:disabled { background: var(--muted-foreground); }
@media (pointer: coarse) {
  .dsh-composer-send { width: 44px; height: 44px; }
  .dsh-composer-models :deep(.composer-select__trigger) { min-height: 44px; }
}
@container (max-width: 440px) {
  .dsh-composer-tools { display: contents; }
  .dsh-composer-models { grid-column: 1 / -1; grid-row: 1; }
  .dsh-composer-submit { grid-column: 2; grid-row: 2; justify-self: end; }
}
@media (prefers-reduced-motion: reduce) { .dsh-composer, .dsh-composer-send { transition: none; } .animate-spin { animation: none; } }
</style>
