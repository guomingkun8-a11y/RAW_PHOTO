import { expect, type Page, type Route, test } from "@playwright/test";
import type { VideoAgentPlan, VideoGenerationTask } from "../src/lib/api";

function task(id: string, patch: Partial<VideoGenerationTask> = {}): VideoGenerationTask {
  return {
    id, status: "running", mode: "text_to_video", prompt: `Video ${id}`,
    model: "gk-video-3.5", conversation_id: "conversation-1", owner_id: "user-1",
    created_at: "2026-09-13T01:00:00Z", updated_at: "2026-09-13T01:00:00Z", ...patch,
  };
}

async function setup(page: Page, tasks: VideoGenerationTask[] = [], plans: VideoAgentPlan[] = []) {
  await page.clock.install();
  const state = {
    tasks, plans, lists: [] as URL[], queries: [] as string[][],
    submissions: [] as Record<string, unknown>[], deletes: [] as string[], reconciles: [] as string[],
    failSubmission: false, failQuery: false, failNextPage: false, includeTotal: false,
    holdQuery: null as Promise<void> | null,
    handleList: null as ((route: Route, url: URL) => Promise<void>) | null,
  };
  const session = { role: "user", subject_id: "user-1", username: "tester", name: "测试用户", token: "mock-only" };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (url.hostname === "assets.example") return route.fulfill({ status: 204 });
    if (path === "/auth/login" || path === "/api/auth/me") return route.fulfill({ json: { ok: true, ...session } });
    if (path === "/auth/captcha") return route.fulfill({ json: { ok: true, captcha_id: "mock", image_data_url: "" } });
    if (path === "/api/settings") return route.fulfill({ json: { config: {
      openai_relay: { enabled: true, has_api_key: true, api_key_count: 1 },
      image_task_queue: { enabled: true, owner_concurrency: 3, owner_pending_limit: 30 },
      image_reference_upload: { enabled: false }, image_storage: { enabled: false },
      video_generation: { enabled: true, base_url: "https://mock.example", has_api_key: true, api_key_count: 1 },
    } } });
    if (path === "/v1/models") return route.fulfill({ json: { data: [{ id: "gpt-image-2" }] } });
    if (path === "/api/video-generation/tasks" && request.method() === "POST") {
      const body = request.postDataJSON();
      state.submissions.push(body);
      if (state.failSubmission) return route.abort("timedout");
      const created = task(body.client_task_id, { prompt: body.prompt, conversation_id: body.conversation_id });
      state.tasks.unshift(created);
      return route.fulfill({ json: created });
    }
    if (path === "/api/video-generation/tasks" && request.method() === "GET") {
      state.lists.push(url);
      if (state.handleList) return state.handleList(route, url);
      const cursor = url.searchParams.get("cursor") || "";
      if (cursor && state.failNextPage) return route.fulfill({ status: 503, json: { detail: "页面暂不可用" } });
      const limit = Number(url.searchParams.get("limit") || 50);
      const q = url.searchParams.get("q")?.toLowerCase();
      const filtered = state.tasks.filter((item) => (
        (!url.searchParams.get("status") || item.status === url.searchParams.get("status"))
        && (!url.searchParams.get("conversation_id") || item.conversation_id === url.searchParams.get("conversation_id"))
        && (!q || `${item.prompt} ${item.model}`.toLowerCase().includes(q))
      ));
      const offset = cursor ? filtered.findIndex((item) => `cursor:${item.id}` === cursor) + 1 : 0;
      const items = filtered.slice(offset, offset + limit);
      const hasMore = offset + items.length < filtered.length;
      return route.fulfill({ json: { items, missing_ids: [], has_more: hasMore,
        next_cursor: hasMore ? `cursor:${items.at(-1)!.id}` : null,
        ...(state.includeTotal ? { total: filtered.length } : {}),
      } });
    }
    if (path === "/api/video-generation/tasks/query") {
      const ids: string[] = request.postDataJSON().ids;
      state.queries.push(ids);
      if (state.holdQuery) await state.holdQuery;
      if (state.failQuery) return route.fulfill({ status: 503, json: { detail: "视频服务暂不可用" } });
      const items = state.tasks.filter((item) => ids.includes(item.id));
      return route.fulfill({ json: { items, missing_ids: ids.filter((id) => !items.some((item) => item.id === id)) } });
    }
    if (path.endsWith("/cancel") && path.startsWith("/api/video-generation/tasks/")) {
      const item = state.tasks.find((item) => path.includes(`/${item.id}/`))!;
      Object.assign(item, { status: "canceled", cancellation_pending: true });
      return route.fulfill({ json: item });
    }
    if (path.endsWith("/reconcile") && path.startsWith("/api/video-generation/tasks/")) {
      const item = state.tasks.find((candidate) => path.includes(`/${candidate.id}/`))!;
      state.reconciles.push(item.id);
      Object.assign(item, { status: "queued", reconciliation_required: false });
      return route.fulfill({ json: item });
    }
    if (request.method() === "DELETE" && path.startsWith("/api/video-")) {
      state.deletes.push(path);
      state.tasks = [];
      state.plans = [];
      return route.fulfill({ json: { ok: true, deleted: 1 } });
    }
    if (path === "/api/video-agent/plans") {
      return route.fulfill({ json: { items: state.plans, has_more: false, total: state.plans.length } });
    }
    // No API request in this suite may reach a real backend or model provider.
    if (path.startsWith("/api/") || path.startsWith("/auth/") || path.startsWith("/v1/")) {
      return route.fulfill({ json: { items: [], total: 0, has_more: false, missing_ids: [] } });
    }
    return route.continue();
  });
  await page.goto("/login");
  const username = page.getByTestId("login-username");
  await username.click();
  await username.fill("tester");
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
  return state;
}

