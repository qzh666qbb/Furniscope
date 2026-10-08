import { api } from "./api.js";

export const MARKETS = {
  US: "美国", GB: "英国", DE: "德国", FR: "法国", IT: "意大利",
  ES: "西班牙", NL: "荷兰", JP: "日本", AU: "澳大利亚", CA: "加拿大",
};
export const DECISIONS = {
  prioritize_validate: "建议优先验证后推进",
  collect_more_data: "建议先补充样本再决策",
  capability_gap: "存在制造能力缺口，谨慎推进",
  limited_opportunity: "机会有限，建议观望或收缩投入",
};
export const COMP_TYPE = { direct: "直接竞品", benchmark: "标杆竞品", substitute: "替代竞品", similar: "近似竞品" };
export const PRIORITY = { high: "高", medium: "中", low: "低" };
export const CLUSTER_LABEL = {
  comfort: "舒适性", packaging: "包装防护", assembly: "组装体验",
  material: "材质工艺", durability: "耐用性", price: "价格感知",
  size: "尺寸适配", noise: "噪音", smell: "气味",
};
export const DIMS = [
  ["demand_heat_score", "需求热度"],
  ["demand_growth_score", "需求增长"],
  ["unmet_need_score", "未满足程度"],
  ["competition_space_score", "竞争空间"],
  ["profit_space_score", "利润空间"],
];
const PERSONA = {
  elderly: "银发人群", senior: "中老年", family: "家庭用户", couple: "二人家庭",
  office: "居家办公", rental: "租赁/公寓", pet: "宠物家庭", youth: "年轻用户",
};
const SCENE = {
  living_room: "客厅", bedroom: "卧室", office: "书房/办公", outdoor: "户外",
  theater: "影音室", apartment: "小户型", recliner: "躺卧休闲",
};

export const scoreOf = (value) => Math.round(Number(value || 0));
export const pct = (value) => `${Math.round(Number(value || 0) * 100)}%`;
export const marketLabel = (code) => MARKETS[code] || code;
export const productImage = (sku) => (sku ? `/assets/hf-products/${encodeURIComponent(sku)}.webp` : "/assets/furniscope-mark.png");

export function dimScore(row, key) {
  const raw = row?.[key];
  if (raw == null || raw === "") return null;
  const score = Number(raw);
  return Number.isFinite(score) ? score : null;
}

export function clusterTitle(cluster) {
  return CLUSTER_LABEL[(cluster?.taxonomy_code || "").toLowerCase()]
    || cluster?.cluster_name || cluster?.name || cluster?.cluster_code || "需求簇";
}

export function parseIdsFromHash() {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const fromIds = (params.get("ids") || params.get("batch") || "").split(",").map((item) => item.trim()).filter(Boolean);
  const id = params.get("id");
  if (fromIds.length) return fromIds;
  return id ? [id] : [];
}

export async function loadReportDossier(id) {
  const [report, evidence] = await Promise.all([
    api(`/api/v1/reports/${id}`),
    api(`/api/v1/reports/${id}/evidence`),
  ]);
  const task = report.task_uuid;
  const required = async (path, label) => {
    try {
      return await api(path);
    } catch (error) {
      throw new Error(`${label}加载失败：${error.message}`);
    }
  };
  const [opp, rec, cmp, cls, asp, catalog] = await Promise.all([
    required(`/api/v1/analysis-tasks/${task}/opportunities`, "机会项"),
    required(`/api/v1/analysis-tasks/${task}/recommendations`, "建议项"),
    required(`/api/v1/analysis-tasks/${task}/competitors`, "竞品证据"),
    required(`/api/v1/analysis-tasks/${task}/insight-clusters`, "评论聚类"),
    required(`/api/v1/analysis-tasks/${task}/review-aspects`, "评论证据"),
    api(`/api/v1/products/${report.product_id}`).catch(() => null),
  ]);
  return {
    report,
    evidence: evidence || [],
    opportunities: opp.items || [],
    recommendations: rec.items || [],
    competitors: cmp.items || [],
    clusters: cls.items || [],
    aspects: asp.items || [],
    product: catalog,
  };
}

