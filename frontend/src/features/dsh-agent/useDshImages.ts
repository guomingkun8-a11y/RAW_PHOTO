import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue";
import { toast } from "vue-sonner";
import { discardDshFile, discardDshImage, queryDshImages, uploadDshFile, uploadDshImage, type DshAgentMessage, type DshImageMedia } from "./api";

export type DshUpload = { id: number; conversationId: string; filename: string; size: number; error?: string };

export function useDshImages(activeId: Ref<string>, messages: Ref<DshAgentMessage[]>, ensureConversation: () => Promise<string>) {
  const drafts = ref<Record<string, DshImageMedia[]>>({});
  const references = computed(() => drafts.value[activeId.value] || []);
  const uploading = ref(false);
  const uploads = ref<DshUpload[]>([]);
  const currentUploads = computed(() => uploads.value.filter(item => item.conversationId === activeId.value));
  let uploadSequence = 0;
  const pollingError = ref("");
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight = false;
  let failures = 0;
  let unconfirmedAttempts = 0;
  const pending = computed(() => messages.value.flatMap((m) => m.media || []).filter((m) => m.kind === "task"
    && ["queued", "running", "pending", "unconfirmed"].includes(m.task?.status || "")));

  async function upload(files: File[]) {
    if (uploading.value || !files.length) return;
    const images = files.filter(file => file.type.startsWith("image/"));
    if (references.value.filter(item => item.kind === "reference").length + images.length > 6
      || references.value.filter(item => item.kind === "file").length + files.length - images.length > 4) {
      return toast.error("每次最多添加 6 张图片和 4 个文件");
    }
    for (const file of files) {
      if (file.type.startsWith("image/")) {
        if (file.size > 20 * 1024 * 1024 || !/^image\/(png|jpeg|webp|gif)$/.test(file.type)) return toast.error("图片支持 20MB 以内的 PNG、JPEG、WebP 或 GIF");
      } else if (file.size > 5 * 1024 * 1024 || !/\.(txt|md|csv|json|log|pdf|docx)$/i.test(file.name)) {
        return toast.error("文件支持 5MB 以内的 TXT、MD、CSV、JSON、LOG、PDF 和 DOCX");
      }
    }
    uploading.value = true;
    try {
      const id = await ensureConversation();
      for (const file of files) {
        if (disposed) return;
        const key = ++uploadSequence;
        uploads.value.push({ id: key, conversationId: id, filename: file.name, size: file.size });
        try {
          const item = await (file.type.startsWith("image/") ? uploadDshImage(id, file) : uploadDshFile(id, file));
          if (disposed) return;
          drafts.value[id] = [...(drafts.value[id] || []), item];
          uploads.value = uploads.value.filter(item => item.id !== key);
        } catch (error) {
          const item = uploads.value.find(item => item.id === key);
          if (item) item.error = error instanceof Error ? error.message : "上传失败，请重新添加";
        }
      }
    } catch (error) {
      if (!disposed) toast.error(error instanceof Error ? error.message : "上传附件失败");
    } finally { uploading.value = false; }
  }

  async function remove(item: DshImageMedia) {
    try {
      await (item.kind === "file" ? discardDshFile : discardDshImage)(item.conversation_id, item.media_id);
      drafts.value[item.conversation_id] = (drafts.value[item.conversation_id] || []).filter((ref) => ref.media_id !== item.media_id);
    } catch { toast.error("移除附件失败，请重试"); }
  }
  function dismissUpload(id: number) { uploads.value = uploads.value.filter(item => item.id !== id || !item.error); }
  function consume(id: string) { drafts.value[id] = []; uploads.value = uploads.value.filter(item => item.conversationId !== id); }
  function restore(id: string, items: DshImageMedia[]) { drafts.value[id] = items; }

  function schedule(delay = 2500) {
    clearTimeout(timer);
    if (!disposed && pending.value.length) timer = setTimeout(() => void poll(), delay);
  }
  async function poll() {
    if (disposed || inFlight || !pending.value.length) return;
    if (document.hidden) return schedule(15000);
    if (pending.value.every((item) => item.task?.status === "unconfirmed") && unconfirmedAttempts >= 8) return;
    inFlight = true;
    const id = activeId.value;
    const target = messages.value;
    try {
      const result = await queryDshImages(id, pending.value.map((item) => item.media_id));
      if (disposed || activeId.value !== id || target !== messages.value) return;
      const updates = new Map(result.items.map((item) => [item.media_id, item]));
      for (const message of target) message.media = message.media?.map((item) => updates.get(item.media_id) || item);
      failures = 0;
      pollingError.value = "";
      unconfirmedAttempts++;
    } catch {
      if (activeId.value === id) pollingError.value = "图片状态暂时无法更新，正在重试";
      failures++;
    } finally {
      inFlight = false;
      schedule(Math.min(30000, 2500 * 2 ** Math.min(failures, 4)));
    }
  }
  watch(() => `${activeId.value}:${pending.value.map((item) => item.media_id).join(",")}`, () => {
    failures = 0;
    unconfirmedAttempts = 0;
    pollingError.value = "";
    schedule();
  });
  const visible = () => { if (!document.hidden) schedule(100); };
  document.addEventListener("visibilitychange", visible);
  onBeforeUnmount(() => { disposed = true; clearTimeout(timer); document.removeEventListener("visibilitychange", visible); });
  return { references, uploading, uploads: currentUploads, dismissUpload, pollingError, upload, remove, consume, restore };
}
