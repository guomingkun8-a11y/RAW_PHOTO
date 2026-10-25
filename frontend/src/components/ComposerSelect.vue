<script setup lang="ts">
import { Check, ChevronDown, Search, X } from "@lucide/vue";
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
  variant?: "wide" | "compact" | "field";
  disabled?: boolean;
  searchable?: boolean;
  searchPlaceholder?: string;
}>(), {
  ariaLabel: "",
  variant: "compact",
  disabled: false,
  searchable: false,
  searchPlaceholder: "搜索选项",
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  change: [value: string];
}>();

const triggerRef = ref<HTMLButtonElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const searchInputRef = ref<HTMLInputElement | null>(null);
const isOpen = ref(false);
const focusedIndex = ref(-1);
const placement = ref<"top" | "bottom">("bottom");
const menuStyle = ref<Record<string, string>>({});
const searchQuery = ref("");
const focusSearchOnOpen = ref(false);
const popoverId = `composer-select-${Math.random().toString(36).slice(2, 10)}`;

const selectedOption = computed(() =>
  props.options.find((option) => option.value === props.modelValue) || props.options.find((option) => !option.disabled),
);
const visibleOptions = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase();
  return props.options
    .map((option) => ({ option }))
    .filter(({ option }) => !query || option.label.toLocaleLowerCase().includes(query));
});

function enabledIndex(start: number, step: 1 | -1) {
  if (!visibleOptions.value.length) return -1;
  for (let offset = 0; offset < visibleOptions.value.length; offset += 1) {
    const index = (start + offset * step + visibleOptions.value.length) % visibleOptions.value.length;
    if (!visibleOptions.value[index]?.option.disabled) return index;
  }
  return -1;
}

function focusOption(index: number) {
  const normalized = enabledIndex(index, 1);
  if (normalized < 0) return;
  focusedIndex.value = normalized;
  void nextTick(() => {
    const option = menuRef.value
      ?.querySelector<HTMLButtonElement>(`[data-option-index="${normalized}"]`);
    option?.focus({ preventScroll: true });
    option?.scrollIntoView({ block: "nearest" });
  });
}

function selectedIndex() {
  const index = visibleOptions.value.findIndex(
    ({ option }) => option.value === props.modelValue && !option.disabled,
  );
  return index >= 0 ? index : enabledIndex(0, 1);
}

function focusSearch() {
  void nextTick(() => searchInputRef.value?.focus({ preventScroll: true }));
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
  const searchHeight = props.searchable ? 51 : 0;
  const desiredHeight = Math.min(
    Math.max(props.searchable ? 100 : 46, visibleOptions.value.length * 38 + 10 + searchHeight),
    props.searchable ? 360 : 320,
  );
  const spaceBelow = viewportHeight - triggerRect.bottom - gap - edge;
  const spaceAbove = triggerRect.top - gap - edge;
  const openAbove = spaceBelow < Math.min(desiredHeight, 180) && spaceAbove > spaceBelow;
  const availableHeight = Math.max(0, openAbove ? spaceAbove : spaceBelow);
  const viewportCapacity = Math.max(80, viewportHeight - edge * 2);
  const height = Math.min(desiredHeight, Math.max(80, availableHeight), viewportCapacity);
  const rawTop = openAbove ? triggerRect.top - gap - height : triggerRect.bottom + gap;
  const top = Math.min(
    Math.max(edge, rawTop),
    Math.max(edge, viewportHeight - height - edge),
  );
  const left = Math.min(
    Math.max(edge, triggerRect.left),
    Math.max(edge, viewportWidth - width - edge),
  );

  placement.value = openAbove ? "top" : "bottom";
  menuStyle.value = {
    width: `${width}px`,
    height: `${height}px`,
    maxHeight: `${height}px`,
    left: `${left}px`,
    top: `${top}px`,
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
    searchQuery.value = "";
    return;
  }

  focusedIndex.value = selectedIndex();
  addPositionListeners();
  void nextTick(() => {
    positionMenu();
    requestAnimationFrame(() => {
      if (props.searchable && focusSearchOnOpen.value) focusSearch();
      else focusOption(focusedIndex.value);
    });
  });
}

function showMenu(preferredIndex = selectedIndex(), preferSearch = props.searchable) {
  const menu = menuRef.value;
  if (!menu || props.disabled) return;
  focusSearchOnOpen.value = preferSearch;
  if (!menu.matches(":popover-open") && typeof menu.showPopover === "function") {
    isOpen.value = true;
    positionMenu();
    menu.showPopover();
  }
  focusedIndex.value = preferredIndex;
  void nextTick(() => {
    if (props.searchable && preferSearch) focusSearch();
    else focusOption(preferredIndex);
  });
}

function closeMenu(restoreFocus = true) {
  const menu = menuRef.value;
  if (menu?.matches(":popover-open") && typeof menu.hidePopover === "function") menu.hidePopover();
  isOpen.value = false;
  searchQuery.value = "";
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
    showMenu(event.key === "ArrowDown" ? baseIndex : enabledIndex(baseIndex - 1, -1), false);
  }
}

function onMenuKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented) return;
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

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    focusOption(enabledIndex(0, 1));
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    focusOption(enabledIndex(visibleOptions.value.length - 1, -1));
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeMenu();
  }
}

watch(() => [props.modelValue, props.options.length, visibleOptions.value.length], () => {
  if (isOpen.value) void nextTick(positionMenu);
});

watch(searchQuery, () => {
  focusedIndex.value = -1;
});