async function generation(page: Page) {
  await page.goto("/video-generation?conversation=conversation-1");
  await expect(page.locator("[data-video-task-card]").first()).toBeVisible();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1_000));
}

test("video polling queries only unfinished IDs, never overlaps and stops on completion", async ({ page }) => {
  const state = await setup(page, [task("running"), task("done", { status: "success" })]);
  await generation(page);
  let release!: () => void;
  state.holdQuery = new Promise<void>((resolve) => { release = resolve; });
  await page.clock.runFor(5_100);
  await expect.poll(() => state.queries.length).toBe(1);
  expect(state.queries[0]).toEqual(["running"]);
  await page.clock.runFor(20_000);
  expect(state.queries).toHaveLength(1);
  state.tasks[0]!.status = "success";
  release();
  await expect(page.locator("[data-video-task-card]").filter({ hasText: "Video running" }).getByText("已完成", { exact: true }).first()).toBeVisible();
  await page.clock.runFor(60_000);
  expect(state.queries).toHaveLength(1);
  expect(state.lists).toHaveLength(1);
});

test("video polling slows in hidden tabs and backs off after errors", async ({ page }) => {
  const state = await setup(page, [task("running")]);
  await generation(page);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(59_000);
  expect(state.queries).toHaveLength(0);
  await page.clock.runFor(1_100);
  await expect.poll(() => state.queries.length).toBe(1);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  state.failQuery = true;
  await page.clock.runFor(5_100);
  await expect(page.getByRole("status").filter({ hasText: "视频状态同步暂时失败" })).toBeVisible();
  expect(state.queries).toHaveLength(2);
  await page.clock.runFor(9_000);
  expect(state.queries).toHaveLength(2);
  await page.clock.runFor(1_100);
  await expect.poll(() => state.queries.length).toBe(3);
  await expect(page.getByRole("status").filter({ hasText: "视频状态同步暂时失败" })).toBeVisible();
  await page.clock.runFor(19_000);
  expect(state.queries).toHaveLength(3);
  state.failQuery = false;
  state.tasks[0]!.status = "success";
  await page.clock.runFor(1_100);
  await expect.poll(() => state.queries.length).toBe(4);
  await expect(page.getByRole("status").filter({ hasText: "视频状态同步暂时失败" })).toHaveCount(0);
});

