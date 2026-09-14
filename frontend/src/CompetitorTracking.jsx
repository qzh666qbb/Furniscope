import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Camera,
  ChartLine,
  Database,
  MagnifyingGlass,
  Plus,
  Pulse,
  Trash,
  TrendUp,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api } from "./api.js";
import { SentimentStreamPanel } from "./MarketSignals.jsx";
import { ParentPageTab } from "./ParentPageTab.jsx";
import "./market-datasets.css";
import "./competitor-tracking.css";

const countries = { US: "美国", GB: "英国", DE: "德国", FR: "法国", CA: "加拿大", AU: "澳大利亚", JP: "日本" };
const messageOf = (error) => (error instanceof Error ? error.message : String(error));
const dateText = (value) => (value ? new Date(value).toLocaleString("zh-CN", { dateStyle: "medium" }) : "—");
const money = (value, currency) => (value == null ? "—" : `${currency || ""} ${Number(value).toFixed(2)}`.trim());
const categoryLabels = { sofa: "沙发", armchair: "扶手椅", recliner: "功能椅", chair: "椅类", unclassified: "未分类" };
const inferCategory = (item) => {
  const raw = String(item.category_code || item.category || "").toLowerCase();
  if (categoryLabels[raw]) return raw;
  const title = String(item.title || "");
  if (/扶手椅|armchair/i.test(title)) return "armchair";
  if (/功能|升降|recliner|lift/i.test(title)) return "recliner";
  if (/沙发|sofa/i.test(title)) return "sofa";
  return raw || "unclassified";
};
const tokenScore = (left, right) => {
  const tokens = (value) => new Set(String(value || "").toLowerCase().match(/[a-z0-9]+|[\u4e00-\u9fff]/g) || []);
  const a = tokens(left);
  const b = tokens(right);
  if (!a.size || !b.size) return 0;
  let hit = 0;
  a.forEach((item) => { if (b.has(item)) hit += 1; });
  return hit / new Set([...a, ...b]).size;
};

