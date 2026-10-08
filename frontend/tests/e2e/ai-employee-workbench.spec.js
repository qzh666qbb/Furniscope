import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "agent-e2e" });
const goalUuid = "10000000-0000-4000-8000-000000000010";
const runUuid = "20000000-0000-4000-8000-000000000010";
const now = "2026-10-07T10:00:00Z";

const summary = {
  goal_uuid: goalUuid,
  objective: "检查最新销量数据是否满足训练条件",
  selected_skill_id: "data_readiness_check",
  skill_name: "数据就绪检查",
  status: "running",
  priority: "normal",
  progress_percent: 50,
  pending_approvals: 0,
  artifact_count: 1,
  current_run_uuid: runUuid,
  next_step_title: "汇总阻断问题",
  return_href: null,
  created_at: now,
  updated_at: now,
  completed_at: null,
};

const artifact = {
  artifact_uuid: "30000000-0000-4000-8000-000000000010",
  goal_uuid: goalUuid,
  artifact_type: "data_readiness_report",
  title: "数据就绪检查清单",
  summary: "标准数据与 SKU 身份关联已完成校验。",
  sha256: "a".repeat(64),
  open_href: "forecast?tab=training",
  created_at: now,
};

const approval = {
  approval_uuid: "40000000-0000-4000-8000-000000000010",
  goal_uuid: goalUuid,
  approval_type: "confirmed_sales_data_required",
  title: "确认标准销量数据",
  question: "请先确认一个销量数据版本。",
  reason: "仅使用不可变的已确认版本。",
  impact: { href: "forecast?tab=training" },
  options: [
    { value: "continue", label: "已处理，继续执行" },
    { value: "cancel", label: "取消目标" },
  ],
  recommended_option: "continue",
  status: "pending",
  created_at: now,
  objective: summary.objective,
};

const overview = {
  profile: {
    profile_uuid: "50000000-0000-4000-8000-000000000010",
    display_name: "经营分析员工",
    role_title: "跨境家具经营分析",
    autonomy_level: "L2",
    status: "active",
    enabled_skills: ["market_entry_assessment@1", "weekly_sales_review@1", "data_readiness_check@1"],
  },
  counts: { active: 1, waiting_human: 1, completed: 3, artifacts: 1 },
  commitments: [summary],
  approvals: [approval],
  artifacts: [artifact],
  activities: [{
    event_uuid: "60000000-0000-4000-8000-000000000010",
    goal_uuid: goalUuid,
    run_uuid: runUuid,
    sequence_no: 2,
    event_type: "step.started",
    payload: { title: "检查 SKU 身份关联" },
    created_at: now,
  }],
};

const detail = {
  ...summary,
  expected_deliverables: [{ type: "data_readiness_report", required: true }],
  constraints: {},
  acceptance_criteria: ["data_version_sha_present"],
  autonomy_envelope: { level: "L2", allowed_effect_levels: ["R0", "R1", "R2"] },
  deadline: null,
  plan: {
    plan_uuid: "70000000-0000-4000-8000-000000000010",
    version: 1,
    skill_id: "data_readiness_check",
    skill_version: 1,
    plan_sha256: "b".repeat(64),
    risk_summary: { highest_effect: "R2", requires_approval: false },
    status: "active",
    steps: [
      { step_uuid: "71000000-0000-4000-8000-000000000010", ordinal: 1, title: "读取已确认标准数据", capability_id: "forecast.data.inspect", effect_level: "R1", status: "succeeded" },
      { step_uuid: "72000000-0000-4000-8000-000000000010", ordinal: 2, title: "检查 SKU 身份关联", capability_id: "sku.mapping.inspect", effect_level: "R1", status: "running" },
      { step_uuid: "73000000-0000-4000-8000-000000000010", ordinal: 3, title: "汇总阻断问题", capability_id: "readiness.verify", effect_level: "R0", status: "pending" },
      { step_uuid: "74000000-0000-4000-8000-000000000010", ordinal: 4, title: "交付修复清单", capability_id: "artifact.deliver", effect_level: "R2", status: "pending" },
    ],
  },
  run: {
    run_uuid: runUuid,
    status: "running",
    progress_percent: 50,
    tool_call_count: 1,
    replan_count: 0,
    failure_code: null,
    failure_message: null,
    created_at: now,
    started_at: now,
    completed_at: null,
  },
  artifacts: [artifact],
  approvals: [],
  timeline: overview.activities,
  messages: [
    { message_uuid: "80000000-0000-4000-8000-000000000010", role: "user", content: summary.objective, created_at: now },
    { message_uuid: "81000000-0000-4000-8000-000000000010", role: "employee", content: "已接收目标并生成执行计划。", created_at: now },
  ],
};

const skills = [
  { skill_id: "market_entry_assessment", version: 1, display_name: "市场进入评估", description: "运行市场分析并交付报告。", deliverable_label: "决策报告", status: "active", step_count: 5 },
  { skill_id: "weekly_sales_review", version: 1, display_name: "每周销量复盘", description: "执行受控问数并交付周报。", deliverable_label: "销量周报", status: "active", step_count: 4 },
  { skill_id: "data_readiness_check", version: 1, display_name: "数据就绪检查", description: "检查数据质量与 SKU 关联。", deliverable_label: "数据就绪清单", status: "active", step_count: 4 },
];

