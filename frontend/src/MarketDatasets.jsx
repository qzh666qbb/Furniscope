import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  CloudArrowUp,
  Database,
  BracketsCurly,
  FileCsv,
  Globe,
  MagnifyingGlass,
  ShieldCheck,
  Star,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, idempotencyKey, createUuid } from "./api.js";
import { useMarketInsights } from "./MarketInsightsContext.jsx";
import { MarketPageFrame } from "./MarketWorkspace.jsx";
import { ParentPageTab } from "./ParentPageTab.jsx";
import "./market-datasets.css";

const countries = { US: "美国", GB: "英国", DE: "德国", FR: "法国", CA: "加拿大", AU: "澳大利亚", JP: "日本" };
const statusLabels = { uploaded: "等待导入", validating: "正在清洗", ready: "可用于分析", rejected: "导入异常", archived: "已归档" };
const sentimentLabels = { positive: "正向", neutral: "中性", negative: "负向" };
const reasonLabels = { empty: "空评", duplicate: "重复评论", garbled: "乱码", spam: "无意义水文", orphan: "无对应商品" };
const emptyForm = () => ({
  name: "",
  market_country: "US",
  platform: "amazon",
  category_code: "sofa",
  data_start_date: "",
  data_end_date: new Date().toISOString().slice(0, 10),
  source_name: "企业授权脱敏数据",
  authorization_reference: "企业内部授权导出",
});
const messageOf = (error) => (error instanceof Error ? error.message : String(error));
const dateText = (value) => (value ? new Date(value).toLocaleString("zh-CN", { dateStyle: "medium" }) : "—");
const countryName = (code) => countries[code] || code || "—";
const priceRange = (item) => {
  const sale = Number(item.sale_price);
  const list = item.list_price == null || item.list_price === "" ? null : Number(item.list_price);
  const currency = item.currency || "";
  if (!Number.isFinite(sale)) return "—";
  if (Number.isFinite(list) && list !== sale) {
    const low = Math.min(sale, list);
    const high = Math.max(sale, list);
    return `${currency} ${low.toFixed(2)}–${high.toFixed(2)}`;
  }
  return `${currency} ${sale.toFixed(2)}`;
};
const attributeLabels = {
  color: "颜色", fabric: "面料", material: "材质", firmness: "软硬度", fill: "填充",
  legs: "椅腿", seat_count: "座位数", back_style: "靠背", lumbar_support: "腰托",
  recliner_type: "功能类型", mechanism_name: "机构", wall_clearance_cm: "离墙距离",
  style: "风格", size: "尺寸", category: "品类",
};
const attributeEntries = (attrs) => Object.entries(attrs || {})
  .filter(([key]) => key !== "market_country")
  .map(([key, raw]) => {
    const value = raw && typeof raw === "object" && "value" in raw ? raw.value : raw;
    const text = value == null || value === "" ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
    return [attributeLabels[key] || key, text];
  });
const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

async function waitUntilSettled(datasetId) {
  let current = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    current = await api(`/api/v1/market-datasets/${datasetId}`);
    if (["ready", "rejected"].includes(current.status)) return current;
    await wait(700);
  }
  return current;
}

