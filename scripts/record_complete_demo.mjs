import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const BASE_URL = (process.env.FURNISCOPE_DEMO_BASE_URL || "http://127.0.0.1:4173").replace(/\/$/, "");
const OUTPUT_ROOT = path.resolve(
  process.env.FURNISCOPE_DEMO_OUTPUT || path.join(ROOT, "artifacts", "demo-video"),
);
const USER_EMAIL = process.env.FURNISCOPE_DEMO_USER_EMAIL;
const USER_PASSWORD = process.env.FURNISCOPE_DEMO_USER_PASSWORD;
const ADMIN_EMAIL = process.env.FURNISCOPE_DEMO_ADMIN_EMAIL;
const ADMIN_PASSWORD = process.env.FURNISCOPE_DEMO_ADMIN_PASSWORD;
const FFMPEG_PATH = process.env.FFMPEG_PATH || "";
const PACE = Math.max(0.4, Number(process.env.FURNISCOPE_DEMO_PACE || "1"));
const WIDTH = 1920;
const HEIGHT = 1080;

for (const [name, value] of Object.entries({
  FURNISCOPE_DEMO_USER_EMAIL: USER_EMAIL,
  FURNISCOPE_DEMO_USER_PASSWORD: USER_PASSWORD,
  FURNISCOPE_DEMO_ADMIN_EMAIL: ADMIN_EMAIL,
  FURNISCOPE_DEMO_ADMIN_PASSWORD: ADMIN_PASSWORD,
})) {
  if (!value) throw new Error(`${name} is required`);
}

const runId = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "");
const runDir = path.join(OUTPUT_ROOT, runId);
const rawDir = path.join(runDir, "raw");
fs.mkdirSync(rawDir, { recursive: true });

const sleep = (milliseconds) => new Promise((resolve) => {
  setTimeout(resolve, Math.round(milliseconds * PACE));
});
const chapterEntries = [];
const failures = [];
const browserErrors = [];
const startedAt = Date.now();
let recordingStartedAt = startedAt;

