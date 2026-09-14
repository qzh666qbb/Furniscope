import { expect, test } from "@playwright/test";
import path from "node:path";

test("真实后端：登录、数据导入、分析报告与预测入口", async ({ page }) => {
  test.skip(process.env.FURNISCOPE_E2E_REAL !== "1", "opt-in full-stack test");
  test.setTimeout(120_000);
  const email = process.env.FURNISCOPE_E2E_EMAIL;
  const password = process.env.FURNISCOPE_E2E_PASSWORD;
  if (!email || !password) throw new Error("FURNISCOPE_E2E_EMAIL/PASSWORD are required");

  await page.goto("/#login");
  await page.getByLabel("企业邮箱").fill(email);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();

  await page.getByRole("button", { name: /分析中心/ }).click();
  await page.getByRole("button", { name: /上传资料验证/ }).click();
  const productSelect = page.getByLabel("选择产品");
  const productId = await productSelect.locator("option").filter({ hasText: "E2E-SOFA" }).getAttribute("value");
  await productSelect.selectOption(productId);
  await page.getByRole("button", { name: "使用该产品" }).click();
  await page.getByLabel("数据名称").fill(`Browser E2E ${Date.now()}`);
  await page.getByLabel("数据来源说明").fill("浏览器全链路验证数据");
  await page.getByLabel("市场数据工作簿").setInputFiles(
    path.resolve("tests/fixtures/real-e2e-market.json"),
  );
  await page.getByRole("button", { name: "上传并继续" }).click();
  await expect(page.getByRole("heading", { name: "确认分析范围" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "启动分析任务" }).click();
  await expect(page.getByRole("heading", { name: /Browser E2E sofa/ })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "决策报告", exact: true }).click();
  await expect(page.getByText("模型调用与证据")).toBeVisible();
  await expect(page.getByText(/关联证据 [1-9]\d* 条/)).toBeVisible();

  await page.evaluate(() => { location.hash = "forecast"; });
  await expect(page.getByRole("heading", { name: "商品销量预测" })).toBeVisible();
});
