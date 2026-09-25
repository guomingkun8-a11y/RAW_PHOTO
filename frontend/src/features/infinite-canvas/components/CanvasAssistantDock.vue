<script setup lang="ts">
import { ArrowUp, LoaderCircle, Plus, Sparkles, X } from '@lucide/vue';
import type { ChatMessage } from '@/features/infinite-canvas/types';

defineProps<{
  open: boolean;
  chatEnabled: boolean;
  messages: ChatMessage[];
  input: string;
  sending: boolean;
}>();

defineEmits<{
  'update:open': [value: boolean];
  'update:input': [value: string];
  newChat: [];
  send: [];
  toggle: [];
}>();
</script>

<template>
  <div class="assistant-dock" :class="{ 'is-open': open }">
    <Transition name="assistant-panel">
      <section v-if="open" class="assistant-panel" aria-label="创意助手" @pointerdown.stop>
        <header class="assistant-header">
          <div class="assistant-identity">
            <span class="assistant-avatar"><Sparkles :size="18" aria-hidden="true" /></span>
            <div>
              <h2>创意助手</h2>
              <span><i :class="{ offline: !chatEnabled }" aria-hidden="true" /> 对话模型</span>
            </div>
          </div>
          <div class="assistant-header-actions">
            <button class="icon-button" type="button" title="新建对话" aria-label="新建对话" @click="$emit('newChat')"><Plus :size="17" /></button>
            <button class="icon-button" type="button" title="关闭助手" aria-label="关闭创意助手" @click="$emit('update:open', false)"><X :size="18" /></button>
          </div>
        </header>

        <div class="assistant-messages" aria-live="polite">
          <div v-if="!messages.length" class="assistant-welcome">
            <span class="assistant-welcome-icon"><Sparkles :size="21" aria-hidden="true" /></span>
            <h3>今天想创作什么？</h3>
            <p>告诉我一个想法，我来帮你整理图片或视频提示词。</p>
            <div class="assistant-starters">
              <button type="button" @click="$emit('update:input', '帮我把一个创意整理成图片生成提示词')">图片提示词</button>
              <button type="button" @click="$emit('update:input', '帮我设计一个短视频分镜和镜头提示词')">视频分镜</button>
            </div>
          </div>
          <article v-for="(message, index) in messages" :key="`${message.role}-${index}`" :class="['assistant-message', message.role]">
            <span v-if="message.role === 'assistant'" class="assistant-message-avatar"><Sparkles :size="13" aria-hidden="true" /></span>
            <div>
              <strong>{{ message.role === 'user' ? '你' : '创意助手' }}</strong>
              <p>{{ message.content }}</p>
            </div>
          </article>
          <div v-if="sending" class="assistant-thinking"><i /><i /><i /><span class="visually-hidden">助手正在输入</span></div>
        </div>

        <form class="assistant-composer" @submit.prevent="$emit('send')">
          <textarea
            :value="input"
            placeholder="输入你的创意..."
            aria-label="输入创意问题"
            maxlength="2000"
            rows="2"
            @input="$emit('update:input', ($event.target as HTMLTextAreaElement).value)"
            @keydown.enter.exact.prevent="$emit('send')"
          />
          <div class="assistant-composer-footer">
            <span>{{ input.length }}/2000</span>
            <button type="submit" :disabled="sending || !input.trim()" title="发送" aria-label="发送消息">
              <LoaderCircle v-if="sending" class="spin" :size="17" aria-hidden="true" />
              <ArrowUp v-else :size="18" aria-hidden="true" />
            </button>
          </div>
        </form>
      </section>
    </Transition>

    <button class="assistant-orb" :class="{ active: open }" type="button" :title="open ? '关闭创意助手' : '打开创意助手'" :aria-label="open ? '关闭创意助手' : '打开创意助手'" :aria-expanded="open" @click="$emit('toggle')">
      <X v-if="open" :size="21" aria-hidden="true" />
      <Sparkles v-else :size="23" aria-hidden="true" />
    </button>
  </div>
</template>
