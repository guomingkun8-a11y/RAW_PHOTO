<script setup lang="ts">
import { Check, ChevronDown } from "@lucide/vue";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

export type ComposerSelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

const props = withDefaults(defineProps<{
  modelValue: string;
  label: string;
  options: ComposerSelectOption[];
  ariaLabel?: string;
  variant?: "wide" | "compact";
}>(), {
  ariaLabel: "",
  variant: "compact",
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  change: [value: string];
}>();

const triggerRef = ref<HTMLButtonElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const isOpen = ref(false);
const focusedIndex = ref(-1);
const placement = ref<"top" | "bottom">("bottom");
const menuStyle = ref<Record<string, string>>({});
const popoverId = `composer-select-${Math.random().toString(36).slice(2, 10)}`;

const selectedOption = computed(() =>
  props.options.find((option) => option.value === props.modelValue) || props.options.find((option) => !option.disabled),
);

function enabledIndex(start: number, step: 1 | -1) {
  if (!props.options.length) return -1;
  for (let offset = 0; offset < props.options.length; offset += 1) {
    const index = (start + offset * step + props.options.length) % props.options.length;
    if (!props.options[index]?.disabled) return index;
  }
  return -1;
}

function focusOption(index: number) {
  const normalized = enabledIndex(index, 1);
  if (normalized < 0) return;
  focusedIndex.value = normalized;
  void nextTick(() => {
    menuRef.value
      ?.querySelector<HTMLButtonElement>(`[data-option-index="${normalized}"]`)
      ?.focus({ preventScroll: true });
  });
}

function selectedIndex() {
  const index = props.options.findIndex((option) => option.value === props.modelValue && !option.disabled);
  return index >= 0 ? index : enabledIndex(0, 1);
}

function positionMenu() {
  const trigger = triggerRef.value;
  const menu = menuRef.value;
  if (!trigger || !menu || !isOpen.value) return;

  const triggerRect = trigger.getBoundingClientRect();
  const edge = 8;
  const gap = 6;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const targetWidth = props.variant === "wide"
    ? triggerRect.width
    : Math.max(triggerRect.width, 152);
  const width = Math.min(targetWidth, viewportWidth - edge * 2);
  const desiredHeight = Math.min(Math.max(46, props.options.length * 38 + 10), 320);
  const spaceBelow = viewportHeight - triggerRect.bottom - gap - edge;
  const spaceAbove = triggerRect.top - gap - edge;
  const openAbove = spaceBelow < Math.min(desiredHeight, 180) && spaceAbove > spaceBelow;
  const availableHeight = Math.max(96, openAbove ? spaceAbove : spaceBelow);
  const height = Math.min(desiredHeight, availableHeight);
  const left = Math.min(
    Math.max(edge, triggerRect.left),
    Math.max(edge, viewportWidth - width - edge),
  );

  placement.value = openAbove ? "top" : "bottom";
  menuStyle.value = {
    width: `${width}px`,
    maxHeight: `${availableHeight}px`,
    left: `${left}px`,
    top: `${openAbove ? triggerRect.top - gap - height : triggerRect.bottom + gap}px`,
  };
}

function addPositionListeners() {
  window.addEventListener("resize", positionMenu);
  window.addEventListener("scroll", positionMenu, true);
}

function removePositionListeners() {
  window.removeEventListener("resize", positionMenu);
  window.removeEventListener("scroll", positionMenu, true);
}

function onToggle(event: Event) {
  const nextState = (event as Event & { newState?: string }).newState;
  isOpen.value = nextState === "open";
  if (!isOpen.value) {
    removePositionListeners();
    return;
  }

  focusedIndex.value = selectedIndex();
  addPositionListeners();
  void nextTick(() => {
    positionMenu();
    requestAnimationFrame(() => focusOption(focusedIndex.value));
  });
}

function showMenu(preferredIndex = selectedIndex()) {
  const menu = menuRef.value;
  if (!menu) return;
  if (!menu.matches(":popover-open") && typeof menu.showPopover === "function") {
    isOpen.value = true;
    positionMenu();
    menu.showPopover();
  }
  focusedIndex.value = preferredIndex;
  void nextTick(() => focusOption(preferredIndex));
}

function closeMenu(restoreFocus = true) {
  const menu = menuRef.value;
  if (menu?.matches(":popover-open") && typeof menu.hidePopover === "function") menu.hidePopover();
  isOpen.value = false;
  if (restoreFocus) void nextTick(() => triggerRef.value?.focus({ preventScroll: true }));
}

function toggleMenu() {
  if (menuRef.value?.matches(":popover-open")) {
    closeMenu();
    return;
  }
  showMenu();
}

function selectOption(option: ComposerSelectOption) {
  if (option.disabled) return;
  emit("update:modelValue", option.value);
  emit("change", option.value);
  closeMenu();
}

function moveFocus(step: 1 | -1) {
  const start = focusedIndex.value < 0 ? selectedIndex() : focusedIndex.value + step;
  focusOption(enabledIndex(start, step));
}

function onTriggerKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const baseIndex = selectedIndex();
    showMenu(event.key === "ArrowDown" ? baseIndex : enabledIndex(baseIndex - 1, -1));
  }
}

function onMenuKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveFocus(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    moveFocus(-1);
  } else if (event.key === "Home") {
    event.preventDefault();
    focusOption(enabledIndex(0, 1));
  } else if (event.key === "End") {
    event.preventDefault();
    focusOption(enabledIndex(props.options.length - 1, -1));
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeMenu();
  }
}

watch(() => [props.modelValue, props.options.length], () => {
  if (isOpen.value) void nextTick(positionMenu);
});

onBeforeUnmount(() => {
  removePositionListeners();
  if (menuRef.value?.matches(":popover-open")) menuRef.value.hidePopover();
});
</script>

<template>
  <div class="composer-select" :class="`composer-select--${variant}`">
    <button
      ref="triggerRef"
      type="button"
      class="composer-select__trigger"
      :class="{ 'is-open': isOpen }"
      :aria-label="ariaLabel || label"
      :aria-controls="popoverId"
      :aria-expanded="isOpen"
      aria-haspopup="listbox"
      data-slot="select-trigger"
      @click="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span class="composer-select__label">{{ label }}</span>
      <span class="composer-select__value">{{ selectedOption?.label || "请选择" }}</span>
      <ChevronDown class="composer-select__chevron" aria-hidden="true" />
    </button>

    <div
      :id="popoverId"
      ref="menuRef"
      class="composer-select__menu"
      :data-placement="placement"
      :style="menuStyle"
      popover="auto"
      role="listbox"
      :aria-label="ariaLabel || label"
      @toggle="onToggle"
      @keydown="onMenuKeydown"
    >
      <button
        v-for="(option, index) in options"
        :key="option.value"
        type="button"
        class="composer-select__option"
        :class="{ 'is-selected': option.value === modelValue }"
        :disabled="option.disabled"
        role="option"
        :aria-selected="option.value === modelValue"
        :data-option-index="index"
        @click="selectOption(option)"
        @focus="focusedIndex = index"
      >
        <span>{{ option.label }}</span>
        <Check v-if="option.value === modelValue" class="composer-select__check" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.composer-select {
  min-width: 0;
}

.composer-select--wide {
  width: 100%;
}

.composer-select--compact {
  min-width: 100px;
}

.composer-select__trigger {
  display: grid;
  width: 100%;
  height: 40px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border: 1px solid rgb(15 23 42 / 0.07);
  border-radius: 12px;
  background: rgb(248 250 252);
  padding: 0 10px;
  color: rgb(15 23 42);
  cursor: pointer;
  text-align: left;
  transition:
    border-color 180ms cubic-bezier(0.22, 1, 0.36, 1),
    background-color 180ms cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 180ms cubic-bezier(0.22, 1, 0.36, 1);
}

.composer-select__trigger:hover {
  border-color: rgb(79 124 255 / 0.24);
  background: rgb(255 255 255);
}

.composer-select__trigger:focus-visible,
.composer-select__trigger.is-open {
  border-color: rgb(79 124 255 / 0.42);
  outline: none;
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.11);
}