export function quotesFor(dossier, filters = {}) {
  const { evidence, aspects } = dossier;
  const related = evidence.filter((row) => {
    if (filters.claimType && row.claim_type !== filters.claimType) return false;
    if (filters.claimId && Number(row.claim_id) !== Number(filters.claimId)) return false;
    return Boolean(row.evidence_quote);
  });
  if (related.length) return related;
  const byTax = aspects.filter((row) => !filters.taxonomy || row.taxonomy_code === filters.taxonomy);
  return byTax.filter((row) => row.evidence_quote).slice(0, 8).map((row) => ({
    evidence_quote: row.evidence_quote,
    sentiment: row.sentiment,
    listing_title: row.listing_title,
    taxonomy_code: row.taxonomy_code,
    relevance_score: row.extraction_confidence,
  }));
}

export function derivePersonas(dossier) {
  const { aspects, report, clusters } = dossier;
  const counts = {};
  for (const row of aspects) {
    for (const code of row.person_codes || []) counts[code] = (counts[code] || 0) + 1;
  }
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 4);
  if (ranked.length) return ranked.map(([code, count]) => ({ label: PERSONA[code] || code, count }));
  const snapshot = report?.target_user_summary;
  if (Array.isArray(snapshot?.personas)) return snapshot.personas.map((item) => ({ label: item, count: null }));
  return clusters.slice(0, 3).map((cluster) => ({
    label: `${clusterTitle(cluster)}关注人群`,
    count: cluster.review_count || null,
    taxonomy: cluster.taxonomy_code,
  }));
}

export function deriveScenes(dossier) {
  const counts = {};
  for (const row of dossier.aspects) {
    for (const code of row.scenario_codes || []) counts[code] = (counts[code] || 0) + 1;
  }
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 4);
  if (ranked.length) return ranked.map(([code, count]) => ({ label: SCENE[code] || code, count }));
  return [{ label: "待识别使用场景", count: null }];
}

export function derivePrice(dossier) {
  const summary = dossier.report?.price_summary;
  const frozen = Boolean(
    summary?.calculation_version
    && summary?.input_snapshot
    && summary?.evidence_source,
  );
  if (!frozen) {
    return {
      status: "unverified",
      min: null,
      max: null,
      sample: 0,
      currency: null,
      suggest: null,
      calculationVersion: null,
    };
  }
  const hasRecommendation = summary.recommended_low != null && summary.recommended_high != null;
  return {
    status: summary.status || "frozen",
    min: summary.market_low ?? null,
    max: summary.market_high ?? null,
    mid: summary.market_median ?? null,
    sample: Number(summary.sample_size || 0),
    currency: summary.currency || null,
    suggest: hasRecommendation
      ? { low: summary.recommended_low, high: summary.recommended_high }
      : null,
    calculationVersion: summary.calculation_version,
  };
}

export function competitorDiff(row) {
  const reasons = row.match_reasons;
  const texts = [];
  if (Array.isArray(reasons)) {
    for (const item of reasons) texts.push(typeof item === "string" ? item : item?.reason || item?.text || item?.advantage || "");
  } else if (reasons && typeof reasons === "object") {
    texts.push(reasons.advantage || reasons.match || reasons.reason || "");
    texts.push(reasons.difference || reasons.gap || reasons.weakness || "");
  } else if (reasons) texts.push(String(reasons));
  const clean = texts.map((item) => String(item || "").trim()).filter(Boolean);
  return {
    advantage: clean[0] || (row.competitor_type === "benchmark" ? "评分/销量为市场标杆" : "与分析产品同品类可比"),
    gap: clean[1] || clean[0] || "功能、尺寸或面料组合存在差异化空间",
  };
}

export function recSampleSize(row, clusters) {
  const ids = (row.evidence_cluster_ids || []).map(Number);
  const matched = clusters.filter((cluster) => ids.includes(Number(cluster.cluster_id)));
  const sum = matched.reduce((total, cluster) => total + Number(cluster.review_count || cluster.aspect_count || 0), 0);
  return sum || matched.length || ids.length || 0;
}

export function recCost(row) {
  if (row.cost_impact_min != null && row.cost_calculation_version && row.cost_input_snapshot) {
    return `${row.cost_currency || "USD"} ${row.cost_impact_min}–${row.cost_impact_max ?? row.cost_impact_min}`;
  }
  return "待核算";
}

export function topOpportunity(dossier) {
  const primaryId = dossier.report?.version_bundle?.report_contract?.primary_opportunity_id;
  if (primaryId == null) return null;
  return dossier.opportunities.find((row) => Number(row.opportunity_id) === Number(primaryId)) || null;
}
