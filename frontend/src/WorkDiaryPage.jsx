import { useEffect, useMemo, useState } from "react";
import { CaretLeft, CaretRight, MagnifyingGlass, Notebook, Pulse, WarningCircle } from "@phosphor-icons/react";
import { api } from "./api.js";
import { ParentPageTab } from "./ParentPageTab.jsx";
import {
  PLANE_SOURCE,
  PLANE_TASKS_PATH,
  TASK_STATUS_LABELS,
  archiveWorkspace,
  fetchWorkspaces,
  isPlaneTask,
  matchesWorkspaceQuery,
  mergeWorkspaceLists,
  workspaceId,
} from "./planeSession.js";
import "./parent-page-tab.css";
import "./analysis-center.css";

const STATUS_FILTERS = [
  { value: "", label: "全部" },
  { value: "succeeded", label: "已完成" },
  { value: "partial_succeeded", label: "部分完成" },
  { value: "running", label: "运行中" },
  { value: "queued", label: "排队中" },
  { value: "waiting_human", label: "待确认" },
  { value: "failed", label: "异常" },
  { value: "draft", label: "待运行" },
];

const PAGE_SIZE = 10;
const productImage = (sku) => sku ? `/assets/hf-products/${encodeURIComponent(sku)}.webp` : "/assets/furniscope-mark.png";
const imageFallback = (event) => {
  event.currentTarget.onerror = null;
  event.currentTarget.src = "/assets/furniscope-mark.png";
};

