import { expect, test, type Page } from "@playwright/test";
import type { DshAgentMessage, DshImageMedia, DshSkill } from "../src/features/dsh-agent/api";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aCucAAAAASUVORK5CYII=", "base64");
const imageUrl = "data:image/png;base64," + png.toString("base64");
const imageModels = [
  { id: "gpt-image-2.6-flare", label: "gpt-image-2.6-flare", ratios: ["auto", "1:1", "3:2", "2:3"], qualities: ["auto", "high"], resolutions: [] },
  { id: "gpt-image-2.6-sunburst", label: "gpt-image-2.6-sunburst", ratios: ["auto", "1:1", "3:2", "2:3"], qualities: ["auto", "high"], resolutions: [] },
  { id: "gpt-image-2.5-flare", label: "gpt-image-2.5-flare", ratios: ["auto", "1:1", "16:9"], qualities: ["auto", "high"], resolutions: ["auto", "1K", "2K", "4K"] },
  { id: "gpt-image-2.5-sunburst", label: "gpt-image-2.5-sunburst", ratios: ["auto", "1:1", "16:9"], qualities: ["auto", "high"], resolutions: ["auto", "1K", "2K", "4K"] },
  { id: "gpt-image-2", label: "gpt-image-2", ratios: ["auto", "1:1", "16:9"], qualities: ["auto", "high"], resolutions: ["1K", "2K", "4K"] },
  { id: "banana-2", label: "Nano Banana 2", ratios: ["auto", "1:1", "16:9"], qualities: ["auto"], resolutions: ["1K", "2K", "4K"] },
];

async function setup(page: Page, filesEnabled = false, outputUrl: string | string[] = imageUrl, expertsEnabled = false, teamsEnabled = false, searchEnabled = false, skillsEnabled: boolean | DshSkill[] = false) {
  const outputs = (Array.isArray(outputUrl) ? outputUrl : [outputUrl]).map(url => ({ url }));
  const conversation = { conversation_id: "dsh-images", model: "tt-6-sol", title: "商品图片", status: "idle", turn_count: 0, created_at: "2026-11-07 10:00:00", updated_at: "2026-11-07 10:00:00" };
  const messages: DshAgentMessage[] = [];
  const submissions: any[] = [];
  const queries: string[][] = [];
  const deleted: string[] = [];
  let draftFiles: DshImageMedia[] = [];
  let fileSequence = 0;
  let uploaded = 0;
  let downloadable = false;
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path === "/auth/login" || path === "/api/auth/me") return route.fulfill({ json: { ok: true, role: "admin", subject_id: "alice", username: "alice", name: "Alice", token: "mock-dsh-images" } });
    if (path === "/api/navigation") return route.fulfill({ json: { items: [] } });
    if (path === "/api/dsh-agent/status") return route.fulfill({ json: { enabled: true, configured: true, available: true, files_enabled: filesEnabled, teams_enabled: teamsEnabled, search_enabled: searchEnabled, model: "gem-3.5-flash", models: [{ id: "gem-3.5-flash", label: "GEM 3.5 Flash" }, { id: "tt-6-sol", label: "GPT 6 Sol" }], image_models: imageModels, image_model: imageModels[0]!.id, image_tools_enabled: true, connectors: skillsEnabled ? [{ id: "public-web", label: "联网搜索", description: "公开网页搜索与来源读取", available: searchEnabled }] : undefined, skills: Array.isArray(skillsEnabled) ? skillsEnabled : skillsEnabled ? [{ id: "gpt-image-2-style-library", label: "GPT Image 设计库", description: "商品商业视觉、摄影、海报与构图模板", source: "awesome-gpt-image-2" }] : [], experts: expertsEnabled ? [{ id: "ecommerce-design", label: "电商设计专家", description: "商品主图、白底图、SKU 与营销视觉" }] : [] } });
    if (path === "/api/dsh-agent/conversations") return route.fulfill({ json: method === "POST" ? conversation : { items: [conversation], total: 1 } });
    if (path.endsWith("/images") && method === "POST") {
      uploaded++;
      return route.fulfill({ json: { media_id: `ref-${uploaded}`, conversation_id: conversation.conversation_id, message_id: "", task_id: "", kind: "reference", url: imageUrl, filename: "商品参考图.png", width: 1, height: 1 } });
    }
    if (path.includes("/images/") && method === "DELETE") { deleted.push(path); return route.fulfill({ json: { ok: true } }); }
    if (path.endsWith("/files") && method === "POST") {
      const item: DshImageMedia = { media_id: `file-${++fileSequence}`, conversation_id: conversation.conversation_id, message_id: "", task_id: "", kind: "file", filename: "商品说明.txt", size_bytes: 42, format: "txt", text_chars: 18, truncated: false };
      draftFiles.push(item);
      return route.fulfill({ json: item });
    }
    if (path.includes("/files/") && method === "DELETE") {
      deleted.push(path); draftFiles = draftFiles.filter(file => !path.endsWith(file.media_id));
      return route.fulfill({ json: { ok: true } });
    }
    if (path.endsWith("/images/query")) {
      queries.push(route.request().postDataJSON().ids);
      const media = messages.flatMap(m => m.media || []).filter(m => m.kind === "task");
      for (const m of media) m.task = { id: m.task_id, status: "success", data: outputs };
      downloadable = true;
      return route.fulfill({ json: { items: media } });
    }
    if (path === "/api/image-tasks/download-zip") {
      expect(downloadable).toBe(true);
      return route.fulfill({ contentType: "application/zip", body: Buffer.from("mock-zip") });
    }
    if (path.endsWith("/messages/stream")) {
      const body = route.request().postDataJSON();
      submissions.push(body);
      const id = `user-${submissions.length}`;
      const media: DshImageMedia = { media_id: `task-${submissions.length}`, task_id: `task-${submissions.length}`, conversation_id: conversation.conversation_id, message_id: id, kind: "task", model: body.image_options.model, prompt: body.content, task: { id: `task-${submissions.length}`, status: "queued", image_count: outputs.length, data: [] } };
      const documents = draftFiles.filter(file => body.file_ids?.includes(file.media_id)).map(file => ({ ...file, message_id: id }));
      draftFiles = draftFiles.filter(file => !body.file_ids?.includes(file.media_id));
      const generates = expertsEnabled ? !body.text_only && body.content.includes("生成图片") : !documents.length || body.content.includes("生成");
      const user: DshAgentMessage = { message_id: id, conversation_id: conversation.conversation_id, role: "user", content: body.content, model: "", status: "completed", input_tokens: null, output_tokens: null, total_tokens: null, usage: {}, error_code: "", created_at: "2026-11-07 10:00:00", media: [...body.reference_ids.map((ref: string) => ({ media_id: ref, kind: "reference", conversation_id: conversation.conversation_id, message_id: id, task_id: "", filename: "商品参考图.png", url: imageUrl })), ...documents, ...(generates ? [media] : [])] };
      const assistant = { ...user, message_id: `assistant-${submissions.length}`, role: "assistant" as const, content: generates ? "图片任务已提交。" : expertsEnabled ? "建议保留商品原有标签，使用干净背景。" : "附件中的商品编号是 42。", model: "tt-6-sol", media: [] };
      user.skill_ids = body.skill_ids;
      assistant.skill_ids = body.skill_ids;
      user.expert_id = body.expert_id || "";
      assistant.expert_id = body.expert_id || "";
      messages.push(user, assistant);
      return route.fulfill({ contentType: "text/event-stream", body: [{ type: "start", conversation_id: conversation.conversation_id, model: "tt-6-sol", user_message_id: id }, ...(generates ? [{ type: "media", media }] : []), { type: "complete", message: assistant }].map(event => `data: ${JSON.stringify(event)}\n\n`).join("") });
    }
    if (path === `/api/dsh-agent/conversations/${conversation.conversation_id}`) return route.fulfill({ json: { conversation, items: messages, draft_references: draftFiles } });
    if (path.startsWith("/api/") || ["xhr", "fetch"].includes(route.request().resourceType())) return route.fulfill({ json: {} });
    return route.continue();
  });
  await page.goto("/login");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("alice");
  await page.getByTestId("login-password").click();
  await page.getByTestId("login-password").fill("test-password");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
  await page.goto("/ai-agent");
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  return { submissions, queries, deleted, messages };
}

async function openImageModels(page: Page) {
  await page.getByRole("button", { name: "模型选择", exact: true }).click();
  await page.getByRole("tab", { name: "生图模型", exact: true }).click();
}

