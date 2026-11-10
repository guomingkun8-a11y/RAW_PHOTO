import { createServer, type Server, type ServerResponse } from "node:http";
import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { DshAgentActivity, DshAgentConversation, DshAgentMessage, DshContextSnapshot } from "../src/features/dsh-agent/api";

const dshModels = [
  { id: "gem-3.5-flash", label: "GEM 3.5 Flash", protocol: "gemini" },
  { id: "gem-3.8-flash", label: "GEM 3.8 Flash", protocol: "gemini" },
  { id: "tt-6-sol", label: "GPT 6 Sol", protocol: "openai" },
  { id: "tt-6.1-sol", label: "GPT 6.1 Sol", protocol: "openai" },
  { id: "deepseek-v4.1-flash", label: "DS-V4.1-Flash", protocol: "openai" },
  { id: "deepseek-v4-flash", label: "DS-V4-Flash", protocol: "openai" },
  { id: "tt-5.6-sol", label: "GPT 5.6 Sol", protocol: "openai" },
].map(model => ({ ...model, context_window: model.id === "tt-6.1-sol" ? 65536 : 128000,
  max_output_tokens: 8192, compact_at_tokens: model.id === "tt-6.1-sol" ? 49152 : 102400, limit_source: "deployment" }));

const markdownReply = [
  "我可以帮你处理以下任务：",
  "",
  "### 1. 代码与技术支持",
  "- **代码编写与修改**：支持 `Python`、JavaScript。",
  "- **项目分析**：检查结构和潜在问题。",
  "",
  "> 先明确需求，再执行任务。",
  "",
  "1. 上传商品图片",
  "2. 确认生成方案",
  "",
  "---",
  "",
  "```python",
  'print("<script>window.__dshMarkdownInjected = 1</script>")',
  `# ${"long_code_".repeat(40)}`,
  "```",
  "",
  "| 模型 | 接口 | 用途 | 备注 |",
  "| --- | --- | --- | --- |",
  "| GEM | Gemini | 对话 | 按会话保存 |",
  "",
  "[查看资料](https://example.com/reference)",
  "",
  '[危险链接](javascript:alert(1)) <img src="invalid" onerror="window.__dshMarkdownInjected = 1">',
].join("\n");

test("员工可以使用 DSH 聊天，管理员可以查看用户对话和 Token 用量", async ({ page }) => {
  const conversation = {
    conversation_id: "conversation-1", title: "如何优化商品图", status: "idle", turn_count: 1,
    created_at: "2026-11-06 10:00:00", updated_at: "2026-11-06 10:00:00",
  };
  const messages: any[] = [];
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (path === "/auth/login" || path === "/api/auth/me") {
      return route.fulfill({ json: { ok: true, role: "admin", subject_id: "admin-1", username: "admin", name: "管理员", token: "dsh-e2e-token" } });
    }
    if (path === "/api/navigation") return route.fulfill({ json: { items: [] } });
    if (path === "/api/dsh-agent/status") {
      return route.fulfill({ json: { enabled: true, configured: true, available: true, queue_available: true, model: "tt-5.6-sol", tools_enabled: false, plugins_enabled: false, search_enabled: false, fetch_enabled: false } });
    }
    if (path === "/api/dsh-agent/conversations" && method === "POST") return route.fulfill({ json: conversation });
    if (path === "/api/dsh-agent/conversations" && method === "GET") return route.fulfill({ json: { items: [conversation], total: 1 } });
    if (path === "/api/dsh-agent/conversations/conversation-1" && method === "GET") {
      return route.fulfill({ json: { conversation, items: messages } });
    }
    if (path === "/api/dsh-agent/conversations/conversation-1/messages/stream") {
      const turn = messages.filter((message) => message.role === "assistant").length + 1;
      const userMessage = {
        message_id: `user-${turn}`, conversation_id: conversation.conversation_id, role: "user", status: "completed",
        content: route.request().postDataJSON().content, model: "", input_tokens: null, output_tokens: null,
        total_tokens: null, usage: {}, error_code: "", created_at: "2026-11-06 10:00:01",
      };
      const assistantMessage = {
        message_id: `assistant-${turn}`, conversation_id: conversation.conversation_id, role: "assistant", status: "completed",
        content: "可以先固定光线与背景，再根据商品图逐张生成。", model: "tt-5.6-sol", input_tokens: 18,
        output_tokens: 15, total_tokens: 33, usage: { inputTokens: 18, outputTokens: 15, totalTokens: 33 },
        error_code: "", created_at: "2026-11-06 10:00:02",
      };
      messages.push(userMessage, assistantMessage);
      const body = [
        { type: "start", conversation_id: conversation.conversation_id, model: "tt-5.6-sol" },
        { type: "delta", text: "可以先固定光线与背景，再根据商品图逐张生成。" },
        { type: "complete", message: assistantMessage },
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body });
    }
    if (path === "/api/dsh-agent/admin/usage") {
      return route.fulfill({ json: { summary: { assistant_turns: 1, input_tokens: 18, output_tokens: 15, total_tokens: 33, cost_available: false }, items: [{ ...messages[1], owner_id: "user-1", owner_username: "alice", owner_name: "张三", conversation_title: conversation.title }] } });
    }
    if (path === "/api/dsh-agent/admin/conversations/conversation-1") {
      return route.fulfill({ json: { conversation: { ...conversation, owner_name: "张三", owner_username: "alice" }, items: messages } });
    }
    if (path === "/api/image-conversations/count") return route.fulfill({ json: { count: 0 } });
    if (path === "/api/system/announcements") return route.fulfill({ json: { items: [] } });
    if (path.startsWith("/api/") || ["xhr", "fetch"].includes(route.request().resourceType())) return route.fulfill({ json: {} });
    return route.continue();
  });

  await page.goto("/login");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
  await page.getByRole("link", { name: /AI 智能体/ }).click();
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  await page.getByRole("textbox", { name: "发送消息" }).fill("怎么优化商品主图？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".dsh-messages")).toContainText("可以先固定光线与背景");
  await expect(page.locator(".dsh-message-usage")).toContainText("33 tokens");

  for (const question of ["那第二张图呢？", "再给一个建议"]) {
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    await page.getByRole("textbox", { name: "发送消息" }).fill(question);
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".dsh-messages")).toContainText(question);
  }
  await expect(page.locator(".dsh-message.assistant")).toHaveCount(3);
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();

  await page.getByRole("link", { name: /智能体监控/ }).click();
  await expect(page.getByRole("heading", { name: "智能体监控" })).toBeVisible();
  await expect(page.locator(".dsh-usage-table")).toContainText("张三");
  await expect(page.locator(".dsh-usage-table")).toContainText("33");
  await page.locator(".dsh-usage-row").first().click();
  await expect(page.locator(".dsh-admin-messages")).toContainText("怎么优化商品主图？");
});

test("手机端可打开并关闭 DSH 对话历史", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/login" || path === "/api/auth/me") return route.fulfill({ json: { ok: true, role: "user", subject_id: "user-1", username: "alice", name: "Alice", token: "dsh-e2e-token" } });
    if (path === "/api/navigation") return route.fulfill({ json: { items: [] } });
    if (path === "/api/dsh-agent/status") return route.fulfill({ json: { enabled: true, configured: true, available: true, queue_available: true, model: "tt-5.6-sol", search_enabled: false, fetch_enabled: false } });
    if (path === "/api/dsh-agent/conversations") return route.fulfill({ json: { items: [], total: 0 } });
    if (path === "/api/image-conversations/count") return route.fulfill({ json: { count: 0 } });
    if (path === "/api/system/announcements") return route.fulfill({ json: { items: [] } });
    if (path.startsWith("/api/") || ["xhr", "fetch"].includes(route.request().resourceType())) return route.fulfill({ json: {} });
    return route.continue();
  });
  await page.goto("/login");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("alice");
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: /AI 智能体/ }).click();
  await page.getByRole("button", { name: "打开对话历史" }).click();
  await expect(page.getByText("最近对话")).toBeVisible();
  await page.mouse.click(370, 200);
  await expect(page.getByRole("button", { name: "关闭对话历史" })).toHaveCount(0);
  await expect(page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).resolves.toBe(true);
});

const localServers = new Set<Server>();
test.afterEach(async () => {
  for (const server of localServers) {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
  localServers.clear();
});

function assistant(overrides: Partial<DshAgentMessage> = {}): DshAgentMessage {
  return {
    message_id: "answer-1", conversation_id: "conversation-1", role: "assistant", status: "completed",
    content: "建议固定商品拍摄光线，再比较不同背景的效果。", model: "tt-5.6-sol", input_tokens: 18,
    output_tokens: 15, total_tokens: 33, usage: {}, error_code: "", created_at: "2026-11-06 10:00:02", ...overrides,
  };
}

const webSources = [
  { title: "商品摄影参考", url: "https://example.com/photography?ref=dsh" },
  { title: "补充资料", url: "http://example.org/reference" },
  { title: "重复来源", url: "https://example.com/photography?ref=dsh" },
  ...["javascript:alert(1)", "data:text/html,bad", "/relative", "//example.com", "file:///secret", "https://user:password@example.com"].map((url) => ({ title: "不安全来源", url })),
];
const searchActivity: DshAgentActivity = { id: "search-1", kind: "search", status: "completed", title: "搜索商品摄影资料", detail: "找到相关参考资料", sources: webSources };
const fetchFailure: DshAgentActivity = { id: "fetch-1", kind: "fetch", status: "error", title: "读取参考网页", detail: "目标网站超时，未能读取网页正文。" };
const analysisActivity: DshAgentActivity = { id: "analysis-1", kind: "analysis", status: "completed", title: "不应显示的内部思维标题", detail: "不应显示的原始内部思维" };

async function mockDsh(page: Page, history: Record<string, DshAgentMessage[]> = {}, tools = true) {
  const conversations: DshAgentConversation[] = ["conversation-1", "conversation-2"].map((id, index) => ({
    conversation_id: id, model: "tt-5.6-sol", title: index ? "另一段对话" : "商品摄影建议", status: "idle", turn_count: 1,
    created_at: "2026-11-06 10:00:00", updated_at: "2026-11-06 10:00:00",
  }));
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/__dsh_mock_stream") return route.continue();
    if (path === "/auth/login" || path === "/api/auth/me") return route.fulfill({ json: { ok: true, role: "admin", subject_id: "admin-1", username: "admin", name: "管理员", token: "mock-only" } });
    if (path === "/api/navigation") return route.fulfill({ json: { items: [] } });
    if (path === "/api/dsh-agent/status") return route.fulfill({ json: { enabled: true, configured: true, available: true, queue_available: true, model: "tt-5.6-sol", models: dshModels, tools_enabled: tools, plugins_enabled: false, search_enabled: tools, fetch_enabled: tools } });
    if (path === "/api/dsh-agent/conversations") {
      if (route.request().method() === "POST") {
        const conversation = {
          conversation_id: "conversation-new", title: "新对话", model: route.request().postDataJSON()?.model || "tt-5.6-sol",
          status: "idle", turn_count: 0, created_at: "2026-11-06 10:00:00", updated_at: "2026-11-06 10:00:00",
        };
        conversations.unshift(conversation);
        return route.fulfill({ json: conversation });
      }
      return route.fulfill({ json: { items: conversations, total: conversations.length } });
    }
    const modelConversation = conversations.find((item) => path === `/api/dsh-agent/conversations/${item.conversation_id}/model`);
    if (modelConversation && route.request().method() === "PATCH") {
      const previous = modelConversation.model;
      const model = route.request().postDataJSON().model;
      modelConversation.model = model;
      const notice = previous !== model ? assistant({
        message_id: `switch-${modelConversation.conversation_id}-${Date.now()}`,
        conversation_id: modelConversation.conversation_id, role: "model_switch", content: "",
        model, model_switch: { from_model: previous, to_model: model },
        input_tokens: null, output_tokens: null, total_tokens: null,
      }) : null;
      if (notice) (history[modelConversation.conversation_id] ||= []).push(notice);
      return route.fulfill({ json: { ...modelConversation, model_switch: notice } });
    }
    const conversation = conversations.find((item) => path === `/api/dsh-agent/conversations/${item.conversation_id}`);
    if (conversation) return route.fulfill({ json: { conversation, items: history[conversation.conversation_id] || [] } });
    if (path === "/api/dsh-agent/admin/usage") return route.fulfill({ json: { summary: { assistant_turns: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, cost_available: false }, items: [] } });
    if (path.startsWith("/api/") || path.startsWith("/auth/") || ["xhr", "fetch"].includes(route.request().resourceType())) return route.fulfill({ json: {} });
    return route.continue();
  });
  return conversations;
}

