import { useEffect, useState } from "react";
import { api, idempotencyKey } from "./api.js";
import "./enterprise-workflows.css";

const numericFields = [["unit_cost", "单位成本", "USD"], ["lead_time", "交期", "天"], ["moq", "最小起订量", "件"], ["factory_price", "出厂报价", "USD"]];

export function ProductFacts({ detail, etag, onSaved }) {
  const [vocabulary, setVocabulary] = useState(null), [values, setValues] = useState({});
  const [numbers, setNumbers] = useState({}), [changed, setChanged] = useState([]);
  const [note, setNote] = useState(""), [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => { api("/api/v1/enterprise/fact-vocabulary").then(setVocabulary).catch(err => setError(err.message)); }, []);
  useEffect(() => {
    setValues(Object.fromEntries(detail.attributes.filter(a => Array.isArray(a.attribute_value) && a.confirmation_status === "confirmed" &&
      (["user_input", "confirmed_structured"].includes(a.source_type) || a.source_locator?.confirmed_by))
      .map(a => [a.attribute_code, a.attribute_value])));
    setNumbers(Object.fromEntries(numericFields.map(([code, , unit]) => {
      const attr = detail.attributes.find(a => a.attribute_code === code);
      return [code, { value: attr?.attribute_value ?? "", unit: attr?.unit || unit }];
    })));
    setChanged([]); setNote(""); setReviewed(false);
  }, [detail]);
  const mark = code => { setChanged(current => [...new Set([...current, code])]); setReviewed(false); };
  const perform = async action => {
    setBusy(true); setError("");
    try { await action(); await onSaved(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const save = () => perform(async () => {
    const attributes = changed.map(code => ({
      attribute_code: code,
      attribute_value: code.endsWith("_codes") ? values[code] || [] : Number(numbers[code].value),
      unit: code.endsWith("_codes") ? null : numbers[code].unit,
      source_type: "user_input", confidence: 1, confirmation_status: "confirmed",
      source_locator: { input: "product_fact_review", review_note: note.trim(),
        vocabulary_version: vocabulary.version,
        suggestions: (detail.fact_suggestions || []).filter(s => s.attribute_code === code && (values[code] || []).includes(s.code)) },
    }));
    await api(`/api/v1/products/${detail.product_id}`, { method: "PATCH", headers: { "If-Match": etag }, body: JSON.stringify({ attributes }) });
  });
  const confirm = () => perform(async () => {
    await api(`/api/v1/products/${detail.product_id}/profile:confirm`, {
      method: "POST", headers: { "If-Match": etag, "Idempotency-Key": idempotencyKey("product-confirm") },
      body: JSON.stringify({ profile_version_id: detail.profile_version_id, confirmed_attribute_codes: detail.attributes.map(a => a.attribute_code) }),
    });
  });
  const conflicts = detail.attributes.some(a => a.confirmation_status === "conflicted");
  const invalidNumber = changed.some(code => numbers[code] && (numbers[code].value === "" || !Number.isFinite(Number(numbers[code].value)) || Number(numbers[code].value) < 0 || !numbers[code].unit.trim()));
  return <section className="detail-section enterprise-flow product-fact-review">
    <h3>企业适配所需事实</h3>
    <p>从资料匹配出的内容仅为候选。请核验后选择；未选择的能力保持未知，不能视为具备。认证请核实有效文件及适用范围。</p>
    {error && <p role="alert" className="enterprise-error">{error}</p>}
    {!vocabulary ? <p>正在读取标准选项…</p> : <>
      <fieldset disabled={busy}>{vocabulary.groups.map(group => <div className="product-fact-group" key={group.attribute_code}>
        <strong>{group.name}</strong>
        <div>{group.options.map(option => {
          const suggestions = (detail.fact_suggestions || []).filter(s => s.attribute_code === group.attribute_code && s.code === option.code);
          return <div key={option.code}>
            <label className="enterprise-checkbox"><input type="checkbox" checked={(values[group.attribute_code] || []).includes(option.code)} onChange={event => {
              setValues(current => ({ ...current, [group.attribute_code]: event.target.checked ? [...(current[group.attribute_code] || []), option.code] : (current[group.attribute_code] || []).filter(c => c !== option.code) })); mark(group.attribute_code);
            }}/>{option.name}{suggestions.length > 0 && <small>资料候选</small>}</label>
            {suggestions.length > 0 && <details><summary>查看原文证据</summary>{suggestions.map((s, i) => <blockquote key={i}>{s.evidence_text}{s.source_locator?.evidence_text && <small>{s.source_locator.evidence_text}</small>}</blockquote>)}</details>}
          </div>;
        })}</div>
      </div>)}</fieldset>
      <details><summary>补充成本、交期与起订量</summary><p>成本与出厂报价分别记录，系统不会互相替代；单位须与企业条件一致。</p>
        <fieldset disabled={busy} className="enterprise-fields">{numericFields.map(([code, name]) => <label key={code}>{name}<input type="number" min="0" value={numbers[code]?.value ?? ""} onChange={e => { setNumbers(n => ({ ...n, [code]: { ...n[code], value: e.target.value } })); mark(code); }}/><input aria-label={`${name}单位`} value={numbers[code]?.unit || ""} onChange={e => { setNumbers(n => ({ ...n, [code]: { ...n[code], unit: e.target.value } })); mark(code); }}/></label>)}</fieldset>
      </details>
      <label>事实核验依据<textarea disabled={busy} maxLength={1000} value={note} onChange={e => setNote(e.target.value)} placeholder="例如：依据当前产品规格书及认证附件核验；记录文件名或编号"/></label>
      <button disabled={busy || !changed.length || note.trim().length < 4 || invalidNumber} onClick={save}>保存已核验事实</button>
    </>}
    <div className="product-profile-confirm">
      <h4>{detail.profile_status === "confirmed" ? "当前画像已确认" : "确认当前画像"}</h4>
      <p>检查下方全部参数和原文证据，先保存修改并处理冲突。确认后才可开始产品分析。</p>
      <label className="enterprise-checkbox"><input type="checkbox" checked={reviewed} disabled={busy || changed.length > 0 || conflicts || detail.profile_status === "confirmed"} onChange={e => setReviewed(e.target.checked)}/>我已核验当前画像的全部参数</label>
      <button className="primary" disabled={busy || !reviewed || changed.length > 0 || conflicts || !detail.attributes.length || detail.profile_status === "confirmed"} onClick={confirm}>确认画像并启用分析</button>
    </div>
  </section>;
}
