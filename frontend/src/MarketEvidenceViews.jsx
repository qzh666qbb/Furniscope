import {
  ArrowLeft,
  ArrowRight,
  CaretLeft,
  CaretRight,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";

const scoreText = (value) => value == null ? "—" : Number(value).toFixed(0);
const percentText = (value) => `${Math.round(Number(value || 0) * 100)}%`;
const dateTimeText = (value) => value ? new Date(value).toLocaleString("zh-CN") : "—";
const changeLabels = {
  price: "价格变动",
  title: "标题更新",
  image: "主图更新",
  bullets: "五点描述更新",
  promo: "促销变动",
  new_listing: "监控目标上新",
};
const valueLabels = {
  sale_price: "售价",
  list_price: "标价",
  currency: "币种",
  title: "标题",
  promo_label: "促销",
  review_count: "评论数",
  rating: "评分",
  snapshot_id: "快照编号",
};
const sentimentLabels = {
  positive: "正向",
  negative: "负向",
  neutral: "中性",
  mixed: "正负并存",
};

function Empty({ children }) {
  return <div className="intelligence-empty"><WarningCircle /><span>{children}</span></div>;
}

function Loading({ children }) {
  return <div className="intelligence-loading"><SpinnerGap />{children}</div>;
}

function EndpointNotice({ noun }) {
  return <div className="intelligence-compatibility" role="status">
    <WarningCircle />
    <span>当前 API 服务尚未加载{noun}分页接口，正在显示总览预览。重启后端服务后可翻页和查看详情。</span>
  </div>;
}

function Pagination({ page, pageSize, total, hasNext, loading, onNavigate }) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return <footer className="selection-pagination">
    <span>共 {total} 条 · 第 {page}/{totalPages} 页</span>
    <label>每页
      <select
        value={pageSize}
        onChange={(event) => onNavigate("overview", {
          page: 1,
          pageSize: Number(event.target.value),
        })}
      >
        {[10, 20, 50].map((size) => <option value={size} key={size}>{size} 条</option>)}
      </select>
    </label>
    <button
      type="button"
      aria-label="上一页"
      disabled={loading || page <= 1}
      onClick={() => onNavigate("overview", { page: page - 1 })}
    ><CaretLeft /></button>
    <button
      type="button"
      aria-label="下一页"
      disabled={loading || !hasNext}
      onClick={() => onNavigate("overview", { page: page + 1 })}
    ><CaretRight /></button>
  </footer>;
}

function DetailHeader({ noun, page, pageSize, onNavigate }) {
  return <header className="evidence-detail-header">
    <button type="button" onClick={() => onNavigate("overview")}>
      <ArrowLeft />返回{noun}列表
    </button>
    <span>返回后保留第 {page} 页和每页 {pageSize} 条</span>
  </header>;
}

function ValuePanel({ title, value }) {
  const entries = value && typeof value === "object" && !Array.isArray(value)
    ? Object.entries(value)
    : [];
  return <section className="change-value-panel">
    <strong>{title}</strong>
    {entries.length ? <dl>
      {entries.map(([key, item]) => <div key={key}>
        <dt>{valueLabels[key] || key}</dt>
        <dd>{item == null || item === "" ? "—" : Array.isArray(item) ? item.join("、") : String(item)}</dd>
      </div>)}
    </dl> : <p>{value == null ? "无历史值" : String(value)}</p>}
  </section>;
}

export function CompetitorEvidenceView({
  view,
  page,
  pageSize,
  pageData,
  loading,
  error,
  endpointUnavailable,
  selectedAlert,
  overview,
  onNavigate,
}) {
  const items = pageData?.items || overview.recent_alerts || [];
  const total = pageData?.total ?? overview.alert_total ?? items.length;

  if (view === "detail") {
    return <section className="evidence-detail">
      <DetailHeader noun="动态" page={page} pageSize={pageSize} onNavigate={onNavigate} />
      {error && <div className="intelligence-error"><WarningCircle />{error}</div>}
      {loading ? <Loading>正在读取竞品动态详情…</Loading> : selectedAlert && <>
        <div className="evidence-detail-title">
          <span>{selectedAlert.change_label || changeLabels[selectedAlert.change_type] || "竞品动态"}</span>
          <strong>{selectedAlert.title || selectedAlert.asin}</strong>
          <p>{selectedAlert.summary}</p>
        </div>
        <dl className="evidence-metadata">
          <div><dt>ASIN / 商品编号</dt><dd>{selectedAlert.asin}</dd></div>
          <div><dt>市场</dt><dd>{selectedAlert.platform} · {selectedAlert.market_country}</dd></div>
          <div><dt>发现时间</dt><dd>{dateTimeText(selectedAlert.detected_at)}</dd></div>
          <div><dt>读取状态</dt><dd>{selectedAlert.is_read ? "已读" : "未读"}</dd></div>
        </dl>
        <div className="change-comparison">
          <ValuePanel title="变更前" value={selectedAlert.before_value} />
          <ValuePanel title="变更后" value={selectedAlert.after_value} />
        </div>
        {selectedAlert.latest_snapshot && <ValuePanel title="当前最新快照" value={selectedAlert.latest_snapshot} />}
      </>}
    </section>;
  }

  return <div className="capability-summary">
    <div className="intelligence-kpis">
      <span><small>监控目标</small><strong>{overview.watch_count || 0}</strong></span>
      <span><small>持续监控</small><strong>{overview.active_watch_count || 0}</strong></span>
      <span><small>累计动态</small><strong>{total}</strong></span>
    </div>
    {endpointUnavailable && <EndpointNotice noun="竞品动态" />}
    {error && !endpointUnavailable && <div className="intelligence-error"><WarningCircle />{error}</div>}
    {loading && !pageData && !items.length ? <Loading>正在读取竞品动态…</Loading> : items.length ? <>
      <div className="compact-alert-list evidence-alert-list">
        {items.map((item) => <article key={item.alert_id}>
          <i className={item.severity} />
          <span>
            <small>{item.change_label || changeLabels[item.change_type] || "竞品动态"} · {dateTimeText(item.detected_at)}</small>
            <strong>{item.summary}</strong>
            {item.title && <small>{item.title} · {item.asin}</small>}
          </span>
          {!endpointUnavailable && <button
            type="button"
            className="evidence-open-button"
            onClick={() => onNavigate("detail", { itemId: item.alert_id })}
          >查看详情 <ArrowRight /></button>}
        </article>)}
      </div>
      {!endpointUnavailable && <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        hasNext={Boolean(pageData?.has_next)}
        loading={loading}
        onNavigate={onNavigate}
      />}
    </> : <Empty>尚无价格、Listing、促销或上新变更记录。</Empty>}
  </div>;
}