watch(() => props.disabled, (disabled) => {
  if (disabled && isOpen.value) closeMenu(false);
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
      :disabled="disabled"
      aria-haspopup="listbox"
      data-slot="select-trigger"
      @click="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span v-if="label" class="composer-select__label">{{ label }}</span>
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
      @toggle="onToggle"
      @keydown="onMenuKeydown"
    >
      <div v-if="searchable" class="composer-select__search-wrap">
        <Search class="composer-select__search-icon" aria-hidden="true" />
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          type="search"
          class="composer-select__search"
          :placeholder="searchPlaceholder"
          :aria-label="searchPlaceholder"
          autocomplete="off"
          spellcheck="false"
          @keydown="onSearchKeydown"
        />
        <button
          v-if="searchQuery"
          type="button"
          class="composer-select__search-clear"
          aria-label="清空搜索"
          title="清空搜索"
          @click="searchQuery = ''; focusSearch()"
        >
          <X aria-hidden="true" />
        </button>
      </div>

      <div class="composer-select__options" role="listbox" :aria-label="ariaLabel || label">
        <button
          v-for="({ option }, index) in visibleOptions"
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
        <div v-if="!visibleOptions.length" class="composer-select__empty" role="status">
          没有匹配的选项
        </div>
      </div>
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
  position: fixed;
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
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

.composer-select__search-wrap {
  position: relative;
  flex: 0 0 auto;
  padding: 3px 3px 7px;
}

.composer-select__search-icon {
  position: absolute;
  top: 13px;
  left: 13px;
  width: 15px;
  height: 15px;
  color: rgb(100 116 139);
  pointer-events: none;
}

.composer-select__search {
  width: 100%;
  height: 36px;
  border: 1px solid rgb(15 23 42 / 0.08);
  border-radius: 8px;
  background: rgb(248 250 252);
  padding: 0 34px;
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 550;
  outline: none;
  appearance: none;
}

.composer-select__search::-webkit-search-cancel-button {
  display: none;
}

.composer-select__search::placeholder {
  color: rgb(100 116 139);
}

.composer-select__search:focus {
  border-color: rgb(79 124 255 / 0.42);
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.1);
}

.composer-select__search-clear {
  position: absolute;
  top: 8px;
  right: 8px;
  display: grid;
  width: 26px;
  height: 26px;
  place-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgb(100 116 139);
  cursor: pointer;
}

.composer-select__search-clear:hover,
.composer-select__search-clear:focus-visible {
  background: rgb(15 23 42 / 0.06);
  color: rgb(15 23 42);
  outline: none;
}

.composer-select__search-clear svg {
  width: 14px;
  height: 14px;
}

.composer-select__options {
  min-height: 0;
  flex: 1 1 auto;
  display: grid;
  align-content: start;
  gap: 2px;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.composer-select__empty {
  display: grid;
  min-height: 48px;
  place-items: center;
  padding: 8px;
  color: rgb(100 116 139);
  font-size: 12px;
  text-align: center;
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

.dark .composer-select__search {
  border-color: rgb(255 255 255 / 0.1);
  background: rgb(255 255 255 / 0.05);
  color: rgb(250 250 249);
}

.dark .composer-select__search::placeholder,
.dark .composer-select__search-icon,
.dark .composer-select__search-clear,
.dark .composer-select__empty {
  color: rgb(168 162 158);
}

.dark .composer-select__search:focus {
  border-color: rgb(157 179 255 / 0.44);
  box-shadow: 0 0 0 3px rgb(79 124 255 / 0.16);
}

.dark .composer-select__search-clear:hover,
.dark .composer-select__search-clear:focus-visible {
  background: rgb(255 255 255 / 0.08);
  color: rgb(250 250 249);
}

.dark .composer-select__option:hover,
.dark .composer-select__option:focus-visible,
.dark .composer-select__option.is-selected {
  background: rgb(107 141 255 / 0.14);
  color: rgb(191 219 254);
}

.composer-select--field .composer-select__trigger {
  height: 34px;
  grid-template-columns: minmax(0, 1fr) auto;
  border-color: var(--border);
  border-radius: 6px;
  background: var(--card);
  color: var(--foreground);
  box-shadow: none;
  transition: border-color 120ms ease-out, background-color 120ms ease-out;
}

.composer-select--field .composer-select__trigger:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--accent) 35%, var(--border));
  background: var(--secondary);
}

.composer-select--field .composer-select__trigger:focus-visible,
.composer-select--field .composer-select__trigger.is-open {
  border-color: var(--accent);
  outline: 2px solid color-mix(in srgb, var(--accent) 20%, transparent);
  outline-offset: 1px;
  box-shadow: none;
}

.composer-select--field .composer-select__trigger:disabled {
  cursor: not-allowed;
  opacity: .5;
}

.composer-select--field .composer-select__value {
  font-weight: 500;
}

.composer-select--field .composer-select__chevron {
  color: var(--muted-foreground);
}

.composer-select--field .composer-select__menu {
  border-color: var(--border);
  border-radius: 7px;
  background: var(--card);
  color: var(--foreground);
  transform: none;
  transition: opacity 120ms ease-out, overlay 120ms allow-discrete, display 120ms allow-discrete;
}

.composer-select--field .composer-select__menu:not(:popover-open) {
  display: none;
}

.composer-select--field .composer-select__option {
  border-radius: 4px;
  color: var(--foreground);
  font-weight: 400;
}

.composer-select--field .composer-select__option:hover,
.composer-select--field .composer-select__option:focus-visible,
.composer-select--field .composer-select__option.is-selected {
  background: color-mix(in srgb, var(--accent) 10%, var(--card));
  color: var(--accent);
}

@starting-style {
  .composer-select--field .composer-select__menu:popover-open {
    opacity: 0;
    transform: none;
  }
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
  .composer-select__search,
  .composer-select__option {
    transition: none;
  }
}
</style>
