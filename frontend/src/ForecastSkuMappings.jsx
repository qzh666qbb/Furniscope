import { useEffect, useState } from "react";
import {
  ArrowRightIcon,
  FloppyDiskIcon,
  LinkSimpleIcon,
  PlusIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { api } from "./api.js";

function withPendingSources(items, issues) {
  const result = [...items];
  const existing = new Set(items.map(item => item.source_sku.trim().toUpperCase()));
  for (const issue of [...(issues?.unmapped || []), ...(issues?.conflicts || [])]) {
    const source = String(issue.source_sku || "").trim();
    if (source && !existing.has(source.toUpperCase())) {
      result.push({ source_sku: source, product_sku: "" });
      existing.add(source.toUpperCase());
    }
  }
  return result;
}

export function ForecastSkuMappings({ issues, onSaved }) {
  const [config, setConfig] = useState(null), [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const load = async () => {
    try {
      setError("");
      const result = await api("/api/v1/forecast/sku-mappings");
      setConfig(result); setItems(withPendingSources(result.items, issues));
    } catch (reason) { setError(reason.message); }
  };
  useEffect(() => { load(); }, []);
  const edit = (index, field, value) => {
    setItems(rows => rows.map((row, i) => i === index ? { ...row, [field]: value } : row));
    setNotice("");
  };
  const save = async () => {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api("/api/v1/forecast/sku-mappings", { method: "PUT",
        body: JSON.stringify({ expected_revision: config.revision, items }) });
      setConfig(result); setItems(result.items); onSaved?.(result);
      setNotice("产品编码对照已保存，请重新预览训练范围。");
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  const add = () => {
    setItems(rows => [...rows, { source_sku: "", product_sku: "" }]);
    setNotice("");
  };
  const remove = index => {
    setItems(rows => rows.filter((_, rowIndex) => rowIndex !== index));
    setNotice("");
  };
  const incomplete = items.some(row => !row.source_sku.trim() || !row.product_sku);
  const unmapped = issues?.unmapped || [];
  const conflicts = issues?.conflicts || [];

  return <section className="sku-mapping-panel">
    <header className="sku-mapping-header">
      <div className="sku-mapping-heading">
        <span className="sku-mapping-heading-icon"><LinkSimpleIcon weight="bold"/></span>
        <div>
          <div className="sku-mapping-title">
            <h3>产品编码对照（可选）</h3>
            <span>{unmapped.length + conflicts.length} 项待处理</span>
          </div>
          <p>仅做编码身份绑定，不会修改日期、销量或其他标准数据。</p>
        </div>
      </div>
      <button type="button" className="sku-mapping-add" disabled={!config || busy} onClick={add}>
        <PlusIcon weight="bold"/>
        添加编码对照
      </button>
    </header>

    <div className="sku-mapping-problems" role="status">
      <strong>训练预检发现产品身份问题</strong>
      <span>未关联 {unmapped.length} 个，冲突 {conflicts.length} 个。编码一致时系统自动关联，只有编码不一致或发生碰撞时才需要填写。</span>
      <ul>
        {unmapped.slice(0, 6).map(item => <li key={`missing-${item.source_sku}-${item.site}`}>
          {item.source_sku} · {item.site}：产品中心无同编码产品
        </li>)}
        {conflicts.slice(0, 6).map(item => <li key={`conflict-${item.source_sku}-${item.site}`}>
          {item.source_sku} · {item.site}：{item.reason}
        </li>)}
      </ul>
    </div>

    {error && <p role="alert" className="enterprise-error sku-mapping-message">
      <span>{error}</span>
      <button type="button" disabled={busy} onClick={load}>刷新对照</button>
    </p>}
    {notice && <p role="status" className="enterprise-notice sku-mapping-message">{notice}</p>}

    {!config && !error && <p className="sku-mapping-loading">正在读取产品编码对照...</p>}
    {config && <fieldset className="sku-mapping-editor" disabled={busy}>
      {items.length > 0 ? <>
        <div className="sku-mapping-columns" aria-hidden="true">
          <span>原始数据 SKU</span>
          <span/>
          <span>产品中心 SKU</span>
          <span/>
        </div>
        <div className="sku-mapping-rows">
          {items.map((item, index) => <div className="sku-mapping-row" key={index}>
            <label>
              <span>原始数据 SKU</span>
              <input
                aria-label={`原始 SKU ${index + 1}`}
                maxLength={128}
                placeholder="输入数据文件中的 SKU"
                value={item.source_sku}
                onChange={event => edit(index, "source_sku", event.target.value)}
              />
            </label>
            <span className="sku-mapping-arrow" aria-hidden="true"><ArrowRightIcon weight="bold"/></span>
            <label>
              <span>产品中心 SKU</span>
              <select
                aria-label={`目标 SKU ${index + 1}`}
                value={item.product_sku}
                onChange={event => edit(index, "product_sku", event.target.value)}
              >
                <option value="">选择要关联的产品</option>
                {config.products.map(product => <option key={product.product_id} value={product.sku}>
                  {product.name ? `${product.sku} · ${product.name}` : product.sku}
                </option>)}
              </select>
            </label>
            <button
              type="button"
              className="sku-mapping-remove"
              aria-label={`移除第 ${index + 1} 条 SKU 映射`}
              title="移除此映射"
              onClick={() => remove(index)}
            >
              <TrashIcon/>
            </button>
          </div>)}
        </div>
      </> : <div className="sku-mapping-empty">
        <LinkSimpleIcon/>
        <div>
          <strong>当前无需手动映射</strong>
          <span>数据中的 SKU 将直接与产品中心同编码产品关联。</span>
        </div>
      </div>}

      <footer className="sku-mapping-actions">
        <span>保存后用于新建训练任务，不影响已排队任务。</span>
        <button
          type="button"
          className="primary"
          disabled={incomplete}
          onClick={save}
        >
          <FloppyDiskIcon weight="bold"/>
          {busy ? "正在保存..." : "保存产品编码对照"}
        </button>
      </footer>
    </fieldset>}
  </section>;
}
