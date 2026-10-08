import { expect, test } from "@playwright/test";

test("关闭企业租户需要确认并携带资源版本", async ({ page }) => {
  let deleteRequest = null;
  let deleted = false;
  const enterprise = {
    tenant_id: 12,
    tenant_code: "DELETE_DEMO",
    enterprise_name: "删除流程测试企业",
    tenant_status: "active",
    entitlements: ["sales_forecast"],
    resource_version: "tenant-version-12",
    user_id: 21,
    contact_name: "测试联系人",
    email: "delete-contract@example.invalid",
    user_status: "active",
    sku_count: 8,
  };
  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "admin-token"));
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let data = { items: [], total: 0 };
    if (path === "/api/v1/users/me") {
      data = { user_id: 1, name: "平台管理员", role_code: "admin" };
    } else if (path === `/api/v1/admin/enterprise-users/${enterprise.tenant_id}` && request.method() === "DELETE") {
      deleteRequest = { method: request.method(), ifMatch: request.headers()["if-match"] };
      deleted = true;
      data = { tenant_id: enterprise.tenant_id, tenant_status: "closed", deleted: true };
    } else if (path === "/api/v1/admin/enterprise-users") {
      data = { items: deleted ? [] : [enterprise], total: deleted ? 0 : 1 };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, data }),
    });
  });

  await page.goto("/#admin");
  await expect(page.locator(".control-table").getByText("删除流程测试企业")).toBeVisible();
  await page.getByRole("button", { name: "关闭企业", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "关闭企业租户？" });
  await expect(dialog).toContainText("业务数据与审计记录会保留");
  await dialog.getByRole("button", { name: "确认关闭" }).click();

  await expect.poll(() => deleteRequest).toEqual({ method: "DELETE", ifMatch: "tenant-version-12" });
  await expect(page.getByText("删除流程测试企业")).toHaveCount(0);
});
