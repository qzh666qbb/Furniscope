import { expect, test } from "@playwright/test";

test("真实后端：登录、产品分析入口与预测入口", async ({ page }) => {
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

  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "产品中心" }).click();
  await expect(page.getByRole("heading", { name: "产品中心" })).toBeVisible();
  await expect(page.getByText("E2E-SOFA", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(page.getByText("AI 分析对话", { exact: true })).toBeVisible();
  await expect(page.getByText("Browser E2E sofa")).toBeVisible();

  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "销量预测" }).click();
  await expect(page.getByRole("heading", { name: "商品销量预测" })).toBeVisible();
});
