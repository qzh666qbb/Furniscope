import { useEffect, useMemo, useState } from "react";
import {
  ChartPie,
  Check,
  FilePdf,
  ShieldWarning,
  Target,
  Users,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  CLUSTER_LABEL,
  COMP_TYPE,
  DECISIONS,
  DIMS,
  PRIORITY,
  clusterTitle,
  competitorDiff,
  derivePersonas,
  derivePrice,
  deriveScenes,
  dimScore,
  loadReportDossier,
  marketLabel,
  parseIdsFromHash,
  pct,
  productImage,
  quotesFor as dossierQuotes,
  recCost,
  recSampleSize,
  scoreOf,
  topOpportunity,
} from "./reportDossier.js";
import { ParentPageTab } from "./ParentPageTab.jsx";

function Radar({ values }) {
  const size = 220;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 78;
  const n = DIMS.length;
  const point = (index, ratio) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / n;
    return [cx + Math.cos(angle) * radius * ratio, cy + Math.sin(angle) * radius * ratio];
  };
  const polygon = values.map((value, index) => point(index, Math.min(1, Math.max(0, value / 100))).join(",")).join(" ");
  const rings = [0.25, 0.5, 0.75, 1];
  return (
    <svg className="rdp-radar" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="五维机会评分雷达图">
      {rings.map((ring) => (
        <polygon key={ring} className="radar-ring" points={DIMS.map((_, index) => point(index, ring).join(",")).join(" ")} />
      ))}
      {DIMS.map(([, label], index) => {
        const [x2, y2] = point(index, 1);
        const [lx, ly] = point(index, 1.28);
        return (
          <g key={label}>
            <line x1={cx} y1={cy} x2={x2} y2={y2} className="radar-axis" />
            <text x={lx} y={ly} className="radar-label">{label}</text>
          </g>
        );
      })}
      <polygon className="radar-fill" points={polygon} />
    </svg>
  );
}

