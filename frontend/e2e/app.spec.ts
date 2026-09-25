import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { expect, type Page, test } from "@playwright/test";

const fakePngBase64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";
const fakePngDataUrl = `data:image/png;base64,${fakePngBase64}`;
const captchaDataUrl =
  "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iNDgiPjx0ZXh0IHg9IjIwIiB5PSIzMCIgZm9udC1zaXplPSIyMCI+MTIzNDwvdGV4dD48L3N2Zz4=";

type MockState = {
  authenticated: boolean;
  libraryItems: Array<Record<string, unknown>>;
  libraryDownloadRequests: number;
  libraryDeleteRequests: number;
  imageAgentRequests: Array<Record<string, unknown>>;
  folderUploadBodies: string[];
  generationTaskRequests: Array<Record<string, unknown>>;
  editTaskRequests: string[];
  generationResultUrl?: string;
  generatedImageCost?: number;
  externalImageRequests: number;
  imageConversations: Array<Record<string, unknown>>;
  agentMemories: Array<Record<string, unknown>>;
};

const userSession = {
  role: "user",
  subject_id: "user-1",
  username: "tester",
  name: "测试用户",
  token: "e2e-token",
};

function imageTaskFromBody(body: Record<string, unknown>, resultUrl = "") {
  const id = String(body.client_task_id || `task-${Date.now()}`);
  return {
    id,
    status: "success",
    mode: body.image ? "edit" : "generate",
    model: String(body.model || "gpt-image-2"),
    size: String(body.size || "1024x1024"),
    quality: String(body.quality || "auto"),
    conversation_id: body.conversation_id,
    created_at: "2026-08-02T00:00:00Z",
    updated_at: "2026-08-02T00:00:01Z",
    duration_ms: 1280,
    data: [{
      ...(resultUrl ? { url: resultUrl } : { b64_json: fakePngBase64 }),
      revised_prompt: String(body.prompt || ""),
    }],
  };
}

function parseMultipartField(raw: string, field: string) {
  const escaped = field.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = raw.match(new RegExp(`name="${escaped}"\\r?\\n\\r?\\n([^\\r\\n]+)`));
  return match?.[1]?.trim() || "";
}

