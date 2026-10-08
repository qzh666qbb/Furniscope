import { useEffect, useState } from "react";
import {
  ArrowClockwise,
  Buildings,
  CaretDown,
  ClipboardText,
  FloppyDisk,
  ShieldCheck,
  SlidersHorizontal,
  TrendUp,
} from "@phosphor-icons/react";
import { api } from "./api.js";
import { saveJson } from "./EnterpriseTraining.jsx";
import { OpportunityOutcomes } from "./OpportunityOutcomes.jsx";
import "./enterprise-workflows.css";

const weightNames = { demand_heat: "需求热度", demand_growth: "需求增长", unmet_need: "未满足需求", competition_space: "竞争空间", profit_space: "利润空间代理" };
const statusNames = { accepted: "采纳", rejected: "拒绝", pending_validation: "待验证" };
const gateNames = { pass: "已知条件满足", blocked: "条件不满足，暂缓", unknown: "条件待确认" };
const factorNames = { category: "产品品类", export_market: "出口市场", capability: "具体能力", manufacturing_requirements: "制造要求", unit_cost: "单位成本", factory_price: "出厂价", lead_time: "交期", moq: "MOQ" };
const profileKeys = ["business_model", "primary_categories", "export_markets", "sales_channels", "annual_capacity_note", "constraints"];
const capabilityKeys = ["capability_type", "capability_code", "capability_name", "availability", "min_value", "max_value", "unit", "notes"];
const pick = (object, keys) => Object.fromEntries(keys.filter(key => key in object).map(key => [key, object[key]]));
const split = text => text.split(/[,，、\n]+/).map(value => value.trim()).filter(Boolean);
const profileDefaults = { business_model: [], primary_categories: [], export_markets: [], sales_channels: [], annual_capacity_note: "", constraints: [] };