async function enterDsh(page: Page) {
  await page.goto("/login?next=/ai-agent");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/ai-agent$/);
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
}

async function send(page: Page) {
  await page.getByRole("textbox", { name: "发送消息" }).fill("请给我商品摄影建议");
  await page.getByRole("button", { name: "发送", exact: true }).click();
}

async function chooseModel(page: Page, label: string) {
  await page.getByRole("button", { name: "模型选择", exact: true }).click();
  await page.getByRole("option", { name: label, exact: true }).click();
}

const contextSnapshot: DshContextSnapshot = {
  model: "tt-5.6-sol", used_tokens: 100000, context_window: 128000, max_output_tokens: 8192,
  compact_at_tokens: 102400, limit_source: "deployment", estimated: true,
  compacting: false, compacted: false, compaction_failed: false,
};

for (const width of [1440, 390, 320]) {
  test(`上下文与计费分离，切换模型重新显示额度 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 320 ? 568 : 900 });
    await mockDsh(page, { "conversation-1": [assistant({ context: contextSnapshot, total_tokens: 999999 })] });
    await enterDsh(page);
    const indicator = page.getByRole("button", { name: "上下文估算已用 78%", exact: true });
    await indicator.click();
    const details = page.getByRole("region", { name: "上下文详情" });
    await expect(details).toContainText("100,000 tokens");
    await expect(details).toContainText("128,000 tokens");
    await expect(details).not.toContainText("999,999");
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      await page.getByRole("button", { name: /上下文估算已用/ }).hover();
      await expect(details).toBeVisible();
      const bounds = (await details.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: testInfo.outputPath(`dsh-context-${theme}-${width}.png`), fullPage: true });
    }
    await page.getByRole("textbox", { name: "发送消息" }).fill("请".repeat(1000));
    await page.getByRole("button", { name: /上下文估算已用/ }).hover();
    await expect(details).toContainText("101,500 tokens");
    await page.getByRole("button", { name: /上下文估算已用/ }).press("Escape");
    await chooseModel(page, "GPT 6.1 Sol");
    await page.getByRole("button", { name: /上下文估算已用/ }).hover();
    await expect(details).toContainText("65,536 tokens");
    await expect(details).toContainText("已按新模型额度重算");
    await expect(page.locator(".dsh-context-trigger")).toHaveClass(/is-warning/);
    await page.reload();
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    await page.getByRole("button", { name: /上下文估算已用/ }).hover();
    await expect(details).toContainText("100,000 tokens");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("上下文压缩状态随事件更新，摘要不进入聊天内容", async ({ page }) => {
  await mockDsh(page);
  const stream = await localStream(page);
  await enterDsh(page);
  await expect(page.getByRole("button", { name: "上下文用量待测量" })).toBeVisible();
  await send(page);
  await stream.connected;
  stream.write({ type: "context", context: { ...contextSnapshot, compacting: true } });
  await expect(page.getByRole("button", { name: "正在压缩上下文" })).toBeVisible();
  stream.write({ type: "context", context: { ...contextSnapshot, used_tokens: 8000, compacted: true } });
  await expect(page.getByRole("button", { name: "上下文估算已用 6%" })).toBeVisible();
  stream.write({ type: "complete", message: assistant({ context: { ...contextSnapshot, used_tokens: 8000, compacted: true } }) });
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  await page.getByRole("button", { name: "上下文估算已用 6%" }).hover();
  await expect(page.getByRole("region", { name: "上下文详情" })).toContainText("完整聊天记录仍保留");
  await expect(page.locator(".dsh-message.assistant")).toHaveCount(1);
});

for (const { width, hasTouch } of [{ width: 1440, hasTouch: false }, { width: 390, hasTouch: true }]) {
  test.describe(`历史操作布局 ${width}px`, () => {
    test.use({ hasTouch });
    test("时间与删除独立占位，忙碌时不重叠并保留删除确认", async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      const conversations = await mockDsh(page, { "conversation-1": [assistant()] });
      conversations[0]!.title = "你看下这个商品图";
      conversations[0]!.updated_at = new Date().toISOString();
      conversations[1]!.title = "很长的商品图片修改需求".repeat(8);
      conversations[1]!.updated_at = new Date(Date.now() - 35 * 60000).toISOString();
      await enterDsh(page);
      if (width < 760) await page.getByRole("button", { name: "打开对话历史" }).click();
      const sidebar = page.locator(".dsh-sidebar");
      const row = sidebar.locator(".dsh-history-item").first();
      const remove = row.locator(".dsh-history-delete");
      const time = row.locator("time");
      await expect(remove).toHaveCSS("visibility", hasTouch ? "visible" : "hidden");
      for (const item of await sidebar.locator(".dsh-history-item").all()) {
        await item.locator(".dsh-history-select").focus();
        await expect(item.locator("time")).toBeVisible();
        await expect(item.locator(".dsh-history-delete")).toBeVisible();
        const timestamp = (await item.locator("time").boundingBox())!;
        const button = (await item.locator(".dsh-history-delete").boundingBox())!;
        expect(timestamp.x + timestamp.width + 4).toBeLessThanOrEqual(button.x);
        expect(await item.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      }
      await sidebar.screenshot({ path: testInfo.outputPath(`dsh-history-separated-${width}.png`) });
      if (hasTouch) await page.getByRole("button", { name: "收起对话侧边栏" }).click();
      let release!: () => void;
      const waiting = new Promise<void>(resolve => { release = resolve; });
      await page.route("**/conversations/conversation-1/model", async route => {
        await waiting;
        await route.fulfill({ json: { ...conversations[0], model: "gem-3.8-flash" } });
      });
      try {
        await chooseModel(page, "GEM 3.8 Flash");
        if (hasTouch) await page.getByRole("button", { name: "打开对话历史" }).click();
        await expect(remove).toBeDisabled();
        await expect(remove).toHaveCSS("visibility", hasTouch ? "visible" : "hidden");
        await expect(time).toBeVisible();
        await row.locator(".dsh-history-select").focus();
        await expect(remove).toBeVisible();
        await expect(time).toBeVisible();
        const timestamp = (await time.boundingBox())!;
        const button = (await remove.boundingBox())!;
        expect(timestamp.x + timestamp.width + 4).toBeLessThanOrEqual(button.x);
      } finally { release(); }
      await expect(remove).toBeEnabled();
      let confirmation = "";
      page.once("dialog", async dialog => { confirmation = dialog.message(); await dialog.dismiss(); });
      await remove.click();
      expect(confirmation).toContain("确定删除");
      await expect(sidebar.locator(".dsh-history-item")).toHaveCount(2);
    });
  });
}

for (const width of [1440, 390]) {
  test(`新会话按钮在标题下方整行显示 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await mockDsh(page, { "conversation-1": [assistant()] });
    await enterDsh(page);
    if (width < 760) await page.getByRole("button", { name: "打开对话历史" }).click();
    const sidebar = page.locator(".dsh-sidebar");
    const button = sidebar.getByRole("button", { name: "新会话", exact: true });
    await expect(button).toBeVisible();
    await expect(button).toBeEnabled();
    await expect(sidebar.locator(".dsh-sidebar-head").getByRole("button", { name: "收起对话侧边栏", exact: true })).toBeVisible();
    const header = (await sidebar.locator(".dsh-sidebar-head").boundingBox())!;
    const bounds = (await button.boundingBox())!;
    const history = (await sidebar.locator(".dsh-history-list h2").first().boundingBox())!;
    expect(bounds.y).toBeGreaterThanOrEqual(header.y + header.height);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(history.y);
    expect(bounds.width).toBeCloseTo(header.width, 0);
    await sidebar.screenshot({ path: testInfo.outputPath(`dsh-new-conversation-${width}.png`) });
    await button.click();
    await expect(page.getByRole("heading", { name: "今天想完成什么？" })).toBeVisible();
    await expect(page.locator(".dsh-message")).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    if (width < 760) await expect(page.getByRole("button", { name: "关闭对话历史" })).toHaveCount(0);
  });
}