async function mockAppApi(page: Page, overrides: Partial<MockState> = {}) {
  const state: MockState = {
    authenticated: false,
    libraryItems: [
      {
        id: 1,
        task_id: "task-library-1",
        owner_id: "user-1",
        mode: "generate",
        model: "gpt-image-2",
        prompt: "白底商品主图",
        size: "1024x1024",
        image_rel: "mock/one.png",
        image_url: fakePngDataUrl,
        thumbnail_url: fakePngDataUrl,
        width: 1024,
        height: 1024,
        file_size: 1024,
        favorite: false,
        created_at: "2026-08-02T00:00:00Z",
      },
      {
        id: 2,
        task_id: "task-library-2",
        owner_id: "user-1",
        mode: "generate",
        model: "gpt-image-2",
        prompt: "场景商品图",
        size: "1024x1024",
        image_rel: "mock/two.png",
        image_url: fakePngDataUrl,
        thumbnail_url: fakePngDataUrl,
        width: 1024,
        height: 1024,
        file_size: 2048,
        favorite: false,
        created_at: "2026-08-02T00:00:00Z",
      },
    ],
    libraryDownloadRequests: 0,
    libraryDeleteRequests: 0,
    imageAgentRequests: [],
    folderUploadBodies: [],
    generationTaskRequests: [],
    editTaskRequests: [],
    generationResultUrl: "",
    externalImageRequests: 0,
    imageConversations: [],
    agentMemories: [{
      id: 1,
      memoryId: "1",
      scope: "user",
      scopeId: "user-1",
      memoryKey: "preference:1",
      category: "visual_style",
      content: "汽车详情页优先使用真实环境背景",
      confidence: 0.9,
      confirmed: true,
      status: "active",
      createdAt: "2026-08-02T00:00:00",
      updatedAt: "2026-08-02T00:00:00",
    }],
    ...overrides,
  };

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (url.hostname === "assets.example") {
      state.externalImageRequests += 1;
      await route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from(fakePngBase64, "base64") });
      return;
    }

    if (path === "/auth/captcha") {
      await route.fulfill({ json: { ok: true, captcha_id: "captcha-1", image_data_url: captchaDataUrl } });
      return;
    }
    if (path === "/auth/login" || path === "/auth/register") {
      state.authenticated = true;
      await route.fulfill({ json: { ok: true, version: "e2e", ...userSession } });
      return;
    }
    if (path === "/api/auth/me") {
      if (!state.authenticated) {
        await route.fulfill({ status: 401, json: { detail: { error: "login required" } } });
        return;
      }
      await route.fulfill({ json: { ok: true, ...userSession } });
      return;
    }
    if (path === "/api/settings") {
      await route.fulfill({
        json: {
          config: {
            openai_relay: { enabled: true, has_api_key: true, api_key_count: 1 },
            image_task_queue: { enabled: true, owner_concurrency: 3, owner_pending_limit: 30 },
            image_reference_upload: { enabled: false, provider: "oss" },
            image_storage: { enabled: false, mode: "local", provider: "minio", public_base_url: "" },
          },
        },
      });
      return;
    }
    if (path === "/v1/models") {
      await route.fulfill({
        json: {
          object: "list",
          data: ["gpt-image-2", "banana-pro", "tt-image-2.5-flare-token", "kling-v3-video"].map((id) => ({
            id,
            object: "model",
            created: 0,
            owned_by: "mock",
            permission: [],
            root: id,
            parent: null,
          })),
        },
      });
      return;
    }
    if (path === "/api/prompt-templates") {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }
    if (path === "/api/image-agent/runs") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.imageAgentRequests.push(body);
      const runId = `mock-cow-agent-run-${state.imageAgentRequests.length}`;
      await route.fulfill({
        json: {
          agentRun: {
            runId,
            agent: "cowagent-professional",
            status: "pending",
            startedAt: "2026-08-02T00:00:00Z",
            maxSteps: 12,
            stepCount: 0,
            toolCalls: 0,
            steps: [],
            events: [],
          },
        },
      });
      return;
    }
    if (path === "/api/image-agent/memory" && request.method() === "GET") {
      const includePending = url.searchParams.get("includePending") === "true";
      await route.fulfill({
        json: {
          items: state.agentMemories.filter((item) => (
            String(item.status) === "active" && item.confirmed
          ) || (includePending && String(item.status) === "pending_review")),
          counts: {
            active: state.agentMemories.filter((item) => item.status === "active" && item.confirmed).length,
            pendingReview: state.agentMemories.filter((item) => item.status === "pending_review").length,
          },
        },
      });
      return;
    }
    if (path === "/api/image-agent/memory" && request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      const id = String(state.agentMemories.length + 1);
      const item = {
        id: Number(id),
        memoryId: id,
        scope: String(body.scope || "user"),
        scopeId: String(body.scopeId || body.conversationId || "user-1"),
        memoryKey: `manual:${id}`,
        category: String(body.category || "note"),
        content: String(body.content || ""),
        confidence: 1,
        confirmed: true,
        status: "active",
        createdAt: "2026-08-02T00:00:00",
        updatedAt: "2026-08-02T00:00:00",
      };
      state.agentMemories.unshift(item);
      await route.fulfill({ json: { item } });
      return;
    }
    if (path === "/api/image-agent/memory" && request.method() === "DELETE") {
      const deleted = state.agentMemories.length;
      state.agentMemories = [];
      await route.fulfill({ json: { ok: true, deleted } });
      return;
    }
    const memoryReviewMatch = path.match(/^\/api\/image-agent\/memory\/(\d+)\/review$/);
    if (memoryReviewMatch && request.method() === "POST") {
      const memoryId = memoryReviewMatch[1];
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = state.agentMemories.find((item) => String(item.memoryId) === memoryId) || {};
      const item = {
        ...existing,
        memoryId,
        confirmed: body.decision === "approve",
        status: body.decision === "approve" ? "active" : "rejected",
        confidence: body.decision === "approve" ? 0.9 : existing.confidence,
        updatedAt: "2026-08-02T00:01:00",
      };
      state.agentMemories = state.agentMemories.map((current) => String(current.memoryId) === memoryId ? item : current);
      await route.fulfill({ json: { item } });
      return;
    }
    if (path.startsWith("/api/image-agent/memory/") && request.method() === "PATCH") {
      const memoryId = path.split("/").pop() || "";
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = state.agentMemories.find((item) => String(item.memoryId) === memoryId) || {};
      const item = { ...existing, ...body, memoryId, updatedAt: "2026-08-02T00:01:00" };
      state.agentMemories = state.agentMemories.map((current) => String(current.memoryId) === memoryId ? item : current);
      await route.fulfill({ json: { item } });
      return;
    }
    if (path.startsWith("/api/image-agent/memory/") && request.method() === "DELETE") {
      const memoryId = path.split("/").pop() || "";
      state.agentMemories = state.agentMemories.filter((item) => String(item.memoryId) !== memoryId);
      await route.fulfill({ json: { ok: true } });
      return;
    }
    if (path === "/api/image-agent/folders" && request.method() === "POST") {
      state.folderUploadBodies.push(request.postData() || "");
      await route.fulfill({
        json: {
          folderId: "folder-e2e-1",
          name: "batch-folder",
          status: "ready",
          itemCount: 3,
          totalBytes: 300,
          summary: {
            fileCount: 3,
            categories: { main: 1, scene: 1, detail: 1 },
          },
          items: [],
        },
      });
      return;
    }
    const cowAgentEventMatch = path.match(/^\/api\/agent\/runs\/(mock-cow-agent-run-\d+)\/events$/);
    if (cowAgentEventMatch) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: [
          { sequence: 1, type: "run.started", timestamp: "2026-08-02T00:00:00Z", payload: { engine: "cowagent" } },
          { sequence: 2, type: "agent.update", timestamp: "2026-08-02T00:00:01Z", payload: { phase: "analysis", summary: "CowAgent 已读取专业知识和对话记忆。" } },
          { sequence: 3, type: "run.completed", timestamp: "2026-08-02T00:00:02Z", payload: {} },
        ].map((event) => `id: ${event.sequence}\ndata: ${JSON.stringify(event)}\n\n`).join(""),
      });
      return;
    }
    const cowAgentRunMatch = path.match(/^\/api\/agent\/runs\/(mock-cow-agent-run-\d+)$/);
    if (cowAgentRunMatch) {
      const includeResult = url.searchParams.get("includeResult") === "true";
      const requestIndex = Number(cowAgentRunMatch[1].split("-").pop() || "1") - 1;
      const agentRequest = state.imageAgentRequests[requestIndex];
      const shouldGenerate = /generate|execute|render/i.test(String(agentRequest?.prompt || ""));
      const shouldRemember = /记住/.test(String(agentRequest?.prompt || ""));
      if (includeResult && shouldRemember && !state.agentMemories.some((item) => item.memoryId === "99")) {
        state.agentMemories.unshift({
          id: 99,
          memoryId: "99",
          scope: "user",
          scopeId: "user-1",
          memoryKey: "explicit:99",
          category: "visual_style",
          content: "汽车详情页默认使用真实道路背景",
          confidence: 0.95,
          confirmed: true,
          status: "active",
          createdAt: "2026-08-02T00:00:00",
          updatedAt: "2026-08-02T00:00:00",
        });
      }
      await route.fulfill({
        json: {
          agentRun: {
            runId: cowAgentRunMatch[1],
            agent: "cowagent-professional",
            status: includeResult ? "completed" : "running",
            startedAt: "2026-08-02T00:00:00Z",
            finishedAt: includeResult ? "2026-08-02T00:00:02Z" : undefined,
            durationMs: includeResult ? 2000 : undefined,
            maxSteps: 20,
            stepCount: includeResult ? 2 : 0,
            toolCalls: includeResult ? 1 : 0,
            steps: [],
            events: [],
            ...(includeResult ? {
              result: {
                phase: "completed",
                promptPlan: { model: "gpt-5.6-sol", sceneType: "auto", sceneName: "CowAgent open workflow" },
                proposal: {},
                images: shouldGenerate ? [{ taskId: "mock-cowagent-image-1", url: fakePngDataUrl, width: 1, height: 1, cost: state.generatedImageCost }] : [],
                qualityChecks: [],
                assistantMessage: "你好，我可以帮你规划商品主图、详情页、场景图和文字排版。",
                suggestions: [],
                intent: "cow_agent",
                recommendedAction: "continue",
                creativeBrief: {},
                optimizationRoute: "direct_consult",
                modelUsage: {
                  dialogueCalls: 1,
                  visionCalls: 0,
                  totalCalls: 1,
                  imageGenerationCalls: 0,
                },
                knowledgeSources: [{ id: "professional-scope", title: "商业视觉专业知识" }],
                memorySources: agentRequest?.useLongTermMemory === false ? [] : [{ id: "1", title: "汽车详情页优先使用真实环境背景", scope: "user", category: "visual_style" }],
                memoryUpdates: shouldRemember ? [{ memoryId: "99", content: "汽车详情页默认使用真实道路背景", category: "visual_style", scope: "user", scopeId: "user-1" }] : [],
              },
            } : {}),
          },
        },
      });
      return;
    }
    if (path === "/api/users") {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }
    if (path === "/api/system/announcements") {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }
    if (path === "/api/image-conversations") {
      if (request.method() === "GET") {
        await route.fulfill({ json: { items: state.imageConversations, total: state.imageConversations.length } });
        return;
      }
      if (request.method() === "DELETE") {
        await route.fulfill({ json: { ok: true, deleted: 0 } });
        return;
      }
    }
    if (path.startsWith("/api/image-conversations/")) {
      if (request.method() === "PUT") {
        const body = request.postDataJSON() as { conversation?: Record<string, unknown> };
        if (body.conversation) {
          const id = String(body.conversation.id || "");
          state.imageConversations = [
            body.conversation,
            ...state.imageConversations.filter((conversation) => String(conversation.id || "") !== id),
          ];
        }
        await route.fulfill({ json: body.conversation || { ok: true } });
        return;
      }
      await route.fulfill({ json: { ok: true } });
      return;
    }
    if (path === "/api/image-tasks/generations") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.generationTaskRequests.push(body);
      await route.fulfill({ json: { ...imageTaskFromBody(body, state.generationResultUrl), cost: state.generatedImageCost } });
      return;
    }
    if (path === "/api/image-tasks/edits") {
      const raw = request.postData() || "";
      state.editTaskRequests.push(raw);
      await route.fulfill({
        json: imageTaskFromBody({
          image: true,
          client_task_id: parseMultipartField(raw, "client_task_id"),
          prompt: parseMultipartField(raw, "prompt"),
          model: parseMultipartField(raw, "model"),
          size: parseMultipartField(raw, "size"),
          quality: parseMultipartField(raw, "quality"),
          conversation_id: parseMultipartField(raw, "conversation_id"),
        }),
      });
      return;
    }
    if (path === "/api/image-tasks/query") {
      const body = request.postDataJSON() as { ids?: string[] };
      await route.fulfill({
        json: {
          items: [],
          missing_ids: body.ids || [],
        },
      });
      return;
    }
    if (path === "/api/image-library") {
      const limit = Number(url.searchParams.get("limit") || 20);
      const offset = Number(url.searchParams.get("offset") || 0);
      const items = state.libraryItems.slice(offset, offset + limit);
      await route.fulfill({
        json: {
          items,
          total: state.libraryItems.length,
          limit,
          offset,
          has_more: offset + items.length < state.libraryItems.length,
          next_cursor: null,
        },
      });
      return;
    }
    if (path === "/api/image-library/download-zip") {
      state.libraryDownloadRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/zip",
        body: Buffer.from("mock zip"),
      });
      return;
    }
    if (path === "/api/image-library/bulk-delete") {
      state.libraryDeleteRequests += 1;
      const body = request.postDataJSON() as { ids?: number[] };
      const ids = new Set(body.ids || []);
      state.libraryItems = state.libraryItems.filter((item) => !ids.has(Number(item.id)));
      await route.fulfill({ json: { requested: ids.size, deleted: ids.size, missing: 0 } });
      return;
    }

    await route.continue();
  });

  return state;
}

