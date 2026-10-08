import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BellRinging,
  ChartBar,
  ChatCircleDots,
  CloudArrowUp,
  CurrencyDollar,
  Database,
  Package,
  ShieldWarning,
  Storefront,
} from "@phosphor-icons/react";
import { MarketIntelligenceHub } from "./MarketIntelligenceHub.jsx";
import { NotificationChannels } from "./NotificationChannels.jsx";
import { useMarketInsights } from "./MarketInsightsContext.jsx";
import { MarketPageFrame } from "./MarketWorkspace.jsx";

const moduleRows = [
  ["market-data", Database, "数据资产", "授权数据集、质量与版本"],
  ["market-decisions?capability=competitors&view=prices", Storefront, "竞品分析", "商品页价格与 Listing 变化"],
  ["market-decisions?capability=reviews&view=stream", ChatCircleDots, "评论舆情", "评论页采集与需求证据"],
  ["market-automation", BellRinging, "自动化投递", "竞品变化与政策预警渠道"],
];

function useMarketOverview() {
  const market = useMarketInsights();
  const [intelligenceLoading, setIntelligenceLoading] = useState(true);
  const [intelligenceError, setIntelligenceError] = useState("");

  useEffect(() => {
    market.refreshDatasets().catch(() => {});
  }, [market.refreshDatasets]);

  const ready = useMemo(
    () => market.datasets.filter((item) => item.status === "ready"),
    [market.datasets],
  );
  const datasetId = market.selectedDatasetId || String(ready[0]?.dataset_id || "");

  useEffect(() => {
    if (!market.datasetsLoaded) return;
    setIntelligenceLoading(true);
    setIntelligenceError("");
    market.loadIntelligence(datasetId || null)
      .catch((reason) => setIntelligenceError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setIntelligenceLoading(false));
  }, [datasetId, market.datasetsLoaded, market.loadIntelligence]);

  return {
    ...market,
    ready,
    datasetId,
    intelligence: market.getIntelligence(datasetId || null),
    intelligenceLoading,
    intelligenceError,
  };
}

function useSharedDatasets() {
  const market = useMarketInsights();
  useEffect(() => {
    market.refreshDatasets().catch(() => {});
  }, [market.refreshDatasets]);
  return market;
}