for (const width of [1440, 900, 390, 320]) {
  test(`标题旁可收起和展开对话侧边栏，消息和草稿保持不变 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await mockDsh(page, { "conversation-1": [assistant()] });
    await enterDsh(page);
    const sidebar = page.getByRole("complementary", { name: "对话历史", exact: true });
    const collapse = page.getByRole("button", { name: "收起对话侧边栏", exact: true });
    const expand = page.getByRole("button", { name: "打开对话历史", exact: true });
    const input = page.getByRole("textbox", { name: "发送消息" });
    await input.fill("还没有发送的商品要求");
    const panel = page.locator(".dsh-chat-panel");
    const original = (await panel.boundingBox())!;
    if (width < 760) await expand.click();
    await expect(collapse).toBeVisible();
    const titleBounds = (await sidebar.locator(".dsh-sidebar-head h1").boundingBox())!;
    const buttonBounds = (await collapse.boundingBox())!;
    expect(buttonBounds.x).toBeGreaterThan(titleBounds.x + titleBounds.width);
    await page.screenshot({ path: testInfo.outputPath(`dsh-sidebar-expanded-${width}.png`) });
    await collapse.click();
    await expect(sidebar).not.toBeVisible();
    await expect(expand).toBeFocused();
    await expect(expand).toHaveAttribute("aria-expanded", "false");
    await expect(input).toHaveValue("还没有发送的商品要求");
    await expect(page.locator(".dsh-message.assistant")).toContainText("建议固定商品拍摄光线");
    const collapsed = (await panel.boundingBox())!;
    if (width > 760) expect(collapsed.width - original.width).toBeCloseTo(260, 0);
    else expect(collapsed.width).toBeCloseTo(original.width, 0);
    await page.screenshot({ path: testInfo.outputPath(`dsh-sidebar-collapsed-${width}.png`) });
    await page.getByRole("button", { name: "切换主题" }).click();
    await expand.press("Enter");
    await expect(sidebar).toBeVisible();
    await expect(collapse).toBeFocused();
    await expect(page.getByRole("heading", { name: "AI 智能体", exact: true })).toBeVisible();
    await expect(page.locator(".dsh-history-item.active")).toContainText("商品摄影建议");
    await expect(input).toHaveValue("还没有发送的商品要求");
    await page.screenshot({ path: testInfo.outputPath(`dsh-sidebar-restored-dark-${width}.png`) });
    await collapse.press("Enter");
    await expect(sidebar).not.toBeVisible();
    await expect(expand).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const width of [1440, 390]) {
  test(`侧栏滑动过渡与聊天区同步，文字不挤压并支持反向切换 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "no-preference" });
    await mockDsh(page, { "conversation-1": [assistant()] });
    await enterDsh(page);
    const sidebar = page.locator(".dsh-sidebar");
    const expand = page.getByRole("button", { name: "打开对话历史", exact: true });
    const collapse = page.getByRole("button", { name: "收起对话侧边栏", exact: true });
    const root = page.locator(".dsh-page");
    await expect(root).toHaveCSS("transition-duration", "0.28s");
    // Slow only the visual test so it can capture an exact intermediate frame.
    await root.evaluate(el => (el as HTMLElement).style.setProperty("--dsh-sidebar-duration", "1200ms"));
    if (width < 760) await expand.click();
    await expect(sidebar).not.toHaveClass(/dsh-sidebar-slide-enter/);
    const original = (await sidebar.boundingBox())!;
    const titleWidth = (await sidebar.locator("h1").boundingBox())!.width;
    const panel = (await page.locator(".dsh-chat-panel").boundingBox())!;
    async function sample() {
      return root.evaluate(async el => {
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
        const sidebar = el.querySelector<HTMLElement>(".dsh-sidebar")!;
        const targets = [el, sidebar, ...el.querySelectorAll(".dsh-history-button, .dsh-mobile-scrim")];
        const animations = targets.flatMap(target => target.getAnimations());
        const properties = animations.map(animation => (animation as CSSTransition).transitionProperty);
        for (const animation of animations) { animation.pause(); animation.currentTime = 600; }
        const rect = sidebar.getBoundingClientRect();
        return { x: rect.x, width: rect.width, titleWidth: sidebar.querySelector("h1")!.getBoundingClientRect().width,
          panelX: el.querySelector(".dsh-chat-panel")!.getBoundingClientRect().x, properties };
      });
    }
    async function finish() {
      await root.evaluate(el => {
        for (const target of [el, ...el.querySelectorAll(".dsh-sidebar, .dsh-history-button, .dsh-mobile-scrim")]) {
          for (const animation of target.getAnimations()) animation.finish();
        }
      });
    }
    await collapse.click();
    const closing = await sample();
    expect(closing.properties).toContain("transform");
    expect(closing.x).toBeGreaterThan(original.x - original.width);
    expect(closing.x).toBeLessThan(original.x);
    expect(closing.width).toBeCloseTo(original.width, 0);
    expect(closing.titleWidth).toBeCloseTo(titleWidth, 0);
    await expect(sidebar).toHaveCSS("opacity", "1");
    await expect(sidebar).toHaveAttribute("inert", "");
    if (width > 760) {
      expect(closing.properties).toContain("grid-template-columns");
      expect(closing.panelX).toBeLessThan(panel.x);
      expect(closing.panelX).toBeGreaterThan(panel.x - original.width);
      expect(closing.x + closing.width).toBeCloseTo(closing.panelX, 0);
    } else expect(closing.panelX).toBeCloseTo(panel.x, 0);
    await page.screenshot({ path: testInfo.outputPath(`dsh-sidebar-closing-midway-${width}.png`) });
    await finish();
    await expect(sidebar).not.toBeVisible();
    await expand.click();
    const opening = await sample();
    expect(opening.properties).toContain("transform");
    expect(opening.x).toBeLessThan(original.x);
    expect(opening.x).toBeGreaterThan(original.x - original.width);
    expect(opening.titleWidth).toBeCloseTo(titleWidth, 0);
    await page.screenshot({ path: testInfo.outputPath(`dsh-sidebar-opening-midway-${width}.png`) });
    await finish();
    await expect(sidebar).not.toHaveClass(/dsh-sidebar-slide-enter/);
    await root.evaluate(async el => {
      el.querySelector<HTMLButtonElement>(".dsh-sidebar-collapse")!.click();
      await new Promise(resolve => setTimeout(resolve, 60));
      el.querySelector<HTMLButtonElement>(".dsh-history-button")!.click();
      await new Promise(resolve => setTimeout(resolve, 60));
      el.querySelector<HTMLButtonElement>(".dsh-sidebar-collapse")!.click();
    });
    await expect(sidebar).not.toBeVisible();
    await expect(expand).toBeFocused();
    await expect(page.locator(".dsh-message.assistant")).toContainText("建议固定商品拍摄光线");
    await root.evaluate(el => (el as HTMLElement).style.removeProperty("--dsh-sidebar-duration"));
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect.poll(() => root.evaluate(el => parseFloat(getComputedStyle(el).transitionDuration))).toBeLessThanOrEqual(.001);
    await expand.click();
    await expect(sidebar).toBeVisible();
    await expect(sidebar).not.toHaveClass(/dsh-sidebar-slide-enter/);
  });
}

test("切换桌面和手机视口后侧栏没有残留遮罩或空白列", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockDsh(page);
  await enterDsh(page);
  const sidebar = page.locator(".dsh-sidebar");
  await page.getByRole("button", { name: "收起对话侧边栏", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByRole("button", { name: "打开对话历史", exact: true }).click();
  await expect(sidebar).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭对话历史", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 900, height: 900 });
  await expect(sidebar).not.toBeVisible();
  await expect(page.locator(".dsh-mobile-scrim")).toHaveCount(0);
  const root = (await page.locator(".dsh-page").boundingBox())!;
  const panel = (await page.locator(".dsh-chat-panel").boundingBox())!;
  expect(panel.width).toBeCloseTo(root.width, 0);
  await page.getByRole("button", { name: "打开对话历史", exact: true }).click();
  await expect(sidebar).toBeVisible();
});

for (const width of [1440, 390, 320]) {
  test(`新会话首页居中且发送后输入框回到底部 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 320 ? 568 : 900 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await mockDsh(page);
    const stream = await localStream(page);
    await enterDsh(page);
    const panel = page.locator(".dsh-chat-panel");
    const group = page.locator(".dsh-composer-wrap");
    const input = page.getByRole("textbox", { name: "发送消息" });
    await expect(panel).toHaveClass(/is-welcome/);
    await expect(page.locator(".dsh-welcome :is(img, svg), .dsh-welcome-icon")).toHaveCount(0);
    await expect(page.locator(".dsh-composer")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "模型选择", exact: true }).locator("img")).toBeVisible();
    await expect(page.locator("img.dsh-brand-icon")).toHaveAttribute("src", (await page.getByRole("link", { name: "AI Image Studio", exact: true }).locator("img").getAttribute("src"))!);
    expect(await page.locator("img.dsh-brand-icon").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    const header = (await page.locator(".dsh-chat-header").boundingBox())!;
    const bounds = (await panel.boundingBox())!;
    const centered = (await group.boundingBox())!;
    expect(centered.x + centered.width / 2).toBeCloseTo(bounds.x + bounds.width / 2, 0);
    expect(centered.y + centered.height / 2).toBeCloseTo((header.y + header.height + bounds.y + bounds.height) / 2, 0);
    expect(centered.y + centered.height).toBeLessThan(bounds.y + bounds.height - 30);
    await input.fill("帮我分析商品");
    await input.evaluate(el => { (window as any).__dshHomeTextarea = el; });
    await expect(panel).toHaveClass(/is-welcome/);
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      await page.screenshot({ path: testInfo.outputPath(`dsh-home-${theme}-${width}.png`), fullPage: true });
    }
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await stream.connected;
    stream.write({ type: "start", conversation_id: "conversation-1", model: "tt-5.6-sol" });
    stream.write({ type: "complete", message: assistant() });
    await expect(panel).not.toHaveClass(/is-welcome/);
    await expect(page.locator(".dsh-messages")).toBeVisible();
    await expect(input).toBeEnabled();
    expect(await input.evaluate(el => el === (window as any).__dshHomeTextarea)).toBe(true);
    const bottom = (await group.boundingBox())!;
    expect(bottom.y).toBeGreaterThan(centered.y);
    expect(bottom.y + bottom.height).toBeCloseTo(bounds.y + bounds.height, 0);
    if (width < 760) await page.getByRole("button", { name: "打开对话历史" }).click();
    await page.getByRole("button", { name: "新会话", exact: true }).click();
    await expect(panel).toHaveClass(/is-welcome/);
    await expect(page.getByRole("heading", { name: "今天想完成什么？" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const width of [1440, 390]) {
  test(`DSH 跟随系统明暗主题并在刷新后保留 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await mockDsh(page, { "conversation-1": [
      assistant({ message_id: "question-1", role: "user", content: "帮我分析这款商品的主图。" }),
      assistant({ content: markdownReply, activities: [searchActivity] }),
      assistant({ message_id: "stopped-1", content: "后续可以统一商品的拍摄光线。", status: "stopped" }),
    ] });
    await enterDsh(page);
    const tokens = [
      [".dsh-page", "background-color", "--studio-bg"],
      [".dsh-sidebar", "background-color", "--studio-panel"],
      [".dsh-composer", "background-color", "--studio-panel"],
      [".dsh-composer textarea", "color", "--foreground"],
      [".dsh-message.user .dsh-message-text", "background-color", "--secondary"],
      [".dsh-markdown pre", "background-color", "--studio-bg"],
      [".dsh-markdown th", "background-color", "--studio-bg"],
      [".dsh-stopped-controls > span", "background-color", "--studio-bg"],
      [".dsh-run", "color", "--muted-foreground"],
    ];
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark)/);
      await expect(page.locator("html")).toHaveCSS("filter", "none");
      await expect.poll(() => page.evaluate((checks) => checks.map(([selector, property, token]) => {
        const element = document.querySelector(selector!)!;
        const probe = document.createElement("span");
        probe.style.setProperty(property!, `var(${token})`);
        document.body.append(probe);
        const expected = getComputedStyle(probe).getPropertyValue(property!);
        probe.remove();
        return { selector, actual: getComputedStyle(element).getPropertyValue(property!), expected };
      }).filter(({ actual, expected }) => actual !== expected), tokens)).toEqual([]);
      const model = page.getByRole("button", { name: "模型选择", exact: true });
      await expect(model.locator("img")).toHaveCSS("filter", theme === "dark" ? "invert(1)" : "none");
      await model.click();
      await expect(page.locator(".composer-select__menu:popover-open")).toHaveCSS("background-color", theme === "dark" ? "rgb(23, 26, 33)" : "rgb(255, 255, 255)");
      await page.screenshot({ path: testInfo.outputPath(`dsh-theme-menu-${theme}-${width}.png`), fullPage: true });
      await page.keyboard.press("Escape");
      const more = page.getByRole("button", { name: "更多回复操作", exact: true }).last();
      await more.click();
      await expect(page.getByRole("menu", { name: "更多回复操作" })).toHaveCSS("background-color", theme === "dark" ? "rgb(23, 26, 33)" : "rgb(255, 255, 255)");
      await page.keyboard.press("Escape");
      await page.locator(".dsh-messages").evaluate(el => { el.scrollTop = 0; });
      await page.screenshot({ path: testInfo.outputPath(`dsh-theme-${theme}-${width}.png`), fullPage: true });
      if (width < 760) {
        await page.getByRole("button", { name: "打开对话历史" }).click();
        await page.screenshot({ path: testInfo.outputPath(`dsh-theme-history-${theme}-${width}.png`), fullPage: true });
        await page.getByRole("button", { name: "关闭对话历史" }).click({ position: { x: width - 10, y: 100 } });
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.locator(".dsh-composer")).toHaveCSS("background-color", "rgb(23, 26, 33)");
    await page.getByRole("button", { name: "切换主题" }).click();
    await expect(page.locator(".dsh-composer")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  });
}