function ImportDatasetModal({ dataset, onClose, onDone }) {
  const [form, setForm] = useState(() => (dataset ? null : emptyForm()));
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef(null);

  const submit = async () => {
    if (!file) return setError("请选择 CSV 或 JSON 数据文件");
    setBusy(true);
    setError("");
    try {
      let target = dataset;
      if (!target) {
        if (!form.name.trim()) throw new Error("请填写数据集名称");
        target = await api("/api/v1/market-datasets", {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey("dataset") },
          body: JSON.stringify({
            ...form,
            name: form.name.trim(),
            data_start_date: form.data_start_date || null,
            source_type: "enterprise_export",
            field_mapping: [],
          }),
        });
      }
      const body = new FormData();
      body.append("deduplication_strategy", "platform_id_latest");
      body.append("files", file);
      await api(`/api/v1/market-datasets/${target.dataset_id}/imports`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dataset-import") },
        body,
      });
      const settled = await waitUntilSettled(target.dataset_id);
      if (!settled || settled.status === "rejected") throw new Error("数据文件无法完成清洗，请检查后重新上传");
      onDone(settled);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dataset-modal-backdrop" onClick={onClose}>
      <section className="dataset-import-modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <span>{dataset ? "REPLACE DATASET" : "MARKET DATA IMPORT"}</span>
            <h2>{dataset ? "重新上传数据集" : "导入市场数据集"}</h2>
            <p>
              {dataset
                ? `更新「${dataset.name}」将覆盖旧的竞品与评论，并重新执行清洗统计。`
                : "上传企业拥有或已获授权的脱敏竞品与评论数据，系统会自动清洗后供 AI 工作流复用。"}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X /></button>
        </header>
        <div className="dataset-import-scroll">
          {!dataset && (
            <section className="dataset-form-section">
              <h3>基础信息 <small>必填名称，其余可按目标市场调整</small></h3>
              <div className="dataset-form-grid">
                <label className="full">数据集名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：美国扶手椅市场数据 2026-Q3" /></label>
                <label>目标国家<select value={form.market_country} onChange={(event) => setForm({ ...form, market_country: event.target.value })}>{Object.entries(countries).map(([code, label]) => <option value={code} key={code}>{label}（{code}）</option>)}</select></label>
                <label>平台<select value={form.platform} onChange={(event) => setForm({ ...form, platform: event.target.value })}><option value="amazon">Amazon</option><option value="wayfair">Wayfair</option><option value="walmart">Walmart</option></select></label>
                <label>家具品类<select value={form.category_code} onChange={(event) => setForm({ ...form, category_code: event.target.value })}><option value="sofa">沙发</option><option value="armchair">扶手椅</option><option value="power_recliner">电动躺椅</option><option value="lift_chair">升降椅</option><option value="dining_table">餐桌</option></select></label>
                <label>数据起始日期<input type="date" value={form.data_start_date} onChange={(event) => setForm({ ...form, data_start_date: event.target.value })} /></label>
                <label>数据截止日期<input type="date" value={form.data_end_date} onChange={(event) => setForm({ ...form, data_end_date: event.target.value })} /></label>
              </div>
            </section>
          )}
          <section className="dataset-form-section">
            <h3>上传文件 <small>支持 CSV / JSON / Excel</small></h3>
            <button type="button" className={`dataset-dropzone ${file ? "selected" : ""}`} onClick={() => fileRef.current?.click()}>
              <input ref={fileRef} type="file" accept=".csv,.json,.xlsx,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => setFile(event.target.files?.[0] || null)} />
              <CloudArrowUp />
              <strong>{file?.name || "选择 CSV / JSON 脱敏数据文件"}</strong>
              <small>{file ? `${(file.size / 1024).toFixed(1)} KB · 已准备上传` : "CSV 使用 record_type 区分 listing 与 review；JSON 包含 listings、reviews 两组数据"}</small>
              <div><FileCsv />CSV<BracketsCurly />JSON</div>
            </button>
            <a className="dataset-template" href="/api/v1/market-datasets/import-template">下载填写模板</a>
            <div className="dataset-cleaning-steps">
              {["结构校验", "空值与乱码过滤", "评论去重", "水文识别", "有效样本入库"].map((item) => (
                <span key={item}><Check />{item}</span>
              ))}
            </div>
          </section>
          {error && <div className="dataset-error"><WarningCircle />{error}</div>}
        </div>
        <footer>
          <span>{busy ? "正在校验结构并执行评论清洗…" : "导入后可在列表中复用到 AI 分析"}</span>
          <button type="button" onClick={onClose}>取消</button>
          <button type="button" className="primary-save" onClick={submit} disabled={busy}>{busy ? "正在导入并清洗…" : dataset ? "覆盖并更新" : "创建并开始清洗"}</button>
        </footer>
      </section>
    </div>
  );
}

function CleaningBanner({ report, name, onClose }) {
  if (!report) return null;
  const reasons = report.filter_reasons || {};
  return (
    <section className="dataset-cleaning-banner">
      <ShieldCheck />
      <div>
        <strong>清洗完成{name ? ` · ${name}` : ""}</strong>
        <p>
          评论 {report.raw_review_count ?? 0} → 有效 {report.valid_review_count ?? 0}，过滤 {report.filtered_review_count ?? 0}
          {report.raw_listing_count != null ? ` · 竞品 ${report.raw_listing_count} → ${report.kept_listing_count ?? report.raw_listing_count}` : ""}
        </p>
        {Object.keys(reasons).length > 0 && (
          <small>
            {Object.entries(reasons).map(([key, value]) => `${reasonLabels[key] || key} ${value}`).join(" · ")}
          </small>
        )}
      </div>
      {onClose ? <button onClick={onClose}>知道了</button> : null}
    </section>
  );
}

