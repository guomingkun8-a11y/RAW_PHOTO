<script setup lang="ts">
import { Check, ChevronDown } from '@lucide/vue';
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue';

export interface CanvasSelectOption {
  value: string;
  label: string;
  group?: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<{
  modelValue: string;
  options: CanvasSelectOption[];
  label?: string;
  ariaLabel?: string;
  placeholder?: string;
  variant?: 'toolbar' | 'field' | 'action';
  disabled?: boolean;
  menuMinWidth?: number;
  menuMaxWidth?: number;
}>(), {
  label: '',
  ariaLabel: '',
  placeholder: '请选择',
  variant: 'toolbar',
  disabled: false,
  menuMinWidth: 144,
  menuMaxWidth: 280,
});

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const triggerRef = ref<HTMLButtonElement>();
const menuRef = ref<HTMLElement>();
const isOpen = ref(false);
const focusedIndex = ref(-1);
const placement = ref<'top' | 'bottom'>('bottom');
const menuStyle = ref<Record<string, string>>({});
const menuTarget = shallowRef<string | HTMLElement>('body');
const popoverId = `canvas-select-${Math.random().toString(36).slice(2, 10)}`;
const menuThemeVariables = [
  '--ink',
  '--muted',
  '--muted-strong',
  '--line',
  '--floating-surface',
  '--shadow',
  '--surface',
  '--surface-hover',
  '--accent',
  '--accent-soft',
] as const;

const selectedOption = computed(() => props.options.find((option) => option.value === props.modelValue));
const groupCount = computed(() => props.options.reduce((count, option, index) => (
  option.group && option.group !== props.options[index - 1]?.group ? count + 1 : count
), 0));

function enabledIndex(start: number, step: 1 | -1) {
  if (!props.options.length) return -1;
  for (let offset = 0; offset < props.options.length; offset += 1) {
    const index = (start + offset * step + props.options.length) % props.options.length;
    if (!props.options[index]?.disabled) return index;
  }
  return -1;
}

function selectedIndex() {
  const index = props.options.findIndex((option) => option.value === props.modelValue && !option.disabled);
  return index >= 0 ? index : enabledIndex(0, 1);
}

function focusOption(index: number) {
  const normalized = enabledIndex(index, 1);
  if (normalized < 0) return;
  focusedIndex.value = normalized;
  void nextTick(() => {
    const option = menuRef.value?.querySelector<HTMLButtonElement>(`[data-option-index="${normalized}"]`);
    option?.focus({ preventScroll: true });
    option?.scrollIntoView({ block: 'nearest' });
  });
}

function positionMenu() {
  const trigger = triggerRef.value;
  if (!trigger || !isOpen.value) return;

  const triggerRect = trigger.getBoundingClientRect();
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const edge = 8;
  const gap = 6;
  const width = Math.min(
    Math.max(triggerRect.width, props.menuMinWidth),
    props.menuMaxWidth,
    viewportWidth - edge * 2,
  );
  const desiredHeight = Math.min(296, props.options.length * 34 + groupCount.value * 25 + 10);
  const spaceBelow = viewportHeight - triggerRect.bottom - gap - edge;
  const spaceAbove = triggerRect.top - gap - edge;
  const openAbove = spaceBelow < Math.min(desiredHeight, 180) && spaceAbove > spaceBelow;
  const availableHeight = openAbove ? spaceAbove : spaceBelow;
  const viewportCapacity = Math.max(48, viewportHeight - edge * 2);
  const menuHeight = Math.min(desiredHeight, Math.max(48, availableHeight), viewportCapacity);
  const rawTop = openAbove ? triggerRect.top - gap - menuHeight : triggerRect.bottom + gap;
  const top = Math.min(
    Math.max(edge, rawTop),
    Math.max(edge, viewportHeight - menuHeight - edge),
  );
  const left = Math.min(
    Math.max(edge, triggerRect.left),
    Math.max(edge, viewportWidth - width - edge),
  );
  const themeSource = trigger.closest<HTMLElement>('.infinite-canvas-root') || trigger;
  const themeStyle = window.getComputedStyle(themeSource);
  const inheritedTheme = Object.fromEntries(menuThemeVariables.map((property) => (
    [property, themeStyle.getPropertyValue(property)]
  )));

  placement.value = openAbove ? 'top' : 'bottom';
  menuStyle.value = {
    ...inheritedTheme,
    width: `${width}px`,
    maxHeight: `${menuHeight}px`,
    left: `${left}px`,
    top: `${top}px`,
    fontFamily: themeStyle.fontFamily,
    colorScheme: themeStyle.colorScheme,
  };
}

