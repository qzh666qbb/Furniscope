import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowRight,
  CaretDown,
  CaretLeft,
  CaretRight,
  CaretUp,
  ChatCircleText,
  Check,
  Database,
  FileText,
  Gauge,
  MagnifyingGlass,
  PaperPlaneRight,
  Play,
  Plus,
  Pulse,
  SquaresFour,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, createUuid, idempotencyKey, isUuid, subscribeTaskEvents } from "./api.js";
import { PLANE_SOURCE, PLANE_TASKS_PATH, TASK_STATUS_LABELS, chatTurn, fetchWorkspaceMessages, fetchWorkspaces, isPlaneTask, loadPlaneChat, mergeWorkspaceLists, persistWorkspace, persistWorkspaceMessages, planeGreeting, removePlaneWorkspace, reusePlaneWorkspace, savePlaneChat, savePlaneWorkspace, withoutFailureNotices, workspaceId } from "./planeSession.js";
import { WorkDiaryList } from "./WorkDiaryPage.jsx";
import { ParentPageTab } from "./ParentPageTab.jsx";
import "./analysis-center.css";

const terminalStatuses = ["succeeded", "partial_succeeded", "failed", "cancelled", "waiting_human"];
const successfulStatuses = ["succeeded", "partial_succeeded"];
const statusLabels = TASK_STATUS_LABELS;
const marketNames = { US: "美国", GB: "英国", DE: "德国", FR: "法国", JP: "日本", CA: "加拿大", AU: "澳大利亚" };
const stageOrder = {
  understanding_product: 1,
  researching_market: 2,
  evaluating_opportunity: 3,
  generating_recommendation: 4,
  completed: 5,
};
const attributeNames = {
  dimensions: "产品尺寸", material: "材质工艺", factory_price: "出厂报价",
  moq: "MOQ", customization: "定制能力", certifications: "产品认证",
  scenarios: "适配场景", description: "产品说明",
};
const nodeDefinitions = [
  { id: "start", step: "开始", title: "数据入口", subtitle: "载入产品档案与市场数据", Icon: Database },
  { id: "product", step: "01", title: "产品解析", subtitle: "多模态画像与参数冲突校验", Icon: SquaresFour },
  { id: "market", step: "02", title: "市场研究", subtitle: "竞品、价格带与评论痛点聚类", Icon: MagnifyingGlass },
  { id: "score", step: "03", title: "机会评估", subtitle: "五维市场机会评分", Icon: Gauge },
  { id: "plan", step: "04", title: "方案生成", subtitle: "产品改款、定位与定价推演", Icon: Pulse },
  { id: "report", step: "05", title: "报告输出", subtitle: "生成可追溯的完整决策报告", Icon: FileText },
];
const executionTargets = nodeDefinitions.filter((node) => node.id !== "start");

function inferExecutionTarget(text) {
  if (/完整|全链路|报告|全部/.test(text)) return "report";
  if (/方案|改款|建议|定位|定价/.test(text)) return "plan";
  if (/评分|得分|机会|评估/.test(text)) return "score";
  if (/竞品|评论|痛点|市场|价格带|趋势/.test(text)) return "market";
  if (/产品|画像|参数|冲突/.test(text)) return "product";
  return "report";
}

function isForecastQuestion(text) {
  if (/机会评分|市场机会|出海机会/.test(text)) return false;
  return /销量预测|未来销量|销售数据|预测销量|可以进行预测|销量对话|forecast/i.test(text);
}

function forecastChatReply(product) {
  const sku = product?.sku;
  return {
    text: sku
      ? `当前对话绑定的是市场洞察任务（评论、竞品、机会评分），没有接入订单训练数据，不能给出「${product.name}」（${sku}）的未来销量。机会分不能当作销量预测。请打开销量预测并选择该 SKU。`
      : "当前工作台是市场洞察分析，没有接入订单训练数据和销量预测模型。要看未来销量，请打开「销量预测」。",
    action: { href: sku ? `forecast?sku=${encodeURIComponent(sku)}` : "forecast", label: "去销量预测" },
  };
}

function evidenceChatReply(text, { result, insights }) {
  const summary = result?.report_summary;
  if (/痛点|评论|舆情/.test(text) && insights.clusters.length) {
    return `根据本任务已落库的评论聚类：${insights.clusters.slice(0, 3).map((item) => `${item.cluster_name}：${item.summary || `重要度 ${item.importance_score}`}`).join("；")}。原始摘录可在节点详情核验。`;
  }
  if (/评分|得分|依据|风险/.test(text) && insights.opportunities[0]) {
    const item = insights.opportunities[0];
    return `市场机会评分主要依据：需求热度 ${item.demand_heat_score}、增长趋势 ${item.demand_growth_score}、未满足度 ${item.unmet_need_score}、竞争空间 ${item.competition_space_score}、利润空间 ${item.profit_space_score}。综合机会分见报告预览。`;
  }
  if (/改款|建议|成本/.test(text) && insights.recommendations.length) {
    return `优先级最高的改款建议：${insights.recommendations.slice(0, 3).map((item) => `${item.recommended_action}${item.cost_impact_min != null ? `（成本 ${item.cost_currency || "USD"} ${item.cost_impact_min}–${item.cost_impact_max}）` : ""}`).join("；")}。`;
  }
  if (summary && /总结|报告|机会|结论/.test(text)) {
    return `${summary.executive_summary}（综合机会分 ${summary.overall_opportunity_score}，置信度 ${Math.round((summary.overall_confidence || 0) * 100)}%）。`;
  }
  return "";
}

const messageOf = (error) => error instanceof Error ? error.message : String(error);
const productImage = (sku) => sku ? `/assets/hf-products/${encodeURIComponent(sku)}.webp` : "/assets/furniscope-mark.png";
const imageFallback = (event) => {
  event.currentTarget.onerror = null;
  event.currentTarget.src = "/assets/furniscope-mark.png";
};

function failureReason(task) {
  const failedRun = (task?.stage_runs || []).find((item) => item.status === "failed" && item.error_message);
  return task?.failure_message || failedRun?.error_message || "工作流执行失败，请查看右侧节点详情与执行日志。";
}

function failedNodeId(task) {
  return nodeDefinitions[stageOrder[task?.stage] || 1]?.id || "product";
}

function taskStatusLabel(task) {
  if (task?.status === "partial_succeeded" && task?.analysis_config?.target_node !== "report") {
    return "已运行至目标";
  }
  return statusLabels[task?.status] || task?.status || "新对话";
}

function nodeState(nodeIndex, task, product) {
  if (nodeIndex === 0) return product ? "completed" : "ready";
  if (!task) return "pending";
  if (task.status === "succeeded") return "completed";
  const active = stageOrder[task.stage] || 1;
  if (task.status === "partial_succeeded") return nodeIndex <= active ? "completed" : "pending";
  if (nodeIndex < active) return "completed";
  if (nodeIndex > active) return ["failed", "cancelled"].includes(task.status) ? "blocked" : "pending";
  if (task.status === "failed" || task.status === "cancelled") return "error";
  if (task.status === "waiting_human") return "warning";
  if (task.status === "draft") return "ready";
  return "running";
}

function matchCatalogProduct(list, ...texts) {
  const query = texts.filter(Boolean).join(" ").toLowerCase()
    .replace(/分析|出海|机会|工作台|报告|任务|产品/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!query || !list?.length) return null;
  const exact = list.find((item) => {
    const name = String(item.name || "").toLowerCase();
    const sku = String(item.sku || "").toLowerCase();
    return (name && query.includes(name)) || (sku && query.includes(sku));
  });
  if (exact) return exact;
  const ranked = list.map((item) => {
    const name = String(item.name || "").toLowerCase();
    const sku = String(item.sku || "").toLowerCase();
    const nameCore = name.replace(sku, "").trim();
    let score = 0;
    if (nameCore && (query.includes(nameCore) || (nameCore.length >= 2 && nameCore.includes(query)))) score += nameCore.length;
    if (sku && query.includes(sku)) score += 50;
    return { item, score };
  }).filter((row) => row.score >= 2).sort((a, b) => b.score - a.score);
  if (!ranked.length) return null;
  if (ranked[1] && ranked[0].score === ranked[1].score) return null;
  return ranked[0].item;
}

function readyDatasetFor(product, availableDatasets, country = "") {
  return availableDatasets.find((item) => item.status === "ready"
    && (!country || item.market_country === country)
    && (!item.category_code || !product?.category_code || item.category_code === product.category_code))
    || availableDatasets.find((item) => item.status === "ready" && (!country || item.market_country === country))
    || null;
}

