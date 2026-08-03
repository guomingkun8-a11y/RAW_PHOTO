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
};

const userSession = {
  role: "user",
  subject_id: "user-1",
  username: "tester",
  name: "测试用户",
  token: "e2e-token",
};

function imageTaskFromBody(body: Record<string, unknown>) {
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
    data: [{ b64_json: fakePngBase64, revised_prompt: String(body.prompt || "") }],
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
    ...overrides,
  };

  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

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
          data: [{
            id: "gpt-image-2",
            object: "model",
            created: 0,
            owned_by: "mock",
            permission: [],
            root: "gpt-image-2",
            parent: null,
          }],
        },
      });
      return;
    }
    if (path === "/api/prompt-templates") {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }
    if (path === "/api/users") {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }
    if (path === "/api/image-conversations") {
      if (request.method() === "GET") {
        await route.fulfill({ json: [] });
        return;
      }
      if (request.method() === "DELETE") {
        await route.fulfill({ json: { ok: true, deleted: 0 } });
        return;
      }
    }
    if (path.startsWith("/api/image-conversations/")) {
      if (request.method() === "PUT") {
        const body = request.postDataJSON() as { conversation?: unknown };
        await route.fulfill({ json: body.conversation || { ok: true } });
        return;
      }
      await route.fulfill({ json: { ok: true } });
      return;
    }
    if (path === "/api/image-tasks/generations") {
      const body = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: imageTaskFromBody(body) });
      return;
    }
    if (path === "/api/image-tasks/edits") {
      const raw = request.postData() || "";
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
          items: (body.ids || []).map((id) => imageTaskFromBody({ client_task_id: id })),
          missing_ids: [],
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
  await page.getByTestId("login-username").fill("tester");
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
  await expect(page.getByTestId("image-prompt-input")).toBeVisible();
}

test("login redirects to the image workspace", async ({ page }) => {
  await mockAppApi(page);

  await loginThroughUi(page);
});

test("registration creates a user session and enters the workspace", async ({ page }) => {
  await mockAppApi(page);

  await page.goto("/register");
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
  await mockAppApi(page);
  await loginThroughUi(page);

  await page.getByTestId("image-prompt-input").fill("生成一张白底商品主图");
  await page.getByTestId("generate-submit-button").click();
  await expect(page.getByTestId("generated-image")).toHaveCount(1);

  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByTestId("pick-batch-folder-button").click();
  const chooser = await chooserPromise;
  await chooser.setFiles(path.resolve("e2e/fixtures/batch-folder"));
  await page.getByTestId("image-prompt-input").fill("每张图片生成同风格商品海报");
  await page.getByTestId("generate-submit-button").click();

  await expect(page.getByTestId("generated-image")).toHaveCount(2);
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
