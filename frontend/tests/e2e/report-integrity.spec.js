import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "report-integrity" });
const reportId = "90000000-0000-4000-8000-000000000001";
const taskId = "90000000-0000-4000-8000-000000000002";

const report = {
  report_uuid: reportId,
  task_uuid: taskId,
  product_id: 21,
  product_sku: "FS-REPORT",
  product_name: "Integrity Sofa",
  title: "评分完整性报告",
  target_country: "US",
  target_platform: "amazon",
  created_at: "2026-10-07T12:00:00Z",
  decision_recommendation: "collect_more_data",
  executive_summary: "部分评分维度缺少连续市场数据，保持未评分。",
  overall_opportunity_score: 61,
  overall_confidence: 0.72,
  data_scope_snapshot: {
    dataset: "授权市场样本",
    listing_count: 20,
    valid_review_count: 5,
  },
  version_bundle: {
    report_contract: { primary_opportunity_id: 71 },
  },
  risk_summary: [],
  pending_validation_items: [],
  partial_failures_snapshot: [],
  price_summary: {},
  model_run: {
    provider: "rules",
    model_id: "deterministic",
    input_tokens: 0,
    output_tokens: 0,
    latency_ms: 4,
    schema_valid: true,
  },
};

const opportunity = {
  opportunity_id: 71,
  title: "小户型舒适沙发",
  demand_heat_score: 70,
  demand_growth_score: null,
  unmet_need_score: 66,
  competition_space_score: 52,
  profit_space_score: null,
};

async function mockReportApi(page, { failOpportunities = false } = {}) {
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "report-user-token");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/users/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(success({
          user_id: 7,
          name: "报告验收员",
          email: "report@example.com",
          role_code: "user",
          tenant: { tenant_id: 3, name: "验收企业" },
        })),
      });
      return;
    }
    if (failOpportunities && path.endsWith("/opportunities")) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          success: false,
          error: { code: "UPSTREAM_FAILED", message: "机会服务不可用", details: [] },
          request_id: "report-integrity",
        }),
      });
      return;
    }
    let data = { items: [], total: 0 };
    if (path === `/api/v1/reports/${reportId}`) data = report;
    else if (path === `/api/v1/reports/${reportId}/evidence`) data = [];
    else if (path.endsWith("/opportunities")) data = { items: [opportunity] };
    else if (path === "/api/v1/products/21") {
      data = { product_id: 21, sku: "FS-REPORT", name: "Integrity Sofa", attributes: [] };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(success(data)),
    });
  });
}

test("报告缺失维度保持未评分，不使用其他得分补造", async ({ page }) => {
  await mockReportApi(page);
  await page.goto(`/#report-detail?id=${reportId}`);

  await expect(page.getByRole("heading", { name: "评分完整性报告" })).toBeVisible();
  await expect(page.getByText("未评分", { exact: true })).toHaveCount(2);
  await expect(page.getByText(/需求增长、利润空间缺少有效依据，保持未评分/)).toBeVisible();
});

test("报告核心子资源失败时展示具体错误并停止组装", async ({ page }) => {
  await mockReportApi(page, { failOpportunities: true });
  await page.goto(`/#report-detail?id=${reportId}`);

  await expect(page.getByText(/机会项加载失败：机会服务不可用/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "评分完整性报告" })).toHaveCount(0);
});