function ClusterSummary({ item, onOpen }) {
  const negative = Number(item.sentiment_distribution?.negative || 0);
  const mixed = Number(item.sentiment_distribution?.mixed || 0);
  return <article>
    <header>
      <span><small>评论需求主题</small><strong>{item.name}</strong></span>
      <b>{scoreText(item.importance_score)}</b>
    </header>
    <p>{item.summary}</p>
    <dl>
      <div><dt>覆盖评论</dt><dd>{item.review_count ?? "—"}</dd></div>
      <div><dt>涉及商品</dt><dd>{item.listing_count ?? "—"}</dd></div>
      <div><dt>负向/混合</dt><dd>{negative + mixed}</dd></div>
    </dl>
    <small>提及率 {percentText(item.mention_rate)} · 聚类置信度 {percentText(item.cluster_confidence)}</small>
    {onOpen && <button type="button" className="evidence-open-button" onClick={onOpen}>查看原文证据 <ArrowRight /></button>}
  </article>;
}

export function ReviewEvidenceView({
  view,
  page,
  pageSize,
  pageData,
  loading,
  error,
  endpointUnavailable,
  selectedCluster,
  overview,
  onNavigate,
}) {
  const items = pageData?.items || overview.clusters || [];
  const total = pageData?.total ?? overview.cluster_total ?? items.length;

  if (view === "detail") {
    return <section className="evidence-detail">
      <DetailHeader noun="主题" page={page} pageSize={pageSize} onNavigate={onNavigate} />
      {error && <div className="intelligence-error"><WarningCircle />{error}</div>}
      {loading ? <Loading>正在读取评论主题详情…</Loading> : selectedCluster && <>
        <div className="evidence-detail-title">
          <span>评论需求主题 · {selectedCluster.taxonomy_code}</span>
          <strong>{selectedCluster.name}</strong>
          <p>{selectedCluster.summary}</p>
        </div>
        <dl className="evidence-metadata">
          <div><dt>重要度</dt><dd>{scoreText(selectedCluster.importance_score)}</dd></div>
          <div><dt>覆盖评论</dt><dd>{selectedCluster.review_count}</dd></div>
          <div><dt>涉及商品</dt><dd>{selectedCluster.listing_count}</dd></div>
          <div><dt>聚类置信度</dt><dd>{percentText(selectedCluster.cluster_confidence)}</dd></div>
        </dl>
        <div className="sentiment-breakdown">
          {Object.entries(selectedCluster.sentiment_distribution || {}).map(([key, value]) =>
            <span key={key}><small>{sentimentLabels[key] || key}</small><strong>{value}</strong></span>)}
        </div>
        <section className="review-evidence-section">
          <header><strong>评论原文证据</strong><span>{selectedCluster.evidence?.length || 0} 条</span></header>
          <div>
            {(selectedCluster.evidence || []).map((item) => <article key={item.aspect_id}>
              <blockquote>{item.evidence_quote}</blockquote>
              <dl>
                <div><dt>情绪</dt><dd>{sentimentLabels[item.sentiment] || item.sentiment}</dd></div>
                <div><dt>评分</dt><dd>{item.rating == null ? "—" : `${item.rating}/5`}</dd></div>
                <div><dt>提取置信度</dt><dd>{percentText(item.extraction_confidence)}</dd></div>
              </dl>
              <small>{item.listing_title} · {item.platform_listing_id}{item.is_representative ? " · 代表证据" : ""}</small>
            </article>)}
            {!selectedCluster.evidence?.length && <Empty>该主题暂未保留可展示的原文证据。</Empty>}
          </div>
        </section>
      </>}
    </section>;
  }

  return <div className="capability-summary">
    <div className="intelligence-kpis">
      <span><small>有效评论</small><strong>{overview.total || 0}</strong></span>
      <span><small>负向评论</small><strong>{overview.negative || 0}</strong></span>
      <span><small>需求主题</small><strong>{total}</strong></span>
    </div>
    {endpointUnavailable && <EndpointNotice noun="评论主题" />}
    {error && !endpointUnavailable && <div className="intelligence-error"><WarningCircle />{error}</div>}
    {loading && !pageData && !items.length ? <Loading>正在读取评论需求主题…</Loading> : items.length ? <>
      <div className="cluster-grid evidence-cluster-grid">
        {items.map((item) => <ClusterSummary
          key={item.cluster_id}
          item={item}
          onOpen={endpointUnavailable ? null : () => onNavigate("detail", { itemId: item.cluster_id })}
        />)}
      </div>
      {!endpointUnavailable && <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        hasNext={Boolean(pageData?.has_next)}
        loading={loading}
        onNavigate={onNavigate}
      />}
    </> : <Empty>已有评论可浏览；完成 AI 分析后可查看带原文证据区间的需求主题。</Empty>}
  </div>;
}
