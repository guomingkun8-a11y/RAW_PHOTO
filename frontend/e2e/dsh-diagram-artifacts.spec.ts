import { expect, test, type Page } from "@playwright/test";
import type { DshAgentMessage, DshImageMedia } from "../src/features/dsh-agent/api";
import { readFile } from "node:fs/promises";

const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 280" width="720" height="280"><style>text{font-family:system-ui,sans-serif;fill:#17212b}</style><rect width="720" height="280" fill="#f8faf9"/><path d="M220 140H480" stroke="#42786a" stroke-width="3"/><rect x="40" y="80" width="180" height="120" rx="6" fill="#e3f1eb"/><rect x="480" y="80" width="180" height="120" rx="6" fill="#e8edf8"/><text x="130" y="146" text-anchor="middle" font-size="24">提交需求</text><text x="570" y="146" text-anchor="middle" font-size="24">审核发布</text></svg>';

async function setup(page: Page) {
  const messages: DshAgentMessage[] = [];
  const submissions: any[] = [];
  const requests: string[] = [];
  const conversation = { conversation_id: "dsh-diagrams", model: "gem-3.5-flash", title: "商品上新流程", status: "idle", turn_count: 0, created_at: "2026-11-08 10:00:00", updated_at: "2026-11-08 10:00:00" };
  await page.route("**/*", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/login" || path === "/api/auth/me") return route.fulfill({ json: { ok: true, role: "admin", subject_id: "alice", username: "alice", name: "Alice", token: "mock-diagrams" } });
    if (path === "/api/navigation") return route.fulfill({ json: { items: [] } });
    if (path === "/api/dsh-agent/status") return route.fulfill({ json: { enabled: true, configured: true, available: true, model: "gem-3.5-flash", models: [{ id: "gem-3.5-flash", label: "GEM 3.5 Flash" }], skills: [{ id: "diagram-design", label: "图表设计", description: "流程图、架构图与组织图，可按需调用", source: "cathrynlavery/diagram-design" }, ...["ecommerce-competitor-analysis", "customer-research", "competitor-profiling", "ecommerce-competitor-analyzer"].map(id => ({ id, label: id, description: "按需研究", source: "test" }))] } });
    if (path === "/api/dsh-agent/conversations") return route.fulfill({ json: route.request().method() === "POST" ? conversation : { items: [conversation], total: 1 } });
    if (path === `/api/dsh-agent/conversations/${conversation.conversation_id}`) return route.fulfill({ json: { conversation, items: messages } });
    if (path.endsWith("/messages/stream")) {
      const body = route.request().postDataJSON();
      submissions.push(body);
      const index = submissions.length;
      const generates = /流程|修改/.test(body.content);
      const user: DshAgentMessage = { message_id: `user-${index}`, conversation_id: conversation.conversation_id, role: "user", status: "completed", content: body.content, model: "", input_tokens: null, output_tokens: null, total_tokens: null, usage: {}, error_code: "", created_at: "2026-11-08 10:00:00", skill_ids: body.skill_ids };
      const media: DshImageMedia = { media_id: `diagram-${index}`, task_id: "", conversation_id: conversation.conversation_id, message_id: user.message_id, kind: "diagram", title: index === 1 ? "商品上新流程" : "商品上新流程第二版", svg: index === 1 ? svg : svg.replace("审核发布", "确认发布"), width: 720, height: 280 };
      const assistant: DshAgentMessage = { ...user, message_id: `assistant-${index}`, role: "assistant", content: generates ? "图示已保存，可以继续告诉我需要调整的步骤。" : "你好！", model: conversation.model, activities: generates ? [{ id: "draw", kind: "diagram", status: "completed", title: "图示工具已完成" }] : [] };
      if (generates) user.media = [media];
      messages.push(user, assistant);
      return route.fulfill({ contentType: "text/event-stream", body: [{ type: "start", conversation_id: conversation.conversation_id, user_message_id: user.message_id, model: conversation.model }, ...(generates ? [{ type: "media", media }] : []), { type: "complete", message: assistant }].map(event => `data: ${JSON.stringify(event)}\n\n`).join("") });
    }
    if (path.startsWith("/api/") || ["xhr", "fetch"].includes(route.request().resourceType())) { requests.push(path); return route.fulfill({ json: {} }); }
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
  return { submissions, requests };
}

for (const width of [1440, 390, 320]) {
  test(`图表技能按需调用，预览导出和刷新恢复 ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    const input = page.getByRole("textbox", { name: "发送消息" });
    await expect(page.locator(".dsh-skill-chip")).toHaveCount(0);
    await page.getByRole("button", { name: "添加附件", exact: true }).click();
    await page.getByRole("tab", { name: "技能", exact: true }).click();
    await expect(page.getByRole("button", { name: /图表设计.*流程图/ })).toHaveAttribute("aria-pressed", "false");
    await page.keyboard.press("Escape");
    await input.fill("画一个商品上新流程图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const artifact = page.getByRole("region", { name: "图示：商品上新流程", exact: true });
    await expect(artifact.getByRole("button", { name: "放大图示", exact: true })).toBeEnabled();
    await expect(page.locator(".dsh-message.assistant .dsh-artifact")).toHaveCount(1);
    await expect(page.locator(".dsh-message.user .dsh-artifact")).toHaveCount(0);
    expect(state.submissions[0].skill_ids).toEqual([]);
    await expect(page.locator(".dsh-skill-chip")).toHaveCount(0);
    const pixels = await artifact.locator("img").evaluate(image => {
      const canvas = document.createElement("canvas");
      canvas.width = 720; canvas.height = 280;
      const context = canvas.getContext("2d")!;
      context.drawImage(image as HTMLImageElement, 0, 0);
      return [Array.from(context.getImageData(60, 100, 1, 1).data), Array.from(context.getImageData(10, 10, 1, 1).data)];
    });
    expect(pixels[0]).not.toEqual(pixels[1]);
    await artifact.getByRole("button", { name: "放大图示", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "图示预览", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "放大图示", exact: true }).last().click();
    await expect(page.getByRole("dialog").getByText("150%", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "关闭图示预览", exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath(`diagram-light-${width}.png`) });
    for (const format of ["SVG", "PNG", "HTML"]) {
      const pending = page.waitForEvent("download");
      await artifact.getByRole("button", { name: `下载图示 ${format}`, exact: true }).click();
      const download = await pending;
      expect(download.suggestedFilename()).toBe(`商品上新流程.${format.toLowerCase()}`);
      const path = await download.path();
      const bytes = await readFile(path!);
      if (format === "PNG") expect(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))).toBe(true);
      else if (format === "SVG") expect(bytes.toString()).toContain("提交需求");
      else expect(bytes.toString()).toContain("Content-Security-Policy");
    }
    await page.reload();
    await expect(artifact.getByRole("button", { name: "放大图示", exact: true })).toBeEnabled();
    await input.fill("修改最后一步为确认发布");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.locator(".dsh-message.assistant .dsh-artifact")).toHaveCount(2);
    await expect(page.getByRole("region", { name: "图示：商品上新流程第二版", exact: true }).getByRole("button", { name: "放大图示", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "切换主题" }).click();
    await page.screenshot({ path: testInfo.outputPath(`diagram-dark-${width}.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(state.requests.some(path => path.includes("images/query") || path.includes("image-tasks"))).toBe(false);
  });
}