for (const width of [1440, 390, 320]) {
  test(`新对话任务入口保留草稿、绑定已开放专家且不自动发送 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 320 ? 568 : 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const state = await setup(page, true, imageUrl, true, true);
    const input = page.getByRole("textbox", { name: "发送消息" });
    const tasks = page.getByRole("navigation", { name: "快捷任务", exact: true });
    await expect(page.getByRole("heading", { name: "今天想完成什么？" })).toBeVisible();
    await expect(tasks.getByRole("button")).toHaveCount(3);
    await expect(page.locator(".dsh-composer .composer-select__label")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
    await expect(input).toHaveAttribute("placeholder", "描述你想完成的任务…");
    const composer = (await page.locator(".dsh-composer").boundingBox())!;
    const panel = (await page.locator(".dsh-chat-panel").boundingBox())!;
    expect(composer.x + composer.width / 2).toBeCloseTo(panel.x + panel.width / 2, 0);
    expect(composer.width).toBeLessThanOrEqual(720);
    await page.screenshot({ path: testInfo.outputPath(`dsh-welcome-light-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "切换主题" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.locator(".dsh-composer")).toHaveCSS("background-color", "rgb(23, 26, 33)");
    await page.screenshot({ path: testInfo.outputPath(`dsh-welcome-dark-${width}.png`), fullPage: true });
    await tasks.getByRole("button", { name: "商品图设计", exact: true }).click();
    await expect(page.locator(".dsh-expert-chip")).toContainText("电商设计专家");
    await expect(input).toBeFocused();
    await expect(input).toHaveValue("");
    await tasks.getByRole("button", { name: "商品营销", exact: true }).click();
    await expect(page.locator(".dsh-expert-chip")).toHaveCount(0);
    await expect(input).toHaveValue("请帮我梳理商品卖点和营销文案：");
    await tasks.getByRole("button", { name: "视频创作", exact: true }).click();
    await expect(input).toHaveValue("请帮我策划一条商品视频：");
    await expect(input).toBeFocused();
    await input.fill("保留我自己填写的需求");
    await tasks.getByRole("button", { name: "商品图设计", exact: true }).click();
    await expect(input).toHaveValue("保留我自己填写的需求");
    expect(state.submissions).toHaveLength(0);
    expect(await tasks.getByRole("button").evaluateAll(elements => elements.every(element => {
      const bounds = element.getBoundingClientRect();
      return bounds.x >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight;
    }))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test.describe("触屏空状态", () => {
  test.use({ hasTouch: true, isMobile: true });
  test("小屏触控按钮保留点击面积且快捷任务不溢出", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 320, height: 568 });
    const state = await setup(page, true, imageUrl, true, true);
    await expect(page.getByRole("button", { name: "添加附件", exact: true })).toHaveCSS("width", "44px");
    await expect(page.getByRole("button", { name: "发送", exact: true })).toHaveCSS("height", "44px");
    await page.getByRole("navigation", { name: "快捷任务" }).getByRole("button", { name: "商品图设计", exact: true }).tap();
    await expect(page.locator(".dsh-expert-chip")).toContainText("电商设计专家");
    expect(await page.locator(".dsh-composer-actions").evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    const tasks = (await page.getByRole("navigation", { name: "快捷任务" }).boundingBox())!;
    expect(tasks.y + tasks.height).toBeLessThanOrEqual(568);
    expect(state.submissions).toHaveLength(0);
    await page.screenshot({ path: testInfo.outputPath("dsh-welcome-touch-320.png") });
  });
});

test("未开放电商专家时快捷任务只填写需求，首次发送输入框平滑移到底部", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const state = await setup(page);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const input = page.getByRole("textbox", { name: "发送消息" });
  await page.getByRole("navigation", { name: "快捷任务" }).getByRole("button", { name: "商品图设计", exact: true }).click();
  await expect(page.locator(".dsh-expert-chip")).toHaveCount(0);
  await expect(input).toHaveValue("请帮我设计商品图片：");
  expect(state.submissions).toHaveLength(0);
  await page.evaluate(() => {
    const composer = document.querySelector(".dsh-composer")!;
    const originalAnimate = composer.animate.bind(composer);
    (composer as HTMLElement).animate = (...args: Parameters<Element["animate"]>) => {
      (window as any).__dshComposerAnimation = args;
      return originalAnimate(...args);
    };
    (window as any).__dshWelcomeInput = composer.querySelector("textarea");
  });
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".dsh-chat-panel")).not.toHaveClass(/is-welcome/);
  await expect(page.getByRole("navigation", { name: "快捷任务" })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => (window as any).__dshComposerAnimation?.[1]?.duration)).toBe(280);
  expect(await input.evaluate(element => element === (window as any).__dshWelcomeInput)).toBe(true);
  await expect.poll(() => page.locator(".dsh-composer").evaluate(element => element.getAnimations().length)).toBe(0);
  const bounds = (await page.locator(".dsh-composer-wrap").boundingBox())!;
  expect(bounds.y + bounds.height).toBeCloseTo(900, 0);
});

