import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// Dedicated synthetic PostgreSQL fixture from test_enterprise_http.seed_enterprise.
// Explicit opt-in: never run these mutations against a regular enterprise server.
const fixturePath = process.env.FURNISCOPE_ENTERPRISE_FIXTURE;
const base = process.env.FURNISCOPE_ENTERPRISE_URL || "http://127.0.0.1:4186";
test.skip(!fixturePath, "Requires isolated enterprise fixture and local API");
test.describe.configure({ mode: "serial" });
let account;
test.beforeAll(() => { if (fixturePath) [account] = JSON.parse(readFileSync(fixturePath, "utf8")); });
test.beforeEach(async ({ page }) => {
  page.setDefaultTimeout(15_000);
  await page.goto(`${base}/#login`);
  await page.getByRole("textbox", { name: "企业邮箱", exact: true }).fill(account.email);
  await page.locator('input[type="password"]').fill(account.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();
});
function records(days, sku = "SAME-SKU") {
  return Array.from({ length: days }, (_, index) => ({
    date: new Date(Date.UTC(2025, 0, 6 + index)).toISOString().slice(0, 10),
    sku, site: "US", sales: 10,
  }));
}
async function upload(page, name, days, sku) {
  await page.getByLabel("选择数据文件", { exact: true }).setInputFiles({
    name, mimeType: "application/json", buffer: Buffer.from(JSON.stringify(records(days, sku))),
  });
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认此标准数据版本" })).toBeEnabled();
}
test("映射修改使旧预检失效；首次建模发布并刷新SKU和模型状态", async ({ page }) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.getByRole("button", { name: "销量预测", exact: true }).first().click();
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await upload(page, "synthetic-history.json", 196, "EXTERNAL-SKU");
  await page.getByRole("combobox", { name: "原始粒度", exact: true }).selectOption("transactions");
  await expect(page.getByRole("button", { name: "确认此标准数据版本" })).toHaveCount(0);
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await page.getByRole("button", { name: "确认此标准数据版本" }).click();
  await page.getByRole("button", { name: "预览训练范围", exact: true }).click();
  await expect(page.getByText(/合并后 196 行/)).toBeVisible();
  await expect(page.getByText(
    /^已关联 0 个 SKU \/ 站点 · 未关联 1 个 · 冲突 0 个$/,
  )).toBeVisible();
  await expect(page.getByRole("button", { name: "启动首次建模", exact: true })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "产品编码对照（可选）", exact: true })).toBeVisible();
  await expect(page.getByLabel("原始 SKU 1", { exact: true })).toHaveValue("EXTERNAL-SKU");
  await page.getByLabel("目标 SKU 1", { exact: true }).selectOption("SAME-SKU");
  await page.getByRole("button", { name: "保存产品编码对照", exact: true }).click();
  await expect(page.getByText(/产品编码对照已保存/)).toBeVisible();
  await expect(page.getByRole("button", { name: "启动首次建模", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "预览训练范围", exact: true }).click();
  await expect(page.getByText(
    /^已关联 1 个 SKU \/ 站点 · 未关联 0 个 · 冲突 0 个$/,
  )).toBeVisible();
  await page.getByRole("button", { name: "启动首次建模", exact: true }).click();
  await expect(page.locator(".enterprise-run .succeeded")).toBeVisible({ timeout: 45_000 });
  await expect(page.locator(".forecast-current-model")).toContainText("已就绪");
  await expect(page.getByText("总 WAPE", { exact: true })).toBeVisible();
  await page.getByText("日预测 · 查看各 SKU 和验证窗口", { exact: true }).click();
  await expect(page.getByText("逐期销量通过", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("训练截止", { exact: true }).first()).toBeVisible();
  await page.locator(".enterprise-run").first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/enterprise-training-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".workspace-main")).toHaveCSS("margin-left", "0px");
  await expect(page.locator(".sidebar")).toHaveCSS("width", "390px");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator(".enterprise-run").first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/enterprise-training-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "销量预测", exact: true }).last().click();
  await page.getByLabel("产品中心 SKU").fill("SAME-SKU");
  await page.getByRole("button", { name: "复杂业务预测", exact: false }).click();
  await expect(page.getByLabel("计划售价")).toBeDisabled();
  await page.getByRole("button", { name: "开始复杂业务预测", exact: true }).click();
  await expect(page.getByRole("heading", { name: "预测已完成", exact: true })).toBeVisible({ timeout: 20_000 });
  expect(errors).toEqual([]);
});

