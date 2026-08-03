import localforage from "localforage";

import {
  clearImageConversationsRemote,
  deleteImageConversationRemote,
  fetchImageConversationsRemote,
  renameImageConversationRemote,
  upsertImageConversationRemote,
  type ImageConversationApiPayload,
  type ImageModel,
} from "@/lib/api";
import { getStoredAuthKey, getStoredAuthSession } from "@/stores/auth";

export type ImageConversationMode = "generate" | "edit";

export type StoredReferenceImage = {
  name: string;
  type: string;
  dataUrl?: string;
  url?: string;
};

export type StoredImageQualityCheck = {
  status: "analyzing" | "passed" | "review" | "failed";
  score?: number;
  summary?: string;
  issues?: string[];
  suggestions?: string[];
  checkedAt?: string;
  model?: string;
};

export type StoredImage = {
  id: string;
  taskId?: string;
  status?: "loading" | "success" | "error" | "canceled";
  taskStatus?: "queued" | "running";
  progress?: string;
  b64_json?: string;
  url?: string;
  revised_prompt?: string;
  error?: string;
  startTime?: number;
  elapsedSecs?: number;
  elapsedUpdatedAt?: number;
  durationMs?: number;
  qualityCheck?: StoredImageQualityCheck;
  sourceImageIndex?: number;
  sourceName?: string;
};

export type ImageTurnStatus = "queued" | "generating" | "success" | "error" | "canceled";

export type ImageBatchReplacePlan = {
  productImage: StoredReferenceImage;
  folderImages: StoredReferenceImage[];
};

export type ImageBatchFolderPlan = {
  folderImages: StoredReferenceImage[];
};

export type ImageTurn = {
  id: string;
  prompt: string;
  model: ImageModel;
  mode: ImageConversationMode;
  referenceImages: StoredReferenceImage[];
  batchReplace?: ImageBatchReplacePlan;
  batchFolder?: ImageBatchFolderPlan;
  preserveSubject?: boolean;
  count: number;
  size: string;
  ratio: string;
  tier: string;
  quality: string;
  productId?: number;
  templateId?: number;
  images: StoredImage[];
  createdAt: string;
  status: ImageTurnStatus;
  error?: string;
  promptDeleted?: boolean;
  resultsDeleted?: boolean;
};

export type ImageConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns: ImageTurn[];
};

export type ImageConversationStats = {
  queued: number;
  running: number;
};

const legacyImageConversationStorage = localforage.createInstance({
  name: "gmkraw",
  storeName: "image_conversations",
});

const singleImageConversationStorage = localforage.createInstance({
  name: "gmkraw",
  storeName: "image_single_conversations",
});

const IMAGE_CONVERSATIONS_KEY = "items";
const ACCOUNT_IMAGE_CONVERSATIONS_PREFIX = "items:account:";
const ANONYMOUS_IMAGE_CONVERSATIONS_KEY = "items:anonymous";
const IMAGE_CONVERSATIONS_LEGACY_MIGRATION_KEY = "legacy_migration_v1";
let imageConversationWriteQueue: Promise<void> = Promise.resolve();
let imageConversationMigrationPromise: Promise<void> | null = null;
const INLINE_IMAGE_REMOTE_LIMIT = 2048;

function isImageDataUrl(value: string) {
  return /^data:image\/[a-z0-9.+-]+;base64,/i.test(value);
}