async function loginThroughUi(page: Page) {
  await page.goto("/login");
  const username = page.getByTestId("login-username");
  const password = page.getByTestId("login-password");
  await username.click();
  await expect(username).not.toHaveAttribute("readonly", "");
  await username.fill("tester");
  await password.fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
  await expect(page.getByTestId("image-prompt-input")).toBeVisible();
}

for (const version of ["flare", "sunburst"]) {
  for (const mode of ["standard", "agent"]) {
    test(`gpt-image-2.5 ${version} ${mode} keeps model and cost in history`, async ({ page }) => {
      const state = await mockAppApi(page, { generatedImageCost: 0.35, generationResultUrl: fakePngDataUrl });
      if (version === "sunburst") await page.setViewportSize({ width: 390, height: 844 });
      await loginThroughUi(page);
      if (mode === "agent") await page.getByRole("button", { name: "智能体", exact: true }).click();
      await page.getByTestId(mode === "agent" ? "agent-generation-preferences-toggle" : "image-model-card-toggle").click();
      const select = page.locator(mode === "agent" ? "#composer-preferences-card-panel select" : "#composer-model-card-panel select");
      await expect(select.locator("option")).toHaveCount(4);
      await expect(select.locator('option[value="gpt-image-2.5-flare"]')).toHaveCount(1);
      await expect(select.locator('option[value="gpt-image-2.5-sunburst"]')).toHaveCount(1);
      await expect(select.locator('option[value="gpt-image-2.5"]')).toHaveCount(0);
      await expect(select.locator('option[value="banana-pro"]')).toHaveCount(0);
      await expect(select.locator('option[value="tt-image-2.5-flare-token"]')).toHaveCount(0);
      await expect(select.locator('option[value="kling-v3-video"]')).toHaveCount(0);
      const model = `gpt-image-2.5-${version}`;
      await select.selectOption(model);
      await select.press("Escape");
      await page.getByTestId("image-prompt-input").fill("generate a product image");
      await page.getByTestId("generate-submit-button").click();
      const requests = mode === "agent" ? state.imageAgentRequests : state.generationTaskRequests;
      await expect.poll(() => requests.length).toBe(1);
      expect(requests[0].model).toBe(model);
      await expect(page.getByTestId("generated-image")).toHaveCount(1);
      await expect(page.getByText("费用 ￥0.35", { exact: true })).toBeVisible();
      await expect.poll(() => state.imageConversations[0]).toMatchObject({ turns: [{ model, images: [{ cost: 0.35 }] }] });
      await page.reload();
      await expect(page.getByTestId("generated-image")).toHaveCount(1);
      await expect(page.getByText("费用 ￥0.35", { exact: true })).toBeVisible();
      await page.screenshot({ path: `test-results/image-25-${version}-${mode}.png`, fullPage: true });
    });
  }
}