test("成熟 SKU 与三天新品一起发布，新品预测标记待验证且不生成安全库存", async ({ page }) => {
  test.setTimeout(90_000);
  const sku = `NEW-STRATA-${Date.now()}`;
  await page.goto(`${base}/#products`);
  await page.getByRole("button", { name: "新增产品", exact: true }).click();
  await page.getByLabel("产品名称", { exact: true }).fill("合成短历史新品");
  await page.getByLabel("唯一 SKU", { exact: true }).fill(sku);
  await page.getByRole("button", { name: "创建产品档案", exact: true }).click();
  await expect(page.locator(".product-fact-review")).toBeVisible();
  await page.goto(`${base}/#forecast`);
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await page.getByRole("button", { name: /标准数据建模/ }).click();
  const rows = [...records(196, "EXTERNAL-SKU"), ...records(196, sku).slice(-3).map(row => ({ ...row, sales: 3 }))];
  const filename = `${sku}.json`;
  await page.getByLabel("选择数据文件", { exact: true }).setInputFiles({
    name: filename, mimeType: "application/json", buffer: Buffer.from(JSON.stringify(rows)),
  });
  await expect(page.getByRole("heading", { name: filename, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await page.getByRole("button", { name: "确认此标准数据版本" }).click();
  await page.getByRole("combobox", { name: "训练方式", exact: true }).selectOption("rebuild");
  await page.getByRole("button", { name: "预览训练范围", exact: true }).click();
  await page.getByRole("button", { name: "启动完整重建", exact: true }).click();
  const run = page.locator(".enterprise-run").filter({ has: page.locator("header strong").filter({ hasText: filename }) });
  await expect(run.locator(".succeeded")).toBeVisible({ timeout: 45_000 });
  await run.getByText("日预测 · 查看各 SKU 和验证窗口", { exact: true }).click();
  await expect(run.getByText(/3 个历史周期 · 尚未验证精度/)).toBeVisible();
  await page.getByRole("button", { name: "销量预测", exact: true }).last().click();
  await page.getByLabel("产品中心 SKU").fill(sku);
  await page.getByRole("button", { name: "开始天预测", exact: true }).click();
  await expect(page.getByRole("heading", { name: "预测已完成" })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".forecast-output")).toContainText("近期均值");
  await expect(page.locator(".forecast-output")).toContainText("此范围尚未验证精度");
  await expect(page.locator(".forecast-output-kpis")).toContainText("待验证");
  await page.locator(".forecast-output").screenshot({ path: "test-results/enterprise-strata-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator(".forecast-output").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/enterprise-strata-mobile.png" });
});
test("历史不足的完整重建显示未达标，现用模型仍可用", async ({ page }) => {
  await page.getByRole("button", { name: "销量预测", exact: true }).first().click();
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await page.getByRole("button", { name: /标准数据建模/ }).click();
  await upload(page, "synthetic-short.json", 10);
  await page.getByRole("button", { name: "确认此标准数据版本" }).click();
  await page.getByRole("combobox", { name: "训练方式", exact: true }).selectOption("rebuild");
  await page.getByRole("button", { name: "预览训练范围", exact: true }).click();
  await page.getByRole("button", { name: "启动完整重建", exact: true }).click();
  await expect(page.locator(".enterprise-run .rejected")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".enterprise-run").first()).toContainText("历史不足");
  await expect(page.locator(".forecast-current-model")).toContainText("2025-07-20");
});
test("企业策略、事实和机会反馈通过真实API保存，刷新后可追溯", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.getByRole("button", { name: "市场洞察", exact: true }).first().click();
  await page.locator(".market-section-nav").getByRole("button", { name: /决策中心/ }).click();
  await page.getByRole("button", { name: "经营配置工作台", exact: true }).click();
  const strategy = page.locator(".enterprise-strategy");
  await strategy.getByRole("button", { name: /^需求增长/ }).click();
  await strategy.getByRole("button", { name: "保存经营策略", exact: true }).click();
  await expect(strategy.getByRole("status")).toContainText("策略已保存");
  await strategy.getByRole("tab", { name: "企业事实", exact: true }).click();
  await strategy.getByLabel("产品品类代码").fill("sofa");
  await strategy.getByLabel("出口市场代码").fill("US");
  await strategy.getByLabel("新增企业能力").selectOption("7");
  await strategy.getByRole("button", { name: "添加能力事实", exact: true }).click();
  await strategy.getByLabel("ISTA 3A 包装能力", { exact: true }).selectOption("no");
  await strategy.getByRole("button", { name: "确认并保存企业事实", exact: true }).click();
  await expect(strategy.getByRole("status")).toContainText("企业事实已确认");
  await page.getByRole("button", { name: "洞察概览", exact: true }).click();
  await page.getByRole("button", { name: "查看详情", exact: true }).first().click();
  const decision = page.locator(".opportunity-decision").first();
  await decision.getByText(/查看判断依据与处理结果/).click();
  await decision.getByRole("combobox", { name: "处理结果", exact: true }).selectOption("rejected");
  await decision.getByLabel("处理原因", { exact: true }).fill("合成验收：暂不具备指定包装能力");
  await decision.getByRole("button", { name: "保存处理结果", exact: true }).click();
  await expect(decision.getByRole("status")).toContainText("已保存");
  await page.reload();
  await page.getByRole("button", { name: "经营配置工作台", exact: true }).click();
  await expect(page.locator(".enterprise-strategy .selected")).toContainText("需求增长");
  await strategy.getByRole("tab", { name: "企业事实", exact: true }).click();
  await expect(strategy.getByLabel("产品品类代码")).toHaveValue("sofa");
  await expect(strategy.getByLabel("ISTA 3A 包装能力", { exact: true })).toHaveValue("no");
  await strategy.screenshot({ path: "test-results/enterprise-strategy-desktop.png" });
  await page.getByRole("button", { name: "洞察概览", exact: true }).click();
  await page.getByRole("button", { name: "查看详情", exact: true }).first().click();
  await page.locator(".opportunity-decision").first().getByText(/查看判断依据与处理结果/).click();
  await expect(decision.getByRole("textbox", { name: "处理原因", exact: true })).toHaveValue("合成验收：暂不具备指定包装能力");
  await decision.screenshot({ path: "test-results/enterprise-decision-desktop.png" });
});

