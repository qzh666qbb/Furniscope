import { useEffect, useMemo, useState } from "react";
import {
  CaretDown,
  CheckSquare,
  FilePdf,
  FileText,
  MagnifyingGlass,
  Square,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "./api.js";

const MARKETS = {
  US: "美国", GB: "英国", DE: "德国", FR: "法国", IT: "意大利",
  ES: "西班牙", NL: "荷兰", JP: "日本", AU: "澳大利亚", CA: "加拿大",
};
const DECISIONS = {
  prioritize_validate: "优先验证",
  collect_more_data: "补充数据",
  capability_gap: "能力缺口",
  limited_opportunity: "机会有限",
};
const TIME_OPTIONS = [
  ["", "全部时间"],
  ["7", "近 7 天"],
  ["30", "近 30 天"],
  ["90", "近 90 天"],
];

function productImage(sku) {
  return sku ? `/assets/hf-products/${encodeURIComponent(sku)}.webp` : "/assets/furniscope-mark.png";
}
function imageFallback(event) {
  event.currentTarget.onerror = null;
  event.currentTarget.src = "/assets/furniscope-mark.png";
}
function marketLabel(code) {
  return MARKETS[code] || code;
}
function sinceIso(days) {
  if (!days) return "";
  const date = new Date();
  date.setDate(date.getDate() - Number(days));
  return date.toISOString();
}

export function ReportLibrary({ Sidebar, Topbar }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [productId, setProductId] = useState("");
  const [country, setCountry] = useState("");
  const [days, setDays] = useState("");
  const [page, setPage] = useState(1);
  const [filterOptions, setFilterOptions] = useState({ products: [], countries: [] });
  const [selected, setSelected] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [working, setWorking] = useState(false);

  const load = () => {
    setLoading(true);
    const params = new URLSearchParams({ page_size: "10", page: String(page) });
    if (query.trim()) params.set("q", query.trim());
    if (productId) params.set("product_id", productId);
    if (country) params.set("target_country", country);
    if (days) params.set("created_since", sinceIso(days));
    api(`/api/v1/reports?${params}`)
      .then((data) => {
        setItems(data.items || []);
        setTotal(data.total || 0);
        setError("");
        setSelected((current) => current.filter((id) => (data.items || []).some((row) => row.report_uuid === id)));
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, [productId, country, days, page]);
  useEffect(() => {
    api("/api/v1/reports/filter-options")
      .then((data) => setFilterOptions(data))
      .catch(() => setFilterOptions({ products: [], countries: [] }));
  }, []);

  const products = useMemo(
    () => (filterOptions.products || []).map((item) => [
      item.product_id, `${item.product_sku} ${item.product_name}`,
    ]),
    [filterOptions.products],
  );
  const countries = filterOptions.countries || [];
  const totalPages = Math.max(1, Math.ceil(total / 10));
  const allSelected = items.length > 0 && selected.length === items.length;
  const runSearch = () => {
    if (page === 1) load();
    else setPage(1);
  };

  const toggle = (id) =>
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  const toggleAll = () => setSelected(allSelected ? [] : items.map((row) => row.report_uuid));

  const archive = async () => {
    if (!selected.length) return;
    setWorking(true);
    try {
      await api("/api/v1/reports:archive", {
        method: "POST",
        body: JSON.stringify({ report_uuids: selected }),
      });
      setConfirmDelete(false);
      setSelected([]);
      load();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setWorking(false);
    }
  };

  const exportSelected = () => {
    const ids = selected.length ? selected : items.slice(0, 1).map((row) => row.report_uuid);
    if (!ids.length) return;
    location.hash = ids.length === 1
      ? `report-detail?id=${ids[0]}&print=1&from=report`
      : `report-detail?ids=${ids.join(",")}&print=1&from=report`;
  };

  return (
    <main className="workspace asset-library-page report-library-page">
      <Sidebar page="report" />
      <section className="workspace-main">
        <Topbar />
        <div className="asset-library report-library">
          <header>
            <div>
              <span className="eyebrow">DECISION REPORT ARCHIVE</span>
              <h1>决策报告</h1>
              <p>AI 工作流完成后自动归档至此。支持检索、批量导出 PDF 与归档删除，报告结论均可溯源原始市场证据。</p>
            </div>
            <button className="catalog-header-actions catalog-primary" onClick={() => (location.hash = "analysis")}>
              <FileText /> 去 AI 工作台
            </button>
          </header>

          {error && (
            <div className="forecast-form-error">
              <WarningCircle />
              {error}
            </div>
          )}

          <div className="asset-toolbar report-toolbar">
            <label className="catalog-search">
              <MagnifyingGlass />
              <input
                placeholder="搜索产品名称、任务名称或报告标题…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && runSearch()}
              />
            </label>
            <label className="report-filter-select">
              <select aria-label="按产品筛选" value={productId} onChange={(event) => { setProductId(event.target.value); setPage(1); }}>
                <option value="">全部产品</option>
                {products.map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
              <CaretDown />
            </label>
            <label className="report-filter-select">
              <select aria-label="按市场筛选" value={country} onChange={(event) => { setCountry(event.target.value); setPage(1); }}>
                <option value="">全部市场</option>
                {countries.map((code) => (
                  <option key={code} value={code}>{marketLabel(code)} · {code}</option>
                ))}
              </select>
              <CaretDown />
            </label>
            <label className="report-filter-select">
              <select aria-label="按时间筛选" value={days} onChange={(event) => { setDays(event.target.value); setPage(1); }}>
                {TIME_OPTIONS.map(([value, label]) => (
                  <option key={value || "all"} value={value}>{label}</option>
                ))}
              </select>
              <CaretDown />
            </label>
            <button type="button" onClick={runSearch}>检索 <CaretDown /></button>
            <span>共 <b>{total}</b> 份报告</span>
          </div>

          {selected.length > 0 && (
            <div className="report-batch-bar">
              <span>已选 <b>{selected.length}</b> 份</span>
              <button onClick={toggleAll}>{allSelected ? "取消全选" : "全选本页"}</button>
              <button onClick={exportSelected}><FilePdf /> 批量导出 PDF</button>
              <button className="danger" onClick={() => setConfirmDelete(true)}><Trash /> 批量删除归档</button>
              <button onClick={() => setSelected([])}>取消选择</button>
            </div>
          )}

          {loading ? (
            <div className="live-insight-state">正在加载已归档报告…</div>
          ) : !error && items.length === 0 ? (
            <div className="live-insight-state">
              暂无决策报告。完成一次 AI 工作流分析后，报告会自动沉淀到这里。
            </div>
          ) : (
            <div className="asset-list">
              {items.map((row) => {
                const checked = selected.includes(row.report_uuid);
                return (
                  <article key={row.report_uuid} className={checked ? "selected" : ""}>
                    <button className="report-check" onClick={() => toggle(row.report_uuid)} aria-label="选择报告">
                      {checked ? <CheckSquare weight="fill" /> : <Square />}
                    </button>
                    <img
                      src={productImage(row.product_sku)}
                      alt={`${row.product_name} 产品图`}
                      onError={imageFallback}
                    />
                    <div className="asset-copy">
                      <header>
                        <span>{row.product_sku}</span>
                        <em>已归档</em>
                      </header>
                      <h2>{row.title}</h2>
                      <p>{DECISIONS[row.decision_recommendation] || row.decision_recommendation}{row.job_name ? ` · 任务 ${row.job_name}` : ""}</p>
                      <footer>
                        <span>产品 <b>{row.product_name}</b></span>
                        <span>市场 <b>{marketLabel(row.target_country)} · {row.target_platform}</b></span>
                        <span>生成时间 <b>{new Date(row.created_at).toLocaleString("zh-CN")}</b></span>
                      </footer>
                    </div>
                    <div className="asset-scores">
                      <span>综合机会分<strong>{Math.round(row.overall_opportunity_score)}</strong></span>
                      <span>置信度<strong>{Math.round(row.overall_confidence * 100)}%</strong></span>
                    </div>
                    <div className="asset-actions">
                      <button onClick={() => (location.hash = `report-detail?id=${row.report_uuid}&print=1&from=report`)}>
                        导出 PDF
                      </button>
                      <button className="ghost-danger" onClick={() => { setSelected([row.report_uuid]); setConfirmDelete(true); }}>
                        删除
                      </button>
                      <button
                        className="primary-action"
                        onClick={() => (location.hash = `report-detail?id=${row.report_uuid}&from=report`)}
                      >
                        查看详情
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          {total > 0 && <div className="asset-pagination">
            <span>第 {page} / {totalPages} 页</span>
            <div><button disabled={page === 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div>
          </div>}
        </div>
      </section>

      {confirmDelete && (
        <div className="modal-backdrop" onMouseDown={() => !working && setConfirmDelete(false)}>
          <section className="modal" onMouseDown={(event) => event.stopPropagation()}>
            <h3>确认删除归档？</h3>
            <p>将把 {selected.length} 份报告标记为已归档删除。对应的首页 AI 工作流任务和工作日记也会同步移除，原始分析数据仍保留在系统内。</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setConfirmDelete(false)}>取消</button>
              <button type="button" className="primary-save" onClick={archive} disabled={working}>
                {working ? "正在删除…" : "确认删除"}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