test("login redirects to the image workspace", async ({ page }) => {
  await mockAppApi(page);

  await loginThroughUi(page);
});

test("sidebar generation history opens the history panel", async ({ page }) => {
  await mockAppApi(page);

  await loginThroughUi(page);
  await page.getByRole("link", { name: /生成历史记录/ }).click();
  await expect(page).toHaveURL(/\/image\?history=1$/);
  await expect(page.getByRole("heading", { name: "生成历史记录" })).toBeVisible();
});

test("registration creates a user session and enters the workspace", async ({ page }) => {
  await mockAppApi(page);

  await page.goto("/register");
  await page.getByTestId("auth-register-tab").click();
  await expect(page.getByTestId("register-submit")).toBeEnabled();
  await page.getByTestId("register-username").fill("new-user");
  await page.getByTestId("register-name").fill("新用户");
  await page.getByTestId("register-password").fill("password123");
  await page.getByTestId("register-confirm-password").fill("password123");
  await page.getByTestId("register-captcha-code").fill("1234");
  await page.getByTestId("register-submit").click();

  await expect(page).toHaveURL(/\/image$/);
  await expect(page.getByTestId("image-prompt-input")).toBeVisible();
});

test("image generation and folder batch generation render completed results", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-prompt-input").fill("生成一张白底商品主图");
  await page.getByTestId("generate-submit-button").click();
  await expect(page.getByTestId("generated-image")).toHaveCount(1);
  await expect(page.getByTestId("generated-image").first()).toHaveClass(/object-contain/);
  await expect.poll(() => state.generationTaskRequests.length).toBe(1);
  expect(state.generationTaskRequests[0].prompt_engine_mode).toBe("standard");

  const composerBox = await page.locator(".composer-prompt-shell--home").boundingBox();
  const assistantCardBox = await page.locator(".chat-message-row--assistant").first().locator(":scope > div").last().boundingBox();
  const userBubbleBox = await page.locator(".chat-message-row--user").first().locator(":scope > div").first().boundingBox();
  expect(composerBox).not.toBeNull();
  expect(assistantCardBox).not.toBeNull();
  expect(userBubbleBox).not.toBeNull();
  if (!composerBox || !assistantCardBox || !userBubbleBox) throw new Error("image conversation layout is unavailable");
  expect(assistantCardBox.x).toBeGreaterThanOrEqual(composerBox.x - 1);
  expect(assistantCardBox.x + assistantCardBox.width).toBeLessThanOrEqual(composerBox.x + composerBox.width + 1);
  expect(userBubbleBox.x).toBeGreaterThanOrEqual(composerBox.x - 1);
  expect(userBubbleBox.x + userBubbleBox.width).toBeLessThanOrEqual(composerBox.x + composerBox.width + 1);

  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByTestId("pick-batch-folder-button").click();
  const chooser = await chooserPromise;
  await chooser.setFiles(path.resolve("e2e/fixtures/batch-folder"));
  await page.getByTestId("image-prompt-input").fill("每张图片生成同风格商品海报");
  await page.getByTestId("generate-submit-button").click();

  await expect(page.getByTestId("generated-image")).toHaveCount(3);
  await expect(page.getByText(/2 轮/)).toBeVisible();
  expect(state.imageConversations).toHaveLength(1);
  expect((state.imageConversations[0] as { turns?: unknown[] }).turns).toHaveLength(2);
});