for (const width of [1280, 390, 320]) {
  test(`斜杠命令支持筛选、键盘和专家切换，不自动发送 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page, true, imageUrl, true, true);
    const input = page.getByRole("textbox", { name: "发送消息" });
    const menu = page.getByRole("listbox", { name: "斜杠命令" });
    await input.fill("/");
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("option")).toHaveCount(5);
    await expect(input).toBeFocused();
    await expect(input).toHaveAttribute("aria-controls", await menu.getAttribute("id") || "");
    const bounds = (await menu.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(900);
    await expect(menu).toHaveCSS("opacity", "1");
    await page.screenshot({ path: testInfo.outputPath(`dsh-slash-${width}.png`) });
    await page.keyboard.press("ArrowDown");
    await expect(menu.getByRole("option", { name: /普通对话/ })).toHaveAttribute("aria-selected", "true");
    await expect(input).toHaveAttribute("aria-activedescendant", await menu.getByRole("option", { name: /普通对话/ }).getAttribute("id") || "");
    await page.keyboard.press("ArrowUp");
    await input.fill("/电商");
    await expect(menu.getByRole("option")).toHaveCount(1);
    await page.keyboard.press("Enter");
    await expect(menu).toBeHidden();
    await expect(input).toHaveValue("");
    await expect(page.locator(".dsh-expert-chip")).toHaveText("电商设计专家");
    await expect(input).toBeFocused();
    await input.fill("/teams");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "对话模式", exact: true })).toContainText("多智能体协作");
    await input.fill("/teams");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "对话模式", exact: true })).toContainText("多智能体协作");
    await input.fill("/chat");
    await menu.getByRole("option", { name: /普通对话/ }).click();
    await expect(page.locator(".dsh-expert-chip")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "对话模式", exact: true })).toContainText("普通对话");
    await input.fill("/summary");
    await page.keyboard.press("Enter");
    await expect(input).toHaveValue("请总结当前对话的要点和待办事项。");
    await input.fill("帮我 /image 保留瓶型");
    await input.evaluate(element => { element.setSelectionRange(9, 9); element.dispatchEvent(new Event("select")); });
    await expect(menu).toBeVisible();
    await menu.getByRole("option", { name: /生成图片/ }).click();
    await expect(input).toHaveValue("帮我 请生成图片： 保留瓶型");
    expect(state.submissions).toHaveLength(0);
    await page.getByRole("button", { name: "切换主题" }).click();
    await input.fill("/");
    await expect(menu).toBeVisible();
    await expect(menu).toHaveCSS("opacity", "1");
    await page.screenshot({ path: testInfo.outputPath(`dsh-slash-dark-${width}.png`) });
    if (width === 320) {
      await page.setViewportSize({ width, height: 568 });
      await expect.poll(async () => {
        const compact = (await menu.boundingBox())!;
        return compact.y >= 0 && compact.y + compact.height <= 568;
      }).toBe(true);
      await page.keyboard.press("ArrowUp");
      await expect(menu.getByRole("option", { name: /总结对话/ })).toBeInViewport();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("斜杠菜单不误触网址、中文输入法和空结果，支持关闭与能力过滤", async ({ page }) => {
  const state = await setup(page);
  const input = page.getByRole("textbox", { name: "发送消息" });
  const menu = page.getByRole("listbox", { name: "斜杠命令" });
  await input.fill("https://example.com/image");
  await expect(menu).toBeHidden();
  await input.fill("1/2");
  await expect(menu).toBeHidden();
  await input.fill("/");
  await expect(menu.getByRole("option")).toHaveCount(3);
  await expect(menu.getByRole("option", { name: /^\/(design|teams)\b/ })).toHaveCount(0);
  await input.dispatchEvent("compositionstart");
  await expect(menu).toBeHidden();
  await input.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  await input.dispatchEvent("compositionend");
  await expect(menu).toBeVisible();
  await input.dispatchEvent("keydown", { key: "Enter", keyCode: 229 });
  await expect(input).toHaveValue("/");
  await input.fill("/not-installed");
  await expect(menu).toContainText("没有匹配的命令");
  await page.keyboard.press("Enter");
  await expect(input).toHaveValue("/not-installed");
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await input.fill("/");
  await expect(menu).toBeVisible();
  await page.getByRole("button", { name: "模型选择", exact: true }).click();
  await expect(menu).toBeHidden();
  await input.click();
  await input.fill("/im");
  await expect(menu.getByRole("option")).toHaveCount(1);
  await page.keyboard.press("Shift+Enter");
  await expect(menu).toBeHidden();
  expect(state.submissions).toHaveLength(0);
  await page.route("**/api/dsh-agent/status", route => route.fulfill({ json: { enabled: true, configured: true, available: true, model: "gem-3.5-flash", models: [{ id: "gem-3.5-flash", label: "GEM 3.5 Flash" }] } }));
  await page.reload();
  await input.fill("/");
  await expect(menu.getByRole("option")).toHaveCount(2);
  await expect(menu.getByRole("option", { name: /生成图片/ })).toHaveCount(0);
});

test("真实图片加载、加载失败重试和终态不使用生成动画", async ({ page }) => {
  const output = "https://dsh-test.invalid/image.png";
  const state = await setup(page, false, output);
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await page.route(output, async route => {
    if (++requests === 1) {
      await waiting;
      return route.fulfill({ status: 503, contentType: "text/plain", body: "image unavailable" });
    }
    return route.fulfill({ contentType: "image/png", body: png });
  });
  await page.getByRole("textbox", { name: "发送消息" }).fill("生成商品图");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".image-generation")).toContainText("图片已生成，正在加载", { timeout: 10000 });
  await expect(page.locator(".image-placeholder.is-pending")).toHaveCount(0);
  await expect(page.locator(".image-stage")).toHaveAttribute("aria-busy", "true");
  release();
  await expect(page.locator(".image-placeholder")).toContainText("图片加载失败");
  await expect(page.locator(".generation-dots")).toHaveCount(0);
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.locator(".image-open img")).toHaveClass("is-loaded");
  await expect(page.locator(".image-stage")).toHaveAttribute("aria-busy", "false");
  await expect(page.locator(".image-stack-layer")).toHaveCount(0);
  const item = state.messages[0]!.media![0]!;
  for (const [status, label] of [["canceled", "已取消"], ["unconfirmed", "提交状态待确认"], ["success", "未返回图片"]]) {
    item.task = { id: item.task_id, status: status!, data: [] };
    await page.reload();
    await expect(page.locator(".image-placeholder")).toContainText(label!);
    await expect(page.locator(".generation-dots")).toHaveCount(0);
    await expect(page.locator(".image-stage")).toHaveAttribute("aria-busy", "false");
  }
  expect(state.submissions).toHaveLength(1);
});

for (const width of [1280, 390, 320]) {
  test(`电商专家可选择移除，刷新恢复且与模型分离 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const { submissions } = await setup(page, true, imageUrl, true);
    const plus = page.getByRole("button", { name: "添加附件", exact: true });
    const chip = page.locator(".dsh-expert-chip");
    const choose = async () => {
      await plus.click();
      await page.getByRole("tab", { name: "专家", exact: true }).click();
      await expect(page.getByRole("button", { name: /电商设计专家.*商品主图/ })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath(`dsh-expert-menu-${width}.png`) });
      await page.getByRole("button", { name: /电商设计专家.*商品主图/ }).click();
      await expect(chip).toContainText("电商设计专家");
    };
    await choose();
    await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GPT 6 Sol");
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeFocused();
    await plus.click();
    await page.getByRole("tab", { name: "专家", exact: true }).click();
    await expect(page.getByRole("button", { name: /电商设计专家.*商品主图/ })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: /电商设计专家.*商品主图/ }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(page.getByRole("tab", { name: "专家", exact: true })).toBeFocused();
    await page.getByRole("tab", { name: "添加图片和文件" }).click();
    await expect(page.getByRole("button", { name: /^上传图片/ })).toBeVisible();
    await page.keyboard.press("Escape");
    const input = page.getByRole("textbox", { name: "发送消息" });
    await input.fill("只给主图设计建议，暂不出图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(1);
    expect(submissions[0].expert_id).toBe("ecommerce-design");
    await expect(page.locator(".dsh-message.assistant")).toHaveCount(1);
    await page.reload();
    await expect(chip).toBeVisible();
    await expect(page.locator(".dsh-message.assistant .dsh-message-expert")).toHaveText("电商设计专家");
    await openImageModels(page);
    await page.getByRole("option", { name: /GPT Image 2\.5.*Flare/ }).click();
    await expect(chip).toBeVisible();
    await input.fill("帮我生成图片，保留商品标签");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(2);
    expect(submissions[1].expert_id).toBe("ecommerce-design");
    expect(submissions[1].image_options.model).toBe("gpt-image-2.5-flare");
    await expect(page.locator(".dsh-message.assistant")).toHaveCount(2);
    await expect(page.getByRole("button", { name: "移除电商设计专家" })).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath(`dsh-expert-chat-${width}.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "移除电商设计专家" }).click();
    await expect(chip).toHaveCount(0);
    await input.fill("继续给建议");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(3);
    expect(submissions[2].expert_id).toBeUndefined();
    await expect(page.locator(".dsh-message.assistant")).toHaveCount(3);
    await page.reload();
    await expect(input).toBeEnabled();
    await expect(chip).toHaveCount(0);
    await choose();
    if (width < 760) await page.getByRole("button", { name: "打开对话历史" }).click();
    await page.getByRole("button", { name: "新会话", exact: true }).click();
    await expect(chip).toHaveCount(0);
    await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GEM 3.5 Flash");
  });
}

for (const width of [1280, 390]) {
  test(`模型栏各区域可再次点击收起，滚动条统一变细 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page);
    const trigger = page.getByRole("button", { name: "模型选择", exact: true });
    const menu = page.locator(".dsh-composer-model .composer-select__menu");
    const targets = [trigger.locator(".dsh-model-option > span"), trigger.locator("img"), trigger.locator(".composer-select__chevron")];
    for (const target of targets) {
      await trigger.click();
      await expect(menu).toHaveCSS("opacity", "1");
      await target.click();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
      await expect(menu).toBeHidden();
    }
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await trigger.focus();
    await page.keyboard.press("Space");
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await page.getByRole("textbox", { name: "发送消息" }).click();
    await expect(menu).toBeHidden();
    await trigger.click();
    for (const selector of ["html", ".composer-select__options", ".dsh-messages", ".dsh-history-list", ".dsh-composer textarea"]) {
      await expect(page.locator(selector)).toHaveCSS("scrollbar-width", "thin");
    }
    await page.screenshot({ path: testInfo.outputPath(`dsh-thin-scrollbars-${width}.png`) });
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect(await menu.evaluate(el => getComputedStyle(el).transitionDuration.split(",").every(duration => parseFloat(duration) <= 0.001))).toBe(true);
    await trigger.click();
    await expect(menu).toBeHidden();
  });
}

