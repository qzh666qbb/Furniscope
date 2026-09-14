import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "market-insights-contract" });

test("市场洞察提供明确的数据导入、清洗和 AI 分析路径", async ({ page }) => {
  const user = { user_id: 7, name: "产品验收员", email: "judge@example.com", role_code: "user", tenant: { tenant_id: 3, name: "验收企业" } };
  const dataset = {
    dataset_id: 31, name: "授权市场样本", platform: "amazon", market_country: "US",
    category_code: "unclassified", source_type: "enterprise_export", source_name: "企业授权脱敏数据",
    status: "ready", listing_count: 2, review_count: 2, valid_review_count: 2,
    quality_score: 95, data_start_date: null, data_end_date: "2026-09-01",
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", version_no: 1,
    quality_report: { raw_review_count: 2, valid_review_count: 2, filtered_review_count: 0 },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    if (path === "/api/v1/auth/login") data = { access_token: "contract-access", refresh_token: "contract-refresh", user };
    else if (path === "/api/v1/users/me") data = user;
    else if (path === "/api/v1/dashboard/summary") data = { products: 1, running_tasks: 0, pending_confirmations: 0, reports: 0 };
    else if (path === "/api/v1/market-datasets") data = { items: [dataset], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/competitor-tracking/overview") data = { watch_count: 0, unread_alerts: 1, watches: [], alerts: [], rhythm: { launches: [], promos: [] } };
    else if (path === "/api/v1/market-signals/overview") data = { source_count: 3, sentiment_count: 128, unread_policy_alerts: 2 };
    else if (path === "/api/v1/market-signals/sources") data = [];
    else if (path === "/api/v1/market-signals/sentiment-events") data = [];
    else if (path === "/api/v1/market-signals/sentiment-feed") data = { items: [], total: 0, page: 1, page_size: 20, has_next: false, positive_count: 0, negative_count: 0, neutral_count: 0, live_count: 0, dataset_count: 0 };
    else if (path === "/api/v1/market-signals/policy-sources") data = [];
    else if (path === "/api/v1/market-signals/policy-alerts") data = [];
    else if (path === "/api/v1/notification-channels" || path === "/api/v1/notification-channels/events") data = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#login");
  await page.getByRole("textbox", { name: "企业邮箱", exact: true }).fill("judge@example.com");
  await page.locator('input[type="password"]').fill("contract-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();
  await page.evaluate(() => { location.hash = "insights"; });

  await expect(page.getByRole("heading", { name: "市场洞察", exact: true })).toBeVisible();
  const monitor = page.locator(".market-capability-hub").first();
  const datasets = page.locator(".dataset-section-heading").first();
  await expect(monitor.getByText("竞品分析与评论舆情", { exact: true })).toBeVisible();
  await expect(datasets.getByRole("heading", { name: "市场数据集" })).toBeVisible();
  const monitorBox = await monitor.boundingBox();
  const datasetsBox = await datasets.boundingBox();
  expect(monitorBox.y).toBeLessThan(datasetsBox.y);
  await page.screenshot({ path: "design-qa-market-insights.png", fullPage: true });
  await page.getByRole("button", { name: "用于 AI 分析", exact: true }).first().click();
  await expect(page).toHaveURL(/#workflow\?.*dataset=31/);
});

test("自动化监控子页按任务提供首次使用引导", async ({ page }) => {
  const user = { user_id: 7, name: "产品验收员", email: "judge@example.com", role_code: "user", tenant: { tenant_id: 3, name: "验收企业" } };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    if (path === "/api/v1/auth/login") data = { access_token: "contract-access", refresh_token: "contract-refresh", user };
    else if (path === "/api/v1/users/me") data = user;
    else if (path === "/api/v1/dashboard/summary") data = { products: 1, running_tasks: 0, pending_confirmations: 0, reports: 0 };
    else if (path === "/api/v1/market-datasets") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/competitor-tracking/overview") data = { watch_count: 0, unread_alerts: 0, watches: [], alerts: [], rhythm: { launches: [], promos: [] } };
    else if (path === "/api/v1/market-signals/sentiment-feed") data = { items: [], total: 0, page: 1, page_size: 20, has_next: false, positive_count: 0, negative_count: 0, neutral_count: 0, live_count: 0, dataset_count: 0 };
    else if (path === "/api/v1/market-signals/sources" || path === "/api/v1/market-signals/sentiment-events" || path === "/api/v1/market-signals/policy-sources" || path === "/api/v1/market-signals/policy-alerts" || path === "/api/v1/notification-channels" || path === "/api/v1/notification-channels/events") data = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });
  await page.goto("/#login");
  await page.getByRole("textbox", { name: "企业邮箱", exact: true }).fill("judge@example.com");
  await page.locator('input[type="password"]').fill("contract-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();
  await page.goto("/#competitor-tracking?tab=prices");

  await expect(page.getByRole("heading", { name: "竞品分析与评论舆情" })).toBeVisible();
  await expect(page.getByRole("button", { name: /竞品分析/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "用本企业产品对比市场上的相似商品" })).toBeVisible();
  await expect(page.getByText("开始对比", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /主动采集/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /政策与告警/ })).toHaveCount(0);

  await page.getByRole("button", { name: /评论舆情/ }).click();
  await expect(page.getByRole("heading", { name: "把市场评论和网页采集放在同一条舆情流里" })).toBeVisible();
  await expect(page.getByRole("button", { name: "获取舆情", exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-automation-center.png", fullPage: true });
});
