import { expect, test, type Page } from "@playwright/test";
import type { SystemAnnouncement } from "../src/lib/api";

async function openManagement(page: Page, role = "admin") {
  const state = {
    items: [{
      id: 1, title: "已有更新公告", content: "第一行内容\n第二行内容", type: "info", enabled: true,
      created_at: "2026-10-25 09:00:00", updated_at: "2026-10-25 09:00:00",
    }] as SystemAnnouncement[],
    failSave: false, failLoad: false, writes: 0,
    event: null as SystemAnnouncement | null,
  };
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    if (path === "/auth/login" || path === "/api/auth/me") {
      await route.fulfill({ json: { ok: true, role, subject_id: role === "admin" ? "local-admin" : "member-1", username: "admin", name: "测试成员", token: "test-token" } });
      return;
    }
    if (path === "/api/navigation") {
      await route.fulfill({ json: { items: [{ id: "image", visible: true }] } });
      return;
    }
    if (path === "/api/events/stream") {
      await route.fulfill({ contentType: "text/event-stream", body: state.event ? `event: announcement\ndata: ${JSON.stringify(state.event)}\n\n` : ": connected\n\n" });
      return;
    }
    if (path === "/api/system/announcements" && method === "GET") {
      if (state.failLoad && url.searchParams.has("include_disabled")) {
        await route.fulfill({ status: 503, json: { detail: "公告读取暂时不可用" } });
        return;
      }
      await route.fulfill({ json: { items: state.items.filter((item) => url.searchParams.has("include_disabled") || item.enabled), total: state.items.length } });
      return;
    }
    if (path.startsWith("/api/system/announcements") && method !== "GET") {
      state.writes += 1;
      if (state.failSave) {
        await route.fulfill({ status: 503, json: { detail: "公告保存暂时不可用" } });
        return;
      }
      if (method === "POST") {
        const item = { id: state.items.length + 1, ...route.request().postDataJSON(), created_at: "2026-10-25 10:00:00", updated_at: "2026-10-25 10:00:00" };
        state.items.unshift(item);
        if (item.enabled) state.event = item;
        await route.fulfill({ json: item });
      } else {
        const item = state.items.find((value) => value.id === Number(path.split("/").pop()))!;
        Object.assign(item, method === "DELETE" ? { enabled: false } : route.request().postDataJSON());
        state.event = item;
        await route.fulfill({ json: item });
      }
      return;
    }
    if (path.startsWith("/api/") || ["fetch", "xhr"].includes(route.request().resourceType())) {
      await route.fulfill({ json: path.endsWith("/count") ? { count: 0 } : {} });
      return;
    }
    await route.continue();
  });
  await page.goto("/login");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").click();
  await page.getByTestId("login-password").fill("password123");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
  await page.goto("/system-management?tab=announcements");
  return state;
}