function addPositionListeners() {
  window.addEventListener('resize', positionMenu);
  window.addEventListener('scroll', positionMenu, true);
}

function removePositionListeners() {
  window.removeEventListener('resize', positionMenu);
  window.removeEventListener('scroll', positionMenu, true);
}

function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (triggerRef.value?.contains(target) || menuRef.value?.contains(target)) return;
  closeMenu(false);
}

function onWindowKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented || event.key !== 'Escape' || !isOpen.value) return;
  event.preventDefault();
  closeMenu();
}

function addDismissListeners() {
  document.addEventListener('pointerdown', onDocumentPointerDown, true);
  window.addEventListener('keydown', onWindowKeydown);
}

function removeDismissListeners() {
  document.removeEventListener('pointerdown', onDocumentPointerDown, true);
  window.removeEventListener('keydown', onWindowKeydown);
}

function openMenu(preferredIndex = selectedIndex()) {
  if (!triggerRef.value || props.disabled || !props.options.length) return;
  if (isOpen.value) return;
  menuTarget.value = triggerRef.value?.closest<HTMLElement>('dialog') || document.body;
  isOpen.value = true;
  focusedIndex.value = preferredIndex;
  addPositionListeners();
  addDismissListeners();
  void nextTick(() => {
    positionMenu();
    requestAnimationFrame(() => focusOption(focusedIndex.value));
  });
}

function closeMenu(restoreFocus = true) {
  isOpen.value = false;
  removePositionListeners();
  removeDismissListeners();
  if (restoreFocus) void nextTick(() => triggerRef.value?.focus({ preventScroll: true }));
}

function toggleMenu() {
  if (isOpen.value) {
    closeMenu();
    return;
  }
  openMenu();
}

function selectOption(option: CanvasSelectOption) {
  if (option.disabled) return;
  emit('update:modelValue', option.value);
  closeMenu();
}

function moveFocus(step: 1 | -1) {
  const start = focusedIndex.value < 0 ? selectedIndex() : focusedIndex.value + step;
  focusOption(enabledIndex(start, step));
}

function onTriggerKeydown(event: KeyboardEvent) {
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
  event.preventDefault();
  const current = selectedIndex();
  openMenu(event.key === 'ArrowDown' ? current : enabledIndex(current - 1, -1));
}

function onMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    moveFocus(1);
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    moveFocus(-1);
  } else if (event.key === 'Home') {
    event.preventDefault();
    focusOption(enabledIndex(0, 1));
  } else if (event.key === 'End') {
    event.preventDefault();
    focusOption(enabledIndex(props.options.length - 1, -1));
  } else if (event.key === 'Escape') {
    event.preventDefault();
    closeMenu();
  }
}

watch(() => [props.modelValue, props.options.length], () => {
  if (isOpen.value) void nextTick(positionMenu);
});

watch(() => props.disabled, (disabled) => {
  if (disabled) closeMenu(false);
});

onBeforeUnmount(() => {
  removePositionListeners();
  removeDismissListeners();
});
</script>

<template>
  <div class="canvas-select" :class="[`canvas-select--${variant}`, { 'is-disabled': disabled }]">
    <button
      ref="triggerRef"
      type="button"
      class="canvas-select__trigger"
      :class="{ 'is-open': isOpen }"
      :disabled="disabled"
      :aria-label="ariaLabel || label"
      :aria-controls="popoverId"
      :aria-expanded="isOpen"
      :data-value="modelValue"
      aria-haspopup="listbox"
      @pointerdown.stop
      @click.stop="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span class="canvas-select__content">
        <slot name="prefix" />
        <template v-if="variant === 'action'">
          <span class="canvas-select__value">{{ label || placeholder }}</span>
        </template>
        <template v-else>
          <span v-if="label" class="canvas-select__label">{{ label }}</span>
          <span class="canvas-select__value">{{ selectedOption?.label || placeholder }}</span>
        </template>
      </span>
      <ChevronDown class="canvas-select__chevron" aria-hidden="true" />
    </button>

    <Teleport :to="menuTarget">
      <div
        v-if="isOpen"
        :id="popoverId"
        ref="menuRef"
        class="canvas-select__menu"
        :class="{ 'is-open': isOpen }"
        :data-placement="placement"
        :style="menuStyle"
        role="listbox"
        :aria-label="ariaLabel || label"
        @keydown="onMenuKeydown"
        @pointerdown.stop
        @click.stop
      >
        <template v-for="(option, index) in options" :key="`${option.group || 'option'}-${option.value}-${index}`">
          <div
            v-if="option.group && option.group !== options[index - 1]?.group"
            class="canvas-select__group"
            role="presentation"
          >{{ option.group }}</div>
          <button
            type="button"
            class="canvas-select__option"
            :class="{ 'is-selected': option.value === modelValue }"
            :disabled="option.disabled"
            role="option"
            :aria-selected="option.value === modelValue"
            :data-option-index="index"
            :data-value="option.value"
            @click="selectOption(option)"
            @focus="focusedIndex = index"
          >
            <span>{{ option.label }}</span>
            <Check v-if="option.value === modelValue" class="canvas-select__check" aria-hidden="true" />
          </button>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.canvas-select {
  min-width: 0;
  flex: 0 0 auto;
  color: var(--ink);
}

