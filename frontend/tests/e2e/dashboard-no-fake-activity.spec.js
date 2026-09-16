import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "dashboard-e2e" });

test("首页工作流节点和运行中计数与真实任务状态同步", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "dashboard-user-token");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [], total: 0 };
    if (path === "/api/v1/users/me") {
      data = {
        user_id: 7,
        name: "首页验收员",
        email: "dashboard@example.com",
        role_code: "user",
        tenant: { tenant_id: 3, name: "首页测试企业" },
      };
    } else if (path === "/api/v1/dashboard/summary") {
      data = {
        products: 12,
        running_tasks: 1,
        pending_confirmations: 0,
        failed_tasks: 1,
        conflicted_products: 2,
        reports: 3,
      };
    } else if (path === "/api/v1/user-confirmations") {
      data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    } else if (path === "/api/v1/analysis-workspaces") {
      data = {
        items: [
          {
            workspace_uuid: "10000000-0000-4000-8000-000000000001",
            job_name: "扶手椅 HF-B0142 出海机会分析",
            source: "node_workflow_canvas",
            workspace_status: "active",
            status: "succeeded",
            stage: "completed",
            progress_percent: 100,
            analysis_count: 1,
            product_sku: "HF-B0142",
            product_name: "扶手椅",
            target_country: "US",
            target_platform: "amazon",
            task_uuid: "20000000-0000-4000-8000-000000000001",
            report_uuid: "30000000-0000-4000-8000-000000000001",
            created_at: "2026-09-15T12:00:00Z",
            updated_at: "2026-09-15T13:41:54Z",
          },
          {
            workspace_uuid: "10000000-0000-4000-8000-000000000002",
            job_name: "扶手椅 HF-A0345 出海机会分析",
            source: "node_workflow_canvas",
            workspace_status: "active",
            status: "partial_succeeded",
            stage: "researching_market",
            progress_percent: 65,
            analysis_config: { target_node: "market" },
            analysis_count: 1,
            product_sku: "HF-A0345",
            product_name: "扶手椅",
            target_country: "US",
            target_platform: "amazon",
            task_uuid: "20000000-0000-4000-8000-000000000002",
            report_uuid: null,
            created_at: "2026-09-15T12:00:00Z",
            updated_at: "2026-09-15T13:21:54Z",
          },
        ],
        total: 2,
        page: 1,
        page_size: 10,
        total_pages: 1,
      };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(success(data)),
    });
  });

  await page.goto("/#workspace");
  await expect(page.getByText("运行中任务")).toBeVisible();
  await expect(page.locator(".metrics button").filter({ hasText: "运行中任务" }).getByRole("strong")).toHaveText("1");
  await expect(page.locator(".metrics button").filter({ hasText: "待确认异常" }).getByRole("strong")).toHaveText("3");
  const completedRow = page.locator(".product-row").filter({ hasText: "HF-B0142" });
  await expect(completedRow.locator(".step.done")).toHaveCount(5);
  await expect(completedRow.locator(".step.active")).toHaveCount(0);
  const partialRow = page.locator(".product-row").filter({ hasText: "HF-A0345" });
  await expect(partialRow.locator(".step").nth(1)).toHaveClass(/active/);
  await expect(partialRow.locator(".step").nth(0)).toHaveClass(/done/);
});

test("首页不展示写死的 AI 实时分析动态", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "dashboard-user-token");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [], total: 0 };
    if (path === "/api/v1/users/me") {
      data = {
        user_id: 7,
        name: "首页验收员",
        email: "dashboard@example.com",
        role_code: "user",
        tenant: { tenant_id: 3, name: "首页测试企业" },
      };
    } else if (path === "/api/v1/dashboard/summary") {
      data = { products: 0, running_tasks: 0, pending_confirmations: 0, reports: 0 };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(success(data)),
    });
  });

  await page.goto("/#workspace");
  await expect(page.getByRole("heading", { name: "今天要研究哪个产品？" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "AI 工作流任务" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近报告" })).toBeVisible();
  await expect(page.getByText("AI 实时分析动态", { exact: false })).toHaveCount(0);
  await expect(page.getByText("系统正在持续解析产品、研究市场并校验分析结论", { exact: true })).toHaveCount(0);
});
