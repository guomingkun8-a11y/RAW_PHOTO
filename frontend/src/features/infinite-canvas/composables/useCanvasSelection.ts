import { computed, ref, shallowRef } from 'vue';

export function useCanvasSelection() {
  const selectedIds = shallowRef<Set<string>>(new Set());
  const selectedId = ref<string>();

  const selectionCount = computed(() => selectedIds.value.size);
  const hasSelection = computed(() => selectedIds.value.size > 0);

  function replaceSelection(ids: Iterable<string>, primaryId?: string) {
    const next = new Set(ids);
    const primary = primaryId && next.has(primaryId)
      ? primaryId
      : next.values().next().value as string | undefined;
    selectedIds.value = next;
    selectedId.value = primary;
  }

  function selectOnly(nodeId?: string) {
    replaceSelection(nodeId ? [nodeId] : [], nodeId);
  }

  function toggleSelection(nodeId: string) {
    const next = new Set(selectedIds.value);
    if (next.has(nodeId)) next.delete(nodeId);
    else next.add(nodeId);
    replaceSelection(next, next.has(nodeId) ? nodeId : selectedId.value);
  }

  function selectMany(ids: Iterable<string>, options: { additive?: boolean; primaryId?: string } = {}) {
    const next = options.additive ? new Set(selectedIds.value) : new Set<string>();
    for (const id of ids) next.add(id);
    replaceSelection(next, options.primaryId);
  }

  function clearSelection() {
    replaceSelection([]);
  }

  function removeFromSelection(ids: Iterable<string>) {
    const next = new Set(selectedIds.value);
    for (const id of ids) next.delete(id);
    replaceSelection(next, selectedId.value);
  }

  function retainSelection(validIds: ReadonlySet<string>) {
    replaceSelection(
      [...selectedIds.value].filter((id) => validIds.has(id)),
      selectedId.value,
    );
  }

  function isSelected(nodeId: string) {
    return selectedIds.value.has(nodeId);
  }

  return {
    selectedIds,
    selectedId,
    selectionCount,
    hasSelection,
    replaceSelection,
    selectOnly,
    toggleSelection,
    selectMany,
    clearSelection,
    removeFromSelection,
    retainSelection,
    isSelected,
  };
}