.composer-select__label {
  color: rgb(100 116 139);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}

.composer-select__value {
  min-width: 0;
  overflow: hidden;
  color: inherit;
  font-size: 13px;
  font-weight: 650;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-select__chevron {
  width: 14px;
  height: 14px;
  color: rgb(100 116 139);
  transition: transform 180ms cubic-bezier(0.22, 1, 0.36, 1);
}

.composer-select__trigger.is-open .composer-select__chevron {
  transform: rotate(180deg);
}

.composer-select__menu {
  z-index: 50;
  inset: auto;
  display: grid;
  gap: 2px;
  margin: 0;
  overflow-x: hidden;
  overflow-y: auto;
  border: 1px solid rgb(15 23 42 / 0.09);
  border-radius: 12px;
  background: rgb(255 255 255);
  padding: 5px;
  color: rgb(15 23 42);
  box-shadow: 0 6px 8px rgb(15 23 42 / 0.1);
  opacity: 0;
  pointer-events: none;
  transform: translateY(-4px) scale(0.985);
  transform-origin: top center;
  transition:
    opacity 150ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 150ms cubic-bezier(0.22, 1, 0.36, 1),
    overlay 150ms allow-discrete,
    display 150ms allow-discrete;
}

.composer-select__menu[data-placement="top"] {
  transform: translateY(4px) scale(0.985);
  transform-origin: bottom center;
}

.composer-select__menu:popover-open {
  opacity: 1;
  pointer-events: auto;
  transform: translateY(0) scale(1);
}

@starting-style {
  .composer-select__menu:popover-open {
    opacity: 0;
    transform: translateY(-4px) scale(0.985);
  }

  .composer-select__menu[data-placement="top"]:popover-open {
    transform: translateY(4px) scale(0.985);
  }
}

.composer-select__menu::backdrop {
  background: transparent;
}

.composer-select__option {
  display: grid;
  width: 100%;
  min-height: 36px;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  padding: 0 10px;
  color: rgb(51 65 85);
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  text-align: left;
  transition:
    background-color 140ms ease,
    color 140ms ease;
}

.composer-select__option:hover,
.composer-select__option:focus-visible {
  background: rgb(79 124 255 / 0.08);
  color: rgb(49 91 232);
  outline: none;
}

.composer-select__option.is-selected {
  background: rgb(79 124 255 / 0.11);
  color: rgb(49 91 232);
  font-weight: 700;
}

.composer-select__option:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.composer-select__check {
  width: 15px;
  height: 15px;
  stroke-width: 2.4;
}

.dark .composer-select__trigger {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.05);
  color: rgb(250 250 249);
}

.dark .composer-select__trigger:hover {
  border-color: rgb(157 179 255 / 0.28);
  background: rgb(255 255 255 / 0.075);
}

.dark .composer-select__trigger:focus-visible,
.dark .composer-select__trigger.is-open {
  border-color: rgb(157 179 255 / 0.44);
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.16);
}

.dark .composer-select__label,
.dark .composer-select__chevron {
  color: rgb(168 162 158);
}

.dark .composer-select__menu {
  border-color: rgb(255 255 255 / 0.11);
  background: rgb(29 32 39);
  color: rgb(250 250 249);
  box-shadow: 0 6px 8px rgb(0 0 0 / 0.3);
}

.dark .composer-select__option {
  color: rgb(214 211 209);
}

.dark .composer-select__option:hover,
.dark .composer-select__option:focus-visible,
.dark .composer-select__option.is-selected {
  background: rgb(107 141 255 / 0.14);
  color: rgb(191 219 254);
}

@media (max-width: 760px) {
  .composer-select--compact .composer-select__trigger {
    gap: 5px;
    padding-inline: 7px;
  }

  .composer-select--compact .composer-select__label {
    font-size: 10px;
  }

  .composer-select--compact .composer-select__value {
    font-size: 12px;
  }

  .composer-select--compact .composer-select__chevron {
    width: 13px;
    height: 13px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .composer-select__trigger,
  .composer-select__chevron,
  .composer-select__menu,
  .composer-select__option {
    transition: none;
  }
}
</style>