for (const width of [1440, 390, 320]) {
  test(`DSH 输入框内置模型菜单与品牌图标 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockDsh(page);
    await enterDsh(page);
    const composer = page.locator(".dsh-composer");
    const selector = composer.getByRole("button", { name: "模型选择", exact: true });
    const input = composer.getByRole("textbox", { name: "发送消息" });
    const sendButton = composer.getByRole("button", { name: "发送", exact: true });
    await expect(sendButton).toBeDisabled();
    await expect(selector.locator("img")).toHaveAttribute("src", /OpenAI|openai\.svg/);
    await selector.click();
    const menu = page.getByRole("listbox", { name: "模型选择", exact: true });
    await expect(menu.getByRole("option")).toHaveCount(dshModels.length);
    await expect(menu.locator("img")).toHaveCount(dshModels.length);
    expect(await menu.locator("img").evaluateAll((images) => images.every((image) => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const menuBox = (await menu.boundingBox())!;
    expect(menuBox.x).toBeGreaterThanOrEqual(0);
    expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: testInfo.outputPath(`dsh-composer-menu-${width}.png`), fullPage: true });
    await menu.press("Escape");
    await expect(selector).toBeFocused();
    for (const [label, logo] of [["GEM 3.8 Flash", "Gemini|gemini-color\\.svg"], ["DS-V4.1-Flash", "DeepSeek|deepseek-color\\.svg"], ["GPT 6.1 Sol", "OpenAI|openai\\.svg"]]) {
      await chooseModel(page, label!);
      await expect(selector).toBeEnabled();
      await expect(selector).toContainText(label!);
      await expect(selector.locator("img")).toHaveAttribute("src", new RegExp(logo!));
    }
    await input.fill("帮我分析这款商品，并给出可以执行的优化建议");
    await input.press("Shift+Enter");
    await expect(input).toHaveValue("帮我分析这款商品，并给出可以执行的优化建议\n");
    await expect(sendButton).toBeEnabled();
    const composerBox = (await composer.boundingBox())!;
    const inputBox = (await input.boundingBox())!;
    const selectorBox = (await selector.boundingBox())!;
    const sendBox = (await sendButton.boundingBox())!;
    expect(selectorBox.y).toBeGreaterThanOrEqual(inputBox.y + inputBox.height);
    expect(selectorBox.x).toBeGreaterThanOrEqual(composerBox.x);
    expect(selectorBox.x + selectorBox.width).toBeLessThanOrEqual(sendBox.x);
    expect(sendBox.x + sendBox.width).toBeLessThanOrEqual(composerBox.x + composerBox.width);
    expect(sendBox.y + sendBox.height).toBeLessThanOrEqual(composerBox.y + composerBox.height);
    await expect(sendButton).toHaveCSS("border-radius", "50%");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await composer.screenshot({ path: testInfo.outputPath(`dsh-composer-${width}.png`) });
  });
}

// A real local HTTP stream exercises chunking, EOF, socket failure and reader cancellation without an upstream call.
async function localStream(page: Page) {
  let response: ServerResponse;
  let onConnect!: () => void;
  let onDisconnect!: () => void;
  const connected = new Promise<void>((resolve) => { onConnect = resolve; });
  const disconnected = new Promise<void>((resolve) => { onDisconnect = resolve; });
  const server = createServer((request, reply) => {
    reply.setHeader("Access-Control-Allow-Origin", "*");
    reply.setHeader("Access-Control-Allow-Headers", "authorization, content-type");
    reply.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    if (request.method === "OPTIONS") { reply.writeHead(204).end(); return; }
    response = reply;
    reply.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
    reply.flushHeaders();
    reply.on("close", onDisconnect);
    onConnect();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  localServers.add(server);
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Mock server did not bind");
  await page.addInitScript((url) => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => originalFetch(String(input).includes("/messages/stream") ? url : input, init);
  }, `http://127.0.0.1:${address.port}/__dsh_mock_stream`);
  return {
    connected, disconnected,
    write: (event: unknown) => response.write(`data: ${JSON.stringify(event)}\r\n\r\n`),
    raw: (chunk: string | Buffer) => response.write(chunk),
    end: () => response.end(),
    destroy: () => response.destroy(),
  };
}

for (const width of [1440, 390, 320]) {
  test(`消息操作支持复制反馈导出及安全重新回答 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", { value: { writeText: async (text: string) => { (window as any).__copiedReply = text; } } });
    });
    const reply = assistant();
    const history = { "conversation-1": [assistant({ message_id: "question-1", role: "user", content: "给我商品摄影建议" }), reply] };
    await mockDsh(page, history);
    const stream = await localStream(page);
    const ratings: unknown[] = [];
    await page.route("**/messages/answer-1/feedback", async (route) => {
      reply.feedback = route.request().postDataJSON().feedback;
      ratings.push(reply.feedback);
      return route.fulfill({ json: reply });
    });
    await enterDsh(page);
    await page.getByRole("button", { name: "复制回复", exact: true }).click();
    await expect(page.getByRole("button", { name: "已复制", exact: true })).toBeVisible();
    expect(await page.evaluate(() => (window as any).__copiedReply)).toBe(reply.content);
    await page.getByRole("button", { name: "有帮助", exact: true }).click();
    await expect(page.getByRole("button", { name: "有帮助", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await expect(page.getByRole("button", { name: "有帮助", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "没帮助", exact: true }).click();
    await expect(page.getByRole("button", { name: "有帮助", exact: true })).toHaveAttribute("aria-pressed", "false");
    await expect(page.getByRole("button", { name: "没帮助", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "没帮助", exact: true }).click();
    await expect(page.getByRole("button", { name: "没帮助", exact: true })).toHaveAttribute("aria-pressed", "false");
    expect(ratings).toEqual(["positive", "negative", null]);
    const more = page.getByRole("button", { name: "更多回复操作", exact: true });
    await more.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "导出 Markdown" })).toBeFocused();
    await page.getByRole("menuitem", { name: "导出 Markdown" }).press("Escape");
    await expect(more).toBeFocused();
    await more.click();
    const menuBounds = (await page.getByRole("menu", { name: "更多回复操作" }).boundingBox())!;
    expect(menuBounds.x).toBeGreaterThanOrEqual(0);
    expect(menuBounds.x + menuBounds.width).toBeLessThanOrEqual(width);
    const download = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: "导出 Markdown" }).click();
    expect((await download).suggestedFilename()).toBe("AI-answer-1.md");
    await page.screenshot({ path: testInfo.outputPath(`dsh-message-actions-${width}.png`), fullPage: true });
    const request = page.waitForRequest(req => req.url().includes("/__dsh_mock_stream") && req.method() === "POST");
    await page.getByRole("button", { name: "重新回答", exact: true }).click();
    const payload = (await request).postDataJSON();
    expect(payload.text_only).toBe(true);
    expect(payload.content).toContain("给我商品摄影建议");
    await stream.connected;
    stream.write({ type: "complete", message: assistant({ message_id: "regenerated", content: "新的文字建议" }) });
    await expect(page.locator(".dsh-message.assistant").last()).toContainText("新的文字建议");
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test(`真实阶段等待指示与停止继续丢弃 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const history: Record<string, DshAgentMessage[]> = {};
    await mockDsh(page, history);
    const stream = await localStream(page);
    const stopped = assistant({ message_id: "stopped-1", status: "stopped", content: "先固定商品的拍摄光线", activities: [{ id: "analysis", kind: "analysis", title: "分析阶段", status: "stopped" }] });
    let stopCalls = 0;
    await page.route("**/conversations/conversation-1/stop", async route => {
      expect(route.request().postDataJSON()).toEqual({ user_message_id: "running-question" });
      stopCalls++;
      stream.write({ type: "complete", message: stopped });
      return route.fulfill({ json: { accepted: true } });
    });
    await enterDsh(page);
    await send(page);
    await stream.connected;
    stream.write({ type: "start", conversation_id: "conversation-1", model: "tt-5.6-sol", user_message_id: "running-question" });
    stream.write({ type: "activity", activity: { id: "analysis", kind: "analysis", title: "正在分析问题", status: "running" } });
    await expect(page.locator(".dsh-thinking-dot")).toBeVisible();
    await expect(page.getByRole("status", { name: "正在等待回复" })).toBeVisible();
    await expect(page.locator(".dsh-typing span").first()).toHaveCSS("animation-name", "none");
    await page.screenshot({ path: testInfo.outputPath(`dsh-waiting-${width}.png`), fullPage: true });
    stream.write({ type: "delta", text: stopped.content });
    await expect(page.locator(".dsh-message .dsh-typing")).toHaveCount(0);
    await page.getByRole("button", { name: "停止回答", exact: true }).click();
    await expect(page.locator(".dsh-stopped-run")).toContainText("已停止");
    await expect(page.locator(".dsh-latest-button")).toHaveCount(0);
    await expect(page.locator(".dsh-message.assistant .dsh-message-text")).toHaveText(stopped.content);
    expect(stopCalls).toBe(1);
    history["conversation-1"] = [assistant({ message_id: "running-question", role: "user", content: "请给我商品摄影建议" }), stopped];
    await page.reload();
    await expect(page.getByRole("button", { name: "继续回答", exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`dsh-stopped-${width}.png`), fullPage: true });
    const resumed = page.waitForResponse(res => res.url().includes("/__dsh_mock_stream"));
    await page.getByRole("button", { name: "继续回答", exact: true }).click();
    const payload = (await resumed).request().postDataJSON();
    expect(payload.text_only).toBe(true);
    expect(payload.content).toContain(stopped.content);
    stream.write({ type: "complete", message: assistant({ message_id: "continued-1", content: "然后统一商品背景。" }) });
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    await expect(page.locator(".dsh-message.assistant").last()).toContainText("然后统一商品背景。");
    await page.route("**/messages/stopped-1/draft", async route => {
      expect(route.request().method()).toBe("DELETE");
      history["conversation-1"] = history["conversation-1"]!.filter(item => item.message_id !== "stopped-1");
      return route.fulfill({ json: { ok: true } });
    });
    await page.getByRole("button", { name: "丢弃", exact: true }).click();
    await expect(page.locator(".dsh-stopped-run")).toHaveCount(0);
    await page.reload();
    await expect(page.locator(".dsh-stopped-run")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

for (const width of [1440, 390]) {
  test(`智能体回复安全渲染 Markdown，流式与历史一致 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const user = {
      ...assistant({ message_id: "markdown-user", content: "### 用户原文\n**不要格式化**" }),
      role: "user" as const,
    };
    const history: Record<string, DshAgentMessage[]> = { "conversation-1": [user] };
    await mockDsh(page, history);
    const stream = await localStream(page);
    await enterDsh(page);
    await expect(page.locator(".dsh-message.user .dsh-message-text")).toHaveText(user.content);
    await expect(page.locator(".dsh-message.user :is(h3, strong strong)")).toHaveCount(0);
    await send(page);
    await stream.connected;
    const splitAt = markdownReply.indexOf("代码编写") + 2;
    stream.write({ type: "delta", text: markdownReply.slice(0, splitAt) });
    const rendered = page.locator(".dsh-message.assistant .dsh-markdown").last();
    await expect(rendered.getByRole("heading", { name: "1. 代码与技术支持" })).toBeVisible();
    stream.write({ type: "delta", text: markdownReply.slice(splitAt) });
    await expect(rendered.locator("li strong").first()).toHaveText("代码编写与修改");
    const reply = assistant({ message_id: "markdown-assistant", content: markdownReply });
    history["conversation-1"] = [user, reply];
    stream.write({ type: "complete", message: reply });
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    await page.reload();
    await expect(rendered.getByRole("heading", { name: "1. 代码与技术支持" })).toBeVisible();
    await expect(rendered.locator("ul > li")).toHaveCount(2);
    await expect(rendered.locator("ul")).toHaveCSS("list-style-type", "disc");
    await expect(rendered.locator("ol > li")).toHaveCount(2);
    await expect(rendered.locator("blockquote")).toHaveText("先明确需求，再执行任务。");
    await expect(rendered.locator("pre code")).toContainText('print("<script>');
    await expect(rendered.locator("table th")).toHaveCount(4);
    await expect(rendered.locator("hr")).toHaveCount(1);
    const source = rendered.getByRole("link", { name: "查看资料" });
    await expect(source).toHaveAttribute("target", "_blank");
    await expect(source).toHaveAttribute("rel", "noopener noreferrer");
    await expect(rendered.locator("script, img, iframe, svg, a[href^='javascript:']")).toHaveCount(0);
    expect(await page.evaluate(() => "__dshMarkdownInjected" in window)).toBe(false);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`dsh-markdown-${width}.png`), fullPage: true });
  });
}

test("管理员对话详情与聊天页共用安全 Markdown 排版", async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await mockDsh(page);
  const reply = { ...assistant({ content: markdownReply }), owner_name: "测试用户", owner_username: "alice" };
  await page.route("**/api/dsh-agent/admin/usage?*", (route) => route.fulfill({ json: {
    summary: { assistant_turns: 1, input_tokens: 18, output_tokens: 15, total_tokens: 33, cost_available: false }, items: [reply],
  } }));
  await page.route("**/api/dsh-agent/admin/conversations/*", (route) => route.fulfill({ json: { items: [reply] } }));
  await enterDsh(page);
  await page.goto("/ai-agent/monitoring");
  await page.locator(".dsh-usage-row").first().click();
  const rendered = page.locator(".dsh-admin-messages .dsh-markdown");
  await expect(rendered.getByRole("heading", { name: "1. 代码与技术支持" })).toBeVisible();
  await expect(rendered.locator("li strong").first()).toHaveText("代码编写与修改");
  await expect(rendered.locator("script, img, a[href^='javascript:']")).toHaveCount(0);
  for (const theme of ["light", "dark"]) {
    if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
    for (const selector of [".dsh-stats article", ".dsh-usage-table", ".dsh-conversation-view"]) {
      await expect(page.locator(selector).first()).toHaveCSS("background-color", theme === "dark" ? "rgb(23, 26, 33)" : "rgb(255, 255, 255)");
    }
    await page.screenshot({ path: testInfo.outputPath(`dsh-monitor-${theme}.png`), fullPage: true });
  }
});

const flowDiagram = "flowchart LR\n  A[上传商品图片] --> B{确认商品信息}\n  B -->|通过| C[生成套图]\n  B -->|修改| A";
const mindmapDiagram = "mindmap\n  root((项目交付延期))\n    人员\n      核心人员变动\n      技能与任务不匹配\n    方法与流程\n      需求变更频繁\n      缺乏明确排期\n    工具与设备\n      测试环境不稳定";
const diagramReply = `这里是商品工作流：\n\n\`\`\`mermaid\n${flowDiagram}\n\`\`\`\n\n原因分析：\n\n\`\`\`\n${mindmapDiagram}\n\`\`\`\n\n普通代码保持原样：\n\n\`\`\`python\nprint("mindmap")\n\`\`\``;

for (const width of [1440, 390, 320]) {
  test(`中文流程图与旧思维导图可预览缩放下载，兼容主题 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
    await mockDsh(page, { "conversation-1": [assistant({ content: diagramReply })] });
    await enterDsh(page);
    const diagrams = page.locator(".dsh-diagram");
    await expect(diagrams).toHaveCount(2);
    const diagram = diagrams.first();
    const image = diagram.locator(".dsh-diagram-graphic img");
    await expect(image).toBeVisible({ timeout: 20000 });
    await expect(diagrams.last().locator("img")).toBeVisible({ timeout: 20000 });
    expect(await image.evaluate(async (img: HTMLImageElement) => {
      await img.decode();
      const canvas = document.createElement("canvas"); canvas.width = 240; canvas.height = 120;
      const context = canvas.getContext("2d")!; context.drawImage(img, 0, 0, 240, 120);
      return [...context.getImageData(0, 0, 240, 120).data].filter((value, index) => index % 4 !== 3 && value < 220).length > 100;
    })).toBe(true);
    await expect(page.locator(".dsh-markdown-fragment pre code")).toHaveText('print("mindmap")');
    await diagram.getByRole("button", { name: "查看图表源码", exact: true }).click();
    await expect(diagram.locator("pre code")).toContainText(flowDiagram);
    await diagram.getByRole("button", { name: "复制图表源码", exact: true }).click();
    await expect(diagram.getByRole("button", { name: "已复制图表源码", exact: true })).toBeVisible();
    expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n")).toContain(flowDiagram);
    await diagram.getByRole("button", { name: "显示图表预览", exact: true }).click();
    const downloading = page.waitForEvent("download");
    await diagram.getByRole("button", { name: "下载图表 SVG", exact: true }).click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe("流程图.svg");
    const svg = await readFile((await download.path())!, "utf8");
    await testInfo.attach("flow-diagram.svg", { body: svg, contentType: "image/svg+xml" });
    expect(svg).toContain("<svg");
    expect(svg).toContain("上传商品图片");
    expect(svg).not.toMatch(/<script|<foreignObject|<image\b|onerror=/i);
    expect(await page.evaluate(source => {
      const container = document.createElement("div");
      container.style.cssText = "position:fixed;visibility:hidden";
      const svg = new DOMParser().parseFromString(source, "image/svg+xml").documentElement as unknown as SVGSVGElement;
      container.append(svg);
      document.body.append(container);
      try {
        const content = svg.getBBox(); const view = svg.viewBox.baseVal;
        return content.x >= view.x && content.y >= view.y && content.x + content.width <= view.x + view.width && content.y + content.height <= view.y + view.height;
      } finally { container.remove(); }
    }, svg)).toBe(true);
    await diagram.getByRole("button", { name: "放大图表", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "图表预览", exact: true });
    await expect(dialog).toBeVisible();
    const before = (await dialog.locator("img").boundingBox())!;
    await dialog.getByRole("button", { name: "放大图表", exact: true }).click();
    await expect(dialog.locator("output")).toHaveText("150%");
    await expect.poll(async () => (await dialog.locator("img").boundingBox())!.width).toBeGreaterThan(before.width * 1.4);
    await dialog.getByRole("button", { name: "重置图表缩放", exact: true }).click();
    await expect(dialog.locator("output")).toHaveText("100%");
    await page.screenshot({ path: testInfo.outputPath(`dsh-diagram-preview-${width}.png`) });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(diagram.getByRole("button", { name: "放大图表", exact: true })).toBeFocused();
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      await expect(image).toBeVisible({ timeout: 10000 });
      await expect(diagrams.last().locator("img")).toBeVisible({ timeout: 10000 });
      await expect(diagram).toHaveCSS("background-color", theme === "dark" ? "rgb(23, 26, 33)" : "rgb(255, 255, 255)");
      await diagram.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`dsh-diagram-${theme}-${width}.png`) });
      await diagrams.last().scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`dsh-mindmap-${theme}-${width}.png`) });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await diagrams.evaluateAll(elements => elements.every(el => el.scrollWidth <= el.clientWidth + 1))).toBe(true);
    }
    await page.reload();
    await expect(page.locator(".dsh-diagram-graphic img")).toHaveCount(2, { timeout: 20000 });
  });
}

test("图表流式输出结束后才渲染，不把半截代码当成错误", async ({ page }) => {
  await mockDsh(page);
  const stream = await localStream(page);
  await enterDsh(page);
  await send(page);
  await stream.connected;
  stream.write({ type: "delta", text: "```mermaid\nflowchart LR\nA -->" });
  const diagram = page.locator(".dsh-diagram");
  await expect(diagram.getByRole("status")).toHaveText("正在编排图表…");
  await expect(diagram.locator("img")).toHaveCount(0);
  await expect(diagram.locator(".dsh-diagram-source")).toHaveCount(0);
  stream.write({ type: "complete", message: assistant({ content: `\`\`\`mermaid\n${flowDiagram}\n\`\`\`` }) });
  await expect(diagram.locator("img")).toBeVisible({ timeout: 20000 });
  await expect(diagram.locator(".dsh-diagram-source")).toHaveCount(0);
});

test("错误和超长图表保留源码，不影响后续合法图表，拒绝内嵌配置", async ({ page }) => {
  const sources = ["flowchart LR\nA -->", '%%{init: {"securityLevel":"loose"}}%%\nflowchart LR\nA --> B', "mindmap\n" + "x".repeat(20001), flowDiagram];
  await mockDsh(page, { "conversation-1": sources.map((code, index) => assistant({ message_id: `diagram-${index}`, content: `\`\`\`mermaid\n${code}\n\`\`\`` })) });
  await enterDsh(page);
  const diagrams = page.locator(".dsh-diagram");
  await expect(diagrams).toHaveCount(4);
  for (let index = 0; index < 3; index++) {
    await expect(diagrams.nth(index).locator("pre code")).toContainText(sources[index]!, { timeout: 20000 });
    await expect(diagrams.nth(index).getByRole("button", { name: "放大图表", exact: true })).toBeDisabled();
  }
  await expect(diagrams.nth(1).getByRole("status")).toContainText("不支持内嵌配置");
  await expect(diagrams.nth(2).getByRole("status")).toContainText("内容过长");
  await expect(diagrams.last().locator("img")).toBeVisible({ timeout: 20000 });
  await expect(page.locator("body > div[id^='ddsh-diagram-'], body > svg")).toHaveCount(0);
});

test("图表标签和链接不执行脚本，不加载外部图片", async ({ page }) => {
  let externalRequests = 0;
  await mockDsh(page, { "conversation-1": [assistant({ content: '```mermaid\nflowchart LR\nA["<img src=https://diagram-attack.invalid/pixel onerror=window.__dshDiagramInjected=1>"] --> B[完成]\nclick B "javascript:window.__dshDiagramInjected=1"\n```' })] });
  await page.route("https://diagram-attack.invalid/**", route => { externalRequests++; return route.fulfill({ status: 404, body: "" }); });
  await enterDsh(page);
  await expect(page.locator(".dsh-diagram")).toHaveAttribute("aria-busy", "false", { timeout: 20000 });
  await expect(page.locator(".dsh-diagram script, .dsh-diagram iframe, .dsh-diagram-graphic svg, .dsh-diagram a[href^='javascript:']")).toHaveCount(0);
  expect(await page.evaluate(() => "__dshDiagramInjected" in window)).toBe(false);
  expect(externalRequests).toBe(0);
});

test("切换主题和离开会话释放图表资源，不残留预览弹层", async ({ page }) => {
  await page.addInitScript(() => {
    const urls = new Set<string>();
    const create = URL.createObjectURL.bind(URL); const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = value => { const url = create(value); urls.add(url); return url; };
    URL.revokeObjectURL = url => { urls.delete(url); revoke(url); };
    (window as any).__diagramUrls = urls;
  });
  await mockDsh(page, { "conversation-1": [assistant({ content: `\`\`\`mermaid\n${flowDiagram}\n\`\`\`` })] });
  await enterDsh(page);
  const image = page.locator(".dsh-diagram-graphic img");
  await expect(image).toBeVisible({ timeout: 20000 });
  const url = await image.getAttribute("src");
  await page.getByRole("button", { name: "切换主题" }).click();
  await expect(image).not.toHaveAttribute("src", url!);
  await expect(image).toBeVisible({ timeout: 10000 });
  expect(await page.evaluate(() => (window as any).__diagramUrls.size)).toBe(1);
  await page.locator(".dsh-diagram").getByRole("button", { name: "放大图表", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "图表预览", exact: true })).toBeVisible();
  await page.evaluate(() => { history.pushState({}, "", "/ai-agent/monitoring"); window.dispatchEvent(new PopStateEvent("popstate")); });
  await expect(page.getByRole("dialog", { name: "图表预览", exact: true })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).__diagramUrls.size)).toBe(0);
});

test("管理员历史对话详情共用图表渲染", async ({ page }) => {
  await mockDsh(page);
  const reply = { ...assistant({ content: `\`\`\`mermaid\n${mindmapDiagram}\n\`\`\`` }), owner_name: "测试用户", owner_username: "alice" };
  await page.route("**/api/dsh-agent/admin/usage?*", route => route.fulfill({ json: {
    summary: { assistant_turns: 1, input_tokens: 18, output_tokens: 15, total_tokens: 33, cost_available: false }, items: [reply],
  } }));
  await page.route("**/api/dsh-agent/admin/conversations/*", route => route.fulfill({ json: { items: [reply] } }));
  await enterDsh(page);
  await page.goto("/ai-agent/monitoring");
  await page.locator(".dsh-usage-row").first().click();
  await expect(page.locator(".dsh-admin-messages .dsh-diagram-graphic img")).toBeVisible({ timeout: 20000 });
});

test("真实活动按 id 更新，complete 合并进度，来源安全且工具失败明确", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockDsh(page);
  const stream = await localStream(page);
  await enterDsh(page);
  await send(page);
  await stream.connected;
  stream.write({ type: "activity", activity: { id: "queue-1", kind: "queue", status: "running", title: "排队等待运行" } });
  await expect(page.locator(".dsh-run-summary")).toHaveText("排队等待运行");
  stream.write({ type: "activity", activity: { id: "queue-1", kind: "queue", status: "completed", title: "已进入运行队列" } });
  stream.write({ type: "activity", activity: { ...analysisActivity, status: "running" } });
  stream.write({ type: "reasoning", text: "不应显示的原始 reasoning" });
  await expect(page.locator(".dsh-run-summary")).toHaveText("分析阶段");
  stream.write({ type: "activity", activity: analysisActivity });
  stream.write({ type: "activity", activity: { ...searchActivity, status: "running" } });
  await expect(page.locator(".dsh-run-summary")).toHaveText(searchActivity.title);
  const { sources: _sources, ...searchUpdate } = searchActivity;
  stream.write({ type: "activity", activity: searchUpdate });
  stream.write({ type: "activity", activity: fetchFailure });
  stream.write({ type: "timing", timings: { search: 123, total_ms: 1234 } });
  await expect(page.locator(".dsh-run-timing")).toContainText("已运行 2 秒");
  // Split a Chinese UTF-8 character across network chunks.
  const delta = Buffer.from(`data: ${JSON.stringify({ type: "delta", text: "已收到部分回答" })}\n\n`);
  const split = delta.indexOf(Buffer.from("已")) + 1;
  stream.raw(delta.subarray(0, split));
  stream.raw(delta.subarray(split));
  await expect(page.locator(".dsh-message-text").last()).toHaveText("已收到部分回答");
  await expect(page.locator(".dsh-run-tool-error")).toContainText("网页抓取失败");
  const finalMessage = assistant({ activities: [{ id: "answer-1", kind: "answer", status: "completed", title: "回复已生成" }] });
  // Accept a final complete frame without a trailing blank line.
  stream.raw(`data: ${JSON.stringify({ type: "complete", message: finalMessage })}`);
  stream.end();
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  await expect(page.locator(".dsh-message.assistant")).toHaveCount(1);
  await page.locator(".dsh-run-process summary").click();
  await expect(page.locator('[data-activity-id="search-1"]')).toHaveCount(1);
  await expect(page.locator('[data-activity-id="search-1"]')).toContainText("已完成");
  await expect(page.locator('[data-activity-id="answer-1"]')).toBeVisible();
  await page.getByRole("button", { name: "来源 2", exact: true }).click();
  await expect(page.getByRole("region", { name: "搜索来源" })).toBeVisible();
  await expect(page.locator(".dsh-sources-panel a")).toHaveCount(2);
  for (const link of await page.locator(".dsh-sources-panel a").all()) {
    await expect(link).toHaveAttribute("href", /^https?:\/\//);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", /noopener/);
  }
  await expect(page.locator(".dsh-page")).not.toContainText("不应显示");
  await expect(page.locator(".dsh-message-text").last()).toHaveText(finalMessage.content);
  await page.screenshot({ path: testInfo.outputPath("dsh-desktop-live-complete.png"), fullPage: true });
});

test("complete 未携带 activities 时保留流式进度和来源", async ({ page }) => {
  await mockDsh(page);
  await page.route("**/messages/stream", (route) => route.fulfill({ contentType: "text/event-stream", body: [
    { type: "activity", activity: searchActivity },
    { type: "complete", message: assistant() },
  ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") }));
  await enterDsh(page);
  await send(page);
  await expect(page.locator(".dsh-message-usage")).toHaveText("33 tokens");
  await page.locator(".dsh-run-process summary").click();
  await expect(page.locator('[data-activity-id="search-1"]')).toContainText("已完成");
  await expect(page.getByRole("button", { name: "来源 2", exact: true })).toBeVisible();
  await expect(page.locator(".dsh-sources-panel a")).toHaveCount(2);
});

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`历史回复可展开过程与真实来源 ${viewport.width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const message = assistant({ activities: [analysisActivity, searchActivity, fetchFailure] });
    await mockDsh(page, { "conversation-1": [message] });
    await enterDsh(page);
    const process = page.locator(".dsh-run-process");
    await expect(process).not.toHaveAttribute("open", "");
    await expect(page.locator(".dsh-run-tool-error")).toContainText(fetchFailure.detail!);
    await page.getByRole("button", { name: "来源 2", exact: true }).click();
    await expect(page.getByRole("region", { name: "搜索来源" })).toBeVisible();
    await expect(page.locator(".dsh-sources-panel a")).toHaveCount(2);
    const sourcesBounds = (await page.getByRole("region", { name: "搜索来源" }).boundingBox())!;
    expect(sourcesBounds.x).toBeGreaterThanOrEqual(0);
    expect(sourcesBounds.x + sourcesBounds.width).toBeLessThanOrEqual(viewport.width);
    await page.screenshot({ path: testInfo.outputPath(`dsh-sources-${viewport.width}.png`) });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("region", { name: "搜索来源" })).not.toBeVisible();
    await process.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(process).toHaveAttribute("open", "");
    await expect(page.locator('[data-activity-id="analysis-1"]')).toContainText("分析阶段");
    await expect(page.locator(".dsh-page")).not.toContainText("不应显示");
    await expect(page.locator(".dsh-run-spinner")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const composer = await page.locator(".dsh-composer textarea").boundingBox();
    const button = await page.getByRole("button", { name: "发送", exact: true }).boundingBox();
    expect(composer!.y + composer!.height).toBeLessThanOrEqual(button!.y);
    await page.screenshot({ path: testInfo.outputPath(`dsh-history-${viewport.width}.png`), fullPage: true });
    await page.reload();
    await expect(page.locator(".dsh-sources-panel a")).toHaveCount(2);
    await expect(page.locator(".dsh-run-process")).not.toHaveAttribute("open", "");
  });
}

for (const tools of [true, false]) {
  test(`普通回答不显示搜索或原始 reasoning，能力开启=${tools}`, async ({ page }) => {
    await mockDsh(page, {}, tools);
    await page.route("**/messages/stream", (route) => route.fulfill({ contentType: "text/event-stream", body: [
      { type: "reasoning", text: "不要显示内部思维" },
      { type: "timing", timings: { runtime: 12 } },
      { type: "delta", text: assistant().content },
      { type: "complete", message: assistant() },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") }));
    await enterDsh(page);
    await send(page);
    await expect(page.locator(".dsh-message-usage")).toHaveText("33 tokens");
    await expect(page.locator(".dsh-run")).toHaveCount(0);
    await expect(page.locator(".dsh-messages")).not.toContainText(/搜索|抓取|内部思维/);
  });
}

for (const failure of ["eof", "malformed", "error-event", "socket"] as const) {
  test(`SSE ${failure} 保留正文和进度并提示未完成`, async ({ page }) => {
    await mockDsh(page);
    const stream = await localStream(page);
    await enterDsh(page);
    await send(page);
    await stream.connected;
    stream.write({ type: "activity", activity: { ...searchActivity, status: "running" } });
    stream.write({ type: "delta", text: "这些已收到的正文不能丢失" });
    await expect(page.locator(".dsh-message-text").last()).toHaveText("这些已收到的正文不能丢失");
    if (failure === "malformed") stream.raw("data: {invalid json}\n\n");
    if (failure === "error-event") stream.write({ type: "error", message: "模型服务暂时不可用" });
    if (failure === "socket") stream.destroy();
    else stream.end();
    await expect(page.locator(".dsh-message-error")).toContainText("本次回复未完成");
    await expect(page.locator(".dsh-message-error")).toContainText("已保留收到的内容");
    await expect(page.locator(".dsh-message-text").last()).toHaveText("这些已收到的正文不能丢失");
    await page.locator(".dsh-run-process summary").click();
    await expect(page.locator('[data-activity-id="search-1"]')).toContainText("状态未确认");
    await expect(page.locator(".dsh-sources-panel a")).toHaveCount(2);
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  });
}

test("发送中切换对话，活动、正文和 complete 始终属于原会话", async ({ page }) => {
  const history = { "conversation-1": [] as DshAgentMessage[], "conversation-2": [assistant({ conversation_id: "conversation-2", content: "第二段对话的原有正文" })] };
  await mockDsh(page, history);
  const stream = await localStream(page);
  await enterDsh(page);
  await send(page);
  await stream.connected;
  stream.write({ type: "delta", text: "第一段的流式正文" });
  await expect(page.locator(".dsh-message-text").last()).toHaveText("第一段的流式正文");
  await expect(page.locator(".dsh-latest-button .dsh-typing")).toBeVisible();
  await page.getByRole("button", { name: /另一段对话/ }).click();
  await expect(page.locator(".dsh-message-text")).toHaveText("第二段对话的原有正文");
  await expect(page.locator(".dsh-latest-button")).toHaveCount(0);
  stream.write({ type: "activity", activity: searchActivity });
  stream.write({ type: "delta", text: "继续接收" });
  await page.getByRole("button", { name: /商品摄影建议/ }).click();
  await expect(page.locator(".dsh-message-text").last()).toHaveText("第一段的流式正文继续接收");
  await expect(page.locator(".dsh-latest-button .dsh-typing")).toBeVisible();
  await page.getByRole("button", { name: /另一段对话/ }).click();
  history["conversation-1"] = [assistant({ content: "第一段完整回答", activities: [searchActivity] })];
  stream.write({ type: "complete", message: history["conversation-1"][0] });
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  await expect(page.locator(".dsh-message-text")).toHaveText("第二段对话的原有正文");
  await expect(page.locator(".dsh-run")).toHaveCount(0);
  await page.getByRole("button", { name: /商品摄影建议/ }).click();
  await expect(page.locator(".dsh-message-text")).toHaveText("第一段完整回答");
});

test("较慢的历史响应不会覆盖后来选中的对话", async ({ page }) => {
  await mockDsh(page);
  await enterDsh(page);
  let release!: () => void;
  let requested!: () => void;
  const pending = new Promise<void>((resolve) => { requested = resolve; });
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/dsh-agent/conversations/conversation-2", async (route) => {
    requested();
    await gate;
    await route.fulfill({ json: { conversation: {}, items: [assistant({ conversation_id: "conversation-2", content: "不应覆盖当前会话" })] } });
  });
  await page.getByRole("button", { name: /另一段对话/ }).click();
  await pending;
  await page.getByRole("button", { name: /商品摄影建议/ }).click();
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  const response = page.waitForResponse("**/api/dsh-agent/conversations/conversation-2");
  release();
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.locator(".dsh-chat-header strong")).toHaveText("商品摄影建议");
  await expect(page.locator(".dsh-message")).toHaveCount(0);
});

test("离开页面取消流读取，不调用后台任务取消接口", async ({ page }) => {
  const history: Record<string, DshAgentMessage[]> = {};
  const requests: string[] = [];
  page.on("request", (request) => { if (request.method() !== "GET") requests.push(new URL(request.url()).pathname); });
  await mockDsh(page, history);
  const stream = await localStream(page);
  await enterDsh(page);
  await send(page);
  await stream.connected;
  stream.write({ type: "delta", text: "后台仍在生成" });
  await expect(page.locator(".dsh-message-text").last()).toHaveText("后台仍在生成");
  await page.getByRole("link", { name: /查看用户使用与 Token 统计/ }).click();
  await expect(page).toHaveURL(/\/ai-agent\/monitoring$/);
  await stream.disconnected;
  expect(requests.some((path) => /cancel|abort|stop/.test(path))).toBe(false);
  history["conversation-1"] = [assistant({ content: "离开后后台完成的回答" })];
  await page.goto("/ai-agent");
  await expect(page.locator(".dsh-message-text")).toHaveText("离开后后台完成的回答");
});

for (const width of [1440, 390]) {
  test(`模型切换保存并在刷新后恢复，发送期间禁用选择器 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const history = { "conversation-1": [assistant({ message_id: "previous-assistant" })] };
    const conversations = await mockDsh(page, history);
    const stream = await localStream(page);
    await enterDsh(page);
    const selector = page.getByRole("button", { name: "模型选择", exact: true });
    await expect(selector).toContainText("GPT 5.6 Sol");
    await selector.click();
    for (const model of dshModels) await expect(page.getByRole("option", { name: model.label, exact: true })).toBeVisible();
    const saved = page.waitForResponse((response) => response.url().endsWith("/conversation-1/model") && response.request().method() === "PATCH");
    await page.getByRole("option", { name: "GPT 6.1 Sol", exact: true }).click();
    expect((await saved).request().postDataJSON()).toEqual({ model: "tt-6.1-sol" });
    await expect(selector).toBeEnabled();
    await expect(selector).toContainText("GPT 6.1 Sol");
    await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveText("已从 GPT 5.6 Sol 切换至 GPT 6.1 Sol");
    await expect(page.locator(".dsh-messages > :last-child")).toHaveClass(/dsh-model-switch/);
    await expect(page.locator(".dsh-message.assistant .dsh-message-meta strong")).toHaveText("GPT 5.6 Sol");
    await page.reload();
    await expect(selector).toBeEnabled();
    await expect(selector).toContainText("GPT 6.1 Sol");
    await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveCount(1);
    const request = page.waitForRequest((request) => request.url().endsWith("/__dsh_mock_stream") && request.method() === "POST");
    await send(page);
    await stream.connected;
    expect((await request).postDataJSON()).toEqual({ content: "请给我商品摄影建议", model: "tt-6.1-sol", skill_ids: [] });
    await expect(selector).toBeDisabled();
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
    // The server's start event is authoritative, even if it differs from the requested model.
    stream.write({ type: "start", conversation_id: "conversation-1", model: "tt-6-sol" });
    await expect(page.locator(".dsh-message.assistant .dsh-message-meta strong").last()).toHaveText("GPT 6 Sol");
    conversations[0]!.model = "tt-6-sol";
    const message = assistant({ message_id: "model-assistant", model: "tt-6-sol" });
    history["conversation-1"].push(message);
    stream.write({ type: "complete", message });
    await expect(selector).toBeEnabled();
    await expect(selector).toContainText("GPT 6 Sol");
    await expect(page.locator(".dsh-message.assistant .dsh-message-meta strong").first()).toHaveText("GPT 5.6 Sol");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`dsh-models-${width}.png`), fullPage: true });
  });
}

for (const status of [409, 500]) {
  test(`模型保存失败 ${status} 回滚选择并提示，保存期间不可发送`, async ({ page }) => {
    await mockDsh(page);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/dsh-agent/conversations/conversation-1/model", async (route) => {
      await gate;
      await route.fulfill({ status, json: { detail: "模型暂时不能切换" } });
    });
    await enterDsh(page);
    await page.getByRole("textbox", { name: "发送消息" }).fill("保留草稿");
    await chooseModel(page, "DS-V4.1-Flash");
    const selector = page.getByRole("button", { name: "模型选择", exact: true });
    await expect(selector).toContainText("DS-V4.1-Flash");
    await expect(selector).toBeDisabled();
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
    release();
    await expect(selector).toBeEnabled();
    await expect(selector).toContainText("GPT 5.6 Sol");
    await expect(page.locator("[data-sonner-toast]")).toContainText("模型暂时不能切换");
    await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "发送消息" })).toHaveValue("保留草稿");
  });
}