test("cancel pending remains polled until reconciliation completes", async ({ page }) => {
  const state = await setup(page, [task("cancel-me")]);
  await generation(page);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByText("已请求本地取消，上游可能仍在生成并计费，正在核对结果。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "再生成", exact: true })).toBeDisabled();
  await page.clock.runFor(5_100);
  await expect.poll(() => state.queries.length).toBe(1);
  expect(state.queries[0]).toEqual(["cancel-me"]);
  state.tasks[0]!.cancellation_pending = false;
  state.tasks[0]!.cost = 0.25;
  await page.clock.runFor(5_100);
  await expect(page.getByRole("button", { name: "再生成", exact: true })).toBeEnabled();
  const count = state.queries.length;
  await page.clock.runFor(20_000);
  expect(state.queries).toHaveLength(count);
});

test("uncertain submission shows the server error without creating another task", async ({ page }) => {
  const state = await setup(page, [task("uncertain", {
    status: "error", reconciliation_required: true, error: "上游提交结果未知，正在核对，请勿重复提交",
  })]);
  await generation(page);
  await expect(page.getByText("上游提交结果未知，正在核对，请勿重复提交", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "再生成", exact: true })).toBeDisabled();
  await page.clock.runFor(10_100);
  expect(state.submissions).toHaveLength(0);
  expect(state.queries).toHaveLength(0);
});

test("known upstream reconciliation queries the original task without a new submission", async ({ page }) => {
  const state = await setup(page, [task("known-upstream", {
    status: "error", reconciliation_required: true, upstream_task_id: "upstream-original",
    error: "Status query timed out",
  })]);
  await generation(page);
  await page.getByRole("button", { name: "核对结果", exact: true }).click();
  await expect.poll(() => state.reconciles).toEqual(["known-upstream"]);
  expect(state.submissions).toHaveLength(0);
});

test("video submission retry after network timeout preserves client_task_id", async ({ page }) => {
  const state = await setup(page);
  state.failSubmission = true;
  await page.goto("/video-generation");
  await page.locator("textarea").fill("A slow camera move around the product");
  await page.locator(".video-send-button").click();
  await expect.poll(() => state.submissions.length).toBe(1);
  await expect(page.locator(".video-send-button")).toBeEnabled();
  state.failSubmission = false;
  await page.locator(".video-send-button").click();
  await expect.poll(() => state.submissions.length).toBe(2);
  expect(state.submissions[1]!.client_task_id).toBe(state.submissions[0]!.client_task_id);
  await expect(page.locator("[data-video-task-card]")).toHaveCount(1);
});

test("video history uses opaque cursor pages of nine, optional total and server search", async ({ page }) => {
  const state = await setup(page, Array.from({ length: 20 }, (_, i) => task(String(i), {
    status: "success", video_url: `https://assets.example/${i}.mp4`,
  })));
  await page.goto("/image-library?type=video");
  await expect(page.getByTestId("library-video-card")).toHaveCount(9);
  expect(state.lists[0]!.searchParams.get("limit")).toBe("9");
  expect(state.lists[0]!.searchParams.get("status")).toBe("success");
  expect(state.lists[0]!.searchParams.has("cursor")).toBe(false);
  state.tasks.unshift(task("inserted", { status: "success", video_url: "https://assets.example/new.mp4" }));
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("第 2 页，显示 10-18 条视频，每页 9 条", { exact: true })).toBeVisible();
  expect(state.lists.at(-1)!.searchParams.get("cursor")).toBe("cursor:8");
  await expect(page.getByTestId("library-video-card").first()).toContainText("Video 9");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByTestId("library-video-card")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.getByRole("button", { name: "上一页" }).click();
  await expect(page.getByTestId("library-video-card")).toHaveCount(9);
  await page.getByTestId("library-search-input").fill("Video 19");
  await expect(page.getByTestId("library-video-card")).toHaveCount(1);
  expect(state.lists.at(-1)!.searchParams.get("q")).toBe("Video 19");
  expect(state.lists.at(-1)!.searchParams.has("cursor")).toBe(false);
});