async function expectWorkspaceWithinViewport(page: Page) {
  await expect.poll(() => page.locator(
    ".image-chat-page, .image-chat-content, .image-chat-composer, .composer-prompt-shell, .image-results-thread",
  ).evaluateAll((elements) => elements.flatMap((element) => {
    const bounds = element.getBoundingClientRect();
    return bounds.left < -1 || bounds.right > window.innerWidth + 1 || element.scrollWidth > element.clientWidth + 1
      ? [{ className: element.className, left: bounds.left, right: bounds.right, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }]
      : [];
  }))).toEqual([]);
}

for (const viewport of [{ width: 1911, height: 930 }, { width: 390, height: 844 }]) {
  test(`many reference images and long prompts fit a ${viewport.width}px workspace`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockAppApi(page);
    await loginThroughUi(page);

    await page.getByTestId("reference-file-input").setInputFiles(Array.from({ length: 48 }, (_, index) => ({
      name: `reference-${index}.png`,
      mimeType: "image/png",
      buffer: Buffer.from(fakePngBase64, "base64"),
    })));
    await expect(page.locator("[data-reference-thumb]")).toHaveCount(48);
    await expectWorkspaceWithinViewport(page);
    const referenceStrip = page.locator(".composer-reference-strip");
    await referenceStrip.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    await expect(page.locator("[data-reference-thumb]").last()).toBeInViewport();
    await expect(page.locator(".composer-reference-assist-button").first()).toBeInViewport();

    await page.getByTestId("image-prompt-input").fill(`Product photo https://example.com/${"a".repeat(500)}`);
    await page.getByTestId("generate-submit-button").click();
    await expect(page.getByTestId("generated-image")).toHaveCount(1);
    await expectWorkspaceWithinViewport(page);
    const userMessage = page.locator(".chat-message-body--user");
    await expect.poll(() => userMessage.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    const savedReferences = userMessage.locator(".overflow-x-auto");
    await expect(savedReferences.locator("button")).toHaveCount(48);
    await savedReferences.scrollIntoViewIfNeeded();
    await savedReferences.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    await expect(savedReferences.locator("button").last()).toBeInViewport();
  });
}