.canvas-select__trigger {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 5px;
  border: 1px solid transparent;
  color: var(--ink);
  cursor: pointer;
  text-align: left;
  transition: border-color 160ms ease-out, background-color 160ms ease-out, color 160ms ease-out;
}

.canvas-select__content {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
}

.canvas-select__content > svg {
  flex: 0 0 auto;
  color: var(--muted);
}

.canvas-select--toolbar .canvas-select__trigger {
  height: 31px;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--surface-raised);
}

.canvas-select--field {
  width: 100%;
}

.canvas-select--field .canvas-select__trigger {
  width: 100%;
  height: 34px;
  padding: 0 9px;
  border-color: var(--line);
  border-radius: 5px;
  background: var(--canvas-bg);
}

.canvas-select--action .canvas-select__trigger {
  height: 26px;
  padding: 0 7px;
  border-radius: 4px;
  background: transparent;
  color: var(--muted-strong);
}

.canvas-select__trigger:hover:not(:disabled) {
  border-color: var(--line);
  background: var(--surface-hover);
  color: var(--ink);
}

.canvas-select__trigger:focus-visible,
.canvas-select__trigger.is-open {
  border-color: var(--accent-border);
  outline: 2px solid var(--accent-border-soft);
  outline-offset: 1px;
  background: var(--surface);
}

.canvas-select__trigger:disabled {
  cursor: not-allowed;
  opacity: 0.38;
}

.canvas-select__label {
  color: var(--muted);
  font-size: 9px;
  line-height: 1;
  white-space: nowrap;
}

.canvas-select__value {
  min-width: 0;
  max-width: var(--canvas-select-value-max-width, 78px);
  overflow: hidden;
  color: currentColor;
  font-size: 10px;
  font-weight: 650;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.canvas-select--field .canvas-select__value {
  max-width: none;
  font-size: 12px;
}

.canvas-select--action .canvas-select__value {
  max-width: 100px;
  font-weight: 500;
}

.canvas-select__chevron {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  color: var(--muted);
  transition: transform 160ms cubic-bezier(0.22, 1, 0.36, 1);
}

.canvas-select__trigger.is-open .canvas-select__chevron {
  transform: rotate(180deg);
}

.canvas-select__menu {
  position: fixed;
  z-index: 30;
  inset: auto;
  display: grid;
  gap: 2px;
  margin: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 5px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--floating-surface);
  color: var(--ink);
  box-shadow: 0 4px 8px var(--shadow);
  opacity: 0;
  pointer-events: none;
  scrollbar-color: var(--muted) transparent;
  scrollbar-width: thin;
  transform: translateY(-4px) scale(0.985);
  transform-origin: top center;
  transition: opacity 120ms ease-out, transform 120ms cubic-bezier(0.22, 1, 0.36, 1);
}

.canvas-select__menu,
.canvas-select__menu * {
  box-sizing: border-box;
}

.canvas-select__menu[data-placement='top'] {
  transform: translateY(4px) scale(0.985);
  transform-origin: bottom center;
}

.canvas-select__menu.is-open {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0) scale(1);
}

.canvas-select__group {
  min-height: 24px;
  display: flex;
  align-items: center;
  padding: 5px 8px 2px;
  color: var(--muted);
  font-size: 9px;
  font-weight: 650;
}

.canvas-select__option {
  width: 100%;
  min-height: 32px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 0 8px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--muted-strong);
  cursor: pointer;
  font-family: inherit;
  font-size: 11px;
  text-align: left;
}

.canvas-select__option > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.canvas-select__option:hover:not(:disabled),
.canvas-select__option:focus-visible {
  outline: none;
  background: var(--surface-hover);
  color: var(--ink);
}

.canvas-select__option.is-selected {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 680;
}

.canvas-select__option:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.canvas-select__check {
  width: 13px;
  height: 13px;
  color: currentColor;
}

@media (prefers-reduced-motion: reduce) {
  .canvas-select__trigger,
  .canvas-select__chevron,
  .canvas-select__menu {
    transition-duration: 0.01ms;
  }
}
</style>