function Stars({ value }) {
  return <span className="preview-stars"><Star weight="fill" />{value == null ? "—" : Number(value).toFixed(1)}</span>;
}

function Pager({ page, total, pageSize, onPage }) {
  const pages = Math.max(1, Math.ceil((total || 0) / pageSize));
  if (pages <= 1) return null;
  const windowEnd = Math.min(pages, Math.max(5, page + 2));
  const windowStart = Math.max(1, windowEnd - 4);
  const numbers = [];
  for (let index = windowStart; index <= windowEnd; index += 1) numbers.push(index);
  return (
    <nav className="dataset-pager">
      <span>共 {total} 条</span>
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</button>
      {numbers.map((index) => (
        <button type="button" key={index} className={index === page ? "active" : ""} onClick={() => onPage(index)}>{index}</button>
      ))}
      <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页</button>
    </nav>
  );
}

export function MarketDatasetCenter({ Sidebar, Topbar }) {
  const {
    datasets,
    datasetsLoading: loading,
    datasetsLoaded,
    datasetsError,
    refreshDatasets,
    upsertDataset,
    removeDataset: removeCachedDataset,
  } = useMarketInsights();
  const [query, setQuery] = useState("");
  const [country, setCountry] = useState("");
  const [period, setPeriod] = useState("");
  const [modal, setModal] = useState(null);
  const [error, setError] = useState("");
  const [cleaning, setCleaning] = useState(null);
  const [listPage, setListPage] = useState(1);
  const pageSize = 10;

  const load = (force = false) => refreshDatasets({ force }).catch((reason) => setError(messageOf(reason)));

  useEffect(() => { load(); }, [refreshDatasets]);
  useEffect(() => {
    const importRequested = new URLSearchParams(location.hash.split("?")[1] || "").get("import") === "1";
    if (importRequested) setModal("new");
  }, []);
  useEffect(() => {
    if (!datasets.some((item) => ["uploaded", "validating"].includes(item.status))) return undefined;
    const timer = window.setInterval(() => load(true), 2200);
    return () => window.clearInterval(timer);
  }, [datasets, refreshDatasets]);

  const visible = useMemo(() => datasets.filter((item) => {
    const searchMatch = `${item.name} ${item.market_country} ${countries[item.market_country] || ""} ${item.category_code}`
      .toLowerCase()
      .includes(query.trim().toLowerCase());
    const countryMatch = !country || item.market_country === country;
    if (!searchMatch || !countryMatch || !period) return searchMatch && countryMatch;
    const created = new Date(item.created_at || item.updated_at || item.data_end_date);
    const days = (Date.now() - created.getTime()) / 86400000;
    return period === "30" ? days <= 30 : period === "90" ? days <= 90 : days > 90;
  }), [datasets, query, country, period]);
  useEffect(() => { setListPage(1); }, [query, country, period]);
  const paged = useMemo(() => {
    const start = (listPage - 1) * pageSize;
    return visible.slice(start, start + pageSize);
  }, [visible, listPage]);
  const summary = useMemo(() => datasets.reduce((result, item) => ({
    ready: result.ready + (item.status === "ready" ? 1 : 0),
    listings: result.listings + Number(item.listing_count || 0),
    reviews: result.reviews + Number(item.review_count || 0),
    valid: result.valid + Number(item.valid_review_count || 0),
  }), { ready: 0, listings: 0, reviews: 0, valid: 0 }), [datasets]);

  const analyzeWith = (item) => {
    const workspace = createUuid();
    location.hash = `workflow?new=1&workspace=${workspace}&dataset=${item.dataset_id}&name=${encodeURIComponent(`${item.name} 市场分析`)}`;
  };

  const remove = async (item) => {
    if (!window.confirm(`确认删除数据集“${item.name}”？数据将进入归档状态。`)) return;
    try {
      await api(`/api/v1/market-datasets/${item.dataset_id}`, { method: "DELETE" });
      removeCachedDataset(item.dataset_id);
    } catch (reason) {
      setError(messageOf(reason));
    }
  };

  return (
    <MarketPageFrame Sidebar={Sidebar} Topbar={Topbar} active="data">
        <div className="market-page-canvas market-dataset-shell">
          <header className="market-dataset-hero">
            <div>
              <span>MARKET DATA ASSETS</span>
              <h1>市场数据资产</h1>
              <p>管理授权数据集、导入质量和数据版本，为决策中心与持续监测提供统一证据范围。</p>
            </div>
            <div className="market-hero-actions">
              <small>第一次使用？从导入一份市场数据开始</small>
              <button type="button" onClick={() => setModal("new")}><CloudArrowUp />导入竞品与评论数据</button>
              <a href="/api/v1/market-datasets/import-template">下载填写模板</a>
            </div>
          </header>
          <div className="dataset-section-heading dataset-assets-heading">
            <div><span>ASSET HEALTH</span><h2>资产概览</h2><p>统计只包含当前企业已落库的竞品和评论记录。</p></div>
            <details className="dataset-access-rules">
              <summary>数据接入规则</summary>
              <div>
                <span><i>1</i><strong>上传</strong><small>CSV、JSON 或 Excel</small></span>
                <ArrowRight />
                <span><i>2</i><strong>清洗</strong><small>去空值、乱码、重复与水文</small></span>
                <ArrowRight />
                <span><i>3</i><strong>使用</strong><small>进入 AI 分析与持续监测</small></span>
              </div>
            </details>
          </div>
          <section className="dataset-summary">
            <article><Database /><span><small>可用于分析</small><strong>{summary.ready}</strong><em>{datasets.length} 组档案</em></span></article>
            <article><Globe /><span><small>已入库竞品</small><strong>{summary.listings.toLocaleString("zh-CN")}</strong><em>实际商品行</em></span></article>
            <article><Star /><span><small>已入库评论</small><strong>{summary.reviews.toLocaleString("zh-CN")}</strong><em>含已过滤</em></span></article>
            <article><ShieldCheck /><span><small>有效评论</small><strong>{summary.valid.toLocaleString("zh-CN")}</strong><em>可作证据</em></span></article>
          </section>
          <p className="dataset-summary-note">顶部数字按库里实际商品/评论条数汇总。只有名称、没有导入正文的空内容档案会计入「档案」数，不会把未入库的商品计入统计。</p>
          <div className="dataset-section-heading">
            <div><h2>市场数据集</h2><p>每组数据代表一个国家、平台和品类范围，可被多个分析工作台重复使用。</p></div>
          </div>
          <div className="dataset-toolbar">
            <label><MagnifyingGlass /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索数据集名称、目标市场" /></label>
            <select value={country} onChange={(event) => setCountry(event.target.value)}>
              <option value="">全部国家</option>
              {Object.entries(countries).map(([code, label]) => <option value={code} key={code}>{label}</option>)}
            </select>
            <select value={period} onChange={(event) => setPeriod(event.target.value)}>
              <option value="">全部创建时间</option>
              <option value="30">近 30 天</option>
              <option value="90">近 90 天</option>
              <option value="older">90 天以前</option>
            </select>
            <span>共 <b>{visible.length}</b> 组 · 每页 {pageSize} 条</span>
            <button type="button" onClick={() => setModal("new")}><CloudArrowUp />导入数据集</button>
          </div>
          {(error || datasetsError) && <div className="dataset-error"><WarningCircle />{error || datasetsError}<button onClick={() => setError("")}>×</button></div>}
          <CleaningBanner report={cleaning?.quality_report} name={cleaning?.name} onClose={() => setCleaning(null)} />
          {(!datasetsLoaded || loading) && !datasets.length ? (
            <div className="dataset-empty">正在读取市场数据资产…</div>
          ) : !visible.length ? (
            <div className="dataset-empty"><Database /><strong>{datasets.length ? "没有符合筛选条件的数据集" : "还没有市场数据集"}</strong><p>{datasets.length ? "清除搜索词或筛选条件后再试。" : "导入竞品与海外评论文件，系统将自动完成清洗和质量统计。"}</p>{!datasets.length && <button type="button" onClick={() => setModal("new")}><CloudArrowUp />导入第一份数据</button>}</div>
          ) : (
            <>
              <div className="dataset-table dataset-asset-table">
                <table>
                  <thead>
                    <tr>
                      <th>数据集</th>
                      <th>市场范围</th>
                      <th>状态</th>
                      <th>竞品</th>
                      <th>评论 / 有效</th>
                      <th>质量</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((item) => {
                      const invalid = Math.max(0, (item.review_count || 0) - (item.valid_review_count || 0));
                      const retention = item.review_count ? Math.round((item.valid_review_count / item.review_count) * 100) : 0;
                      return (
                        <tr key={item.dataset_id}>
                          <td data-label="数据集">
                            <strong>{item.name}</strong>
                            <small>#{item.dataset_id} · v{item.version_no || 1} · 更新 {dateText(item.updated_at || item.data_end_date)}</small>
                          </td>
                          <td data-label="市场范围">{countryName(item.market_country)} · {item.platform} · {item.category_code}</td>
                          <td data-label="状态"><span className={`dataset-status ${item.status}`}><i />{statusLabels[item.status] || item.status}</span></td>
                          <td data-label="竞品"><b>{item.listing_count?.toLocaleString("zh-CN") || 0}</b></td>
                          <td data-label="评论 / 有效">
                            <b>{item.review_count?.toLocaleString("zh-CN") || 0}</b>
                            <small>有效 {item.valid_review_count?.toLocaleString("zh-CN") || 0} · 过滤 {invalid.toLocaleString("zh-CN")}</small>
                          </td>
                          <td data-label="质量">
                            <div className="dataset-quality-cell">
                              <strong>{Number(item.quality_score || 0).toFixed(0)}</strong>
                              <i><b style={{ width: `${retention}%` }} /></i>
                              <small>留存 {retention}%</small>
                            </div>
                          </td>
                          <td className="dataset-actions-cell">
                            <div className="dataset-row-actions">
                              <button className="dataset-use-action" disabled={item.status !== "ready"} onClick={() => analyzeWith(item)}>用于 AI 分析</button>
                              <button onClick={() => { location.hash = `dataset-detail?id=${item.dataset_id}`; }}>详情</button>
                              <button onClick={() => setModal(item)}>更新</button>
                              <button className="danger" onClick={() => remove(item)}>删除</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pager page={listPage} total={visible.length} pageSize={pageSize} onPage={setListPage} />
            </>
          )}
        </div>
      {modal && (
        <ImportDatasetModal
          dataset={modal === "new" ? null : modal}
          onClose={() => setModal(null)}
          onDone={(detail) => {
            setModal(null);
            setCleaning(detail);
            upsertDataset(detail);
            load(true);
          }}
        />
      )}
    </MarketPageFrame>
  );
}

export function MarketDatasetDetail({ Sidebar, Topbar }) {
  const id = new URLSearchParams(location.hash.split("?")[1] || "").get("id");
  const { datasets, upsertDataset } = useMarketInsights();
  const cachedDataset = datasets.find((item) => String(item.dataset_id) === String(id));
  const pageSize = 20;
  const [dataset, setDataset] = useState(cachedDataset || null);
  const [listings, setListings] = useState([]);
  const [reviews, setReviews] = useState([]);
  const [listingTotal, setListingTotal] = useState(0);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [tab, setTab] = useState("listings");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [market, setMarket] = useState("");
  const [sentiment, setSentiment] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(id ? "" : "缺少数据集编号");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 280);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => { setPage(1); setSelected(null); }, [tab, debouncedQuery, market, sentiment]);

  useEffect(() => {
    if (!id) return;
    api(`/api/v1/market-datasets/${id}`).then((detail) => {
      setDataset(detail);
      upsertDataset(detail);
      setListingTotal(detail.listing_count || 0);
      setReviewTotal(detail.review_count || 0);
    }).catch((reason) => setError(messageOf(reason)));
  }, [id, upsertDataset]);

  useEffect(() => {
    if (!id) return;
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (debouncedQuery) params.set("q", debouncedQuery);
    const path = tab === "listings"
      ? `/api/v1/market-datasets/${id}/listings?${params}${market ? `&market_country=${market}` : ""}`
      : `/api/v1/market-datasets/${id}/reviews?${params}${sentiment ? `&sentiment=${sentiment}` : ""}`;
    api(path).then((result) => {
      if (tab === "listings") {
        setListings(result.items || []);
        setListingTotal(result.total || 0);
      } else {
        setReviews(result.items || []);
        setReviewTotal(result.total || 0);
      }
    }).catch((reason) => setError(messageOf(reason)));
  }, [id, tab, page, debouncedQuery, market, sentiment]);

  const rows = tab === "listings" ? listings : reviews;
  const invalid = Math.max(0, (dataset?.review_count || 0) - (dataset?.valid_review_count || 0));

  return (
    <MarketPageFrame Sidebar={Sidebar} Topbar={Topbar} active="data" className="market-detail-module">
        <div className={`dataset-detail-shell ${selected ? "with-record" : ""}`}>
          <div className="dataset-detail-main">
            <ParentPageTab label="数据资产" current="数据集详情" to="market-data" />
            {error && <div className="dataset-error"><WarningCircle />{error}</div>}
            {!dataset && !error ? <div className="dataset-empty">正在加载数据集详情…</div> : dataset && (
              <>
                <header className="dataset-detail-hero">
                  <div>
                    <span className={`dataset-status ${dataset.status}`}><i />{statusLabels[dataset.status]}</span>
                    <h1>{dataset.name}</h1>
                    <p>{countryName(dataset.market_country)} · {dataset.platform} · {dataset.category_code} · 数据集 #{dataset.dataset_id}</p>
                  </div>
                  <div className="dataset-detail-side">
                    <div className="dataset-detail-metrics">
                      <span><small>商品</small><strong>{dataset.listing_count}</strong></span>
                      <span><small>有效评论</small><strong>{dataset.valid_review_count}</strong></span>
                      <span><small>过滤无效</small><strong>{invalid}</strong></span>
                      <span><small>质量评分</small><strong>{Number(dataset.quality_score).toFixed(0)}</strong></span>
                    </div>
                    <button className="dataset-detail-analyze" disabled={dataset.status !== "ready"} onClick={() => {
                      const workspace = createUuid();
                      location.hash = `workflow?new=1&workspace=${workspace}&dataset=${dataset.dataset_id}&name=${encodeURIComponent(`${dataset.name} 市场分析`)}`;
                    }}>用于 AI 分析 <ArrowRight /></button>
                  </div>
                </header>
                <section className="dataset-preview-card">
                  <nav className="dataset-detail-tabs">
                    <button className={tab === "listings" ? "active" : ""} onClick={() => setTab("listings")}>竞品商品 <b>{dataset.listing_count}</b></button>
                    <button className={tab === "reviews" ? "active" : ""} onClick={() => setTab("reviews")}>海外评论 <b>{dataset.review_count}</b></button>
                  </nav>
                  <div className="dataset-preview-toolbar">
                    <label><MagnifyingGlass /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={tab === "listings" ? "搜索商品标题、品牌或商品编号" : "搜索评论原文或关联商品"} /></label>
                    {tab === "listings" && (
                      <select value={market} onChange={(event) => setMarket(event.target.value)}>
                        <option value="">全部市场</option>
                        {Object.entries(countries).map(([code, label]) => <option value={code} key={code}>{label}</option>)}
                      </select>
                    )}
                    {tab === "reviews" && (
                      <select value={sentiment} onChange={(event) => setSentiment(event.target.value)}>
                        <option value="">全部情感</option>
                        <option value="positive">正向评论</option>
                        <option value="neutral">中性评论</option>
                        <option value="negative">负向评论</option>
                      </select>
                    )}
                    <span>当前 {rows.length} 条 · 共 {tab === "listings" ? listingTotal : reviewTotal} 条</span>
                  </div>
                  {tab === "listings" ? (
                    <div className="dataset-table">
                      <table>
                        <thead>
                          <tr><th>竞品商品</th><th>品类</th><th>市场</th><th>售价区间</th><th>星级</th><th>上架 / 采集</th><th>参数</th></tr>
                        </thead>
                        <tbody>
                          {rows.length ? rows.map((item) => (
                            <tr key={item.listing_id} className={selected?.listing_id === item.listing_id ? "active" : ""} onClick={() => setSelected(selected?.listing_id === item.listing_id ? null : item)}>
                              <td><strong>{item.title}</strong><small>{item.brand || "未知品牌"} · {item.platform_listing_id}</small></td>
                              <td>{item.category_code}</td>
                              <td>{countryName(item.market_country || dataset.market_country)}</td>
                              <td><b>{priceRange(item)}</b></td>
                              <td><Stars value={item.rating} /></td>
                              <td>{dateText(item.first_available_date || item.captured_at)}</td>
                              <td>{attributeEntries(item.normalized_attributes).length} 项</td>
                            </tr>
                          )) : (
                            <tr><td colSpan={7}>没有符合条件的商品，试试调整搜索或市场筛选。</td></tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="review-preview-list">
                      {!rows.length && <p className="dataset-record-empty">没有符合条件的评论。</p>}
                          {rows.map((item) => {
                        const tone = item.sentiment || (item.rating >= 4 ? "positive" : item.rating <= 2 ? "negative" : "neutral");
                        return (
                          <article key={item.review_id} className={selected?.review_id === item.review_id ? "active" : ""} onClick={() => setSelected(selected?.review_id === item.review_id ? null : item)}>
                            <header>
                              <Stars value={item.rating} />
                              <span className={tone}>{sentimentLabels[tone] || "中性"}</span>
                              <time>{dateText(item.reviewed_at)}</time>
                            </header>
                            <p>{item.content_original}</p>
                            <footer>
                              <span>{item.listing_title}</span>
                              <span>{item.reviewer_location || "属地未标注"} · {item.language_code?.toUpperCase()}</span>
                              <b>{item.is_valid ? "有效证据" : `已过滤：${item.invalid_reason}`}</b>
                            </footer>
                          </article>
                        );
                      })}
                    </div>
                  )}
                  <Pager page={page} total={tab === "listings" ? listingTotal : reviewTotal} pageSize={pageSize} onPage={setPage} />
                </section>
              </>
            )}
          </div>
          {selected && (
            <aside className="dataset-record-panel">
              <header>
                <div>
                  <span>记录详情</span>
                  <h2>{selected.title || selected.listing_title}</h2>
                </div>
                <button type="button" onClick={() => setSelected(null)} aria-label="关闭"><X /></button>
              </header>
              {selected.content_original ? (
                <div className="dataset-record-body">
                  <Stars value={selected.rating} />
                  <blockquote>{selected.content_original}</blockquote>
                  <dl>
                    <div><dt>评论编号</dt><dd>{selected.platform_review_id || "—"}</dd></div>
                    <div><dt>发布时间</dt><dd>{dateText(selected.reviewed_at)}</dd></div>
                    <div><dt>用户属地</dt><dd>{selected.reviewer_location || "未标注"}</dd></div>
                    <div><dt>情感倾向</dt><dd>{sentimentLabels[selected.sentiment] || "未标注"}</dd></div>
                    <div><dt>语言</dt><dd>{selected.language_code?.toUpperCase() || "—"}</dd></div>
                    {!selected.is_valid && <div><dt>过滤原因</dt><dd>{selected.invalid_reason || "—"}</dd></div>}
                  </dl>
                </div>
              ) : (
                <div className="dataset-record-body">
                  <dl>
                    <div><dt>商品编号</dt><dd>{selected.platform_listing_id || "—"}</dd></div>
                    <div><dt>市场</dt><dd>{countryName(selected.market_country || dataset?.market_country)}</dd></div>
                    <div><dt>售价区间</dt><dd>{priceRange(selected)}</dd></div>
                    <div><dt>星级</dt><dd>{selected.rating ?? "—"}</dd></div>
                    <div><dt>上架 / 采集</dt><dd>{dateText(selected.first_available_date || selected.captured_at)}</dd></div>
                  </dl>
                  <h3>核心参数</h3>
                  {attributeEntries(selected.normalized_attributes).length ? (
                    <dl>
                      {attributeEntries(selected.normalized_attributes).map(([label, value]) => (
                        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
                      ))}
                    </dl>
                  ) : <p className="dataset-record-empty">这条商品没有结构化参数。</p>}
                </div>
              )}
            </aside>
          )}
        </div>
    </MarketPageFrame>
  );
}