async function login(endpoint, email, password) {
  const response = await fetch(`${BASE_URL}${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok || !body?.data?.access_token || !body?.data?.refresh_token) {
    throw new Error(body?.error?.message || `Login failed: HTTP ${response.status}`);
  }
  return body.data;
}

async function installPresentationLayer(page) {
  await page.evaluate(() => {
    if (!document.getElementById("furniscope-demo-style")) {
      const style = document.createElement("style");
      style.id = "furniscope-demo-style";
      style.textContent = `
        #furniscope-demo-cursor {
          position: fixed; left: 24px; top: 24px; width: 22px; height: 22px;
          border: 3px solid #ef4444; border-radius: 50%; z-index: 2147483647;
          pointer-events: none; transform: translate(-50%, -50%);
          box-shadow: 0 0 0 5px rgba(239, 68, 68, .17);
          transition: width .15s ease, height .15s ease, box-shadow .15s ease;
        }
        #furniscope-demo-cursor.active {
          width: 34px; height: 34px; box-shadow: 0 0 0 8px rgba(239, 68, 68, .22);
        }
        #furniscope-demo-chapter {
          position: fixed; inset: 0; z-index: 2147483646; pointer-events: none;
          display: grid; place-items: center; background: rgba(8, 14, 24, .91);
          color: #fff; opacity: 1; transition: opacity .55s ease;
          font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
        }
        #furniscope-demo-chapter.hide { opacity: 0; }
        #furniscope-demo-chapter .inner { width: min(1120px, 78vw); }
        #furniscope-demo-chapter .eyebrow {
          display: block; margin-bottom: 24px; color: #67e8f9; font-size: 24px;
          font-weight: 700; letter-spacing: 0;
        }
        #furniscope-demo-chapter h1 {
          margin: 0; color: #fff; font-size: 66px; line-height: 1.13;
          font-weight: 760; letter-spacing: 0;
        }
        #furniscope-demo-chapter p {
          margin: 28px 0 0; color: #d7e2ee; font-size: 30px; line-height: 1.55;
          letter-spacing: 0;
        }
        #furniscope-demo-note {
          position: fixed; right: 36px; bottom: 34px; z-index: 2147483645;
          max-width: 680px; padding: 16px 22px; border-left: 5px solid #06b6d4;
          background: rgba(8, 14, 24, .92); color: #fff; border-radius: 5px;
          box-shadow: 0 16px 44px rgba(0, 0, 0, .24); pointer-events: none;
          font: 600 20px/1.45 Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
          opacity: 1; transition: opacity .4s ease;
        }
        #furniscope-demo-note.hide { opacity: 0; }
        .furniscope-demo-focus {
          outline: 5px solid rgba(6, 182, 212, .9) !important;
          outline-offset: 5px !important;
          box-shadow: 0 0 0 10px rgba(6, 182, 212, .12) !important;
          transition: outline-color .2s ease, box-shadow .2s ease !important;
        }
      `;
      document.head.appendChild(style);
    }
    if (!document.getElementById("furniscope-demo-cursor")) {
      const cursor = document.createElement("div");
      cursor.id = "furniscope-demo-cursor";
      document.body.appendChild(cursor);
      document.addEventListener("mousemove", (event) => {
        cursor.style.left = `${event.clientX}px`;
        cursor.style.top = `${event.clientY}px`;
      });
      document.addEventListener("mousedown", () => cursor.classList.add("active"));
      document.addEventListener("mouseup", () => cursor.classList.remove("active"));
    }
  });
}

async function showChapter(page, title, subtitle, duration = 3000) {
  chapterEntries.push({
    at_seconds: Number(((Date.now() - recordingStartedAt) / 1000).toFixed(1)),
    title,
    subtitle,
  });
  await page.evaluate(({ titleText, subtitleText }) => {
    document.getElementById("furniscope-demo-chapter")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "furniscope-demo-chapter";
    const inner = document.createElement("div");
    inner.className = "inner";
    const eyebrow = document.createElement("span");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "FURNISCOPE · 跨境家具超级 AI 员工";
    const heading = document.createElement("h1");
    heading.textContent = titleText;
    const copy = document.createElement("p");
    copy.textContent = subtitleText;
    inner.append(eyebrow, heading, copy);
    overlay.appendChild(inner);
    document.body.appendChild(overlay);
  }, { titleText: title, subtitleText: subtitle });
  await sleep(duration);
  await page.evaluate(() => document.getElementById("furniscope-demo-chapter")?.classList.add("hide"));
  await sleep(650);
  await page.evaluate(() => document.getElementById("furniscope-demo-chapter")?.remove());
}

async function showPrivacyCover(page, title, subtitle) {
  chapterEntries.push({
    at_seconds: Number(((Date.now() - recordingStartedAt) / 1000).toFixed(1)),
    title,
    subtitle,
  });
  await page.evaluate(({ titleText, subtitleText }) => {
    document.getElementById("furniscope-demo-chapter")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "furniscope-demo-chapter";
    overlay.style.background = "#08101a";
    const inner = document.createElement("div");
    inner.className = "inner";
    const eyebrow = document.createElement("span");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "FURNISCOPE · 跨境家具超级 AI 员工";
    const heading = document.createElement("h1");
    heading.textContent = titleText;
    const copy = document.createElement("p");
    copy.textContent = subtitleText;
    inner.append(eyebrow, heading, copy);
    overlay.appendChild(inner);
    document.body.appendChild(overlay);
  }, { titleText: title, subtitleText: subtitle });
  await sleep(1400);
}

async function hidePrivacyCover(page) {
  await page.evaluate(() => document.getElementById("furniscope-demo-chapter")?.classList.add("hide"));
  await sleep(650);
  await page.evaluate(() => document.getElementById("furniscope-demo-chapter")?.remove());
}

async function showNote(page, text, duration = 2300) {
  await page.evaluate((value) => {
    document.getElementById("furniscope-demo-note")?.remove();
    const note = document.createElement("div");
    note.id = "furniscope-demo-note";
    note.textContent = value;
    document.body.appendChild(note);
  }, text);
  await sleep(duration);
  await page.evaluate(() => document.getElementById("furniscope-demo-note")?.classList.add("hide"));
  await sleep(450);
  await page.evaluate(() => document.getElementById("furniscope-demo-note")?.remove());
}

async function settle(page, expectedText) {
  if (expectedText) {
    await page.getByText(expectedText, { exact: false }).first().waitFor({ state: "visible", timeout: 15_000 });
  }
  await page.waitForFunction(() => {
    const busy = document.querySelector(".route-loading, .intelligence-loading, .kb-loading");
    return !busy || getComputedStyle(busy).display === "none";
  }, null, { timeout: 10_000 }).catch(() => {});
  await page.waitForFunction(() => (
    [...document.images].filter((image) => image.getBoundingClientRect().width > 0).every((image) => image.complete)
  ), null, { timeout: 8_000 }).catch(() => {});
  await installPresentationLayer(page);
  await sleep(1000);
}

async function openRoute(page, hash, expectedText) {
  await page.goto(`${BASE_URL}/#${hash}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await settle(page, expectedText);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
}