for (const success of [true, false]) {
  test(`较慢的模型保存不会覆盖后来选中的会话，成功=${success}`, async ({ page }) => {
    const history: Record<string, DshAgentMessage[]> = { "conversation-1": [assistant()] };
    const conversations = await mockDsh(page, history);
    conversations[1]!.model = "gem-3.8-flash";
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/dsh-agent/conversations/conversation-1/model", async (route) => {
      await gate;
      if (success) {
        conversations[0]!.model = route.request().postDataJSON().model;
        const notice = assistant({ message_id: "late-switch", role: "model_switch", content: "", model: "tt-6-sol", model_switch: { from_model: "tt-5.6-sol", to_model: "tt-6-sol" } });
        history["conversation-1"]!.push(notice);
        await route.fulfill({ json: { ...conversations[0], model_switch: notice } });
      } else {
        await route.fulfill({ status: 409, json: { detail: "会话正在运行" } });
      }
    });
    await enterDsh(page);
    await chooseModel(page, "GPT 6 Sol");
    await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: /另一段对话/ }).click();
    await expect(page.locator(".dsh-chat-header strong")).toHaveText("另一段对话");
    const selector = page.getByRole("button", { name: "模型选择", exact: true });
    await expect(selector).toContainText("GEM 3.8 Flash");
    release();
    await expect(selector).toBeEnabled();
    await expect(selector).toContainText("GEM 3.8 Flash");
    await expect(page.locator(".dsh-chat-header strong")).toHaveText("另一段对话");
    await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveCount(0);
    await page.getByRole("button", { name: /商品摄影建议/ }).click();
    await expect(selector).toBeEnabled();
    await expect(selector).toContainText(success ? "GPT 6 Sol" : "GPT 5.6 Sol");
    await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveCount(success ? 1 : 0);
  });
}

