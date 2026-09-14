import { expect, test } from "@playwright/test";

const success = data => ({ success: true, data, request_id: "plane-scope-e2e" });

test("Plane 对话更宽且画布节点可作为实际运行终点", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 560 });
  let createBody = null;
  const taskUuid = "00000000-0000-4000-8000-000000000077";
  const product = { product_id: 21, sku: "HF-A0396-1", name: "电动躺椅", category_code: "recliner", current_profile_version_id: 8, attributes: [] };
  const dataset = { dataset_id: 31, name: "美国授权市场数据", market_country: "US", platform: "amazon", category_code: "recliner", status: "ready" };
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "plane-user-token");
    localStorage.setItem("furniscope-workflow-pane-sizes", JSON.stringify({ history: 180, chat: 250, preview: 420 }));
  });
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path.endsWith("/events")) {
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: `data: ${JSON.stringify({ task_uuid: taskUuid, status: "partial_succeeded", stage: "researching_market", progress_percent: 65, report_uuid: null })}\n\n` });
      return;
    }
    let data = { items: [] };
    if (path === "/api/v1/users/me") data = { user_id: 7, name: "分析员", email: "plane@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    else if (path === "/api/v1/products" && method === "GET") data = { items: [product], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/products/21") data = product;
    else if (path === "/api/v1/market-datasets") data = { items: [dataset], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/analysis-workspaces" && method === "POST") {
      const body = request.postDataJSON() || {};
      data = {
        workspace_uuid: body.workspace_uuid, job_name: body.name, source: body.source || "node_workflow_canvas",
        workspace_status: "active", status: "draft", analysis_count: 0,
        created_at: "2026-09-14T08:00:00Z", updated_at: "2026-09-14T08:00:00Z",
      };
    } else if (path === "/api/v1/analysis-workspaces") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path.includes("/analysis-workspaces/") && path.endsWith("/messages") && method === "POST") {
      data = { message_uuid: "40000000-0000-4000-8000-000000000088", role: "assistant", message_kind: "text", content: "ok", seq_no: 1, created_at: "2026-09-14T08:00:00Z" };
    } else if (path.includes("/analysis-workspaces/") && path.includes("/messages")) {
      data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    }
    else if (path === "/api/v1/analysis-tasks" && method === "GET") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/analysis-tasks" && method === "POST") {
      createBody = request.postDataJSON();
      data = { task_uuid: taskUuid, job_name: "电动躺椅出海分析", status: "draft", stage: "understanding_product", progress_percent: 0, report_uuid: null };
    } else if (path.endsWith(":start")) data = { task_uuid: taskUuid, status: "queued", stage: "understanding_product", progress_percent: 0, checkpoint_stage: null, retryable: false };
    else if (path.includes(`/analysis-tasks/${taskUuid}/`)) data = { items: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#workflow?product=21");
  await expect(page.getByText("AI 分析对话", { exact: true })).toBeVisible();
  await expect(page.locator(".bound-product").getByText("电动躺椅", { exact: true })).toBeVisible();
  await expect(page.getByText("分析对象", { exact: true })).toBeVisible();
  await expect(page.locator(".conversation-context-setup.collapsed")).toBeVisible();
  await page.getByRole("button", { name: "展开分析对象" }).click();
  await expect(page.getByLabel("对话分析产品")).toHaveValue("21");
  await expect(page.getByLabel("对话市场数据集")).toHaveValue("31");
  await page.getByRole("button", { name: "收起分析对象" }).click();
  await expect(page.getByRole("button", { name: "展开分析对象" })).toBeVisible();
  await page.getByRole("button", { name: "展开建议下一步" }).click();
  const guidedPrompt = page.getByRole("button", { name: /分析电动躺椅在美国的竞品、价格带和用户评论痛点/ });
  await expect(guidedPrompt).toBeVisible();
  await page.screenshot({ path: "design-qa-ai-chat-guidance.png", fullPage: true });
  await guidedPrompt.click();
  await expect(page.getByLabel("分析问题")).toHaveValue("分析电动躺椅在美国的竞品、价格带和用户评论痛点");
  const chatWidth = (await page.locator(".workflow-active-chat-pane").boundingBox()).width;
  const canvasWidth = (await page.locator(".node-canvas").boundingBox()).width;
  expect(chatWidth).toBeGreaterThanOrEqual(330);
  expect(chatWidth).toBeGreaterThan(canvasWidth);
  const chatFont = await page.locator(".vertical-chat-composer textarea").evaluate(element => parseFloat(getComputedStyle(element).fontSize));
  const shortcutFont = await page.locator(".conversation-next-guide > div button").first().evaluate(element => parseFloat(getComputedStyle(element).fontSize));
  expect(chatFont).toBeGreaterThanOrEqual(11);
  expect(shortcutFont).toBeGreaterThanOrEqual(10);

  const marketNode = page.locator(".workflow-node").filter({ hasText: "市场研究" });
  await page.waitForTimeout(100);
  const nodeBefore = await marketNode.boundingBox();
  const canvasBefore = await page.locator(".node-canvas").evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }));
  await marketNode.click();
  const nodeAfter = await marketNode.boundingBox();
  const canvasAfter = await page.locator(".node-canvas").evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }));
  expect(nodeAfter.x).toBe(nodeBefore.x);
  expect(nodeAfter.y).toBe(nodeBefore.y);
  expect(canvasAfter).toEqual(canvasBefore);
  await expect(page.getByLabel("运行范围")).toHaveValue("market");
  await expect(page.locator(".workflow-node.target")).toContainText("市场研究");
  const detailScroll = page.locator(".node-detail-scroll");
  await expect.poll(() => detailScroll.evaluate(element => getComputedStyle(element).overflowY)).toBe("auto");
  await detailScroll.evaluate(element => { element.scrollTop = 80; });
  await expect.poll(() => detailScroll.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "运行至市场研究" }).click();
  await expect.poll(() => createBody?.analysis_config?.target_node).toBe("market");
  expect(createBody.analysis_config.workspace_uuid).toMatch(/^[0-9a-f-]{36}$/);
  expect(createBody.analysis_config.include_forecast).toBe(false);
  await expect(page.getByText("已运行至目标", { exact: true }).first()).toBeVisible();
  if (await page.getByRole("button", { name: "收起建议下一步" }).count()) {
    await page.getByRole("button", { name: "收起建议下一步" }).click();
  }
  await expect(page.getByText(/已完成到“市场研究”的指定范围/)).toBeVisible();
  await expect(page.getByText("建议下一步", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "展开建议下一步" }).click();
  const continueScore = page.getByRole("button", { name: /继续评估电动躺椅的市场机会并给出五维评分/ });
  await expect(continueScore).toBeVisible();
  await continueScore.click();
  await expect(page.getByLabel("分析问题")).toHaveValue("继续评估电动躺椅的市场机会并给出五维评分");
});