async function moveTo(page, locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  if (!box) return;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 18 });
  await sleep(500);
}

async function click(page, locator, options = {}) {
  await moveTo(page, locator);
  await locator.click({ timeout: 10_000, ...options });
  await sleep(1100);
}

async function focus(page, locator, duration = 2200) {
  await locator.scrollIntoViewIfNeeded();
  await moveTo(page, locator);
  await locator.evaluate((element) => element.classList.add("furniscope-demo-focus"));
  await sleep(duration);
  await locator.evaluate((element) => element.classList.remove("furniscope-demo-focus")).catch(() => {});
}

async function scrollViewport(page, ratio = 0.75) {
  await page.evaluate((amount) => {
    const candidates = [
      document.scrollingElement,
      document.querySelector(".workspace-main"),
      document.querySelector(".market-module-content"),
    ].filter(Boolean);
    const target = candidates.find((item) => item.scrollHeight > item.clientHeight + 40) || document.scrollingElement;
    target.scrollBy({ top: Math.round(target.clientHeight * amount), behavior: "smooth" });
  }, ratio);
  await sleep(1800);
}

async function safeStep(label, action) {
  try {
    await action();
    console.log(`done: ${label}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    failures.push({ label, message });
    console.warn(`skipped: ${label}: ${message}`);
  }
}

const userSession = await login("/api/v1/auth/login", USER_EMAIL, USER_PASSWORD);
const browser = await chromium.launch({
  headless: true,
  args: ["--hide-scrollbars", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  locale: "zh-CN",
  viewport: { width: WIDTH, height: HEIGHT },
  recordVideo: { dir: rawDir, size: { width: WIDTH, height: HEIGHT } },
});
await context.addInitScript(({ accessToken, refreshToken }) => {
  sessionStorage.setItem("furniscope-access-token", accessToken);
  sessionStorage.setItem("furniscope-refresh-token", refreshToken);
}, {
  accessToken: userSession.access_token,
  refreshToken: userSession.refresh_token,
});

const page = await context.newPage();
recordingStartedAt = Date.now();
page.setDefaultTimeout(12_000);
page.on("console", (message) => {
  if (message.type() === "error") browserErrors.push({ type: "console", message: message.text() });
});
page.on("response", (response) => {
  if (response.status() >= 400 && !response.url().includes("/favicon")) {
    browserErrors.push({ type: "http", status: response.status(), url: response.url() });
  }
});
const video = page.video();

await safeStep("开场", async () => {
  await openRoute(page, "workspace", "今天要研究哪个产品");
  await showChapter(
    page,
    "FurniScope 完整项目演示",
    "从企业产品资产出发，贯通 AI 分析、销量预测、市场决策、经营回流与平台治理",
    4700,
  );
});

await safeStep("企业首页", async () => {
  await openRoute(page, "workspace", "今天要研究哪个产品");
  await showChapter(page, "01 企业经营总览", "76 个产品资产、分析任务与决策报告统一汇总");
  await focus(page, page.locator(".metrics"), 2600);
  await focus(page, page.getByRole("heading", { name: "AI 工作流任务" }).locator(".."), 2400);
});

await safeStep("产品中心", async () => {
  await openRoute(page, "products", "产品中心");
  await showChapter(page, "02 产品中心", "Excel 批量导入、AI 识别、主档一致性校验与人工确认");
  await focus(page, page.locator(".product-record-card").first(), 2200);
  const preview = page.getByRole("button", { name: /快速预览/ }).first();
  if (await preview.isVisible()) {
    await click(page, preview);
    await showNote(page, "快速预览汇总产品状态、制造属性与分析就绪情况");
    await click(page, page.getByRole("button", { name: "关闭快速预览" }));
  }
  await click(page, page.getByRole("button", { name: "查看档案" }).first());
  await settle(page, "经营档案");
  await showNote(page, "产品经营档案保留版本、事实来源、确认状态与冲突处理记录");
  await scrollViewport(page, 0.8);
  await scrollViewport(page, 0.8);
});

await safeStep("AI 工作台", async () => {
  await openRoute(page, "analysis", "AI 工作台");
  await showChapter(page, "03 AI 分析工作台", "产品、授权市场数据与企业知识库共同约束分析范围");
  await focus(page, page.getByRole("heading", { name: "AI 工作台" }).locator(".."), 2200);
  const enter = page.getByRole("button", { name: /进入分析工作台/ }).first();
  if (await enter.isVisible()) {
    await click(page, enter);
    await page.getByRole("heading", { name: "新建分析工作台" }).waitFor({ timeout: 10_000 });
    await showNote(page, "可配置分析目标、数据范围和交付终点，关键节点支持人工确认");
  }
  await openRoute(page, "work-diary", "工作");
  await focus(page, page.locator("article, .work-diary-item, .task-card").first(), 2400);
});

await safeStep("销量预测", async () => {
  await openRoute(page, "forecast", "商品销量预测");
  await showChapter(page, "04 企业销量预测", "按 SKU 与站点输出区间、安全库存、生产建议和可复核依据");
  await focus(page, page.locator(".forecast-current-model"), 2200);
  await click(page, page.getByRole("button", { name: /周预测/ }).first());
  await click(page, page.getByRole("button", { name: /复杂业务预测/ }).first());
  await showNote(page, "普通预测与复杂业务预测分离，历史不足时可设置新品基准或参考 SKU");
  await click(page, page.getByRole("button", { name: "历史预测记录" }));
  await settle(page, "预测任务");
  const openResult = page.getByRole("button", { name: "查看预测结果" }).first();
  if (await openResult.isEnabled().catch(() => false)) {
    await click(page, openResult);
    await showNote(page, "历史任务冻结预测点、区间、可靠度和导出结果");
    await click(page, page.getByRole("button", { name: "关闭预测结果" }));
  }
  await click(page, page.getByRole("button", { name: "模型训练" }));
  await settle(page, "快速追加销量数据");
  await click(page, page.getByRole("button", { name: /标准数据建模/ }));
  await settle(page, "从原始数据到可验证的模型");
  await showNote(page, "标准数据经过清洗、确认、SKU 对照与滚动评测后才允许原子发布");
  await scrollViewport(page, 0.7);
});

await safeStep("市场洞察首页", async () => {
  await openRoute(page, "insights", "市场洞察");
  await showChapter(page, "05 市场洞察", "五项市场决策能力共享同一授权数据范围与证据口径");
  await focus(page, page.locator(".market-home-capabilities"), 2700);
  await focus(page, page.locator(".market-home-data-health"), 2200);
});

await safeStep("智能选品与经营配置", async () => {
  await openRoute(page, "market-decisions?capability=selection&view=overview", "市场决策中心");
  await showChapter(page, "05.1 AI 智能选品", "需求主题、市场原分与企业适配共同形成可解释机会排序");
  await focus(page, page.locator(".selection-list article").first(), 2400);
  const detail = page.getByRole("button", { name: /查看详情/ }).first();
  if (await detail.isVisible()) {
    await click(page, detail);
    await settle(page, "企业条件判断");
    await showNote(page, "机会详情可记录采纳、拒绝或待验证，并继续回流打样与经营结果");
    const decision = page.getByText("查看判断依据与处理结果", { exact: false }).first();
    if (await decision.isVisible()) await click(page, decision.locator(".."));
    await scrollViewport(page, 0.55);
  }
  await openRoute(page, "market-decisions?capability=selection&view=workbench", "经营决策配置");
  await showNote(page, "经营配置把通用市场结论修正为企业可执行的排序与准入判断");
  for (const tabName of ["企业事实", "准入条件", "版本与导出", "排序策略"]) {
    const tab = page.getByRole("tab", { name: tabName });
    if (await tab.isVisible()) {
      await click(page, tab);
      await focus(page, page.locator('[role="tabpanel"], .enterprise-strategy-panel, .enterprise-strategy-templates').first(), 1200);
    }
  }
});

await safeStep("竞品、评论、定价与合规", async () => {
  await openRoute(page, "market-decisions?capability=competitors&view=overview", "竞品动态追踪");
  await showChapter(page, "05.2 竞品与评论证据", "监测 Listing、价格变化与评论需求主题，并保留原文证据");
  await focus(page, page.locator(".competitor-evidence, .evidence-list, article").first(), 2200);
  await openRoute(page, "market-decisions?capability=competitors&view=prices", "竞品");
  await showNote(page, "竞品工作台记录价格快照、Listing 变化和持续监测节奏");
  await scrollViewport(page, 0.65);
  await openRoute(page, "market-decisions?capability=reviews&view=overview", "评论");
  await focus(page, page.locator(".review-evidence, .evidence-list, article").first(), 2200);
  await openRoute(page, "market-decisions?capability=reviews&view=stream", "舆情");
  await showNote(page, "评论舆情支持按来源、情感与关键词筛选，定位可回溯的需求证据");
  await openRoute(page, "market-decisions?capability=pricing&view=overview", "智能定价");
  await showChapter(page, "05.3 定价与跨境合规", "市场价格带、企业成本和毛利护栏共同约束建议动作");
  await focus(page, page.locator(".pricing-result"), 2600);
  await scrollViewport(page, 0.72);
  await openRoute(page, "market-decisions?capability=compliance&view=overview", "跨境合规");
  await showNote(page, "合规工作台区分官方来源、风险等级和待核验事项");
  await scrollViewport(page, 0.65);
});

await safeStep("市场数据资产", async () => {
  await openRoute(page, "market-data", "市场数据");
  await showChapter(page, "06 数据资产", "授权数据集、清洗质量、版本与原始 Listing/评论统一管理");
  await focus(page, page.locator(".dataset-table tbody tr").first(), 2300);
  const datasetDetail = page.getByRole("button", { name: "详情" }).first();
  if (await datasetDetail.isVisible()) {
    await click(page, datasetDetail);
    await settle(page, "竞品商品");
    await showNote(page, "数据集详情保留商品与评论原文，可用于 AI 分析和报告证据溯源");
    const reviews = page.getByRole("button", { name: /海外评论/ }).first();
    if (await reviews.isVisible()) await click(page, reviews);
  }
});

await safeStep("自动化投递", async () => {
  await openRoute(page, "market-automation", "自动化投递");
  await showChapter(page, "07 自动化投递", "把竞品变化和政策预警投递到企业现有协作渠道");
  await focus(page, page.locator(".notification-channel-list"), 2300);
  const records = page.getByText("最近投递记录", { exact: true });
  if (await records.isVisible()) {
    await click(page, records);
    const event = page.locator(".notification-events button").first();
    if (await event.isVisible()) {
      await click(page, event);
      await showNote(page, "每次投递保留渠道、状态、尝试次数、错误原因和送达时间");
      await click(page, page.getByRole("button", { name: "关闭" }).last());
    }
  }
});

await safeStep("决策报告", async () => {
  await openRoute(page, "report", "决策报告");
  await showChapter(page, "08 决策报告与经营闭环", "结论、反证、模型版本、数据范围和原始证据冻结归档");
  await focus(page, page.locator(".asset-list article").first(), 2400);
  await click(page, page.getByRole("button", { name: "查看详情" }).first());
  await settle(page, "报告目录");
  await showNote(page, "报告中的评分和数字由确定性程序计算，叙述不能覆盖事实口径");
  for (const heading of ["价格证据快照", "工程与制造建议", "证据目录"]) {
    const target = page.getByText(heading, { exact: false }).first();
    if (await target.isVisible().catch(() => false)) await focus(page, target.locator(".."), 1800);
  }
  await scrollViewport(page, 0.8);
  await scrollViewport(page, 0.8);
  await scrollViewport(page, 0.8);
});

await safeStep("企业知识库", async () => {
  await openRoute(page, "knowledge", "企业知识库");
  await showChapter(page, "09 企业知识库", "私有资料版本化、索引验真、无证据拒答与 Citation 溯源");
  await focus(page, page.locator(".kb-library-list"), 2000);
  await focus(page, page.locator(".kb-retrieval"), 2200);
  const preview = page.getByRole("button", { name: "快速预览" }).first();
  if (await preview.isVisible()) {
    await click(page, preview);
    await showNote(page, "预览与全屏详情展示文档版本、工作表内容和索引状态");
    const closePreview = page.getByTitle("关闭快速预览");
    if (await closePreview.isVisible()) await click(page, closePreview);
  }
  const detail = page.getByRole("button", { name: /查看详情/ }).first();
  if (await detail.isVisible()) {
    await click(page, detail);
    await settle(page, "返回知识库");
    await scrollViewport(page, 0.65);
  }
});

await safeStep("平台管理端", async () => {
  await openRoute(page, "admin-login", "管理员登录");
  await showPrivacyCover(page, "10 平台管理端", "企业租户、账号状态与企业私有预测模型隔离管理");
  await page.getByLabel("管理员账号").fill(ADMIN_EMAIL);
  await page.getByLabel("密码", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "安全登录" }).click();
  await page.getByText("平台控制中心", { exact: true }).first().waitFor({ timeout: 15_000 });
  await sleep(900);
  await installPresentationLayer(page);
  await page.getByPlaceholder("搜索企业、租户编码或邮箱").fill("HeFeng");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByText("HeFeng", { exact: true }).first().waitFor({ timeout: 10_000 });
  await hidePrivacyCover(page);
  await focus(page, page.locator(".control-kpis"), 2200);
  await focus(page, page.locator(".control-table").first(), 2300);
  await showPrivacyCover(page, "10.1 企业私有模型", "训练、评测、发布、历史版本与回滚均按租户隔离");
  await page.getByRole("button", { name: "预测模型" }).click();
  await settle(page, "企业模型");
  const hefengModel = page.locator(".tenant-model-list button").filter({ hasText: "HeFeng" }).first();
  if (await hefengModel.isVisible()) await hefengModel.click();
  await page.getByText("租户私有模型", { exact: true }).waitFor({ timeout: 12_000 });
  await page.addStyleTag({
    content: `
      .model-control-grid { grid-template-columns: minmax(0, 1fr) !important; }
      .tenant-model-list { display: none !important; }
    `,
  });
  await sleep(900);
  await hidePrivacyCover(page);
  await showNote(page, "共享基础模型与企业私有模型分层管理，发布、历史版本与回滚均可审计");
  await focus(page, page.locator(".model-detail"), 2600);
  await scrollViewport(page, 0.55);
});

await safeStep("片尾", async () => {
  await showChapter(
    page,
    "从证据到经营结果的持续闭环",
    "分析 → 决策 → 执行 → 经营观察 → 企业知识沉淀 → 再分析",
    5200,
  );
});

const recordingFinishedAt = Date.now();
await context.close();
await browser.close();

const rawPath = await video.path();
const rawOutput = path.join(runDir, `FurniScope-完整项目演示-${runId}.webm`);
fs.renameSync(rawPath, rawOutput);

let mp4Output = null;
if (FFMPEG_PATH && fs.existsSync(FFMPEG_PATH)) {
  mp4Output = path.join(runDir, `FurniScope-完整项目演示-${runId}.mp4`);
  const conversion = spawnSync(FFMPEG_PATH, [
    "-y",
    "-i", rawOutput,
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    mp4Output,
  ], { encoding: "utf8" });
  if (conversion.status !== 0) {
    failures.push({
      label: "MP4 转码",
      message: conversion.stderr?.slice(-2000) || `ffmpeg exited with ${conversion.status}`,
    });
    mp4Output = null;
  }
}

const manifest = {
  generated_at: new Date().toISOString(),
  base_url: BASE_URL,
  duration_seconds: Number(((recordingFinishedAt - recordingStartedAt) / 1000).toFixed(1)),
  resolution: `${WIDTH}x${HEIGHT}`,
  raw_video: rawOutput,
  mp4_video: mp4Output,
  chapters: chapterEntries,
  skipped_steps: failures,
  browser_errors: browserErrors,
};
fs.writeFileSync(
  path.join(runDir, "manifest.json"),
  `${JSON.stringify(manifest, null, 2)}\n`,
);

console.log(JSON.stringify(manifest, null, 2));