for (const width of [1280, 390, 320]) {
  test(`统一模型入口按对话和生图分类，新对话默认 GEM 3.5 Flash ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await setup(page);
    const selector = page.getByRole("button", { name: "模型选择", exact: true });
    await expect(selector).toHaveCount(1);
    await expect(selector).toContainText("GPT 6 Sol");
    if (width < 760) await page.getByRole("button", { name: "打开对话历史" }).click();
    await page.getByRole("button", { name: "新会话", exact: true }).click();
    await expect(selector).toContainText("GEM 3.5 Flash");
    await selector.click();
    const chat = page.getByRole("tab", { name: "对话模型", exact: true });
    const image = page.getByRole("tab", { name: "生图模型", exact: true });
    await expect(chat).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("option", { name: "GEM 3.5 Flash", exact: true })).toHaveAttribute("aria-selected", "true");
    await chat.focus();
    await page.keyboard.press("ArrowRight");
    await expect(image).toBeFocused();
    await expect(image).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("option", { name: "GPT Image 2.6 · Flare", exact: true })).toBeFocused();
    await page.getByRole("option", { name: "GPT Image 2.6 · Sunburst", exact: true }).click();
    await expect(selector).toContainText("GEM 3.5 Flash");
    await expect(page.locator(".dsh-composer-model")).toHaveAttribute("title", "对话：GEM 3.5 Flash；生图：GPT Image 2.6 · Sunburst");
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      await selector.click();
      await expect(page.getByRole("option", { name: "GPT Image 2.6 · Sunburst", exact: true })).toHaveAttribute("aria-selected", "true");
      const bounds = (await page.locator(".composer-select__menu:popover-open").boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(900);
      await page.screenshot({ path: testInfo.outputPath(`dsh-unified-models-${theme}-${width}.png`) });
      await chat.click();
      await expect(page.getByRole("option", { name: "GEM 3.5 Flash", exact: true })).toHaveAttribute("aria-selected", "true");
      await page.screenshot({ path: testInfo.outputPath(`dsh-unified-chat-${theme}-${width}.png`) });
      await image.click();
      await page.keyboard.press("Escape");
      await expect(selector).toBeFocused();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const width of [1280, 390]) {
  test(`DSH 生图结果和上传控件适配明暗主题 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await setup(page);
    await page.getByLabel("上传参考图片").setInputFiles({ name: "product.png", mimeType: "image/png", buffer: png });
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(1);
    await expect(page.locator(".dsh-chat-panel")).toHaveClass(/is-welcome/);
    await page.screenshot({ path: testInfo.outputPath(`dsh-home-images-${width}.png`), fullPage: true });
    await page.getByRole("textbox", { name: "发送消息" }).fill("保留商品外观，生成一张主图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".image-result")).toContainText("生成完成", { timeout: 10000 });
    await page.getByLabel("上传参考图片").setInputFiles({ name: "reference.png", mimeType: "image/png", buffer: png });
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(1);
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      const panelColor = theme === "dark" ? "rgb(23, 26, 33)" : "rgb(255, 255, 255)";
      await expect(page.locator(".image-result")).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
      await expect(page.locator(".dsh-composer")).toHaveCSS("background-color", panelColor);
      await openImageModels(page);
      await expect(page.locator(".composer-select__menu:popover-open")).toHaveCSS("background-color", panelColor);
      await expect(page.getByRole("option", { name: "Nano Banana 2", exact: true }).locator("img")).toHaveCSS("filter", "none");
      await page.screenshot({ path: testInfo.outputPath(`dsh-image-theme-menu-${theme}-${width}.png`), fullPage: true });
      await page.keyboard.press("Escape");
      await page.screenshot({ path: testInfo.outputPath(`dsh-image-theme-${theme}-${width}.png`), fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  });
}

for (const width of [1280, 390, 320]) {
  test(`DSH 行内模型、上传、异步回显、连续修改与刷新恢复 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    await expect(page.getByRole("button", { name: "模型选择", exact: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "模型选择", exact: true })).toContainText("GPT 6 Sol");
    await expect(page.locator(".dsh-composer-model")).toHaveAttribute("title", "对话：GPT 6 Sol；生图：GPT Image 2.6 · Flare");
    await page.locator(".dsh-composer").screenshot({ path: testInfo.outputPath(`dsh-composer-grouped-${width}.png`) });
    await expect(page.getByRole("button", { name: "生图模型与参数" })).toHaveCount(0);
    await page.getByLabel("上传参考图片").setInputFiles({ name: "reference.png", mimeType: "image/png", buffer: png });
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(1);
    await page.getByRole("textbox", { name: "发送消息" }).fill("用这张商品图生成 3:2 的新图片");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".image-result")).toContainText("生成完成", { timeout: 10000 });
    expect(state.submissions[0].reference_ids).toEqual(["ref-1"]);
    expect(state.submissions[0].image_options).toEqual({ model: "gpt-image-2.6-flare" });
    expect(state.submissions[0].content).toContain("3:2");
    expect(state.queries.every(ids => ids.length > 0)).toBe(true);
    await expect(page.locator(".image-open img")).toHaveJSProperty("naturalWidth", 1);
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(0);
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载生成图片" }).click();
    expect((await download).suggestedFilename()).toBe("AI-images.zip");
    await page.getByRole("button", { name: "继续修改" }).click();
    await expect(page.getByRole("textbox", { name: "发送消息" })).toHaveValue(/task-1:0/);
    const chatModel = await page.getByRole("button", { name: "模型选择", exact: true }).boundingBox();
    expect(chatModel!.width).toBeGreaterThanOrEqual(100);
    const upload = (await page.getByRole("button", { name: "添加附件", exact: true }).boundingBox())!;
    const send = (await page.getByRole("button", { name: "发送", exact: true }).boundingBox())!;
    for (const [first, second] of [[chatModel!, upload], [chatModel!, send], [upload, send]] as const) {
      expect(first.x + first.width <= second.x || second.x + second.width <= first.x
        || first.y + first.height <= second.y || second.y + second.height <= first.y).toBe(true);
    }
    expect(await page.locator(".dsh-composer-model .dsh-model-option > span").evaluateAll(elements => elements.every(el => el.scrollWidth <= el.clientWidth + 1))).toBe(true);
    expect(await page.getByRole("button", { name: "模型选择", exact: true }).locator("img").evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await openImageModels(page);
    await expect(page.getByRole("option", { name: "Nano Banana 2" })).toBeVisible();
    await expect(page.getByRole("option", { name: "GPT Image 2.6 · Sunburst", exact: true })).toBeVisible();
    await expect(page.getByRole("option", { name: "GPT Image 2.5 · Flare", exact: true })).toBeVisible();
    await expect(page.getByRole("option", { name: "GPT Image 2.5 · Sunburst", exact: true })).toBeVisible();
    await expect(page.locator(".dsh-composer-model .composer-select__menu:popover-open")).toHaveCSS("opacity", "1");
    await page.screenshot({ path: testInfo.outputPath(`dsh-image-models-${width}.png`) });
    await page.keyboard.press("Escape");
    await page.screenshot({ path: testInfo.outputPath(`dsh-images-${width}.png`) });
    for (const selector of [".dsh-page", ".dsh-chat-panel", ".dsh-messages", ".image-result", ".dsh-composer"]) {
      const rect = await page.locator(selector).first().boundingBox();
      expect(rect!.x + rect!.width, selector).toBeLessThanOrEqual(width + 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const composer = await page.locator(".dsh-composer").boundingBox();
    expect(composer!.y + composer!.height).toBeLessThanOrEqual(900);
    await page.reload();
    await expect(page.locator(".image-result")).toContainText("生成完成");
    await expect(page.locator(".image-reference img")).toHaveCount(1);
  });
}

for (const width of [1024, 768]) {
  test(`输入框按可用宽度排列且保留完整生图版本 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    const selector = page.getByRole("button", { name: "模型选择", exact: true });
    await openImageModels(page);
    await page.getByRole("option", { name: "GPT Image 2.6 · Sunburst", exact: true }).click();
    await expect(selector).toContainText("GPT 6 Sol");
    await expect(selector.locator("..")).toHaveAttribute("title", "对话：GPT 6 Sol；生图：GPT Image 2.6 · Sunburst");
    await page.locator(".dsh-composer").screenshot({ path: testInfo.outputPath(`dsh-composer-grouped-${width}.png`) });
    const controls = page.locator(".dsh-composer-actions");
    expect(await controls.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    expect(await page.locator(".dsh-composer-model .dsh-model-option > span").evaluateAll(elements => elements.every(el => el.scrollWidth <= el.clientWidth + 1))).toBe(true);
    await page.getByRole("textbox", { name: "发送消息" }).fill("生成商品图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".image-result")).toBeVisible();
    expect(state.submissions[0].image_options).toEqual({ model: "gpt-image-2.6-sunburst" });
  });
}

test("直接切换生图模型，只提交模型不固定参数，移除草稿图片不会发送引用", async ({ page }) => {
  const state = await setup(page);
  await openImageModels(page);
  await page.getByRole("option", { name: "Nano Banana 2" }).click();
  await expect(page.locator(".dsh-composer-model")).toHaveAttribute("title", "对话：GPT 6 Sol；生图：Nano Banana 2");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "图片比例" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "分辨率" })).toHaveCount(0);
  await page.getByLabel("上传参考图片").setInputFiles({ name: "reference.png", mimeType: "image/png", buffer: png });
  await page.getByRole("button", { name: "移除参考图片" }).click();
  await expect(page.locator(".dsh-upload-previews")).toHaveCount(0);
  expect(state.deleted).toHaveLength(1);
  await page.getByRole("textbox", { name: "发送消息" }).fill("生成三张 16:9 的 4K 商品图");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".image-result")).toContainText("生成完成", { timeout: 10000 });
  expect(state.submissions[0].reference_ids).toEqual([]);
  expect(state.submissions[0].image_options).toEqual({ model: "banana-2" });
  expect(state.submissions[0].content).toBe("生成三张 16:9 的 4K 商品图");
});

test("粘贴多张图片后可发送引用，普通文本粘贴不被拦截", async ({ page }) => {
  const state = await setup(page);
  const input = page.getByRole("textbox", { name: "发送消息" });
  await input.fill("请编辑这两张图");
  const textPrevented = await input.evaluate(element => {
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", "普通文字");
    const event = new ClipboardEvent("paste", { clipboardData, bubbles: true, cancelable: true });
    element.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(textPrevented).toBe(false);
  await input.evaluate((element, bytes) => {
    const clipboardData = new DataTransfer();
    for (let i = 0; i < 2; i++) clipboardData.items.add(new File([new Uint8Array(bytes)], `clipboard-${i}.png`, { type: "image/png" }));
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData, bubbles: true, cancelable: true }));
  }, [...png]);
  await expect(page.locator(".dsh-upload-previews img")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
  await input.press("Enter");
  await expect(page.locator(".image-result")).toBeVisible();
  expect(state.submissions[0].reference_ids).toEqual(["ref-1", "ref-2"]);
});