function normalizeStoredImage(image: StoredImage): StoredImage {
  const qualityCheck = image.qualityCheck && typeof image.qualityCheck === "object"
    ? {
        status:
          image.qualityCheck.status === "analyzing" ||
          image.qualityCheck.status === "passed" ||
          image.qualityCheck.status === "review" ||
          image.qualityCheck.status === "failed"
            ? image.qualityCheck.status
            : "review",
        score: typeof image.qualityCheck.score === "number" ? image.qualityCheck.score : undefined,
        summary: typeof image.qualityCheck.summary === "string" ? image.qualityCheck.summary : undefined,
        issues: Array.isArray(image.qualityCheck.issues)
          ? image.qualityCheck.issues.map(String).filter(Boolean).slice(0, 8)
          : undefined,
        suggestions: Array.isArray(image.qualityCheck.suggestions)
          ? image.qualityCheck.suggestions.map(String).filter(Boolean).slice(0, 8)
          : undefined,
        checkedAt: typeof image.qualityCheck.checkedAt === "string" ? image.qualityCheck.checkedAt : undefined,
        model: typeof image.qualityCheck.model === "string" ? image.qualityCheck.model : undefined,
      }
    : undefined;
  const normalized = {
    ...image,
    taskId: typeof image.taskId === "string" && image.taskId ? image.taskId : undefined,
    taskStatus: image.taskStatus === "queued" || image.taskStatus === "running" ? image.taskStatus : undefined,
    url: typeof image.url === "string" && image.url ? image.url : undefined,
    revised_prompt: typeof image.revised_prompt === "string" ? image.revised_prompt : undefined,
    startTime: typeof image.startTime === "number" ? image.startTime : undefined,
    elapsedSecs: typeof image.elapsedSecs === "number" ? image.elapsedSecs : undefined,
    elapsedUpdatedAt: typeof image.elapsedUpdatedAt === "number" ? image.elapsedUpdatedAt : undefined,
    durationMs: typeof image.durationMs === "number" ? image.durationMs : undefined,
    sourceImageIndex:
      typeof image.sourceImageIndex === "number" && Number.isInteger(image.sourceImageIndex) && image.sourceImageIndex >= 0
        ? image.sourceImageIndex
        : undefined,
    sourceName: typeof image.sourceName === "string" && image.sourceName ? image.sourceName : undefined,
    qualityCheck,
  };
  if (image.status === "loading" || image.status === "error" || image.status === "success" || image.status === "canceled") {
    return normalized;
  }
  return {
    ...normalized,
    status: image.b64_json || image.url ? "success" : "loading",
  };
}

function normalizeReferenceImage(image: StoredReferenceImage): StoredReferenceImage {
  return {
    name: image.name || "reference.png",
    type: image.type || "image/png",
    dataUrl: typeof image.dataUrl === "string" && image.dataUrl ? image.dataUrl : undefined,
    url: typeof image.url === "string" && image.url ? image.url : undefined,
  };
}

function normalizeBatchReplacePlan(value: unknown): ImageBatchReplacePlan | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const plan = value as Partial<ImageBatchReplacePlan>;
  if (!plan.productImage || !Array.isArray(plan.folderImages)) {
    return undefined;
  }
  const productImage = normalizeReferenceImage(plan.productImage);
  const folderImages = plan.folderImages
    .filter((image): image is StoredReferenceImage => Boolean(image?.dataUrl || image?.url))
    .map(normalizeReferenceImage);
  if (!(productImage.dataUrl || productImage.url) || folderImages.length === 0) {
    return undefined;
  }
  return { productImage, folderImages };
}

function normalizeBatchFolderPlan(value: unknown): ImageBatchFolderPlan | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const plan = value as Partial<ImageBatchFolderPlan>;
  if (!Array.isArray(plan.folderImages)) {
    return undefined;
  }
  const folderImages = plan.folderImages
    .filter((image): image is StoredReferenceImage => Boolean(image?.dataUrl || image?.url))
    .map(normalizeReferenceImage);
  if (folderImages.length === 0) {
    return undefined;
  }
  return { folderImages };
}

function dataUrlMimeType(dataUrl: string) {
  const match = dataUrl.match(/^data:(.*?);base64,/);
  return match?.[1] || "image/png";
}

function getLegacyReferenceImages(source: Record<string, unknown>): StoredReferenceImage[] {
  if (Array.isArray(source.referenceImages)) {
    return source.referenceImages
      .filter((image): image is StoredReferenceImage => {
        if (!image || typeof image !== "object") {
          return false;
        }
        const candidate = image as StoredReferenceImage;
        return (
          (typeof candidate.dataUrl === "string" && candidate.dataUrl.length > 0)
          || (typeof candidate.url === "string" && candidate.url.length > 0)
        );
      })
      .map(normalizeReferenceImage);
  }

  if (source.sourceImage && typeof source.sourceImage === "object") {
    const image = source.sourceImage as { dataUrl?: unknown; fileName?: unknown };
    if (typeof image.dataUrl === "string" && image.dataUrl) {
      return [
        {
          name: typeof image.fileName === "string" && image.fileName ? image.fileName : "reference.png",
          type: dataUrlMimeType(image.dataUrl),
          dataUrl: image.dataUrl,
        },
      ];
    }
  }

  return [];
}