export function EnterpriseStrategy() {
  const [policy, setPolicy] = useState(null), [templates, setTemplates] = useState({});
  const [catalog, setCatalog] = useState([]);
  const [profile, setProfile] = useState(null), [capabilities, setCapabilities] = useState([]);
  const [selectedCapability, setSelectedCapability] = useState("0");
  const [preferenceOpen, setPreferenceOpen] = useState(true);
  const [strategyTab, setStrategyTab] = useState("ranking");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const load = async () => {
    const [strategy, facts, vocabulary] = await Promise.all([api("/api/v1/enterprise/opportunity-policy"), api("/api/v1/enterprise/profile"), api("/api/v1/enterprise/fact-vocabulary")]);
    setCatalog(vocabulary.groups.flatMap(group => group.options.map(option => [group.capability_type, option.code, option.name])));
    setPolicy(strategy.current); setTemplates(strategy.templates);
    setProfile({ ...profileDefaults, ...pick(facts.profile || {}, profileKeys) });
    setCapabilities((facts.capabilities || []).map(item => pick(item, capabilityKeys)));
  };
  useEffect(() => { load().catch(reason => setError(reason.message)); }, []);
  const perform = async action => {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (reason) { setError(reason.message); } finally { setBusy(false); }
  };
  const savePolicy = () => perform(async () => {
    const saved = await api("/api/v1/enterprise/opportunity-policy", { method: "PUT", body: JSON.stringify({
      ...pick(policy, ["name", "objective", "weights", "fit_strength", "required_capabilities"]), expected_version: policy.version,
    }) });
    setPolicy(saved); setNotice("策略已保存，仅新建分析使用此版本；已有任务保持原策略。");
  });
  const saveProfile = () => perform(async () => {
    await api("/api/v1/enterprise/profile", { method: "PUT", body: JSON.stringify({ profile, capabilities }) });
    setNotice("企业事实已确认保存，新建分析时生效。产品尺寸、成本等事实请在产品中心确认。");
  });
  const saveRequirements = () => perform(async () => {
    const saved = await api("/api/v1/enterprise/opportunity-policy", { method: "PUT", body: JSON.stringify({
      ...pick(policy, ["name", "objective", "weights", "fit_strength", "required_capabilities"]), expected_version: policy.version,
    }) });
    await api("/api/v1/enterprise/profile", { method: "PUT", body: JSON.stringify({ profile, capabilities }) });
    setPolicy(saved);
    setNotice("必要条件已保存，新建分析将按能力、成本、交期与起订量条件执行准入判断。");
  });
  const exportFeedback = () => perform(async () => {
    const items = []; let cursor = 0, page;
    do { page = await api(`/api/v1/enterprise/opportunity-feedback/export?after_id=${cursor}&limit=1000`); items.push(...page.items); cursor = page.next_after_id; } while (cursor != null);
    saveJson({ format_version: page.format_version, label_semantics: page.label_semantics, items }, "opportunity-feedback.json");
    setNotice(`已导出 ${items.length} 条处理记录。采纳不等于已产生业务收益。`);
  });
  const exportRanking = () => perform(async () => {
    const groups = []; let cursor = 0, asOf, page;
    do {
      const params = new URLSearchParams({ after_task_id: cursor, limit: 50 });
      if (asOf) params.set("as_of", asOf);
      page = await api(`/api/v1/enterprise/opportunity-ranking/export?${params}`);
      asOf = page.as_of; groups.push(...page.groups); cursor = page.next_after_task_id;
    } while (cursor != null);
    saveJson({ ...page, groups, next_after_task_id: null }, "opportunity-ranking.json");
    setNotice(`已导出 ${groups.length} 个分析任务的完整候选及最新标签；未标注、历史缺特征和回溯实施会单独标识。`);
  });
  const weightTotal = policy ? Object.values(policy.weights).reduce((a, b) => a + b, 0) : 0;
  const strategyInvalid = !policy || Math.abs(weightTotal - 1) > 0.000001 || !policy.name.trim();
  return <section className="enterprise-flow enterprise-strategy">
    <header className="enterprise-strategy-header">
      <div><span>BUSINESS PREFERENCES</span><h3>经营决策配置</h3><p>市场机会先按可解释权重排序，再由企业事实和必要条件判断是否可执行。</p></div>
    </header>
    {error && <div role="alert" className="enterprise-error">{error}<button disabled={busy} onClick={() => perform(load)}>重新读取配置</button></div>}
    {notice && <p role="status" className="enterprise-notice">{notice}</p>}
    {!policy ? <p>正在读取经营策略…</p> : <>
      <nav className="enterprise-strategy-tabs" aria-label="经营决策配置分类" role="tablist">
        {[
          ["ranking", SlidersHorizontal, "排序策略"],
          ["facts", Buildings, "企业事实"],
          ["requirements", ShieldCheck, "准入条件"],
          ["version", ClipboardText, "版本与导出"],
        ].map(([key, Icon, label]) => <button
          type="button"
          role="tab"
          aria-selected={strategyTab === key}
          className={strategyTab === key ? "active" : ""}
          key={key}
          onClick={() => setStrategyTab(key)}
        ><Icon />{label}</button>)}
      </nav>

      {strategyTab === "ranking" && <>
      <section className="enterprise-strategy-templates">
        <div><strong>策略模板</strong><small>选择后仍可调整单项权重</small></div>
        <fieldset disabled={busy} className="enterprise-template-row">{Object.entries(templates).map(([key, template]) => <button key={key} className={policy.objective === key ? "selected" : ""} onClick={() => setPolicy({ ...policy, ...template, objective: key })}><strong>{template.name}</strong><small>{Object.entries(template.weights).map(([field, value]) => `${weightNames[field]} ${Math.round(value * 100)}%`).join(" · ")}</small></button>)}</fieldset>
      </section>

      <details
        className="enterprise-strategy-panel enterprise-preference-panel"
        open={preferenceOpen}
        onToggle={(event) => setPreferenceOpen(event.currentTarget.open)}
      >
        <summary>
          <i><SlidersHorizontal /></i>
          <span><strong>调整业务偏好</strong><small>当前策略版本 {policy.version}</small></span>
          <em className={Math.abs(weightTotal - 1) <= 0.000001 ? "valid" : "invalid"}>权重 {Math.round(weightTotal * 100)}%</em>
          <CaretDown />
        </summary>
        <div className="enterprise-panel-content">
          <fieldset disabled={busy} className="enterprise-fields enterprise-weight-fields">
            <label>策略名称<input value={policy.name} maxLength={80} onChange={event => setPolicy({ ...policy, name: event.target.value })}/></label>
            {Object.entries(weightNames).map(([key, label]) => <label key={key}>{label} %<input type="number" min="0" max="100" step="1" value={Number((policy.weights[key] * 100).toFixed(4))} onChange={event => setPolicy({ ...policy, objective: "custom", weights: { ...policy.weights, [key]: Number(event.target.value) / 100 } })}/></label>)}
            <label>企业适配影响强度 %<input type="number" min="0" max="100" value={Math.round(policy.fit_strength * 100)} onChange={event => setPolicy({ ...policy, objective: "custom", fit_strength: Number(event.target.value) / 100 })}/></label>
          </fieldset>
          <p className="enterprise-panel-hint">五项权重合计须为 100%。缺失市场因子按可用权重重新计算，资料缺失会标记为待确认。</p>
          <footer className="enterprise-save-bar">
            <span><strong>保存后创建新策略版本</strong><small>仅影响后续新建分析，已有任务保持原判断口径。</small></span>
            <button className="primary" disabled={busy || strategyInvalid} onClick={savePolicy}><FloppyDisk />{busy ? "保存中…" : "保存经营策略"}</button>
          </footer>
        </div>
      </details>
      </>}

      {profile && strategyTab === "facts" && <details className="enterprise-strategy-panel enterprise-facts" open>
        <summary>
          <i><Buildings /></i>
          <span><strong>确认企业事实</strong><small>经营范围与已核验能力</small></span>
          <em>{capabilities.filter(item => item.availability !== "unknown").length} 项已确认</em>
          <CaretDown />
        </summary>
        <div className="enterprise-panel-content">
          <p className="enterprise-panel-hint">只填写有依据的信息。“确认不具备”与“待确认”会产生不同的适配判断。</p>
          <fieldset disabled={busy} className="enterprise-fields">{[["business_model", "经营模式", "OEM, ODM"], ["primary_categories", "产品品类代码", "sofa, chair"], ["export_markets", "出口市场代码", "US, DE"], ["sales_channels", "销售渠道", "Amazon"]].map(([key, label, placeholder]) => <label key={key}>{label}<input placeholder={placeholder} value={(profile[key] || []).join(",")} onChange={event => setProfile({ ...profile, [key]: event.target.value.split(",") })} onBlur={() => setProfile(current => ({ ...current, [key]: split((current[key] || []).join(",")) }))}/></label>)}</fieldset>
          <div className="enterprise-capability-heading"><strong>企业能力清单</strong><small>确认状态作为机会适配证据</small></div>
          <div className="enterprise-capabilities">{capabilities.map((item, index) => <div key={`${item.capability_type}/${item.capability_code}`}>
            <strong>{item.capability_name}</strong><select aria-label={`${item.capability_name}能力`} disabled={busy} value={item.availability} onChange={event => setCapabilities(values => values.map((value, i) => i === index ? { ...value, availability: event.target.value } : value))}><option value="yes">确认具备</option><option value="no">确认不具备</option><option value="unknown">待确认</option></select>
          </div>)}</div>
          <div className="enterprise-inline enterprise-add-capability"><select aria-label="新增企业能力" value={selectedCapability} disabled={busy} onChange={event => setSelectedCapability(event.target.value)}>{catalog.map(([, , name], index) => <option key={name} value={index}>{name}</option>)}</select><button disabled={busy || !catalog.length} onClick={() => { const selected = catalog[Number(selectedCapability)]; if (!selected) return; const [capability_type, capability_code, capability_name] = selected; if (!capabilities.some(item => item.capability_type === capability_type && item.capability_code === capability_code)) setCapabilities([...capabilities, { capability_type, capability_code, capability_name, availability: "unknown" }]); }}>添加能力事实</button></div>
          <footer className="enterprise-save-bar">
            <span><strong>事实与判断分开保存</strong><small>产品尺寸、成本等单品事实仍在产品中心维护。</small></span>
            <button className="primary" disabled={busy} onClick={saveProfile}><FloppyDisk />{busy ? "保存中…" : "确认并保存企业事实"}</button>
          </footer>
        </div>
      </details>}

      {profile && strategyTab === "requirements" && <details className="enterprise-strategy-panel enterprise-requirements" open>
        <summary>
          <i><ShieldCheck /></i>
          <span><strong>必要条件</strong><small>不满足时阻止机会进入优先队列</small></span>
          <em>{(policy.required_capabilities || []).length + (profile.constraints || []).length} 项条件</em>
          <CaretDown />
        </summary>
        <div className="enterprise-panel-content">
          <div className="enterprise-capability-heading"><strong>必要能力</strong><small>仅勾选所有机会都必须具备的能力</small></div>
          <div className="enterprise-requirement-list">
            {capabilities.map(item => <label className="enterprise-checkbox" key={`${item.capability_type}/${item.capability_code}`}>
              <input type="checkbox" disabled={busy} checked={(policy.required_capabilities || []).some(value => value.capability_type === item.capability_type && value.capability_code === item.capability_code && !value.taxonomy_code)} onChange={event => setPolicy({ ...policy, required_capabilities: [
                ...(policy.required_capabilities || []).filter(value => !(value.capability_type === item.capability_type && value.capability_code === item.capability_code && !value.taxonomy_code)),
                ...(event.target.checked ? [{ capability_type: item.capability_type, capability_code: item.capability_code }] : []),
              ] })}/>
              <span><strong>{item.capability_name}</strong><small>{item.availability === "yes" ? "企业已确认具备" : item.availability === "no" ? "企业已确认不具备" : "企业事实待确认"}</small></span>
            </label>)}
            {!capabilities.length && <p className="enterprise-panel-hint">请先在“确认企业事实”中添加能力，再设置必要能力。</p>}
          </div>
          <div className="enterprise-capability-heading"><strong>成本、交期与起订量</strong><small>数值仅与同单位、已确认的产品事实比较</small></div>
          <div className="enterprise-constraint-list">
            {(profile.constraints || []).map((constraint, index) => <fieldset disabled={busy} className="enterprise-fields enterprise-constraint-row" key={index}>
              {[
                ["constraint_type", "条件", [["unit_cost", "单位成本"], ["lead_time", "交期"], ["moq", "MOQ"]]],
                ["operator", "比较", [["lte", "不高于"], ["gte", "不低于"], ["eq", "等于"]]],
                ["hardness", "强度", [["hard", "必须满足"], ["soft", "偏好"]]],
              ].map(([key, label, options]) => <label key={key}>{label}<select value={constraint[key]} onChange={event => setProfile({ ...profile, constraints: profile.constraints.map((item, i) => i === index ? { ...item, [key]: event.target.value } : item) })}>{!options.some(([value]) => value === constraint[key]) && <option value={constraint[key]}>{constraint[key]}（已有条件）</option>}{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>)}
              <label>数值<input type="number" min="0" value={constraint.value} onChange={event => setProfile({ ...profile, constraints: profile.constraints.map((item, i) => i === index ? { ...item, value: event.target.value === "" ? "" : Number(event.target.value) } : item) })}/></label>
              <label>单位<input value={constraint.unit || ""} placeholder="例如 USD" onChange={event => setProfile({ ...profile, constraints: profile.constraints.map((item, i) => i === index ? { ...item, unit: event.target.value } : item) })}/></label>
              <button onClick={() => setProfile({ ...profile, constraints: profile.constraints.filter((_, i) => i !== index) })}>移除此条件</button>
            </fieldset>)}
          </div>
          <button className="enterprise-add-requirement" disabled={busy} onClick={() => setProfile({ ...profile, constraints: [...(profile.constraints || []), { constraint_type: "unit_cost", operator: "lte", value: 0, unit: "USD", hardness: "hard" }] })}>增加业务条件</button>
          <footer className="enterprise-save-bar">
            <span><strong>必要条件优先于市场得分</strong><small>硬条件不满足时，高市场分也不会覆盖准入判断。</small></span>
            <button className="primary" disabled={busy || strategyInvalid} onClick={saveRequirements}><FloppyDisk />{busy ? "保存中…" : "保存必要条件"}</button>
          </footer>
        </div>
      </details>}

      {strategyTab === "version" && <section className="enterprise-version-panel" role="tabpanel">
        <div>
          <span>当前策略版本</span>
          <strong>v{policy.version}</strong>
          <small>{policy.name}</small>
        </div>
        <p>导出文件用于离线评测和复盘，处理记录与排序快照保持各自的版本语义。</p>
        <div className="enterprise-export-actions">
          <button disabled={busy} onClick={exportFeedback}><ClipboardText />导出反馈数据</button>
          <button disabled={busy} onClick={exportRanking}><TrendUp />导出排序评测数据</button>
        </div>
      </section>}
    </>}
  </section>;
}

export function OpportunityDecision({ item }) {
  const [feedback, setFeedback] = useState(item.feedback), [history, setHistory] = useState(null);
  const [status, setStatus] = useState(item.feedback?.status || "pending_validation"), [reason, setReason] = useState(item.feedback?.reason || "");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [saved, setSaved] = useState(false);
  const [outcomeOpen, setOutcomeOpen] = useState(false);
  const endpoint = `/api/v1/enterprise/opportunities/${item.opportunity_id}/feedback`;
  const load = async () => {
    const value = await api(endpoint); setHistory(value.history); setFeedback(value.current);
    setStatus(value.current?.status || "pending_validation"); setReason(value.current?.reason || "");
  };
  const gate = (Array.isArray(item.manufacturing_fit) ? item.manufacturing_fit[0] : item.manufacturing_fit) || {};
  const save = async () => {
    setBusy(true); setError(""); setSaved(false);
    try {
      const value = await api(endpoint, { method: "PUT", body: JSON.stringify({ expected_revision: feedback?.revision || 0, status, reason }) });
      setFeedback(value.current); setHistory(value.history); setSaved(true);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return <div className="enterprise-flow opportunity-decision">
    <div className={`opportunity-gate-band ${gate.status || "unknown"}`}>
      <i><ShieldCheck /></i>
      <span><small>企业条件判断</small><strong>{gateNames[gate.status] || "历史结果未记录条件检查"}</strong></span>
      <em>事实置信度 {Math.round((item.enterprise_fit_confidence || 0) * 100)}%</em>
      <em>策略 v{item.policy_snapshot?.version ?? "—"}</em>
    </div>
    <details className="opportunity-decision-panel">
      <summary>
        <i><ClipboardText /></i>
        <span><strong>查看判断依据与处理结果</strong><small>核对评分依据，并记录本次业务判断</small></span>
        <em className={`decision-status ${feedback?.status || status}`}>{feedback ? statusNames[feedback.status] : "尚未处理"}</em>
        <CaretDown />
      </summary>
      <div className="opportunity-decision-body">
        <section className="opportunity-evidence-panel">
          <header><span>判断依据</span><small>固定于本次分析版本</small></header>
          <div className="opportunity-score-strip">
            <span><small>市场原分</small><strong>{item.market_score ?? "—"}</strong></span>
            <b>→</b>
            <span><small>企业修正分</small><strong>{item.adjusted_score ?? "—"}</strong></span>
          </div>
          {(gate.signals || []).length > 0 ? <ul className="opportunity-signal-list">{gate.signals.map((signal, index) => <li key={index} className={signal.status || "unknown"}>
            <span><strong>{factorNames[signal.factor] || signal.factor}</strong><small>{signal.required || "未指定要求"}</small></span>
            <em>{gateNames[signal.status] || signal.status}</em>
            <p>{signal.reason}{signal.limit != null ? `（${signal.value ?? "未知"} / 限定 ${signal.limit} ${signal.unit || ""}）` : ""}</p>
          </li>)}</ul> : <p className="opportunity-empty-evidence">本次历史结果没有记录条件明细。</p>}
          <p className="opportunity-score-note">利润空间为代理指标；未校准权重不代表真实成交概率。</p>
        </section>
        <section className="opportunity-action-panel">
          <header><span>处理本次机会</span><small>保存后保留历史版本</small></header>
          {error && <p role="alert" className="enterprise-error">{error}</p>}
          {saved && <p role="status" className="enterprise-notice">处理结果已保存，历史版本保留。</p>}
          <div className="opportunity-action-row">
            <label>处理结果<select disabled={busy} value={status} onChange={event => { setStatus(event.target.value); setSaved(false); }}>{Object.entries(statusNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            <button className="opportunity-history-refresh" title="读取最新处理历史" disabled={busy} onClick={async () => { try { await load(); setError(""); } catch (err) { setError(err.message); } }}><ArrowClockwise />读取历史</button>
          </div>
          <label>处理原因<textarea maxLength={2000} value={reason} disabled={busy} onChange={event => { setReason(event.target.value); setSaved(false); }} placeholder="例如：需求明确，但当前包装无法满足运输要求"/></label>
          <footer>
            <small>{feedback ? `当前为第 ${feedback.revision} 版处理记录` : "尚未形成处理记录"}</small>
            <button className="primary" disabled={busy || !reason.trim()} onClick={save}><FloppyDisk />{busy ? "保存中…" : "保存处理结果"}</button>
          </footer>
          {history && <ol className="opportunity-feedback-history">{history.map(event => <li key={event.revision}><span><strong>{statusNames[event.status]}</strong><time>{new Date(event.created_at).toLocaleString("zh-CN")}</time></span><p>{event.reason}</p></li>)}</ol>}
        </section>
      </div>
    </details>
    <details className="opportunity-outcome-panel" onToggle={event => setOutcomeOpen(event.currentTarget.open)}>
      <summary>
        <i><TrendUp /></i>
        <span><strong>跟踪实施与经营结果</strong><small>仅在采纳后记录实施进度与实际观察</small></span>
        <em>{feedback?.status === "accepted" ? "可开始跟踪" : "等待采纳"}</em>
        <CaretDown />
      </summary>
      {outcomeOpen && <div className="opportunity-outcome-body"><OpportunityOutcomes opportunityId={item.opportunity_id} feedbackRevision={feedback?.revision}/></div>}
    </details>
  </div>;
}
