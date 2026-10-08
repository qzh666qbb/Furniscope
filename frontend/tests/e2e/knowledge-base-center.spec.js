import { expect, test } from "@playwright/test";

const success = data => ({ success: true, data, request_id: "knowledge-center-e2e" });

test("知识库页覆盖快速预览、XLSX 详情、版本回溯与返回状态恢复", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const baseUuid = "61000000-0000-4000-8000-000000000001";
  const createdUuid = "61000000-0000-4000-8000-000000000002";
  const documentUuid = "62000000-0000-4000-8000-000000000001";
  const workspaceUuid = "63000000-0000-4000-8000-000000000001";
  let bases = [{
    knowledge_base_uuid: baseUuid,
    name: "产品合规资料",
    description: "出口认证与材料规范",
    status: "active",
    visibility: "tenant",
    document_count: 1,
    ready_document_count: 1,
    created_at: "2026-10-07T08:00:00Z",
    updated_at: "2026-10-07T08:00:00Z",
  }];
  let document = {
    document_uuid: documentUuid,
    knowledge_base_uuid: baseUuid,
    filename: "product-catalog.xlsx",
    mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    document_type: "产品目录",
    status: "ready",
    version: 1,
    sha256: "a".repeat(64),
    byte_size: 4096,
    index_job_uuid: "64000000-0000-4000-8000-000000000001",
    error_code: null,
    error_message: null,
    created_at: "2026-10-07T08:00:00Z",
    updated_at: "2026-10-07T08:00:00Z",
  };
  let versions = [{
    document_version_uuid: "65000000-0000-4000-8000-000000000001",
    version: 1,
    sha256: "a".repeat(64),
    byte_size: 4096,
    extraction_method: "xlsx_cells",
    page_or_sheet_count: 2,
    index_job_uuid: "64000000-0000-4000-8000-000000000001",
    index_status: "succeeded",
    error_code: null,
    error_message: null,
    is_current: true,
    created_at: "2026-10-07T08:00:00Z",
  }];
  let context = {
    context_uuid: "66000000-0000-4000-8000-000000000001",
    revision: 1,
    product_id: null,
    dataset_ids: [],
    knowledge_base_uuids: [],
    memory_scope: ["workspace", "user"],
    task_uuids: [],
    retrieval_policy: {
      history_turn_limit: 12,
      knowledge_top_k: 8,
      max_context_tokens: 12000,
      include_enterprise_profile: true,
      include_product_profile: true,
    },
  };
  let reindexed = false;
  let searchRequest = null;

  await page.addInitScript(() => sessionStorage.setItem("furniscope-access-token", "knowledge-user-token"));
  page.on("dialog", dialog => dialog.accept());
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    let data = {};
    if (path === "/api/v1/users/me") {
      data = { user_id: 7, name: "知识管理员", email: "knowledge@example.com", role_code: "user", tenant: { tenant_id: 3, name: "测试企业" } };
    } else if (path === "/api/v1/knowledge-bases" && method === "GET") {
      data = { items: bases, total: bases.length, page: 1, page_size: 100, total_pages: 1 };
    } else if (path === "/api/v1/knowledge-bases" && method === "POST") {
      const body = request.postDataJSON();
      const created = {
        knowledge_base_uuid: createdUuid,
        ...body,
        status: "active",
        document_count: 0,
        ready_document_count: 0,
        created_at: "2026-10-07T09:00:00Z",
        updated_at: "2026-10-07T09:00:00Z",
      };
      bases = [created, ...bases];
      data = created;
    } else if (path === "/api/v1/knowledge-bases:search" && method === "POST") {
      searchRequest = request.postDataJSON();
      data = {
        matches: [{
          citation_uuid: "67000000-0000-4000-8000-000000000001",
          document_uuid: documentUuid,
          document_name: "product-catalog.xlsx",
          page: 1,
          chunk_text: "HF-SF-001 云感沙发适用于北美市场，并满足当前产品目录要求。",
          score: .91,
          document_version: 1,
        }],
        relevance_threshold: .35,
        refused: false,
      };
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}` && method === "PATCH") {
      const body = request.postDataJSON();
      bases = bases.map(item => item.knowledge_base_uuid === baseUuid ? { ...item, ...body } : item);
      data = bases.find(item => item.knowledge_base_uuid === baseUuid);
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}` && method === "GET") {
      data = bases.find(item => item.knowledge_base_uuid === baseUuid);
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}` && method === "DELETE") {
      bases = bases.filter(item => item.knowledge_base_uuid !== baseUuid);
      data = { knowledge_base_uuid: baseUuid, archived: true };
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}/documents` && method === "GET") {
      const items = document ? [document] : [];
      data = { items, total: items.length, page: 1, page_size: 100, total_pages: 1 };
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}/documents/${documentUuid}` && method === "GET") {
      data = document;
    } else if (path === `/api/v1/knowledge-bases/${createdUuid}/documents` && method === "GET") {
      data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}/documents/${documentUuid}/versions` && method === "GET") {
      data = { items: versions, total: versions.length, page: 1, page_size: 100, total_pages: 1 };
    } else if (path.match(/\/versions\/\d+\/preview$/)) {
      const version = Number(path.match(/\/versions\/(\d+)\/preview$/)[1]);
      data = {
        document_uuid: documentUuid,
        filename: "product-catalog.xlsx",
        mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        version,
        extraction_method: "xlsx_cells",
        page_or_sheet_count: 2,
        text: version === 1
          ? "[sheet:产品目录]\nSKU | 产品名称 | 市场\nHF-SF-001 | 云感沙发 | 北美\nHF-TB-002 | 橡木餐桌 | 德国\n[sheet:资料说明]\n项目 | 内容\n来源 | 产品中心\n更新频率 | 每周"
          : `[sheet:产品目录]\nSKU | 产品名称\nHF-SF-00${version} | 版本 ${version} 产品`,
        available: true,
        truncated: false,
      };
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}/documents/${documentUuid}/versions` && method === "POST") {
      versions = versions.map(item => ({ ...item, is_current: false }));
      const nextVersion = versions.length + 1;
      document = { ...document, version: nextVersion, status: "uploaded", sha256: "b".repeat(64), updated_at: "2026-10-07T10:00:00Z" };
      versions.unshift({
        document_version_uuid: `65000000-0000-4000-8000-00000000000${nextVersion}`,
        version: nextVersion,
        sha256: "b".repeat(64),
        byte_size: 5000,
        extraction_method: null,
        page_or_sheet_count: null,
        index_job_uuid: `64000000-0000-4000-8000-00000000000${nextVersion}`,
        index_status: "queued",
        error_code: null,
        error_message: null,
        is_current: true,
        created_at: "2026-10-07T10:00:00Z",
      });
      data = document;
    } else if (path.match(/\/versions\/1:restore$/) && method === "POST") {
      versions = versions.map(item => ({ ...item, is_current: false }));
      const nextVersion = versions.length + 1;
      document = { ...document, version: nextVersion, status: "uploaded", sha256: "a".repeat(64), updated_at: "2026-10-07T11:00:00Z" };
      versions.unshift({
        ...versions.at(-1),
        document_version_uuid: `65000000-0000-4000-8000-00000000000${nextVersion}`,
        version: nextVersion,
        index_job_uuid: `64000000-0000-4000-8000-00000000000${nextVersion}`,
        index_status: "queued",
        is_current: true,
        created_at: "2026-10-07T11:00:00Z",
      });
      data = document;
    } else if (path.endsWith(`${documentUuid}:reindex`) && method === "POST") {
      reindexed = true;
      document = { ...document, status: "uploaded" };
      data = { document_uuid: documentUuid, status: "queued" };
    } else if (path === `/api/v1/knowledge-bases/${baseUuid}/documents/${documentUuid}` && method === "DELETE") {
      document = null;
      data = { document_uuid: documentUuid, deleted: true };
    } else if (path === `/api/v1/analysis-workspaces/${workspaceUuid}/context` && method === "GET") {
      data = context;
    } else if (path === `/api/v1/analysis-workspaces/${workspaceUuid}/context` && method === "PUT") {
      context = { ...request.postDataJSON(), context_uuid: context.context_uuid, revision: context.revision + 1 };
      data = context;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto(`/#knowledge?workspace=${workspaceUuid}&from=workflow&return=workflow%3Fworkspace%3D${workspaceUuid}`);
  await expect(page.getByRole("heading", { name: "企业知识库", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "产品合规资料", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "新建知识库", exact: true }).click();
  await page.getByLabel("名称", { exact: true }).fill("客户服务手册");
  await page.getByLabel("说明", { exact: true }).fill("售后处理标准");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".kb-library-list")).toContainText("客户服务手册");

  await page.locator(".kb-library-list nav button").filter({ hasText: "产品合规资料" }).click();
  await page.getByTitle("编辑知识库").click();
  await page.locator('.kb-modal input[value="user"]').check();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".kb-content-header")).toContainText("仅自己");

  await page.locator(".kb-binding input").click();
  await expect.poll(() => context.knowledge_base_uuids).toEqual([baseUuid]);
  await expect(page.locator(".kb-binding input")).toBeChecked();

  await page.getByLabel("检索问题", { exact: true }).fill("云感沙发适用于哪个市场？");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect.poll(() => searchRequest).toEqual({
    query: "云感沙发适用于哪个市场？",
    knowledge_base_uuids: [baseUuid],
    workspace_uuid: workspaceUuid,
    top_k: 8,
    filters: { document_type: [] },
  });
  await expect(page.locator(".kb-retrieval-results")).toContainText("云感沙发适用于北美市场");
  await expect(page.locator(".kb-retrieval-results")).toContainText("相关度 91%");
  await page.getByRole("button", { name: "打开文档", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`#knowledge-document\\?`));
  await expect(page.getByRole("heading", { name: "product-catalog.xlsx" })).toBeVisible();
  await page.getByRole("button", { name: "返回知识库" }).click();
  await expect(page.getByRole("heading", { name: "产品合规资料", exact: true })).toBeVisible();

  await page.addStyleTag({ content: ".kb-content{max-height:300px}" });
  await page.locator(".kb-content").evaluate(element => { element.scrollTop = 80; });

  await page.getByTitle("快速预览").click();
  const savedScrollTop = await page.locator(".kb-content").evaluate(element => element.scrollTop);
  const inspector = page.getByRole("complementary", { name: "文档快速预览" });
  await expect(inspector).toContainText("已提取 2 个工作表");
  await expect(inspector).toContainText("HF-SF-001");
  await expect(inspector).not.toContainText("[sheet:");
  await page.screenshot({ path: "test-results/knowledge-document-quick-preview.png", fullPage: true });
  await inspector.getByRole("button", { name: "全屏查看" }).click();

  await expect(page).toHaveURL(new RegExp(`#knowledge-document\\?`));
  await expect(page.getByRole("heading", { name: "product-catalog.xlsx" })).toBeVisible();
  await expect(page.locator(".kb-detail-preview")).toContainText("云感沙发");
  await page.getByRole("button", { name: "资料说明 3" }).click();
  await expect(page.locator(".kb-detail-preview")).toContainText("更新频率");
  await expect(page.locator(".kb-detail-source")).toContainText("产品合规资料");
  await expect(page.locator(".kb-detail-versions")).toContainText("v1");
  await page.screenshot({ path: "test-results/knowledge-document-detail-desktop.png", fullPage: true });

  await page.locator('.kb-detail-header input[type="file"]').setInputFiles({
    name: "product-catalog.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("synthetic xlsx version"),
  });
  await expect(page.locator(".kb-detail-versions")).toContainText("v2");
  await page.getByTitle("将 v1 回溯为新版本").click();
  await expect(page.locator(".kb-detail-versions")).toContainText("v3");

  await page.getByRole("button", { name: "返回知识库" }).click();
  await expect(page.getByRole("heading", { name: "产品合规资料", exact: true })).toBeVisible();
  await expect(page.locator(".kb-document-table tbody tr")).toHaveClass(/selected/);
  await expect.poll(async () => Math.abs(
    await page.locator(".kb-content").evaluate(element => element.scrollTop) - savedScrollTop,
  )).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTitle("快速预览").click();
  await expect(page).toHaveURL(new RegExp(`#knowledge-document\\?`));
  await expect(page.getByRole("complementary", { name: "文档快速预览" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "product-catalog.xlsx" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/knowledge-document-detail-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "返回知识库" }).click();

  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.getByTitle("重新索引").click();
  await expect.poll(() => reindexed).toBeTruthy();
  await page.getByTitle("删除文档").click();
  await expect(page.locator(".kb-document-table tbody tr")).toHaveCount(0);

  await page.getByTitle("归档知识库").click();
  await expect(page.locator(".kb-library-list")).not.toContainText("产品合规资料");

  await page.screenshot({ path: "test-results/knowledge-center-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".workspace-main")).toHaveCSS("margin-left", "0px");
  await expect(page.locator(".sidebar")).toHaveCSS("width", "390px");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/knowledge-center-mobile.png", fullPage: true });
});