function normalizeTurn(turn: ImageTurn & Record<string, unknown>): ImageTurn {
  const normalizedImages = Array.isArray(turn.images) ? turn.images.map(normalizeStoredImage) : [];
  const derivedStatus: ImageTurnStatus =
    normalizedImages.some((image) => image.status === "loading")
      ? "generating"
      : normalizedImages.some((image) => image.status === "error")
        ? "error"
        : normalizedImages.some((image) => image.status === "canceled")
          ? "canceled"
          : "success";
  const validStatus =
    turn.status === "queued" ||
    turn.status === "generating" ||
    turn.status === "success" ||
    turn.status === "error" ||
    turn.status === "canceled"
      ? turn.status
      : derivedStatus;

  return {
    id: String(turn.id || `${Date.now()}`),
    prompt: String(turn.prompt || ""),
    model: (turn.model as ImageModel) || "gpt-image-2",
    mode: turn.mode === "edit" ? "edit" : "generate",
    referenceImages: getLegacyReferenceImages(turn),
    batchReplace: normalizeBatchReplacePlan(turn.batchReplace),
    batchFolder: normalizeBatchFolderPlan(turn.batchFolder),
    preserveSubject: turn.preserveSubject === true,
    count: Math.max(1, Number(turn.count || normalizedImages.length || 1)),
    size: typeof turn.size === "string" ? turn.size : "",
    ratio: typeof turn.ratio === "string" && turn.ratio ? turn.ratio : "1:1",
    tier: typeof turn.tier === "string" && turn.tier ? turn.tier : "1k",
    quality: typeof turn.quality === "string" && turn.quality ? turn.quality : "auto",
    productId: Number(turn.productId || 0) > 0 ? Number(turn.productId) : undefined,
    templateId: Number(turn.templateId || 0) > 0 ? Number(turn.templateId) : undefined,
    images: normalizedImages,
    createdAt: String(turn.createdAt || new Date().toISOString()),
    status: normalizedImages.some((image) => image.status === "loading")
      ? validStatus === "queued" ? "queued" : "generating"
      : derivedStatus,
    error: typeof turn.error === "string" ? turn.error : undefined,
    promptDeleted: turn.promptDeleted === true,
    resultsDeleted: turn.resultsDeleted === true,
  };
}

function normalizeConversation(conversation: ImageConversation & Record<string, unknown>): ImageConversation {
  const turns = Array.isArray(conversation.turns)
    ? conversation.turns.map((turn) => normalizeTurn(turn as ImageTurn & Record<string, unknown>))
    : [
        normalizeTurn({
          id: String(conversation.id || `${Date.now()}`),
          prompt: String(conversation.prompt || ""),
          model: (conversation.model as ImageModel) || "gpt-image-2",
          mode: conversation.mode === "edit" ? "edit" : "generate",
          referenceImages: getLegacyReferenceImages(conversation),
          preserveSubject: conversation.preserveSubject === true,
          count: Number(conversation.count || 1),
          size: typeof conversation.size === "string" ? conversation.size : "",
          ratio: typeof conversation.ratio === "string" && conversation.ratio ? conversation.ratio : "1:1",
          tier: typeof conversation.tier === "string" && conversation.tier ? conversation.tier : "1k",
          quality: typeof conversation.quality === "string" && conversation.quality ? conversation.quality : "auto",
          images: Array.isArray(conversation.images) ? (conversation.images as StoredImage[]) : [],
          createdAt: String(conversation.createdAt || new Date().toISOString()),
          status:
            conversation.status === "generating" ||
            conversation.status === "success" ||
            conversation.status === "error" ||
            conversation.status === "canceled"
              ? conversation.status
              : "success",
          error: typeof conversation.error === "string" ? conversation.error : undefined,
        }),
      ];
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;

  return {
    id: String(conversation.id || `${Date.now()}`),
    title: String(conversation.title || ""),
    createdAt: String(conversation.createdAt || lastTurn?.createdAt || new Date().toISOString()),
    updatedAt: String(conversation.updatedAt || lastTurn?.createdAt || new Date().toISOString()),
    turns,
  };
}

function slimRemoteValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(slimRemoteValue);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const source = value as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  const hasUrl = typeof source.url === "string" && source.url.trim().length > 0;
  for (const [key, child] of Object.entries(source)) {
    if (key === "b64_json") {
      continue;
    }
    if (
      key === "dataUrl"
      && typeof child === "string"
      && isImageDataUrl(child)
      && (hasUrl || child.length > INLINE_IMAGE_REMOTE_LIMIT)
    ) {
      continue;
    }
    result[key] = slimRemoteValue(child);
  }
  return result;
}

function compactStoredValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(compactStoredValue);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const source = value as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  const hasUrl = typeof source.url === "string" && source.url.trim().length > 0;
  for (const [key, child] of Object.entries(source)) {
    if (key === "b64_json" && hasUrl) {
      continue;
    }
    if (key === "dataUrl" && hasUrl && typeof child === "string" && isImageDataUrl(child)) {
      continue;
    }
    result[key] = compactStoredValue(child);
  }
  return result;
}

function slimConversationForRemote(conversation: ImageConversation): ImageConversationApiPayload {
  return slimRemoteValue(normalizeConversation(conversation)) as ImageConversationApiPayload;
}

function compactConversationForStorage(conversation: ImageConversation): ImageConversation {
  return compactStoredValue(normalizeConversation(conversation)) as ImageConversation;
}

function sortImageConversations(conversations: ImageConversation[]): ImageConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function mergeReferenceImage(
  previous: StoredReferenceImage | undefined,
  latest: StoredReferenceImage | undefined,
): StoredReferenceImage | undefined {
  if (!previous) return latest;
  if (!latest) return previous;
  return {
    name: latest.name || previous.name,
    type: latest.type || previous.type,
    dataUrl: latest.dataUrl || previous.dataUrl,
    url: latest.url || previous.url,
  };
}

function mergeReferenceImages(
  previous: StoredReferenceImage[] | undefined,
  latest: StoredReferenceImage[] | undefined,
): StoredReferenceImage[] {
  const previousItems = previous || [];
  const latestItems = latest || [];
  const length = Math.max(previousItems.length, latestItems.length);
  const merged: StoredReferenceImage[] = [];
  for (let index = 0; index < length; index += 1) {
    const item = mergeReferenceImage(previousItems[index], latestItems[index]);
    if (item?.dataUrl || item?.url) merged.push(normalizeReferenceImage(item));
  }
  return merged;
}

function mergeBatchFolderPlan(
  previous: ImageBatchFolderPlan | undefined,
  latest: ImageBatchFolderPlan | undefined,
): ImageBatchFolderPlan | undefined {
  const folderImages = mergeReferenceImages(previous?.folderImages, latest?.folderImages);
  return folderImages.length ? { folderImages } : undefined;
}

function mergeBatchReplacePlan(
  previous: ImageBatchReplacePlan | undefined,
  latest: ImageBatchReplacePlan | undefined,
): ImageBatchReplacePlan | undefined {
  const productImage = mergeReferenceImage(previous?.productImage, latest?.productImage);
  const folderImages = mergeReferenceImages(previous?.folderImages, latest?.folderImages);
  if (!(productImage?.dataUrl || productImage?.url) || !folderImages.length) return undefined;
  return { productImage: normalizeReferenceImage(productImage), folderImages };
}

function preserveReferenceInputs(previous: ImageConversation, latest: ImageConversation) {
  const previousTurns = new Map(previous.turns.map((turn) => [turn.id, turn]));
  let changed = false;
  const turns = latest.turns.map((turn) => {
    const previousTurn = previousTurns.get(turn.id);
    if (!previousTurn) return turn;
    const referenceImages = mergeReferenceImages(previousTurn.referenceImages, turn.referenceImages);
    const batchReplace = mergeBatchReplacePlan(previousTurn.batchReplace, turn.batchReplace);
    const batchFolder = mergeBatchFolderPlan(previousTurn.batchFolder, turn.batchFolder);
    if (
      referenceImages === turn.referenceImages
      && batchReplace === turn.batchReplace
      && batchFolder === turn.batchFolder
    ) {
      return turn;
    }
    changed = true;
    return { ...turn, referenceImages, batchReplace, batchFolder };
  });
  return changed ? { ...latest, turns } : latest;
}

function pickLatestConversation(current: ImageConversation, next: ImageConversation) {
  const latest = getTimestamp(next.updatedAt) >= getTimestamp(current.updatedAt) ? next : current;
  const previous = latest === next ? current : next;
  return preserveSuccessfulImages(previous, preserveReferenceInputs(previous, latest));
}

function imageMergeKey(image: StoredImage) {
  return image.taskId || image.id;
}

function hasGeneratedImageData(image: StoredImage) {
  return image.status === "success" && Boolean(image.b64_json || image.url);
}

