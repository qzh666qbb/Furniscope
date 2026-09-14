import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "e2e-contract" });

test("登录到资料解析、授权数据、分析报告及预测入口的前端金路径契约", async ({
  page,
}) => {
  const user = {
    user_id: 7,
    name: "产品验收员",
    email: "judge@example.com",
    role_code: "user",
    tenant: { tenant_id: 3, name: "验收企业" },
  };
  const product = {
    product_id: 21,
    sku: "FS-E2E",
    name: "验收样品",
    category_code: "unclassified",
    analysis_status: "ready",
    current_profile_version_id: 8,
  };
  const dataset = {
    dataset_id: 31,
    name: "授权市场样本",
    platform: "amazon",
    market_country: "US",
    category_code: "unclassified",
    status: "ready",
    listing_count: 2,
    review_count: 2,
    valid_review_count: 2,
    quality_score: 95,
    data_start_date: null,
    data_end_date: "2026-09-01",
  };
  let taskPolls = 0;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    let data;
    const headers = { "content-type": "application/json" };
    if (path === "/api/v1/auth/login")
      data = {
        access_token: "contract-access",
        refresh_token: "contract-refresh",
        user,
      };
    else if (path === "/api/v1/users/me") data = user;
    else if (path === "/api/v1/dashboard/summary")
      data = {
        products: 1,
        running_tasks: 0,
        pending_confirmations: 0,
        reports: 0,
      };
    else if (path === "/api/v1/user-confirmations")
      data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    else if (path === "/api/v1/reports")
      data = { items: [], total: 0, page: 1, page_size: 5, total_pages: 0 };
    else if (path === "/api/v1/products" && method === "GET")
      data = { items: [product], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/market-datasets" && method === "GET")
      data = {
        items: [dataset],
        total: 1,
        page: 1,
        page_size: 100,
        total_pages: 1,
      };
    else if (path === "/api/v1/analysis-workspaces" && method === "POST") {
      const body = request.postDataJSON() || {};
      data = {
        workspace_uuid: body.workspace_uuid || "40000000-0000-4000-8000-000000000021",
        job_name: body.name || "验收样品分析",
        source: body.source || "node_workflow_canvas",
        workspace_status: "active",
        status: "draft",
        analysis_count: 0,
        created_at: "2026-09-14T08:00:00Z",
        updated_at: "2026-09-14T08:00:00Z",
      };
    } else if (path.includes("/analysis-workspaces/") && path.endsWith("/messages") && method === "POST") {
      data = {
        message_uuid: "40000000-0000-4000-8000-000000000088",
        role: "assistant",
        message_kind: "context",
        content: "ok",
        seq_no: 1,
        created_at: "2026-09-14T08:00:00Z",
      };
    }
    else if (path === "/api/v1/products" && method === "POST")
      data = { ...product, current_profile_version_id: null };
    else if (path === "/api/v1/products/21/assets:parse")
      data = {
        parse_job_id: "parse-e2e",
        product_id: 21,
        status: "queued",
        progress_percent: 0,
        current_stage: "queued",
      };
    else if (path === "/api/v1/product-parse-jobs/parse-e2e")
      data = {
        status: "succeeded",
        summary: {
          file_count: 1,
          succeeded_file_count: 1,
          failed_file_count: 0,
        },
        file_results: [],
      };
    else if (path === "/api/v1/products/21" && method === "GET") {
      data = {
        ...product,
        description: null,
        profile_version: 1,
        completeness_score: 0.25,
        source_summary: {},
        attributes: [
          {
            attribute_code: "material",
            attribute_name: "材质",
            attribute_value: "橡木",
            value_type: "string",
            source_type: "document",
            confidence: 0.92,
            confirmation_status: "confirmed",
          },
          {
            attribute_code: "style",
            attribute_name: "风格",
            attribute_value: "北欧",
            value_type: "string",
            source_type: "document",
            confidence: 0.62,
            confirmation_status: "unconfirmed",
          },
        ],
      };
      headers.etag = '"product-e2e-v1"';
    } else if (path === "/api/v1/products/21/profile:confirm")
      data = {
        product_id: 21,
        profile_version_id: 8,
        status: "confirmed",
        completeness_score: 0.25,
        confirmed_at: "2026-09-10T00:00:00Z",
      };
    else if (path === "/api/v1/analysis-tasks" && method === "POST")
      data = {
        task_uuid: "00000000-0000-4000-8000-000000000042",
        status: "draft",
      };
    else if (path.endsWith(":start"))
      data = {
        task_uuid: "00000000-0000-4000-8000-000000000042",
        status: "queued",
        retryable: false,
      };
    else if (path.endsWith("/result"))
      data = {
        report_uuid: "00000000-0000-4000-8000-000000000099",
        report_summary: {
          title: "验收样品市场报告",
          executive_summary: "基于授权样本生成。",
          overall_opportunity_score: 78,
          overall_confidence: 0.8,
        },
      };
    else if (path.includes("/analysis-tasks/")) {
      taskPolls += 1;
      data = {
        task_uuid: "00000000-0000-4000-8000-000000000042",
        status: "succeeded",
        stage: "completed",
        progress_percent: 100,
        user_confirmation: null,
      };
    } else
      data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    await route.fulfill({
      status: 200,
      headers,
      body: JSON.stringify(success(data)),
    });
  });

  await page.goto("/#login");
  await page.getByLabel("企业邮箱").fill("judge@example.com");
  await page.getByLabel("密码", { exact: true }).fill("contract-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();
  await page.getByRole("button", { name: /产品中心/ }).click();
  await expect(page.getByRole("heading", { name: "产品中心" })).toBeVisible();
  await expect(page.getByText("FS-E2E", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(page.getByText("AI 分析对话", { exact: true })).toBeVisible();
  await expect(page.getByText("验收样品", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /销量预测/ }).click();
  await expect(
    page.getByRole("heading", { name: "商品销量预测" }),
  ).toBeVisible();
});