test("产品资料候选经人工确认成为企业适配事实，画像确认后可分析", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${base}/#products`);
  await page.getByRole("button", { name: "新增产品", exact: true }).click();
  await page.getByLabel("产品名称", { exact: true }).fill("合成事实核验沙发");
  await page.getByLabel("唯一 SKU", { exact: true }).fill(`FACT-${Date.now()}`);
  await page.getByLabel("材质工艺", { exact: true }).fill("实木框架；软包");
  await page.getByRole("button", { name: "创建产品档案", exact: true }).click();
  const facts = page.locator(".product-fact-review");
  await expect(facts.getByText("资料候选").first()).toBeVisible();
  const wood = facts.getByRole("checkbox", { name: /实木/ });
  await expect(wood).not.toBeChecked();
  await wood.check();
  await facts.getByRole("checkbox", { name: /软包工艺/ }).check();
  await facts.getByLabel("事实核验依据", { exact: true }).fill("合成工程验收：依据产品规格书核验");
  await facts.getByRole("button", { name: "保存已核验事实", exact: true }).click();
  await expect(facts.getByLabel("事实核验依据", { exact: true })).toHaveValue("");
  await facts.getByRole("checkbox", { name: "我已核验当前画像的全部参数" }).check();
  await facts.getByRole("button", { name: "确认画像并启用分析", exact: true }).click();
  await expect(facts.getByRole("heading", { name: "当前画像已确认", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /使用该产品开始分析/ })).toBeEnabled();
  await page.setViewportSize({ width: 1440, height: 1500 });
  await facts.scrollIntoViewIfNeeded();
  await facts.screenshot({ path: "test-results/enterprise-product-facts-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await facts.getByRole("heading", { name: "企业适配所需事实" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/enterprise-product-facts-mobile.png" });
  expect(errors).toEqual([]);
});