function deriveTurnStatusFromImages(turn: ImageTurn): Pick<ImageTurn, "status" | "error"> {
  const loading = turn.images.some((image) => image.status === "loading");
  const failed = turn.images.filter((image) => image.status === "error").length;
  const canceled = turn.images.some((image) => image.status === "canceled");
  const success = turn.images.some((image) => image.status === "success");
  if (loading) return { status: turn.images.some((image) => image.taskStatus === "running") ? "generating" : "queued", error: undefined };
  if (failed) return { status: "error", error: `其中 ${failed} 张未成功生成` };
  if (canceled) return { status: "canceled", error: undefined };
  if (success) return { status: "success", error: undefined };
  return { status: "success", error: undefined };
}

function preserveSuccessfulImages(previous: ImageConversation, latest: ImageConversation) {
  const previousTurns = new Map(previous.turns.map((turn) => [turn.id, turn]));
  let changed = false;
  const turns = latest.turns.map((turn) => {
    const previousTurn = previousTurns.get(turn.id);
    if (!previousTurn) return turn;
    const previousImages = new Map(previousTurn.images.map((image) => [imageMergeKey(image), image]));
    let turnChanged = false;
    const images = turn.images.map((image) => {
      const previousImage = previousImages.get(imageMergeKey(image));
      if (image.status === "loading" && previousImage && hasGeneratedImageData(previousImage)) {
        turnChanged = true;
        return previousImage;
      }
      return image;
    });
    if (!turnChanged) return turn;
    changed = true;
    return { ...turn, ...deriveTurnStatusFromImages({ ...turn, images }), images };
  });
  return changed ? { ...latest, turns } : latest;
}

async function mergeMigratedConversations(
  storage: LocalForage,
  incoming: ImageConversation[],
) {
  const existingRaw =
    (await storage.getItem<Array<ImageConversation & Record<string, unknown>>>(IMAGE_CONVERSATIONS_KEY)) || [];
  const conversationMap = new Map(existingRaw.map(normalizeConversation).map((item) => [item.id, item]));
  for (const conversation of incoming) {
    if (!conversationMap.has(conversation.id)) {
      conversationMap.set(conversation.id, conversation);
    }
  }
  await storage.setItem(IMAGE_CONVERSATIONS_KEY, sortImageConversations([...conversationMap.values()]));
}

async function ensureLegacyConversationMigration() {
  if (!imageConversationMigrationPromise) {
    imageConversationMigrationPromise = (async () => {
      const migrated = await legacyImageConversationStorage.getItem<boolean>(IMAGE_CONVERSATIONS_LEGACY_MIGRATION_KEY);
      if (migrated) {
        return;
      }

      const legacyRaw =
        (await legacyImageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
          IMAGE_CONVERSATIONS_KEY,
        )) || [];
      await mergeMigratedConversations(singleImageConversationStorage, legacyRaw.map(normalizeConversation));
      await legacyImageConversationStorage.setItem(IMAGE_CONVERSATIONS_LEGACY_MIGRATION_KEY, true);
    })().catch((error) => {
      imageConversationMigrationPromise = null;
      throw error;
    });
  }
  await imageConversationMigrationPromise;
}