test("拖拽上传保持高亮稳定，校验格式和数量，达到上限后不能继续粘贴", async ({ page }) => {
  await setup(page);
  const composer = page.locator(".dsh-composer");
  const dataTransfer = await page.evaluateHandle(bytes => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(bytes)], "drop.png", { type: "image/png" }));
    return transfer;
  }, [...png]);
  try {
    await composer.dispatchEvent("dragenter", { dataTransfer });
    await expect(composer).toHaveClass(/is-dragging/);
    await composer.locator("textarea").dispatchEvent("dragenter", { dataTransfer });
    await composer.dispatchEvent("dragleave", { dataTransfer });
    await expect(composer).toHaveClass(/is-dragging/);
    await composer.dispatchEvent("dragover", { dataTransfer });
    await composer.dispatchEvent("drop", { dataTransfer });
    await expect(composer).not.toHaveClass(/is-dragging/);
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "添加附件", exact: true })).toBeEnabled();
    await page.getByRole("textbox", { name: "发送消息" }).evaluate(element => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(["not svg"], "image.svg", { type: "image/svg+xml" }));
      element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
    });
    await expect(page.getByText("图片支持 20MB 以内的 PNG、JPEG、WebP 或 GIF")).toBeVisible();
    await page.getByLabel("上传参考图片").setInputFiles(Array.from({ length: 6 }, (_, i) => ({ name: `${i}.png`, mimeType: "image/png", buffer: png })));
    await expect(page.getByText("每次最多添加 6 张图片和 4 个文件")).toBeVisible();
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(1);
    await page.getByLabel("上传参考图片").setInputFiles(Array.from({ length: 5 }, (_, i) => ({ name: `${i}.png`, mimeType: "image/png", buffer: png })));
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(6);
    await expect(page.getByRole("button", { name: "添加附件", exact: true })).toBeEnabled();
    await composer.dispatchEvent("drop", { dataTransfer });
    await composer.evaluate((element, transfer) => {
      element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
    }, dataTransfer);
    await expect(page.locator(".dsh-upload-previews img")).toHaveCount(6);
    await expect(page).toHaveURL(/\/ai-agent$/);
  } finally { await dataTransfer.dispose(); }
});

