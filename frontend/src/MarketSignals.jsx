import { useEffect, useState } from "react";
import {
  CheckCircle,
  MagnifyingGlass,
  Pulse,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "./api.js";
import "./competitor-tracking.css";

const countries = { US: "美国", GB: "英国", DE: "德国", FR: "法国", CA: "加拿大", AU: "澳大利亚", JP: "日本" };
const sentimentLabels = { positive: "正向", neutral: "中性", negative: "负向" };
const dateText = (value) => (value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—");
const messageOf = (error) => (error instanceof Error ? error.message : String(error));

function PanelState({ error, notice, onClear }) {
  return <>
    {error && <div className="dataset-error"><WarningCircle />{error}<button type="button" onClick={onClear}>×</button></div>}
    {notice && <div className="dataset-cleaning-banner"><CheckCircle /><div><strong>{notice}</strong></div><button type="button" onClick={onClear}>知道了</button></div>}
  </>;
}

export function SentimentStreamPanel({ datasets = [] }) {
  const readyDatasets = datasets.filter((item) => item.status === "ready");
  const defaultDataset = readyDatasets.find((item) => Number(item.review_count) > 0) || readyDatasets[0];
  const [events, setEvents] = useState([]);
  const [stats, setStats] = useState({ total: 0, positive_count: 0, negative_count: 0, neutral_count: 0, live_count: 0, dataset_count: 0, page: 1, page_size: 20, has_next: false });
  const [pageUrl, setPageUrl] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [origin, setOrigin] = useState("all");
  const [sentiment, setSentiment] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (!datasetId && defaultDataset) setDatasetId(String(defaultDataset.dataset_id));
  }, [defaultDataset, datasetId]);

  const load = (nextPage = page) => {
    const params = new URLSearchParams({ page: String(nextPage), page_size: "20", origin });
    if (sentiment) params.set("sentiment", sentiment);
    if (query.trim()) params.set("q", query.trim());
    if (datasetId) params.set("dataset_id", datasetId);
    api(`/api/v1/market-signals/sentiment-feed?${params}`)
      .then((data) => {
        setEvents(data.items || []);
        setStats({
          total: data.total || 0,
          positive_count: data.positive_count || 0,
          negative_count: data.negative_count || 0,
          neutral_count: data.neutral_count || 0,
          live_count: data.live_count || 0,
          dataset_count: data.dataset_count || 0,
          page: data.page || nextPage,
          page_size: data.page_size || 20,
          has_next: Boolean(data.has_next),
        });
        setPage(data.page || nextPage);
        setError("");
      })
      .catch((reason) => setError(messageOf(reason)));
  };
  useEffect(() => { load(1); }, [sentiment, origin, datasetId]);
  useEffect(() => {
    const timer = window.setInterval(() => load(page), 30000);
    return () => window.clearInterval(timer);
  }, [sentiment, origin, datasetId, query, page]);

  const collectSentiment = async () => {
    const endpoint = pageUrl.trim();
    if (!endpoint) return setError("请粘贴评论页网址");
    setBusy(true);
    setError("");
    try {
      const result = await api("/api/v1/market-signals/collect-url", {
        method: "POST",
        body: JSON.stringify({
          endpoint_url: endpoint,
          dataset_id: datasetId ? Number(datasetId) : undefined,
        }),
      });
      if (result.status === "succeeded") {
        const bound = datasetId ? "并写入所选市场数据集" : "（未绑定数据集，仅进入实时采集流）";
        setNotice(`已从页面解析 ${result.fetched_count} 条内容，新增 ${result.inserted_count} 条。${bound}情感由后台算法判断。`);
        setPageUrl("");
      } else {
        setError(result.error_summary || "未能读取该评论页，请确认网址可在浏览器中打开");
      }
      load(1);
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(false); }
  };

  const title = stats.total
    ? `评论库 ${stats.total.toLocaleString("zh-CN")} 条 · 可按情感筛选`
    : "把市场评论和网页采集放在同一条舆情流里";
  return <section className="tracking-panel signal-workspace">
    <PanelState error={error} notice={notice} onClear={() => { setError(""); setNotice(""); }} />
    <header className="signal-workspace-intro"><div><span>评论舆情</span><h2>{title}</h2><p>默认读取已导入数据集里的有效评论。粘贴评论页可补采，写入所选数据集后才能进入 AI 分析。</p></div></header>
    <section className="signal-quick-collect">
      <select aria-label="写入的市场数据集" value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
        <option value="">不绑定数据集（仅实时流）</option>
        {readyDatasets.map((item) => (
          <option value={item.dataset_id} key={item.dataset_id}>{item.name} · 评论 {item.review_count || 0}</option>
        ))}
      </select>
      <input aria-label="评论页网址" value={pageUrl} onChange={(event) => setPageUrl(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") collectSentiment(); }} placeholder="粘贴 Amazon 评论页或独立站评论页 https://" />
      <button type="button" className="catalog-primary" onClick={collectSentiment} disabled={busy}><Pulse />{busy ? "正在获取…" : "获取舆情"}</button>
    </section>
    <div className="signal-metrics">
      <article><small>有效评论</small><strong>{stats.total.toLocaleString("zh-CN")}</strong><span>当前筛选</span></article>
      <article className="positive"><small>正向</small><strong>{stats.positive_count.toLocaleString("zh-CN")}</strong><span>条</span></article>
      <article className="negative"><small>负向</small><strong>{stats.negative_count.toLocaleString("zh-CN")}</strong><span>条</span></article>
      <article><small>中性</small><strong>{stats.neutral_count.toLocaleString("zh-CN")}</strong><span>数据集 {stats.dataset_count} · 采集 {stats.live_count}</span></article>
    </div>
    <section className="signal-results"><header><div><strong>舆情事件</strong><small>市场评论库与实时采集合并展示</small></div><div className="signal-filter-row">
      <select aria-label="来源筛选" value={origin} onChange={(event) => setOrigin(event.target.value)}>
        <option value="all">全部来源</option>
        <option value="dataset">市场数据集</option>
        <option value="live">实时采集</option>
      </select>
      <select aria-label="情感筛选" value={sentiment} onChange={(event) => setSentiment(event.target.value)}><option value="">全部情感</option><option value="positive">正向</option><option value="neutral">中性</option><option value="negative">负向</option></select>
      <label className="signal-search-field"><MagnifyingGlass /><input aria-label="搜索舆情" value={query} onChange={(event) => setQuery(event.target.value)} onBlur={() => load(1)} onKeyDown={(event) => { if (event.key === "Enter") load(1); }} placeholder="搜索评论、ASIN 或编号" /></label>
      <button type="button" onClick={() => load(1)}>搜索</button>
    </div></header>
    <div className="review-preview-list">
      {events.map((item) => <article key={item.item_id}>
        <header>
          <span className={item.sentiment}>{sentimentLabels[item.sentiment] || item.sentiment}</span>
          <em className={`signal-origin ${item.origin}`}>{item.origin === "live" ? "实时采集" : "市场数据集"}</em>
          <time>{dateText(item.occurred_at)}</time>
        </header>
        <p>{item.content_original}</p>
        <footer>
          <span>{item.asin || "未关联商品"}</span>
          <span>{item.dataset_name || countries[item.market_country] || item.market_country || "未标注市场"}</span>
          <b>评分 {item.rating ?? "—"}{item.sentiment_score != null ? ` · 置信 ${item.sentiment_score}` : ""}</b>
        </footer>
      </article>)}
      {!events.length && <div className="tracking-guided-empty"><Pulse /><strong>当前范围没有评论</strong><p>先导入带评论的市场数据集，或粘贴评论页点击「获取舆情」。没有商品正文的空内容档案不会出现在这里。</p></div>}
    </div>
    {stats.total > stats.page_size && (
      <nav className="dataset-pager">
        <span>第 {stats.page} 页 · 共 {stats.total.toLocaleString("zh-CN")} 条</span>
        <button type="button" disabled={stats.page <= 1} onClick={() => load(stats.page - 1)}>上一页</button>
        <button type="button" disabled={!stats.has_next} onClick={() => load(stats.page + 1)}>下一页</button>
      </nav>
    )}
    </section>
  </section>;
}