export function MarketInsightsHome({ Sidebar, Topbar }) {
  const state = useMarketOverview();
  const summary = useMemo(() => state.datasets.reduce((result, item) => ({
    listings: result.listings + Number(item.listing_count || 0),
    reviews: result.reviews + Number(item.review_count || 0),
    valid: result.valid + Number(item.valid_review_count || 0),
  }), { listings: 0, reviews: 0, valid: 0 }), [state.datasets]);
  const intelligence = state.intelligence || {};
  const selection = intelligence.smart_selection?.items || [];
  const competitors = intelligence.competitor_tracking || {};
  const reviews = intelligence.review_mining || {};
  const pricing = intelligence.pricing || {};
  const compliance = intelligence.compliance || {};
  const activeDataset = state.ready.find((item) => String(item.dataset_id) === String(state.datasetId));

  const capabilityRows = [
    [Package, "AI 智能选品", selection.length, "项机会", "selection"],
    [ChartBar, "竞品动态", competitors.active_watch_count || 0, "个监控", "competitors"],
    [ChatCircleDots, "评论证据", reviews.total || 0, "条评论", "reviews"],
    [CurrencyDollar, "定价样本", pricing.sample_size || 0, "个价格", "pricing"],
    [ShieldWarning, "合规来源", compliance.source_count || 0, "个来源", "compliance"],
  ];

  return (
    <MarketPageFrame Sidebar={Sidebar} Topbar={Topbar} active="home">
      <div className="market-page-canvas market-home">
        <header className="market-page-heading">
          <div>
            <span>MARKET INTELLIGENCE</span>
            <h1>市场洞察</h1>
            <p>市场证据、决策能力、采集任务与数据资产的统一工作入口。</p>
          </div>
          <button type="button" onClick={() => { location.hash = "market-data?import=1"; }}><CloudArrowUp />导入市场数据</button>
        </header>

        <section className="market-home-status">
          <header>
            <div><span>当前分析范围</span><strong>{activeDataset?.name || "尚无可用数据集"}</strong></div>
            <label>
              切换数据集
              <select value={state.datasetId} onChange={(event) => state.setSelectedDatasetId(event.target.value)}>
                {!state.ready.length && <option value="">暂无可用数据集</option>}
                {state.ready.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.name}</option>)}
              </select>
            </label>
          </header>
          {state.datasetsError || state.intelligenceError ? (
            <p className="market-home-error">{state.datasetsError || state.intelligenceError}</p>
          ) : (
            <div className="market-home-capabilities">
              {capabilityRows.map(([Icon, label, value, unit, capability]) => (
                <button type="button" key={label} onClick={() => { location.hash = `market-decisions?capability=${capability}&view=overview`; }}>
                  <Icon />
                  <small>{label}</small>
                  <span className="market-home-capability-value"><strong>{state.intelligenceLoading ? "…" : Number(value).toLocaleString("zh-CN")}</strong><em>{unit}</em></span>
                  <ArrowRight />
                </button>
              ))}
            </div>
          )}
          <footer>
            <span>数据资产：{summary.listings.toLocaleString("zh-CN")} 个竞品 · {summary.valid.toLocaleString("zh-CN")} 条有效评论</span>
            <button type="button" onClick={() => { location.hash = "market-decisions"; }}>进入决策中心 <ArrowRight /></button>
          </footer>
        </section>

        <div className="market-home-grid">
          <section className="market-home-module-list">
            <header><div><span>业务模块</span><strong>采集、数据与自动化</strong></div></header>
            {moduleRows.map(([route, Icon, label, hint]) => (
              <button type="button" key={route} onClick={() => { location.hash = route; }}>
                <Icon />
                <span><strong>{label}</strong><small>{hint}</small></span>
                <ArrowRight />
              </button>
            ))}
          </section>
          <section className="market-home-data-health">
            <header><div><span>数据健康度</span><strong>当前资产覆盖</strong></div><button type="button" onClick={() => { location.hash = "market-data"; }}>查看资产</button></header>
            <dl>
              <div><dt>可用数据集</dt><dd>{state.ready.length}</dd></div>
              <div><dt>竞品商品</dt><dd>{summary.listings.toLocaleString("zh-CN")}</dd></div>
              <div><dt>有效评论</dt><dd>{summary.valid.toLocaleString("zh-CN")}</dd></div>
              <div><dt>评论留存率</dt><dd>{summary.reviews ? `${Math.round(summary.valid / summary.reviews * 100)}%` : "—"}</dd></div>
            </dl>
            {!!intelligence.data_gaps?.length && <div className="market-home-gaps"><strong>决策边界</strong>{intelligence.data_gaps.slice(0, 3).map((item) => <span key={item}>{item}</span>)}</div>}
          </section>
        </div>
      </div>
    </MarketPageFrame>
  );
}

export function MarketDecisionCenter({ Sidebar, Topbar }) {
  const { datasets, datasetsLoaded, datasetsLoading, datasetsError, refreshDatasets } = useSharedDatasets();
  return (
    <MarketPageFrame Sidebar={Sidebar} Topbar={Topbar} active="decisions">
      <div className="market-page-canvas">
        <header className="market-page-heading">
          <div><span>DECISION CENTER</span><h1>市场决策中心</h1><p>在统一数据范围内完成选品、竞品、评论、定价和合规判断。</p></div>
        </header>
        {datasetsError && <div className="dataset-error">{datasetsError}<button type="button" onClick={() => refreshDatasets({ force: true })}>重新读取</button></div>}
        {(!datasetsLoaded || datasetsLoading) && !datasets.length ? <div className="dataset-empty">正在读取市场数据…</div> : <MarketIntelligenceHub datasets={datasets} />}
      </div>
    </MarketPageFrame>
  );
}

export function MarketAutomationCenter({ Sidebar, Topbar }) {
  return (
    <MarketPageFrame Sidebar={Sidebar} Topbar={Topbar} active="automation">
      <div className="market-page-canvas market-automation-page">
        <header className="market-page-heading">
          <div><span>AUTOMATION</span><h1>自动化投递</h1><p>集中维护竞品变化和政策预警的外部接收渠道与投递记录。</p></div>
        </header>
        <NotificationChannels defaultOpen />
      </div>
    </MarketPageFrame>
  );
}

export function MarketLegacyRedirect({ target }) {
  useEffect(() => {
    location.replace(`${location.pathname}${location.search}#${target}`);
  }, [target]);
  return <div className="route-loading">正在进入决策中心…</div>;
}
