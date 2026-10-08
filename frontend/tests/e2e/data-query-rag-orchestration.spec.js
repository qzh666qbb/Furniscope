import { expect, test } from "@playwright/test";

const success = data => ({ success: true, data, request_id: "tool-orchestration-e2e" });

function turnStream(question) {
  const isQuery = question.includes("销量");
  const action = {
    action: "run_workflow",
    label: "运行分析",
    target_node: "market",
    product_id: 21,
    dataset_id: 31,
  };
  const queryResult = {
    tool: "data_query",
    status: "succeeded",
    data: {
      query_uuid: "71000000-0000-4000-8000-000000000001",
      plan: { metrics: ["sales_units"], grain: "daily", group_by: [], filters: {}, limit: 100 },
      resolved_filters: { date_from: "2025-01-01", date_to: "2025-01-03", skus: ["HF-A0396-1"], sites: ["US"], statuses: [] },
      source_version: {
        version_uuid: "72000000-0000-4000-8000-000000000001",
        canonical_sha256: "a".repeat(64),
        source_kind: "sales",
        filename: "sales.json",
        confirmed_at: "2026-10-06T08:00:00Z",
      },
      columns: [
        { key: "period", label: "期间", type: "dimension" },
        { key: "sales_units", label: "销量", type: "metric", unit: "件" },
      ],
      rows: [
        { period: "2025-01-01", sales_units: 10 },
        { period: "2025-01-02", sales_units: 20 },
        { period: "2025-01-03", sales_units: 30 },
      ],
      row_count: 3,
      limited: false,
      limitations: [],
      result_sha256: "b".repeat(64),
    },
  };
  const payload = {
    turn_uuid: "70000000-0000-4000-8000-000000000001",
    user_message_uuid: "70000000-0000-4000-8000-000000000002",
    assistant_message_uuid: "70000000-0000-4000-8000-000000000003",
    context_snapshot_uuid: "70000000-0000-4000-8000-000000000004",
    answer: isQuery ? "最近三天销量分别为 10、20、30 件。" : "已识别为分析工作流请求。",
    citations: isQuery ? [{
      citation_uuid: "73000000-0000-4000-8000-000000000001",
      source_type: "data_query",
      source_id: "query:71000000-0000-4000-8000-000000000001",
      source_version: "a".repeat(64),
      label: "sales.json · 72000000",
      locator: { query_uuid: "71000000-0000-4000-8000-000000000001" },
      excerpt: "最近三天销量分别为 10、20、30 件。",
      score: 1,
    }] : [],
    memory_candidates: [],
    context_sources: [],
    suggested_actions: isQuery ? [] : [action],
    tool_results: isQuery ? [queryResult] : [],
  };
  return [
    `event: turn_started\ndata: ${JSON.stringify({ turn_uuid: payload.turn_uuid })}\n\n`,
    `event: progress\ndata: ${JSON.stringify({ stage: isQuery ? "data_query_completed" : "workflow_routed", message: isQuery ? "已执行受控指标" : "已路由到工作流" })}\n\n`,
    `event: answer_delta\ndata: ${JSON.stringify({ delta: payload.answer })}\n\n`,
    ...(isQuery ? [
      `event: citation\ndata: ${JSON.stringify(payload.citations[0])}\n\n`,
      `event: tool_result\ndata: ${JSON.stringify(queryResult)}\n\n`,
    ] : [
      `event: action\ndata: ${JSON.stringify(action)}\n\n`,
    ]),
    `event: done\ndata: ${JSON.stringify(payload)}\n\n`,
  ].join("");
}

test("问数先走服务端工具，工作流仅按服务端动作启动", async ({ page }) => {
  const product = { product_id: 21, sku: "HF-A0396-1", name: "电动躺椅", category_code: "recliner", current_profile_version_id: 8, attributes: [] };
  const dataset = { dataset_id: 31, name: "美国授权市场数据", market_country: "US", platform: "amazon", category_code: "recliner", status: "ready" };
  const taskUuid = "74000000-0000-4000-8000-000000000001";
  let taskCreates = 0;
  let turnRequests = 0;
  const requestOrder = [];
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "tool-user-token"));
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path.endsWith("/turns:stream")) {
      turnRequests += 1;
      requestOrder.push("turn");
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: turnStream(request.postDataJSON().question),
      });
      return;
    }
    if (path.endsWith("/events")) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `data: ${JSON.stringify({ task_uuid: taskUuid, status: "partial_succeeded", stage: "researching_market", progress_percent: 65 })}\n\n`,
      });
      return;
    }
    let data = { items: [] };
    if (path === "/api/v1/users/me") data = { user_id: 7, name: "分析员", email: "tools@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    else if (path === "/api/v1/products" && method === "GET") data = { items: [product], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/products/21") data = product;
    else if (path === "/api/v1/market-datasets") data = { items: [dataset], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/knowledge-bases") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/analysis-workspaces" && method === "POST") {
      const body = request.postDataJSON();
      data = { workspace_uuid: body.workspace_uuid, job_name: body.name, source: body.source, workspace_status: "active", status: "draft", analysis_count: 0, created_at: "2026-10-06T08:00:00Z", updated_at: "2026-10-06T08:00:00Z" };
    }
    else if (path === "/api/v1/analysis-workspaces") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path.endsWith("/context") && method === "GET") data = { revision: 0, product_id: 21, dataset_ids: [31], knowledge_base_uuids: [], memory_scope: ["workspace", "user"], task_uuids: [], retrieval_policy: {} };
    else if (path.endsWith("/context") && method === "PUT") data = { revision: 1, ...request.postDataJSON() };
    else if (path.includes("/customer-memories")) data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path.includes("/messages")) data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/analysis-tasks" && method === "GET") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/analysis-tasks" && method === "POST") {
      taskCreates += 1;
      requestOrder.push("task");
      data = { task_uuid: taskUuid, status: "draft", stage: "understanding_product", progress_percent: 0 };
    }
    else if (path.endsWith(":start")) data = { task_uuid: taskUuid, status: "queued" };
    else if (path.includes(`/analysis-tasks/${taskUuid}/`)) data = { items: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#workflow?product=21");
  await page.getByLabel("分析问题").fill("最近3天按日销量是多少");
  await page.getByRole("button", { name: "发送并执行" }).click();
  await expect(page.getByRole("table")).toContainText("30 件");
  await expect(page.locator(".data-query-bars i")).toHaveCount(3);
  expect(taskCreates).toBe(0);
  expect(turnRequests).toBe(1);

  await page.getByRole("button", { name: "前往知识库管理" }).click();
  await expect(page).toHaveURL(/#knowledge\?/);
  await expect(page.getByRole("heading", { name: "企业知识库", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "返回工作台" }).click();
  await expect(page.getByLabel("分析问题")).toBeVisible();

  await page.getByLabel("分析问题").fill("开始分析电动躺椅的美国市场竞品");
  await page.getByRole("button", { name: "发送并执行" }).click();
  await expect.poll(() => taskCreates).toBe(1);
  expect(turnRequests).toBe(2);
  expect(requestOrder.indexOf("turn")).toBeLessThan(requestOrder.indexOf("task"));
});