test("无会话时仅保存本地草稿模型，发送时携带模型创建会话", async ({ page }) => {
  const conversations = await mockDsh(page);
  conversations.length = 0;
  const mutations: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET" && path.startsWith("/api/dsh-agent/")) mutations.push(path);
  });
  await page.route("**/messages/stream", (route) => route.fulfill({ contentType: "text/event-stream", body: [
    { type: "start", conversation_id: "conversation-new", model: "deepseek-v4-flash" },
    { type: "complete", message: assistant({ conversation_id: "conversation-new", model: "deepseek-v4-flash" }) },
  ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") }));
  await enterDsh(page);
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GEM 3.5 Flash");
  await chooseModel(page, "DS-V4-Flash");
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("DS-V4-Flash");
  expect(mutations).toEqual([]);
  await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveCount(0);
  const created = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/dsh-agent/conversations" && request.method() === "POST");
  const sent = page.waitForRequest("**/messages/stream");
  await send(page);
  expect((await created).postDataJSON()).toEqual({ model: "deepseek-v4-flash" });
  expect((await sent).postDataJSON()).toEqual({ content: "请给我商品摄影建议", model: "deepseek-v4-flash", skill_ids: [] });
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await expect(page.locator(".dsh-chat-header strong")).toHaveText("新的对话");
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GEM 3.5 Flash");
  expect(mutations.filter((path) => path === "/api/dsh-agent/conversations")).toHaveLength(1);
});

test("加载历史期间禁用模型选择器，完成后恢复会话模型", async ({ page }) => {
  const conversations = await mockDsh(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/dsh-agent/conversations/conversation-1", async (route) => {
    await gate;
    await route.fulfill({ json: { conversation: conversations[0], items: [] } });
  });
  await page.goto("/login?next=/ai-agent");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").click();
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/ai-agent$/);
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeDisabled();
  release();
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GPT 5.6 Sol");
});