function CatalogPicker({ datasets, datasetId, marketCountry, productId, onClose, onAdded }) {
  const [query, setQuery] = useState("");
  const [dataset, setDataset] = useState(datasetId || "");
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState({ items: [], total: 0, page: 1, page_size: 20 });

  const load = (nextPage = 1) => {
    const params = new URLSearchParams({ page: String(nextPage), page_size: "20" });
    if (query.trim()) params.set("q", query.trim());
    if (dataset) params.set("dataset_id", String(dataset));
    if (marketCountry) params.set("market_country", marketCountry);
    api(`/api/v1/competitor-tracking/catalog?${params}`)
      .then((data) => {
        setResult(data);
        setPage(nextPage);
        setError("");
      })
      .catch((reason) => setError(messageOf(reason)));
  };

  useEffect(() => { load(1); }, [dataset, marketCountry]);

  const add = async (item) => {
    if (item.watched) return;
    setBusyId(item.asin);
    setError("");
    try {
      await api("/api/v1/competitor-tracking/watches", {
        method: "POST",
        body: JSON.stringify({
          asin: item.asin,
          market_country: item.market_country || marketCountry || "US",
          platform: item.platform || "amazon",
          dataset_id: item.dataset_id,
          product_id: productId ? Number(productId) : null,
        }),
      });
      await onAdded();
      load(page);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="dataset-modal-backdrop" onClick={onClose}>
      <section className="dataset-import-modal tracking-catalog-modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <span>MARKET CATALOG</span>
            <h2>从市场数据加入竞品库</h2>
            <p>这里是已导入的外部商品。加入后会出现在竞品库，可勾选对比或随时删除。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X /></button>
        </header>
        <div className="dataset-import-scroll">
          {error && <div className="dataset-error"><WarningCircle />{error}</div>}
          <div className="tracking-catalog-toolbar">
            <label>
              <MagnifyingGlass />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、ASIN、品牌" onKeyDown={(event) => { if (event.key === "Enter") load(1); }} />
            </label>
            <select aria-label="数据集" value={dataset} onChange={(event) => setDataset(event.target.value)}>
              <option value="">全部已导入市场数据</option>
              {datasets.filter((item) => item.status === "ready").map((item) => (
                <option value={item.dataset_id} key={item.dataset_id}>{item.name}</option>
              ))}
            </select>
            <button type="button" onClick={() => load(1)}>搜索</button>
          </div>
          {!result.items.length ? (
            <div className="tracking-guided-empty"><Database /><strong>没有可加入的外部商品</strong><p>先在市场洞察导入数据集，或换一个关键词搜索。</p></div>
          ) : (
            <div className="tracking-watch-table">
              <table>
                <thead><tr><th>商品</th><th>编号</th><th>来源</th><th>售价</th><th></th></tr></thead>
                <tbody>
                  {result.items.map((item) => (
                    <tr key={item.listing_id}>
                      <td><strong>{item.title || item.asin}</strong></td>
                      <td>{item.asin}</td>
                      <td>{item.dataset_name}</td>
                      <td>{money(item.sale_price, item.currency)}</td>
                      <td>
                        {item.watched
                          ? <span className="tracking-in-library">已在库中</span>
                          : <button type="button" disabled={busyId === item.asin} onClick={() => add(item)}>加入竞品库</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {result.total > result.page_size && (
            <nav className="dataset-pager">
              <span>共 {result.total} 条</span>
              <button type="button" disabled={page <= 1} onClick={() => load(page - 1)}>上一页</button>
              <button type="button" disabled={!result.has_next} onClick={() => load(page + 1)}>下一页</button>
            </nav>
          )}
        </div>
      </section>
    </div>
  );
}

function PriceChart({ series }) {
  const raw = series || [];
  const points = raw.length === 1 && Number(raw[0].list_price) > 0 && Number(raw[0].list_price) !== Number(raw[0].sale_price)
    ? [{ ...raw[0], sale_price: raw[0].list_price }, raw[0]]
    : raw.filter((item) => Number.isFinite(Number(item.sale_price)));
  if (points.length < 2) {
    return <div className="tracking-empty-chart">{points.length ? "暂无足够快照绘制曲线，刷新或重新导入数据后即可对比价格。" : "添加监控并生成快照后，将在此展示价格波动。"}</div>;
  }
  const values = points.map((item) => Number(item.sale_price));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coords = points.map((item, index) => {
    const x = (index / (points.length - 1)) * 100;
    const y = 100 - ((Number(item.sale_price) - min) / span) * 100;
    return `${x},${y}`;
  });
  return (
    <svg className="tracking-chart" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="价格波动曲线">
      <polyline fill="none" stroke="#078f8a" strokeWidth="1.8" points={coords.join(" ")} />
      {coords.map((point, index) => {
        const [x, y] = point.split(",");
        return <circle key={index} cx={x} cy={y} r="1.6" fill="#078f8a" />;
      })}
    </svg>
  );
}

function SnapshotCompare({ snapshots }) {
  const [left, setLeft] = useState(1);
  const [right, setRight] = useState(0);
  const a = snapshots[left];
  const b = snapshots[right];
  if (!snapshots.length) return <p className="tracking-hint">尚无 Listing 版本快照。</p>;
  if (snapshots.length === 1) {
    return (
      <article className="tracking-snapshot-card">
        <h3>{snapshots[0].title || "当前版本"}</h3>
        <p>{dateText(snapshots[0].captured_at)} · {snapshots[0].source}</p>
        <p>售价 {money(snapshots[0].sale_price, snapshots[0].currency)} {snapshots[0].promo_label ? `· ${snapshots[0].promo_label}` : ""}</p>
        <ol>{(snapshots[0].bullet_points || []).slice(0, 5).map((item, index) => <li key={index}>{typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ol>
      </article>
    );
  }
  const fields = [
    ["标题", a?.title, b?.title],
    ["售价", money(a?.sale_price, a?.currency), money(b?.sale_price, b?.currency)],
    ["划线价", money(a?.list_price, a?.currency), money(b?.list_price, b?.currency)],
    ["促销", a?.promo_label || "无", b?.promo_label || "无"],
    ["主图数", (a?.image_urls || []).length, (b?.image_urls || []).length],
    ["五点描述", JSON.stringify(a?.bullet_points || []), JSON.stringify(b?.bullet_points || [])],
  ];
  return (
    <div className="tracking-compare">
      <div className="tracking-compare-pick">
        <label>旧版本<select value={left} onChange={(event) => setLeft(Number(event.target.value))}>{snapshots.map((item, index) => <option value={index} key={item.snapshot_id}>{dateText(item.captured_at)}</option>)}</select></label>
        <label>新版本<select value={right} onChange={(event) => setRight(Number(event.target.value))}>{snapshots.map((item, index) => <option value={index} key={item.snapshot_id}>{dateText(item.captured_at)}</option>)}</select></label>
      </div>
      <table>
        <thead><tr><th>字段</th><th>旧版本</th><th>新版本</th></tr></thead>
        <tbody>
          {fields.map(([label, before, after]) => (
            <tr key={label} className={String(before) === String(after) ? "" : "changed"}>
              <th>{label}</th><td>{String(before)}</td><td>{String(after)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TrackingEntry() {
  const [summary, setSummary] = useState({ watch_count: 0, sentiment_count: 0 });
  useEffect(() => {
    Promise.all([
      api("/api/v1/competitor-tracking/overview").catch(() => ({})),
      api("/api/v1/market-signals/overview").catch(() => ({})),
    ]).then(([tracking, signals]) => setSummary({ ...tracking, ...signals }));
  }, []);
  return (
    <section className="market-capability-hub">
      <header>
        <div>
          <span>市场监测</span>
          <strong>竞品分析与评论舆情</strong>
          <p>用本企业产品对照外部竞品，或粘贴评论页获取舆情。采集已包含在评论舆情中。</p>
        </div>
      </header>
      <div className="market-capability-grid">
        <button type="button" onClick={() => { location.hash = "competitor-tracking?tab=prices"; }}>
          <i><ChartLine /></i>
          <span>
            <strong>竞品分析</strong>
            <small>对照本企业产品，跟踪价格、Listing、促销与上新节奏</small>
          </span>
          <em>{summary.watch_count || 0} 个监控</em>
          <ArrowRight />
        </button>
        <button type="button" onClick={() => { location.hash = "competitor-tracking?tab=stream"; }}>
          <i><Pulse /></i>
          <span>
            <strong>评论舆情</strong>
            <small>粘贴评论页网址采集，识别情感并沉淀可复用事件</small>
          </span>
          <em>{summary.sentiment_count || 0} 条事件</em>
          <ArrowRight />
        </button>
      </div>
    </section>
  );
}

export function CompetitorTrackingBoard({ Sidebar, Topbar }) {
  const rawTab = new URLSearchParams(location.hash.split("?")[1] || "").get("tab") || "prices";
  const initialTab = ["sources", "policy", "notifications", "alerts"].includes(rawTab) ? (rawTab === "sources" ? "stream" : "prices") : rawTab;
  const [tab, setTab] = useState(["prices", "snapshots", "rhythm", "stream"].includes(initialTab) ? initialTab : "prices");
  const [overview, setOverview] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [watchId, setWatchId] = useState(null);
  const [prices, setPrices] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [form, setForm] = useState({ asin: "", market_country: "US", dataset_id: "", product_id: "", page_url: "" });
  const [products, setProducts] = useState([]);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [libraryQuery, setLibraryQuery] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => {
    const params = new URLSearchParams();
    if (form.product_id) params.set("product_id", String(form.product_id));
    return api(`/api/v1/competitor-tracking/overview${params.toString() ? `?${params}` : ""}`)
      .then((data) => {
        setOverview(data);
        const selected = data.watches?.find((item) => item.compare_selected)?.watch_id
          || data.selected_watch_id
          || data.watches?.[0]?.watch_id
          || null;
        setWatchId((current) => (current && data.watches?.some((item) => item.watch_id === current) ? current : selected));
      })
      .catch((reason) => setError(messageOf(reason)));
  };

  useEffect(() => {
    api("/api/v1/market-datasets?page_size=100").then((page) => setDatasets(page.items || [])).catch(() => {});
    api("/api/v1/products?page_size=100").then((page) => setProducts(page.items || [])).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [form.product_id]);

  useEffect(() => {
    if (!watchId) {
      setPrices([]);
      setSnapshots([]);
      return;
    }
    Promise.all([
      api(`/api/v1/competitor-tracking/watches/${watchId}/prices`),
      api(`/api/v1/competitor-tracking/watches/${watchId}/snapshots`),
    ]).then(([series, versions]) => {
      setPrices(series);
      setSnapshots(versions);
    }).catch((reason) => setError(messageOf(reason)));
  }, [watchId]);

  const selected = useMemo(
    () => overview?.watches?.find((item) => item.watch_id === watchId),
    [overview, watchId],
  );
  const selectedProduct = useMemo(
    () => products.find((item) => String(item.product_id) === String(form.product_id)),
    [products, form.product_id],
  );
  const libraryRows = useMemo(() => (overview?.watches || []).map((item) => ({
    ...item,
    category: inferCategory({ ...item, category_code: item.category_code }),
    match_score: item.match_score ?? (selectedProduct ? tokenScore(selectedProduct.name, item.title) : null),
  })), [overview, selectedProduct]);
  const listings = libraryRows;
  const visibleRows = listings.filter((row) => {
    const categoryOk = !categoryFilter || row.category === categoryFilter;
    const hay = `${row.title || ""} ${row.asin || ""}`.toLowerCase();
    const queryOk = !libraryQuery.trim() || hay.includes(libraryQuery.trim().toLowerCase());
    return categoryOk && queryOk;
  });
  const categories = [...new Set(libraryRows.map((row) => row.category))];
  const checkedRows = libraryRows.filter((row) => row.compare_selected);
  const ownForCompare = selectedProduct || products.find((item) => String(item.product_id) === String(selected?.product_id));
  const compareTarget = checkedRows[0] || selected;

  const toggleChecked = async (item) => {
    setBusy(true);
    setError("");
    try {
      const next = !item.compare_selected;
      await api(`/api/v1/competitor-tracking/watches/${item.watch_id}`, {
        method: "PATCH",
        body: JSON.stringify({ compare_selected: next }),
      });
      if (next) setWatchId(item.watch_id);
      await load();
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const matchProduct = async () => {
    if (!form.product_id) return setError("请先选择一个本企业产品");
    setBusy(true);
    setError("");
    setCategoryFilter("");
    try {
      const items = await api("/api/v1/competitor-tracking/watches/from-product", {
        method: "POST",
        body: JSON.stringify({
          product_id: Number(form.product_id),
          dataset_id: form.dataset_id ? Number(form.dataset_id) : null,
          limit: 8,
        }),
      });
      if (items[0]) setWatchId(items[0].watch_id);
      await load();
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };
  const competitorTabs = ["prices", "snapshots", "rhythm"];
  const isCompetitorMode = competitorTabs.includes(tab);
  const selectTab = (next) => {
    setTab(next);
    location.hash = `competitor-tracking?tab=${next}`;
  };

  const addWatch = async () => {
    if (!form.asin.trim()) return setError("请填写 ASIN 或商品编号");
    setBusy(true);
    setError("");
    try {
      const created = await api("/api/v1/competitor-tracking/watches", {
        method: "POST",
        body: JSON.stringify({
          asin: form.asin.trim(),
          market_country: form.market_country,
          dataset_id: form.dataset_id ? Number(form.dataset_id) : null,
          product_id: form.product_id ? Number(form.product_id) : null,
        }),
      });
      setForm({ ...form, asin: "" });
      setWatchId(created.watch_id);
      await load();
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const seedDataset = async (datasetId) => {
    setBusy(true);
    setError("");
    try {
      const items = await api("/api/v1/competitor-tracking/watches/from-dataset", {
        method: "POST",
        body: JSON.stringify({
          dataset_id: Number(datasetId),
          product_id: form.product_id ? Number(form.product_id) : null,
          limit: 8,
        }),
      });
      if (items[0]) setWatchId(items[0].watch_id);
      await load();
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const addFromUrl = async () => {
    if (!form.page_url.trim()) return setError("请粘贴竞品商品页网址");
    setBusy(true);
    setError("");
    try {
      const items = await api("/api/v1/competitor-tracking/watches/from-url", {
        method: "POST",
        body: JSON.stringify({
          endpoint_url: form.page_url.trim(),
          product_id: form.product_id ? Number(form.product_id) : null,
          dataset_id: form.dataset_id ? Number(form.dataset_id) : null,
          market_country: form.market_country,
        }),
      });
      setForm({ ...form, page_url: "" });
      if (items[0]) setWatchId(items[0].watch_id);
      await load();
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/api/v1/competitor-tracking/refresh", { method: "POST" });
      await load();
      if (watchId) {
        setPrices(await api(`/api/v1/competitor-tracking/watches/${watchId}/prices`));
        setSnapshots(await api(`/api/v1/competitor-tracking/watches/${watchId}/snapshots`));
      }
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    if (!window.confirm("从竞品库移除该外部商品？历史价格快照会保留。")) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/competitor-tracking/watches/${id}`, { method: "DELETE" });
      if (watchId === id) setWatchId(null);
      await load();
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="workspace tracking-page">
      <Sidebar page="insights" />
      <section className="workspace-main">
        <Topbar />
        <div className="tracking-shell">
          <ParentPageTab label="市场洞察" current="竞品分析与评论舆情" to="insights" />
          <header className="tracking-main-header">
            <div>
              <span>MARKET MONITORING</span>
              <h1>竞品分析与评论舆情</h1>
              <p>竞品对照价格与 Listing 变化；评论舆情读取已导入的有效评论，也可粘贴网址补采进数据集。</p>
            </div>
            <button type="button" onClick={() => { location.hash = "insights"; }}><Database />返回市场数据集</button>
          </header>
          {error && <div className="dataset-error"><WarningCircle />{error}<button type="button" onClick={() => setError("")}>×</button></div>}
          <nav className="tracking-workspace-nav" aria-label="市场监测">
            {[
              ["prices", ChartLine, "竞品分析", "与本企业产品对比", overview?.watch_count],
              ["stream", Pulse, "评论舆情", "市场评论与网页补采", null],
            ].map(([id, Icon, label, hint, count]) => {
              const active = id === "prices" ? isCompetitorMode : tab === id;
              return <button key={id} className={active ? "active" : ""} type="button" onClick={() => selectTab(id)}><Icon /><span><strong>{label}</strong><small>{hint}</small></span>{count ? <b>{count}</b> : null}</button>;
            })}
          </nav>
          {isCompetitorMode && <>
            <section className="tracking-task-intro">
              <div><span>竞品分析</span><h2>用本企业产品对比市场上的相似商品</h2><p>匹配或挑选外部商品写入竞品库；勾选参与对比，不需要的条目可以直接从库中删除。</p></div>
            </section>
            <section className="tracking-setup-card">
              <header><div><b>开始对比</b></div></header>
              <div className="tracking-add tracking-add-compare">
                <select aria-label="本企业产品" value={form.product_id} onChange={(event) => setForm({ ...form, product_id: event.target.value })}>
                  <option value="">选择本企业产品</option>
                  {products.map((item) => <option value={item.product_id} key={item.product_id}>{item.sku} · {item.name}</option>)}
                </select>
                <select aria-label="目标市场" value={form.market_country} onChange={(event) => setForm({ ...form, market_country: event.target.value })}>{Object.entries(countries).map(([code, label]) => <option value={code} key={code}>{label}</option>)}</select>
                <select aria-label="优先使用的市场数据集" value={form.dataset_id} onChange={(event) => setForm({ ...form, dataset_id: event.target.value })}><option value="">全部已导入市场数据</option>{datasets.filter((item) => item.status === "ready").map((item) => <option value={item.dataset_id} key={item.dataset_id}>{item.name}</option>)}</select>
                <button type="button" onClick={matchProduct} disabled={busy}>匹配相似竞品</button>
              </div>
              <div className="tracking-add tracking-add-url">
                <input aria-label="竞品商品页网址" value={form.page_url} onChange={(event) => setForm({ ...form, page_url: event.target.value })} placeholder="粘贴 Amazon 等竞品商品页 https://" />
                <button type="button" onClick={addFromUrl} disabled={busy}>采集并加入对比</button>
              </div>
              <details className="signal-advanced">
                <summary>手工填写外部 ASIN</summary>
                <div className="tracking-add">
                  <input aria-label="ASIN 或商品编号" value={form.asin} onChange={(event) => setForm({ ...form, asin: event.target.value })} placeholder="外部 ASIN" />
                  <button type="button" onClick={addWatch} disabled={busy}><Plus />添加该竞品</button>
                  <select aria-label="从数据集批量加入" defaultValue="" onChange={(event) => { if (event.target.value) seedDataset(event.target.value); event.target.value = ""; }}><option value="">从数据集加入外部 Top Listing</option>{datasets.filter((item) => item.status === "ready").map((item) => <option value={item.dataset_id} key={item.dataset_id}>{item.name}</option>)}</select>
                </div>
              </details>
            </section>
            <section className="tracking-target-section">
              <header>
                <div><strong>竞品库</strong><small>已加入监控的外部商品，可勾选对比或删除</small></div>
                <div className="signal-filter-row">
                  <label>
                    <MagnifyingGlass />
                    <input value={libraryQuery} onChange={(event) => setLibraryQuery(event.target.value)} placeholder="搜索库内商品" />
                  </label>
                  <select aria-label="品类筛选" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}>
                    <option value="">全部品类</option>
                    {categories.map((code) => <option value={code} key={code}>{categoryLabels[code] || code}</option>)}
                  </select>
                  <button type="button" onClick={() => setPickerOpen(true)} disabled={busy}>从市场数据加入</button>
                  <button type="button" onClick={refresh} disabled={busy}>刷新快照</button>
                  <span>{checkedRows.length} 对比中 / {libraryRows.length} 条</span>
                </div>
              </header>
              <div className="tracking-watch-table">
                {visibleRows.length === 0 ? <div className="tracking-guided-empty"><ChartLine /><strong>{libraryRows.length ? "没有符合筛选的商品" : "竞品库还是空的"}</strong><p>{libraryRows.length ? "换一个关键词或品类再看。" : "选择本企业产品后匹配相似商品，或从已导入的市场数据中加入。"}</p></div> : (
                  <table>
                    <thead><tr><th>对比</th><th>商品</th><th>编号</th><th>品类</th><th>售价</th><th>评分</th><th>相似度</th><th></th></tr></thead>
                    <tbody>
                      {visibleRows.map((item) => (
                        <tr key={item.watch_id} className={item.compare_selected ? "checked" : ""}>
                          <td><input type="checkbox" checked={Boolean(item.compare_selected)} disabled={busy} onChange={() => toggleChecked(item)} aria-label={`对比 ${item.title || item.asin}`} /></td>
                          <td><button type="button" className="tracking-title-btn" onClick={() => setWatchId(item.watch_id)}><strong>{item.title || item.asin}</strong></button></td>
                          <td>{item.asin}</td>
                          <td>{categoryLabels[item.category] || item.category}</td>
                          <td>{money(item.sale_price, item.currency)}</td>
                          <td>{item.rating ?? "—"}</td>
                          <td>{item.match_score ? `${Math.round(item.match_score * 100)}%` : "—"}</td>
                          <td><button type="button" className="tracking-row-delete" disabled={busy} onClick={() => remove(item.watch_id)} aria-label={`从竞品库删除 ${item.title || item.asin}`}><Trash /></button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>
            {(ownForCompare || compareTarget) && <section className="tracking-compare-hero">
              <article>
                <small>本企业产品</small>
                <strong>{ownForCompare?.name || "未选择"}</strong>
                <span>{ownForCompare?.sku || "请先选择产品"}</span>
              </article>
              <b>对比</b>
              <article>
                <small>已选市场竞品</small>
                <strong>{checkedRows.length ? checkedRows.map((item) => item.title || item.asin).join("、") : "未勾选"}</strong>
                <span>{checkedRows.length ? `${checkedRows.length} 个商品` : money(compareTarget?.sale_price, compareTarget?.currency)}</span>
              </article>
            </section>}
            <nav className="tracking-subnav">
              {[["prices", ChartLine, "价格趋势"], ["snapshots", Camera, "版本对比"], ["rhythm", TrendUp, "上新与促销"]].map(([id, Icon, label]) => <button key={id} className={tab === id ? "active" : ""} type="button" onClick={() => selectTab(id)}><Icon />{label}</button>)}
            </nav>
          </>}
          {tab === "prices" && (
            <section className="tracking-panel">
              <header><strong>{selected?.asin || "未选择"}</strong><span>{selected ? money(selected.sale_price, selected.currency) : ""} {selected?.promo_label || ""}</span></header>
              <PriceChart series={prices} />
              <table className="tracking-mini-table">
                <thead><tr><th>时间</th><th>售价</th><th>划线价</th><th>促销</th></tr></thead>
                <tbody>
                  {[...prices].reverse().map((item, index) => (
                    <tr key={index}><td>{dateText(item.captured_at)}</td><td>{money(item.sale_price, item.currency)}</td><td>{money(item.list_price, item.currency)}</td><td>{item.promo_label || "—"}</td></tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {tab === "snapshots" && <section className="tracking-panel"><SnapshotCompare snapshots={snapshots} /></section>}
          {tab === "rhythm" && (
            <section className="tracking-panel tracking-rhythm">
              <article>
                <h3>上新节奏</h3>
                {(overview?.rhythm?.launches || []).length === 0 ? <p>暂无上架日期统计。</p> : (
                  <ul>{overview.rhythm.launches.map((item) => <li key={item.month}><b>{item.month}</b><span>{item.listing_count} 个 Listing</span></li>)}</ul>
                )}
              </article>
              <article>
                <h3>促销覆盖</h3>
                {(overview?.rhythm?.promos || []).length === 0 ? <p>暂无促销统计。</p> : (
                  <ul>{overview.rhythm.promos.map((item) => <li key={item.month}><b>{item.month}</b><span>{item.promo_count} / {item.listing_count} 件在促</span></li>)}</ul>
                )}
              </article>
            </section>
          )}
          {tab === "stream" && <SentimentStreamPanel datasets={datasets} />}
        </div>
        {pickerOpen && (
          <CatalogPicker
            datasets={datasets}
            datasetId={form.dataset_id}
            marketCountry={form.market_country}
            productId={form.product_id}
            onClose={() => setPickerOpen(false)}
            onAdded={load}
          />
        )}
      </section>
    </main>
  );
}