test("a 48-image folder batch stays within the workspace after generation and reload", async ({ page }) => {
  await page.setViewportSize({ width: 1911, height: 930 });
  const state = await mockAppApi(page);
  await loginThroughUi(page);
  const folder = await mkdtemp(path.join(os.tmpdir(), "raw-photo-layout-"));
  try {
    await Promise.all(Array.from({ length: 48 }, (_, index) => writeFile(
      path.join(folder, `photo-${index}.png`), Buffer.from(fakePngBase64, "base64"),
    )));
    const chooserPromise = page.waitForEvent("filechooser");
    await page.getByTestId("pick-batch-folder-button").click();
    await (await chooserPromise).setFiles(folder);
    await page.getByTestId("image-prompt-input").fill("Product photo");
    await page.getByTestId("generate-submit-button").click();
    await expect(page.getByTestId("generated-image")).toHaveCount(48);
    await expect.poll(() => state.editTaskRequests.length).toBe(48);
    await expectWorkspaceWithinViewport(page);
    await page.reload();
    await expect(page.getByTestId("generated-image")).toHaveCount(48);
    await expectWorkspaceWithinViewport(page);
    const savedReferences = page.locator(".chat-message-body--user .overflow-x-auto");
    await expect(savedReferences.locator("button")).toHaveCount(48);
    await savedReferences.scrollIntoViewIfNeeded();
    await savedReferences.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
    await expect(savedReferences.locator("button").last()).toBeInViewport();
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});

test("continued prompts reuse the current conversation until a new task is requested", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-prompt-input").fill("第一轮商品图");
  await page.getByTestId("generate-submit-button").click();
  await expect.poll(() => state.generationTaskRequests.length).toBe(1);
  const conversationId = String(state.generationTaskRequests[0].conversation_id || "");
  expect(conversationId).not.toBe("");

  await page.getByTestId("image-prompt-input").fill("继续调整背景");
  await page.getByTestId("generate-submit-button").click();
  await expect.poll(() => state.generationTaskRequests.length).toBe(2);
  expect(state.generationTaskRequests[1].conversation_id).toBe(conversationId);
  expect(state.imageConversations).toHaveLength(1);
  expect((state.imageConversations[0] as { turns?: unknown[] }).turns).toHaveLength(2);

  await page.reload();
  await expect(page.getByText(/2 轮/)).toBeVisible();
  await page.getByTestId("image-prompt-input").fill("刷新后继续调整");
  await page.getByTestId("generate-submit-button").click();
  await expect.poll(() => state.generationTaskRequests.length).toBe(3);
  expect(state.generationTaskRequests[2].conversation_id).toBe(conversationId);

  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByTestId("image-prompt-input").fill("另一个独立任务");
  await page.getByTestId("generate-submit-button").click();
  await expect.poll(() => state.generationTaskRequests.length).toBe(4);
  expect(state.generationTaskRequests[3].conversation_id).not.toBe(conversationId);
  expect(state.imageConversations).toHaveLength(2);
});

test("continue edit keeps remote result URLs out of browser fetches", async ({ page }) => {
  const remoteUrl = "https://assets.example/generated-result.png";
  const state = await mockAppApi(page, { generationResultUrl: remoteUrl });
  await loginThroughUi(page);

  await page.getByTestId("image-prompt-input").fill("product image");
  await page.getByTestId("generate-submit-button").click();
  await expect(page.getByTestId("generated-image")).toHaveCount(1);
  await expect.poll(() => state.externalImageRequests).toBeGreaterThan(0);
  const requestsBeforeContinue = state.externalImageRequests;

  await page.getByRole("button", { name: "继续编辑" }).first().click();
  await expect(page.getByText("已加入当前参考图，继续输入描述即可编辑")).toBeVisible();
  await expect(page.getByRole("button", { name: "移除参考图" })).toBeVisible();
  expect(state.externalImageRequests).toBe(requestsBeforeContinue);

  await page.getByTestId("image-prompt-input").fill("change the background");
  await page.getByTestId("generate-submit-button").click();
  await expect.poll(() => state.editTaskRequests.length).toBe(1);
  expect(parseMultipartField(state.editTaskRequests[0], "image_url")).toBe(remoteUrl);
});

