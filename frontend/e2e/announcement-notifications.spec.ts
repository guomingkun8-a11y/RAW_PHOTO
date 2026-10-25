import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page) {
  const state = {
    owner: "local-admin",
    failLoad: false,
    items: [1, 2].map((id) => ({
      id, title: `更新公告 ${id}`, content: "优化部分功能体验，解决了一些已知问题。",
      type: "info", enabled: true, created_at: "2026-10-25 09:00:00", updated_at: "2026-10-25 09:00:00",
    })),
    events: [] as object[],
  };
  await page.routeWebSocket("**/api/feedback/ws", (socket) => socket.close());
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/login" || path === "/api/auth/me") {
      await route.fulfill({ json: { ok: true, role: "admin", subject_id: state.owner, username: "admin", name: "测试管理员", token: "mock-token" } });
    } else if (path === "/api/navigation") {
      await route.fulfill({ json: { items: [{ id: "image", visible: true }] } });
    } else if (path === "/api/system/announcements") {
      await route.fulfill(state.failLoad
        ? { status: 503, json: { detail: "暂时无法同步公告" } }
        : { json: { items: state.items.filter((item) => item.enabled), total: state.items.length } });
    } else if (path === "/api/events/stream") {
      await route.fulfill({ contentType: "text/event-stream", body: state.events.splice(0).map((item) => `event: announcement\ndata: ${JSON.stringify(item)}\n\n`).join("") || ": heartbeat\n\n" });
    } else if (path.startsWith("/api/") || ["xhr", "fetch"].includes(route.request().resourceType())) {
      await route.fulfill({ json: path.endsWith("/count") ? { count: 0 } : {} });
    } else {
      await route.continue();
    }
  });
  await login(page);
  await page.goto("/system-management");
  return state;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByTestId("login-username").click();
  await page.getByTestId("login-username").fill("admin");
  await page.getByTestId("login-password").click();
  await page.getByTestId("login-password").fill("mock-password");
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(/\/image$/);
}

test("公告数字角标固定在铃铛右上角，阅读后刷新不重复提醒", async ({ page }) => {
  const state = await setup(page);
  const bell = page.getByRole("button", { name: "通知", exact: true });
  const badge = page.getByTestId("announcement-unread-count");
  await expect(badge).toHaveText("2");
  const checkPosition = async () => {
    const buttonBox = (await bell.boundingBox())!;
    const badgeBox = (await badge.boundingBox())!;
    expect(badgeBox.width).toBeGreaterThanOrEqual(20);
    expect(badgeBox.x).toBeGreaterThan(buttonBox.x + buttonBox.width / 2);
    expect(Math.abs(badgeBox.x + badgeBox.width - buttonBox.x - buttonBox.width)).toBeLessThanOrEqual(5);
    expect(Math.abs(badgeBox.y - buttonBox.y)).toBeLessThanOrEqual(5);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  };
  await checkPosition();
  await page.screenshot({ path: "test-results/announcement-badge-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await checkPosition();
  await page.getByRole("button", { name: "切换主题", exact: true }).click();
  await page.screenshot({ path: "test-results/announcement-badge-mobile-dark.png" });
  await bell.click();
  await expect(badge).toHaveCount(0);
  await bell.click();
  await page.reload();
  await expect(page.getByRole("heading", { name: "系统管理", exact: true })).toBeVisible();
  await expect(badge).toHaveCount(0);

  state.items[0]!.updated_at = "2026-10-25 10:00:00";
  state.events.push({ ...state.items[0]! }, { ...state.items[0]! });
  await expect(badge).toHaveText("1", { timeout: 10_000 });
  state.items[0]!.enabled = false;
  state.events.push({ id: 1, enabled: false });
  await expect(badge).toHaveCount(0, { timeout: 10_000 });
});

test("轮询发现新公告、失败保留未读数，已读状态按用户隔离", async ({ page }) => {
  await page.clock.install();
  const state = await setup(page);
  const bell = page.getByRole("button", { name: "通知", exact: true });
  const badge = page.getByTestId("announcement-unread-count");
  await expect(badge).toHaveText("2");
  await bell.click();
  await expect(badge).toHaveCount(0);
  await bell.click();
  state.items.push({ ...state.items[0]!, id: 3, title: "新公告", updated_at: "2026-10-25 11:00:00" });
  await page.clock.runFor(10_100);
  await expect(badge).toHaveText("1");
  state.failLoad = true;
  await page.clock.runFor(10_100);
  await expect(badge).toHaveText("1");
  state.failLoad = false;
  await page.getByRole("button", { name: "用户菜单", exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  state.owner = "another-admin";
  await login(page);
  await expect(badge).toHaveText("3");
});
