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
  dataUrl: string;
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

export type ImageTurn = {
  id: string;
  prompt: string;
  model: ImageModel;
  mode: ImageConversationMode;
  referenceImages: StoredReferenceImage[];
  batchReplace?: ImageBatchReplacePlan;
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
  name: "chatgpt2api",
  storeName: "image_conversations",
});

const singleImageConversationStorage = localforage.createInstance({
  name: "chatgpt2api",
  storeName: "image_single_conversations",
});

const IMAGE_CONVERSATIONS_KEY = "items";
const ACCOUNT_IMAGE_CONVERSATIONS_PREFIX = "items:account:";
const ANONYMOUS_IMAGE_CONVERSATIONS_KEY = "items:anonymous";
const IMAGE_CONVERSATIONS_LEGACY_MIGRATION_KEY = "legacy_migration_v1";
let imageConversationWriteQueue: Promise<void> = Promise.resolve();
let imageConversationMigrationPromise: Promise<void> | null = null;

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
    dataUrl: image.dataUrl,
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
    .filter((image): image is StoredReferenceImage => Boolean(image?.dataUrl))
    .map(normalizeReferenceImage);
  if (!productImage.dataUrl || folderImages.length === 0) {
    return undefined;
  }
  return { productImage, folderImages };
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
        return typeof candidate.dataUrl === "string" && candidate.dataUrl.length > 0;
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

  return {
    id: String(turn.id || `${Date.now()}`),
    prompt: String(turn.prompt || ""),
    model: (turn.model as ImageModel) || "gpt-image-2",
    mode: turn.mode === "edit" ? "edit" : "generate",
    referenceImages: getLegacyReferenceImages(turn),
    batchReplace: normalizeBatchReplacePlan(turn.batchReplace),
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
    status:
      turn.status === "queued" ||
      turn.status === "generating" ||
      turn.status === "success" ||
      turn.status === "error" ||
      turn.status === "canceled"
        ? turn.status
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

function sortImageConversations(conversations: ImageConversation[]): ImageConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getTimestamp(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function pickLatestConversation(current: ImageConversation, next: ImageConversation) {
  return getTimestamp(next.updatedAt) >= getTimestamp(current.updatedAt) ? next : current;
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
  await singleImageConversationStorage.setItem(key, sortImageConversations(conversations));
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
    await upsertImageConversationRemote(conversation as ImageConversationApiPayload, headers);
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
      await writeStoredImageConversations(remoteItems);
      return remoteItems;
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
      sortImageConversations([...conversationMap.values()]),
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