test("企业订单模板、取消退款和销量对账经过真实预检，模板复用仍需确认", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${base}/#forecast`);
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await page.getByRole("button", { name: /标准数据建模/ }).click();
  const control = [{ date: "2025-01-06", sku: "SAME-SKU", site: "US", sales: 13 }];
  await page.getByLabel("选择数据文件", { exact: true }).setInputFiles({
    name: "reconciliation-control.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(control)),
  });
  await expect(page.getByRole("heading", { name: "reconciliation-control.json" })).toBeVisible();
  await page.getByRole("combobox", { name: "销量口径", exact: true }).selectOption("net_units");
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await page.getByRole("button", { name: "确认此标准数据版本" }).click();
  const first = { date: "2025-01-06", sku: "SAME-SKU", warehouse: "美西仓", sales: 10,
    order_id: "O1", line_id: "L1", order_status: "成交", refunded_units: 0, unit_price: 100, discount: 0.1 };
  const orders = [first, first, { ...first, order_id: "O2", sales: 4, order_status: "取消" },
    { ...first, order_id: "O3", sales: 5, order_status: "部分退款", refunded_units: 2, unit_price: 90 }];
  await page.getByLabel("选择数据文件", { exact: true }).setInputFiles({
    name: "erp-order-snapshot.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(orders)),
  });
  await expect(page.getByRole("heading", { name: "erp-order-snapshot.json" })).toBeVisible();
  await page.getByRole("combobox", { name: "原始粒度", exact: true }).selectOption("transactions");
  await page.getByRole("combobox", { name: "销量口径", exact: true }).selectOption("net_units");
  await page.getByText("订单、仓库与成交事实（可选）", { exact: true }).click();
  for (const [label, column] of [["仓库", "warehouse"], ["订单号", "order_id"], ["订单行号", "line_id"],
    ["订单状态", "order_status"], ["实际退货件数", "refunded_units"], ["折后成交单价", "unit_price"], ["折扣比例", "discount"]]) {
    await page.getByLabel(`映射${label}`, { exact: true }).selectOption(column);
  }
  await page.getByLabel("原始仓库站点", { exact: true }).fill("美西仓");
  await page.getByLabel("对应仓库站点", { exact: true }).fill("US");
  await page.getByRole("button", { name: "添加仓库站点", exact: true }).click();
  for (const [source, target] of [["成交", "completed"], ["取消", "cancelled"], ["部分退款", "refunded"]]) {
    await page.getByLabel("原始订单状态", { exact: true }).fill(source);
    await page.getByLabel("对应订单状态", { exact: true }).selectOption(target);
    await page.getByRole("button", { name: "添加订单状态", exact: true }).click();
  }
  await page.getByRole("combobox", { name: "重复订单行", exact: true }).selectOption("drop_identical");
  await page.getByRole("checkbox", { name: /^确认文件是覆盖整日的订单行快照/ }).check();
  await page.getByLabel("无币种列时使用", { exact: true }).fill("USD");
  await page.getByRole("checkbox", { name: /^确认单价为折后成交单价/ }).check();
  await page.getByText("关联对账表与库存（可选）", { exact: true }).click();
  const option = page.getByRole("combobox", { name: "销量对账版本", exact: true }).locator("option").filter({ hasText: "reconciliation-control.json" });
  await page.getByRole("combobox", { name: "销量对账版本", exact: true }).selectOption(await option.getAttribute("value"));
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await expect(page.getByText(/重复去除 10 件 · 取消排除 4 件 · 退货扣减 2 件/)).toBeVisible();
  await expect(page.getByText("销量对账：匹配 1 行 · 缺失 0 行 · 多余 0 行 · 差额 0 行")).toBeVisible();
  await page.getByRole("button", { name: "确认此标准数据版本" }).click();
  await page.getByLabel("企业模板名称", { exact: true }).fill("ERP整日订单测试");
  await page.getByRole("button", { name: "保存为企业模板", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("企业模板已保存");
  await page.getByLabel("选择数据文件", { exact: true }).setInputFiles({
    name: "erp-order-next.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(orders.map(row => ({ ...row, date: "2025-01-07" })))),
  });
  await expect(page.getByRole("heading", { name: "erp-order-next.json" })).toBeVisible();
  await page.getByRole("combobox", { name: "复用企业模板", exact: true }).selectOption({ label: "ERP整日订单测试 · 第 1 版" });
  await page.getByRole("button", { name: "应用导入模板", exact: true }).click();
  await expect(page.getByText(/已采用模板：ERP整日订单测试/)).toBeVisible();
  await expect(page.getByRole("button", { name: "确认此标准数据版本" })).toHaveCount(0);
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认此标准数据版本" })).toBeEnabled();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator(".enterprise-quality").screenshot({ path: "test-results/enterprise-import-reconciliation-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator(".enterprise-quality").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/enterprise-import-reconciliation-mobile.png" });
  expect(errors).toEqual([]);
});

test("采纳后记录实施与经营观察，刷新持久化并导出完整排序候选", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const today = new Date().toISOString().slice(0, 10);
  const filename = `observed-sales-${Date.now()}.json`;
  await page.goto(`${base}/#forecast`);
  await page.getByRole("button", { name: "模型训练", exact: true }).click();
  await page.getByRole("button", { name: /标准数据建模/ }).click();
  await page.getByLabel("选择数据文件", { exact: true }).setInputFiles({
    name: filename, mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify([{ date: today, sku: "SAME-SKU", site: "US", sales: 3, reference: filename }])),
  });
  await expect(page.getByRole("heading", { name: filename, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "运行质量预检", exact: true }).click();
  await page.getByRole("button", { name: "确认此标准数据版本" }).click();
  await page.goto(`${base}/#market-decisions`);
  await page.getByRole("button", { name: "查看详情", exact: true }).first().click();
  const decision = page.locator(".opportunity-decision").first();
  await decision.getByText(/查看判断依据与处理结果/).click();
  await decision.getByRole("combobox", { name: "处理结果", exact: true }).selectOption("accepted");
  await decision.getByRole("textbox", { name: "处理原因", exact: true }).fill("合成试点：以三件观察销量为目标");
  await decision.getByRole("button", { name: "保存处理结果", exact: true }).click();
  await expect(decision.getByRole("status")).toContainText("已保存");
  await decision.getByText("跟踪实施与经营结果", { exact: true }).click();
  const outcome = page.getByRole("region", { name: "实施与经营观察", exact: true });
  await outcome.getByRole("textbox", { name: "实施及结果依据", exact: true }).fill("合成试点计划与核验依据");
  await outcome.getByRole("button", { name: "保存实施与观察", exact: true }).click();
  await expect(outcome.getByRole("status")).toContainText("已保存");
  await outcome.getByRole("combobox", { name: "实施进度", exact: true }).selectOption("completed");
  for (const label of ["实际实施开始", "实际实施结束", "观察开始", "观察结束"]) {
    await outcome.getByLabel(label, { exact: true }).fill(today);
  }
  await outcome.getByRole("combobox", { name: "目标达成情况", exact: true }).selectOption("achieved");
  await outcome.getByRole("textbox", { name: "实施及结果依据", exact: true }).fill("合成报表核验：目标三件，观察期实际三件");
  await outcome.getByText("关联销量与企业财务记录（可选）", { exact: true }).click();
  const version = await outcome.locator("datalist option").filter({ hasText: filename }).getAttribute("value");
  await outcome.getByLabel("已确认销量版本", { exact: true }).fill(version);
  await outcome.getByLabel("回流来源 SKU", { exact: true }).fill("SAME-SKU");
  await outcome.getByLabel("观察期收入", { exact: true }).fill("300");
  await outcome.getByLabel("观察期费用", { exact: true }).fill("240");
  await outcome.getByLabel("财务口径与凭据", { exact: true }).fill("合成核验单：收入扣退货，费用含制造及物流");
  await outcome.getByRole("button", { name: "保存实施与观察", exact: true }).click();
  await expect(outcome.getByRole("status")).toContainText("已保存");
  await page.reload();
  await decision.getByText("跟踪实施与经营结果", { exact: true }).click();
  await expect(outcome.getByRole("combobox", { name: "目标达成情况", exact: true })).toHaveValue("achieved");
  await outcome.getByText(/第 2 版 · 已完成/).click();
  await expect(outcome.getByText(/观察销量 3 件 · 1 天/)).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1200 });
  await outcome.screenshot({ path: "test-results/enterprise-outcomes-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await outcome.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/enterprise-outcomes-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "经营配置工作台", exact: true }).click();
  await page.getByRole("tab", { name: "版本与导出", exact: true }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出排序评测数据", exact: true }).click();
  const download = await downloadPromise;
  const exported = JSON.parse(readFileSync(await download.path(), "utf8"));
  expect(exported.format_version).toBe("opportunity-ranking-v2");
  const candidate = exported.groups.flatMap(group => group.candidates).find(row => row.opportunity_id === account.opportunity_id);
  expect(candidate.labels.outcome_eligible).toBe(true);
  expect(candidate.outcome.sales_snapshot.units).toBe(3);
  expect(errors).toEqual([]);
});
