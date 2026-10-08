import { expect, test } from "@playwright/test";

test("租户模型空间解释标准训练要求，不开放直接代码发布", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "admin-token"));
  await page.route("**/api/v1/users/me", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ success: true, data: { user_id: 1, name: "平台管理员", role_code: "admin" } }),
  }));
  await page.route("**/api/v1/admin/enterprise-users**", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ success: true, data: { items: [] } }),
  }));
  await page.route("**/api/v1/admin/registration-applications**", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ success: true, data: { items: [] } }),
  }));
  const models = [
    { tenant_id: 7, tenant_code: "HEFENG", enterprise_name: "HeFeng", tenant_status: "active", version: "sales-v4-hf", model_scope: "tenant_private" },
  ];
  await page.route("**/api/v1/admin/forecast-models**", route => {
    const match = route.request().url().match(/forecast-models\/(\d+)$/);
    const tenant = match ? models.find(item => item.tenant_id === Number(match[1])) : null;
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ success: true, data: tenant ? { ...tenant, deployments: [], training_runs: [] } : { items: models } }),
    });
  });

  await page.goto("/#admin");
  await page.getByRole("button", { name: "预测模型" }).click();

  await expect(page.locator(".control-heading").getByRole("button")).toHaveCount(0);
  await expect(page.locator(".model-detail").getByRole("button", { name: "更新模型" })).toBeVisible();
  await expect(page.locator(".model-detail").getByText("HEFENG · 企业私有模型空间")).toBeVisible();

  await page.locator(".model-detail").getByRole("button", { name: "更新模型" }).click();
  await expect(page.getByRole("heading", { name: "更新 HeFeng 的模型" })).toBeVisible();
  await expect(page.getByRole("dialog").getByText("sales-v4-hf", { exact: true })).toBeVisible();
  await expect(page.getByText("代码直接替换暂未开放", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog").locator('input[type="file"]')).toHaveCount(0);
});