async function mockApi(page) {
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "employee-user-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let data = { items: [], total: 0 };
    if (path === "/api/v1/users/me") {
      data = { user_id: 7, name: "何峰", email: "hefeng@example.com", role_code: "user", tenant: { tenant_id: 3, name: "和风家居" } };
    } else if (path === "/api/v1/dashboard/summary") {
      data = { products: 76, running_tasks: 1, pending_confirmation_tasks: 0, failed_tasks: 0, conflicted_products: 0, reports: 3 };
    } else if (path === "/api/v1/agent-runtime/overview") {
      data = overview;
    } else if (path === "/api/v1/agent-runtime/skills") {
      data = skills;
    } else if (path === "/api/v1/agent-runtime/capabilities") {
      data = [{ capability_id: "product.read", version: 1, display_name: "读取产品档案", description: "读取已确认产品事实。", provider: "ProductService", effect_level: "R1", permission_code: "product.read", status: "active" }];
    } else if (path === `/api/v1/agent-runtime/goals/${goalUuid}`) {
      data = detail;
    } else if (path === "/api/v1/agent-runtime/goals:draft") {
      data = {
        objective: "检查最新销量数据是否满足训练条件",
        selected_skill_id: "data_readiness_check@1",
        skill_name: "数据就绪检查",
        expected_deliverables: [{ type: "data_readiness_report", required: true }],
        constraints: {},
        acceptance_criteria: ["data_version_sha_present", "sku_mapping_status_present"],
        autonomy_envelope: { level: "L2", allowed_effect_levels: ["R0", "R1", "R2"] },
        resolved_resources: [],
        validation_issues: [{ code: "CONFIRMED_SALES_DATA_REQUIRED", message: "没有已确认的销量数据版本。", href: "forecast?tab=training", blocking: true }],
        estimated_steps: 4,
        return_href: null,
      };
    } else if (path === "/api/v1/agent-runtime/goals") {
      data = detail;
    } else if (path.endsWith("/start")) {
      data = detail;
    } else if (path.startsWith("/api/v1/agent-runtime/approvals/")) {
      data = { approval_uuid: approval.approval_uuid, status: "approved", run_uuid: runUuid };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });
}

async function expectNoOverflow(page) {
  const size = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(size.document).toBeLessThanOrEqual(size.viewport);
}

test("本期隐藏模式切换但保留 AI 员工路由", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#workspace?focus=goals");
  await expect(page.getByRole("group", { name: "工作模式" })).toHaveCount(0);
  await page.goto("/#employee");
  await expect(page.getByRole("heading", { name: "经营分析员工" })).toBeVisible();
  await expect(page.getByRole("group", { name: "工作模式" })).toHaveCount(0);
  await page.goto("/#workspace?focus=goals");
  await expect(page).toHaveURL(/#workspace\?focus=goals$/);
  await expect(page.getByRole("heading", { name: "今天要研究哪个产品？" })).toBeVisible();
});

test("AI 员工驾驶舱、目标执行台和移动端布局完整", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#employee");
  await expect(page.getByText("进行中目标")).toBeVisible();
  await expect(page.getByText("检查最新销量数据是否满足训练条件").first()).toBeVisible();
  await expect(page.getByText("数据就绪检查清单")).toBeVisible();
  await page.screenshot({ path: "test-results/ai-employee-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 1024, height: 900 });
  await expectNoOverflow(page);
  await page.screenshot({ path: "test-results/ai-employee-tablet.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.locator(".employee-goal-row").first().click();
  await expect(page).toHaveURL(new RegExp(`goal=${goalUuid}`));
  await expect(page.getByRole("heading", { name: summary.objective })).toBeVisible();
  await expect(page.locator(".employee-plan-steps").getByText("检查 SKU 身份关联", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "交付物", exact: true }).click();
  await expect(page.getByText(`SHA256 ${artifact.sha256.slice(0, 12)}`)).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".employee-sidebar")).toHaveCSS("position", "fixed");
  await expect(page.getByRole("navigation", { name: "AI 员工导航" })).toBeVisible();
  await expectNoOverflow(page);
  await page.screenshot({ path: "test-results/ai-employee-mobile.png", fullPage: true });
});

test("委派目标先生成计划再确认启动", async ({ page }) => {
  await mockApi(page);
  await page.goto("/#employee");
  await page.getByRole("button", { name: "委派目标" }).click();
  await page.getByPlaceholder("例如：检查最新销量数据是否已满足训练条件").fill("检查最新销量数据是否满足训练条件");
  await page.locator(".employee-skill-picker").getByRole("button", { name: /数据就绪检查/ }).click();
  await page.getByRole("button", { name: "生成计划" }).click();
  await expect(page.getByRole("heading", { name: "确认执行计划" })).toBeVisible();
  await expect(page.getByText("没有已确认的销量数据版本。")).toBeVisible();
  await page.getByRole("button", { name: "确认并启动" }).click();
  await expect(page).toHaveURL(new RegExp(`goal=${goalUuid}`));
});