function queueImageConversationWrite<T>(operation: () => Promise<T>): Promise<T> {
  const result = imageConversationWriteQueue.then(operation);
  imageConversationWriteQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function currentImageConversationStorageKey() {
  const session = await getStoredAuthSession().catch(() => null);
  const owner = String(session?.subjectId || session?.username || "").trim();
  return owner ? `${ACCOUNT_IMAGE_CONVERSATIONS_PREFIX}${owner}` : ANONYMOUS_IMAGE_CONVERSATIONS_KEY;
}

async function writeStoredImageConversations(conversations: ImageConversation[]): Promise<void> {
  const key = await currentImageConversationStorageKey();
  await singleImageConversationStorage.setItem(key, sortImageConversations(conversations.map(compactConversationForStorage)));
}

async function currentAuthHeaders() {
  const key = await getStoredAuthKey().catch(() => "");
  return key ? { Authorization: `Bearer ${key}` } : undefined;
}

async function readStoredImageConversations(): Promise<ImageConversation[]> {
  const key = await currentImageConversationStorageKey();
  const items =
    (await singleImageConversationStorage.getItem<Array<ImageConversation & Record<string, unknown>>>(
      key,
    )) || [];
  return items.map(normalizeConversation);
}

async function syncRemoteConversation(conversation: ImageConversation, headers?: Record<string, string>) {
  try {
    await upsertImageConversationRemote(slimConversationForRemote(conversation), headers);
  } catch {
    // The account-scoped IndexedDB copy remains available if the API is briefly unreachable.
  }
}

async function syncRemoteConversations(conversations: ImageConversation[]) {
  const headers = await currentAuthHeaders();
  await Promise.allSettled(conversations.map((conversation) => syncRemoteConversation(conversation, headers)));
}

export async function listImageConversations(): Promise<ImageConversation[]> {
  const localItems = sortImageConversations(await readStoredImageConversations());
  try {
    const remote = await fetchImageConversationsRemote();
    const remoteItems = sortImageConversations(
      remote.items.map((item) => normalizeConversation(item as ImageConversation & Record<string, unknown>)),
    );
    if (remoteItems.length) {
      const conversationMap = new Map(localItems.map((item) => [item.id, item]));
      for (const conversation of remoteItems) {
        const current = conversationMap.get(conversation.id);
        conversationMap.set(conversation.id, current ? pickLatestConversation(current, conversation) : conversation);
      }
      const mergedItems = sortImageConversations([...conversationMap.values()]);
      await writeStoredImageConversations(mergedItems);
      return mergedItems;
    }
    if (localItems.length) {
      void syncRemoteConversations(localItems);
      return localItems;
    }
    return [];
  } catch {
    return localItems;
  }
}

export async function saveImageConversations(
  conversations: ImageConversation[],
): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const conversationMap = new Map(items.map((item) => [item.id, item]));
    for (const conversation of conversations.map(normalizeConversation)) {
      const current = conversationMap.get(conversation.id);
      conversationMap.set(conversation.id, current ? pickLatestConversation(current, conversation) : conversation);
    }
    await singleImageConversationStorage.setItem(
      await currentImageConversationStorageKey(),
      sortImageConversations([...conversationMap.values()].map(compactConversationForStorage)),
    );
    void syncRemoteConversations(conversations.map(normalizeConversation));
  });
}

export async function saveImageConversation(
  conversation: ImageConversation,
): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const nextConversation = normalizeConversation(conversation);
    const current = items.find((item) => item.id === nextConversation.id);
    const persistedConversation = current ? pickLatestConversation(current, nextConversation) : nextConversation;
    const nextItems = sortImageConversations([
      persistedConversation,
      ...items.filter((item) => item.id !== persistedConversation.id),
    ]);
    await writeStoredImageConversations(nextItems);
    const headers = await currentAuthHeaders();
    void syncRemoteConversation(persistedConversation, headers);
  });
}

export async function renameImageConversation(
  id: string,
  title: string,
): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    const target = items.find((item) => item.id === id);
    if (!target) return;
    const updated = { ...target, title, updatedAt: new Date().toISOString() };
    const nextItems = sortImageConversations([
      updated,
      ...items.filter((item) => item.id !== id),
    ]);
    await writeStoredImageConversations(nextItems);
    const headers = await currentAuthHeaders();
    try {
      await renameImageConversationRemote(id, title, headers);
    } catch {
      void syncRemoteConversation(updated, headers);
    }
  });
}

export async function deleteImageConversation(
  id: string,
): Promise<void> {
  await queueImageConversationWrite(async () => {
    const items = await readStoredImageConversations();
    await writeStoredImageConversations(items.filter((item) => item.id !== id));
    const headers = await currentAuthHeaders();
    try {
      await deleteImageConversationRemote(id, headers);
    } catch {
      // Keep the local delete even when offline; the server copy will be refreshed on the next successful save.
    }
  });
}

export async function clearImageConversations(): Promise<void> {
  await queueImageConversationWrite(async () => {
    await singleImageConversationStorage.removeItem(await currentImageConversationStorageKey());
    await singleImageConversationStorage.removeItem(IMAGE_CONVERSATIONS_KEY);
    const headers = await currentAuthHeaders();
    try {
      await clearImageConversationsRemote(headers);
    } catch {
      // Local clear still protects this browser session if the API is temporarily unavailable.
    }
  });
}

export function getImageConversationStats(conversation: ImageConversation | null): ImageConversationStats {
  if (!conversation) {
    return { queued: 0, running: 0 };
  }

  return conversation.turns.reduce(
    (acc, turn) => {
      if (turn.resultsDeleted) {
        return acc;
      }
      if (turn.status === "queued") {
        acc.queued += 1;
      } else if (turn.status === "generating") {
        acc.running += 1;
      }
      return acc;
    },
    { queued: 0, running: 0 },
  );
}
