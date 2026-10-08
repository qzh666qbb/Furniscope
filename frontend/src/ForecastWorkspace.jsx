import { useEffect, useState } from "react";
import { Check, Database, DownloadSimple, MagnifyingGlass, TrendUp, WarningCircle, X } from "@phosphor-icons/react";
import { api, idempotencyKey } from "./api.js";
import { ParentPageTab } from "./ParentPageTab.jsx";
import { EnterpriseTraining } from "./EnterpriseTraining.jsx";

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const split = value => value.split(/[,，;；\s]+/).filter(Boolean);
const FORECAST_SITES = ["US", "CA", "DE", "FR", "IT", "ES", "NL"];
const optionalNumber = value => value === "" || value === null ? null : Number(value);
const units = value => value == null ? "待验证" : `${value} 件`;
const methodLabels = { xgboost: "独立模型", intermittent_mean: "间歇销量均值", recent_mean: "近期均值", explicit_baseline: "手动基线", reference_sku: "参考 SKU" };

function ForecastBasis({ summaries = [] }) {
  return summaries.filter(item => item.method).map(item => <p key={`${item.sku}-${item.site}`} className="forecast-site-hint">
    {item.sku} / {item.site} · {methodLabels[item.method] || item.method} · {item.validation_status === "validated" ? (item.validation_scope === "window_total" ? "仅整个窗口总量已验证" : "已通过时间窗口评测") : "此范围尚未验证精度，不生成误差区间或安全库存建议"}
    {item.data_through ? ` · 该 SKU 数据至 ${item.data_through}` : ""}
  </p>);
}