test("最近记录按分析工作台聚合而不是按分析次数重复", async ({ page }) => {
  const workspaceUuid = "10000000-0000-4000-8000-000000000001";
  const base = {
    job_name: "电动躺椅美国市场", job_type: "product_market_fit", product_id: 21,
    product_sku: "HF-A0396-1", product_name: "电动躺椅", target_country: "US",
    target_platform: "amazon", source: "node_workflow_canvas", workspace_uuid: workspaceUuid,
    stage: "completed", progress_percent: 100, report_uuid: null,
  };
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "workbench-user-token"));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [] };
    if (path === "/api/v1/users/me") data = { user_id: 7, name: "分析员", email: "user@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    if (path === "/api/v1/analysis-workspaces") data = { items: [{
      ...base, workspace_status: "active", analysis_count: 2, status: "partial_succeeded",
      updated_at: "2026-09-14T06:00:00Z", created_at: "2026-09-14T04:00:00Z",
    }], total: 1, page: 1, page_size: 100, total_pages: 1 };
    if (path === "/api/v1/analysis-tasks") data = { items: [
      { ...base, task_uuid: "20000000-0000-4000-8000-000000000001", status: "succeeded", updated_at: "2026-09-14T05:00:00Z", created_at: "2026-09-14T04:00:00Z" },
      { ...base, task_uuid: "20000000-0000-4000-8000-000000000002", status: "partial_succeeded", stage: "researching_market", progress_percent: 65, updated_at: "2026-09-14T06:00:00Z", created_at: "2026-09-14T05:30:00Z" },
    ], total: 2, page: 1, page_size: 100, total_pages: 1 };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });
  await page.goto("/#analysis");
  await expect(page.getByRole("heading", { name: "工作日记" })).toBeVisible();
  await expect(page.locator(".workbench-list > article")).toHaveCount(1);
  await expect(page.locator(".workbench-list > article")).toContainText("2 次");
  await expect(page.getByText("Plane", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: "更多" }).click();
  await expect(page.getByRole("heading", { name: "工作日记" })).toBeVisible();
  await expect(page.getByLabel("搜索工作日记")).toBeVisible();
  await expect(page.getByRole("button", { name: "删除" })).toBeVisible();
});
