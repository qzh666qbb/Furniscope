import { expect, test } from "@playwright/test";

const success = (data) => ({ success: true, data, request_id: "market-insights-contract" });
const policy = {
  current: {
    name: "稳健增长",
    objective: "balanced",
    version: 1,
    weights: { demand_heat: .2, demand_growth: .2, unmet_need: .2, competition_space: .2, profit_space: .2 },
    fit_strength: .35,
    required_capabilities: [],
  },
  templates: {
    balanced: {
      name: "稳健增长",
      weights: { demand_heat: .2, demand_growth: .2, unmet_need: .2, competition_space: .2, profit_space: .2 },
      fit_strength: .35,
      required_capabilities: [],
    },
  },
};
const enterpriseProfile = {
  profile: { business_model: ["ODM"], primary_categories: ["sofa"], export_markets: ["US"], sales_channels: ["Amazon"], annual_capacity_note: "", constraints: [] },
  capabilities: [{ capability_type: "quality", capability_code: "ista_3a", capability_name: "ISTA 3A 包装能力", availability: "yes" }],
};
const factVocabulary = { groups: [{ capability_type: "quality", options: [{ code: "ista_3a", name: "ISTA 3A 包装能力" }] }] };

test("市场洞察多页面导航共享同一份数据与决策缓存", async ({ page }) => {
  const user = { user_id: 7, name: "产品验收员", email: "judge@example.com", role_code: "user", tenant: { tenant_id: 3, name: "验收企业" } };
  let datasetRequests = 0;
  let intelligenceRequests = 0;
  let pricingRequest = null;
  let opportunityEndpointAvailable = true;
  const dataset = {
    dataset_id: 31, name: "授权市场样本", platform: "amazon", market_country: "US",
    category_code: "unclassified", source_type: "enterprise_export", source_name: "企业授权脱敏数据",
    status: "ready", listing_count: 2, review_count: 2, valid_review_count: 2,
    quality_score: 95, data_start_date: null, data_end_date: "2026-09-01",
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", version_no: 1,
    quality_report: { raw_review_count: 2, valid_review_count: 2, filtered_review_count: 0 },
  };
  const opportunity = {
    opportunity_id: 1, opportunity_code: "OPP-1", title: "改善包装保护",
    description: "破损反馈形成需求缺口", base_score: 76, confidence: .82,
    recommendation_level: "prioritize_validate", unmet_need_score: 80,
    enterprise_fit_score: 72, enterprise_fit_confidence: .7, market_score: 78,
    adjusted_score: 76, weight_config: {}, manufacturing_fit: [],
  };
  const opportunities = Array.from({ length: 11 }, (_, index) => ({
    ...opportunity,
    opportunity_id: index + 1,
    opportunity_code: `OPP-${index + 1}`,
    title: index ? `候选机会 ${index + 1}` : opportunity.title,
    primary_cluster_ids: [index + 101],
  }));
  const alerts = Array.from({ length: 11 }, (_, index) => ({
    alert_id: index + 1,
    watch_id: 100 + index,
    dataset_id: 31,
    asin: `ASIN-${index + 1}`,
    title: `竞品座椅 ${index + 1}`,
    change_type: "price",
    change_label: "价格变动",
    severity: "medium",
    summary: `竞品价格动态 ${index + 1}`,
    is_read: false,
    detected_at: `2026-09-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
  }));
  const clusters = Array.from({ length: 11 }, (_, index) => ({
    cluster_id: index + 101,
    cluster_code: `CLUSTER-${index + 1}`,
    taxonomy_code: "comfort",
    name: `坐感需求主题 ${index + 1}`,
    summary: `第 ${index + 1} 组评论集中反馈坐垫支撑问题。`,
    sentiment_distribution: { positive: 1, negative: 2, mixed: 1, neutral: 0 },
    aspect_count: 4,
    review_count: 4,
    listing_count: 2,
    mention_rate: .25,
    importance_score: 80 - index,
    cluster_confidence: .82,
    representative_aspect_ids: [index + 201],
    created_at: "2026-09-01T00:00:00Z",
  }));
  let notificationChannels = [
    {
      channel_id: 81, name: "市场运营钉钉群", channel_type: "dingtalk",
      target_url: "https://alerts.example.invalid/furniscope/market-ops",
      secret_env: null, events: ["competitor_alert"], enabled: true,
      created_at: "2026-10-08T00:00:00Z",
    },
    {
      channel_id: 82, name: "跨境合规 Slack", channel_type: "slack",
      target_url: "https://alerts.example.invalid/furniscope/compliance",
      secret_env: null, events: ["policy_alert"], enabled: true,
      created_at: "2026-10-08T00:00:00Z",
    },
    {
      channel_id: 83, name: "管理层邮件网关", channel_type: "email_gateway",
      target_url: "https://alerts.example.invalid/furniscope/management",
      secret_env: null, events: ["competitor_alert", "policy_alert"], enabled: false,
      created_at: "2026-10-08T00:00:00Z",
    },
  ];
  let notificationEvents = [
    {
      event_id: 91, channel_id: 81, channel_name: "市场运营钉钉群",
      channel_type: "dingtalk", event_type: "competitor_alert",
      title: "竞品价格变化：重点休闲椅进入促销", content: "价格较观察基线下降 8.4%。",
      status: "delivered", attempts: 1, last_error: null,
      created_at: "2026-10-08T01:00:00Z", delivered_at: "2026-10-08T01:01:00Z",
    },
    {
      event_id: 92, channel_id: 82, channel_name: "跨境合规 Slack",
      channel_type: "slack", event_type: "policy_alert",
      title: "美国 CPSC：休闲椅电池召回风险", content: "请核对产品适用范围。",
      status: "delivered", attempts: 1, last_error: null,
      created_at: "2026-10-07T20:00:00Z", delivered_at: "2026-10-07T20:01:00Z",
    },
    {
      event_id: 93, channel_id: 83, channel_name: "管理层邮件网关",
      channel_type: "email_gateway", event_type: "policy_alert",
      title: "管理层邮件摘要投递失败", content: "接收端尚未完成配置。",
      payload: { severity: "medium" },
      status: "failed", attempts: 5, last_error: "EndpointNotConfigured",
      created_at: "2026-10-07T18:00:00Z", delivered_at: null,
    },
  ];
  let nextPolicySourceId = 3;
  let policySources = [];
  let policyAlerts = [];
  page.on("dialog", dialog => dialog.accept());
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    const method = request.method();
    let data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    if (path === "/api/v1/auth/login") data = { access_token: "contract-access", refresh_token: "contract-refresh", user };
    else if (path === "/api/v1/users/me") data = user;
    else if (path === "/api/v1/dashboard/summary") data = { products: 1, running_tasks: 0, pending_confirmations: 0, reports: 0 };
    else if (path === "/api/v1/market-datasets") {
      datasetRequests += 1;
      data = { items: [dataset], total: 1, page: 1, page_size: 100, total_pages: 1 };
    }
    else if (path === "/api/v1/products") data = { items: [{ product_id: 1, sku: "FS-001", name: "企业扶手椅" }], total: 1, page: 1, page_size: 100, total_pages: 1 };
    else if (path === "/api/v1/competitor-tracking/overview") data = { watch_count: 0, unread_alerts: 1, watches: [], alerts: [], rhythm: { launches: [], promos: [] } };
    else if (path === "/api/v1/market-signals/overview") data = { source_count: 3, sentiment_count: 128, unread_policy_alerts: 2 };
    else if (path === "/api/v1/market-signals/sources") data = [];
    else if (path === "/api/v1/market-signals/sentiment-events") data = [];
    else if (path === "/api/v1/market-signals/sentiment-feed") data = { items: [], total: 0, page: 1, page_size: 20, has_next: false, positive_count: 0, negative_count: 0, neutral_count: 0, live_count: 0, dataset_count: 0 };
    else if (path === "/api/v1/market-signals/policy-sources:install-defaults" && method === "POST") {
      if (!policySources.length) {
        policySources = [
          {
            source_id: 1, name: "美国消费品安全委员会", source_type: "official_rss",
            source_url: "https://www.cpsc.gov/recalls/rss", market_country: "US",
            category_code: null, keywords: ["recall", "furniture"],
            authorization_reference: "官方公开 RSS", schedule_minutes: 1440,
            enabled: true, last_fetched_at: null, last_status: null, last_error: null,
          },
          {
            source_id: 2, name: "美国联邦公报", source_type: "official_json",
            source_url: "https://www.federalregister.gov/api/v1/documents.json", market_country: "US",
            category_code: null, keywords: ["flammability", "labeling"],
            authorization_reference: "官方公开 API", schedule_minutes: 1440,
            enabled: true, last_fetched_at: null, last_status: null, last_error: null,
          },
        ];
      }
      data = policySources;
    }
    else if (path === "/api/v1/market-signals/policy-sources" && method === "GET") data = policySources;
    else if (path === "/api/v1/market-signals/policy-sources" && method === "POST") {
      const body = request.postDataJSON();
      const saved = {
        source_id: nextPolicySourceId++, ...body,
        last_fetched_at: null, last_status: null, last_error: null,
      };
      policySources = [...policySources, saved];
      data = saved;
    }
    else if (/^\/api\/v1\/market-signals\/policy-sources\/\d+$/.test(path) && method === "PATCH") {
      const sourceId = Number(path.split("/").at(-1));
      const body = request.postDataJSON();
      policySources = policySources.map((item) => item.source_id === sourceId ? { ...item, ...body } : item);
      data = policySources.find((item) => item.source_id === sourceId);
    }
    else if (/^\/api\/v1\/market-signals\/policy-sources\/\d+$/.test(path) && method === "DELETE") {
      const sourceId = Number(path.split("/").at(-1));
      policySources = policySources.filter((item) => item.source_id !== sourceId);
      data = { source_id: sourceId, deleted: true };
    }
    else if (/^\/api\/v1\/market-signals\/policy-sources\/\d+\/fetch$/.test(path) && method === "POST") {
      const sourceId = Number(path.split("/").at(-2));
      policySources = policySources.map((item) => item.source_id === sourceId ? {
        ...item,
        last_fetched_at: "2026-09-29T09:30:00Z",
        last_status: "succeeded",
        last_error: null,
      } : item);
      if (sourceId === 1 && !policyAlerts.length) {
        policyAlerts = [{
          alert_id: 51, source_id: 1, source_name: "美国消费品安全委员会",
          external_id: "CPSC-51", title: "软体家具阻燃标签规则更新",
          summary: "新规则调整了软体家具阻燃标签与召回报告要求。",
          url: "https://www.cpsc.gov/example/51", published_at: "2026-09-29T08:00:00Z",
          severity: "high", matched_keywords: ["flammability", "labeling"],
          is_read: false, created_at: "2026-09-29T08:10:00Z",
        }];
      }
      data = { source_id: sourceId, status: "succeeded", fetched_count: 2, inserted_count: sourceId === 1 ? 1 : 0, error_summary: null };
    }
    else if (path === "/api/v1/market-signals/policy-alerts" && method === "GET") {
      const severity = requestUrl.searchParams.get("severity");
      const unreadOnly = requestUrl.searchParams.get("unread_only") === "true";
      data = policyAlerts.filter((item) => (!severity || item.severity === severity) && (!unreadOnly || !item.is_read));
    }
    else if (/^\/api\/v1\/market-signals\/policy-alerts\/\d+\/read$/.test(path) && method === "POST") {
      const alertId = Number(path.split("/").at(-2));
      policyAlerts = policyAlerts.map((item) => item.alert_id === alertId ? { ...item, is_read: true } : item);
      data = { alert_id: alertId, is_read: true };
    }
    else if (path === "/api/v1/market-intelligence/overview") {
      intelligenceRequests += 1;
      data = {
      generated_at: "2026-09-29T00:00:00Z",
      scope: { dataset_id: 31, dataset_name: "授权市场样本", market_country: "US", platform: "amazon", data_class: "authorized_market_data" },
      smart_selection: { status: "ready", items: opportunities.slice(0, 5), total: 11, preview_limit: 5 },
      competitor_tracking: { watch_count: 11, active_watch_count: 11, dataset_listing_count: 11, alert_total: 11, recent_alerts: alerts.slice(0, 8) },
      review_mining: { total: 44, negative: 22, cluster_total: 11, clusters: clusters.slice(0, 8) },
      pricing: {
        status: "planning_anchor", currency: "USD", sample_size: 5, dataset_sample_size: 8,
        comparator_group: "recliner", comparator_group_label: "休闲椅",
        comparator_groups: [
          { group_code: "recliner", group_label: "休闲椅", sample_size: 5 },
          { group_code: "armchair", group_label: "扶手椅", sample_size: 3 },
        ],
        market_low: 99, market_median: 109, market_high: 119, recommended_price: 109,
        promo_floor: 100.28, price_floor: 107.69, unit_cost: 70, cost_basis: "planning_assumption",
        confidence: .75, review_growth_price_sensitivity: null,
        promo_floor_components: { market_p25: 99, max_discount_floor: 100.28, promo_margin_floor: 87.5 },
        scope_message: "当前按“休闲椅”可比竞品组计算，使用 5 / 8 个有效价格。",
        decision_support: {
          market_position: "主流偏低", market_percentile: .6,
          binding_constraint: { code: "max_discount_floor", label: "最大折扣率", floor: 100.28 },
          regular: { gross_profit_per_unit: 39, gross_margin: .3578 },
          promotion: { gross_profit_per_unit: 30.28, gross_margin: .302, discount_from_regular: .08 },
          scenarios: [
            { code: "market_low", label: "市场低位", price: 99, price_change_from_regular: -.0917, gross_profit_per_unit: 29, gross_margin: .2929, meets_promo_margin: true },
            { code: "regular", label: "建议常规价", price: 109, price_change_from_regular: 0, gross_profit_per_unit: 39, gross_margin: .3578, meets_target_margin: true, meets_promo_margin: true },
            { code: "promotion", label: "最低促销价", price: 100.28, price_change_from_regular: -.08, gross_profit_per_unit: 30.28, gross_margin: .302, meets_promo_margin: true },
            { code: "market_high", label: "市场高位", price: 119, price_change_from_regular: .0917, gross_profit_per_unit: 49, gross_margin: .4118, meets_promo_margin: true },
          ],
          recommended_action: { headline: "常规价以 USD 109.00 验证，促销不得低于 USD 100.28。", guardrail: "当前促销底线由“最大折扣率”约束决定。", next_step: "先确认实际完全成本。" },
        },
        message: "建议价基于规划参数。",
      },
      compliance: {
        status: policySources.some((item) => item.enabled) ? "ready" : "source_required",
        source_count: policySources.filter((item) => item.enabled).length,
        unread_count: policyAlerts.filter((item) => !item.is_read).length,
        high_risk_count: policyAlerts.filter((item) => item.severity === "high").length,
        alerts: policyAlerts,
        matched_scope: { market_country: "US", category_code: "unclassified" },
      },
        data_gaps: ["企业画像缺少单位成本"],
      };
    }
    else if (path === "/api/v1/market-intelligence/opportunities") {
      if (!opportunityEndpointAvailable) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({
            success: false,
            error: { code: "RESOURCE_NOT_FOUND", message: "请求资源不存在或不可访问", details: [] },
            request_id: "stale-api",
          }),
        });
        return;
      }
      const requestUrl = new URL(route.request().url());
      const pageNumber = Number(requestUrl.searchParams.get("page") || 1);
      const pageSize = Number(requestUrl.searchParams.get("page_size") || 10);
      const start = (pageNumber - 1) * pageSize;
      data = {
        items: opportunities.slice(start, start + pageSize),
        total: opportunities.length,
        page: pageNumber,
        page_size: pageSize,
        has_next: start + pageSize < opportunities.length,
      };
    }
    else if (/^\/api\/v1\/market-intelligence\/opportunities\/\d+$/.test(path)) {
      data = opportunities.find((item) => item.opportunity_id === Number(path.split("/").at(-1)));
    }
    else if (path === "/api/v1/market-intelligence/competitor-alerts") {
      const requestUrl = new URL(route.request().url());
      const pageNumber = Number(requestUrl.searchParams.get("page") || 1);
      const pageSize = Number(requestUrl.searchParams.get("page_size") || 10);
      const start = (pageNumber - 1) * pageSize;
      data = {
        items: alerts.slice(start, start + pageSize),
        total: alerts.length,
        page: pageNumber,
        page_size: pageSize,
        has_next: start + pageSize < alerts.length,
      };
    }
    else if (/^\/api\/v1\/market-intelligence\/competitor-alerts\/\d+$/.test(path)) {
      const item = alerts.find((alert) => alert.alert_id === Number(path.split("/").at(-1)));
      data = {
        ...item,
        platform: "amazon",
        market_country: "US",
        watch_status: "active",
        before_value: { sale_price: 219, currency: "USD" },
        after_value: { sale_price: 199, currency: "USD" },
        latest_snapshot: { sale_price: 199, currency: "USD", rating: 4.3 },
      };
    }
    else if (path === "/api/v1/market-intelligence/review-clusters") {
      const requestUrl = new URL(route.request().url());
      const pageNumber = Number(requestUrl.searchParams.get("page") || 1);
      const pageSize = Number(requestUrl.searchParams.get("page_size") || 10);
      const start = (pageNumber - 1) * pageSize;
      data = {
        items: clusters.slice(start, start + pageSize),
        total: clusters.length,
        page: pageNumber,
        page_size: pageSize,
        has_next: start + pageSize < clusters.length,
      };
    }
    else if (/^\/api\/v1\/market-intelligence\/review-clusters\/\d+$/.test(path)) {
      const item = clusters.find((cluster) => cluster.cluster_id === Number(path.split("/").at(-1)));
      data = {
        ...item,
        task_uuid: "00000000-0000-4000-8000-000000000001",
        dataset_id: 31,
        evidence: [{
          aspect_id: 201,
          review_id: 301,
          listing_id: 401,
          platform_listing_id: "ASIN-EVIDENCE",
          listing_title: "人体工学座椅",
          brand: "Fixture",
          rating: 2,
          title_original: "Seat support",
          language_code: "en",
          reviewed_at: "2026-09-01T00:00:00Z",
          verified_purchase: true,
          taxonomy_code: "comfort",
          sentiment: "negative",
          severity: "high",
          evidence_quote: "The seat cushion loses support after one hour.",
          extraction_confidence: .91,
          similarity_score: .95,
          is_representative: true,
        }],
      };
    }
    else if (path === "/api/v1/market-intelligence/pricing") {
      pricingRequest = route.request().postDataJSON();
      data = {
        status: "actionable", currency: "USD", sample_size: 3, market_low: 122.35,
        dataset_sample_size: 8, comparator_group: "armchair", comparator_group_label: "扶手椅",
        comparator_groups: [
          { group_code: "recliner", group_label: "休闲椅", sample_size: 5 },
          { group_code: "armchair", group_label: "扶手椅", sample_size: 3 },
        ],
        market_median: 209.14, market_high: 383.76, recommended_price: 209.14,
        promo_floor: 188.23, price_floor: 153.85, unit_cost: 100, confidence: .7, calculation_version: "market-pricing-v3",
        promo_floor_components: { market_p25: 122.35, max_discount_floor: 188.23, promo_margin_floor: 125 },
        scope_message: "当前按“扶手椅”可比竞品组计算，使用 3 / 8 个有效价格。",
        decision_support: {
          market_position: "主流偏低", market_percentile: .6,
          binding_constraint: { code: "max_discount_floor", label: "最大折扣率", floor: 188.23 },
          regular: { gross_profit_per_unit: 109.14, gross_margin: .5218 },
          promotion: { gross_profit_per_unit: 88.23, gross_margin: .4687, discount_from_regular: .1 },
          scenarios: [
            { code: "market_low", label: "市场低位", price: 122.35, price_change_from_regular: -.415, gross_profit_per_unit: 22.35, gross_margin: .1827, meets_promo_margin: false },
            { code: "regular", label: "建议常规价", price: 209.14, price_change_from_regular: 0, gross_profit_per_unit: 109.14, gross_margin: .5218, meets_target_margin: true, meets_promo_margin: true },
            { code: "promotion", label: "最低促销价", price: 188.23, price_change_from_regular: -.1, gross_profit_per_unit: 88.23, gross_margin: .4687, meets_promo_margin: true },
            { code: "market_high", label: "市场高位", price: 383.76, price_change_from_regular: .834, gross_profit_per_unit: 283.76, gross_margin: .7394, meets_promo_margin: true },
          ],
          recommended_action: { headline: "常规价以 USD 209.14 验证，促销不得低于 USD 188.23。", guardrail: "当前促销底线由“最大折扣率”约束决定。", next_step: "可进入小流量价格测试。" },
        },
        history_coverage: { tracked_targets: 0, observation_times: 0 },
        review_growth_price_sensitivity: null, message: "建议价满足市场与利润约束。",
      };
    }
    else if (path === "/api/v1/enterprise/opportunity-policy") data = policy;
    else if (path === "/api/v1/enterprise/profile") data = enterpriseProfile;
    else if (path === "/api/v1/enterprise/fact-vocabulary") data = factVocabulary;
    else if (/^\/api\/v1\/notification-channels\/events\/\d+:retry$/.test(path) && method === "POST") {
      const eventId = Number(path.match(/events\/(\d+):retry$/)?.[1]);
      notificationEvents = notificationEvents.map((item) => item.event_id === eventId
        ? { ...item, status: "pending", attempts: 0, last_error: null }
        : item);
      data = notificationEvents.find((item) => item.event_id === eventId);
    }
    else if (/^\/api\/v1\/notification-channels\/events\/\d+$/.test(path) && method === "GET") {
      const eventId = Number(path.split("/").at(-1));
      data = notificationEvents.find((item) => item.event_id === eventId);
    }
    else if (/^\/api\/v1\/notification-channels\/\d+$/.test(path) && method === "PATCH") {
      const channelId = Number(path.split("/").at(-1));
      const body = request.postDataJSON();
      notificationChannels = notificationChannels.map((item) => item.channel_id === channelId
        ? { ...item, ...body }
        : item);
      data = notificationChannels.find((item) => item.channel_id === channelId);
    }
    else if (path === "/api/v1/notification-channels") data = notificationChannels;
    else if (path === "/api/v1/notification-channels/events") data = notificationEvents;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });

  await page.goto("/#login");
  await page.getByRole("textbox", { name: "企业邮箱", exact: true }).fill("judge@example.com");
  await page.locator('input[type="password"]').fill("contract-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => { location.hash = "insights"; });

  await expect(page.getByRole("heading", { name: "市场洞察", exact: true })).toBeVisible();
  const navigationEntries = await page.evaluate(() => performance.getEntriesByType("navigation").length);
  const marketNav = page.locator(".market-section-nav");
  await expect(marketNav.getByRole("button")).toHaveCount(4);
  await expect(marketNav.getByRole("button", { name: /洞察首页/ })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".market-home-status>header strong")).toHaveText("授权市场样本");
  await page.screenshot({ path: "design-qa-market-insights-home.png", fullPage: true });

  await page.getByRole("button", { name: /合规来源/ }).click();
  await expect(page).toHaveURL(/#market-decisions\?capability=compliance&view=overview$/);
  const monitor = page.locator(".market-capability-hub").first();
  await expect(monitor.getByText("尚未配置政策来源", { exact: true })).toBeVisible();
  await monitor.getByRole("button", { name: "安装默认源", exact: true }).click();
  await expect(monitor.getByText("美国消费品安全委员会", { exact: true })).toBeVisible();
  await expect(monitor.getByText("软体家具阻燃标签规则更新", { exact: true })).toBeVisible();
  await expect(monitor.getByText("默认来源已安装并完成首次同步", { exact: true })).toBeVisible();
  await monitor.getByTitle("同步美国消费品安全委员会").click();
  await expect(monitor.getByText("“美国消费品安全委员会”同步完成：新增 1 条预警", { exact: true })).toBeVisible();
  await monitor.locator(".compliance-source-list article").filter({ hasText: "美国联邦公报" }).locator(".compliance-toggle").click();
  await expect(monitor.getByText("已停用“美国联邦公报”", { exact: true })).toBeVisible();
  await monitor.getByRole("button", { name: "标记已读", exact: true }).click();
  await expect(monitor.getByText("预警已标记为已读", { exact: true })).toBeVisible();
  await expect(monitor.getByRole("button", { name: "标记已读", exact: true })).toHaveCount(0);
  await monitor.getByRole("button", { name: "新增来源", exact: true }).click();
  await monitor.getByLabel("来源名称", { exact: true }).fill("欧盟产品安全公告");
  await monitor.getByLabel("官方地址", { exact: true }).fill("https://example.eu/safety.rss");
  await monitor.getByLabel("市场国家", { exact: true }).fill("DE");
  await monitor.getByLabel("风险关键词", { exact: true }).fill("recall, safety");
  await monitor.getByRole("button", { name: "保存并启用", exact: true }).click();
  await expect(monitor.getByText("欧盟产品安全公告", { exact: true })).toBeVisible();
  await monitor.getByTitle("删除欧盟产品安全公告").click();
  await expect(monitor.getByText("欧盟产品安全公告", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: "design-qa-market-compliance.png", fullPage: true });
  const intelligenceRequestsAfterCompliance = intelligenceRequests;

  await marketNav.getByRole("button", { name: /洞察首页/ }).click();
  await expect(page).toHaveURL(/#insights$/);
  await marketNav.getByRole("button", { name: /决策中心/ }).click();
  await expect(page).toHaveURL(/#market-decisions$/);
  await expect(page.getByRole("heading", { name: "市场决策中心", exact: true })).toBeVisible();
  await expect(monitor.getByText("五项市场决策能力", { exact: true })).toBeVisible();
  await expect(monitor.getByRole("button", { name: /智能定价/ })).toBeVisible();
  await expect(monitor.getByRole("button", { name: /跨境合规预警/ })).toBeVisible();
  await expect(monitor.getByText("11 项机会", { exact: true })).toBeVisible();
  await expect(monitor.getByText("11 监控 · 11 样本", { exact: true })).toBeVisible();
  await expect(monitor.getByText("44 评论 · 22 负向", { exact: true })).toBeVisible();
  await expect(monitor.getByText("这里展示的是机会方向，不是商品或 SKU。", { exact: true })).toBeVisible();
  await expect(page.getByText("选择数据集并运行一次 AI 分析后", { exact: false })).toHaveCount(0);
  await monitor.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page).toHaveURL(/capability=selection&view=overview&page=2&page_size=10$/);
  await expect(monitor.getByText("候选机会 11", { exact: true })).toBeVisible();
  await monitor.getByRole("button", { name: "查看详情", exact: true }).click();
  await expect(page).toHaveURL(/capability=selection&view=detail&page=2&page_size=10&opportunity_id=11$/);
  await expect(monitor.getByRole("button", { name: /返回机会列表/ })).toBeVisible();
  await monitor.getByRole("button", { name: /返回机会列表/ }).click();
  await expect(page).toHaveURL(/capability=selection&view=overview&page=2&page_size=10$/);
  await monitor.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(page).toHaveURL(/capability=selection&view=overview&page=1&page_size=10$/);
  await monitor.getByRole("button", { name: "经营配置工作台", exact: true }).click();
  await expect(monitor.getByRole("tab", { name: "排序策略", exact: true })).toBeVisible();
  await expect(monitor.getByRole("tab", { name: "企业事实", exact: true })).toBeVisible();
  await expect(monitor.getByRole("tab", { name: "准入条件", exact: true })).toBeVisible();
  await expect(monitor.getByRole("tab", { name: "版本与导出", exact: true })).toBeVisible();
  await monitor.getByRole("tab", { name: "企业事实", exact: true }).click();
  await expect(monitor.getByText("确认企业事实", { exact: true })).toBeVisible();
  await monitor.getByRole("tab", { name: "准入条件", exact: true }).click();
  await expect(monitor.getByText("必要条件", { exact: true })).toBeVisible();
  await monitor.getByRole("button", { name: /智能定价/ }).click();
  await expect(monitor.getByLabel("可比竞品组")).toHaveValue("recliner");
  await expect(monitor.getByText("价格场景对比", { exact: true })).toBeVisible();
  await expect(monitor.getByText("常规价单位毛利", { exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-pricing.png", fullPage: true });
  const pricingCardHeights = await monitor.locator(".pricing-result article").evaluateAll((items) => items.map((item) => item.getBoundingClientRect().height));
  expect(new Set(pricingCardHeights.map((height) => Math.round(height))).size).toBe(1);
  await monitor.getByLabel("单位完全成本").fill("0");
  await monitor.getByRole("button", { name: "重新计算", exact: true }).click();
  await expect(monitor.getByText("单位完全成本必须大于 0", { exact: false })).toBeVisible();
  expect(pricingRequest).toBeNull();
  await monitor.getByLabel("单位完全成本").fill("100");
  await monitor.getByLabel("最大折扣率").fill("10");
  await monitor.getByLabel("可比竞品组").selectOption("armchair");
  await monitor.getByRole("button", { name: "重新计算", exact: true }).click();
  await expect.poll(() => pricingRequest).not.toBeNull();
  expect(pricingRequest).toEqual({
    dataset_id: 31,
    unit_cost: 100,
    target_margin: .35,
    promo_margin_floor: .2,
    max_discount_rate: .1,
    comparator_group: "armchair",
  });
  await expect(monitor.getByText("扶手椅 · 3 / 8 个价格", { exact: true })).toBeVisible();
  await expect(monitor.getByText("market-pricing-v3", { exact: true })).toBeVisible();
  await monitor.getByRole("button", { name: /AI 智能选品/ }).click();
  await page.screenshot({ path: "design-qa-market-decisions.png", fullPage: true });

  await monitor.getByRole("button", { name: /竞品动态追踪/ }).click();
  await expect(page).toHaveURL(/#market-decisions\?capability=competitors&view=overview$/);
  await expect(monitor.getByRole("button", { name: "进入监控工作台", exact: true })).toHaveCount(0);
  await monitor.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page).toHaveURL(/capability=competitors&view=overview&page=2&page_size=10$/);
  await expect(monitor.getByText("竞品价格动态 11", { exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-competitor-overview.png", fullPage: true });
  await monitor.getByRole("button", { name: "查看详情", exact: true }).click();
  await expect(page).toHaveURL(/capability=competitors&view=detail&page=2&page_size=10&alert_id=11$/);
  await expect(monitor.getByText("变更前", { exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-competitor-detail.png", fullPage: true });
  await monitor.getByRole("button", { name: /返回动态列表/ }).click();
  await expect(page).toHaveURL(/capability=competitors&view=overview&page=2&page_size=10$/);
  await monitor.getByRole("button", { name: "监控工作台", exact: true }).click();
  await expect(page).toHaveURL(/capability=competitors&view=prices$/);
  await expect(page.getByRole("heading", { name: "用本企业产品对比市场上的相似商品" })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-competitors.png", fullPage: true });

  await monitor.getByRole("button", { name: /评论深挖/ }).click();
  await expect(page).toHaveURL(/#market-decisions\?capability=reviews&view=overview$/);
  await expect(monitor.getByRole("button", { name: "进入舆情工作台", exact: true })).toHaveCount(0);
  await monitor.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page).toHaveURL(/capability=reviews&view=overview&page=2&page_size=10$/);
  await expect(monitor.getByText("坐感需求主题 11", { exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-review-overview.png", fullPage: true });
  await monitor.getByRole("button", { name: "查看原文证据", exact: true }).click();
  await expect(page).toHaveURL(/capability=reviews&view=detail&page=2&page_size=10&cluster_id=111$/);
  await expect(monitor.getByText("The seat cushion loses support after one hour.", { exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-review-detail.png", fullPage: true });
  await monitor.getByRole("button", { name: /返回主题列表/ }).click();
  await expect(page).toHaveURL(/capability=reviews&view=overview&page=2&page_size=10$/);
  await monitor.getByRole("button", { name: "舆情工作台", exact: true }).click();
  await expect(page).toHaveURL(/capability=reviews&view=stream$/);
  await expect(page.getByRole("button", { name: "获取舆情", exact: true })).toBeVisible();

  await marketNav.getByRole("button", { name: /数据资产/ }).click();
  await expect(page).toHaveURL(/#market-data$/);
  await expect(page.getByRole("heading", { name: "市场数据资产", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "市场数据集", exact: true })).toBeVisible();
  await expect(page.getByText("授权市场样本", { exact: true })).toBeVisible();
  await page.screenshot({ path: "design-qa-market-data.png", fullPage: true });

  await marketNav.getByRole("button", { name: /自动化/ }).click();
  await expect(page).toHaveURL(/#market-automation$/);
  await expect(page.getByRole("heading", { name: "自动化投递", exact: true })).toBeVisible();
  const delivery = page.locator(".notification-channel-manager");
  await expect(delivery).toHaveAttribute("open", "");
  await expect(delivery.getByText("自动化投递配置", { exact: true })).toBeVisible();
  await expect(delivery.getByText("2 / 3 个渠道启用", { exact: true })).toBeVisible();
  await expect(delivery.getByText("市场运营钉钉群", { exact: true })).toBeVisible();
  await expect(delivery.getByText("跨境合规 Slack", { exact: true })).toBeVisible();
  await delivery.getByText("最近投递记录", { exact: true }).click();
  await expect(delivery.locator(".notification-events button").filter({
    hasText: "竞品价格变化：重点休闲椅进入促销",
  })).toBeVisible();
  await expect(delivery.locator(".notification-events button").filter({
    hasText: "美国 CPSC：休闲椅电池召回风险",
  })).toBeVisible();
  await delivery.locator(".notification-channel-list article").filter({
    hasText: "市场运营钉钉群",
  }).getByTitle("编辑渠道").click();
  await expect(delivery.locator('input[name="name"]')).toHaveValue("市场运营钉钉群");
  await delivery.locator('input[name="name"]').fill("北美市场运营钉钉群");
  await delivery.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(delivery.getByText("北美市场运营钉钉群", { exact: true })).toBeVisible();
  await delivery.locator(".notification-events button").filter({
    hasText: "管理层邮件摘要投递失败",
  }).click();
  const eventDialog = page.getByRole("dialog", { name: "投递详情" });
  await expect(eventDialog).toContainText("EndpointNotConfigured");
  await eventDialog.getByRole("button", { name: "重新投递", exact: true }).click();
  await expect(delivery.getByText("“管理层邮件摘要投递失败”已重新进入投递队列", {
    exact: true,
  })).toBeVisible();
  await expect(delivery.locator(".notification-events button").filter({
    hasText: "管理层邮件摘要投递失败",
  })).toContainText("待投递");
  await page.screenshot({ path: "design-qa-market-automation.png", fullPage: true });

  await page.locator(".sidebar").getByRole("button", { name: "市场洞察", exact: true }).click();
  await expect(page).toHaveURL(/#insights$/);
  await expect(page.getByRole("heading", { name: "市场洞察", exact: true })).toBeVisible();
  expect(datasetRequests).toBe(1);
  expect(intelligenceRequests).toBe(intelligenceRequestsAfterCompliance);
  expect(await page.evaluate(() => performance.getEntriesByType("navigation").length)).toBe(navigationEntries);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  await expect(page.locator(".market-module-page>.workspace-main")).toHaveCSS("margin-left", "0px");
  await expect(marketNav.getByRole("button", { name: /洞察首页/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-insights-home-mobile.png", fullPage: true });
  await marketNav.getByRole("button", { name: /数据资产/ }).click();
  await expect(page.getByRole("heading", { name: "市场数据资产", exact: true })).toBeVisible();
  await expect(page.locator(".dataset-asset-table table")).toHaveCSS("min-width", "0px");
  await expect(page.locator(".dataset-asset-table tbody tr").first()).toHaveCSS("display", "grid");
  await expect(page.locator('.dataset-asset-table td[data-label="质量"]').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-data-mobile.png", fullPage: true });
  await marketNav.getByRole("button", { name: /决策中心/ }).click();
  await monitor.getByRole("button", { name: /AI 智能选品/ }).click();
  await monitor.getByRole("button", { name: "经营配置工作台", exact: true }).click();
  await expect(monitor.getByRole("tab", { name: "排序策略", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-selection-mobile.png", fullPage: true });
  await monitor.getByRole("button", { name: /智能定价/ }).click();
  await expect(monitor.getByText("价格场景对比", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-pricing-mobile.png", fullPage: true });
  await monitor.getByRole("button", { name: /竞品动态追踪/ }).click();
  await expect(monitor.getByText("竞品价格动态 1", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-competitor-overview-mobile.png", fullPage: true });
  await monitor.getByRole("button", { name: "查看详情", exact: true }).first().click();
  await expect(monitor.getByText("变更前", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-competitor-detail-mobile.png", fullPage: true });
  await monitor.getByRole("button", { name: /返回动态列表/ }).click();
  await monitor.getByRole("button", { name: "监控工作台", exact: true }).click();
  await expect(page.getByRole("heading", { name: "用本企业产品对比市场上的相似商品" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await monitor.getByRole("button", { name: /评论深挖/ }).click();
  await expect(monitor.getByText("坐感需求主题 1", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-review-overview-mobile.png", fullPage: true });
  await monitor.getByRole("button", { name: "查看原文证据", exact: true }).first().click();
  await expect(monitor.getByText("The seat cushion loses support after one hour.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: "design-qa-market-review-detail-mobile.png", fullPage: true });
  await monitor.getByRole("button", { name: /返回主题列表/ }).click();
  await monitor.getByRole("button", { name: "舆情工作台", exact: true }).click();
  await expect(page.getByRole("button", { name: "获取舆情", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await marketNav.getByRole("button", { name: /自动化/ }).click();
  await expect(page.getByRole("heading", { name: "自动化投递", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();

  opportunityEndpointAvailable = false;
  await marketNav.getByRole("button", { name: /决策中心/ }).click();
  await expect(monitor.getByText("当前 API 服务尚未加载选品分页接口", { exact: false })).toBeVisible();
  await expect(monitor.getByText("请求资源不存在或不可访问", { exact: true })).toHaveCount(0);
  await expect(monitor.getByText("改善包装保护", { exact: true })).toBeVisible();
  await expect(monitor.locator(".selection-pagination")).toHaveCount(0);
});

test("竞品与评论归入决策中心并兼容旧路由", async ({ page }) => {
  const user = { user_id: 7, name: "产品验收员", email: "judge@example.com", role_code: "user", tenant: { tenant_id: 3, name: "验收企业" } };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data = { items: [], total: 0, page: 1, page_size: 20, total_pages: 0 };
    if (path === "/api/v1/auth/login") data = { access_token: "contract-access", refresh_token: "contract-refresh", user };
    else if (path === "/api/v1/users/me") data = user;
    else if (path === "/api/v1/dashboard/summary") data = { products: 1, running_tasks: 0, pending_confirmations: 0, reports: 0 };
    else if (path === "/api/v1/market-datasets") data = { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 };
    else if (path === "/api/v1/competitor-tracking/overview") data = { watch_count: 0, unread_alerts: 0, watches: [], alerts: [], rhythm: { launches: [], promos: [] } };
    else if (path === "/api/v1/market-signals/sentiment-feed") data = { items: [], total: 0, page: 1, page_size: 20, has_next: false, positive_count: 0, negative_count: 0, neutral_count: 0, live_count: 0, dataset_count: 0 };
    else if (path === "/api/v1/market-intelligence/overview") data = {
      generated_at: "2026-09-29T00:00:00Z",
      scope: { data_class: "authorized_market_data" },
      smart_selection: { status: "awaiting_analysis", items: [] },
      competitor_tracking: { watch_count: 0, active_watch_count: 0, dataset_listing_count: 0, recent_alerts: [] },
      review_mining: { total: 0, negative: 0, clusters: [] },
      pricing: { status: "insufficient_market_data", sample_size: 0, message: "当前数据集没有有效价格。" },
      compliance: { status: "source_required", source_count: 0, unread_count: 0, high_risk_count: 0, alerts: [] },
      data_gaps: ["尚无可用市场数据集"],
    };
    else if (path === "/api/v1/enterprise/opportunity-policy") data = policy;
    else if (path === "/api/v1/enterprise/profile") data = enterpriseProfile;
    else if (path === "/api/v1/enterprise/fact-vocabulary") data = factVocabulary;
    else if (path === "/api/v1/market-signals/sources" || path === "/api/v1/market-signals/sentiment-events" || path === "/api/v1/market-signals/policy-sources" || path === "/api/v1/market-signals/policy-alerts" || path === "/api/v1/notification-channels" || path === "/api/v1/notification-channels/events") data = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(success(data)) });
  });
  await page.goto("/#login");
  await page.getByRole("textbox", { name: "企业邮箱", exact: true }).fill("judge@example.com");
  await page.locator('input[type="password"]').fill("contract-only");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("今天要研究哪个产品？")).toBeVisible();
  await page.goto("/#competitor-tracking?tab=prices");

  await expect(page).toHaveURL(/#market-decisions\?capability=competitors&view=prices$/);
  await expect(page.getByRole("heading", { name: "市场决策中心", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "用本企业产品对比市场上的相似商品" })).toBeVisible();
  await expect(page.getByText("开始对比", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /主动采集/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /政策与告警/ })).toHaveCount(0);
  await expect(page.locator(".market-section-nav").getByRole("button", { name: /竞品分析|评论舆情/ })).toHaveCount(0);

  const monitor = page.locator(".market-capability-hub");
  await monitor.getByRole("button", { name: /评论深挖/ }).click();
  await monitor.getByRole("button", { name: "舆情工作台", exact: true }).click();
  await expect(page).toHaveURL(/#market-decisions\?capability=reviews&view=stream$/);
  await expect(page.getByRole("heading", { name: "把市场评论和网页采集放在同一条舆情流里" })).toBeVisible();
  await expect(page.getByRole("button", { name: "获取舆情", exact: true })).toBeVisible();
  await page.goto("/#competitor-tracking?tab=stream");
  await expect(page).toHaveURL(/#market-decisions\?capability=reviews&view=stream$/);
  await page.screenshot({ path: "design-qa-market-reviews.png", fullPage: true });
});
