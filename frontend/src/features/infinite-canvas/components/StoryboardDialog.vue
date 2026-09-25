<script setup lang="ts">
import { ref } from 'vue';
import { Clapperboard, Image as ImageIcon, LoaderCircle, Sparkles, X } from '@lucide/vue';

import { canvasApi } from '@/features/infinite-canvas/services/canvas-api';
import CanvasSelect from '@/features/infinite-canvas/components/CanvasSelect.vue';
import type { StoryboardScene } from '@/features/infinite-canvas/types';

const props = defineProps<{ open: boolean; workflowId?: string }>();

const emit = defineEmits<{
  close: [];
  addPrompt: [prompt: string];
  error: [message: string];
}>();

const story = ref('');
const sceneCount = ref(4);
const scripts = ref<StoryboardScene[]>([]);
const styleAnchor = ref('');
const loadingScripts = ref(false);
const sceneCountOptions = [3, 4, 6, 9].map((value) => ({
  value: String(value),
  label: `${value} 个`,
}));

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试。';
}

async function createScripts() {
  if (!story.value.trim()) {
    emit('error', '请先输入故事梗概。');
    return;
  }
  loadingScripts.value = true;
  try {
    const result = await canvasApi.storyboardScripts({
      story: story.value.trim(),
      sceneCount: sceneCount.value,
      workflowId: props.workflowId,
    });
    scripts.value = result.scripts;
    styleAnchor.value = result.styleAnchor;
  } catch (error) {
    emit('error', errorMessage(error));
  } finally {
    loadingScripts.value = false;
  }
}

function createComposite() {
  if (!scripts.value.length) return;
  const scenePrompts = scripts.value.map((scene) => [
    `分镜 ${scene.sceneNumber}`,
    scene.description,
    `景别：${scene.cameraAngle}`,
    `运镜：${scene.cameraMovement}`,
    `光线：${scene.lighting}`,
    `氛围：${scene.mood}`,
  ].join('，'));
  emit('addPrompt', [
    `生成一张 ${scripts.value.length} 格商业故事板，所有分镜按时间顺序排列。`,
    `统一视觉设定：${styleAnchor.value}`,
    ...scenePrompts,
    '保持人物、产品、服装、颜色和环境连续一致，每格清晰区分，不添加无关文字或水印。',
  ].join('\n'));
  emit('close');
}
</script>

<template>
  <dialog class="storyboard-dialog" :open="open" @click.self="emit('close')">
    <section class="storyboard-surface" aria-label="分镜生成器">
      <header class="dialog-header">
        <div>
          <span class="section-label"><Clapperboard :size="15" /> 分镜</span>
          <h2>故事板</h2>
        </div>
        <button class="icon-button" type="button" title="关闭" aria-label="关闭" @click="emit('close')"><X :size="18" /></button>
      </header>

      <div class="dialog-body">
        <label class="field-label" for="storyboard-story">故事梗概</label>
        <textarea id="storyboard-story" v-model="story" class="editor-textarea storyboard-story" placeholder="描述人物、转折与结尾" />
        <div class="storyboard-controls">
          <span class="field-label">镜头数量</span>
          <CanvasSelect
            id="scene-count"
            class="storyboard-scene-select"
            variant="field"
            :model-value="String(sceneCount)"
            :options="sceneCountOptions"
            aria-label="镜头数量"
            @update:model-value="sceneCount = Number($event)"
          />
          <button class="secondary-button" type="button" :disabled="loadingScripts" @click="createScripts">
            <LoaderCircle v-if="loadingScripts" class="spin" :size="17" />
            <Sparkles v-else :size="17" />
            生成镜头脚本
          </button>
        </div>

        <ol v-if="scripts.length" class="scene-list">
          <li v-for="scene in scripts" :key="scene.sceneNumber">
            <span>{{ scene.sceneNumber }}</span>
            <div>
              <strong>{{ scene.cameraAngle }}</strong>
              <p>{{ scene.description }}</p>
            </div>
          </li>
        </ol>
      </div>

      <footer class="dialog-footer">
        <button class="secondary-button" type="button" @click="emit('close')">取消</button>
        <button class="primary-button" type="button" :disabled="!scripts.length" @click="createComposite">
          <ImageIcon :size="17" />
          生成分镜图
        </button>
      </footer>
    </section>
  </dialog>
</template>
