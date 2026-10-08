import { expect, test } from "@playwright/test";

const success = data => ({ success: true, data, request_id: "product-import-upload-first" });

test("产品导入上传优先，仅在服务端识别歧义时显示问题字段", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  let preflightConfig = null;

  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "product-import-token"));
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let data = {};
    if (path === "/api/v1/users/me") {
      data = { user_id: 7, name: "产品管理员", email: "product@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    } else if (path === "/api/v1/products") {
      data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    } else if (path === "/api/v1/product-imports/recent") {
      data = { items: [] };
    } else if (path === "/api/v1/product-imports:inspect") {
      data = {
        original_filename: "products.xlsx",
        sheet_name: "产品数据",
        available_sheets: ["说明", "产品数据"],
        header_row: 2,
        headers: ["SKU", "产品SKU", "产品名称", "品类"],
        total_rows: 36,
        field_mapping: { name: "产品名称", category_code: "品类" },
        detected_fields: [
          { field_code: "name", title: "产品名称", source_header: "产品名称", confidence: 100 },
          { field_code: "category_code", title: "品类代码", source_header: "品类", confidence: 100 },
        ],
        mapping_issues: [{
          field_code: "sku",
          title: "SKU",
          kind: "ambiguous",
          required: true,
          current_header: null,
          candidates: ["SKU", "产品SKU"],
          message: "多个列都可能是 SKU，请选择正确来源",
        }],
        value_issues: [],
        unit_mapping: {},
        dictionary_mapping: {},
        ignored_columns: ["SKU", "产品SKU"],
        warnings: [],
      };
    } else if (path === "/api/v1/product-imports:preflight") {
      preflightConfig = request.postData();
      data = {
        job_uuid: "71000000-0000-4000-8000-000000000001",
        original_filename: "products.xlsx",
        source_sha256: "a".repeat(64),
        template_version: null,
        sheet_name: "产品数据",
        available_sheets: ["说明", "产品数据"],
        header_row: 2,
        field_mapping: { sku: "SKU", name: "产品名称", category_code: "品类" },
        unit_mapping: {},
        dictionary_mapping: {},
        import_mode: "create_only",
        status: "ready",
        total_rows: 36,
        valid_rows: 36,
        error_rows: 0,
        warning_rows: 0,
        imported_rows: 0,
        preview_sha256: "b".repeat(64),
        schema_snapshot: { file_errors: [], alias_errors: [] },
        error_message: null,
        created_at: "2026-10-07T10:00:00Z",
        updated_at: "2026-10-07T10:00:00Z",
        committed_at: null,
        rows: [{
          source_row_number: 3,
          source_values: { SKU: "HF-001", 产品名称: "云感沙发", 品类: "sofa" },
          normalized_values: { sku: "HF-001", name: "云感沙发", category_code: "sofa", lifecycle_status: "active" },
          validation_errors: [],
          validation_warnings: [],
          planned_action: "create",
          is_user_edited: false,
          imported_product_id: null,
        }],
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#products");
  await page.getByRole("button", { name: "Excel 批量导入" }).click();
  await expect(page.getByRole("heading", { name: "产品主档批量导入" })).toBeVisible();
  await expect(page.getByText("确认字段映射")).toHaveCount(0);
  await expect(page.getByText("不导入", { exact: true })).toHaveCount(0);
  await expect(page.getByText("自动识别", { exact: true })).toHaveCount(0);
  const advancedSettings = page.locator(".import-advanced-options");
  await expect(advancedSettings).not.toHaveAttribute("open");
  await expect(advancedSettings.locator("summary")).toContainText("重复 SKU：跳过");
  await advancedSettings.locator("summary").click();
  const createOnlyButton = advancedSettings.getByRole("button", { name: /仅新增/ });
  const upsertButton = advancedSettings.getByRole("button", { name: /新增并更新/ });
  await expect(advancedSettings.getByText("可选字段不存在时会被忽略，不生成缺失的业务数据。")).toHaveCount(0);
  await expect(createOnlyButton).toHaveAttribute("aria-pressed", "true");
  await expect(upsertButton).toHaveAttribute("aria-pressed", "false");
  await upsertButton.click();
  await expect(upsertButton).toHaveAttribute("aria-pressed", "true");
  await expect(advancedSettings.locator("summary")).toContainText("重复 SKU：更新");
  await createOnlyButton.click();
  await expect(createOnlyButton).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: "test-results/product-import-write-mode-buttons.png", fullPage: true });
  await advancedSettings.locator("summary").click();
  await page.screenshot({ path: "test-results/product-import-upload-first-initial.png", fullPage: true });

  await page.locator('.import-file-picker input[type="file"]').setInputFiles({
    name: "products.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("synthetic workbook"),
  });

  await expect(page.getByRole("heading", { name: "文件结构识别完成" })).toBeVisible();
  await expect(page.locator(".import-resolution-row")).toHaveCount(1);
  await expect(page.locator(".import-resolution")).toContainText("多个列都可能是 SKU");
  await page.screenshot({ path: "test-results/product-import-upload-first-resolution.png", fullPage: true });
  const resolveButton = page.getByRole("button", { name: "应用修正并重新预检" });
  await expect(resolveButton).toBeDisabled();
  await page.locator(".import-resolution-row select").selectOption("SKU");
  await resolveButton.click();

  await expect(page.locator(".import-summary-strip")).toContainText("36");
  await expect(page.locator(".import-summary-strip")).toContainText("可提交");
  expect(preflightConfig).toContain('name="field_mapping"');
  expect(preflightConfig).toContain('"sku":"SKU"');
  await page.screenshot({ path: "test-results/product-import-upload-first-desktop.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/product-import-upload-first-mobile.png", fullPage: true });
});
