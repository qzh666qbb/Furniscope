import {
  ArrowLeft,
  ArrowRight,
  CaretLeft,
  CaretRight,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";
import { EnterpriseStrategy, OpportunityDecision } from "./EnterpriseDecision.jsx";

const scoreText = (value) => value == null ? "—" : Number(value).toFixed(0);
const factorNames = { demand_growth: "需求增长", profit_space: "利润空间" };
const recommendationNames = {
  prioritize_validate: "优先验证",
  collect_more_data: "补充证据",
  limited_opportunity: "有限机会",
  capability_gap: "能力条件不满足",
  defer: "暂缓",
};
const opportunityThemeNames = {
  comfort: "坐感与支撑",
  durability: "结构与耐用性",
  assembly: "组装体验",
  dimension: "尺寸适配",
  material: "面料与材质",
  packaging: "包装防护",
  odor: "气味控制",
  appearance: "外观与颜色",
};

function opportunityTitle(item) {
  const source = String(item.title || "");
  const theme = Object.entries(opportunityThemeNames)
    .find(([key]) => source.toLowerCase().includes(key));
  return theme ? `${theme[1]}机会方向` : source;
}

function OpportunitySummary({ item, rank, onOpen, children }) {
  return <article>
    <b>{rank}</b>
    <div>
      <span className="opportunity-kind">候选机会方向</span>
      <strong>{opportunityTitle(item)}</strong>
      <p>{item.description}</p>
      <small>
        {item.primary_cluster_ids?.length ? `关联 ${item.primary_cluster_ids.length} 个评论需求主题 · ` : ""}
        证据置信度 {Math.round(Number(item.confidence || 0) * 100)}% ·
        决策建议：{recommendationNames[item.recommendation_level] || "待复核"}
      </small>
      {!!item.weight_config?.missing_factors?.length && <small className="opportunity-coverage">评分权重覆盖 {Math.round(Number(item.weight_config.coverage || 0) * 100)}% · 待确认：{item.weight_config.missing_factors.map((key) => factorNames[key] || key).join("、")}</small>}
    </div>
    <dl><div><dt>企业修正分</dt><dd>{scoreText(item.adjusted_score ?? item.base_score)}</dd></div><div><dt>市场原分</dt><dd>{scoreText(item.market_score)}</dd></div><div><dt>企业适配</dt><dd>{item.enterprise_fit_score == null ? "未确认" : scoreText(item.enterprise_fit_score)}</dd></div></dl>
    {onOpen && <button type="button" className="opportunity-detail-link" onClick={onOpen}>查看详情 <ArrowRight /></button>}
    {children}
  </article>;
}

export function SelectionWorkspace({
  view,
  datasetId,
  page,
  pageSize,
  pageData,
  loading,
  error,
  endpointUnavailable,
  selectedOpportunity,
  overview,
  onNavigate,
  onStartAnalysis,
}) {
  const opportunities = pageData?.items || overview.opportunities;
  const total = pageData?.total ?? overview.total ?? opportunities.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return <div className="selection-workbench">
    <nav className="capability-view-nav" aria-label="能力视图">
      <button type="button" className={view === "overview" ? "active" : ""} onClick={() => onNavigate("overview")}>洞察概览</button>
      <button type="button" className={view === "workbench" ? "active" : ""} onClick={() => onNavigate("workbench")}>经营配置工作台</button>
    </nav>

    {view === "workbench" && <EnterpriseStrategy />}

    {view === "overview" && <>
      <div className="selection-meaning">
        <strong>这里展示的是机会方向，不是商品或 SKU。</strong>
        <span>每条由评论需求主题和市场信号聚合形成，用于决定优先验证的产品改善方向。</span>
      </div>
      {endpointUnavailable && <div className="intelligence-compatibility" role="status">
        <WarningCircle />
        <span>当前 API 服务尚未加载选品分页接口，正在显示总览预览。重启后端服务后可翻页和查看详情。</span>
      </div>}
      {error && !endpointUnavailable && <div className="intelligence-error"><WarningCircle />{error}</div>}
      {loading && !pageData ? <div className="intelligence-loading"><SpinnerGap />正在读取机会列表…</div> : opportunities.length ? <>
        <div className="selection-list">
          {opportunities.map((item, index) => <OpportunitySummary
            key={item.opportunity_id}
            item={item}
            rank={(page - 1) * pageSize + index + 1}
            onOpen={endpointUnavailable ? null : () => onNavigate("detail", { opportunityId: item.opportunity_id })}
          />)}
        </div>
        {!endpointUnavailable && <footer className="selection-pagination">
          <span>共 {total} 项 · 第 {page}/{totalPages} 页</span>
          <label>每页
            <select value={pageSize} onChange={(event) => onNavigate("overview", { page: 1, pageSize: Number(event.target.value) })}>
              {[10, 20, 50].map((size) => <option value={size} key={size}>{size} 条</option>)}
            </select>
          </label>
          <button type="button" aria-label="上一页" disabled={loading || page <= 1} onClick={() => onNavigate("overview", { page: page - 1 })}><CaretLeft /></button>
          <button type="button" aria-label="下一页" disabled={loading || !pageData?.has_next} onClick={() => onNavigate("overview", { page: page + 1 })}><CaretRight /></button>
        </footer>}
      </> : <section className="selection-evidence-baseline">
        <header>
          <div><strong>当前选品证据基线</strong><p>当前范围尚无已完成的机会排序，先保留可核验的市场事实。</p></div>
          {datasetId && <button type="button" className="intelligence-action" onClick={onStartAnalysis}>启动选品分析 <ArrowRight /></button>}
        </header>
        <div>
          <span><small>分析范围</small><strong>{overview.datasetName || "尚未选择数据集"}</strong></span>
          <span><small>竞品商品</small><strong>{overview.competitorCount || 0}</strong></span>
          <span><small>有效评论</small><strong>{overview.reviewCount || 0}</strong></span>
          <span><small>价格样本</small><strong>{overview.priceCount || 0}</strong></span>
        </div>
      </section>}
    </>}

    {view === "detail" && <section className="selection-detail">
      <header>
        <button type="button" onClick={() => onNavigate("overview")}><ArrowLeft />返回机会列表</button>
        <span>返回后保留第 {page} 页和每页 {pageSize} 条</span>
      </header>
      {error && <div className="intelligence-error"><WarningCircle />{error}</div>}
      {loading ? <div className="intelligence-loading"><SpinnerGap />正在读取机会详情…</div> : selectedOpportunity && <div className="selection-list selection-detail-list">
        <OpportunitySummary item={selectedOpportunity} rank="详情">
          <OpportunityDecision item={selectedOpportunity} />
        </OpportunitySummary>
      </div>}
    </section>}
  </div>;
}
