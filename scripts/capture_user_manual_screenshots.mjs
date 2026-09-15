import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:8083";
const OUT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../Docs/参赛提交/screenshots");
const tokens = JSON.parse(fs.readFileSync("/tmp/manual-tokens.json", "utf8"));
const IDS = {
  product: "1",
  dataset: "2",
  report: "04e05414-4225-440b-8901-bca12e0096b0",
  workspace: "499f3cb1-acfb-413d-978f-52bd2da5a6ef",
};

fs.mkdirSync(OUT, { recursive: true });

async function shot(page, name) {
  await page.waitForTimeout(700);
  await page.screenshot({
    path: path.join(OUT, `${name}.png`),
    fullPage: true,
    animations: "disabled",
  });
  console.log("saved", name);
}

async function open(context, hash) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${BASE}/#${hash}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  return page;
}

async function withSession(role) {
  const browser = await chromium.launch({ args: ["--hide-scrollbars"] });
  const context = await browser.newContext({
    locale: "zh-CN",
    viewport: { width: 1440, height: 900 },
  });
  const session = tokens[role];
  await context.addInitScript(({ access, refresh }) => {
    sessionStorage.setItem("furniscope-access-token", access);
    sessionStorage.setItem("furniscope-refresh-token", refresh);
  }, { access: session.access_token, refresh: session.refresh_token });
  return { browser, context };
}

async function publicPages() {
  const browser = await chromium.launch({ args: ["--hide-scrollbars"] });
  const context = await browser.newContext({
    locale: "zh-CN",
    viewport: { width: 1440, height: 900 },
  });
  const login = await open(context, "login");
  await login.getByRole("heading", { name: /登录|企业/ }).first().waitFor({ timeout: 15000 }).catch(() => {});
  await shot(login, "01-login");
  const register = await open(context, "register");
  await register.getByRole("heading").first().waitFor();
  await shot(register, "02-register");
  const adminLogin = await open(context, "admin-login");
  await adminLogin.getByRole("heading").first().waitFor();
  await shot(adminLogin, "03-admin-login");
  await browser.close();
}

async function userPages() {
  const { browser, context } = await withSession("user");
  const home = await open(context, "workspace");
  await home.getByRole("heading").first().waitFor({ timeout: 20000 });
  await shot(home, "04-home");

  const products = await open(context, "products");
  await products.locator(".product-record-card").first().waitFor({ timeout: 20000 });
  await shot(products, "05-products");
  await products.getByRole("button", { name: "新增产品" }).click();
  await products.locator(".product-modal-backdrop").waitFor({ timeout: 8000 });
  await shot(products, "06-product-create");
  await products.getByRole("button", { name: "取消" }).click();
  await products.locator(".product-modal-backdrop").waitFor({ state: "detached", timeout: 8000 }).catch(() => {});

  const detail = await open(context, `products?product=${IDS.product}`);
  await detail.locator(".product-drawer-backdrop, .product-record-card").first().waitFor({ timeout: 20000 });
  await shot(detail, "07-product-detail");

  const analysis = await open(context, "analysis");
  await analysis.getByRole("heading", { name: "AI 工作台" }).waitFor({ timeout: 20000 });
  await shot(analysis, "08-analysis-home");
  await analysis.getByRole("button", { name: /进入分析工作台/ }).click();
  await analysis.getByRole("heading", { name: "新建分析工作台" }).waitFor({ timeout: 10000 });
  await shot(analysis, "09-analysis-launcher");

  const workflow = await open(context, `workflow?workspace=${IDS.workspace}`);
  await workflow.waitForTimeout(1500);
  await shot(workflow, "10-workflow");

  const diary = await open(context, "work-diary");
  await diary.getByRole("heading").first().waitFor({ timeout: 20000 });
  await shot(diary, "11-work-diary");

  const forecast = await open(context, "forecast");
  await forecast.getByRole("heading", { name: "商品销量预测" }).waitFor({ timeout: 20000 });
  await shot(forecast, "12-forecast");
  await forecast.getByRole("button", { name: "历史预测记录" }).click();
  await forecast.waitForTimeout(800);
  await shot(forecast, "13-forecast-history");
  await forecast.getByRole("button", { name: "模型训练" }).click();
  await forecast.waitForTimeout(800);
  await shot(forecast, "14-forecast-training");

  const insights = await open(context, "insights");
  await insights.getByRole("heading", { name: "市场洞察" }).waitFor({ timeout: 20000 });
  await shot(insights, "15-insights");
  await insights.getByRole("button", { name: "导入竞品与评论数据" }).click();
  await insights.waitForTimeout(700);
  await shot(insights, "16-dataset-import");
  await insights.keyboard.press("Escape");

  const dataset = await open(context, `dataset-detail?id=${IDS.dataset}`);
  await dataset.waitForTimeout(1200);
  await shot(dataset, "17-dataset-detail");

  const prices = await open(context, "competitor-tracking?tab=prices");
  await prices.getByRole("heading", { name: /竞品分析/ }).waitFor({ timeout: 20000 });
  await shot(prices, "18-competitor");
  const stream = await open(context, "competitor-tracking?tab=stream");
  await stream.waitForTimeout(1000);
  await shot(stream, "19-sentiment");

  const reports = await open(context, "report");
  await reports.getByRole("heading", { name: "决策报告" }).waitFor({ timeout: 20000 });
  await shot(reports, "20-reports");
  const report = await open(context, `report-detail?id=${IDS.report}&from=report`);
  await report.waitForTimeout(1500);
  await shot(report, "21-report-detail");

  await browser.close();
}

async function adminPages() {
  const { browser, context } = await withSession("admin");
  const admin = await open(context, "admin");
  await admin.getByText("平台控制中心").waitFor({ timeout: 20000 });
  await shot(admin, "22-admin-enterprises");
  await admin.getByRole("button", { name: "预测模型" }).click();
  await admin.waitForTimeout(900);
  await shot(admin, "23-admin-models");
  await browser.close();
}

await publicPages();
await userPages();
await adminPages();
console.log("done");