test("连续切换模型按顺序记录，重复选择不新增记录，且不串到其他会话", async ({ page }) => {
  await mockDsh(page, { "conversation-1": [assistant()] });
  await enterDsh(page);
  const notices = page.getByRole("note", { name: "模型切换记录" });
  let patches = 0;
  page.on("request", (request) => { if (request.method() === "PATCH") ++patches; });
  await chooseModel(page, "GEM 3.8 Flash");
  await expect(notices).toHaveCount(1);
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeEnabled();
  await chooseModel(page, "GEM 3.8 Flash");
  expect(patches).toBe(1);
  await expect(notices).toHaveCount(1);
  await chooseModel(page, "DS-V4.1-Flash");
  await expect(notices).toHaveCount(2);
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeEnabled();
  await chooseModel(page, "GPT 6 Sol");
  const expected = [
    "已从 GPT 5.6 Sol 切换至 GEM 3.8 Flash",
    "已从 GEM 3.8 Flash 切换至 DS-V4.1-Flash",
    "已从 DS-V4.1-Flash 切换至 GPT 6 Sol",
  ];
  await expect(notices).toHaveText(expected);
  await page.getByRole("button", { name: /另一段对话/ }).click();
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeEnabled();
  await expect(notices).toHaveCount(0);
  await page.getByRole("button", { name: /商品摄影建议/ }).click();
  await expect(notices).toHaveText(expected);
  await page.reload();
  await expect(notices).toHaveText(expected);
});

