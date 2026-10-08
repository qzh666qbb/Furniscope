import { useState } from "react";

export const importExtraFields = [
  ["warehouse", "仓库"], ["order_id", "订单号"], ["line_id", "订单行号"],
  ["order_status", "订单状态"], ["refunded_units", "实际退货件数"],
  ["unit_price", "折后成交单价"], ["discount", "折扣比例"], ["currency", "币种"],
];

function ValueMap({ title, values = {}, onChange, choices }) {
  const [source, setSource] = useState(""), [target, setTarget] = useState(choices?.[0]?.[0] || "");
  return <div><h4>{title}</h4>
    {Object.entries(values).map(([key, value]) => <div className="enterprise-inline" key={key}>
      <span>{key} → {choices?.find(([code]) => code === value)?.[1] || value}</span>
      <button type="button" aria-label={`移除${title} ${key}`} onClick={() => {
        const next = { ...values }; delete next[key]; onChange(next);
      }}>移除</button>
    </div>)}
    <div className="enterprise-inline">
      <label>原始{title}<input aria-label={`原始${title}`} value={source} onChange={event => setSource(event.target.value)}/></label>
      <label>对应{title}{choices ? <select aria-label={`对应${title}`} value={target} onChange={event => setTarget(event.target.value)}>
        {choices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select> : <input aria-label={`对应${title}`} value={target} placeholder="US / UK / EU" onChange={event => setTarget(event.target.value)}/>}</label>
      <button type="button" disabled={!source.trim() || !target.trim()} onClick={() => {
        onChange({ ...values, [source.trim()]: target.trim() }); setSource("");
      }}>添加{title}</button>
    </div>
  </div>;
}

export function ImportSemantics({ rules, editRules, renderMapping }) {
  return <details><summary>订单、仓库与成交事实（可选）</summary>
    <div className="enterprise-fields">{importExtraFields.filter(([key]) => rules.kind === "sales" || key === "warehouse").map(renderMapping)}</div>
    {rules.mapping.warehouse && <ValueMap title="仓库站点" values={rules.warehouse_sites} onChange={warehouse_sites => editRules({ warehouse_sites })}/>}
    {rules.kind === "sales" && <>
      {rules.mapping.order_status && <ValueMap title="订单状态" values={rules.order_statuses} onChange={order_statuses => editRules({ order_statuses })}
        choices={[["completed", "已成交"], ["cancelled", "已取消"], ["refunded", "有退款 / 退货"]]}/>}
      {(rules.mapping.order_id || rules.mapping.order_status || rules.mapping.refunded_units || rules.mapping.line_id) && <>
        <label>重复订单行<select value={rules.duplicate_orders || "error"} onChange={event => editRules({ duplicate_orders: event.target.value })}>
          <option value="error">全部报错，人工核对</option><option value="drop_identical">仅完全相同的订单行去重</option>
        </select></label>
        <p>订单号＋行号＋站点定位订单行。取消单排除；净销量按实际退货件数扣减。内容冲突始终报错。</p>
        <label className="enterprise-checkbox"><input type="checkbox" checked={!!rules.order_semantics_confirmed} onChange={event => editRules({ order_semantics_confirmed: event.target.checked })}/>
          确认文件是覆盖整日的订单行快照，件数为退货前数量，退款按原订单日期回写；追加时按日替换，不累加部分订单批次
        </label>
      </>}
      {(rules.mapping.unit_price || rules.mapping.discount) && <>
        <label>无币种列时使用<input maxLength={3} value={rules.default_currency || ""} placeholder="USD" onChange={event => editRules({ default_currency: event.target.value.toUpperCase() || null })}/></label>
        <label className="enterprise-checkbox"><input type="checkbox" checked={!!rules.price_semantics_confirmed} onChange={event => editRules({ price_semantics_confirmed: event.target.checked })}/>
          确认单价为折后成交单价（不含运费税费），折扣为 0—1 比例，不再次扣折扣
        </label>
        <p>保留逐行成交事实。同日不同价格不强行平均；当前模型仍只学习销量。</p>
      </>}
    </>}
  </details>;
}