export function WorkDiaryList({ items, onDelete, from = "workbench" }) {
  return (
    <div className="workbench-list work-diary-list">
      <div className="workbench-list-head">
        <span>工作台</span>
        <span>产品与市场</span>
        <span>分析次数</span>
        <span>最近状态</span>
        <span>最后更新</span>
        <span>操作</span>
      </div>
      {items.map((item) => (
        <article key={workspaceId(item)}>
          <div className="workbench-list-title">
            <img src={productImage(item.product_sku)} onError={imageFallback} alt="" />
            <span>
              <strong>{item.job_name}</strong>
              <small>{workspaceId(item).slice(0, 8)}</small>
            </span>
          </div>
          <span>{item.product_sku} · {item.target_country} / {item.target_platform}</span>
          <b>{item.analysis_count} 次</b>
          <span className={`workbench-task-status ${item.status}`}>{TASK_STATUS_LABELS[item.status] || item.status}</span>
          <time>{new Date(item.updated_at).toLocaleString("zh-CN")}</time>
          <div className="workbench-list-actions">
            <button type="button" onClick={() => {
              const plane = item.local_only || item.source === PLANE_SOURCE || isPlaneTask(item) || (item.runs || []).some(isPlaneTask);
              if (plane) location.hash = `workflow?workspace=${workspaceId(item)}&name=${encodeURIComponent(item.job_name || "")}`;
              else if (item.report_uuid) location.hash = `report-detail?id=${item.report_uuid}&from=${from}`;
              else location.hash = "analysis";
            }}>查看</button>
            <button type="button" disabled={!item.report_uuid} onClick={() => { location.hash = `report-detail?id=${item.report_uuid}&from=${from}`; }}>报告</button>
            {onDelete && (
              <button type="button" className="ghost-danger" onClick={() => onDelete(item)}>删除</button>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

export function WorkDiaryPage({ Sidebar, Topbar }) {
  const fromHome = new URLSearchParams(location.hash.split("?")[1] || "").get("from") === "workspace";
  const parent = fromHome
    ? { label: "首页", to: "workspace", from: "workspace" }
    : { label: "AI 工作台", to: "analysis", from: "workbench" };
  const [workspaces, setWorkspaces] = useState([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [pending, setPending] = useState(null);

  const load = () => {
    setLoading(true);
    Promise.all([
      fetchWorkspaces().catch(() => []),
      api(fromHome ? "/api/v1/analysis-tasks?page_size=100" : PLANE_TASKS_PATH),
    ])
      .then(([workspacePage, pageData]) => {
        const tasks = fromHome
          ? (pageData.items || [])
          : (pageData.items || []).filter(isPlaneTask);
        setWorkspaces(mergeWorkspaceLists(workspacePage, tasks));
        setError("");
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [fromHome]);

  const filtered = useMemo(
    () => workspaces.filter((item) => matchesWorkspaceQuery(item, query, status)),
    [workspaces, query, status],
  );
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const archive = async () => {
    if (!pending) return;
    try {
      setWorking(true);
      await archiveWorkspace(pending);
      setPending(null);
      load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(false);
    }
  };

  const resetQuery = (event) => {
    setQuery(event.target.value);
    setPage(1);
  };
  const setStatusFilter = (value) => {
    setStatus(value);
    setPage(1);
  };
  const runningCount = workspaces.filter((item) => ["queued", "running"].includes(item.status)).length;
  const completedCount = workspaces.filter((item) => ["succeeded", "partial_succeeded"].includes(item.status)).length;
  const analysisCount = workspaces.reduce((sum, item) => sum + (item.analysis_count || 0), 0);

  return (
    <main className="workspace ai-workbench-home-page work-diary-page">
      <Sidebar page={fromHome ? "workspace" : "analysis"} />
      <section className="workspace-main">
        <Topbar />
        <div className="ai-workbench-home">
          <ParentPageTab label={parent.label} current="工作日记" to={parent.to} />
          <header className="work-diary-hero">
            <div className="work-diary-hero-copy">
              <i><Notebook /></i>
              <div>
                <span>WORK DIARY</span>
                <h1>工作日记</h1>
                <p>查找、查看或删除分析工作台记录。删除后对应决策报告会一并归档，首页任务列表同步更新。</p>
              </div>
            </div>
            <div className="work-diary-hero-stats">
              <article><small>工作台</small><strong>{loading ? "—" : workspaces.length}</strong></article>
              <article><small>累计分析</small><strong>{loading ? "—" : analysisCount}</strong></article>
              <article><small>运行中</small><strong>{loading ? "—" : runningCount}</strong></article>
              <article><small>已完成</small><strong>{loading ? "—" : completedCount}</strong></article>
            </div>
          </header>
          <section className="work-diary-panel">
            <div className="work-diary-toolbar">
              <label>
                <MagnifyingGlass />
                <input
                  aria-label="搜索工作日记"
                  value={query}
                  onChange={resetQuery}
                  placeholder="搜索工作台、产品或市场…"
                />
              </label>
              <div className="work-diary-status-filters" role="tablist" aria-label="筛选状态">
                {STATUS_FILTERS.map((item) => (
                  <button
                    key={item.value || "all"}
                    type="button"
                    role="tab"
                    aria-selected={status === item.value}
                    className={status === item.value ? "active" : ""}
                    onClick={() => setStatusFilter(item.value)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            {error && <div className="workflow-error inline"><WarningCircle />{error}</div>}
            {loading ? (
              <div className="workbench-empty"><Pulse /><strong>正在读取工作日记…</strong></div>
            ) : !visible.length ? (
              <div className="workbench-empty">
                <Pulse />
                <strong>没有符合条件的记录</strong>
                <p>调整筛选条件，或从 AI 工作台新建一次分析。</p>
              </div>
            ) : (
              <WorkDiaryList items={visible} onDelete={setPending} from={parent.from} />
            )}
            <footer className="work-diary-pagination">
              <span>第 {currentPage} / {totalPages} 页 · 共 {filtered.length} 条</span>
              <button type="button" onClick={() => setPage((value) => value - 1)} disabled={currentPage <= 1}>
                <CaretLeft />上一页
              </button>
              <button type="button" onClick={() => setPage((value) => value + 1)} disabled={currentPage >= totalPages}>
                下一页<CaretRight />
              </button>
            </footer>
          </section>
        </div>
      </section>
      {pending && (
        <div className="modal-backdrop" onMouseDown={() => !working && setPending(null)}>
          <section className="modal" onMouseDown={(event) => event.stopPropagation()}>
            <h3>确认删除这条工作日记？</h3>
            <p>将删除「{pending.job_name}」中的 {pending.analysis_count} 次分析，并归档对应决策报告。首页 AI 工作流任务不再展示这些记录。</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setPending(null)}>取消</button>
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