const PANE_LIMITS = {
  history: { min: 160, max: 560 },
  chat: { min: 330, max: 820 },
  preview: { min: 240, max: 760 },
};
const CANVAS_MIN = 220;
const PANE_GUTTERS = 24;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function readStoredPaneSizes() {
  try {
    const stored = JSON.parse(localStorage.getItem("furniscope-workflow-pane-sizes") || "{}");
    return {
      history: clamp(Number(stored.history) || 220, PANE_LIMITS.history.min, PANE_LIMITS.history.max),
      chat: clamp(Number(stored.chat) || 420, PANE_LIMITS.chat.min, PANE_LIMITS.chat.max),
      preview: clamp(Number(stored.preview) || 320, PANE_LIMITS.preview.min, PANE_LIMITS.preview.max),
    };
  } catch {
    return { history: 220, chat: 420, preview: 320 };
  }
}

function fitPaneSizes(next, containerWidth, { historyCollapsed = false, aiCollapsed = false } = {}) {
  const available = Math.max(720, (containerWidth || 1280) - PANE_GUTTERS);
  const historyUsed = historyCollapsed ? 44 : clamp(next.history, PANE_LIMITS.history.min, PANE_LIMITS.history.max);
  const chatUsed = aiCollapsed ? 44 : clamp(next.chat, PANE_LIMITS.chat.min, PANE_LIMITS.chat.max);
  let preview = clamp(next.preview, PANE_LIMITS.preview.min, PANE_LIMITS.preview.max);
  let overflow = historyUsed + chatUsed + preview + CANVAS_MIN - available;
  if (overflow > 0) {
    const shrinkPreview = Math.min(overflow, preview - PANE_LIMITS.preview.min);
    preview -= shrinkPreview;
    overflow -= shrinkPreview;
  }
  let chat = chatUsed;
  if (!aiCollapsed && overflow > 0) {
    const shrinkChat = Math.min(overflow, chat - PANE_LIMITS.chat.min);
    chat -= shrinkChat;
    overflow -= shrinkChat;
  }
  let history = historyUsed;
  if (!historyCollapsed && overflow > 0) {
    history -= Math.min(overflow, history - PANE_LIMITS.history.min);
  }
  return {
    history: historyCollapsed ? next.history : history,
    chat: aiCollapsed ? next.chat : chat,
    preview,
  };
}

function MiniBars({ values = [34, 52, 81, 66, 43] }) {
  return <div className="workflow-mini-bars" aria-label="价格带分布">
    {values.map((value, index) => <i key={index} style={{ height: `${value}%` }} />)}
  </div>;
}

function DetailContent({ node, product, dataset, task, result, insights, onProduct, onDataset, products, datasets, onRetry }) {
  const attributes = product?.attributes || [];
  const summary = result?.report_summary;
  if (node.id === "start") return <>
    <section className="node-detail-section source-summary">
      <h3>输入数据</h3>
      <label>产品档案<select value={product?.product_id || ""} onChange={(event) => onProduct(event.target.value)}>
        <option value="">选择已建档产品</option>
        {products.map((item) => <option value={item.product_id} key={item.product_id}>{item.sku} · {item.name}</option>)}
      </select></label>
      <label>市场数据<select value={dataset?.dataset_id || ""} onChange={(event) => onDataset(event.target.value)}>
        <option value="">自动匹配可用数据集</option>
        {datasets.filter((item) => item.status === "ready").map((item) => <option value={item.dataset_id} key={item.dataset_id}>{item.name} · {item.market_country}</option>)}
      </select></label>
    </section>
    {product && <section className="bound-product"><img src={productImage(product.sku)} onError={imageFallback} alt="" /><div><strong>{product.name}</strong><span>SKU {product.sku}</span><small>{product.category_code || "家具产品"}</small></div></section>}
    <section className="node-detail-section"><h3>载入规则</h3><ul className="trace-list"><li><Check />产品图片、工艺、成本与 MOQ 来自产品中心</li><li><Check />市场数据来自已接入的授权数据集</li><li><Check />无需在分析流程内重复上传资料</li></ul></section>
  </>;
  if (node.id === "product") return <>
    <section className="node-detail-section"><h3>标准化产品画像</h3>{attributes.length ? <div className="attribute-list">{attributes.filter((item) => item.attribute_code !== "color").slice(0, 8).map((item) => <div key={item.attribute_code}><span>{attributeNames[item.attribute_code] || item.attribute_name || item.attribute_code}</span><strong>{String(item.attribute_value)}{item.attribute_code === "factory_price" ? " USD" : ""}</strong><small>{item.source_type === "manual" ? "人工填写" : item.source_type === "document" ? "文档提取" : "AI 识别"} · {Math.round((item.confidence || 0) * 100)}%</small></div>)}</div> : <p className="detail-placeholder">选择产品后显示真实画像参数。</p>}</section>
    <section className="node-detail-section"><h3>多源校验</h3><div className="quality-line"><span>参数优先级</span><strong>人工填写 ＞ 文档提取 ＞ AI 视觉</strong></div><div className="quality-line"><span>冲突状态</span><strong className={product?.has_conflicts ? "warn" : "ok"}>{product?.has_conflicts ? "存在待确认冲突" : "未发现参数冲突"}</strong></div>{product?.has_conflicts && <button className="correct-profile" onClick={() => { location.hash = `products?product=${product.product_id}`; }}>打开冲突字段修正</button>}</section>
  </>;
  if (node.id === "market") return <>
    <section className="node-detail-section"><h3>市场数据处理</h3><div className="detail-progress"><span style={{ width: `${task?.stage === "researching_market" ? task.progress_percent || 45 : successfulStatuses.includes(task?.status) ? 100 : 0}%` }} /></div><p className="detail-placeholder">{task ? `正在使用「${dataset?.name || "已绑定数据集"}」完成竞品相似度匹配、价格统计与评论语义聚类。` : "运行后展示真实竞品、价格带与评论证据。"}</p>{result?.competitor_summary && <div className="market-facts"><span><b>{result.competitor_summary.direct}</b>直接竞品</span><span><b>{result.competitor_summary.benchmark}</b>标杆竞品</span><span><b>{result.data_scope?.valid_review_count}</b>有效评论</span></div>}<MiniBars values={(insights.competitors.length ? insights.competitors.slice(0, 5).map((item) => Math.min(95, Math.max(18, Number(item.sale_price || 0) / 8))) : undefined)} /></section>
    {insights.competitors.length > 0 && <section className="node-detail-section"><h3>竞品匹配</h3><div className="competitor-list">{insights.competitors.slice(0, 5).map((item) => <article key={item.competitor_id}><div><strong>{item.title}</strong><span>{item.brand || "未知品牌"} · {item.competitor_type}</span></div><b>{item.currency} {item.sale_price}</b><small>相似度 {Math.round((item.overall_score || 0) * 100)}%</small></article>)}</div></section>}
    <section className="node-detail-section"><h3>评论痛点与证据</h3>{insights.clusters.length > 0 && <div className="pain-bubbles">{insights.clusters.slice(0, 6).map((item, index) => <button style={{ "--bubble": `${Math.max(46, Math.min(82, 44 + Number(item.importance_score || 0) * .35))}px` }} key={item.cluster_id} title={item.summary}>{item.cluster_name}<small>{item.aspect_count} 次</small></button>)}</div>}<ul className="evidence-list"><li><Database />数据集：{dataset?.name || "待自动匹配"}</li>{insights.reviewAspects.slice(0, 3).map((item) => <li key={item.aspect_id}><FileText />“{item.evidence_quote}” · 置信度 {Math.round((item.extraction_confidence || 0) * 100)}%</li>)}<li><MagnifyingGlass />证据关系 {insights.evidence.length} 条，可追溯至原始样本</li></ul></section>
  </>;
  if (node.id === "score") return <>
    <section className="node-detail-section"><h3>五维机会评分</h3><div className="score-wheel"><div><strong>{summary?.overall_opportunity_score ?? "—"}</strong><span>综合机会分</span></div></div>{(() => { const opportunity = insights.opportunities[0]; const dimensions = [["需求热度", opportunity?.demand_heat_score], ["增长趋势", opportunity?.demand_growth_score], ["未满足度", opportunity?.unmet_need_score], ["竞争空间", opportunity?.competition_space_score], ["利润空间", opportunity?.profit_space_score]]; return <div className="score-dimensions">{dimensions.map(([label, value]) => <span key={label}><i style={{ width: `${value ?? 8}%` }} />{label}<b>{value ?? "—"}</b></span>)}</div>; })()}</section>
    <section className="node-detail-section"><h3>计算说明</h3><p className="detail-placeholder">得分由市场证据、竞争强度、利润空间与工厂能力加权计算；任务完成后可在市场洞察中核验计算依据。</p></section>
  </>;
  if (node.id === "plan") return <>
    <section className="node-detail-section"><h3>决策方案</h3>{summary ? <><strong className="recommendation">{summary.decision_recommendation}</strong><p>{summary.executive_summary}</p>{(insights.recommendations.length ? insights.recommendations : result.recommendations || []).slice(0, 6).map((item) => <div className="recommendation-item" key={item.recommendation_id}><span>{item.priority}</span><p>{item.recommended_action}</p><small>{item.cost_impact_min != null ? `成本 ${item.cost_currency || "USD"} ${item.cost_impact_min}–${item.cost_impact_max} · ` : ""}置信度 {Math.round((item.confidence || 0) * 100)}%</small></div>)}</> : <p className="detail-placeholder">运行至本节点后，将基于已验证市场证据生成改款、定位、人群与定价建议。</p>}</section>
    <section className="node-detail-section"><h3>生成约束</h3><ul className="trace-list"><li><Check />每条建议关联市场痛点依据</li><li><Check />结合工厂成本、MOQ 与定制能力</li><li><Check />标注优先级、风险与置信度</li></ul></section>
  </>;
  return <>
    <section className="node-detail-section report-result"><h3>最终输出</h3>{summary ? <><FileText /><strong>{summary.title}</strong><span>机会评分 {summary.overall_opportunity_score} · 置信度 {Math.round((summary.overall_confidence || 0) * 100)}%</span><div><button onClick={() => { location.hash = `report-detail?id=${result.report_uuid}&from=workbench`; }}>查看完整报告 <ArrowRight /></button><button className="pdf-action" onClick={() => { location.hash = `report-detail?id=${result.report_uuid}&from=workbench&print=1`; }}>导出 PDF <FileText /></button></div></> : <p className="detail-placeholder">全部节点完成后生成完整报告，并开放 PDF 导出。</p>}</section>
  </>;
}

