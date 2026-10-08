import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  ChartBar,
  ChatCircleDots,
  CheckCircle,
  Crosshair,
  CurrencyDollar,
  Package,
  ShieldWarning,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, createUuid } from "./api.js";
import {
  CompetitorEvidenceView,
  ReviewEvidenceView,
} from "./MarketEvidenceViews.jsx";
import { useMarketInsights } from "./MarketInsightsContext.jsx";
import "./market-intelligence.css";

const SelectionWorkspace = lazy(() => import("./MarketSelection.jsx")
  .then((module) => ({ default: module.SelectionWorkspace })));
const CompetitorTrackingWorkspace = lazy(() => import("./CompetitorTracking.jsx")
  .then((module) => ({ default: module.CompetitorTrackingWorkspace })));
const SentimentStreamPanel = lazy(() => import("./MarketSignals.jsx")
  .then((module) => ({ default: module.SentimentStreamPanel })));
const ComplianceWorkspace = lazy(() => import("./ComplianceWorkspace.jsx")
  .then((module) => ({ default: module.ComplianceWorkspace })));

const capabilities = [
  ["selection", Package, "AI 智能选品"],
  ["competitors", ChartBar, "竞品动态追踪"],
  ["reviews", ChatCircleDots, "评论深挖"],
  ["pricing", CurrencyDollar, "智能定价"],
  ["compliance", ShieldWarning, "跨境合规预警"],
];
const money = (value, currency) => value == null ? "—" : `${currency || ""} ${Number(value).toFixed(2)}`.trim();
const percent = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
const signedPercent = (value) => {
  if (value == null) return "—";
  const numeric = Number(value) * 100;
  return `${numeric > 0 ? "+" : ""}${numeric.toFixed(1)}%`;
};
const dateText = (value) => value ? new Date(value).toLocaleDateString("zh-CN") : "—";
const messageOf = (error) => error instanceof Error ? error.message : String(error);
const capabilityKeys = new Set(capabilities.map(([key]) => key));
const routeState = () => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const capability = params.get("capability");
  const page = Number(params.get("page") || 1);
  const pageSize = Number(params.get("page_size") || 10);
  const opportunityId = Number(params.get("opportunity_id") || 0);
  const alertId = Number(params.get("alert_id") || 0);
  const clusterId = Number(params.get("cluster_id") || 0);
  return {
    capability: capabilityKeys.has(capability) ? capability : "selection",
    view: params.get("view") || "overview",
    page: Number.isInteger(page) && page > 0 ? page : 1,
    pageSize: [10, 20, 50].includes(pageSize) ? pageSize : 10,
    opportunityId: Number.isInteger(opportunityId) && opportunityId > 0 ? opportunityId : null,
    alertId: Number.isInteger(alertId) && alertId > 0 ? alertId : null,
    clusterId: Number.isInteger(clusterId) && clusterId > 0 ? clusterId : null,
  };
};

function Empty({ children }) {
  return <div className="intelligence-empty"><WarningCircle /><span>{children}</span></div>;
}

function CapabilityViewNav({ view, workbenchView, workbenchLabel, onOverview, onWorkbench }) {
  return (
    <nav className="capability-view-nav" aria-label="能力视图">
      <button type="button" className={["overview", "detail"].includes(view) ? "active" : ""} onClick={onOverview}>洞察概览</button>
      <button type="button" className={view === workbenchView ? "active" : ""} onClick={onWorkbench}>{workbenchLabel}</button>
    </nav>
  );
}

