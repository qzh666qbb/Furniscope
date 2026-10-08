import { expect, test } from "@playwright/test";

test("真实后端：登录、业务入口与 AI 员工 Goal-Run-Approval", async ({ page }) => {
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
  await expect(page.getByRole("strong").filter({ hasText: "E2E-SOFA · Browser E2E sofa" })).toBeVisible();

  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "销量预测" }).click();
  await expect(page.getByRole("heading", { name: "商品销量预测" })).toBeVisible();

  await page.goto("/#employee");
  await expect(page.getByRole("heading", { name: "经营分析员工" })).toBeVisible();
  await page.getByRole("button", { name: "委派目标" }).click();
  await page
    .getByPlaceholder("例如：检查最新销量数据是否已满足训练条件")
    .fill("检查最新销量数据是否满足训练条件");
  await page
    .locator(".employee-skill-picker")
    .getByRole("button", { name: /数据就绪检查/ })
    .click();
  await page.getByRole("button", { name: "生成计划" }).click();
  await expect(page.getByRole("heading", { name: "确认执行计划" })).toBeVisible();
  await expect(page.getByText("没有已确认的销量数据版本，请先完成标准数据建模。")).toBeVisible();
  await page.getByRole("button", { name: "确认并启动" }).click();
  await expect(page).toHaveURL(/#employee\?goal=/);
  await expect(page.getByRole("heading", { name: "检查最新销量数据是否满足训练条件" })).toBeVisible();
  await expect(page.getByText("确认标准销量数据").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("待我处理", { exact: true }).first()).toBeVisible();
});