test("切换模型与重新加载同一会话交错时不丢失原消息或切换记录", async ({ page }) => {
  const history: Record<string, DshAgentMessage[]> = { "conversation-1": [assistant()] };
  const conversations = await mockDsh(page, history);
  let releaseModel!: () => void;
  let releaseHistory!: () => void;
  const modelGate = new Promise<void>((resolve) => { releaseModel = resolve; });
  const historyGate = new Promise<void>((resolve) => { releaseHistory = resolve; });
  await page.route("**/api/dsh-agent/conversations/conversation-1/model", async (route) => {
    await modelGate;
    const notice = assistant({ message_id: "racing-switch", role: "model_switch", content: "", model: "gem-3.8-flash", model_switch: { from_model: "tt-5.6-sol", to_model: "gem-3.8-flash" } });
    conversations[0]!.model = "gem-3.8-flash";
    history["conversation-1"]!.push(notice);
    await route.fulfill({ json: { ...conversations[0], model_switch: notice } });
  });
  await enterDsh(page);
  await page.route("**/api/dsh-agent/conversations/conversation-1", async (route) => {
    const stale = JSON.parse(JSON.stringify({ conversation: conversations[0], items: history["conversation-1"] }));
    await historyGate;
    await route.fulfill({ json: stale });
  });
  await chooseModel(page, "GEM 3.8 Flash");
  const historyRequest = page.waitForRequest((request) => request.url().endsWith("/conversation-1"));
  await page.getByRole("button", { name: /商品摄影建议/ }).click();
  await historyRequest;
  releaseModel();
  await expect(page.getByRole("status", { name: "正在保存模型" })).toHaveCount(0);
  releaseHistory();
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GEM 3.8 Flash");
  await expect(page.locator(".dsh-message.assistant")).toContainText("建议固定商品拍摄光线");
  await expect(page.getByRole("note", { name: "模型切换记录" })).toHaveCount(1);
});

for (const width of [1440, 390, 320]) {
  test(`长对话用户靠右助手靠左，仅消息区域滚动 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    const history = Array.from({ length: 30 }, (_, index) => assistant({
      message_id: `history-${index}`, role: index % 2 ? "assistant" : "user",
      content: index % 2 ? markdownReply : "请给我商品摄影建议，保留商品外观与文字。".repeat(index % 4 ? 1 : 12),
    }));
    await mockDsh(page, { "conversation-1": history });
    await enterDsh(page);
    const root = page.locator(".dsh-messages");
    const composer = page.locator(".dsh-composer-wrap");
    const header = page.locator(".dsh-chat-header");
    const originalComposer = (await composer.boundingBox())!;
    const originalHeader = (await header.boundingBox())!;
    expect(originalComposer.y + originalComposer.height).toBeLessThanOrEqual(845);
    await expect.poll(() => root.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    const userText = (await page.locator(".dsh-message.user .dsh-message-text").last().boundingBox())!;
    const userAvatar = (await page.locator(".dsh-message.user .dsh-avatar").last().boundingBox())!;
    const aiAvatar = (await page.locator(".dsh-message.assistant .dsh-avatar").last().boundingBox())!;
    const aiText = (await page.locator(".dsh-message.assistant .dsh-message-text").last().boundingBox())!;
    expect(userAvatar.x).toBeGreaterThanOrEqual(userText.x + userText.width);
    expect(aiText.x).toBeGreaterThanOrEqual(aiAvatar.x + aiAvatar.width);
    expect(userText.x).toBeGreaterThan(aiAvatar.x);
    await root.evaluate((el) => { el.scrollTop = 0; });
    await expect.poll(() => root.evaluate((el) => el.scrollTop)).toBe(0);
    await root.hover();
    await page.mouse.wheel(0, -600);
    const topComposer = (await composer.boundingBox())!;
    expect(topComposer.y).toBeCloseTo(originalComposer.y, 0);
    expect((await header.boundingBox())!.y).toBeCloseTo(originalHeader.y, 0);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`dsh-chat-layout-${width}.png`), fullPage: true });
    await page.getByRole("textbox", { name: "发送消息" }).fill("补充内容\n".repeat(30));
    const expanded = (await composer.boundingBox())!;
    expect(expanded.y + expanded.height).toBeCloseTo(originalComposer.y + originalComposer.height, 0);
    expect(expanded.y).toBeGreaterThan(originalHeader.y + originalHeader.height);
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    await root.evaluate((el) => { el.scrollTop = el.scrollHeight; });
    await chooseModel(page, "DS-V4.1-Flash");
    await expect(page.getByRole("note", { name: "模型切换记录" })).toBeInViewport();
    expect((await composer.boundingBox())!.y + (await composer.boundingBox())!.height).toBeCloseTo(expanded.y + expanded.height, 0);
  });
}

for (const width of [1440, 390, 320]) {
test(`运行三点与回到底部切换，上翻阅读不跳动 ${width}px`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width, height: 844 });
  await mockDsh(page, { "conversation-1": [assistant({ content: markdownReply.repeat(8) })] });
  const stream = await localStream(page);
  await enterDsh(page);
  const latest = page.locator(".dsh-latest-button");
  await expect(latest).toHaveCount(0);
  await send(page);
  await stream.connected;
  await expect(latest).toHaveAccessibleName("正在运行，回到最新消息");
  await expect(latest.locator(".dsh-typing span")).toHaveCount(3);
  await expect(latest.locator("span").first()).toHaveCSS("animation-name", /^dsh-typing-wave/);
  await latest.hover();
  await expect.poll(() => latest.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBeGreaterThan(1);
  await page.mouse.down();
  await expect.poll(() => latest.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBeLessThan(1);
  await page.mouse.up();
  await page.mouse.move(0, 0);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(latest.locator("span").first()).toHaveCSS("animation-name", "none");
  await latest.hover();
  expect(await latest.evaluate(el => new DOMMatrix(getComputedStyle(el).transform).a)).toBe(1);
  await page.mouse.move(0, 0);
  for (const theme of ["light", "dark"]) {
    if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
    await expect(latest).toHaveCSS("background-color", theme === "dark" ? "rgba(23, 26, 33, 0.22)" : "rgba(255, 255, 255, 0.18)");
    await expect(latest).toHaveCSS("backdrop-filter", /blur\(10px\)/);
    await expect(latest).toHaveCSS("border-top-color", /rgba\(255, 255, 255, 0\./);
    await expect(latest).toHaveCSS("box-shadow", /inset/);
    expect(await latest.evaluate(el => getComputedStyle(el, "::before").backgroundImage)).toContain("linear-gradient");
    await page.screenshot({ path: testInfo.outputPath(`dsh-run-dots-${theme}-${width}.png`) });
  }
  stream.write({ type: "delta", text: "收到问题，正在整理。" });
  await expect(page.locator(".dsh-message.assistant").last()).toContainText("收到问题");
  const root = page.locator(".dsh-messages");
  const composerY = (await page.locator(".dsh-composer-wrap").boundingBox())!.y;
  await root.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); });
  await expect(latest).toHaveAccessibleName("回到最新消息");
  await expect(latest.locator(".dsh-typing")).toHaveCount(0);
  await expect(latest.locator("svg")).toBeVisible();
  stream.write({ type: "delta", text: markdownReply });
  await expect(page.locator(".dsh-message.assistant").last()).toContainText("代码与技术支持");
  expect(await root.evaluate((el) => el.scrollTop)).toBe(0);
  expect((await page.locator(".dsh-composer-wrap").boundingBox())!.y).toBeCloseTo(composerY, 0);
  const bounds = (await latest.boundingBox())!;
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(composerY);
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
  await page.screenshot({ path: testInfo.outputPath(`dsh-jump-latest-${width}.png`) });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await root.evaluate(el => {
    (window as any).__dshScrollSamples = [];
    el.addEventListener("scroll", () => (window as any).__dshScrollSamples.push(el.scrollTop));
  });
  await latest.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => root.evaluate(el => (window as any).__dshScrollSamples.some((top: number) => top > 0 && top < el.scrollHeight - el.clientHeight - 64))).toBe(true);
  stream.write({ type: "delta", text: "\n这是滚动过程中继续收到的内容。" });
  await expect.poll(() => root.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(2);
  await expect(latest.locator(".dsh-typing")).toBeVisible();
  await root.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); });
  await latest.click();
  await expect.poll(() => root.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await root.evaluate(el => {
    el.dispatchEvent(new WheelEvent("wheel", { deltaY: -300, bubbles: true }));
    el.scrollTop = 0;
    el.dispatchEvent(new Event("scroll"));
  });
  stream.write({ type: "delta", text: "\n手动中断滚动后收到的内容。" });
  await expect(page.locator(".dsh-message.assistant").last()).toContainText("手动中断滚动后");
  expect(await root.evaluate(async el => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return el.scrollTop;
  })).toBe(0);
  await expect(latest).toHaveAccessibleName("回到最新消息");
  await latest.click();
  await expect.poll(() => root.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(2);
  stream.write({ type: "complete", message: assistant({ content: markdownReply }) });
  await expect(page.getByRole("button", { name: "模型选择", exact: true })).toBeEnabled();
  await expect(latest).toHaveCount(0);
  await root.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); });
  await expect(latest).toHaveAccessibleName("回到最新消息");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await latest.click();
  await expect(latest).toHaveCount(0);
});
}

for (const width of [1440, 390]) {
  test(`对话导航按轮次定位并预览，窄屏自动隐藏 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const history = Array.from({ length: 12 }, (_, index) => [
      assistant({ message_id: `q-${index}`, role: "user", content: `第 ${index + 1} 个商品问题` }),
      assistant({ message_id: `a-${index}`, content: markdownReply, activities: index === 0 ? [searchActivity] : [] }),
    ]).flat();
    await mockDsh(page, { "conversation-1": history });
    await enterDsh(page);
    const navigation = page.getByRole("navigation", { name: "对话导航", exact: true, includeHidden: true });
    await expect(navigation.locator("button")).toHaveCount(12);
    await expect(page.locator(".dsh-messages")).toHaveCSS("scrollbar-width", "thin");
    if (width < 700) {
      await expect(navigation).toBeHidden();
      return;
    }
    await expect(navigation).toBeVisible();
    const first = navigation.getByRole("button", { name: "定位对话：第 1 个商品问题", exact: true });
    await first.hover();
    await expect(page.getByRole("tooltip")).toContainText("第 1 个商品问题");
    await expect(page.getByRole("tooltip")).toContainText("我可以帮你处理以下任务");
    await first.click();
    await expect(page.locator('[data-message-id="q-0"]')).toBeInViewport();
    await expect(first).toHaveAttribute("aria-current", "location");
    await page.screenshot({ path: testInfo.outputPath("dsh-conversation-navigation.png") });
    await page.mouse.move(500, 100);
    await first.blur();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    const last = navigation.getByRole("button", { name: "定位对话：第 12 个商品问题", exact: true });
    await last.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator('[data-message-id="q-11"]')).toBeInViewport();
    await expect(last).toHaveAttribute("aria-current", "location");
  });
}