test("failed cursor page preserves current results and retries the failed page", async ({ page }) => {
  const state = await setup(page, Array.from({ length: 11 }, (_, i) => task(String(i), {
    status: "success", video_url: `https://assets.example/${i}.mp4`,
  })));
  state.includeTotal = true;
  await page.goto("/image-library?type=video");
  await expect(page.getByTestId("library-video-card")).toHaveCount(9);
  state.failNextPage = true;
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("alert")).toContainText("页面暂不可用");
  await expect(page.getByTestId("library-video-card")).toHaveCount(9);
  state.failNextPage = false;
  await page.getByRole("button", { name: "重试加载视频" }).click();
  await expect(page.getByTestId("library-video-card")).toHaveCount(2);
  await expect(page.getByText("第 2 / 2 页，显示 10-11 / 11 条视频，每页 9 条", { exact: true })).toBeVisible();
});

test("pending cancellation blocks history deletion until reconciled", async ({ page }) => {
  const state = await setup(page, [task("pending", { status: "canceled", cancellation_pending: true })]);
  await page.goto("/image?history=1&type=video");
  const remove = page.getByTestId("video-history-delete");
  await expect(remove).toBeDisabled();
  await expect(page.getByText("取消后仍在核对上游结果和费用，暂不能删除", { exact: true })).toBeVisible();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1_000));
  state.tasks[0]!.cancellation_pending = false;
  await page.clock.runFor(5_100);
  await expect(remove).toBeEnabled();
  await remove.click();
  await expect(page.getByRole("heading", { name: "永久删除视频记录" })).toBeVisible();
  expect(state.deletes).toHaveLength(0);
  await page.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect.poll(() => state.deletes.length).toBe(1);
});

test("agent history retains conversations and fetches linked generation IDs beyond the first page", async ({ page }) => {
  const plans: VideoAgentPlan[] = [{ id: "plan-1", status: "completed", prompt: "Agent conversation", message: "Ready",
    conversation_id: "agent-1", created_at: "2026-09-13T00:00:00Z",
    attachments: [{ kind: "generation", task_id: "linked" }],
  }];
  const state = await setup(page, [
    ...Array.from({ length: 50 }, (_, i) => task(String(i), { status: "success" })),
    task("linked", { status: "canceled", cancellation_pending: true }),
  ], plans);
  await page.goto("/image?history=1&type=video");
  await expect(page.getByRole("button", { name: "打开视频智能体会话 Agent conversation" })).toBeVisible();
  expect(state.queries.some((ids) => ids.includes("linked"))).toBe(true);
  await page.getByRole("button", { name: "打开视频智能体会话 Agent conversation" }).click();
  await expect(page).toHaveURL(/\/video-generation\/agent\?conversation=agent-1$/);
});

test("signed video URL refresh uses ID query and media errors cannot loop", async ({ page }) => {
  const state = await setup(page, [task("asset", { status: "success", video_url: "https://assets.example/expired.mp4" })]);
  await generation(page);
  await expect.poll(() => state.queries.length).toBeGreaterThanOrEqual(1);
  const before = state.queries.length;
  await page.locator("video").dispatchEvent("error");
  await page.locator("video").dispatchEvent("error");
  await page.clock.runFor(60_000);
  expect(state.queries).toHaveLength(before);
  state.tasks[0]!.video_url = "https://assets.example/refreshed.mp4";
  await page.getByRole("button", { name: "刷新视频链接" }).click();
  await expect(page.locator("video")).toHaveAttribute("src", "https://assets.example/refreshed.mp4");
  expect(state.queries.at(-1)).toEqual(["asset"]);
});