test("professional mode runs the agent and shows its advice", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("image-prompt-input").fill("先分析一张高端家居场景图");
  await page.getByTestId("generate-submit-button").click();

  await expect(page.getByTestId("agent-completed-advice")).toContainText("你好，我可以帮你规划商品主图");
  await expect(page.getByTestId("agent-memory-sources")).toContainText("本次参考 1 条长期记忆");
  expect(state.imageAgentRequests).toHaveLength(1);
  expect(state.imageAgentRequests[0].agentEngine).toBe("cowagent");
  expect(state.imageAgentRequests[0].sceneType).toBe("auto");
  expect(state.generationTaskRequests).toHaveLength(0);
  await expect(page.getByTestId("agent-progress-panel").last()).toContainText("专业模式");
  await expect(page.getByTestId("agent-progress-panel").last()).toContainText("1 次模型调用");
  await expect(page.getByText(/CowAgent/i)).toHaveCount(0);

  const userMessage = page.getByTestId("chat-user-message").last();
  const advisorMessage = page.getByTestId("agent-advisor-message").last();
  const userBubble = await userMessage.locator(".chat-message-body--user").boundingBox();
  const userAvatar = await userMessage.locator(".chat-avatar").boundingBox();
  const advisorAvatar = await advisorMessage.locator(".chat-avatar").boundingBox();
  const advisorCard = await page.getByTestId("agent-response-card").last().boundingBox();
  const executionPanel = await page.getByTestId("agent-progress-panel").last().boundingBox();
  expect(userBubble).not.toBeNull();
  expect(userAvatar).not.toBeNull();
  expect(advisorAvatar).not.toBeNull();
  expect(advisorCard).not.toBeNull();
  expect(executionPanel).not.toBeNull();
  expect(userAvatar!.x).toBeGreaterThan(userBubble!.x);
  expect(advisorAvatar!.x).toBeLessThan(advisorCard!.x);
  expect(executionPanel!.x).toBeGreaterThanOrEqual(advisorCard!.x);
  expect(executionPanel!.x + executionPanel!.width).toBeLessThanOrEqual(advisorCard!.x + advisorCard!.width + 1);
  await expect(page.getByTestId("agent-response-card").last().getByTestId("agent-progress-panel")).toHaveCount(1);
});

test("professional memory panel shows only saved memories and supports simple management", async ({ page }) => {
  const state = await mockAppApi(page);
  state.agentMemories.unshift({
    id: 2,
    memoryId: "2",
    scope: "user",
    scopeId: "user-1",
    memoryKey: "candidate:2",
    category: "visual_style",
    content: "汽车详情页默认使用夜间道路背景",
    confidence: 0.82,
    confirmed: false,
    status: "pending_review",
    createdAt: "2026-08-02T00:00:00",
    updatedAt: "2026-08-02T00:00:00",
  });
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("agent-memory-button").click();

  await expect(page.getByRole("heading", { name: "记忆", exact: true })).toBeVisible();
  await expect(page.getByText("汽车详情页优先使用真实环境背景")).toBeVisible();
  await expect(page.getByText("汽车详情页默认使用夜间道路背景")).toHaveCount(0);
  await expect(page.getByText("1 条已保存记忆")).toBeVisible();
  await expect(page.getByText("采用")).toHaveCount(0);
  await page.getByRole("switch").click();
  await expect(page.getByText("当前创作不会读取记忆")).toBeVisible();

  await page.getByRole("button", { name: "新增", exact: true }).click();
  await page.getByPlaceholder("例如：汽车详情页优先使用真实道路背景").fill("详情页不要使用纯白背景");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("详情页不要使用纯白背景")).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();

  await expect(page.getByTestId("agent-memory-button")).toContainText("临时对话");
  await page.getByTestId("image-prompt-input").fill("先分析汽车详情页方案");
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.imageAgentRequests.length).toBe(1);
  expect(state.imageAgentRequests[0].useLongTermMemory).toBe(false);
  expect(state.agentMemories.some((item) => item.content === "详情页不要使用纯白背景")).toBe(true);
});

test("explicit memory is acknowledged inline and can be undone", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("image-prompt-input").fill("请记住，以后汽车详情页默认使用真实道路背景");
  await page.getByTestId("generate-submit-button").click();

  const update = page.getByTestId("agent-memory-updates");
  await expect(update).toContainText("已记住");
  await expect(update).toContainText("汽车详情页默认使用真实道路背景");
  await update.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(update).toHaveCount(0);
  expect(state.agentMemories.some((item) => item.memoryId === "99")).toBe(false);
});

test("professional CowAgent image results render inside the conversation", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("image-prompt-input").fill("generate a car detail page image");
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.imageAgentRequests.length).toBe(1);
  await expect(page.getByTestId("generated-image")).toHaveCount(1);
  await expect(page.getByTestId("generated-image").first()).toHaveAttribute("src", fakePngDataUrl);
  expect(state.generationTaskRequests).toHaveLength(0);
});

test("professional mode uploads a persistent folder before starting CowAgent", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByRole("button", { name: "智能体", exact: true }).click();
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "分析文件夹", exact: true }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles(path.resolve("e2e/fixtures/batch-folder"));

  await expect(page.getByTestId("agent-folder-asset")).toContainText("3 张图片已保存");
  await page.getByTestId("image-prompt-input").fill("先分析文件夹并给出统一的商业视觉方案");
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.folderUploadBodies.length).toBe(1);
  await expect.poll(() => state.imageAgentRequests.length).toBe(1);
  expect(state.folderUploadBodies[0]).toContain('name="relative_names"');
  expect(state.imageAgentRequests[0].folderId).toBe("folder-e2e-1");
  expect(state.generationTaskRequests).toHaveLength(0);
});