function download(points, type, filename = "furniscope-forecast") {
  const fields = [
    ["bucket_start", "日期"],
    ["site", "站点"],
    ["sku", "SKU"],
    ["predicted_sales", "预测销量"],
    ["lower", "下界"],
    ["upper", "上界"],
    ["interval", "销量预测区间"],
    ["reliability", "可靠度值"],
  ];
  const csvCell = value => {
    const text = value ?? "";
    return /[",\n]/.test(String(text)) ? `"${String(text).replaceAll('"', '""')}"` : text;
  };
  const rows = points.map(row => ({
    ...row,
    interval: row.lower != null && row.upper != null ? `${row.lower} ~ ${row.upper}` : "",
  }));
  const content = type === "json" ? JSON.stringify(points, null, 2)
    : `\uFEFF${[fields.map(([, label]) => label), ...rows.map(row => fields.map(([field]) => csvCell(row[field])))].map(row => row.join(",")).join("\n")}`;
  const url = URL.createObjectURL(new Blob([content], { type: type === "json" ? "application/json" : "text/csv;charset=utf-8" }));
  const safeName = filename.replace(/[\\/:*?"<>|]/g, "-");
  const link = document.createElement("a"); link.href = url; link.download = `${safeName}.${type}`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 800);
}

const forecastStatusLabels = {
  draft: "待启动", queued: "排队中", running: "预测中", succeeded: "已完成",
  failed: "失败", cancelled: "已取消",
};

function ForecastHistoryTab() {
  const [items, setItems] = useState([]), [page, setPage] = useState(1), [totalPages, setTotalPages] = useState(1);
  const [query, setQuery] = useState(""), [statusFilter, setStatusFilter] = useState(""), [granularity, setGranularity] = useState("");
  const [loading, setLoading] = useState(true), [message, setMessage] = useState(""), [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ page: String(page), page_size: "10" });
      if (query.trim()) params.set("q", query.trim());
      if (statusFilter) params.set("status", statusFilter);
      if (granularity) params.set("granularity", granularity);
      setLoading(true); setMessage("");
      api(`/api/v1/forecast-jobs?${params}`)
        .then(data => {
          if (cancelled) return;
          setItems(data.items || []);
          setTotalPages(Math.max(1, data.total_pages || 1));
        })
        .catch(error => { if (!cancelled) setMessage(error.message); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [page, query, statusFilter, granularity]);

  const readResult = async job => {
    if (job.status !== "succeeded") throw new Error("该预测任务尚未生成可查看的结果");
    return api(`/api/v1/forecast-jobs/${job.job_uuid}/result`);
  };
  const openResult = async job => {
    try {
      setMessage(""); setDetailLoading(true);
      setDetail(await readResult(job));
    } catch (error) { setMessage(error.message); }
    finally { setDetailLoading(false); }
  };
  const exportResult = async (job, type) => {
    try {
      setMessage("");
      const result = await readResult(job);
      download(result.points, type, `${job.job_name}-${job.job_uuid.slice(0, 8)}`);
    } catch (error) { setMessage(error.message); }
  };
  const resetPage = setter => event => { setter(event.target.value); setPage(1); };
  const detailMetrics = detail?.metrics || {};

  return <section className="forecast-history-page">
    <header><div><h2>历史预测记录</h2><p>筛选和查找已执行的预测任务，查看或导出完整预测结果。</p></div></header>
    <div className="forecast-history-filters">
      <label><MagnifyingGlass/><input aria-label="查找预测记录" value={query} onChange={resetPage(setQuery)} placeholder="搜索任务名称、SKU 或站点"/></label>
      <select aria-label="筛选任务状态" value={statusFilter} onChange={resetPage(setStatusFilter)}><option value="">全部状态</option><option value="succeeded">已完成</option><option value="running">预测中</option><option value="queued">排队中</option><option value="failed">失败</option></select>
      <select aria-label="筛选预测类型" value={granularity} onChange={resetPage(setGranularity)}><option value="">全部类型</option><option value="day">天预测</option><option value="week">周预测</option></select>
    </div>
    {message && <div className="forecast-form-error"><WarningCircle/>{message}</div>}
    <div className="forecast-history-table">
      <table><thead><tr><th>预测任务</th><th>预测范围</th><th>类型</th><th>创建时间</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>{!loading && items.map(job => <tr key={job.job_uuid}>
          <td><strong>{job.job_name}</strong><small>{job.job_uuid.slice(0, 8)}</small></td>
          <td><strong>{job.skus.join("、")}</strong><small>{job.sites.join("、")} · {job.horizon} 个周期</small></td>
          <td>{job.granularity === "day" ? "天预测" : "周预测"}</td>
          <td>{new Date(job.created_at).toLocaleString("zh-CN")}</td>
          <td><span className={`forecast-history-state ${job.status}`}>{forecastStatusLabels[job.status] || job.status}</span></td>
          <td><div className="forecast-history-actions"><button onClick={() => openResult(job)} disabled={job.status !== "succeeded" || detailLoading}>查看预测结果</button><button onClick={() => exportResult(job, "csv")} disabled={job.status !== "succeeded"}>CSV</button><button onClick={() => exportResult(job, "json")} disabled={job.status !== "succeeded"}>JSON</button></div></td>
        </tr>)}</tbody>
      </table>
      {loading && <div className="forecast-history-loading">正在读取预测记录…</div>}
      {!loading && !items.length && <div className="forecast-history-loading">没有符合当前筛选条件的预测记录。</div>}
    </div>
    <footer className="forecast-history-pagination"><button onClick={() => setPage(value => value - 1)} disabled={page <= 1}>上一页</button><span>第 {page} / {totalPages} 页</span><button onClick={() => setPage(value => value + 1)} disabled={page >= totalPages}>下一页</button></footer>
    {detail && <div className="forecast-result-modal-backdrop" onClick={() => setDetail(null)}><section className="forecast-result-modal" onClick={event => event.stopPropagation()}>
      <header><div><h2>预测结果</h2><p>{detail.job.job_name} · {detail.job.granularity === "day" ? "天预测" : "周预测"}</p></div><button onClick={() => setDetail(null)} aria-label="关闭预测结果"><X/></button></header>
      <div className="forecast-result-modal-actions"><button onClick={() => download(detail.points, "csv", detail.job.job_name)}><DownloadSimple/>导出 CSV</button><button onClick={() => download(detail.points, "json", detail.job.job_name)}><DownloadSimple/>导出 JSON</button></div>
      <div className="forecast-output-kpis">{[["预测组合", detailMetrics.pair_count], ["预测总销量", units(detailMetrics.total_forecast)], ["安全库存", units(detailMetrics.safety_stock)], ["建议生产量", units(detailMetrics.recommended_production)]].map(([label, value]) => <span key={label}><small>{label}</small><strong>{value}</strong></span>)}</div>
      <ForecastBasis summaries={detail.summaries}/>
      <div className="forecast-result-table"><table><thead><tr>{["日期", "站点", "SKU", "预测销量", "下界", "上界", "销量预测区间", "可靠度值"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{detail.points.map((row, index) => <tr key={`${row.sku}-${row.site}-${row.bucket_start}-${index}`}><td>{row.bucket_start}</td><td>{row.site}</td><td>{row.sku}</td><td>{row.predicted_sales}</td><td>{row.lower ?? "—"}</td><td>{row.upper ?? "—"}</td><td>{row.lower != null && row.upper != null ? `${row.lower} ~ ${row.upper}` : "—"}</td><td>{row.reliability ?? "—"}</td></tr>)}</tbody></table></div>
    </section></div>}
  </section>;
}

export function ForecastWorkspace({ Sidebar, Topbar }) {
  const routeParams = new URLSearchParams(location.hash.split("?")[1] || "");
  const requestedSku = routeParams.get("sku") || "";
  const requestedProductId = Number(routeParams.get("product")) || null;
  const fromProducts = routeParams.get("from") === "products";
  const [model, setModel] = useState(null), [sku, setSku] = useState(requestedSku), [selectedSites, setSelectedSites] = useState(["US"]);
  const [activeTab, setActiveTab] = useState("forecast");
    const [mode, setMode] = useState("day"), [businessGranularity, setBusinessGranularity] = useState("day");
  const [days, setDays] = useState("7"), [weeks, setWeeks] = useState("4"), [startDate, setStartDate] = useState("");
  const [price, setPrice] = useState(""), [discount, setDiscount] = useState(""), [inventory, setInventory] = useState("");
  const [promotionImpact, setPromotionImpact] = useState(""), [baseline, setBaseline] = useState(""), [referenceSku, setReferenceSku] = useState("");
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(""), [result, setResult] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const reloadModel = () => api("/api/v1/forecast/status").then(setModel);
  const reloadCatalog = () => api("/api/v1/forecast/skus?limit=5000").then(page => setCatalog(page.items || []));
  useEffect(() => {
    Promise.all([
      api("/api/v1/forecast/status"),
      api("/api/v1/forecast/skus?limit=5000"),
    ]).then(([nextModel, nextCatalog]) => {
      setModel(nextModel);
      setCatalog(nextCatalog.items || []);
    }).catch(err => setError(err.message));
  }, []);

  useEffect(() => {
    const sites = [...new Set(catalog.filter(item => item.sku === sku.trim() && item.model_eligible).map(item => item.site))];
    if (!sites.length) return;
    setSelectedSites(current => {
      const kept = current.filter(site => sites.includes(site));
      return kept.length ? kept : [sites[0]];
    });
  }, [catalog, sku]);

  const skuReady = catalog.some(item => item.sku === sku.trim() && item.model_eligible);
  const uniqueSkus = [...new Set(catalog.map(item => item.sku))];
  const matchingSites = [...new Set(catalog.filter(item => item.sku === sku.trim() && item.model_eligible).map(item => item.site))].sort();
  const uniqueSites = matchingSites.length ? matchingSites : FORECAST_SITES;
  const effectiveGranularity = mode === "business" ? businessGranularity : mode;
  const period = effectiveGranularity === "day" ? days : weeks;
  const historicalOnly = model?.engine?.startsWith("tenant-xgb-");

  const toggleSite = site => {
    if (selectedSites.includes(site)) {
      if (selectedSites.length === 1) return setError("至少保留一个预测站点");
      setSelectedSites(values => values.filter(value => value !== site));
    } else {
      setSelectedSites(values => [...values, site]);
    }
    setError("");
  };

  const run = async () => {
    const skus = split(sku), siteList = selectedSites, horizon = Number(period);
    if (!skus.length || !siteList.length) return setError("请填写至少一个 SKU 和站点");
    const unknownSkus = skus.filter(value => !uniqueSkus.includes(value));
    if (unknownSkus.length) return setError(`只能预测产品中心现有 SKU：${unknownSkus.join("、")}`);
    if (!Number.isInteger(horizon) || horizon < 1 || horizon > (effectiveGranularity === "day" ? 365 : 52)) return setError("预测周期超出允许范围");
    const scenario = mode === "business" ? {
      price: historicalOnly ? null : optionalNumber(price), discount: historicalOnly ? null : optionalNumber(discount), inventory: historicalOnly ? null : optionalNumber(inventory),
      is_promotion: !historicalOnly && promotionImpact !== "", promotion_impact: historicalOnly ? null : optionalNumber(promotionImpact),
      baseline: optionalNumber(baseline), reference_sku: referenceSku.trim() || null,
    } : {};
    try {
      setError(""); setResult(null); setStatus("creating");
      const created = await api("/api/v1/forecast-jobs", { method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("forecast-create") },
        body: JSON.stringify({ job_name: `${skus.join(",")} 销量预测`, product_id: requestedProductId, granularity: effectiveGranularity, horizon,
          start_date: startDate || null, skus, sites: siteList, scenario }) });
      setStatus("running");
      await api(`/api/v1/forecast-jobs/${created.job_uuid}:start`, { method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("forecast-start") } });
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await wait(1000);
        const job = await api(`/api/v1/forecast-jobs/${created.job_uuid}`);
        if (job.status === "failed") throw new Error(job.failure_message || "预测执行失败");
        if (job.status === "succeeded") { setResult(await api(`/api/v1/forecast-jobs/${created.job_uuid}/result`)); setStatus("done"); return; }
      }
      throw new Error("预测仍在运行，请稍后在预测历史中查看");
    } catch (err) { setStatus("idle"); setError(err.message); }
  };
  const metrics = result?.metrics || {};
  return <main className="workspace forecast-page"><Sidebar page="forecast"/><section className="workspace-main"><Topbar/><div className="forecast-workspace">
    {fromProducts && <ParentPageTab label="产品中心" current={`${requestedSku || "产品"} 销量预测`} to="products" />}
    <header className="forecast-hero-card">
      <div><h1>商品销量预测</h1><p>按 SKU 和站点生成销量区间，并形成可执行的备货建议。{model?.ready && model?.reported_backtest?.candidate_metrics?.wape_pct != null ? `本环境回测 WAPE ${Number(model.reported_backtest.candidate_metrics.wape_pct).toFixed(1)}%（${model.reported_backtest.catalog_audit?.scored_skus || "—"}/${model.reported_backtest.catalog_audit?.catalog_skus || "—"} SKU）。` : ""}</p></div>
      <div className={`forecast-current-model ${model && !model.ready ? "unavailable" : ""}`}>
        <Database/><span>当前模型<strong>{model?.ready ? `已就绪 · 数据更新至 ${model.data_through || "—"}` : model ? "模型暂不可用" : "正在检查模型状态"}</strong></span>
      </div>
    </header>
    <nav className="forecast-tabs" aria-label="销量预测功能">
      <button className={activeTab === "forecast" ? "active" : ""} onClick={() => setActiveTab("forecast")}>销量预测</button>
      <button className={activeTab === "history" ? "active" : ""} onClick={() => setActiveTab("history")}>历史预测记录</button>
      <button className={activeTab === "append" ? "active" : ""} onClick={() => setActiveTab("append")}>模型训练</button>
    </nav>
    {activeTab === "forecast" ? <>
    <section className="forecast-prediction-card">
      <header><h2>选择使用方式</h2><p>普通预测只需填写 SKU、站点和预测周期</p></header>
      {fromProducts && !skuReady && <div className="forecast-product-notice"><WarningCircle/><span><strong>已从产品中心带入 {requestedSku}</strong>该 SKU 还没有映射到训练历史，请改走复杂业务预测并填写基准日销量或参考 SKU。</span></div>}
      {fromProducts && skuReady && <div className="forecast-product-notice"><span><strong>已从产品中心带入 {requestedSku}</strong>已对接训练历史，可直接预测。</span></div>}
      <div className="forecast-mode-grid">
        <button className={mode === "day" ? "active" : ""} onClick={() => setMode("day")}><strong>天预测</strong><span>查看未来每天的销量和波动区间</span></button>
        <button className={mode === "week" ? "active" : ""} onClick={() => setMode("week")}><strong>周预测</strong><span>查看未来每周汇总销量</span></button>
        <button className={mode === "business" ? "active" : ""} onClick={() => setMode("business")}><strong>复杂业务预测</strong><span>{historicalOnly ? "设置新品基准销量或参考 SKU" : "指定价格、库存、促销或新品参数"}</span></button>
      </div>
      <div className="forecast-primary-fields">
        <label>产品中心 SKU<input list="forecast-sku-options" value={sku} onChange={event => setSku(event.target.value)} readOnly={fromProducts} placeholder="多个 SKU 可用空格或逗号分隔"/><datalist id="forecast-sku-options">{uniqueSkus.map(value => <option value={value} key={value}/>)}</datalist></label>
        <div className="forecast-site-field"><span>站点 <small>可多选 · 已选 {selectedSites.length}</small></span><div role="group" aria-label="选择预测站点">{uniqueSites.map(site => <button key={site} className={selectedSites.includes(site) ? "active" : ""} aria-pressed={selectedSites.includes(site)} onClick={() => toggleSite(site)}>{site}</button>)}</div></div>
        <label>预测{effectiveGranularity === "day" ? "天数" : "周数"}<input type="number" min="1" max={effectiveGranularity === "day" ? "365" : "52"} value={period} onChange={event => effectiveGranularity === "day" ? setDays(event.target.value) : setWeeks(event.target.value)}/></label>
        <label className="forecast-optional-field"><span>起始日期</span><small>可选</small><input type="date" value={startDate} onChange={event => setStartDate(event.target.value)}/></label>
      </div>
      {matchingSites.length > 0 && <p className="forecast-site-hint">该 SKU 已接入站点：{matchingSites.join("、")}</p>}
      {mode === "business" && <section className="forecast-business-panel">
        <header><div><h3>复杂业务参数</h3><p>{historicalOnly ? "当前模型仅学习销量历史，暂不支持价格、库存或促销情景。" : "留空的字段仍使用历史默认值"}</p></div><span className="forecast-business-granularity"><button className={businessGranularity === "day" ? "active" : ""} onClick={() => setBusinessGranularity("day")}>按天预测</button><button className={businessGranularity === "week" ? "active" : ""} onClick={() => setBusinessGranularity("week")}>按周预测</button></span></header>
        <div>
          <label>计划售价<input disabled={historicalOnly} type="number" min="0" step="0.01" value={price} onChange={event => setPrice(event.target.value)} placeholder="历史最后售价"/></label>
          <label>折扣力度<input disabled={historicalOnly} type="number" min="0" max="1" step="0.01" value={discount} onChange={event => setDiscount(event.target.value)} placeholder="0 ~ 1"/></label>
          <label>当前库存<input disabled={historicalOnly} type="number" min="0" step="1" value={inventory} onChange={event => setInventory(event.target.value)} placeholder="历史最后库存"/></label>
          <label>促销流量倍数（留空表示无促销）<input disabled={historicalOnly} type="number" min="0.1" step="0.1" value={promotionImpact} onChange={event => setPromotionImpact(event.target.value)} placeholder="例如 1.5"/></label>
          <label>新品基准日销量<input type="number" min="0" step="0.1" value={baseline} onChange={event => setBaseline(event.target.value)} placeholder="无历史 SKU 使用"/></label>
          <label>参考 SKU<input value={referenceSku} onChange={event => setReferenceSku(event.target.value)} placeholder="借用已有 SKU 模式"/></label>
        </div>
      </section>}
      <button className="forecast-run" onClick={run} disabled={!model?.ready || status === "creating" || status === "running"} title={!model?.ready ? "模型就绪后才能开始预测" : undefined}><TrendUp/>{status === "running" ? "模型正在预测…" : status === "creating" ? "正在创建任务…" : !model ? "正在检查模型…" : !model.ready ? "模型暂不可用" : mode === "business" ? "开始复杂业务预测" : `开始${effectiveGranularity === "day" ? "天" : "周"}预测`}</button>
      {error && <div className="forecast-form-error"><WarningCircle/>{error}</div>}
    </section>
    {result && <section className="forecast-output">
      <header>
        <div><Check/><span><h2>预测已完成</h2><p>{result.job.skus.length} SKU × {result.job.sites.length} 站点 · {result.job.granularity === "day" ? "天预测" : "周预测"}</p></span></div>
        <div>
          <button onClick={() => download(result.points, "csv")}><DownloadSimple/>导出 CSV</button>
          <button onClick={() => download(result.points, "json")}><DownloadSimple/>导出 JSON</button>
        </div>
      </header>
      <div className="forecast-output-kpis">{[["预测组合", metrics.pair_count], ["预测总销量", units(metrics.total_forecast)], ["安全库存", units(metrics.safety_stock)], ["建议生产量", units(metrics.recommended_production)]].map(([label, value]) => <span key={label}><small>{label}</small><strong>{value}</strong></span>)}</div>
      <ForecastBasis summaries={result.summaries}/>
      {result.summaries?.length > 0 && (
        <div className="forecast-pair-summaries">
          {result.summaries.map(item => (
            <article key={`${item.sku}-${item.site}`}>
              <header><strong>{item.sku} · {item.site}</strong><span>可靠度 {item.reliability}</span></header>
              <div>
                <span>预测总量<strong>{item.total}</strong></span>
                <span>日均销量<strong>{item.daily_average}</strong></span>
                  <span>误差参考区间<strong>{item.lower == null || item.upper == null ? "尚未验证" : `${item.lower} – ${item.upper}`}</strong></span>
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="forecast-output-body">
        <div className="forecast-result-table">
          <table>
            <thead><tr>{["日期", "站点", "SKU", "预测销量", "下界", "上界", "销量预测区间", "可靠度值"].map(x => <th key={x}>{x}</th>)}</tr></thead>
            <tbody>{result.points.map((row, index) => <tr key={`${row.sku}-${row.site}-${row.bucket_start}-${index}`}><td>{row.bucket_start}</td><td>{row.site}</td><td>{row.sku}</td><td>{row.predicted_sales}</td><td>{row.lower ?? "—"}</td><td>{row.upper ?? "—"}</td><td>{row.lower != null && row.upper != null ? `${row.lower} ~ ${row.upper}` : "—"}</td><td>{row.reliability ?? "—"}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </section>}
    </> : activeTab === "history" ? <ForecastHistoryTab/> : <EnterpriseTraining model={model} onPublished={() => Promise.all([reloadModel(), reloadCatalog()])}/>}
  </div></section></main>;
}