function WorkflowChatLog({ messages }) {
  const endRef = useRef(null);
  const items = withoutFailureNotices(messages || []).filter((item) => item?.text);
  useEffect(() => {
    const root = endRef.current?.parentElement;
    if (root) root.scrollTop = root.scrollHeight;
  }, [items.length, items.at(-1)?.text, items.at(-1)?.client_message_id]);
  return (
    <div className="vertical-chat-messages">
      {items.map((item, index) => (
        <div className={`${item.role} ${item.kind || "text"}`} key={item.client_message_id || index}>
          {item.role === "assistant" && <Pulse />}
          <div>
            <p>{item.text}</p>
            {item.action?.href && <button type="button" className="chat-inline-action" onClick={() => { location.hash = item.action.href; }}>{item.action.label || "打开"}</button>}
          </div>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}

export function WorkflowCanvas({ Sidebar, Topbar }) {
  const query = useMemo(() => {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const raw = params.get("workspace");
    if (raw && !isUuid(raw)) {
      params.set("workspace", createUuid());
      const page = (location.hash.slice(1).split("?")[0] || "workflow");
      window.history.replaceState(null, "", `#${page}?${params.toString()}`);
    }
    return params;
  }, []);
  const fromProducts = query.get("from") === "products";
  const [products, setProducts] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [product, setProduct] = useState(null);
  const [dataset, setDataset] = useState(null);
  const [task, setTask] = useState(null);
  const [result, setResult] = useState(null);
  const [insights, setInsights] = useState({ competitors: [], reviewAspects: [], clusters: [], opportunities: [], recommendations: [], evidence: [] });
  const [historyTasks, setHistoryTasks] = useState([]);
  const [conversationSearch, setConversationSearch] = useState("");
  const [activeConversationId, setActiveConversationId] = useState(() => query.get("workspace") || query.get("task") || createUuid());
  const [aiCollapsed, setAiCollapsed] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [nextGuideCollapsed, setNextGuideCollapsed] = useState(true);
  const [contextSetupCollapsed, setContextSetupCollapsed] = useState(true);
  const [paneSizes, setPaneSizes] = useState(readStoredPaneSizes);
  const [resizeTarget, setResizeTarget] = useState(null);
  const workflowBodyRef = useRef(null);
  const paneSizesRef = useRef(paneSizes);
  const [selectedNode, setSelectedNode] = useState("start");
  const [executionTarget, setExecutionTarget] = useState("report");
  const [previewMode, setPreviewMode] = useState("node");
  const [chatInput, setChatInput] = useState("");
  const [messages, setMessages] = useState(() => planeGreeting(query.get("name") || "", query.get("workspace") || ""));
  const [taskName, setTaskName] = useState(query.get("name") || "产品出海机会分析");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const poller = useRef(null);
  const streamStop = useRef(null);
  const dragOrigin = useRef(null);

  useEffect(() => { paneSizesRef.current = paneSizes; }, [paneSizes]);

  useEffect(() => {
    localStorage.setItem("furniscope-workflow-pane-sizes", JSON.stringify(paneSizes));
  }, [paneSizes]);

  useEffect(() => {
    const apply = () => {
      const width = workflowBodyRef.current?.clientWidth;
      if (!width) return;
      setPaneSizes((current) => fitPaneSizes(current, width, { historyCollapsed, aiCollapsed }));
    };
    const frame = window.requestAnimationFrame(apply);
    window.addEventListener("resize", apply);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", apply);
    };
  }, [historyCollapsed, aiCollapsed]);

  const beginResize = (pane, event) => {
    event.preventDefault();
    event.stopPropagation();
    const historyIsCollapsed = pane === "history" ? false : historyCollapsed;
    const aiIsCollapsed = pane === "chat" ? false : aiCollapsed;
    if (pane === "history" && historyCollapsed) setHistoryCollapsed(false);
    if (pane === "chat" && aiCollapsed) setAiCollapsed(false);
    const handle = event.currentTarget;
    const pointerId = event.pointerId;
    try { handle.setPointerCapture(pointerId); } catch { /* capture not required */ }
    const originX = event.clientX;
    const originSize = paneSizesRef.current[pane];
    setResizeTarget({ pane, x: originX, size: originSize });
    document.body.classList.add("resizing-workflow-panes");

    const move = (moveEvent) => {
      const delta = moveEvent.clientX - originX;
      const width = workflowBodyRef.current?.clientWidth || 0;
      setPaneSizes((current) => {
        const next = { ...current };
        if (pane === "history") next.history = originSize + delta;
        if (pane === "chat") next.chat = originSize + delta;
        if (pane === "preview") next.preview = originSize - delta;
        return fitPaneSizes(next, width, { historyCollapsed: historyIsCollapsed, aiCollapsed: aiIsCollapsed });
      });
    };
    const stop = () => {
      setResizeTarget(null);
      document.body.classList.remove("resizing-workflow-panes");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      try { handle.releasePointerCapture(pointerId); } catch { /* already released */ }
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  };

  const loadInsights = async (uuid) => {
    const names = ["competitors", "review-aspects", "insight-clusters", "opportunities", "recommendations", "evidence"];
    const pages = await Promise.all(names.map((name) => api(`/api/v1/analysis-tasks/${uuid}/${name}`).catch(() => ({ items: [] }))));
    const next = { competitors: pages[0].items || [], reviewAspects: pages[1].items || [], clusters: pages[2].items || [], opportunities: pages[3].items || [], recommendations: pages[4].items || [], evidence: pages[5].items || [] };
    setInsights(next);
    return next;
  };

  const loadProductDetail = async (id, productPage = products) => {
    if (!id) return setProduct(null);
    const detail = await api(`/api/v1/products/${id}`);
    setProduct(detail);
    const matched = datasets.find((item) => item.status === "ready" && (!item.category_code || item.category_code === detail.category_code));
    if (!dataset && matched) setDataset(matched);
    return detail || productPage.find((item) => String(item.product_id) === String(id));
  };

  const loadTask = async (uuid, summaryItem = null, availableDatasets = datasets) => {
    setResult(null);
    setInsights({ competitors: [], reviewAspects: [], clusters: [], opportunities: [], recommendations: [], evidence: [] });
    const detail = await api(`/api/v1/analysis-tasks/${uuid}?include_stage_runs=true`);
    const merged = { ...summaryItem, ...detail };
    setTask(merged);
    setTaskName(summaryItem?.job_name || detail.job_name || "产品出海机会分析");
    const productId = summaryItem?.product_id || detail.product_id;
    if (productId) {
      const productDetail = await api(`/api/v1/products/${productId}`);
      setProduct(productDetail);
      const country = summaryItem?.target_country;
      const platform = summaryItem?.target_platform;
      setDataset(availableDatasets.find((item) => item.status === "ready" && (!country || item.market_country === country) && (!platform || item.platform === platform) && (!item.category_code || item.category_code === productDetail.category_code)) || availableDatasets.find((item) => item.status === "ready" && (!country || item.market_country === country)) || null);
    }
    if (detail.status === "succeeded" && detail.report_uuid) {
      setResult(await api(`/api/v1/analysis-tasks/${uuid}/result`));
      await loadInsights(uuid);
      setPreviewMode("report");
    } else if (detail.status === "partial_succeeded") {
      await loadInsights(uuid);
      const reached = nodeDefinitions[stageOrder[detail.stage] || 1];
      setSelectedNode(reached.id);
      setExecutionTarget(reached.id);
      setPreviewMode("node");
    } else if (["failed", "cancelled"].includes(detail.status)) {
      const node = failedNodeId(merged);
      setSelectedNode(node);
      setExecutionTarget(node);
      setPreviewMode("node");
    }
    if (["queued", "running"].includes(detail.status)) watchTask(uuid);
    return merged;
  };

  useEffect(() => {
    let active = true;
    async function bootstrap() {
      try {
        const [productPage, datasetPage, taskPage, workspacePage] = await Promise.all([
          api("/api/v1/products?page_size=100"),
          api("/api/v1/market-datasets?page_size=100"),
          api(PLANE_TASKS_PATH),
          fetchWorkspaces().catch(() => []),
        ]);
        if (!active) return;
        const nextProducts = productPage.items || [];
        const nextDatasets = datasetPage.items || [];
        setProducts(nextProducts);
        setDatasets(nextDatasets);
        const workspaces = mergeWorkspaceLists(workspacePage, (taskPage.items || []).filter(isPlaneTask));
        setHistoryTasks(workspaces);
        const requestedTask = query.get("task");
        const requestedWorkspace = query.get("workspace");
        const requestedProduct = query.get("product");
        const requestedDataset = query.get("dataset");
        const requestedName = query.get("name") || "";
        if (requestedName) setTaskName(requestedName);
        if (requestedWorkspace) {
          const listed = workspaces.find((item) => workspaceId(item) === requestedWorkspace);
          const stored = await fetchWorkspaceMessages(requestedWorkspace);
          if (listed) {
            setActiveConversationId(requestedWorkspace);
            setTaskName(listed.job_name || requestedName || "产品出海机会分析");
            setMessages(stored.length ? stored : planeGreeting(listed.job_name, requestedWorkspace));
            const uuid = requestedTask || listed.task_uuid;
            const run = (listed.runs || []).find((item) => String(item.task_uuid) === String(uuid))
              || (taskPage.items || []).find((item) => String(item.task_uuid) === String(uuid))
              || listed;
            if (uuid) await loadTask(uuid, run, nextDatasets);
          } else {
            setActiveConversationId(requestedWorkspace);
            const hinted = matchCatalogProduct(nextProducts, requestedName);
            if (hinted) {
              const detail = await api(`/api/v1/products/${hinted.product_id}`);
              if (!active) return;
              setProduct(detail);
              setDataset(readyDatasetFor(detail, nextDatasets));
            }
            const draft = {
              workspace_uuid: requestedWorkspace,
              job_name: requestedName || "未命名工作台",
              product_id: hinted?.product_id,
              product_sku: hinted?.sku || "",
              product_name: hinted?.name || "",
              status: "draft",
              source: PLANE_SOURCE,
            };
            savePlaneWorkspace(draft);
            setMessages(stored.length ? stored : planeGreeting(draft.job_name, requestedWorkspace));
            if (requestedTask) {
              const listedTask = (taskPage.items || []).find((item) => String(item.task_uuid) === requestedTask);
              await loadTask(requestedTask, listedTask, nextDatasets);
            }
          }
        } else if (requestedTask) {
          const listed = (taskPage.items || []).find((item) => String(item.task_uuid) === requestedTask);
          const id = workspaceId(listed) || requestedTask;
          setActiveConversationId(id);
          const stored = await fetchWorkspaceMessages(id);
          setMessages(stored.length ? stored : planeGreeting(listed?.job_name, id));
          await loadTask(requestedTask, listed, nextDatasets);
        }
        else if (requestedProduct) {
          const detail = await api(`/api/v1/products/${requestedProduct}`);
          if (!active) return;
          setProduct(detail);
          setTaskName(`${detail.name} 出海机会分析`);
          setDataset(nextDatasets.find((item) => item.status === "ready" && (!item.category_code || item.category_code === detail.category_code)) || nextDatasets.find((item) => item.status === "ready") || null);
          const existingId = reusePlaneWorkspace(workspaces, detail, query.get("workspace"));
          setActiveConversationId(existingId);
          window.history.replaceState(null, "", `#workflow?workspace=${existingId}&product=${requestedProduct}${fromProducts ? "&from=products" : ""}`);
        }
        if (requestedDataset) {
          setDataset(nextDatasets.find((item) => String(item.dataset_id) === String(requestedDataset) && item.status === "ready") || null);
        }
      } catch (reason) { if (active) setError(messageOf(reason)); }
    }
    bootstrap();
    return () => { active = false; if (poller.current) window.clearInterval(poller.current); streamStop.current?.(); };
  }, []);

  const watchTask = (uuid) => {
    if (poller.current) window.clearInterval(poller.current);
    streamStop.current?.();
    let finished = false;
    let receivedEvent = false;
    const acceptUpdate = async (current) => {
      if (finished) return;
      receivedEvent = true;
      setTask((value) => ({ ...value, ...current }));
      setHistoryTasks((items) => items.map((item) => workspaceId(item) === activeConversationId
        ? { ...item, ...current, workspace_uuid: activeConversationId, runs: (item.runs || []).map((run) => String(run.task_uuid) === String(uuid) ? { ...run, ...current } : run), updated_at: new Date().toISOString() }
        : item));
      if (!terminalStatuses.includes(current.status)) return;
      finished = true;
      if (poller.current) window.clearInterval(poller.current);
      poller.current = null;
      streamStop.current?.();
      streamStop.current = null;
      if (current.status === "succeeded" && current.report_uuid) {
        const output = await api(`/api/v1/analysis-tasks/${uuid}/result`);
        setResult(output);
        await loadInsights(uuid);
        setSelectedNode("report");
        setPreviewMode("report");
        setMessages((items) => [...items, chatTurn("assistant", "本次分析已完成。报告在右侧预览，你可以继续追问，或在当前工作台发起下一次分析。", { kind: "run_event" })]);
      } else if (current.status === "partial_succeeded") {
        await loadInsights(uuid);
        const reached = nodeDefinitions[stageOrder[current.stage] || 1];
        setSelectedNode(reached.id);
        setExecutionTarget(reached.id);
        setPreviewMode("node");
        setMessages((items) => [...items, chatTurn("assistant", `已完成到“${reached.title}”的指定范围。结果已同步到右侧节点详情；如需继续，可选择下游节点发起新的范围分析。`, { kind: "run_event" })]);
      } else if (current.status === "waiting_human") setSelectedNode(nodeDefinitions[stageOrder[current.stage] || 1].id);
      else {
        const node = failedNodeId(current);
        setSelectedNode(node);
        setExecutionTarget(node);
        setPreviewMode("node");
      }
    };
    const startPollingFallback = () => {
      if (finished || poller.current) return;
      poller.current = window.setInterval(async () => {
        try { await acceptUpdate(await api(`/api/v1/analysis-tasks/${uuid}?include_stage_runs=true`)); }
        catch (reason) { setError(messageOf(reason)); }
      }, 1800);
    };
    streamStop.current = subscribeTaskEvents(uuid, (current) => { acceptUpdate(current).catch((reason) => setError(messageOf(reason))); }, startPollingFallback);
    window.setTimeout(() => { if (!finished && !receivedEvent) startPollingFallback(); }, 4500);
  };

  const runWorkflow = async (overrideProduct = null, overrideDataset = null, overrideTarget = null) => {
    const chosenProduct = overrideProduct || product;
    const chosenDataset = overrideDataset || dataset || datasets.find((item) => item.status === "ready" && (!item.category_code || item.category_code === chosenProduct?.category_code)) || datasets.find((item) => item.status === "ready");
    const chosenTarget = overrideTarget || executionTarget;
    if (!chosenProduct?.current_profile_version_id) throw new Error("请先选择已完成画像的产品档案");
    if (!chosenDataset) throw new Error("没有可用的已授权市场数据集");
    if (overrideProduct) setProduct(overrideProduct);
    if (!dataset || overrideDataset) setDataset(chosenDataset);
    const workspace = reusePlaneWorkspace(historyTasks, chosenProduct, activeConversationId);
    if (workspace !== activeConversationId) setActiveConversationId(workspace);
    const jobName = taskName.trim() || `${chosenProduct.name} 出海机会分析`;
    const created = await api("/api/v1/analysis-tasks", {
      method: "POST", headers: { "Idempotency-Key": idempotencyKey("workflow") },
      body: JSON.stringify({
        job_name: jobName,
        job_type: "product_market_fit", product_id: chosenProduct.product_id,
        product_profile_version_id: chosenProduct.current_profile_version_id,
        dataset_id: chosenDataset.dataset_id, target_country: chosenDataset.market_country,
        target_platform: chosenDataset.platform, analysis_currency: "USD",
        analysis_config: {
          source: "node_workflow_canvas",
          workspace_uuid: workspace,
          include_forecast: chosenTarget === "report",
          trace_evidence: true,
          target_node: chosenTarget,
        },
      }),
    });
    const boundWorkspace = created.workspace_uuid || workspace;
    if (boundWorkspace !== workspace) {
      const previous = loadPlaneChat(workspace);
      if (previous?.length) savePlaneChat(boundWorkspace, previous);
      removePlaneWorkspace(workspace);
    }
    if (boundWorkspace !== activeConversationId) setActiveConversationId(boundWorkspace);
    await persistWorkspace({
      workspace_uuid: boundWorkspace,
      job_name: jobName,
      product_id: chosenProduct.product_id,
      source: PLANE_SOURCE,
    });
    window.history.replaceState(null, "", `#workflow?workspace=${boundWorkspace}${fromProducts ? "&from=products" : ""}`);
    setTask(created); setResult(null); setInsights({ competitors: [], reviewAspects: [], clusters: [], opportunities: [], recommendations: [], evidence: [] }); setSelectedNode("product"); setExecutionTarget(chosenTarget);
    const createdSummary = { ...created, source: "node_workflow_canvas", workspace_uuid: boundWorkspace, product_id: chosenProduct.product_id, product_sku: chosenProduct.sku, product_name: chosenProduct.name, target_country: chosenDataset.market_country, target_platform: chosenDataset.platform, updated_at: new Date().toISOString() };
    setHistoryTasks((items) => mergeWorkspaceLists(items, [createdSummary, ...items.flatMap((item) => item.runs || (item.task_uuid ? [item] : []))]));
    setMessages((items) => {
      savePlaneChat(boundWorkspace, items);
      return items;
    });
    await api(`/api/v1/analysis-tasks/${created.task_uuid}:start`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey("workflow-start") }, body: JSON.stringify({}) });
    setTask((value) => ({ ...value, status: "queued", stage: "understanding_product" }));
    watchTask(created.task_uuid);
    return created;
  };

  const start = async () => {
    setBusy(true); setError("");
    try { await runWorkflow(); } catch (reason) { setError(messageOf(reason)); } finally { setBusy(false); }
  };

  const answerConfirmation = async (option) => {
    if (!task?.user_confirmation) return;
    setBusy(true); setError("");
    try {
      await api(`/api/v1/analysis-tasks/confirmations/${task.user_confirmation.confirmation_id}:answer`, { method: "POST", body: JSON.stringify({ selected_option: option, user_input: null }) });
      setTask((value) => ({ ...value, status: "queued", user_confirmation: null }));
      watchTask(task.task_uuid);
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(false); }
  };

  const sendChat = async () => {
    const text = chatInput.trim();
    if (!text || busy) return;
    setChatInput(""); setMessages((items) => [...items, chatTurn("user", text)]);
    if (isForecastQuestion(text)) {
      const reply = forecastChatReply(product || matchCatalogProduct(products, text));
      setMessages((items) => [...items, chatTurn("assistant", reply.text, { kind: "task_chat", action: reply.action })]);
      return;
    }
    const inferredTarget = inferExecutionTarget(text);
    setSelectedNode(inferredTarget);
    setExecutionTarget(inferredTarget);
    setPreviewMode(inferredTarget === "report" && result ? "report" : "node");
    const namedInMessage = matchCatalogProduct(products, text);
    const inferredIndex = nodeDefinitions.findIndex((node) => node.id === inferredTarget);
    const reachedIndex = stageOrder[task?.stage] || 1;
    const continuePartialRun = task?.status === "partial_succeeded"
      && inferredIndex > reachedIndex
      && /继续|评估|评分|方案|建议|报告|完整/.test(text);
    const retryFromChat = ["failed", "cancelled"].includes(task?.status) && /重试|重新|继续/.test(text);
    if (!task || namedInMessage || continuePartialRun || retryFromChat) {
      setBusy(true);
      try {
        const hinted = namedInMessage || product || matchCatalogProduct(products, taskName);
        const matched = hinted?.product_id && hinted.current_profile_version_id
          ? hinted
          : hinted ? await api(`/api/v1/products/${hinted.product_id}`) : null;
        if (!matched) throw new Error("没有匹配到产品，请输入产品名称或 SKU，或先在开始节点选择产品");
        const country = [["美国", "US"], ["英国", "GB"], ["德国", "DE"], ["法国", "FR"], ["日本", "JP"], ["加拿大", "CA"], ["澳大利亚", "AU"]].find(([label]) => text.includes(label))?.[1];
        const instructedDataset = datasets.find((item) => item.status === "ready" && (!country || item.market_country === country) && (!item.category_code || item.category_code === matched.category_code)) || datasets.find((item) => item.status === "ready" && (!country || item.market_country === country));
        if (country && !instructedDataset) throw new Error(`没有匹配到 ${country} 市场的已授权数据集`);
        await runWorkflow(matched, instructedDataset || null, inferredTarget);
        const targetTitle = nodeDefinitions.find((node) => node.id === inferredTarget)?.title;
        setMessages((items) => [...items, chatTurn("assistant", `已载入 ${matched.name} 的企业产品档案${instructedDataset ? `与 ${instructedDataset.market_country} 市场数据` : ""}，并运行至“${targetTitle}”。画布已同步标出运行终点。`, { kind: "run_event" })]);
      } catch (reason) { setMessages((items) => [...items, chatTurn("assistant", messageOf(reason), { kind: "error" })]); }
      finally { setBusy(false); }
      return;
    }
    const grounded = evidenceChatReply(text, { result, insights });
    setBusy(true);
    try {
      const answer = await api(`/api/v1/analysis-tasks/${task.task_uuid}/chat`, { method: "POST", body: JSON.stringify({ question: text, history: withoutFailureNotices(messages).slice(-6).map((item) => ({ role: item.role, content: item.text })) }) });
      const forecastAction = answer.suggested_action === "forecast" || isForecastQuestion(text)
        ? forecastChatReply(product).action
        : undefined;
      setMessages((items) => [...items, chatTurn("assistant", `${answer.answer}${answer.evidence_refs?.length ? `（证据引用 ${answer.evidence_refs.length} 项）` : ""}`, { kind: "task_chat", evidence_refs: answer.evidence_refs || [], action: forecastAction })]);
    } catch (reason) {
      const fallback = grounded || "这个问题超出当前任务已落库的市场洞察证据。我可以回答竞品、评论痛点、机会评分和改款建议；未来销量请使用「销量预测」。";
      setMessages((items) => [...items, chatTurn("assistant", fallback, { kind: grounded ? "task_chat" : "error", action: /销量|预测/.test(text) ? forecastChatReply(product).action : undefined })]);
    } finally { setBusy(false); }
  };

  useEffect(() => {
    savePlaneChat(activeConversationId, withoutFailureNotices(messages));
    const timer = window.setTimeout(() => {
      persistWorkspaceMessages(activeConversationId, withoutFailureNotices(messages), {
        job_name: taskName,
        product_id: product?.product_id,
        task_uuid: task?.task_uuid,
      }).catch(() => {});
    }, 400);
    return () => window.clearTimeout(timer);
  }, [activeConversationId, messages, taskName, product?.product_id, task?.task_uuid]);

  const persistCurrentChat = () => savePlaneChat(activeConversationId, withoutFailureNotices(messages));
  const beginPan = (event) => {
    if (event.target.closest("button, .pane-resizer") || document.body.classList.contains("resizing-workflow-panes")) return;
    const edge = event.clientX - event.currentTarget.getBoundingClientRect().left;
    if (edge <= 20) {
      beginResize("chat", event);
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    dragOrigin.current = { x: event.clientX - pan.x, y: event.clientY - pan.y };
    setDragging(true);
  };
  const movePan = (event) => {
    if (!dragging || !dragOrigin.current) return;
    setPan({ x: event.clientX - dragOrigin.current.x, y: event.clientY - dragOrigin.current.y });
  };
  const endPan = () => { setDragging(false); dragOrigin.current = null; };

  const newConversation = () => {
    persistCurrentChat();
    if (poller.current) window.clearInterval(poller.current);
    streamStop.current?.();
    setTask(null); setResult(null); setProduct(null); setDataset(null);
    setInsights({ competitors: [], reviewAspects: [], clusters: [], opportunities: [], recommendations: [], evidence: [] });
    setTaskName("新建产品出海分析"); setSelectedNode("start"); setExecutionTarget("report"); setPreviewMode("node");
    const id = createUuid();
    setActiveConversationId(id);
    setMessages(planeGreeting("新建产品出海分析", id));
    savePlaneWorkspace({ workspace_uuid: id, job_name: "新建产品出海分析", status: "draft", source: PLANE_SOURCE });
    window.history.replaceState(null, "", `#workflow?new=1&workspace=${id}`);
  };

  const openConversation = async (item) => {
    persistCurrentChat();
    setError(""); setSelectedNode("start"); setPreviewMode("node");
    const id = workspaceId(item);
    setActiveConversationId(id);
    setTaskName(item.job_name || "产品出海机会分析");
    const stored = await fetchWorkspaceMessages(id);
    setMessages(stored.length ? stored : planeGreeting(item.job_name, id));
    window.history.replaceState(null, "", `#workflow?workspace=${id}${fromProducts ? "&from=products" : ""}`);
    if (!item.task_uuid) {
      setTask(null); setResult(null); setProduct(null);
      return;
    }
    try {
      await loadTask(item.task_uuid, item, datasets);
    } catch (reason) { setError(messageOf(reason)); }
  };

  const selectedDefinition = nodeDefinitions.find((node) => node.id === selectedNode) || nodeDefinitions[0];
  const targetDefinition = nodeDefinitions.find((node) => node.id === executionTarget) || nodeDefinitions.at(-1);
  const activeWorkspace = historyTasks.find((item) => workspaceId(item) === activeConversationId);
  const taskRunning = ["queued", "running"].includes(task?.status);
  const readyDatasets = datasets.filter((item) => item.status === "ready");
  const marketLabel = marketNames[dataset?.market_country] || dataset?.market_country || "目标市场";
  const guidedPrompts = product ? [
    `分析${product.name}在${marketLabel}的竞品、价格带和用户评论痛点`,
    `评估${product.name}在${marketLabel}的市场机会并给出五维评分`,
    `为${product.name}生成${marketLabel}出海完整分析报告和改款建议`,
  ] : [];
  const nextStepPrompts = (() => {
    if (!task) {
      if (product) return guidedPrompts;
      return ["告诉我想分析的产品名称或 SKU", "分析指定产品在美国市场的竞品、价格带和用户评论痛点"];
    }
    if (taskRunning) return ["当前分析运行到哪一步？", "本次分析将输出哪些结果？"];
    if (task.status === "waiting_human") return ["说明当前需要我确认什么，以及不同选择的影响"];
    if (["failed", "cancelled"].includes(task.status)) return [
      `重新运行${product?.name || "当前产品"}的${targetDefinition.title}`,
      "说明刚才失败的节点和可以采取的修复措施",
    ];
    if (task.status === "partial_succeeded") {
      const reached = stageOrder[task.stage] || 1;
      if (reached <= 2) return [
        "总结当前发现的三大评论痛点并列出证据",
        `继续评估${product?.name || "该产品"}的市场机会并给出五维评分`,
        `继续为${product?.name || "该产品"}生成完整分析报告`,
      ];
      if (reached === 3) return [
        "解释五维机会评分中最高和最低的两项",
        `继续为${product?.name || "该产品"}生成产品定位和改款方案`,
        `继续生成${product?.name || "该产品"}的完整分析报告`,
      ];
      return ["总结当前产品方案的三个最高优先级动作", "继续生成完整分析报告"];
    }
    if (task.status === "succeeded") return [
      "总结最值得关注的三大用户评论痛点并引用证据",
      "解释市场机会评分的主要依据和风险",
      "给出优先级最高的三项改款建议和成本影响",
    ];
    return [
      `继续分析${product?.name || "当前产品"}的竞品、价格带和用户评论痛点`,
      `评估${product?.name || "当前产品"}的市场机会并给出五维评分`,
      `为${product?.name || "当前产品"}生成完整分析报告`,
    ];
  })();
  const chooseChatProduct = async (id) => {
    setError("");
    if (!id) { setProduct(null); setDataset(null); return; }
    try {
      const detail = await loadProductDetail(id);
      if (!detail) return;
      setTaskName(`${detail.name} 出海机会分析`);
      setMessages((items) => [...items, chatTurn("assistant", `已选择产品“${detail.name}”（${detail.sku}）。可不填市场数据；直接在下方提问，或展开建议开始分析。`, { kind: "context" })]);
    } catch (reason) { setError(messageOf(reason)); }
  };
  return <main className="workspace workflow-studio-page">
    <Sidebar page="analysis" />
    <section className="workspace-main"><Topbar />
      <div className="workflow-studio">
        <header className="workflow-toolbar">
          <div><ParentPageTab className="compact workflow-parent-tab" label={fromProducts ? "产品中心" : "AI 工作台"} current="分析工作台" to={fromProducts ? "products" : "analysis"} /><input value={taskName} onChange={(event) => setTaskName(event.target.value)} aria-label="工作台名称" /><small>{task ? `${historyTasks.find((item) => workspaceId(item) === activeConversationId)?.analysis_count || 1} 次分析 · 当前${taskStatusLabel(task)}` : "尚未运行分析"}</small></div>
          <div className="workflow-run-scope"><label>运行范围<select value={executionTarget} disabled={busy || taskRunning} onChange={(event) => { setExecutionTarget(event.target.value); setSelectedNode(event.target.value); setPreviewMode("node"); }}>{executionTargets.map((node) => <option value={node.id} key={node.id}>{node.id === "report" ? "完整工作流" : `运行至 · ${node.title}`}</option>)}</select></label><button className="run-workflow" onClick={start} disabled={busy || taskRunning || !product} title={!product ? "请先选择产品，或在对话里说出产品名 / SKU" : ""}><Play weight="fill" />{taskRunning ? `运行中 ${task.progress_percent || 0}%` : busy ? "正在启动" : !product ? "请先选择产品" : executionTarget === "report" ? "运行完整工作流" : `运行至${targetDefinition.title}`}</button></div>
        </header>
        {error && <div className="workflow-error"><WarningCircle />{error}<button onClick={() => setError("")}>×</button></div>}
        <div ref={workflowBodyRef} className={`workflow-body ${aiCollapsed ? "ai-collapsed" : ""} ${historyCollapsed ? "history-collapsed" : ""} ${resizeTarget ? "is-resizing" : ""}`} style={{ "--history-pane": `${paneSizes.history}px`, "--chat-pane": `${paneSizes.chat}px`, "--preview-pane": `${paneSizes.preview}px` }}>
          {aiCollapsed ? <aside className="ai-conversation-collapsed"><button onClick={() => setAiCollapsed(false)} title="展开 AI 对话"><ChatCircleText /><span>展开 AI 对话</span><CaretRight /></button></aside> : <>
            <aside className="workflow-conversation-history-pane">
              <header><div><ChatCircleText /><span><strong>{historyCollapsed ? "" : "分析工作台"}</strong><small>{historyCollapsed ? "" : `${historyTasks.length} 个工作台`}</small></span></div><div><button onClick={newConversation} title="新建工作台"><Plus /></button><button onClick={() => setHistoryCollapsed((value) => !value)} title={historyCollapsed ? "展开工作台列表" : "折叠工作台列表"}>{historyCollapsed ? <CaretRight /> : <CaretLeft />}</button><button onClick={() => setAiCollapsed(true)} title="折叠整个 AI 对话"><ChatCircleText /></button></div></header>
              {!historyCollapsed && <><label className="conversation-search"><MagnifyingGlass /><input value={conversationSearch} onChange={(event) => setConversationSearch(event.target.value)} placeholder="搜索分析工作台" /></label><div className="conversation-history"><small>按工作台归档分析</small>{historyTasks.filter((item) => `${item.job_name} ${item.product_sku} ${item.product_name}`.toLowerCase().includes(conversationSearch.trim().toLowerCase())).map((item) => <button className={workspaceId(item) === activeConversationId ? "active" : ""} key={workspaceId(item)} onClick={() => openConversation(item)}><span><ChatCircleText /><i className={item.status} /></span><div><strong>{item.job_name}</strong><small>{item.analysis_count} 次分析 · {item.product_sku || "待选择产品"}</small></div><time>{new Date(item.updated_at).toLocaleDateString("zh-CN")}</time></button>)}{!historyTasks.length && <p className="conversation-empty">还没有分析工作台。新建后可在同一工作台中进行多次分析。</p>}</div></>}
            </aside>
            <div className={`pane-resizer ${resizeTarget?.pane === "history" ? "active" : ""}`} role="separator" aria-orientation="vertical" aria-label="拖动调整历史栏宽度" title="拖动调整宽度" onPointerDown={(event) => beginResize("history", event)} />
            <aside className="workflow-active-chat-pane">
              <header><div><Pulse /><span><strong>AI 分析对话</strong><small>选择对象、表达目标并启动真实工作流</small></span></div></header>
              <section className="active-conversation">
                <header><div><strong>{task?.job_name || taskName}</strong><small>{product ? `${product.sku} · ${product.name}${dataset ? ` · ${marketLabel}` : " · 市场数据将自动匹配"}` : "可不选产品，直接在对话里说名称或 SKU"}</small></div><span className={task?.status || "draft"} title={["failed", "cancelled"].includes(task?.status) ? failureReason(task) : undefined}>{taskStatusLabel(task)}</span></header>
                <div className="conversation-main">
                  <WorkflowChatLog messages={messages} />
                </div>
                <div className="conversation-dock">
                  <section className={`conversation-context-setup ${product ? "ready" : ""} ${contextSetupCollapsed ? "collapsed" : ""}`}>
                    <header>
                      <div>
                        <span>分析对象</span>
                        <strong>{product ? `${product.sku} · ${product.name}` : "你想分析哪个产品？"}</strong>
                        {contextSetupCollapsed && <small>{product ? (dataset ? `${marketLabel} · ${dataset.name}` : "市场数据将自动匹配，也可在对话里指定") : "可不填，对话里直接说产品名或 SKU 即可"}</small>}
                      </div>
                      <b>{product ? (dataset ? "已就绪" : "将自动匹配") : "可不填"}</b>
                      <button type="button" className="conversation-next-toggle" onClick={() => setContextSetupCollapsed((value) => !value)} aria-expanded={!contextSetupCollapsed} aria-label={contextSetupCollapsed ? "展开分析对象" : "收起分析对象"}>
                        {contextSetupCollapsed ? <CaretDown /> : <CaretUp />}
                        {contextSetupCollapsed ? "展开" : "收起"}
                      </button>
                    </header>
                    {!contextSetupCollapsed && <>
                      <label><span><i>1</i>产品</span><select aria-label="对话分析产品" value={product?.product_id || ""} onChange={(event) => chooseChatProduct(event.target.value)}><option value="">选择已建档产品（可不选）</option>{products.map((item) => <option value={item.product_id} key={item.product_id}>{item.sku} · {item.name}</option>)}</select></label>
                      <label><span><i>2</i>市场数据</span><select aria-label="对话市场数据集" value={dataset?.dataset_id || ""} disabled={!product} onChange={(event) => setDataset(readyDatasets.find((item) => String(item.dataset_id) === event.target.value) || null)}><option value="">{product ? "自动匹配（可不选）" : "选产品后自动匹配"}</option>{readyDatasets.map((item) => <option value={item.dataset_id} key={item.dataset_id}>{item.name} · {marketNames[item.market_country] || item.market_country}</option>)}</select></label>
                      {!products.length && <button type="button" onClick={() => { location.hash = "products"; }}><Plus />先去产品中心创建产品</button>}
                      {product && !dataset && !readyDatasets.length && <p><WarningCircle />当前没有可用数据集。请先到市场洞察导入数据，或在对话里指定市场。</p>}
                      {product && !dataset && readyDatasets.length > 0 && <p className="ready"><Check />未指定时将自动匹配可用市场数据。</p>}
                      {product && dataset && <p className="ready"><Check />将使用“{product.name}”和“{dataset.name}”作为本次分析输入。</p>}
                      {!product && <p className="ready"><Check />不填也可以，在下方直接输入产品名或 SKU。</p>}
                    </>}
                  </section>
                  {nextStepPrompts.length > 0 && (
                    <div className={`conversation-next-guide ${taskRunning ? "running" : ""} ${nextGuideCollapsed ? "collapsed" : ""}`}>
                      <header>
                        <Pulse />
                        <span>
                          <strong>{taskRunning ? "分析正在运行" : "建议下一步"}</strong>
                          {!nextGuideCollapsed && <small>{taskRunning ? "可以询问进度，完成后这里会自动更新" : task ? "根据当前已完成节点生成，可点击后发送" : "可不选分析对象，点击后发送或直接输入问题"}</small>}
                        </span>
                        <button type="button" className="conversation-next-toggle" onClick={() => setNextGuideCollapsed((value) => !value)} aria-expanded={!nextGuideCollapsed} aria-label={nextGuideCollapsed ? "展开建议下一步" : "收起建议下一步"}>
                          {nextGuideCollapsed ? <CaretDown /> : <CaretUp />}
                          {nextGuideCollapsed ? "展开" : "收起"}
                        </button>
                      </header>
                      {!nextGuideCollapsed && <div>{nextStepPrompts.map((text) => <button type="button" key={text} onClick={() => setChatInput(text)}>{text}<ArrowRight /></button>)}</div>}
                    </div>
                  )}
                  <div className="vertical-chat-composer"><textarea aria-label="分析问题" value={chatInput} onChange={(event) => setChatInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendChat(); } }} placeholder={product ? `例如：分析${product.name}在${marketLabel}的竞品和评论痛点` : "输入产品名称、SKU 和分析目标，分析对象可不填"} /><button aria-label="发送并执行" onClick={sendChat} disabled={!chatInput.trim() || busy}><PaperPlaneRight weight="fill" /></button></div>
                </div>
              </section>
            </aside>
          </>}
          <section className={`node-canvas ${dragging ? "dragging" : ""}`} aria-label="AI 工作流节点画布" onPointerDown={beginPan} onPointerMove={movePan} onPointerUp={endPan} onPointerCancel={endPan}>
            <div
              className={`pane-resizer pane-resizer-overlay ${resizeTarget?.pane === "chat" ? "active" : ""}`}
              role="separator"
              aria-orientation="vertical"
              aria-label="拖动调整 AI 对话与画布宽度"
              title="拖动调整 AI 对话 / 画布宽度"
              onPointerDown={(event) => beginResize("chat", event)}
            />
            <div className="canvas-grid" style={{ backgroundPosition: `${pan.x}px ${pan.y}px` }} />
            <div className="canvas-controls"><button onClick={() => setZoom((value) => Math.max(.65, Math.round((value - .1) * 10) / 10))}>−</button><button className="zoom-value" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>{Math.round(zoom * 100)}%</button><button onClick={() => setZoom((value) => Math.min(1.4, Math.round((value + .1) * 10) / 10))}>＋</button></div>
            <div className="canvas-scope-note"><span>点击节点选择运行终点</span><strong>{executionTarget === "report" ? "全链路" : `数据入口 → ${targetDefinition.title}`}</strong></div>
            {task && ["failed", "cancelled"].includes(task.status) && <div className="canvas-failure-note"><WarningCircle /><span>{failureReason(task)}</span></div>}
            <div className="workflow-node-chain" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
              {nodeDefinitions.map((node, index) => {
                const state = nodeState(index, task, product);
                const NodeIcon = node.Icon;
                return <div className="node-with-edge" key={node.id}>
                  <button className={`workflow-node ${state} ${selectedNode === node.id ? "selected" : ""} ${executionTarget === node.id ? "target" : ""}`} onPointerDown={(event) => event.stopPropagation()} onClick={() => { setSelectedNode(node.id); if (node.id !== "start" && !taskRunning) setExecutionTarget(node.id); setPreviewMode(node.id === "report" && result ? "report" : "node"); }}>
                    <span className="node-icon"><NodeIcon /></span><span className="node-copy"><small>{node.step} · {state === "completed" ? "已完成" : state === "running" ? "运行中" : state === "error" ? "异常" : state === "blocked" ? "上游阻断" : state === "warning" ? "待确认" : state === "ready" ? "已就绪" : "待执行"}</small><strong>{node.title}</strong></span>
                    <span className="node-state">{executionTarget === node.id ? <b>终点</b> : state === "completed" ? <Check weight="bold" /> : state === "running" ? <ArrowClockwise /> : state === "error" || state === "warning" ? <WarningCircle /> : <span />}</span>
                  </button>
                  {index < nodeDefinitions.length - 1 && <span className={`node-edge ${["completed", "running", "error", "warning"].includes(nodeState(index + 1, task, product)) ? "active" : ""}`}><i /></span>}
                </div>;
              })}
            </div>
          </section>
          <div className={`pane-resizer ${resizeTarget?.pane === "preview" ? "active" : ""}`} role="separator" aria-orientation="vertical" aria-label="拖动调整报告预览栏宽度" title="拖动调整宽度" onPointerDown={(event) => beginResize("preview", event)} />
          <aside className="node-detail-panel result-preview-panel"><header><div><span>{previewMode === "report" ? "预览" : selectedDefinition.step}</span><h2>{previewMode === "report" ? "分析报告预览" : selectedDefinition.title}</h2></div><div className="preview-tabs"><button className={previewMode === "node" ? "active" : ""} onClick={() => setPreviewMode("node")}>节点详情</button><button className={previewMode === "report" ? "active" : ""} disabled={!result} onClick={() => setPreviewMode("report")}>报告预览</button></div></header><div className="node-detail-scroll">{previewMode === "report" && result ? <section className="inline-report-preview"><span>AI DECISION REPORT</span><h3>{result.report_summary.title}</h3><div><strong>{result.report_summary.overall_opportunity_score}</strong><small>综合机会分</small><strong>{Math.round((result.report_summary.overall_confidence || 0) * 100)}%</strong><small>报告置信度</small></div><em>{result.report_summary.decision_recommendation}</em><p>{result.report_summary.executive_summary}</p><h4>核心建议</h4>{insights.recommendations.slice(0, 4).map((item) => <article key={item.recommendation_id}><b>{item.priority}</b><span>{item.recommended_action}</span></article>)}<button onClick={() => { location.hash = `report-detail?id=${result.report_uuid}&from=workbench`; }}>打开完整报告 <ArrowRight /></button></section> : <>
            {task && ["failed", "cancelled"].includes(task.status) && selectedDefinition.id === failedNodeId(task) && <section className="node-detail-section node-error"><WarningCircle /><strong>{failureReason(task)}</strong><button onClick={start}><ArrowClockwise />重新执行</button></section>}
            {task && ["failed", "cancelled"].includes(task.status) && nodeDefinitions.findIndex((node) => node.id === selectedDefinition.id) > (stageOrder[task.stage] || 1) && <section className="node-detail-section node-blocked"><WarningCircle /><strong>该节点尚未执行</strong><p>上游“{nodeDefinitions[stageOrder[task.stage] || 1]?.title}”执行失败。修复并重试上游后，数据会继续流入当前节点。</p></section>}
            <DetailContent node={selectedDefinition} product={product} dataset={dataset} task={task} result={result} insights={insights} products={products} datasets={datasets} onProduct={(id) => loadProductDetail(id).catch((reason) => setError(messageOf(reason)))} onDataset={(id) => setDataset(datasets.find((item) => String(item.dataset_id) === id) || null)} onRetry={start} />
            {task?.user_confirmation && <section className="node-detail-section confirmation-detail"><WarningCircle /><h3>需要人工确认</h3><p>{task.user_confirmation.question}</p><div>{task.user_confirmation.options.map((option) => <button key={option.code} disabled={busy} onClick={() => answerConfirmation(option.code)}>{option.label || option.code}<small>{option.description}</small></button>)}</div></section>}
            <section className="node-detail-section execution-log"><h3>执行日志</h3>{task?.stage_runs?.length ? task.stage_runs.slice().reverse().map((run) => <div key={`${run.stage_code}-${run.attempt_no}`}><span className={run.status} /><p><strong>{run.stage_code.replaceAll("_", " ")}</strong><small>第 {run.attempt_no} 次执行 · {run.status}</small>{run.error_message && <em>{run.error_message}</em>}</p></div>) : <p className="detail-placeholder">启动任务后，这里会实时记录节点执行状态与异常原因。</p>}</section>
          </>}</div></aside>
        </div>
      </div>
    </section>
  </main>;
}

export function AnalysisCenter({ Sidebar, Topbar }) {
  const [workspaces, setWorkspaces] = useState([]);
  const [launcher, setLauncher] = useState(false);
  const [taskName, setTaskName] = useState("产品出海机会分析");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([fetchWorkspaces().catch(() => []), api(PLANE_TASKS_PATH)])
      .then(([workspacePage, page]) => {
        if (active) setWorkspaces(mergeWorkspaceLists(workspacePage, (page.items || []).filter(isPlaneTask)));
      })
      .catch((reason) => { if (active) setError(messageOf(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const running = workspaces.filter((item) => ["queued", "running"].includes(item.status)).length;
  const completed = workspaces.filter((item) => successfulStatuses.includes(item.status)).length;
  const analysisCount = workspaces.reduce((sum, item) => sum + item.analysis_count, 0);
  const enterNewWorkspace = () => {
    const id = createUuid();
    const name = taskName.trim() || "产品出海机会分析";
    savePlaneWorkspace({ workspace_uuid: id, job_name: name, status: "draft", source: PLANE_SOURCE });
    location.hash = `workflow?new=1&workspace=${id}&name=${encodeURIComponent(name)}`;
  };

  return <main className="workspace ai-workbench-home-page">
    <Sidebar page="analysis" />
    <section className="workspace-main"><Topbar /><div className="ai-workbench-home">
      <header className="workbench-home-hero">
        <div className="workbench-home-copy"><span>AI WORKFLOW STUDIO</span><h1>AI 工作台</h1><p>用分析工作台持续沉淀同一产品与市场目标的多次分析。每次运行都保留节点范围、结果与证据。</p><div><button className="enter-plane" onClick={() => setLauncher(true)}><Pulse />进入分析工作台<ArrowRight /></button><button onClick={() => { location.hash = "products"; }}><SquaresFour />从产品中心快捷启动</button></div></div>
        <div className="workbench-plane-preview"><header><span>WORKBENCH PREVIEW</span><i><b />支持分段或完整运行</i></header><div>{nodeDefinitions.slice(0, 5).map((node, index) => { const Icon = node.Icon; return <div className="preview-node" key={node.id}><i><Icon /></i><span><small>{node.step}</small><strong>{node.title}</strong></span>{index < 4 && <em><ArrowRight /></em>}</div>; })}</div></div>
      </header>
      <section className="workbench-home-metrics">{[[Pulse, "分析工作台", workspaces.length], [ArrowClockwise, "正在运行", running], [FileText, "累计分析", analysisCount], [Check, "最近已完成", completed]].map(([Icon, label, value]) => <article key={label}><Icon /><span><small>{label}</small><strong>{loading ? "—" : value}</strong></span></article>)}</section>
      {error && <div className="workflow-error inline"><WarningCircle />{error}</div>}
      <section className="workbench-recent"><header><div><h2>工作日记</h2><p>最近 10 条分析工作台</p></div><button type="button" className="section-more" onClick={() => { location.hash = "work-diary"; }}>更多 <ArrowRight /></button></header>{!loading && !workspaces.length ? <div className="workbench-empty"><Pulse /><strong>还没有工作日记</strong><p>新建工作台后，分析记录会按时间沉淀在这里。</p></div> : <WorkDiaryList items={workspaces.slice(0, 10)} />}</section>
    </div></section>
    {launcher && <div className="modal-backdrop" onClick={() => setLauncher(false)}><section className="modal workbench-launcher-modal" onClick={(event) => event.stopPropagation()}><button className="close" onClick={() => setLauncher(false)}>×</button><span className="launcher-icon"><Pulse /></span><h3>新建分析工作台</h3><p>工作台用于归档同一分析目标的对话与多次运行结果。</p><label className="plane-task-name">工作台名称<div><input value={taskName} onChange={(event) => setTaskName(event.target.value)} placeholder="例如：HF-A0396 美国市场机会分析" /><button onClick={enterNewWorkspace}>新建并进入</button></div></label><div className="plane-history-title"><strong>已有工作台</strong><span>{workspaces.length} 个</span></div><div className="plane-history-list">{workspaces.length ? workspaces.map((item) => <article key={workspaceId(item)}><img src={productImage(item.product_sku)} onError={imageFallback} alt="" /><div><strong>{item.job_name}</strong><span>{item.product_sku} · {item.target_country} / {item.target_platform}</span><small>{item.analysis_count} 次分析 · {statusLabels[item.status] || item.status}</small></div><button onClick={() => { location.hash = `workflow?workspace=${workspaceId(item)}`; }}>进入工作台</button><button disabled={!item.report_uuid} onClick={() => { location.hash = `report-detail?id=${item.report_uuid}&from=workbench`; }}>最近报告</button></article>) : <div className="plane-history-empty">暂无分析工作台，可以直接新建。</div>}</div></section></div>}
  </main>;
}
