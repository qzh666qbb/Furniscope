import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "forecast-append-e2e" });

test("企业用户可直接打开销量预测的数据追加 tab", async ({ page }) => {
  let dataThrough = "2026-06-30";
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "forecast-user-token");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [] };
    if (path === "/api/v1/users/me") {
      data = {
        user_id: 7,
        name: "预测维护员",
        email: "forecast@example.com",
        role_code: "user",
        tenant: { tenant_id: 3, name: "预测测试企业" },
      };
    } else if (path === "/api/v1/forecast/status") {
      data = { ready: true, version: "sales-forecast-v4", data_through: dataThrough };
    } else if (path === "/api/v1/forecast-jobs") {
      data = { items: [] };
    } else if (path === "/api/v1/forecast/training-runs") {
      data = { items: [] };
    } else if (path === "/api/v1/forecast/append") {
      data = { training_uuid: "00000000-0000-4000-8000-000000000071", status: "queued", metrics: {}, created_at: "2026-09-14T00:00:00Z" };
    } else if (path === "/api/v1/forecast/training-runs/00000000-0000-4000-8000-000000000071") {
      dataThrough = "2026-07-07";
      data = { training_uuid: "00000000-0000-4000-8000-000000000071", status: "succeeded", metrics: { inserted_rows: 42, overwritten_rows: 3, after_last_date: dataThrough }, created_at: "2026-09-14T00:00:00Z" };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(success(data)),
    });
  });

  await page.goto("/#forecast");
  await expect(page.getByRole("heading", { name: "商品销量预测" })).toBeVisible();
  await expect(page.getByText("当前模型")).toBeVisible();
  await expect(page.getByText("已就绪 · 数据更新至 2026-06-30")).toBeVisible();
  await expect(page.getByText("sales-forecast-v4", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^天预测/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^周预测/ })).toBeVisible();
  await page.getByRole("button", { name: /^复杂业务预测/ }).click();
  await expect(page.getByRole("heading", { name: "复杂业务参数" })).toBeVisible();
  await expect(page.getByText("计划售价", { exact: true })).toBeVisible();
  await expect(page.getByText("促销流量倍数", { exact: false })).toBeVisible();
  await expect(page.getByText("参考 SKU", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await expect(page.getByRole("heading", { name: "追加订单与库存数据" })).toBeVisible();
  await expect(page.getByText("每周数据维护")).toBeVisible();
  const submit = page.getByRole("button", { name: "追加数据并重新训练" });
  await expect(submit).toBeDisabled();
  await page.locator('.forecast-training-files input[type="file"]').first().setInputFiles({
    name: "orders.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("PK mock workbook"),
  });
  page.once("dialog", dialog => dialog.accept());
  await submit.click();
  await expect(page.getByText("已就绪 · 数据更新至 2026-07-07", { exact: true })).toBeVisible({ timeout: 10_000 });
});