test("手动选择图表技能不自动发消息，普通聊天不显示图示", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "添加附件", exact: true }).click();
  await page.getByRole("tab", { name: "技能", exact: true }).click();
  await page.getByRole("button", { name: /图表设计.*流程图/ }).click();
  await expect(page.locator(".dsh-skill-chip")).toContainText("图表设计");
  expect(state.submissions).toHaveLength(0);
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "发送消息" }).fill("你好");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".dsh-message.assistant .dsh-message-text")).toContainText("你好！");
  await expect(page.locator(".dsh-artifact")).toHaveCount(0);
  expect(state.submissions[0].skill_ids).toEqual(["diagram-design"]);
});

test("四种商品与竞品研究技能可选但不默认启用", async ({ page }) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "添加附件", exact: true }).click();
  await page.getByRole("tab", { name: "技能", exact: true }).click();
  const skills = ["ecommerce-competitor-analysis", "customer-research", "competitor-profiling", "ecommerce-competitor-analyzer"];
  for (const id of skills) {
    await expect(page.getByRole("button", { name: new RegExp(id) })).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: new RegExp(id) }).click();
  }
  await expect(page.locator(".dsh-skill-chip")).toHaveCount(4);
  await expect(page.getByRole("button", { name: /图表设计.*流程图/ })).toBeDisabled();
  await page.keyboard.press("Escape");
  expect(state.submissions).toHaveLength(0);
  await page.getByRole("textbox", { name: "发送消息" }).fill("分析商品竞品和客户评论");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  expect(state.submissions[0].skill_ids).toEqual(skills);
  await page.reload();
  await expect(page.locator(".dsh-skill-chip")).toHaveCount(4);
});
