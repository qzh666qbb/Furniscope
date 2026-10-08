import { expect, test } from "@playwright/test";

const success = data => ({ success: true, data, request_id: "plane-scope-e2e" });

function chatStreamBody(data, thinking = "1. 核对企业产品目录。\n2. 核对授权市场数据。") {
  const suggestedActions = [
    ...(data.suggested_actions || []),
    ...(data.suggested_action ? [{
      action: data.suggested_action,
      label: data.action_label,
      href: data.action_href,
    }] : []),
    ...(data.suggested_prompts || []).map((value) => ({ action: "send_prompt", label: value, value })),
  ];
  const payload = {
    turn_uuid: "50000000-0000-4000-8000-000000000001",
    user_message_uuid: "50000000-0000-4000-8000-000000000002",
    assistant_message_uuid: "50000000-0000-4000-8000-000000000003",
    context_snapshot_uuid: "50000000-0000-4000-8000-000000000004",
    answer: data.answer,
    citations: data.citations || [],
    memory_candidates: data.memory_candidates || [],
    context_sources: [],
    suggested_actions: suggestedActions,
  };
  return [
    `event: turn_started\ndata: ${JSON.stringify({ turn_uuid: "50000000-0000-4000-8000-000000000001" })}\n\n`,
    `event: progress\ndata: ${JSON.stringify({ stage: "context_ready", message: thinking })}\n\n`,
    `event: answer_delta\ndata: ${JSON.stringify({ delta: data.answer })}\n\n`,
    ...(data.citations || []).map((item) => `event: citation\ndata: ${JSON.stringify(item)}\n\n`),
    ...(data.memory_candidates || []).map((item) => `event: memory_candidate\ndata: ${JSON.stringify(item)}\n\n`),
    ...suggestedActions.map((item) => `event: action\ndata: ${JSON.stringify(item)}\n\n`),
    `event: done\ndata: ${JSON.stringify(payload)}\n\n`,
  ].join("");
}

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
  await expect.poll(async () => page.locator(".vertical-chat-messages > .chat-turn").evaluateAll((nodes) => nodes.filter((node) => !node.textContent.trim()).length)).toBe(0);
  await expect(page.locator(".chat-scroll-end")).toHaveCSS("height", "0px");
  await expect(page.locator(".bound-product").getByText("电动躺椅", { exact: true })).toBeVisible();
  await expect(page.locator(".workflow-conversation-history-pane").getByText("分析对象", { exact: true })).toBeVisible();
  await expect(page.locator(".workflow-active-chat-pane").getByText("分析对象", { exact: true })).toHaveCount(0);
  await expect(page.locator(".conversation-context-setup.collapsed")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "展开分析对象" })).toHaveCount(0);
  await expect(page.getByLabel("对话分析产品")).toHaveValue("21");
  await expect(page.getByLabel("对话市场数据集")).toHaveValue("31");
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
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
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
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

