import { expect, test } from "@playwright/test";

for (const mobile of [false, true]) {
  test(`协作模式可确认修改、刷新恢复并停止后台任务 ${mobile ? 'mobile' : 'desktop'}`, async ({ page }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    const conversation = { conversation_id: "team-conversation", title: "商品分析", model: "tt-6-sol", status: "idle", turn_count: 1, created_at: "2026-11-07 10:00:00", updated_at: "2026-11-07 10:00:00" };
    let team: any = null;
    const messages: any[] = [];
    const requests: any[] = [];
    const stops: any[] = [];
    await page.route("**/*", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/auth/login" || path === "/api/auth/me") return route.fulfill({ json: { ok: true, role: "user", subject_id: "alice", username: "alice", name: "Alice", token: "test-only" } });
      if (path === "/api/dsh-agent/status") return route.fulfill({ json: { enabled: true, available: true, configured: true, queue_available: true, teams_enabled: true, model: "tt-6-sol", models: [{ id: "tt-6-sol", label: "GPT 6 Sol" }] } });
      if (path === "/api/dsh-agent/conversations") return route.fulfill({ json: route.request().method() === "POST" ? conversation : { items: [conversation], total: 1 } });
      if (path === "/api/dsh-agent/conversations/team-conversation") return route.fulfill({ json: { conversation, items: messages, team } });
      if (path.endsWith("/team")) return route.fulfill({ json: { team, status: conversation.status } });
      if (path.endsWith("/stop")) {
        stops.push(route.request().postDataJSON());
        conversation.status = "idle";
        team = { ...team, halted: true, tasks: team.tasks.map((task: any) => ({ ...task, status: "cancelled" })) };
        messages.push({ ...messages[1], message_id: "assistant-recovered", content: "后台协作已停止", status: "stopped" });
        return route.fulfill({ json: { accepted: true } });
      }
      if (path.endsWith("/messages/stream")) {
        const body = route.request().postDataJSON();
        requests.push(body);
        if (!team) team = { id: "product", name: "商品协作", phase: "staged", halted: false, revision: "v1", members: [{ name: "商品专家", role: "分析商品" }, { name: "策划专家", role: "梳理购买理由" }], tasks: [{ id: "t1", subject: "检查商品一致性", status: "pending", assignee: "商品专家", dependencies: [] }, { id: "t2", subject: "规划商品主图", status: "pending", assignee: "策划专家", dependencies: ["t1"] }] };
        else if (body.team_action === "approve") team = { ...team, phase: "running", revision: "v2", tasks: team.tasks.map((task: any) => ({ ...task, status: "completed", output: "商品身份约束和方案已确认" })) };
        const user = { message_id: `user-${requests.length}`, role: "user", content: body.content, status: "completed", conversation_id: conversation.conversation_id, model: "tt-6-sol", created_at: conversation.created_at, usage: {} };
        const assistant = { ...user, message_id: `assistant-${requests.length}`, role: "assistant", content: body.team_action ? "任务执行完成" : "已经整理好计划，请确认后执行。", team, total_tokens: 24 };
        messages.push(user, assistant);
        return route.fulfill({ contentType: "text/event-stream", body: [
          { type: "start", conversation_id: conversation.conversation_id, model: "tt-6-sol", user_message_id: user.message_id },
          { type: "team", team }, { type: "delta", text: assistant.content }, { type: "complete", message: assistant },
        ].map(event => `data: ${JSON.stringify(event)}\n\n`).join("") });
      }
      if (path.startsWith("/api/") || ["xhr", "fetch"].includes(route.request().resourceType())) return route.fulfill({ json: { items: [], count: 0 } });
      return route.continue();
    });
    await page.goto("/login");
    await page.getByTestId("login-username").click();
    await page.getByTestId("login-username").fill("alice");
    await page.getByTestId("login-password").click();
    await page.getByTestId("login-password").fill("password123");
    await page.getByTestId("login-submit").click();
    await expect(page).toHaveURL(/\/image$/);
    await page.goto("/ai-agent");
    await page.getByRole("button", { name: "对话模式", exact: true }).click();
    await page.getByRole("option", { name: "多智能体协作", exact: true }).click();
    await page.getByRole("textbox", { name: "发送消息", exact: true }).fill("帮我分析商品并规划主图");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByRole("button", { name: "确认执行", exact: true })).toBeVisible();
    expect(requests[0].mode).toBe("teams");
    expect(requests[0].team_action).toBeUndefined();
    await page.getByRole("button", { name: "修改计划", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "发送消息", exact: true })).toHaveValue("请修改当前协作计划：");
    await page.reload();
    await expect(page.getByRole("region", { name: "协作计划" })).toContainText("商品专家");
    await page.screenshot({ path: testInfo.outputPath(`teams-plan-${mobile ? 'mobile' : 'desktop'}.png`), fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflow).toBe(false);
    await page.getByRole("button", { name: "确认执行", exact: true }).click();
    await expect(page.getByRole("region", { name: "协作计划" })).toContainText("协作结果");
    expect(requests[1].team_action).toBe("approve");
    expect(requests[1].team_revision).toBe("v1");
    await page.getByText("查看结果", { exact: true }).first().click();
    await expect(page.getByRole("region", { name: "协作计划" })).toContainText("商品身份约束和方案已确认");

    conversation.status = "running";
    team = { ...team, tasks: team.tasks.map((task: any) => ({ ...task, status: "in_progress" })) };
    messages.push({ ...messages[0], message_id: "user-recovered", content: "继续完善方案" });
    await page.reload();
    await expect(page.getByRole("region", { name: "协作计划" })).toContainText("成员正在协作");
    await expect(page.locator(".dsh-latest-button .dsh-typing")).toBeVisible();
    await page.getByRole("button", { name: "停止回答", exact: true }).click();
    await expect(page.getByRole("region", { name: "协作计划" })).toContainText("协作已停止", { timeout: 10000 });
    await expect(page.getByText("后台协作已停止", { exact: true })).toBeVisible();
    await expect(page.locator(".dsh-latest-button .dsh-typing")).toHaveCount(0);
    expect(stops).toEqual([{ user_message_id: "user-recovered" }]);
    expect(requests).toHaveLength(2);

    team = { ...team, error: "搜索服务鉴权异常，已停止本轮协作，请管理员检查配置后重新规划。", tasks: [
      { ...team.tasks[0], status: "failed", output: "搜索服务鉴权异常，请管理员检查配置" },
      { ...team.tasks[1], status: "blocked", output: "", blocked_by: ["t1"] },
    ] };
    await page.reload();
    const panel = page.getByRole("region", { name: "协作计划" });
    await expect(panel).toContainText("协作未完成");
    await expect(panel.getByRole("status")).toContainText("已停止本轮协作");
    await expect(panel).toContainText("前置任务失败，未执行");
    await expect(panel).not.toContainText("成员正在协作");
    await expect(panel.getByRole("button", { name: "继续协作", exact: true })).toHaveCount(0);
    await panel.getByText("查看结果", { exact: true }).click();
    await expect(panel).toContainText("搜索服务鉴权异常");
    await page.screenshot({ path: testInfo.outputPath(`teams-blocked-${mobile ? 'mobile' : 'desktop'}.png`), fullPage: true });
  });
}