function MetaGrid({ items }) {
  return (
    <dl className="rdp-meta">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value || "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

function Traceable({ children, onOpen, className = "" }) {
  return (
    <button type="button" className={`rdp-trace ${className}`} onClick={onOpen}>
      {children}
    </button>
  );
}

function listingQuotes(competitors) {
  return competitors.slice(0, 8).map((row) => ({
    sentiment: COMP_TYPE[row.competitor_type] || "竞品",
    evidence_quote: `${row.title || "未命名商品"} · ${row.brand || "未知品牌"} · ${row.sale_price != null ? `${row.currency || ""} ${row.sale_price}` : "无报价"} · 评分 ${row.rating ?? "—"}`,
    listing_title: row.title,
  }));
}

function ReportBody({ dossier, prefix, setDrawer }) {
  const { report, evidence, recommendations, competitors, clusters, product } = dossier;
  const quotesFor = (filters) => dossierQuotes(dossier, filters);
  const top = topOpportunity(dossier);
  const radarValues = DIMS.map(([key]) => dimScore(top, key));
  const personas = derivePersonas(dossier);
  const scenes = deriveScenes(dossier);
  const priceBand = derivePrice(dossier);
  const attributes = (product?.attributes || []).slice(0, 8);
  const pains = [...clusters].sort((a, b) => Number(b.importance_score || 0) - Number(a.importance_score || 0));
  const riskItems = (() => {
    const items = [];
    const summary = report?.risk_summary;
    if (Array.isArray(summary)) items.push(...summary.map((entry) => (typeof entry === "string" ? entry : entry.message || JSON.stringify(entry))));
    else if (summary?.items) items.push(...summary.items);
    for (const row of report?.pending_validation_items || []) items.push(typeof row === "string" ? row : row.item || row.validation_method);
    for (const row of report?.partial_failures_snapshot || []) items.push(row.message || row.code || "部分分析步骤未完整完成");
    if (report?.data_scope_snapshot?.limitations) items.push(String(report.data_scope_snapshot.limitations));
    return [...new Set(items.filter(Boolean))];
  })();
  const scope = report?.data_scope_snapshot || {};
  const openQuotes = (title, subtitle, quotes, insightView) => setDrawer({ title, subtitle, quotes, insightView, task: report.task_uuid, reportId: report.report_uuid });
  const openPain = (cluster) => openQuotes(
    clusterTitle(cluster),
    `提及 ${cluster.review_count || cluster.aspect_count || 0} 条 · 重要度 ${scoreOf(cluster.importance_score)}`,
    quotesFor({ taxonomy: cluster.taxonomy_code }),
    "reviewAspects",
  );
  const openEvidence = (row) => openQuotes(
    row.claim_category || row.claim_type,
    `${row.evidence_type} #${row.evidence_id}`,
    row.evidence_quote ? [row] : quotesFor({ claimType: row.claim_type, claimId: row.claim_id }),
    "evidence",
  );

  return (
    <div className="rdp-stack-item">
      <header className="report-title">
        <div>
          <h1>{report.title}</h1>
          <p>
            {report.product_sku} {report.product_name} · {marketLabel(report.target_country)} / {report.target_platform} · {new Date(report.created_at).toLocaleString("zh-CN")}
          </p>
        </div>
      </header>
      <section className="conclusion-hero">
        <div className="conclusion-copy">
          <Check />
          <div>
            <small>决策结论</small>
            <Traceable
              className="rdp-trace-block"
              onOpen={() => openQuotes("决策结论溯源", DECISIONS[report.decision_recommendation] || report.decision_recommendation, quotesFor({}), "evidence")}
            >
              <h2>{DECISIONS[report.decision_recommendation] || report.decision_recommendation}</h2>
              <p>{report.executive_summary}</p>
            </Traceable>
          </div>
        </div>
        <div className="report-scores">
          <Traceable onOpen={() => openQuotes("综合机会分", top?.title || "机会评分", quotesFor({ claimType: "opportunity", claimId: top?.opportunity_id }), "opportunities")}>
            综合机会分 <b>{scoreOf(report.overall_opportunity_score)}</b><small>/100</small>
          </Traceable>
          <span>结论置信度 <b>{Math.round(report.overall_confidence * 100)}%</b></span>
        </div>
      </section>

      <section id={`${prefix}m1`} className="report-section">
        <h2>1. 产品与市场基础信息</h2>
        <div className="rdp-hero-split">
          <img src={productImage(report.product_sku)} alt="" onError={(event) => { event.currentTarget.src = "/assets/furniscope-mark.png"; }} />
          <MetaGrid items={[
            ["对应产品", `${report.product_sku} ${report.product_name}`],
            ["目标出海市场", `${marketLabel(report.target_country)} · ${report.target_platform}`],
            ["分析数据集", scope.dataset || scope.source || "任务绑定数据集"],
            ["商品 / 有效评论", `${scope.listing_count ?? "—"} / ${scope.valid_review_count ?? "—"}`],
            ["报告生成时间", new Date(report.created_at).toLocaleString("zh-CN")],
            ["结论整体置信度", pct(report.overall_confidence)],
          ]} />
        </div>
        {attributes.length > 0 && (
          <div className="rdp-attr-grid">
            {attributes.map((item) => (
              <span key={item.attribute_code}>
                <small>{item.attribute_name || item.attribute_code}</small>
                <b>{typeof item.attribute_value === "object" ? JSON.stringify(item.attribute_value) : String(item.attribute_value)}</b>
              </span>
            ))}
          </div>
        )}
      </section>

      <section id={`${prefix}m2`} className="report-section">
        <h2>2. 综合机会评分与五维雷达</h2>
        {top ? (
          <div className="rdp-score-grid">
            <Radar values={radarValues} />
            <div className="rdp-dim-list">
              {DIMS.map(([key, label], index) => (
                <Traceable
                  key={key}
                  className="rdp-dim-trace"
                  onOpen={() => openQuotes(label, top.title, quotesFor({ claimType: "opportunity", claimId: top.opportunity_id }), "opportunities")}
                >
                  <span>{label}</span>
                  <i><b style={{ width: `${radarValues[index]}%` }} /></i>
                  <strong>{scoreOf(radarValues[index])}</strong>
                </Traceable>
              ))}
              <p>雷达图取最高分机会「{top.title}」的五维市场得分。点击分项可查看依据证据。</p>
            </div>
          </div>
        ) : <p>当前报告尚未形成可评分的机会项。</p>}
      </section>

      <section id={`${prefix}m3`} className="report-section">
        <h2>3. 目标用户画像与推荐价格带</h2>
        <div className="rdp-two">
          <article>
            <header><Users /><h3>核心消费人群</h3></header>
            {personas.length ? personas.map((item) => (
              <Traceable
                key={item.label}
                className="rdp-chip"
                onOpen={() => openQuotes(item.label, "人群画像依据", quotesFor({ taxonomy: item.taxonomy }), "reviewAspects")}
              >
                {item.label}{item.count ? ` · ${item.count}` : ""}
              </Traceable>
            )) : <p>评论尚未抽出稳定人群标签，建议结合痛点原文人工复核。</p>}
            <header className="follow"><Target /><h3>适用家居场景</h3></header>
            {scenes.length ? scenes.map((item) => (
              <Traceable
                key={item.label}
                className="rdp-chip"
                onOpen={() => openQuotes(item.label, "场景依据", quotesFor({}), "reviewAspects")}
              >
                {item.label}
              </Traceable>
            )) : <p>场景信号不足。</p>}
          </article>
          <article>
            <header><ChartPie /><h3>建议零售价区间</h3></header>
            <Traceable
              className="rdp-price rdp-trace-block"
              onOpen={() => openQuotes("价格带依据", "竞品售价与工厂成本约束", listingQuotes(competitors), "competitors")}
            >
              <span>竞品样本 <b>{priceBand.sample}</b></span>
              <span>市场观测 <b>{priceBand.min && priceBand.max ? `${priceBand.currency} ${Math.round(priceBand.min)}–${Math.round(priceBand.max)}` : "—"}</b></span>
              <span>建议零售 <b>{priceBand.suggest ? `${priceBand.currency} ${priceBand.suggest.low}–${priceBand.suggest.high}` : "待补充成本后测算"}</b></span>
              {priceBand.costMid ? <span>工厂成本中枢 <b>{priceBand.currency} {Math.round(priceBand.costMid)}</b></span> : null}
            </Traceable>
            <p>定价取竞品中位价，并以产品出厂价的 2.1–2.9 倍作为毛利地板与天花板；落地前需用工厂实际 BOM 复核。</p>
          </article>
        </div>
      </section>

      <section id={`${prefix}m4`} className="report-section">
        <h2>4. 用户核心痛点（点击可溯源原文）</h2>
        {pains.length ? (
          <div className="rdp-pain-list">
            {pains.map((cluster, index) => (
              <button key={cluster.cluster_id || cluster.cluster_code} type="button" onClick={() => openPain(cluster)}>
                <b>{String(index + 1).padStart(2, "0")}</b>
                <span>
                  <strong>{clusterTitle(cluster)}</strong>
                  <small>{cluster.summary || "高频未被满足的需求簇"}</small>
                </span>
                <em>{cluster.review_count || cluster.aspect_count || 0} 条</em>
                <i>溯源</i>
              </button>
            ))}
          </div>
        ) : <p>暂无需求聚类。可到市场洞察查看原始评论。</p>}
      </section>

      <section id={`${prefix}m5`} className="report-section">
        <h2>5. 竞品矩阵分析</h2>
        {competitors.length ? (
          <div className="report-table">
            <table>
              <thead>
                <tr>
                  <th>类型</th><th>商品</th><th>品牌</th><th>售价</th><th>评分</th><th>评论数</th><th>匹配分</th><th>优势</th><th>差异</th>
                </tr>
              </thead>
              <tbody>
                {competitors.slice(0, 12).map((row) => {
                  const diff = competitorDiff(row);
                  return (
                    <tr key={row.competitor_id}>
                      <td>{COMP_TYPE[row.competitor_type] || row.competitor_type || "竞品"}</td>
                      <td>
                        <Traceable
                          className="rdp-quote-btn"
                          onOpen={() => openQuotes(row.title || "竞品", `${row.brand || ""} · 匹配 ${scoreOf(row.overall_score)}`, listingQuotes([row]), "competitors")}
                        >
                          {row.title || "—"}
                        </Traceable>
                      </td>
                      <td>{row.brand || "—"}</td>
                      <td>{row.sale_price != null ? `${row.currency || ""} ${row.sale_price}` : "—"}</td>
                      <td>{row.rating ?? "—"}</td>
                      <td>{row.review_count ?? "—"}</td>
                      <td>{scoreOf(row.overall_score)}</td>
                      <td>{diff.advantage}</td>
                      <td>{diff.gap}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : <p>当前任务未写入竞品集合。</p>}
        <button className="rdp-link no-print" type="button" onClick={() => { location.hash = "competitor-tracking?tab=prices"; }}>打开市场洞察竞品监测 →</button>
      </section>

      <section id={`${prefix}m6`} className="report-section">
        <h2>6. 产品改进建议清单（工厂可落地）</h2>
        {recommendations.length ? (
          <div className="rdp-rec-list">
            {recommendations.map((row) => (
              <article key={row.recommendation_id}>
                <header>
                  <strong>{row.recommended_action}</strong>
                  <em className={row.priority}>{PRIORITY[row.priority] || row.priority}优先级</em>
                </header>
                <p>{row.problem_statement}</p>
                <footer>
                  <span>依据痛点 · 样本 {recSampleSize(row, clusters)} 条</span>
                  <span>预估成本 {recCost(row, dossier)}</span>
                  <span>置信度 {pct(row.confidence)}</span>
                  <span>风险 {PRIORITY[row.risk_level] || row.risk_level}</span>
                  <button className="no-print" type="button" onClick={() => openQuotes(
                    "建议依据",
                    row.validation_method,
                    quotesFor({ claimType: "recommendation", claimId: row.recommendation_id }),
                    "recommendations",
                  )}>溯源证据</button>
                </footer>
              </article>
            ))}
          </div>
        ) : <p>尚未生成结构化改进建议。</p>}
      </section>

      <section id={`${prefix}m7`} className="report-section">
        <h2>7. 风险提示与置信度说明</h2>
        <div className="rdp-risk">
          <ShieldWarning />
          <div>
            <p>本次结论置信度为 <b>{pct(report.overall_confidence)}</b>。适用范围限于 {marketLabel(report.target_country)} / {report.target_platform} 当前数据集，不能外推到未授权市场或未覆盖的销售渠道。</p>
            <ul>
              {riskItems.length ? riskItems.map((item) => <li key={item}>{item}</li>) : <li>未单独记录额外风险项；量产前仍需打样、成本与认证评审。</li>}
            </ul>
          </div>
        </div>
      </section>

      <section id={`${prefix}m8`} className="report-section">
        <h2>8. 证据目录（全链路溯源）</h2>
        <p className="rdp-evidence-lead">模型调用与证据：{report.model_run?.provider} / {report.model_run?.model_id} · Token {(report.model_run?.input_tokens || 0) + (report.model_run?.output_tokens || 0)} · {report.model_run?.latency_ms}ms · Schema {report.model_run?.schema_valid ? "通过" : "未通过"}。关联证据 {evidence.length} 条；数据范围和限制保存在报告快照中。</p>
        {evidence.length ? (
          <div className="report-table">
            <table>
              <thead>
                <tr>
                  <th>论断</th><th>证据类型</th><th>原文 / 标的</th><th>相关度</th><th>主证据</th>
                </tr>
              </thead>
              <tbody>
                {evidence.slice(0, 40).map((row, index) => (
                  <tr key={`${row.evidence_id}-${index}`}>
                    <td>{row.claim_type}</td>
                    <td>{row.evidence_type}</td>
                    <td>
                      <button type="button" className="rdp-quote-btn" onClick={() => openEvidence(row)}>
                        {row.evidence_quote || row.listing_title || `#${row.evidence_id}`}
                      </button>
                    </td>
                    <td>{pct(row.relevance_score)}</td>
                    <td>{row.is_primary ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p>没有可展示的证据链接。</p>}
        <p className="evidence-note">每一条可点击结论均回链到当前任务租户内的评论观点或竞品 listing，禁止用模型自由发挥替代原始证据。</p>
      </section>
    </div>
  );
}

export function ReportDetailPage({ Sidebar, Topbar }) {
  const [ids, setIds] = useState(() => parseIdsFromHash());
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const printOnLoad = params.get("print") === "1";
  const source = params.get("from");
  const parent = source === "workbench"
    ? { label: "AI 工作台", to: "analysis" }
    : source === "workspace"
      ? { label: "首页", to: "workspace" }
      : { label: "决策报告", to: "report" };
  const [dossiers, setDossiers] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(ids.length));
  const [drawer, setDrawer] = useState(null);

  useEffect(() => {
    const sync = () => setIds(parseIdsFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  useEffect(() => {
    if (!ids.length) {
      setError("缺少报告编号");
      setLoading(false);
      setDossiers([]);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    Promise.all(ids.map((id) => loadReportDossier(id).then((row) => ({ ok: true, row })).catch((reason) => ({ ok: false, id, message: reason.message }))))
      .then((results) => {
        if (cancelled) return;
        const rows = results.filter((item) => item.ok).map((item) => item.row);
        const failed = results.filter((item) => !item.ok);
        setDossiers(rows);
        setError(failed.length ? `${failed.length} 份报告加载失败${rows.length ? "，其余已展开可打印" : ""}` : "");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [ids.join(",")]);

  useEffect(() => {
    if (!dossiers.length || !printOnLoad || loading) return undefined;
    const timer = window.setTimeout(() => window.print(), 600);
    return () => window.clearTimeout(timer);
  }, [dossiers.length, printOnLoad, loading]);

  const first = dossiers[0]?.report;
  const tocPrefix = dossiers.length === 1 ? "" : "r0-";

  return (
    <main className={`workspace decision-report rdp-page ${drawer ? "drawer-open" : ""}`}>
      <Sidebar page="report" />
      <section className="workspace-main">
        <Topbar />
        <div className={`report-layout ${drawer ? "with-evidence" : ""}`}>
          <nav className="report-toc rdp-toc" aria-label="报告目录">
            <h2>{dossiers.length > 1 ? `批量 ${dossiers.length} 份` : "报告目录"}</h2>
            {[
              ["m1", "01 基础信息"],
              ["m2", "02 机会评分"],
              ["m3", "03 用户与定价"],
              ["m4", "04 用户痛点"],
              ["m5", "05 竞品矩阵"],
              ["m6", "06 改进建议"],
              ["m7", "07 风险说明"],
              ["m8", "08 证据目录"],
            ].map(([href, label]) => (
              <button
                type="button"
                key={href}
                onClick={() => document.getElementById(`${tocPrefix}${href}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
              >
                {label}
              </button>
            ))}
          </nav>
          <article className="report-document rdp-document">
            <div className="rdp-toolbar no-print">
              <ParentPageTab className="compact" label={parent.label} current="报告详情" to={parent.to} />
              <div className="rdp-toolbar-actions">
                <button className="report-print-action" onClick={() => window.print()}>
                  <FilePdf />{dossiers.length > 1 ? `导出 ${dossiers.length} 份 PDF` : "导出 PDF"}
                </button>
                <span className="rdp-print-hint">打印对话框中选择「另存为 PDF」</span>
              </div>
            </div>
            {error && <div className="forecast-form-error"><WarningCircle />{error}</div>}
            {loading && <p>正在组装一页式决策报告…</p>}
            {!loading && dossiers.map((dossier, index) => (
              <ReportBody
                key={dossier.report.report_uuid}
                dossier={dossier}
                prefix={dossiers.length > 1 ? `r${index}-` : ""}
                setDrawer={setDrawer}
              />
            ))}
          </article>
          {drawer && (
            <aside className="report-evidence rdp-drawer">
              <header>
                <div>
                  <h2>{drawer.title}</h2>
                  <p>{drawer.subtitle}</p>
                </div>
                <button type="button" onClick={() => setDrawer(null)} aria-label="关闭"><X /></button>
              </header>
              {(drawer.quotes || []).length ? drawer.quotes.map((row, index) => (
                <article key={index}>
                  <strong>{row.sentiment || row.taxonomy_code || "原文证据"}</strong>
                  <p>{row.evidence_quote || "该证据未保存评论文本，请到市场洞察查看原始记录。"}</p>
                  <span>{row.listing_title || row.listing_brand || ""}</span>
                </article>
              )) : <p>没有关联到评论文本。可跳转市场洞察查看完整证据链。</p>}
              {first && (
                <button type="button" className="rdp-link" onClick={() => { location.hash = "insights"; }}>
                  打开市场洞察数据集 →
                </button>
              )}
            </aside>
          )}
        </div>
      </section>
    </main>
  );
}