test("从工作台新建分析不带入已有会话，对话后仍只显示当前工作台", async ({ page }) => {
  const existing = {
    workspace_uuid: "10000000-0000-4000-8000-000000000001",
    job_name: "扶手椅 HF-B0142 出海机会分析",
    source: "node_workflow_canvas",
    workspace_status: "active",
    status: "succeeded",
    analysis_count: 1,
    product_sku: "HF-B0142",
    product_name: "扶手椅",
    target_country: "US",
    target_platform: "amazon",
    task_uuid: "20000000-0000-4000-8000-000000000001",
    report_uuid: "30000000-0000-4000-8000-000000000001",
    created_at: "2026-09-14T04:00:00Z",
    updated_at: "2026-09-14T06:00:00Z",
  };
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "new-workspace-token"));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [] };
    if (path === "/api/v1/users/me") data = { user_id: 7, name: "分析员", email: "user@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    else if (path === "/api/v1/products" && route.request().method() === "GET") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/market-datasets") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path.endsWith("/turns:stream")) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: chatStreamBody({
          answer: "当前没有美国市场的已授权数据集，也还没有匹配到电竞椅。请先到市场洞察补数，或告诉我产品 SKU。",
          title: "电竞椅 出海机会分析",
          suggested_prompts: ["打开市场洞察导入美国市场数据"],
          suggested_action: "insights",
          action_href: "insights",
          action_label: "去市场洞察补数据",
          product_candidates: [],
        }),
      });
      return;
    }
    else if (path.endsWith("/analysis-workspaces/chat")) data = {
      answer: "当前没有美国市场的已授权数据集，也还没有匹配到电竞椅。请先到市场洞察补数，或告诉我产品 SKU。",
      title: "电竞椅 出海机会分析",
      suggested_prompts: ["打开市场洞察导入美国市场数据"],
      suggested_action: "insights",
      action_href: "insights",
      action_label: "去市场洞察补数据",
      product_candidates: [],
    };
    else if (path === "/api/v1/analysis-workspaces") data = { items: [existing], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/analysis-tasks") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path.includes("/messages")) data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#analysis");
  await page.getByRole("button", { name: "进入分析工作台" }).click();
  await expect(page.getByRole("heading", { name: "新建分析工作台" })).toBeVisible();
  await expect(page.locator(".workbench-launcher-modal").getByText("已有工作台")).toHaveCount(0);
  await expect(page.locator(".workbench-launcher-modal").getByText("HF-B0142")).toHaveCount(0);
  await page.getByRole("button", { name: "新建并进入" }).click();
  await expect(page.locator(".workflow-conversation-history-pane").getByText("分析对象", { exact: true })).toBeVisible();
  await expect(page.locator(".workflow-active-chat-pane").getByText("分析对象", { exact: true })).toHaveCount(0);
  await expect(page.getByText("从工作台新建时需要先选择产品")).toHaveCount(0);
  await expect(page.getByText("请选择要分析的产品", { exact: true })).toHaveCount(0);
  await expect(page.getByText("待选择", { exact: true })).toBeVisible();
  await expect(page.locator(".conversation-context-setup.collapsed")).toHaveCount(0);
  await expect(page.getByLabel("对话分析产品")).toBeVisible();
  await expect(page.getByText("可不填")).toHaveCount(0);
  await expect(page.locator(".conversation-history")).not.toContainText("HF-B0142");
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "打开已有工作日记" })).toBeVisible();
  await page.getByLabel("分析问题").fill("我想分析一下电竞椅的市场");
  await page.getByRole("button", { name: "发送并执行" }).click();
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
  await expect(page.locator(".conversation-history")).not.toContainText("HF-B0142");
  await expect(page.getByText("4 个工作台")).toHaveCount(0);
});