test("公告新增、预览、编辑、确认下架及重新发布", async ({ page }) => {
  const state = await openManagement(page);
  const panel = page.getByRole("region", { name: "公告管理", exact: true });
  const list = panel.getByRole("list", { name: "公告列表" });
  await expect(list.getByRole("heading", { name: "已有更新公告" })).toBeVisible();
  await panel.getByRole("button", { name: "新增公告", exact: true }).click();
  await expect(page.getByLabel("公告标题", { exact: false })).toBeFocused();
  const publish = panel.getByRole("button", { name: "发布公告", exact: true });
  await expect(publish).toBeDisabled();
  await page.getByLabel("公告标题", { exact: false }).fill("图片生成体验更新");
  await page.getByLabel("公告正文", { exact: false }).fill("优化部分功能体验。\n解决了一些已知问题。");
  await panel.getByRole("button", { name: "公告类型", exact: true }).click();
  await page.getByRole("option", { name: "功能更新" }).click();
  const typeTrigger = panel.getByRole("button", { name: "公告类型", exact: true });
  const typeValue = await typeTrigger.locator(".composer-select__value").boundingBox();
  const typeArrow = await typeTrigger.locator("svg").boundingBox();
  expect(Math.abs((typeValue!.y + typeValue!.height / 2) - (typeArrow!.y + typeArrow!.height / 2))).toBeLessThan(2);
  await expect(panel.getByLabel("公告预览").getByText("图片生成体验更新", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/announcement-editor-desktop.png", fullPage: true });
  await publish.click();
  await expect.poll(() => state.items[0]?.type).toBe("success");
  await expect(list.getByRole("heading", { name: "图片生成体验更新" })).toBeVisible();
  await expect(page.getByRole("form", { name: "公告编辑" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "通知", exact: true }).locator("span")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "通知", exact: true }).click();
  await expect(page.getByText("图片生成体验更新", { exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "通知", exact: true }).click();

  await panel.getByRole("button", { name: "编辑图片生成体验更新", exact: true }).click();
  await page.getByLabel("公告正文", { exact: false }).fill("已更新的公告内容");
  await panel.getByRole("button", { name: "保存并发布更新" }).click();
  await expect(list.getByText("已更新的公告内容")).toBeVisible();
  await panel.getByRole("button", { name: "下架图片生成体验更新", exact: true }).click();
  await page.getByRole("dialog", { name: "下架公告" }).getByRole("button", { name: "取消", exact: true }).click();
  expect(state.items[0]?.enabled).toBe(true);
  await panel.getByRole("button", { name: "下架图片生成体验更新", exact: true }).click();
  await page.getByRole("button", { name: "确认下架", exact: true }).click();
  await expect.poll(() => state.items[0]?.enabled).toBe(false);
  await panel.getByRole("button", { name: "发布图片生成体验更新", exact: true }).click();
  await page.getByRole("button", { name: "确认发布", exact: true }).click();
  await expect.poll(() => state.items[0]?.enabled).toBe(true);
  await page.reload();
  await expect(list.getByText("已更新的公告内容")).toBeVisible();
  expect(state.writes).toBe(4);
});

test("保存失败保留输入、未发布保存与移动端深色显示", async ({ page }) => {
  const state = await openManagement(page);
  const panel = page.getByRole("region", { name: "公告管理", exact: true });
  await page.getByRole("button", { name: "通知", exact: true }).click();
  await page.getByRole("button", { name: "通知", exact: true }).click();
  await panel.getByRole("button", { name: "新增公告", exact: true }).click();
  await page.getByLabel("公告标题", { exact: false }).fill("待发布的维护通知");
  await page.getByLabel("公告正文", { exact: false }).fill("维护内容尚未确认，暂不发布。");
  state.failSave = true;
  await panel.getByRole("button", { name: "保存但不发布" }).click();
  await expect(panel.getByRole("alert")).toHaveText("公告保存暂时不可用");
  await expect(page.getByLabel("公告正文", { exact: false })).toHaveValue("维护内容尚未确认，暂不发布。");
  state.failSave = false;
  await panel.getByRole("button", { name: "保存但不发布" }).click();
  await expect.poll(() => state.items.length).toBe(2);
  expect(state.items[0]?.enabled).toBe(false);
  await expect(page.getByRole("button", { name: "通知", exact: true }).locator("span")).toHaveCount(0);
  await expect(panel.getByRole("listitem").filter({ hasText: "待发布的维护通知" }).getByText("未发布", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "切换主题", exact: true }).click();
  await panel.getByRole("button", { name: "编辑待发布的维护通知" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(panel.getByRole("button", { name: "发布公告", exact: true })).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await page.screenshot({ path: "test-results/announcement-editor-mobile-dark.png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await panel.getByRole("button", { name: "取消编辑" }).click();
  state.failLoad = true;
  await panel.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("公告读取暂时不可用");
  state.failLoad = false;
  await panel.getByRole("button", { name: "重试", exact: true }).click();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  await expect(panel.getByRole("heading", { name: "待发布的维护通知" })).toBeVisible();
});

test("普通用户不能进入系统公告管理", async ({ page }) => {
  const state = await openManagement(page, "user");
  await expect(page).toHaveURL(/\/image$/);
  await page.getByRole("button", { name: "用户菜单" }).click();
  await expect(page.getByRole("link", { name: "系统管理", exact: true })).toHaveCount(0);
  expect(state.writes).toBe(0);
});
