import { expect, test } from "@playwright/test";

test("登录状态失效后清理会话并返回登录页", async ({ page }) => {
  let currentUserRequests = 0;
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "expired-access-token");
    sessionStorage.setItem("furniscope-auth", "1");
  });
  await page.route("**/api/v1/users/me", async (route) => {
    currentUserRequests += 1;
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        success: false,
        error: { message: "登录已过期，请重新登录" },
      }),
    });
  });

  await page.goto("/#products");

  await expect(page.getByRole("heading", { name: "登录 FurniScope" })).toBeVisible();
  await expect.poll(() => currentUserRequests).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(() => ({
        access: sessionStorage.getItem("furniscope-access-token"),
        legacy: sessionStorage.getItem("furniscope-auth"),
        hash: location.hash,
      })),
    )
    .toEqual({ access: null, legacy: null, hash: "#login" });
});

test("管理员模型接口返回 401 时安全卸载并返回登录页", async ({ page }) => {
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.addInitScript(() => {
    sessionStorage.setItem("furniscope-access-token", "expired-admin-token");
  });
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: { user_id: 1, name: "平台管理员", role_code: "admin" },
      }),
    }),
  );
  await page.route("**/api/v1/admin/enterprise-users**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, data: { items: [], total: 0 } }),
    }),
  );
  await page.route("**/api/v1/admin/registration-applications**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, data: { items: [], total: 0 } }),
    }),
  );
  await page.route("**/api/v1/admin/forecast-models", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        success: false,
        error: { message: "登录已过期，请重新登录" },
      }),
    }),
  );

  await page.goto("/#admin");
  await page.getByRole("button", { name: "预测模型" }).click();

  await expect(page.getByRole("heading", { name: "登录 FurniScope" })).toBeVisible();
  await expect.poll(() => pageErrors).toEqual([]);
  await expect.poll(() => page.evaluate(() => location.hash)).toBe("#login");
});
