import { useEffect, useId, useState } from "react";
import { api } from "./api.js";

const states = { planned: "计划中", in_progress: "实施中", completed: "已完成", abandoned: "已放弃" };
const results = { achieved: "达到目标", not_achieved: "未达到目标", inconclusive: "暂不能判断" };
const empty = { status: "planned", implementation_start: "", implementation_end: "", observation_start: "", observation_end: "",
  result_label: "", evidence: "", data_version_uuid: "", source_sku: "", sample_units: "", production_units: "",
  sold_units: "", returned_units: "", revenue: "", cost: "", currency: "USD", basis: "" };
const dateFields = [["implementation_start", "实际实施开始"], ["implementation_end", "实际实施结束"],
  ["observation_start", "观察开始"], ["observation_end", "观察结束"]];
const metricFields = [["sample_units", "打样数量"], ["production_units", "投产数量"],
  ["sold_units", "观察销量"], ["returned_units", "退货数量"]];

export function OpportunityOutcomes({ opportunityId, feedbackRevision }) {
  const [events, setEvents] = useState(null), [adoption, setAdoption] = useState(null), [versions, setVersions] = useState([]);
  const [form, setForm] = useState(empty), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const listId = useId();
  const endpoint = `/api/v1/enterprise/opportunities/${opportunityId}`;
  const load = async () => {
    const [history, feedback, data] = await Promise.all([
      api(`${endpoint}/outcomes`), api(`${endpoint}/feedback`), api("/api/v1/forecast/data-imports?limit=100"),
    ]);
    setEvents(history); setAdoption(feedback.current);
    setVersions(data.items.filter(row => row.status === "confirmed" && row.rules.kind === "sales"));
    const row = history.current?.accepted_feedback_id === feedback.current?.id ? history.current : null;
    setForm(row ? { ...empty, ...Object.fromEntries(Object.keys(empty).filter(key => key in row).map(key => [key, row[key] ?? ""])),
      data_version_uuid: row.sales_snapshot?.version_uuid || "", source_sku: row.sales_snapshot?.source_sku || "",
      ...Object.fromEntries(Object.entries(row.operational_metrics || {}).filter(([key]) => key in empty).map(([key, value]) => [key, String(value)])),
      ...Object.fromEntries(Object.entries(row.financials || {}).map(([key, value]) => [key, String(value)])),
    } : empty);
  };
  useEffect(() => { load().catch(err => setError(err.message)); }, [opportunityId, feedbackRevision]);
  const update = (key, value) => { setForm(current => ({ ...current, [key]: value })); setNotice(""); };
  const save = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const metricValues = metricFields.map(([key]) => form[key]);
      if (metricValues.some(value => value !== "") && metricValues.some(value => value === "")) {
        throw new Error("打样、投产、销量和退货数量须同时填写。");
      }
      const body = {
        expected_revision: events?.current?.revision || 0, accepted_feedback_id: adoption.id,
        status: form.status, evidence: form.evidence, result_label: form.result_label || null,
        ...Object.fromEntries(dateFields.map(([key]) => [key, form[key] || null])),
        data_version_uuid: form.data_version_uuid || null, source_sku: form.source_sku || null,
        operational_metrics: metricValues.every(value => value !== "") ? Object.fromEntries(
          metricFields.map(([key]) => [key, Number(form[key])]),
        ) : null,
        financials: form.revenue !== "" || form.cost !== "" || form.basis.trim() ? {
          revenue: form.revenue === "" ? null : Number(form.revenue), cost: form.cost === "" ? null : Number(form.cost),
          currency: form.currency, basis: form.basis,
        } : null,
      };
      const history = await api(`${endpoint}/outcomes`, { method: "PUT", body: JSON.stringify(body) });
      setEvents(history); setNotice("实施与观察记录已保存，历史修订保留。");
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return <section className="opportunity-outcomes" aria-label="实施与经营观察">
    <header><div><h4>实施与经营观察</h4><p>关联明确的采纳记录，再跟踪实际实施和观察结果。未实施不算失败。</p></div>
      <button disabled={busy} onClick={() => load().then(() => setError("")).catch(err => setError(err.message))}>刷新实施记录</button></header>
    {error && <p role="alert" className="enterprise-error">{error}</p>}
    {notice && <p role="status" className="enterprise-notice">{notice}</p>}
    {!events ? <p>正在读取实施记录…</p> : <>
      {adoption?.status !== "accepted" ? <p>当前尚未采纳，请先在上方保存“采纳”和原因，再记录实施。</p> : <>
        <p className="enterprise-gate">关联第 {adoption.revision} 次处理 · 采纳：{adoption.reason}</p>
        <fieldset disabled={busy} className="enterprise-fields">
          <label>实施进度<select value={form.status} onChange={event => update("status", event.target.value)}>{Object.entries(states).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          {dateFields.map(([key, label]) => <label key={key}>{label}<input type="date" value={form[key]} onChange={event => update(key, event.target.value)}/></label>)}
          <label>目标达成情况<select value={form.result_label} onChange={event => update("result_label", event.target.value)}><option value="">尚无结果标签</option>{Object.entries(results).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        </fieldset>
        <label>实施及结果依据<textarea disabled={busy} maxLength={4000} value={form.evidence} onChange={event => update("evidence", event.target.value)} placeholder="写明实施内容、事先目标、观察结果及可核验依据；仅计划或放弃时说明原因。"/></label>
        <details><summary>关联销量与企业财务记录（可选）</summary>
          <p>经营漏斗、销量和财务均为企业报告的观察口径。收入、费用和 ROI 不用于认定机会带来了增量收益。</p>
          <fieldset disabled={busy} className="enterprise-fields">
            {metricFields.map(([key, label]) => <label key={key}>{label}<input type="number" min="0" step="1" value={form[key]} onChange={event => update(key, event.target.value)}/></label>)}
          </fieldset>
          <p>关联销量版本时，观察期须完整覆盖该产品和机会站点；经营漏斗数量须与同一观察期一致。</p>
          <fieldset disabled={busy} className="enterprise-fields">
            <label>已确认销量版本<input list={listId} value={form.data_version_uuid} onChange={event => update("data_version_uuid", event.target.value)} placeholder="选择最近版本，或粘贴完整版本编号"/></label>
            <datalist id={listId}>{versions.map(row => <option key={row.version_uuid} value={row.version_uuid}>{row.filename} · {row.quality?.date_from}—{row.quality?.date_to}</option>)}</datalist>
            <label>回流来源 SKU<input value={form.source_sku} onChange={event => update("source_sku", event.target.value)} placeholder="须关联该机会的产品"/></label>
            <label>观察期收入<input type="number" min="0" step="any" value={form.revenue} onChange={event => update("revenue", event.target.value)}/></label>
            <label>观察期费用<input type="number" min="0" step="any" value={form.cost} onChange={event => update("cost", event.target.value)}/></label>
            <label>财务币种<input maxLength={3} value={form.currency} onChange={event => update("currency", event.target.value.toUpperCase())}/></label>
          </fieldset>
          <label>财务口径与凭据<textarea maxLength={2000} disabled={busy} value={form.basis} onChange={event => update("basis", event.target.value)} placeholder="说明收入是否扣退货、费用含哪些项目、报表或凭据编号。"/></label>
        </details>
        <button className="primary" disabled={busy || !form.evidence.trim()} onClick={save}>保存实施与观察</button>
      </>}
      {events.history.length > 0 && <div className="outcome-history"><h4>实施记录历史</h4>{events.history.map(row => <details key={row.id}>
        <summary>第 {row.revision} 版 · {states[row.status]}{row.result_label ? ` · ${results[row.result_label]}` : ""} · {new Date(row.created_at).toLocaleString("zh-CN")}</summary>
        <p>{row.evidence}</p><p>实施 {row.implementation_start || "尚未开始"}—{row.implementation_end || "未结束"} · 观察 {row.observation_start || "未记录"}—{row.observation_end || "未记录"}</p>
        {row.operational_metrics && <p>经营回流：打样 {row.operational_metrics.sample_units} 件 · 投产 {row.operational_metrics.production_units} 件 · 销量 {row.operational_metrics.sold_units} 件 · 退货 {row.operational_metrics.returned_units} 件 · 退货率 {row.operational_metrics.return_rate == null ? "无销量基数" : `${(row.operational_metrics.return_rate * 100).toFixed(2)}%`}</p>}
        {row.sales_snapshot && <><p>观察销量 {row.sales_snapshot.units} 件 · {row.sales_snapshot.days} 天 · {row.sales_snapshot.source_sku} / {row.sales_snapshot.site} · {row.sales_snapshot.sales_basis === "net_units" ? "净件数" : "毛件数"} · 确认补零 {row.sales_snapshot.filled_days} 天 · 非正常在售 {row.sales_snapshot.non_active_days} 天</p><small className="enterprise-digest">标准数据 SHA256：{row.sales_snapshot.canonical_sha256}</small></>}
        {row.financials && <p>企业报告：收入 {row.financials.revenue} / 费用 {row.financials.cost} / 经营净额 {row.financials.net_operating_amount} {row.financials.currency} · 报告 ROI {row.financials.reported_roi == null ? "无费用基数" : `${(row.financials.reported_roi * 100).toFixed(2)}%`} · {row.financials.basis}</p>}
      </details>)}</div>}
    </>}
  </section>;
}