test("空白工作台输入电竞椅会列出相近座椅并给出可开跑建议", async ({ page }) => {
  const workspaceUuid = "10000000-0000-4000-8000-0000000000aa";
  const taskUuid = "00000000-0000-4000-8000-000000000078";
  const knowledgeBaseUuid = "60000000-0000-4000-8000-000000000001";
  const citationUuid = "70000000-0000-4000-8000-000000000001";
  const candidateMemoryUuid = "80000000-0000-4000-8000-000000000001";
  const forgetMemoryUuid = "80000000-0000-4000-8000-000000000002";
  let createBody = null;
  let contextBody = null;
  const memoryActions = [];
  const chairs = [
    { product_id: 11, sku: "HF-B0142", name: "扶手椅", category_code: "chair", current_profile_version_id: 3, analysis_status: "ready", attributes: [] },
    { product_id: 12, sku: "CHAIR-9b3c4f61fd", name: "Oak lounge chair", category_code: "chair", current_profile_version_id: 4, analysis_status: "ready", attributes: [] },
    { product_id: 13, sku: "HF-A0101", name: "休闲椅", category_code: "chair", current_profile_version_id: 5, analysis_status: "ready", attributes: [] },
  ];
  const desk = { product_id: 14, sku: "HF-D0001", name: "办公桌", category_code: "desk", current_profile_version_id: 6, analysis_status: "ready", attributes: [] };
  const dataset = { dataset_id: 31, name: "美国授权市场数据", market_country: "US", platform: "amazon", category_code: "chair", status: "ready" };
  const existing = {
    workspace_uuid: "10000000-0000-4000-8000-000000000001",
    job_name: "扶手椅 HF-B0142 出海机会分析",
    source: "node_workflow_canvas",
    workspace_status: "active",
    status: "succeeded",
    analysis_count: 1,
    product_sku: "HF-B0142",
    product_name: "扶手椅",
    task_uuid: "20000000-0000-4000-8000-000000000001",
    created_at: "2026-09-14T04:00:00Z",
    updated_at: "2026-09-14T06:00:00Z",
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "gaming-chair-token");
    localStorage.setItem("furniscope-plane-chats-v2", JSON.stringify({
      "10000000-0000-4000-8000-000000000001": [
        { role: "user", text: "分析扶手椅", kind: "text" },
        { role: "assistant", text: "这是分析工作台「扶手椅 HF-B0142 出海机会分析」。", kind: "text" },
      ],
    }));
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
    else if (path === "/api/v1/products" && method === "GET") data = { items: [...chairs, desk], total: 4, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/products/11") data = chairs[0];
    else if (path === "/api/v1/market-datasets") data = { items: [dataset], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/knowledge-bases") data = {
      items: [{
        knowledge_base_uuid: knowledgeBaseUuid,
        name: "德国市场合规资料",
        description: "企业合规文档",
        status: "active",
        document_count: 2,
        ready_document_count: 2,
        created_at: "2026-09-14T08:00:00Z",
        updated_at: "2026-09-14T08:00:00Z",
      }],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    };
    else if (path.endsWith("/context") && method === "GET") data = {
      workspace_uuid: workspaceUuid,
      revision: 1,
      product_id: null,
      dataset_ids: [],
      knowledge_base_uuids: [],
      memory_scope: ["workspace", "user"],
      task_uuids: [],
      retrieval_policy: {},
    };
    else if (path.endsWith("/context") && method === "PUT") {
      contextBody = request.postDataJSON();
      data = { workspace_uuid: workspaceUuid, revision: 2, ...contextBody };
    }
    else if (path.endsWith("/context:preview") && method === "POST") data = {
      workspace_uuid: workspaceUuid,
      estimated_tokens: 1840,
      resolved_state: {
        current_product_label: "HF-B0142 · 扶手椅",
        current_market: "US",
      },
      sources: [
        {
          type: "product_profile",
          source_id: "profile:3",
          label: "HF-B0142 产品画像",
          priority: 90,
          trust_level: "confirmed",
        },
        {
          type: "knowledge_document",
          source_id: "document:policy-1",
          label: "德国市场合规要求.pdf",
          priority: 60,
          trust_level: "untrusted",
        },
      ],
      conflicts: [{
        field: "current_market",
        resolution: "本轮明确的美国市场覆盖长期记忆中的德国市场。",
      }],
      truncated_sources: [],
    };
    else if (path === "/api/v1/customer-memories" && method === "GET") data = {
      items: [
        {
          memory_uuid: candidateMemoryUuid,
          memory_type: "unit_cost_limit",
          value: { amount: 50, currency: "USD", operator: "lte" },
          scope: "workspace",
          status: "candidate",
          confidence: 0.92,
          source_message_uuid: "50000000-0000-4000-8000-000000000002",
          supersedes_memory_uuid: forgetMemoryUuid,
          effective_at: "2026-09-14T08:00:00Z",
          expires_at: "2027-09-14T08:00:00Z",
          created_at: "2026-09-14T08:00:00Z",
        },
        {
          memory_uuid: forgetMemoryUuid,
          memory_type: "target_market",
          value: { value: "德国" },
          scope: "user",
          status: "confirmed",
          confidence: 1,
          effective_at: "2026-09-01T08:00:00Z",
          expires_at: "2027-09-01T08:00:00Z",
          created_at: "2026-09-01T08:00:00Z",
        },
      ],
      total: 2,
      page: 1,
      page_size: 100,
      total_pages: 1,
    };
    else if (path === `/api/v1/customer-memories/${candidateMemoryUuid}/history`) data = [
      {
        memory_uuid: candidateMemoryUuid,
        value: { amount: 50, currency: "USD", operator: "lte" },
        status: "candidate",
      },
      {
        memory_uuid: forgetMemoryUuid,
        value: { amount: 40, currency: "USD", operator: "lte" },
        status: "superseded",
      },
    ];
    else if (path.endsWith("/turns:stream")) {
      if ((request.postDataJSON()?.question || "").includes("分析扶手椅")) {
        await route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body: chatStreamBody({
            answer: "已识别为分析工作流请求，将运行至市场研究节点。",
            suggested_actions: [{
              action: "run_workflow",
              label: "运行分析",
              target_node: "market",
              product_id: 11,
              dataset_id: 31,
            }],
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: chatStreamBody({
          answer: "目录里没有完全叫「电竞椅」的产品。下面是相近的已建档产品。\n1. 扶手椅（HF-B0142）\n2. Oak lounge chair（CHAIR-9b3c4f61fd）\n3. 休闲椅（HF-A0101）\n已找到美国市场数据集「美国授权市场数据」。",
          title: "电竞椅 出海机会分析",
          suggested_prompts: ["分析扶手椅（HF-B0142）在美国的竞品、价格带和用户评论痛点"],
          product_candidates: [{ product_id: 11, sku: "HF-B0142", name: "扶手椅" }],
          citations: [{
            citation_uuid: citationUuid,
            source_type: "knowledge_document",
            source_id: "document:policy-1",
            source_version: 1,
            label: "德国市场合规要求.pdf",
            locator: { page: 12 },
          }],
          memory_candidates: [{
            memory_uuid: candidateMemoryUuid,
            memory_type: "unit_cost_limit",
            value: { amount: 50, currency: "USD", operator: "lte" },
            scope: "workspace",
            status: "candidate",
            confidence: 0.92,
            source_message_uuid: "50000000-0000-4000-8000-000000000002",
            supersedes_memory_uuid: forgetMemoryUuid,
            created_at: "2026-09-14T08:00:00Z",
          }],
          suggested_actions: [{
            action: "forget_memory",
            requires_confirmation: true,
            targets: [{ memory_uuid: forgetMemoryUuid, label: "单位成本不超过 40 USD" }],
          }],
        }),
      });
      return;
    }
    else if (path === `/api/v1/citations/${citationUuid}`) data = {
      citation_uuid: citationUuid,
      source_type: "knowledge_document",
      source_id: "document:policy-1",
      source_version: 1,
      label: "德国市场合规要求.pdf",
      locator: { page: 12 },
      excerpt: "德国软体家具进入渠道前需要核对阻燃与标签要求。",
    };
    else if (path.startsWith("/api/v1/customer-memories/")) {
      memoryActions.push({ method, path });
      const confirmed = path.endsWith(":confirm");
      data = {
        memory_uuid: confirmed ? candidateMemoryUuid : forgetMemoryUuid,
        memory_type: "unit_cost_limit",
        value: { amount: confirmed ? 50 : 40, currency: "USD", operator: "lte" },
        scope: "workspace",
        status: confirmed ? "confirmed" : "archived",
        confidence: 0.92,
        source_message_uuid: "50000000-0000-4000-8000-000000000002",
        supersedes_memory_uuid: confirmed ? forgetMemoryUuid : null,
        created_at: "2026-09-14T08:00:00Z",
      };
    }
    else if (path.endsWith("/analysis-workspaces/chat") && method === "POST") data = {
      answer: "目录里没有完全叫「电竞椅」的产品。下面是相近的已建档产品。\n1. 扶手椅（HF-B0142）\n2. Oak lounge chair（CHAIR-9b3c4f61fd）\n3. 休闲椅（HF-A0101）\n已找到美国市场数据集「美国授权市场数据」。",
      title: "电竞椅 出海机会分析",
      suggested_prompts: ["分析扶手椅（HF-B0142）在美国的竞品、价格带和用户评论痛点"],
      product_candidates: [{ product_id: 11, sku: "HF-B0142", name: "扶手椅" }],
    };
    else if (path === "/api/v1/analysis-workspaces" && method === "POST") {
      const body = request.postDataJSON() || {};
      data = {
        workspace_uuid: body.workspace_uuid, job_name: body.name, source: body.source || "node_workflow_canvas",
        workspace_status: "active", status: "draft", analysis_count: 0,
        created_at: "2026-09-14T08:00:00Z", updated_at: "2026-09-14T08:00:00Z",
      };
    } else if (path === "/api/v1/analysis-workspaces") data = { items: [existing], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path.includes("/analysis-workspaces/") && path.endsWith("/messages") && method === "POST") {
      data = { message_uuid: "40000000-0000-4000-8000-000000000089", role: "assistant", message_kind: "text", content: "ok", seq_no: 1, created_at: "2026-09-14T08:00:00Z" };
    } else if (path.includes("/analysis-workspaces/") && path.includes("/messages")) {
      data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    } else if (path === "/api/v1/analysis-tasks" && method === "GET") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/analysis-tasks" && method === "POST") {
      createBody = request.postDataJSON();
      data = { task_uuid: taskUuid, job_name: "扶手椅出海分析", status: "draft", stage: "understanding_product", progress_percent: 0, report_uuid: null };
    } else if (path.endsWith(":start")) data = { task_uuid: taskUuid, status: "queued", stage: "understanding_product", progress_percent: 0, checkpoint_stage: null, retryable: false };
    else if (path.includes(`/analysis-tasks/${taskUuid}/`)) data = { items: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto(`/#workflow?new=1&workspace=${workspaceUuid}`);
  await expect(page.locator(".workflow-conversation-history-pane").getByText("分析对象", { exact: true })).toBeVisible();
  await expect(page.locator(".workflow-active-chat-pane").getByText("分析对象", { exact: true })).toHaveCount(0);
  await expect(page.getByText("请选择要分析的产品", { exact: true })).toHaveCount(0);
  await expect(page.getByText("当前还没有分析任务")).toHaveCount(0);
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
  await expect(page.locator(".conversation-history")).not.toContainText("扶手椅 HF-B0142 出海机会分析");
  await expect(page.getByRole("group", { name: "本轮记忆范围" })).toHaveCount(0);
  await page.getByTitle("查看已记住内容与本轮上下文").click();
  await expect(page.getByRole("complementary", { name: "工作台记忆与上下文" })).toBeVisible();
  await expect(page.getByText("单位成本上限", { exact: true })).toBeVisible();
  await expect(page.getByText("目标市场", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "版本历史" }).first().click();
  await expect(page.getByText("superseded", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "预览本轮上下文" }).click();
  await expect(page.getByText("1840 tokens", { exact: true })).toBeVisible();
  await expect(page.getByText(/本轮明确的美国市场覆盖长期记忆/)).toBeVisible();
  await page.screenshot({ path: "../artifacts/context-lifecycle-20261006/memory-drawer-desktop.png", fullPage: true });
  await page.getByTitle("关闭").click();
  await page.locator(".conversation-knowledge-picker summary").click();
  await page.getByRole("checkbox", { name: /德国市场合规资料/ }).check();
  await page.getByLabel("分析问题").fill("我想分析一下电竞椅的市场");
  await page.getByRole("button", { name: "发送并执行" }).click();
  await expect.poll(() => contextBody?.knowledge_base_uuids).toEqual([knowledgeBaseUuid]);
  await expect(page.getByText(/当前还没有分析任务/)).toHaveCount(0);
  await expect(page.getByText(/目录里没有完全叫「电竞椅」/)).toBeVisible();
  await expect(page.locator(".chat-thinking")).toHaveCount(1);
  await expect(page.locator(".chat-thinking")).not.toHaveAttribute("open");
  await expect(page.locator(".chat-thinking summary")).toHaveText("执行摘要");
  await expect(page.getByRole("button", { name: /德国市场合规要求.pdf/ })).toBeVisible();
  await expect(page.getByTitle("确认记忆")).toBeVisible();
  await expect(page.getByRole("button", { name: /单位成本不超过 40 USD/ })).toBeVisible();
  await page.screenshot({ path: "../artifacts/context-lifecycle-20261006/chat-memory-citation-desktop.png", fullPage: true });
  await page.getByRole("button", { name: /德国市场合规要求.pdf/ }).click();
  await expect(page.getByText(/需要核对阻燃与标签要求/)).toBeVisible();
  await page.getByTitle("确认记忆").click();
  await expect(page.getByText("已确认", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /单位成本不超过 40 USD/ }).click();
  await expect.poll(() => memoryActions).toEqual([
    { method: "POST", path: `/api/v1/customer-memories/${candidateMemoryUuid}:confirm` },
    { method: "DELETE", path: `/api/v1/customer-memories/${forgetMemoryUuid}` },
  ]);
  await expect(page.locator(".vertical-chat-messages")).toContainText("扶手椅");
  await expect(page.locator(".vertical-chat-messages")).toContainText("休闲椅");
  await expect(page.locator(".vertical-chat-messages")).not.toContainText("办公桌");
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
  await expect(page.locator(".conversation-history")).not.toContainText("扶手椅 HF-B0142 出海机会分析");
  expect(createBody).toBeNull();
  const chairPrompt = page.getByRole("button", { name: /分析扶手椅（HF-B0142）在美国的竞品/ });
  await expect(chairPrompt).toBeVisible();
  await chairPrompt.click();
  await expect(page.getByLabel("分析问题")).toHaveValue("分析扶手椅（HF-B0142）在美国的竞品、价格带和用户评论痛点");
  await page.getByRole("button", { name: "发送并执行" }).click();
  await expect.poll(() => createBody?.product_id).toBe(11);
  expect(createBody.analysis_config.target_node).toBe("market");
  await expect(page.getByText(/工作流已启动并运行至“市场研究”/)).toBeVisible();
  await expect(page.locator(".conversation-history > button")).toHaveCount(1);
  await expect(page.getByText("4 个工作台")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
  await page.screenshot({ path: "../artifacts/context-lifecycle-20261006/chat-memory-citation-mobile.png", fullPage: true });
  await page.getByTitle("查看已记住内容与本轮上下文").click();
  await expect(page.getByRole("complementary", { name: "工作台记忆与上下文" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
  await page.screenshot({ path: "../artifacts/context-lifecycle-20261006/memory-drawer-mobile.png", fullPage: false });
});
