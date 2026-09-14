import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const success = data => ({ success: true, data, request_id: "forecast-history-e2e" });
const jobs = Array.from({ length: 28 }, (_, index) => ({
  job_uuid: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  job_name: `真实预测记录 ${String(index + 1).padStart(2, "0")}`,
  status: "succeeded",
  granularity: index % 3 === 0 ? "week" : "day",
  horizon: index % 3 === 0 ? 4 : 7,
  skus: [`SKU-${String(index + 1).padStart(3, "0")}`],
  sites: [index % 2 === 0 ? "US" : "DE"],
  progress_percent: 100,
  created_at: new Date(Date.UTC(2026, 8, 14 - index)).toISOString(),
  started_at: new Date(Date.UTC(2026, 8, 14 - index)).toISOString(),
  completed_at: new Date(Date.UTC(2026, 8, 14 - index, 0, 1)).toISOString(),
}));

test("历史预测支持服务端筛选、十条分页、结果弹窗和导出", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "forecast-user-token"));
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let data = { items: [] };
    if (path === "/api/v1/users/me") {
      data = { user_id: 7, name: "预测维护员", email: "forecast@example.com", role_code: "user", tenant: { tenant_id: 3, name: "预测测试企业" } };
    } else if (path === "/api/v1/forecast/status") {
      data = { ready: true, data_through: "2026-06-30" };
    } else if (path === "/api/v1/forecast-jobs") {
      const query = (url.searchParams.get("q") || "").toLowerCase();
      const status = url.searchParams.get("status") || "";
      const granularity = url.searchParams.get("granularity") || "";
      const pageNumber = Number(url.searchParams.get("page") || 1);
      const filtered = jobs.filter(job => {
        const searchable = `${job.job_name} ${job.skus.join(" ")} ${job.sites.join(" ")}`.toLowerCase();
        return (!query || searchable.includes(query)) && (!status || job.status === status) && (!granularity || job.granularity === granularity);
      });
      data = { items: filtered.slice((pageNumber - 1) * 10, pageNumber * 10), page: pageNumber, page_size: 10, total: filtered.length, total_pages: Math.ceil(filtered.length / 10) };
    } else if (/^\/api\/v1\/forecast-jobs\/[^/]+\/result$/.test(path)) {
      const jobUuid = path.split("/").at(-2);
      const job = jobs.find(item => item.job_uuid === jobUuid);
      data = {
        job,
        run_uuid: "00000000-0000-4000-9000-000000000001",
        model: { training_data_through: "2026-06-30" },
        metrics: { pair_count: 1, total_forecast: 72, safety_stock: 9, recommended_production: 81 },
        summaries: [{ sku: job.skus[0], site: job.sites[0], total: 72, daily_average: 10.3, lower: 63, upper: 81, reliability: "A" }],
        points: [{ bucket_start: "2026-07-01", site: job.sites[0], sku: job.skus[0], predicted_sales: 10, lower: 8, upper: 12, reliability: "A" }],
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#forecast");
  await expect(page.getByText("最近预测", { exact: true })).toHaveCount(0);
  await expect(page.getByText("4 条", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "历史预测记录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "历史预测记录" })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看预测结果" })).toHaveCount(10);
  await expect(page.getByText("第 1 / 3 页")).toBeVisible();

  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("真实预测记录 11")).toBeVisible();
  await expect(page.getByText("第 2 / 3 页")).toBeVisible();

  await page.getByLabel("查找预测记录").fill("SKU-028");
  await expect(page.getByText("真实预测记录 28")).toBeVisible();
  await expect(page.getByText("第 1 / 1 页")).toBeVisible();
  await expect(page.getByRole("button", { name: "查看预测结果" })).toHaveCount(1);

  await page.getByRole("button", { name: "查看预测结果" }).click();
  const modal = page.locator(".forecast-result-modal");
  await expect(modal.getByRole("heading", { name: "预测结果" })).toBeVisible();
  await expect(modal.getByText("建议生产量")).toBeVisible();
  await expect(modal.getByRole("button", { name: "导出 CSV" })).toBeVisible();
  await expect(modal.getByRole("button", { name: "导出 JSON" })).toBeVisible();
  await expect(modal.getByText("SKU-028", { exact: true })).toBeVisible();
  await expect(modal.getByRole("columnheader", { name: "销量预测区间" })).toBeVisible();
  await expect(modal.getByRole("columnheader", { name: "可靠度值" })).toBeVisible();

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    modal.getByRole("button", { name: "导出 CSV" }).click(),
  ]);
  const csv = await readFile(await download.path(), "utf8");
  expect(csv).toContain("预测销量,下界,上界,销量预测区间,可靠度值");
  expect(csv).toContain("10,8,12,8 ~ 12,A");
});
