import { expect, test } from "@playwright/test";
import { strToU8, zipSync } from "fflate";

const success = (data) => ({ success: true, data, request_id: "forecast-append-e2e" });

function xlsxBuffer(rows) {
  const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  const column = index => {
    let value = index + 1, result = "";
    while (value) {
      result = String.fromCharCode(65 + (value - 1) % 26) + result;
      value = Math.floor((value - 1) / 26);
    }
    return result;
  };
  const sheetRows = rows.map((row, rowIndex) => `<row r="${rowIndex + 1}">${row.map((value, columnIndex) => {
    const ref = `${column(columnIndex)}${rowIndex + 1}`;
    return typeof value === "number"
      ? `<c r="${ref}"><v>${value}</v></c>`
      : `<c r="${ref}" t="inlineStr"><is><t>${escape(value)}</t></is></c>`;
  }).join("")}</row>`).join("");
  return Buffer.from(zipSync({
    "[Content_Types].xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
      <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
        <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/>
        <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
        <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
      </Types>`),
    "_rels/.rels": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
      <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
      </Relationships>`),
    "xl/workbook.xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
      <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheets><sheet name="销量" sheetId="1" r:id="rId1"/></sheets>
      </workbook>`),
    "xl/_rels/workbook.xml.rels": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
      <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
      </Relationships>`),
    "xl/worksheets/sheet1.xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?>
      <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>${sheetRows}</sheetData>
      </worksheet>`),
  }));
}

test("已有模型默认进入快速追加，合规XLSX预检确认后提交", async ({ page }) => {
  const dataThrough = "2026-06-30";
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
    } else if (path === "/api/v1/forecast/append" && route.request().method() === "POST") {
      data = {
        training_uuid: "00000000-0000-4000-8000-000000000099",
        status: "queued",
        update_kind: "append",
        orders_filename: "append-orders.xlsx",
        created_at: "2026-10-06T12:00:00Z",
        metrics: {},
      };
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
  await expect(page.locator(".forecast-training-page")).toBeVisible();
  await expect(page.getByRole("button", { name: /快速追加/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "快速追加销量数据" })).toBeVisible();
  await expect(page.getByLabel("每日汇总销量文件")).toHaveAttribute("accept", ".xlsx");
  await page.getByLabel("每日汇总销量文件").setInputFiles({
    name: "append-orders.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: xlsxBuffer([
      ["date", "sku", "site", "sales"],
      ["2026-06-28", "SAME-SKU", "US", 10],
      ["2026-06-29", "SAME-SKU", "US", 12],
      ["2026-06-30", "SAME-SKU", "US", 11],
    ]),
  });
  await expect(page.getByText(/每日汇总销量文件已通过预检/)).toBeVisible();
  await page.getByRole("checkbox", { name: /我确认这是完整的每日汇总数据/ }).check();
  const [appendRequest] = await Promise.all([
    page.waitForRequest(request => new URL(request.url()).pathname === "/api/v1/forecast/append"),
    page.getByRole("button", { name: "追加数据并重新训练" }).click(),
  ]);
  expect(appendRequest.method()).toBe("POST");
  expect(appendRequest.postData()).toContain("append-orders.xlsx");
  expect(appendRequest.postData()).toContain("allow_history_overwrite");
  expect(appendRequest.postData()).toContain("true");
  await expect(page.getByText("数据已提交，系统将在质量校验通过后训练并发布")).toBeVisible();
  await page.getByRole("button", { name: /标准数据建模/ }).click();
  await expect(page.getByRole("heading", { name: "从原始数据到可验证的模型" })).toBeVisible();
  await expect(page.getByLabel("选择数据文件")).toHaveAttribute("accept", ".csv,.xlsx,.json");
  await expect(page.getByRole("heading", { name: "训练与评测记录" })).toBeVisible();
});

test("新用户默认进入标准数据建模，快速入口明确提示首次接入风险", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "forecast-new-user-token");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [] };
    if (path === "/api/v1/users/me") {
      data = {
        user_id: 8,
        name: "首次接入用户",
        email: "new@example.com",
        role_code: "user",
        tenant: { tenant_id: 4, name: "新接入企业" },
      };
    } else if (path === "/api/v1/forecast/status") {
      data = { ready: false, data_through: null };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(success(data)),
    });
  });

  await page.goto("/#forecast");
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await expect(page.getByRole("heading", { name: "从原始数据到可验证的模型" })).toBeVisible();
  await expect(page.getByRole("button", { name: /标准数据建模/ })).toHaveClass(/active/);
  await page.getByRole("button", { name: /快速追加/ }).click();
  await expect(page.getByText("首次接入建议使用标准数据建模", { exact: true })).toBeVisible();
  await expect(page.getByText(/仅 XLSX；首个工作表为数据表/)).toBeVisible();
  await page.getByRole("button", { name: "使用标准数据建模", exact: true }).click();
  await expect(page.getByRole("heading", { name: "从原始数据到可验证的模型" })).toBeVisible();
});

test("产品编码对照仅在训练预览发现问题后出现并使旧预览失效", async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "forecast-mapping-token");
  });
  const version = {
    version_uuid: "00000000-0000-4000-8000-000000000201",
    filename: "confirmed-history.xlsx",
    status: "confirmed",
    preview_sha256: "preview-sha",
    raw_sha256: "raw-sha",
    canonical_sha256: "canonical-sha",
    created_at: "2026-10-07T08:00:00Z",
    columns_info: ["date", "sku", "site", "sales"],
    auxiliary_sources: {},
    rules: {
      kind: "sales",
      mapping: { date: "date", sku: "sku", site: "site", sales: "sales" },
      grain: "daily",
      sales_basis: "gross_units",
      date_format: "%Y-%m-%d",
      default_site: null,
      missing_dates: "unknown",
      complete_export_confirmed: false,
    },
    quality: { trainable: true },
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let data = { items: [] };
    if (path === "/api/v1/users/me") {
      data = {
        user_id: 9,
        name: "编码对照测试用户",
        email: "mapping@example.com",
        role_code: "user",
        tenant: { tenant_id: 5, name: "编码对照测试企业" },
      };
    } else if (path === "/api/v1/forecast/status") {
      data = { ready: false, data_through: null };
    } else if (path === "/api/v1/forecast/data-imports") {
      data = { items: [version] };
    } else if (path === `/api/v1/forecast/data-imports/${version.version_uuid}`) {
      data = version;
    } else if (path === "/api/v1/forecast/training-preview" && method === "POST") {
      data = {
        input_rows: 160,
        merged_rows: 160,
        overlap_rows: 0,
        overwritten_rows: 0,
        missing_days: 0,
        gaps: [],
        overwrites: [],
        catalog: {
          items: [],
          unmapped: [{ source_sku: "EXTERNAL-SKU", site: "US", product_sku: "EXTERNAL-SKU" }],
          conflicts: [],
          can_publish: false,
        },
      };
    } else if (path === "/api/v1/forecast/sku-mappings" && method === "GET") {
      data = {
        revision: "mapping-revision-1",
        items: [],
        products: [{ product_id: 501, sku: "SAME-SKU", name: "标准产品" }],
      };
    } else if (path === "/api/v1/forecast/sku-mappings" && method === "PUT") {
      data = {
        revision: "mapping-revision-2",
        items: [{ source_sku: "EXTERNAL-SKU", product_sku: "SAME-SKU" }],
        products: [{ product_id: 501, sku: "SAME-SKU", name: "标准产品" }],
      };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(success(data)),
    });
  });

  await page.goto("/#forecast");
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await expect(page.getByRole("heading", { name: "产品编码对照（可选）", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /confirmed-history\.xlsx/ }).click();
  await page.getByRole("button", { name: "预览训练范围", exact: true }).click();
  await expect(page.getByRole("heading", { name: "产品编码对照（可选）", exact: true })).toBeVisible();
  await expect(page.getByText("仅做编码身份绑定，不会修改日期、销量或其他标准数据。")).toBeVisible();
  await expect(page.getByLabel("原始 SKU 1")).toHaveValue("EXTERNAL-SKU");
  await page.getByLabel("目标 SKU 1").selectOption("SAME-SKU");
  await page.getByRole("button", { name: "保存产品编码对照", exact: true }).click();
  await expect(page.getByText(/产品编码对照已保存，请重新预览训练范围/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "产品编码对照（可选）", exact: true })).toHaveCount(0);
});