export function MarketIntelligenceHub({ datasets: providedDatasets }) {
  const market = useMarketInsights();
  const datasets = providedDatasets || market.datasets;
  const ready = datasets.filter((item) => item.status === "ready");
  const [datasetId, setDatasetId] = useState(
    () => String(market.selectedDatasetId || ready[0]?.dataset_id || ""),
  );
  const initialRoute = routeState();
  const [tab, setTab] = useState(initialRoute.capability);
  const [capabilityView, setCapabilityView] = useState(initialRoute.view);
  const [opportunityPage, setOpportunityPage] = useState(initialRoute.page);
  const [opportunityPageSize, setOpportunityPageSize] = useState(initialRoute.pageSize);
  const [opportunityId, setOpportunityId] = useState(initialRoute.opportunityId);
  const [opportunityData, setOpportunityData] = useState(null);
  const [opportunityLoading, setOpportunityLoading] = useState(false);
  const [opportunityError, setOpportunityError] = useState("");
  const [opportunityEndpointUnavailable, setOpportunityEndpointUnavailable] = useState(false);
  const [selectedOpportunity, setSelectedOpportunity] = useState(null);
  const [competitorPage, setCompetitorPage] = useState(initialRoute.capability === "competitors" ? initialRoute.page : 1);
  const [competitorPageSize, setCompetitorPageSize] = useState(initialRoute.capability === "competitors" ? initialRoute.pageSize : 10);
  const [alertId, setAlertId] = useState(initialRoute.alertId);
  const [alertData, setAlertData] = useState(null);
  const [alertLoading, setAlertLoading] = useState(false);
  const [alertError, setAlertError] = useState("");
  const [alertEndpointUnavailable, setAlertEndpointUnavailable] = useState(false);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [reviewPage, setReviewPage] = useState(initialRoute.capability === "reviews" ? initialRoute.page : 1);
  const [reviewPageSize, setReviewPageSize] = useState(initialRoute.capability === "reviews" ? initialRoute.pageSize : 10);
  const [clusterId, setClusterId] = useState(initialRoute.clusterId);
  const [clusterData, setClusterData] = useState(null);
  const [clusterLoading, setClusterLoading] = useState(false);
  const [clusterError, setClusterError] = useState("");
  const [clusterEndpointUnavailable, setClusterEndpointUnavailable] = useState(false);
  const [selectedCluster, setSelectedCluster] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [margin, setMargin] = useState("35");
  const [promoMargin, setPromoMargin] = useState("20");
  const [maxDiscount, setMaxDiscount] = useState("8");
  const [pricingGroup, setPricingGroup] = useState("");
  const [pricingFieldError, setPricingFieldError] = useState("");
  const [pricing, setPricing] = useState(null);

  useEffect(() => {
    if (!datasetId && market.selectedDatasetId) setDatasetId(String(market.selectedDatasetId));
    else if (!datasetId && ready[0]) setDatasetId(String(ready[0].dataset_id));
  }, [datasetId, ready, market.selectedDatasetId]);
  useEffect(() => {
    const syncRoute = () => {
      const next = routeState();
      setTab(next.capability);
      setCapabilityView(next.view);
      if (next.capability === "selection") {
        setOpportunityPage(next.page);
        setOpportunityPageSize(next.pageSize);
        setOpportunityId(next.opportunityId);
      } else if (next.capability === "competitors") {
        setCompetitorPage(next.page);
        setCompetitorPageSize(next.pageSize);
        setAlertId(next.alertId);
      } else if (next.capability === "reviews") {
        setReviewPage(next.page);
        setReviewPageSize(next.pageSize);
        setClusterId(next.clusterId);
      }
    };
    window.addEventListener("hashchange", syncRoute);
    return () => window.removeEventListener("hashchange", syncRoute);
  }, []);

  const navigateCapability = (capability, view = "overview") => {
    setTab(capability);
    setCapabilityView(view);
    if (location.hash.startsWith("#market-decisions")) {
      location.hash = `market-decisions?capability=${capability}&view=${view}`;
    }
  };

  const navigateSelection = (view, options = {}) => {
    const page = options.page ?? opportunityPage;
    const pageSize = options.pageSize ?? opportunityPageSize;
    const nextId = options.opportunityId;
    const params = new URLSearchParams({
      capability: "selection",
      view,
      page: String(page),
      page_size: String(pageSize),
    });
    if (nextId) params.set("opportunity_id", String(nextId));
    setTab("selection");
    setCapabilityView(view);
    setOpportunityPage(page);
    setOpportunityPageSize(pageSize);
    setOpportunityId(nextId || null);
    location.hash = `market-decisions?${params}`;
  };

  const navigateCompetitors = (view, options = {}) => {
    const page = options.page ?? competitorPage;
    const pageSize = options.pageSize ?? competitorPageSize;
    const nextId = options.itemId;
    const params = new URLSearchParams({
      capability: "competitors",
      view,
      page: String(page),
      page_size: String(pageSize),
    });
    if (nextId) params.set("alert_id", String(nextId));
    setTab("competitors");
    setCapabilityView(view);
    setCompetitorPage(page);
    setCompetitorPageSize(pageSize);
    setAlertId(nextId || null);
    location.hash = `market-decisions?${params}`;
  };

  const navigateReviews = (view, options = {}) => {
    const page = options.page ?? reviewPage;
    const pageSize = options.pageSize ?? reviewPageSize;
    const nextId = options.itemId;
    const params = new URLSearchParams({
      capability: "reviews",
      view,
      page: String(page),
      page_size: String(pageSize),
    });
    if (nextId) params.set("cluster_id", String(nextId));
    setTab("reviews");
    setCapabilityView(view);
    setReviewPage(page);
    setReviewPageSize(pageSize);
    setClusterId(nextId || null);
    location.hash = `market-decisions?${params}`;
  };

  const load = async (force = false, silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    try {
      const result = await market.loadIntelligence(datasetId || null, { force });
      setData(result);
      setPricing(result.pricing || null);
      setPricingGroup(result.pricing?.comparator_group || "");
    } catch (reason) {
      if (!silent) setData(null);
      setError(messageOf(reason));
    } finally {
      if (!silent) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [datasetId, market.loadIntelligence]);

  useEffect(() => {
    if (tab !== "selection" || capabilityView !== "overview" || !datasetId) return;
    let activeRequest = true;
    setOpportunityLoading(true);
    setOpportunityError("");
    setOpportunityEndpointUnavailable(false);
    const params = new URLSearchParams({
      dataset_id: datasetId,
      page: String(opportunityPage),
      page_size: String(opportunityPageSize),
    });
    api(`/api/v1/market-intelligence/opportunities?${params}`)
      .then((result) => {
        if (!activeRequest) return;
        setOpportunityData(result);
        setOpportunityEndpointUnavailable(false);
        if (!result.items.length && result.total > 0 && opportunityPage > 1) {
          navigateSelection("overview", {
            page: Math.max(1, Math.ceil(result.total / opportunityPageSize)),
          });
        }
      })
      .catch((reason) => {
        if (!activeRequest) return;
        if (reason?.status === 404 && reason?.code === "RESOURCE_NOT_FOUND") {
          setOpportunityData(null);
          setOpportunityEndpointUnavailable(true);
          setOpportunityError("");
          if (opportunityPage !== 1) navigateSelection("overview", { page: 1 });
        } else {
          setOpportunityError(messageOf(reason));
        }
      })
      .finally(() => {
        if (activeRequest) setOpportunityLoading(false);
      });
    return () => { activeRequest = false; };
  }, [tab, capabilityView, datasetId, opportunityPage, opportunityPageSize]);

  useEffect(() => {
    if (tab !== "selection" || capabilityView !== "detail" || !opportunityId) return;
    let activeRequest = true;
    setOpportunityLoading(true);
    setOpportunityError("");
    setSelectedOpportunity(null);
    api(`/api/v1/market-intelligence/opportunities/${opportunityId}`)
      .then((result) => {
        if (activeRequest) setSelectedOpportunity(result);
      })
      .catch((reason) => {
        if (activeRequest) setOpportunityError(messageOf(reason));
      })
      .finally(() => {
        if (activeRequest) setOpportunityLoading(false);
      });
    return () => { activeRequest = false; };
  }, [tab, capabilityView, opportunityId]);

  useEffect(() => {
    if (tab !== "competitors" || capabilityView !== "overview" || !datasetId) return;
    let activeRequest = true;
    setAlertLoading(true);
    setAlertError("");
    setAlertEndpointUnavailable(false);
    const params = new URLSearchParams({
      dataset_id: datasetId,
      page: String(competitorPage),
      page_size: String(competitorPageSize),
    });
    api(`/api/v1/market-intelligence/competitor-alerts?${params}`)
      .then((result) => {
        if (!activeRequest) return;
        setAlertData(result);
        setAlertEndpointUnavailable(false);
        if (!result.items.length && result.total > 0 && competitorPage > 1) {
          navigateCompetitors("overview", {
            page: Math.max(1, Math.ceil(result.total / competitorPageSize)),
          });
        }
      })
      .catch((reason) => {
        if (!activeRequest) return;
        if (reason?.status === 404 && reason?.code === "RESOURCE_NOT_FOUND") {
          setAlertData(null);
          setAlertEndpointUnavailable(true);
          setAlertError("");
          if (competitorPage !== 1) navigateCompetitors("overview", { page: 1 });
        } else {
          setAlertError(messageOf(reason));
        }
      })
      .finally(() => {
        if (activeRequest) setAlertLoading(false);
      });
    return () => { activeRequest = false; };
  }, [tab, capabilityView, datasetId, competitorPage, competitorPageSize]);

  useEffect(() => {
    if (tab !== "competitors" || capabilityView !== "detail" || !alertId) return;
    let activeRequest = true;
    setAlertLoading(true);
    setAlertError("");
    setSelectedAlert(null);
    api(`/api/v1/market-intelligence/competitor-alerts/${alertId}`)
      .then((result) => {
        if (activeRequest) setSelectedAlert(result);
      })
      .catch((reason) => {
        if (activeRequest) setAlertError(messageOf(reason));
      })
      .finally(() => {
        if (activeRequest) setAlertLoading(false);
      });
    return () => { activeRequest = false; };
  }, [tab, capabilityView, alertId]);

  useEffect(() => {
    if (tab !== "reviews" || capabilityView !== "overview" || !datasetId) return;
    let activeRequest = true;
    setClusterLoading(true);
    setClusterError("");
    setClusterEndpointUnavailable(false);
    const params = new URLSearchParams({
      dataset_id: datasetId,
      page: String(reviewPage),
      page_size: String(reviewPageSize),
    });
    api(`/api/v1/market-intelligence/review-clusters?${params}`)
      .then((result) => {
        if (!activeRequest) return;
        setClusterData(result);
        setClusterEndpointUnavailable(false);
        if (!result.items.length && result.total > 0 && reviewPage > 1) {
          navigateReviews("overview", {
            page: Math.max(1, Math.ceil(result.total / reviewPageSize)),
          });
        }
      })
      .catch((reason) => {
        if (!activeRequest) return;
        if (reason?.status === 404 && reason?.code === "RESOURCE_NOT_FOUND") {
          setClusterData(null);
          setClusterEndpointUnavailable(true);
          setClusterError("");
          if (reviewPage !== 1) navigateReviews("overview", { page: 1 });
        } else {
          setClusterError(messageOf(reason));
        }
      })
      .finally(() => {
        if (activeRequest) setClusterLoading(false);
      });
    return () => { activeRequest = false; };
  }, [tab, capabilityView, datasetId, reviewPage, reviewPageSize]);

  useEffect(() => {
    if (tab !== "reviews" || capabilityView !== "detail" || !clusterId) return;
    let activeRequest = true;
    setClusterLoading(true);
    setClusterError("");
    setSelectedCluster(null);
    api(`/api/v1/market-intelligence/review-clusters/${clusterId}`)
      .then((result) => {
        if (activeRequest) setSelectedCluster(result);
      })
      .catch((reason) => {
        if (activeRequest) setClusterError(messageOf(reason));
      })
      .finally(() => {
        if (activeRequest) setClusterLoading(false);
      });
    return () => { activeRequest = false; };
  }, [tab, capabilityView, clusterId]);

  const simulate = async () => {
    if (!datasetId) return setError("请先选择一份可用市场数据集");
    const cost = unitCost.trim() === "" ? null : Number(unitCost);
    const targetMargin = margin.trim() === "" ? null : Number(margin);
    const promoMarginFloor = Number(promoMargin);
    const discountRate = Number(maxDiscount);
    if (cost != null && (!Number.isFinite(cost) || cost <= 0)) {
      return setPricingFieldError("单位完全成本必须大于 0，或留空仅按市场数据计算。");
    }
    if (targetMargin != null && (!Number.isFinite(targetMargin) || targetMargin < 5 || targetMargin > 85)) {
      return setPricingFieldError("目标毛利率须在 5% 到 85% 之间。");
    }
    if (!Number.isFinite(promoMarginFloor) || promoMarginFloor < 0 || promoMarginFloor > 85) {
      return setPricingFieldError("促销毛利底线须在 0% 到 85% 之间。");
    }
    if (!Number.isFinite(discountRate) || discountRate < 0 || discountRate > 80) {
      return setPricingFieldError("最大折扣率须在 0% 到 80% 之间。");
    }
    if (targetMargin != null && promoMarginFloor > targetMargin) {
      return setPricingFieldError("促销毛利底线不能高于目标毛利率。");
    }
    setBusy(true);
    setError("");
    setPricingFieldError("");
    try {
      const result = await api("/api/v1/market-intelligence/pricing", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: Number(datasetId),
          unit_cost: cost,
          target_margin: targetMargin == null ? null : targetMargin / 100,
          promo_margin_floor: promoMarginFloor / 100,
          max_discount_rate: discountRate / 100,
          comparator_group: pricingGroup || null,
        }),
      });
      setPricing(result);
      setPricingGroup(result.comparator_group || "");
      market.updateIntelligence(datasetId, (current) => current ? { ...current, pricing: result } : current);
    } catch (reason) {
      setPricingFieldError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const active = useMemo(() => capabilities.find(([key]) => key === tab), [tab]);
  const scope = data?.scope || {};
  const overviewOpportunities = data?.smart_selection?.items || [];
  const opportunityTotal = opportunityData?.total ?? data?.smart_selection?.total ?? overviewOpportunities.length;
  const reviews = data?.review_mining || {};
  const competitors = data?.competitor_tracking || {};
  const compliance = data?.compliance || {};
  const pricingDecision = pricing?.decision_support || {};
  const pricingRegular = pricingDecision.regular || {};
  const pricingPromotion = pricingDecision.promotion || {};
  const pricingGroups = pricing?.comparator_groups || [];
  const scenarioDecision = (scenario) => {
    if (scenario.gross_margin == null) return "需补成本";
    if (scenario.code === "regular" && scenario.meets_target_margin === false) return "未达目标毛利";
    if (scenario.code === "market_high") return "高位测试";
    if (scenario.meets_promo_margin === false) return "低于促销毛利";
    return "约束内可执行";
  };
  const capabilityMetrics = {
    selection: opportunityTotal
      ? `${opportunityTotal} 项机会`
      : `${competitors.dataset_listing_count || 0} 竞品 · ${reviews.total || 0} 评论`,
    competitors: `${competitors.active_watch_count || 0} 监控 · ${competitors.dataset_listing_count || 0} 样本`,
    reviews: `${reviews.total || 0} 评论 · ${reviews.negative || 0} 负向`,
    pricing: `${pricing?.sample_size || 0} 个价格${pricing?.recommended_price ? ` · ${money(pricing.recommended_price, pricing.currency)}` : ""}`,
    compliance: `${compliance.source_count || 0} 来源 · ${compliance.unread_count || 0} 未读`,
  };
  const startAnalysis = () => {
    if (!datasetId) return;
    const workspace = createUuid();
    location.hash = `workflow?new=1&workspace=${workspace}&dataset=${datasetId}&name=${encodeURIComponent(`${scope.dataset_name || "市场数据"} 选品分析`)}`;
  };

  return <section className="market-capability-hub intelligence-hub">
    <header>
      <div>
        <span>MARKET INTELLIGENCE</span>
        <strong>五项市场决策能力</strong>
        <p>选品、竞品、评论与定价使用当前数据集；合规预警按数据集国家和品类匹配企业政策源。</p>
      </div>
      <label className="intelligence-scope">
        分析数据集
        <select value={datasetId} onChange={(event) => {
          setDatasetId(event.target.value);
          setOpportunityData(null);
          setSelectedOpportunity(null);
          setOpportunityPage(1);
          setAlertData(null);
          setSelectedAlert(null);
          setCompetitorPage(1);
          setClusterData(null);
          setSelectedCluster(null);
          setReviewPage(1);
          setPricingGroup("");
          market.setSelectedDatasetId(event.target.value);
          if (tab === "selection") {
            navigateSelection("overview", { page: 1 });
          } else if (tab === "competitors") {
            navigateCompetitors("overview", { page: 1 });
          } else if (tab === "reviews") {
            navigateReviews("overview", { page: 1 });
          }
        }}>
          {!ready.length && <option value="">暂无可用数据集</option>}
          {ready.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.name}</option>)}
        </select>
      </label>
    </header>
    <nav className="intelligence-tabs" aria-label="市场智能能力">
      {capabilities.map(([key, Icon, label]) => (
        <button type="button" key={key} className={tab === key ? "active" : ""} onClick={() => navigateCapability(key)}>
          <Icon /><span><strong>{label}</strong><small>{capabilityMetrics[key]}</small></span>
        </button>
      ))}
    </nav>
    {error && <div className="intelligence-error"><WarningCircle />{error}</div>}
    {loading ? <div className="intelligence-loading"><SpinnerGap />正在计算市场证据…</div> : error && !data ? <div className="intelligence-empty"><span>市场证据读取失败</span><button className="intelligence-action" onClick={() => load(true)}>重新读取</button></div> : (
      <div className="intelligence-body">
        <header className="intelligence-body-header">
          <div><strong>{active?.[2]}</strong><small>{scope.dataset_name || "等待导入授权数据"}</small></div>
          <span>{data?.generated_at ? `更新 ${dateText(data.generated_at)}` : "未计算"}</span>
        </header>

        {tab === "selection" && (
          <Suspense fallback={<div className="intelligence-loading"><SpinnerGap />正在加载选品视图…</div>}>
            <SelectionWorkspace
              view={capabilityView}
              datasetId={datasetId}
              page={opportunityPage}
              pageSize={opportunityPageSize}
              pageData={opportunityData}
              loading={opportunityLoading}
              error={opportunityError}
              endpointUnavailable={opportunityEndpointUnavailable}
              selectedOpportunity={selectedOpportunity}
              overview={{
                opportunities: overviewOpportunities,
                total: data?.smart_selection?.total,
                datasetName: scope.dataset_name,
                competitorCount: competitors.dataset_listing_count,
                reviewCount: reviews.total,
                priceCount: pricing?.sample_size,
              }}
              onNavigate={navigateSelection}
              onStartAnalysis={startAnalysis}
            />
          </Suspense>
        )}

        {tab === "competitors" && <div className="capability-workspace">
          <CapabilityViewNav
            view={capabilityView}
            workbenchView="prices"
            workbenchLabel="监控工作台"
            onOverview={() => navigateCompetitors("overview")}
            onWorkbench={() => navigateCapability("competitors", "prices")}
          />
          {["overview", "detail"].includes(capabilityView) ? <CompetitorEvidenceView
            view={capabilityView}
            page={competitorPage}
            pageSize={competitorPageSize}
            pageData={alertData}
            loading={alertLoading}
            error={alertError}
            endpointUnavailable={alertEndpointUnavailable}
            selectedAlert={selectedAlert}
            overview={competitors}
            onNavigate={navigateCompetitors}
          /> : <Suspense fallback={<div className="intelligence-loading"><SpinnerGap />正在加载竞品工作台…</div>}>
            <CompetitorTrackingWorkspace initialView={capabilityView} />
          </Suspense>}
        </div>}

        {tab === "reviews" && <div className="capability-workspace">
          <CapabilityViewNav
            view={capabilityView}
            workbenchView="stream"
            workbenchLabel="舆情工作台"
            onOverview={() => navigateReviews("overview")}
            onWorkbench={() => navigateCapability("reviews", "stream")}
          />
          {["overview", "detail"].includes(capabilityView) ? <ReviewEvidenceView
            view={capabilityView}
            page={reviewPage}
            pageSize={reviewPageSize}
            pageData={clusterData}
            loading={clusterLoading}
            error={clusterError}
            endpointUnavailable={clusterEndpointUnavailable}
            selectedCluster={selectedCluster}
            overview={reviews}
            onNavigate={navigateReviews}
          /> : <Suspense fallback={<div className="intelligence-loading"><SpinnerGap />正在加载舆情工作台…</div>}>
            <SentimentStreamPanel datasets={datasets} />
          </Suspense>}
        </div>}

        {tab === "pricing" && <div className="pricing-workbench">
          <div className="pricing-controls">
            <label className="pricing-group-control">
              可比竞品组
              <select value={pricingGroup} onChange={(event) => { setPricingGroup(event.target.value); setPricingFieldError(""); }}>
                {!pricingGroups.length && <option value="">全部有效价格</option>}
                {pricingGroups.map((group) => (
                  <option key={group.group_code} value={group.group_code}>
                    {group.group_label}（{group.sample_size}）
                  </option>
                ))}
              </select>
            </label>
            <label>单位完全成本<input type="number" min="0.01" step="0.01" value={unitCost} onChange={(event) => { setUnitCost(event.target.value); setPricingFieldError(""); }} placeholder={pricing?.unit_cost ?? "例如 120"} /></label>
            <label>目标毛利率<input type="number" min="5" max="85" value={margin} onChange={(event) => { setMargin(event.target.value); setPricingFieldError(""); }} /><span>%</span></label>
            <label>促销毛利底线<input type="number" min="0" max="85" value={promoMargin} onChange={(event) => { setPromoMargin(event.target.value); setPricingFieldError(""); }} /><span>%</span></label>
            <label>最大折扣率<input type="number" min="0" max="80" value={maxDiscount} onChange={(event) => { setMaxDiscount(event.target.value); setPricingFieldError(""); }} /><span>%</span></label>
            <button type="button" onClick={simulate} disabled={busy}>{busy ? "计算中…" : "重新计算"}</button>
          </div>
          {pricingFieldError && <p role="alert" className="pricing-field-error"><WarningCircle />{pricingFieldError}</p>}
          {pricing?.recommended_price != null ? <div className="pricing-result">
            <div className="pricing-scope-summary">
              <Crosshair />
              <span>
                <small>当前可比范围</small>
                <strong>{pricing.comparator_group_label || "全部有效价格"} · {pricing.sample_size} / {pricing.dataset_sample_size || pricing.sample_size} 个价格</strong>
              </span>
              <em>按结构化商品类目匹配</em>
            </div>
            <article><small>市场核心价格带</small><strong>{money(pricing.market_low, pricing.currency)} - {money(pricing.market_high, pricing.currency)}</strong></article>
            <article className="primary"><small>建议常规售价</small><strong>{money(pricing.recommended_price, pricing.currency)}</strong></article>
            <article><small>促销价格底线</small><strong>{money(pricing.promo_floor, pricing.currency)}</strong></article>
            <section className="pricing-decision-panel">
              <header>
                <div>
                  <small>建议动作</small>
                  <strong>{pricingDecision.recommended_action?.headline || pricing.message}</strong>
                </div>
                <span className={pricing.cost_basis === "planning_assumption" ? "warning" : ""}>
                  {pricing.cost_basis === "planning_assumption" ? "规划成本 · 待确认" : "成本已纳入计算"}
                </span>
              </header>
              <p>{pricingDecision.recommended_action?.guardrail} {pricingDecision.recommended_action?.next_step}</p>
              <dl>
                <div><dt>常规价单位毛利</dt><dd>{money(pricingRegular.gross_profit_per_unit, pricing.currency)}</dd></div>
                <div><dt>常规价实际毛利率</dt><dd>{percent(pricingRegular.gross_margin)}</dd></div>
                <div><dt>市场位置</dt><dd>{pricingDecision.market_position || "—"} · P{Math.round(Number(pricingDecision.market_percentile || 0) * 100)}</dd></div>
                <div><dt>促销价单位毛利</dt><dd>{money(pricingPromotion.gross_profit_per_unit, pricing.currency)}</dd></div>
                <div><dt>促销价实际毛利率</dt><dd>{percent(pricingPromotion.gross_margin)}</dd></div>
                <div><dt>实际最大折扣</dt><dd>{percent(pricingPromotion.discount_from_regular)}</dd></div>
              </dl>
            </section>
            <section className="pricing-scenario-section">
              <header>
                <div><strong>价格场景对比</strong><small>低位不是推荐价；用于判断降价会损失多少利润。</small></div>
              </header>
              <div className="pricing-scenario-table" role="table" aria-label="价格场景对比">
                <div className="head" role="row"><span>方案</span><span>售价</span><span>相对常规价</span><span>单位毛利</span><span>毛利率</span><span>判断</span></div>
                {(pricingDecision.scenarios || []).map((scenario) => (
                  <div role="row" key={scenario.code} className={scenario.code === "regular" ? "recommended" : ""}>
                    <strong>{scenario.label}</strong>
                    <span data-label="售价">{money(scenario.price, pricing.currency)}</span>
                    <span data-label="相对常规价">{signedPercent(scenario.price_change_from_regular)}</span>
                    <span data-label="单位毛利">{money(scenario.gross_profit_per_unit, pricing.currency)}</span>
                    <span data-label="毛利率">{percent(scenario.gross_margin)}</span>
                    <em data-label="判断" className={scenarioDecision(scenario).includes("可执行") ? "ok" : ""}>{scenarioDecision(scenario)}</em>
                  </div>
                ))}
              </div>
            </section>
            <p className="pricing-evidence-note"><CheckCircle />{pricing.scope_message} {pricing.message} 置信度 {Math.round(Number(pricing.confidence || 0) * 100)}%。{pricing.review_growth_price_sensitivity == null
              ? `监控覆盖 ${pricing.history_coverage?.tracked_targets || 0} 个目标、${pricing.history_coverage?.observation_times || 0} 个观测时点，暂不输出代理敏感度。`
              : `评论增长代理敏感度 ${pricing.review_growth_price_sensitivity}，仅用于探索，不等同销量价格弹性。`}</p>
            <dl className="pricing-provenance">
              <div><dt>促销主导约束</dt><dd>{pricingDecision.binding_constraint?.label || "—"}</dd></div>
              <div><dt>目标毛利价底线</dt><dd>{money(pricing.price_floor, pricing.currency)}</dd></div>
              <div><dt>促销毛利底线</dt><dd>{money(pricing.promo_floor_components?.promo_margin_floor, pricing.currency)}</dd></div>
              <div><dt>计算口径</dt><dd>{pricing.calculation_version || "历史口径"}</dd></div>
            </dl>
          </div> : <Empty>{pricing?.message || "当前数据集没有有效价格。"}</Empty>}
        </div>}

        {tab === "compliance" && <Suspense fallback={<div className="intelligence-loading"><SpinnerGap />正在加载合规工作台…</div>}>
          <ComplianceWorkspace
            overview={compliance}
            scope={scope}
            onOverviewChange={() => load(true, true)}
          />
        </Suspense>}
      </div>
    )}
  </section>;
}