for (const width of [1280, 390, 320]) {
  test(`生图等待占位、多张翻页和缩略图预览 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const urls = await page.evaluate(() => ["#23735a", "#275a99", "#a43e64"].map((color, index) => {
      const canvas = document.createElement("canvas"); canvas.width = 600; canvas.height = 600;
      const context = canvas.getContext("2d")!;
      context.fillStyle = color; context.fillRect(0, 0, 600, 600);
      context.fillStyle = "#fafafa"; context.fillRect(205, 165, 190, 280);
      context.fillStyle = "#20242c"; context.fillRect(235, 120, 130, 45);
      context.font = "24px sans-serif"; context.fillText(`STUDIO ${index + 1}`, 232, 310);
      return canvas.toDataURL("image/png");
    }));
    const state = await setup(page, false, urls);
    let ready = false;
    await page.route("**/images/query", async route => {
      if (ready) return route.fallback();
      const media = state.messages.flatMap(message => message.media || []).filter(item => item.kind === "task");
      for (const item of media) item.task!.status = "running";
      return route.fulfill({ json: { items: media } });
    });
    await page.getByRole("textbox", { name: "发送消息" }).fill("生成三张不同配色的商品图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByRole("button", { name: "停止回答", exact: true })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
    await expect(page.locator(".dsh-message.assistant .image-placeholder")).toContainText("完成后将在这里显示");
    await expect(page.locator(".generation-dots span")).toHaveCount(64);
    await expect(page.locator(".image-stack.has-stack .image-stack-layer")).toHaveCount(2);
    await expect(page.locator(".image-stack-layer img")).toHaveCount(0);
    await expect(page.locator(".image-stage img")).toHaveCount(0);
    await expect(page.locator(".generation-dots span").first()).toHaveCSS("animation-name", /^generation-dot/);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.locator(".generation-dots span").first()).toHaveCSS("animation-name", "none");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(page.locator(".dsh-latest-button .dsh-typing")).toBeVisible();
    await expect(page.locator(".image-pagination output")).toHaveText("1 / 3");
    const frame = (await page.locator(".image-stage").boundingBox())!;
    expect(frame.width).toBeGreaterThan(160);
    expect(frame.height).toBeCloseTo(frame.width, 0);
    await page.screenshot({ path: testInfo.outputPath(`dsh-image-pending-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "下一张生成图片", exact: true }).click();
    await expect(page.locator(".image-pagination output")).toHaveText("2 / 3");
    ready = true;
    await expect(page.locator(".image-result")).toContainText("生成完成", { timeout: 10000 });
    await expect(page.locator(".image-open img")).toHaveAttribute("src", urls[1]!);
    await expect(page.locator(".dsh-latest-button .dsh-typing")).toHaveCount(0);
    await expect(page.locator(".image-open img")).toHaveCount(1);
    await expect(page.locator(".image-stack-layer img")).toHaveCount(2);
    await expect.poll(() => page.locator(".image-stack-layer img").evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    const layers = await page.locator(".image-stack-layer").evaluateAll(elements => elements.map(element => ({ transform: getComputedStyle(element).transform, x: element.getBoundingClientRect().x, right: element.getBoundingClientRect().right })));
    expect(layers.every(layer => layer.transform !== "none" && layer.x >= 0 && layer.right <= width)).toBe(true);
    expect(layers[0]!.transform).not.toBe(layers[1]!.transform);
    await expect(page.locator(".image-generation")).toHaveCount(0);
    await expect(page.locator(".image-open img")).toHaveCSS("filter", "blur(0px)");
    await page.screenshot({ path: testInfo.outputPath(`dsh-image-carousel-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "放大生成图片 2", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "图片预览" });
    await expect(dialog.getByRole("navigation", { name: "图片缩略图" }).getByRole("button")).toHaveCount(3);
    await dialog.getByRole("button", { name: "查看第 3 张图片", exact: true }).click();
    await expect(dialog.locator(".dsh-preview-image img")).toHaveAttribute("src", urls[2]!);
    await expect(dialog.locator("header small")).toHaveText("3 / 3");
    await expect(dialog.getByRole("button", { name: "下一张图片", exact: true })).toBeDisabled();
    await page.screenshot({ path: testInfo.outputPath(`dsh-image-gallery-${width}.png`) });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "放大生成图片 3", exact: true })).toBeFocused();
    await expect(page.locator(".image-pagination output")).toHaveText("3 / 3");
    await page.getByRole("button", { name: "继续修改", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "发送消息" })).toHaveValue(/task-1:2/);
    expect(state.submissions).toHaveLength(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "切换主题" }).click();
    await page.screenshot({ path: testInfo.outputPath(`dsh-image-carousel-dark-${width}.png`), fullPage: true });
  });
}

test("同轮多个生图调用合并，下载与编辑对应真实子图，失败不伪装生成中", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("textbox", { name: "发送消息" }).fill("生成商品图");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".image-result")).toContainText("生成完成", { timeout: 10000 });
  const user = state.messages[0]!;
  const second: DshImageMedia = { ...user.media![0]!, media_id: "second", task_id: "second-task", prompt: "第二组提示词", task: { id: "second-task", status: "success", data: [{ url: imageUrl }, { url: imageUrl }] } };
  user.media!.push(second);
  await page.reload();
  await expect(page.locator(".image-result")).toHaveCount(1);
  await expect(page.locator(".image-pagination output")).toHaveText("1 / 3");
  await page.getByRole("button", { name: "下一张生成图片", exact: true }).click();
  await page.getByRole("button", { name: "下一张生成图片", exact: true }).click();
  await page.locator(".image-result details summary").click();
  await expect(page.locator(".image-result details p")).toHaveText("第二组提示词");
  await page.getByRole("button", { name: "继续修改", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "发送消息" })).toHaveValue(/second:1/);
  const request = page.waitForRequest("**/api/image-tasks/download-zip");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载生成图片", exact: true }).click();
  expect((await request).postDataJSON().items.map((item: any) => [item.task_id, item.image_index])).toEqual([["task-1", 0], ["second-task", 0], ["second-task", 1]]);
  await download;
  second.task = { id: "second-task", status: "error", error: "测试：上游拒绝生成" };
  await page.reload();
  await expect(page.locator(".image-result")).toContainText("部分生成失败");
  await page.getByRole("button", { name: "下一张生成图片", exact: true }).click();
  await expect(page.locator(".image-placeholder")).toContainText("测试：上游拒绝生成");
  await expect(page.locator(".image-placeholder.is-pending")).toHaveCount(0);
  await expect(page.locator(".generation-dots")).toHaveCount(0);
  await expect(page.locator(".image-pagination output")).toHaveText("2 / 2");
  expect(state.submissions).toHaveLength(1);
});

test("图片查询失败后退避恢复，不重复发送生成请求", async ({ page }) => {
  const state = await setup(page);
  let queries = 0;
  await page.route("**/images/query", async route => {
    queries++;
    if (queries === 1) return route.fulfill({ status: 503, json: { detail: { error: "mock temporary outage" } } });
    return route.fallback();
  });
  await page.getByRole("textbox", { name: "发送消息" }).fill("生成一张商品图");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "图片状态暂时无法更新" })).toBeVisible({ timeout: 10000 });
  await expect(page.locator(".image-result")).toContainText("生成完成", { timeout: 12000 });
  expect(state.submissions).toHaveLength(1);
  expect(queries).toBe(2);
});

test("离开会话后较慢的图片查询不会写入新会话", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/images/query", async route => { await blocked; return route.fallback(); });
  await page.getByRole("textbox", { name: "发送消息" }).fill("生成一张商品图");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await page.waitForRequest("**/images/query");
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  release();
  await expect(page.locator(".dsh-chat-header strong")).toHaveText("新的对话");
  await expect(page.locator(".image-result")).toHaveCount(0);
});

for (const width of [1280, 390, 320]) {
  test(`生成图在助手回复内可放大缩放，下载为独立操作 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const outputUrl = await page.evaluate(() => {
      const canvas = document.createElement("canvas"); canvas.width = 480; canvas.height = 320;
      const context = canvas.getContext("2d")!;
      context.fillStyle = "#34785c"; context.fillRect(0, 0, 480, 320);
      context.fillStyle = "#f8fafc"; context.fillRect(165, 75, 150, 170);
      context.fillStyle = "#263d33"; context.fillRect(193, 45, 94, 30);
      context.font = "16px sans-serif"; context.fillText("Preview sample", 183, 165);
      return canvas.toDataURL("image/png");
    });
    const state = await setup(page, false, outputUrl);
    await page.getByRole("textbox", { name: "发送消息" }).fill("生成商品图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".dsh-message.assistant .image-result")).toContainText("生成完成", { timeout: 10000 });
    await expect(page.locator(".dsh-message.user .image-result")).toHaveCount(0);
    let downloads = 0;
    page.on("download", () => downloads++);
    const button = page.getByRole("button", { name: "放大生成图片 1" });
    await button.click();
    const dialog = page.getByRole("dialog", { name: "图片预览" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator("img")).toHaveJSProperty("naturalWidth", 480);
    const original = (await dialog.locator("img").boundingBox())!;
    await page.getByRole("button", { name: "放大图片", exact: true }).click();
    await expect(dialog.locator("output")).toHaveText("150%");
    expect((await dialog.locator("img").boundingBox())!.width).toBeGreaterThan(original.width * 1.4);
    expect((await dialog.locator("img").boundingBox())!.height).toBeGreaterThan(original.height * 1.4);
    await page.getByRole("button", { name: "重置图片缩放" }).click();
    await expect(dialog.locator("output")).toHaveText("100%");
    await page.screenshot({ path: testInfo.outputPath(`dsh-image-preview-${width}.png`) });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(button).toBeFocused();
    expect(downloads).toBe(0);
    expect(page.url()).toMatch(/\/ai-agent$/);
    expect(state.submissions).toHaveLength(1);
  });

  test(`文档附件可移除、刷新恢复、单独发送且不混入图片引用 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page, true);
    await page.getByLabel("上传文件", { exact: true }).setInputFiles({ name: "商品说明.txt", mimeType: "text/plain", buffer: Buffer.from("product code:42") });
    await expect(page.locator(".dsh-attachment")).toContainText("商品说明.txt");
    await expect(page.locator(".dsh-attachment img")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`dsh-document-composer-${width}.png`) });
    const modelText = page.locator(".dsh-composer-model .composer-select__trigger .dsh-model-option > span");
    expect(await modelText.evaluateAll(elements => elements.every(element => element.getBoundingClientRect().height <= parseFloat(getComputedStyle(element).lineHeight) * 3 + 1 && element.scrollWidth <= element.clientWidth + 1))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "移除文件", exact: true }).click();
    await expect(page.locator(".dsh-upload-previews")).toHaveCount(0);
    expect(state.deleted[0]).toContain("/files/");
    await page.getByLabel("上传文件", { exact: true }).setInputFiles({ name: "商品说明.txt", mimeType: "text/plain", buffer: Buffer.from("product code:42") });
    await expect(page.locator(".dsh-attachment")).toBeVisible();
    await page.reload();
    await expect(page.locator(".dsh-attachment")).toBeVisible();
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".dsh-message.assistant")).toContainText("商品编号是 42");
    expect(state.submissions[0].content).toBe("请分析附件内容");
    expect(state.submissions[0].file_ids).toEqual(["file-2"]);
    expect(state.submissions[0].reference_ids).toEqual([]);
    await expect(page.locator(".dsh-message.user .file-reference")).toContainText("商品说明.txt");
    await expect(page.locator(".image-result")).toHaveCount(0);
    await page.reload();
    await expect(page.locator(".file-reference")).toContainText("商品说明.txt");
  });
}

test("文件上传中展示状态，失败保留具体错误且不创建伪附件", async ({ page }) => {
  const state = await setup(page, true);
  let release!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/files", async route => { await blocked; return route.fulfill({ status: 400, json: { detail: { error: "未提取到可读文字" } } }); });
  await page.getByLabel("上传文件", { exact: true }).setInputFiles({ name: "scan.pdf", mimeType: "application/pdf", buffer: Buffer.from("mock scanned pdf") });
  await expect(page.getByRole("status").filter({ hasText: "正在上传并读取" })).toBeVisible();
  await expect(page.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "添加附件", exact: true })).toBeDisabled();
  release();
  await expect(page.locator(".dsh-attachment.has-error")).toContainText("未提取到可读文字");
  await page.getByRole("button", { name: "移除失败附件" }).click();
  await expect(page.locator(".dsh-upload-previews")).toHaveCount(0);
  expect(state.submissions).toHaveLength(0);
});

for (const width of [1280, 390, 320]) {
  test(`加号大面板适配主题并支持关闭与键盘操作 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await setup(page, true);
    const trigger = page.getByRole("button", { name: "添加附件", exact: true });
    const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
    await expect(trigger).toHaveCount(1);
    await expect(page.getByRole("button", { name: "添加参考图片", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "添加文件", exact: true })).toHaveCount(0);
    for (const theme of ["light", "dark"]) {
      if (theme === "dark") await page.getByRole("button", { name: "切换主题" }).click();
      await trigger.click();
      await expect(menu).toBeVisible();
      await expect(trigger).toHaveAttribute("aria-expanded", "true");
      await expect(menu.locator(".dsh-tool-action strong")).toHaveText(["上传图片", "上传文件"]);
      await expect(menu).toHaveCSS("background-color", theme === "dark" ? "rgb(23, 26, 33)" : "rgb(255, 255, 255)");
      const bounds = (await menu.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(12);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width - 12);
      expect(bounds.y).toBeGreaterThanOrEqual(12);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(888);
      expect(bounds.width).toBeGreaterThanOrEqual(width > 520 ? 520 : width - 52);
      expect(bounds.height).toBeGreaterThanOrEqual(240);
      if (width > 520) {
        const nav = (await menu.getByRole("tablist").boundingBox())!;
        const content = (await menu.getByRole("tabpanel").boundingBox())!;
        expect(content.x).toBeGreaterThanOrEqual(nav.x + nav.width - 1);
      }
      await page.screenshot({ path: testInfo.outputPath(`dsh-attachment-menu-${theme}-${width}.png`) });
      await page.keyboard.press("Escape");
      await expect(menu).not.toBeVisible();
      await expect(trigger).toBeFocused();
      await trigger.press("ArrowDown");
      await expect(menu.getByRole("button", { name: /^上传图片/ })).toBeFocused();
      await page.keyboard.press("ArrowDown");
      await expect(menu.getByRole("button", { name: /^上传文件/ })).toBeFocused();
      await page.keyboard.press("Home");
      await expect(menu.getByRole("button", { name: /^上传图片/ })).toBeFocused();
      await page.keyboard.press("End");
      await expect(menu.getByRole("button", { name: /^上传文件/ })).toBeFocused();
      await page.locator(".dsh-chat-header strong").click();
      await expect(menu).not.toBeVisible();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");
    }
  });
}

test("加号菜单的两个选项打开对应文件选择器并上传，选择后关闭", async ({ page }) => {
  await setup(page, true);
  const trigger = page.getByRole("button", { name: "添加附件", exact: true });
  const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
  for (const kind of ["图片", "文件"]) {
    await trigger.click();
    const choosing = page.waitForEvent("filechooser");
    await menu.getByRole("button", { name: new RegExp(`^上传${kind}`) }).click();
    const chooser = await choosing;
    expect(await chooser.element().getAttribute("accept")).toContain(kind === "图片" ? "image/png" : ".pdf");
    await expect(menu).not.toBeVisible();
    await chooser.setFiles(kind === "图片"
      ? { name: "product.png", mimeType: "image/png", buffer: png }
      : { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("product code:42") });
    await expect(page.locator(".dsh-upload-previews .dsh-attachment")).toHaveCount(kind === "图片" ? 1 : 2);
    await expect(trigger).toBeEnabled();
  }
});

test("图片和文件数量上限只禁用上传，保留其他工具，移除后恢复", async ({ page }) => {
  await setup(page, true);
  const trigger = page.getByRole("button", { name: "添加附件", exact: true });
  const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
  await page.getByLabel("上传参考图片").setInputFiles(Array.from({ length: 6 }, (_, i) => ({ name: `${i}.png`, mimeType: "image/png", buffer: png })));
  await expect(page.locator(".dsh-upload-previews img")).toHaveCount(6);
  await expect(trigger).toBeEnabled();
  await trigger.click();
  await expect(menu.getByRole("button", { name: /^上传图片/ })).toBeDisabled();
  await expect(menu.getByRole("button", { name: /^上传文件/ })).toBeEnabled();
  await expect(menu.getByRole("button", { name: /^上传文件/ })).toBeFocused();
  await page.keyboard.press("Escape");
  await page.getByLabel("上传文件", { exact: true }).setInputFiles(Array.from({ length: 4 }, (_, i) => ({ name: `${i}.txt`, mimeType: "text/plain", buffer: Buffer.from("details") })));
  await expect(page.locator(".dsh-upload-previews .dsh-attachment")).toHaveCount(10);
  await expect(trigger).toBeEnabled();
  await trigger.click();
  await expect(menu.getByRole("button", { name: /^上传图片/ })).toBeDisabled();
  await expect(menu.getByRole("button", { name: /^上传文件/ })).toBeDisabled();
  await menu.getByRole("tab", { name: "生成图片", exact: true }).click();
  await expect(menu.getByRole("button", { name: "生成图片", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "移除参考图片", exact: true }).first().click();
  await expect(trigger).toBeEnabled();
  await trigger.click();
  await expect(menu.getByRole("button", { name: /^上传图片/ })).toBeEnabled();
  await expect(menu.getByRole("button", { name: /^上传文件/ })).toBeDisabled();
});

test("未开放的文件、专家、协作和搜索入口不显示", async ({ page }) => {
  await setup(page);
  await page.getByRole("button", { name: "添加附件", exact: true }).click();
  const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
  await expect(menu.locator(".dsh-tool-action strong")).toHaveText(["上传图片"]);
  await expect(menu.getByRole("tab")).toHaveText(["添加图片和文件", "生成图片"]);
});

for (const width of [1440, 390, 320]) {
  test(`大工具面板切换真实能力，保留草稿且不自动发送 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 320 ? 568 : 900 });
    const state = await setup(page, true, imageUrl, true, true, true);
    const input = page.getByRole("textbox", { name: "发送消息" });
    const trigger = page.getByRole("button", { name: "添加附件", exact: true });
    const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
    await input.fill("为新品做一套素材");
    await trigger.click();
    await expect(menu.getByRole("tab")).toHaveText(["添加图片和文件", "生成图片", "专家", "多智能体协作", "连接器"]);
    await expect(menu).toHaveCSS("opacity", "1");
    const bounds = (await menu.boundingBox())!;
    expect(bounds.y).toBeGreaterThanOrEqual(12);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(page.viewportSize()!.height - 12);
    await page.screenshot({ path: testInfo.outputPath(`dsh-tools-expanded-${width}.png`) });
    await menu.getByRole("tab", { name: "生成图片", exact: true }).click();
    await expect(menu.locator(".dsh-tools-current")).toContainText("2.6");
    await menu.getByRole("button", { name: "生成图片", exact: true }).click();
    await expect(menu).not.toBeVisible();
    await expect(input).toHaveValue("请生成图片：为新品做一套素材");
    await expect(input).toBeFocused();
    await trigger.click();
    await menu.getByRole("tab", { name: "生成图片", exact: true }).click();
    await menu.getByRole("button", { name: "生成图片", exact: true }).click();
    await expect(input).toHaveValue("请生成图片：为新品做一套素材");
    await input.fill("核对新品资料");
    await trigger.click();
    await menu.getByRole("tab", { name: "连接器", exact: true }).click();
    await menu.getByRole("button", { name: "联网搜索", exact: true }).click();
    await expect(input).toHaveValue("请联网搜索并核对资料：核对新品资料");
    await expect(input).toBeFocused();
    await trigger.click();
    await menu.getByRole("tab", { name: "多智能体协作", exact: true }).click();
    await menu.getByRole("button", { name: "多智能体协作", exact: true }).click();
    await trigger.click();
    await menu.getByRole("tab", { name: "多智能体协作", exact: true }).click();
    await expect(menu.getByRole("button", { name: "多智能体协作", exact: true })).toHaveAttribute("aria-pressed", "true");
    await menu.getByRole("button", { name: "普通对话", exact: true }).click();
    await expect(input).toHaveValue("请联网搜索并核对资料：核对新品资料");
    expect(state.submissions).toHaveLength(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test("加号平滑旋转为关闭，重复开关与空间不足居中定位正常", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await setup(page, true);
  const trigger = page.getByRole("button", { name: "添加附件", exact: true });
  const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
  const icon = trigger.locator("svg");
  await expect(icon).toHaveCSS("transition-duration", "0.24s");
  for (let i = 0; i < 3; i++) {
    await trigger.click();
    await expect(menu).toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await expect.poll(() => icon.evaluate(element => Math.round(new DOMMatrix(getComputedStyle(element).transform).a * 100))).toBe(71);
    await expect(menu).toHaveAttribute("data-placement", "center");
    await menu.getByRole("button", { name: "关闭能力面板", exact: true }).click();
    await expect(menu).not.toBeVisible();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await expect(icon).toHaveCSS("transform", "matrix(1, 0, 0, 1, 0, 0)");
  }
  await page.getByRole("textbox", { name: "发送消息" }).fill("生成图片");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".dsh-message.assistant")).toBeVisible();
  await expect(page.locator(".dsh-chat-panel")).not.toHaveClass(/is-welcome/);
  await trigger.click();
  await expect(menu).toBeVisible();
  await expect(menu).toHaveAttribute("data-placement", "top");
  const bounds = (await menu.boundingBox())!;
  const composer = (await page.locator(".dsh-composer").boundingBox())!;
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(composer.y);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

for (const width of [1280, 390]) {
test(`工具分类支持方向键切换并在减少动态效果时关闭旋转动画 ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, true, imageUrl, true, true, true);
  const trigger = page.getByRole("button", { name: "添加附件", exact: true });
  const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
  await trigger.click();
  expect(await trigger.locator("svg").evaluate(element => parseFloat(getComputedStyle(element).transitionDuration))).toBeLessThanOrEqual(0.001);
  await page.keyboard.press("ArrowLeft");
  await expect(menu.getByRole("tab", { name: "添加图片和文件" })).toBeFocused();
  await expect(menu.getByRole("tablist")).toHaveAttribute("aria-orientation", width > 520 ? "vertical" : "horizontal");
  await page.keyboard.press(width > 520 ? "ArrowDown" : "ArrowRight");
  await expect(menu.getByRole("tab", { name: "生成图片", exact: true })).toBeFocused();
  await page.keyboard.press("End");
  await expect(menu.getByRole("tab", { name: "连接器", exact: true })).toBeFocused();
  await page.keyboard.press(width > 520 ? "ArrowRight" : "ArrowDown");
  await expect(menu.getByRole("button", { name: "联网搜索", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});
}

for (const width of [1440, 390, 320]) {
  test(`专家不自动启用技能，独立选择和刷新恢复 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const { submissions } = await setup(page, true, imageUrl, true, false, true, true);
    const plus = page.getByRole("button", { name: "添加附件", exact: true });
    const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
    const input = page.getByRole("textbox", { name: "发送消息" });
    const chip = page.locator(".dsh-skill-chip");
    const openSection = async (name: string) => {
      if (await plus.getAttribute("aria-expanded") !== "true") await plus.click();
      await menu.getByRole("tab", { name, exact: true }).click();
    };
    await openSection("专家");
    await menu.getByRole("button", { name: /电商设计专家.*商品主图/ }).click();
    await expect(chip).toHaveCount(0);
    await expect(page.locator(".dsh-expert-chip")).toBeVisible();
    await openSection("技能");
    const skill = menu.getByRole("button", { name: /GPT Image 设计库/ });
    await expect(skill).toHaveAttribute("aria-pressed", "false");
    await skill.click();
    await expect(chip).toContainText("GPT Image 设计库");
    await openSection("技能");
    await expect(skill).toHaveAttribute("aria-pressed", "true");
    await page.screenshot({ path: testInfo.outputPath(`dsh-skills-${width}.png`) });
    const bounds = (await menu.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(12);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width - 12);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "移除技能 GPT Image 设计库", exact: true }).click();
    await expect(chip).toHaveCount(0);
    await expect(page.locator(".dsh-expert-chip")).toBeVisible();
    await input.fill("只给主图建议，不出图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(1);
    expect(submissions[0].skill_ids).toEqual([]);
    expect(submissions[0].expert_id).toBe("ecommerce-design");
    await page.reload();
    await expect(input).toBeEnabled();
    await expect(chip).toHaveCount(0);
    await expect(page.locator(".dsh-expert-chip")).toBeVisible();
    await page.getByRole("button", { name: "移除电商设计专家" }).click();
    await openSection("技能");
    await expect(skill).toHaveAttribute("aria-pressed", "false");
    await skill.click();
    await expect(chip).toBeVisible();
    await openSection("专家");
    await menu.getByRole("button", { name: /电商设计专家.*商品主图/ }).click();
    await expect(chip).toBeVisible();
    await page.getByRole("button", { name: "移除电商设计专家" }).click();
    await expect(chip).toBeVisible();
    await expect(page.locator(".dsh-expert-chip")).toHaveCount(0);
    await input.fill("推荐一个商品摄影构图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect.poll(() => submissions.length).toBe(2);
    expect(submissions[1].skill_ids).toEqual(["gpt-image-2-style-library"]);
    expect(submissions[1].expert_id).toBeUndefined();
    await page.reload();
    await expect(input).toBeEnabled();
    await expect(chip).toBeVisible();
    await openSection("连接器");
    await expect(menu.getByRole("button", { name: /联网搜索/ })).toBeEnabled();
    await menu.getByRole("button", { name: /联网搜索/ }).click();
    await expect(input).toHaveValue("请联网搜索并核对资料：");
    expect(submissions).toHaveLength(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width < 760) await page.getByRole("button", { name: "打开对话历史" }).click();
    await page.getByRole("button", { name: "新会话", exact: true }).click();
    await expect(chip).toHaveCount(0);
  });
}

test("连接器未配置时明确禁用，暗色技能面板保持可读", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setup(page, true, imageUrl, true, false, false, true);
  await expect(page.getByRole("textbox", { name: "发送消息" })).toBeEnabled();
  await page.getByRole("button", { name: "添加附件", exact: true }).click();
  await page.getByRole("tab", { name: "连接器", exact: true }).click();
  await expect(page.getByRole("button", { name: "联网搜索", exact: true })).toBeDisabled();
  await expect(page.getByRole("dialog", { name: "附件与工具" })).toContainText("未配置");
  await page.keyboard.press("Escape");
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.getByRole("button", { name: "添加附件", exact: true }).click();
  await page.getByRole("tab", { name: "技能", exact: true }).click();
  await expect(page.getByRole("button", { name: /GPT Image 设计库/ })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("dsh-skills-dark.png") });
});

for (const width of [1440, 390, 320]) {
  test(`能力面板搜索来源、连续选择与四项上限 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 320 ? 568 : 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const skills: DshSkill[] = [
      { id: "gpt-image-2-style-library", label: "GPT Image 设计库", description: "商品商业视觉、摄影、海报与构图模板", source: "awesome-gpt-image-2" },
      { id: "diagram-design", label: "图表设计", description: "流程图、架构图、组织图与数据图表", source: "cathrynlavery/diagram-design" },
      { id: "ecommerce-competitor-analysis", label: "电商竞品分析", description: "对比商品定位、价格、营销策略与差异化", source: "nexscope-ai/ecommerce-skills" },
      { id: "customer-research", label: "用户痛点研究", description: "从评论、访谈和客服反馈中提炼购买动机、痛点与用户画像", source: "coreyhaines31/marketingskills" },
      { id: "competitor-profiling", label: "竞品深度调研", description: "依据公开网页或上传资料，整理带来源的品牌与竞品档案", source: "coreyhaines31/marketingskills" },
      { id: "ecommerce-competitor-analyzer", label: "商品页面拆解", description: "拆解商品文案、主图视觉、评论与市场定位", source: "buluslan/ecommerce-competitor-analyzer" },
    ];
    const { submissions } = await setup(page, true, imageUrl, true, true, true, skills);
    const trigger = page.getByRole("button", { name: "添加附件", exact: true });
    const menu = page.getByRole("dialog", { name: "附件与工具", exact: true });
    await page.getByRole("textbox", { name: "发送消息" }).fill("保留用户草稿");
    await trigger.click();
    await menu.getByRole("tab", { name: "技能", exact: true }).click();
    const actions = menu.locator(".dsh-tool-action");
    const search = menu.getByRole("searchbox", { name: "搜索技能", exact: true });
    await expect(actions).toHaveCount(6);
    await search.fill("购买动机");
    await expect(actions).toHaveCount(1);
    await expect(actions).toContainText("用户痛点研究");
    await search.press("Home");
    await expect(search).toBeFocused();
    await search.fill("不存在的技能");
    await expect(menu.getByRole("status").filter({ hasText: "没有找到" })).toBeVisible();
    await menu.getByRole("button", { name: "清除筛选", exact: true }).click();
    await expect(actions).toHaveCount(6);
    await menu.getByRole("button", { name: "技能来源", exact: true }).click();
    const sources = page.getByRole("listbox", { name: "技能来源", exact: true });
    const sourcesBox = (await sources.boundingBox())!;
    expect(sourcesBox.x).toBeGreaterThanOrEqual(0);
    expect(sourcesBox.x + sourcesBox.width).toBeLessThanOrEqual(width);
    await sources.getByRole("option", { name: "coreyhaines31/marketingskills", exact: true }).click();
    await expect(menu).toBeVisible();
    await expect(actions).toHaveCount(2);
    const research = menu.locator('[data-skill-id="customer-research"]');
    await research.click();
    await expect(menu).toBeVisible();
    await expect(research).toBeFocused();
    await expect(research).toHaveAttribute("aria-pressed", "true");
    await expect(menu.locator(".dsh-tools-selection")).toHaveText("已选择 1 / 4");
    await page.screenshot({ path: testInfo.outputPath(`dsh-capabilities-light-${width}.png`), fullPage: true });
    await menu.getByRole("button", { name: "技能来源", exact: true }).click();
    await sources.getByRole("option", { name: "全部来源", exact: true }).click();
    for (const id of ["gpt-image-2-style-library", "diagram-design", "competitor-profiling"]) {
      await menu.locator(`[data-skill-id="${id}"]`).click();
      await expect(menu).toBeVisible();
    }
    await expect(menu.locator(".dsh-tools-selection")).toHaveText("已选择 4 / 4");
    const fifth = menu.locator('[data-skill-id="ecommerce-competitor-analysis"]');
    await expect(fifth).toBeDisabled();
    await research.click();
    await expect(fifth).toBeEnabled();
    await expect(menu).toBeVisible();
    await menu.getByRole("button", { name: "关闭能力面板", exact: true }).click();
    await expect(trigger).toBeFocused();
    await expect(page.locator(".dsh-skill-chip")).toHaveCount(3);
    await expect(page.getByRole("textbox", { name: "发送消息" })).toHaveValue("保留用户草稿");
    await page.getByRole("button", { name: "切换主题" }).click();
    await trigger.click();
    await menu.getByRole("tab", { name: "技能", exact: true }).click();
    await expect(search).toHaveValue("");
    await expect(actions).toHaveCount(6);
    await expect(menu).toHaveCSS("background-color", "rgb(23, 26, 33)");
    await page.screenshot({ path: testInfo.outputPath(`dsh-capabilities-dark-${width}.png`), fullPage: true });
    await menu.getByRole("button", { name: "技能来源", exact: true }).click();
    await expect(sources).toBeVisible();
    await expect(sources.locator("..")).toHaveCSS("background-color", "rgb(23, 26, 33)");
    await sources.press("Escape");
    await expect(sources).not.toBeVisible();
    await expect(menu).toBeVisible();
    await menu.getByRole("tab", { name: "专家", exact: true }).click();
    await menu.getByRole("searchbox", { name: "搜索专家", exact: true }).fill("商品主图");
    await expect(actions).toHaveCount(1);
    await expect(actions).toContainText("电商设计专家");
    await menu.getByRole("tab", { name: "技能", exact: true }).click();
    await expect(search).toHaveValue("");
    const bounds = (await menu.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(12);
    expect(bounds.y).toBeGreaterThanOrEqual(12);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width - 12);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(page.viewportSize()!.height - 12);
    expect(await menu.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(submissions).toHaveLength(0);
  });
}
