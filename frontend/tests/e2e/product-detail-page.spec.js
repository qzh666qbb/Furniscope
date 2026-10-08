import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "product-detail-page" });
const products = Array.from({ length: 12 }, (_, index) => ({
  product_id: 21 + index,
  sku: `CHAIR-${String(index + 1).padStart(2, "0")}`,
  name: index === 0 ? "云感休闲椅" : `休闲椅 ${index + 1}`,
  category_code: "chair",
  lifecycle_status: "active",
  analysis_status: "ready",
  current_profile_version_id: 8 + index,
  completeness_score: .86,
  has_conflicts: false,
  moq: 20,
  factory_price: 199 + index,
  created_at: "2026-10-01T08:00:00Z",
  updated_at: "2026-10-06T08:00:00Z",
}));
const detail = {
  ...products[0],
  description: "适用于北美公寓客厅的休闲椅。",
  profile_version: 3,
  profile_version_id: 8,
  profile_status: "confirmed",
  source_summary: { file_count: 4 },
  fact_suggestions: [],
  attributes: [{
    attribute_code: "material",
    attribute_name: "材质工艺",
    attribute_value: "橡木框架与科技布",
    unit: null,
    source_type: "document",
    source_locator: { evidence_text: "规格书第 2 页" },
    confidence: .94,
    confirmation_status: "confirmed",
  }],
};
const inventory = {
  total_inventory_units: 126,
  as_of_date: "2026-10-06",
  sites: [{ site: "北美仓", inventory_units: 126, as_of_date: "2026-10-06", source_version_uuid: "abcd1234-0000" }],
  import_status: { status: "confirmed", filename: "inventory.xlsx", updated_at: "2026-10-06T09:00:00Z" },
};

async function mockProductApi(page) {
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "product-detail-token"));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let data = { items: [] };
    const headers = { "content-type": "application/json" };
    if (path === "/api/v1/users/me") {
      data = { user_id: 7, name: "产品管理员", email: "product@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    } else if (path === "/api/v1/products") {
      data = { items: products, total: products.length, page: 1, page_size: 100, total_pages: 1 };
    } else if (path === "/api/v1/products/21") {
      data = detail;
      headers.etag = '"product-detail-v3"';
    } else if (path === "/api/v1/products/21/inventory-summary") {
      data = inventory;
    } else if (path === "/api/v1/products/21/relations") {
      data = { items: [] };
    } else if (path === "/api/v1/enterprise/fact-vocabulary") {
      data = { version: "2026-10", groups: [] };
    }
    await route.fulfill({ status: 200, headers, body: JSON.stringify(success(data)) });
  });
}

test("桌面端快速预览与独立产品档案分工明确，并恢复列表状态", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 780 });
  await mockProductApi(page);
  await page.goto("/#products?status=ready&category=%E6%A4%85%E7%B1%BB&created=30&product=21");
  await expect(page.getByRole("heading", { name: "产品中心" })).toBeVisible();
  await page.locator(".product-catalog-canvas").evaluate((node) => { node.scrollTop = 120; });

  await page.getByRole("button", { name: "快速预览 云感休闲椅" }).click();
  await expect(page.getByRole("dialog", { name: "云感休闲椅 快速预览" })).toBeVisible();
  await expect(page.getByText("库存摘要", { exact: true })).toBeVisible();
  await expect(page.getByText("产品关系", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: "test-results/product-quick-preview-desktop.png", fullPage: true });

  await page.getByRole("button", { name: "查看完整档案" }).click();
  await expect(page).toHaveURL(/#product-detail\?/);
  await expect(page.getByRole("heading", { name: "云感休闲椅" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "基础资料" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "产品关系" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "来源与档案记录" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/product-detail-page-desktop.png", fullPage: true });

  await page.getByTitle("返回产品中心").click();
  await expect(page).toHaveURL(/#products\?/);
  await expect(page.getByLabel("产品品类")).toHaveValue("椅类");
  await expect(page.getByLabel("创建时间")).toHaveValue("30");
  await expect(page.getByLabel("解析状态")).toHaveValue("ready");
  await expect(page.locator(".product-record-card.selected")).toContainText("云感休闲椅");
  await expect.poll(() => page.locator(".product-catalog-canvas").evaluate((node) => node.scrollTop)).toBeGreaterThanOrEqual(119);
});

test("移动端快速预览入口直接进入独立档案页", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockProductApi(page);
  await page.goto("/#products");
  await page.getByRole("button", { name: "快速预览 云感休闲椅" }).click();
  await expect(page).toHaveURL(/#product-detail\?/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "云感休闲椅" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/product-detail-page-mobile.png", fullPage: true });
});