test("professional auto scene is passed to the agent unchanged", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("image-prompt-input").fill("根据商品选择合适的真实场景");
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.imageAgentRequests.length).toBe(1);
  expect(state.imageAgentRequests[0].sceneType).toBe("auto");
  expect(state.imageAgentRequests[0].agentEngine).toBe("cowagent");
});

test("agent answers first and accepts an image-only follow-up in the same conversation", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("image-prompt-input").fill("你好，先告诉我你能怎样规划汽车详情页");
  await page.getByTestId("generate-submit-button").click();

  await expect(page.getByTestId("agent-completed-advice")).toContainText("你好，我可以帮你规划商品主图");
  await expect(page.getByText("你好，我可以帮你规划商品主图、详情页、场景图和文字排版。")).toBeVisible();
  await expect(page.getByTestId("agent-progress-panel").last()).toContainText("专业模式");
  await page.screenshot({ path: "test-results/cowagent-desktop.png", fullPage: true });
  await expect.poll(() => state.imageAgentRequests.length).toBe(1);
  const conversationId = String(state.imageAgentRequests[0].conversationId || "");
  expect(state.imageAgentRequests[0].agentEngine).toBe("cowagent");

  await page.getByTestId("reference-file-input").setInputFiles({
    name: "late-car.png",
    mimeType: "image/png",
    buffer: Buffer.from(fakePngBase64, "base64"),
  });
  await expect(page.getByTestId("generate-submit-button")).toBeEnabled();
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.imageAgentRequests.length).toBe(2);
  const followUp = state.imageAgentRequests[1] as {
    agentEngine?: string;
    conversationId?: string;
    prompt?: string;
    images?: Array<{ name?: string; dataUrl?: string }>;
  };
  expect(followUp.agentEngine).toBe("cowagent");
  expect(followUp.conversationId).toBe(conversationId);
  expect(followUp.prompt).toContain("分析我刚上传的图片");
  expect(followUp.images).toHaveLength(1);
  expect(followUp.images?.[0].name).toBe("late-car.png");
  expect(followUp.images?.[0].dataUrl).toBe(fakePngDataUrl);
});

test("professional follow-ups keep the original product reference and subject identity", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();
  await page.getByTestId("reference-file-input").setInputFiles({
    name: "original-product.png",
    mimeType: "image/png",
    buffer: Buffer.from(fakePngBase64, "base64"),
  });
  await page.getByTestId("image-prompt-input").fill("Analyze this product and propose an ecommerce main image");
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.imageAgentRequests.length).toBe(1);
  const first = state.imageAgentRequests[0] as {
    conversationId?: string;
    mode?: string;
    preserveSubject?: boolean;
    images?: Array<{ name?: string; dataUrl?: string }>;
  };
  expect(first.mode).toBe("edit");
  expect(first.preserveSubject).toBe(true);
  expect(first.images).toHaveLength(1);

  await page.getByTestId("image-prompt-input").fill("Keep my product unchanged and change only the background");
  await page.getByTestId("generate-submit-button").click();

  await expect.poll(() => state.imageAgentRequests.length).toBe(2);
  const followUp = state.imageAgentRequests[1] as {
    conversationId?: string;
    mode?: string;
    preserveSubject?: boolean;
    inheritReferenceImages?: boolean;
    images?: Array<{ name?: string; dataUrl?: string }>;
  };
  expect(followUp.conversationId).toBe(first.conversationId);
  expect(followUp.mode).toBe("edit");
  expect(followUp.preserveSubject).toBe(true);
  expect(followUp.inheritReferenceImages).toBe(true);
  expect(followUp.images).toHaveLength(1);
  expect(followUp.images?.[0]).toEqual(first.images?.[0]);
});

test("professional CowAgent controls fit a mobile workspace", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-engine-card-toggle").click();
  await page.getByRole("button", { name: "智能体", exact: true }).click();

  const overflow = await page.locator(".composer-control-surface").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
  await page.getByTestId("image-prompt-input").fill("先分析汽车详情页的视觉方向");
  await page.getByTestId("generate-submit-button").click();
  await expect(page.getByTestId("agent-completed-advice")).toBeVisible();
  const pageOverflow = await page.locator("html").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(pageOverflow.scrollWidth).toBeLessThanOrEqual(pageOverflow.clientWidth + 1);
  await page.screenshot({ path: "test-results/cowagent-mobile.png", fullPage: true });
});

test("image library supports bulk download and bulk delete", async ({ page }) => {
  const state = await mockAppApi(page);
  await loginThroughUi(page);

  await page.goto("/image-library");
  await expect(page.getByTestId("library-image-card")).toHaveCount(2);
  await page.getByTestId("library-select-visible").check();

  await page.getByTestId("library-bulk-download").click();
  await expect.poll(() => state.libraryDownloadRequests).toBe(1);

  await page.getByTestId("library-bulk-delete").click();
  await page.getByTestId("library-bulk-delete-confirm").click();

  await expect.poll(() => state.libraryDeleteRequests).toBe(1);
  await expect(page.getByTestId("library-image-card")).toHaveCount(0);
});
