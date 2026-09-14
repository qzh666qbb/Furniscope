import { expect, test } from "@playwright/test";

test("HTTP 内网环境缺少 randomUUID 时仍可新增企业用户", async ({ page }) => {
  let submittedKey = "";
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "admin-contract-token");
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: undefined,
    });
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let data = { items: [], total: 0 };
    if (path === "/api/v1/users/me") {
      data = { user_id: 1, name: "平台管理员", role_code: "admin" };
    } else if (path === "/api/v1/admin/enterprise-users" && request.method() === "POST") {
      submittedKey = request.headers()["idempotency-key"] || "";
      data = { tenant_id: 9, enterprise_name: "兼容性测试企业" };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, data }),
    });
  });

  await page.goto("/#admin");
  await page.getByRole("button", { name: "开通企业用户" }).click();
  await page.getByLabel("企业名称").fill("兼容性测试企业");
  await page.getByLabel("租户编码").fill("UUID_FALLBACK");
  await page.getByLabel("联系人").fill("测试联系人");
  await page.getByLabel("登录邮箱").fill("uuid-fallback@example.com");
  await page.getByLabel("初始密码").fill("Contract-pass-2026");
  await page.getByRole("button", { name: "开通企业用户", exact: true }).last().click();

  await expect.poll(() => submittedKey).toMatch(
    /^enterprise-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  await expect(page.getByRole("heading", { name: "开通企业用户" })).toHaveCount(0);
});
