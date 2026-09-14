import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "dashboard-e2e" });

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
