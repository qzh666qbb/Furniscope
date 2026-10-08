import { useEffect, useRef, useState } from "react";
import readXlsxFile from "read-excel-file";
import {
  ArrowsClockwiseIcon,
  CheckCircleIcon,
  UploadSimpleIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { api, idempotencyKey } from "./api.js";

const DAY_MS = 24 * 60 * 60 * 1000;
const columnAliases = {
  date: ["date", "日期", "时间", "订单日期", "销售日期"],
  sku: ["sku", "商品编码", "产品编码", "货号", "seller sku"],
  site: ["site", "site_code", "站点", "市场", "国家"],
  sales: ["sales", "daily_sales", "销量", "销售数量", "quantity", "数量"],
  inventory: ["inventory", "库存", "库存数量"],
};
const complexColumns = new Set([
  "order_id", "订单号", "line_id", "订单行号", "order_status", "订单状态",
  "refunded_units", "退货件数", "退款件数",
]);
const statusLabels = {
  queued: "排队中",
  running: "训练中",
  succeeded: "已发布",
  rejected: "未达标",
  failed: "失败",
};

const normalizeHeader = value => String(value ?? "").trim().toLowerCase();

function parseDate(value) {
  const today = new Date().toISOString().slice(0, 10);
  const validRange = text => text >= "2000-01-01" && text <= today;
  if (value instanceof Date && !Number.isNaN(value.valueOf())) {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    const text = `${year}-${month}-${day}`;
    return validRange(text) ? text : null;
  }
  const text = String(value ?? "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  const parsed = new Date(`${text}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf())
    && parsed.toISOString().slice(0, 10) === text
    && validRange(text) ? text : null;
}

function findColumn(headers, key, label, issues) {
  const accepted = new Set(columnAliases[key]);
  const matches = headers.map((header, index) => accepted.has(header) ? index : -1).filter(index => index >= 0);
  if (matches.length === 1) return matches[0];
  issues.push(matches.length ? `${label}列不唯一，请只保留一个明确字段` : `缺少${label}列`);
  return null;
}

function inspectRows(rows, kind) {
  const issues = [];
  if (!rows.length) return { state: "blocked", issues: ["工作表为空"] };
  const headers = rows[0].map(normalizeHeader);
  if (headers.some(header => !header)) issues.push("表头存在空列名");
  if (new Set(headers).size !== headers.length) issues.push("表头存在重复列名");
  if (kind === "orders" && headers.some(header => complexColumns.has(header))) {
    issues.push("检测到订单、取消或退货明细字段；快速追加只接收已确认口径的每日汇总销量");
  }
  const dateIndex = findColumn(headers, "date", "日期", issues);
  const skuIndex = findColumn(headers, "sku", "SKU", issues);
  const siteIndex = findColumn(headers, "site", "站点", issues);
  const valueKey = kind === "orders" ? "sales" : "inventory";
  const valueIndex = findColumn(headers, valueKey, kind === "orders" ? "销量件数" : "库存件数", issues);
  const dataRows = rows.slice(1).filter(row => row.some(value => value !== null && String(value).trim() !== ""));
  if (!dataRows.length) issues.push("工作表没有数据行");
  if ([dateIndex, skuIndex, siteIndex, valueIndex].some(index => index === null)) {
    return { state: "blocked", issues: [...new Set(issues)] };
  }

  let invalidDate = 0, invalidIdentity = 0, invalidValue = 0, duplicateRows = 0;
  const seen = new Set(), pairs = new Map(), skus = new Set(), dates = [];
  for (const row of dataRows) {
    const date = parseDate(row[dateIndex]);
    const sku = String(row[skuIndex] ?? "").trim();
    const site = String(row[siteIndex] ?? "").trim().toUpperCase();
    const rawValue = row[valueIndex];
    const value = rawValue === null || String(rawValue).trim() === "" ? NaN : Number(rawValue);
    if (!date) invalidDate += 1;
    if (!sku || !/^[A-Z][A-Z0-9_-]{1,15}$/.test(site)) invalidIdentity += 1;
    if (!Number.isFinite(value) || value < 0) invalidValue += 1;
    if (!date || !sku || !site) continue;
    const rowKey = `${date}\u0000${sku}\u0000${site}`;
    if (seen.has(rowKey)) duplicateRows += 1;
    seen.add(rowKey);
    const pairKey = `${sku}\u0000${site}`;
    const pairDates = pairs.get(pairKey) || new Set();
    pairDates.add(date);
    pairs.set(pairKey, pairDates);
    skus.add(sku);
    dates.push(date);
  }
  if (invalidDate) issues.push(`${invalidDate} 行日期不是有效的 YYYY-MM-DD 或 Excel 日期`);
  if (invalidIdentity) issues.push(`${invalidIdentity} 行 SKU 为空或站点代码不符合 US/UK/DE 等格式`);
  if (invalidValue) issues.push(`${invalidValue} 行${kind === "orders" ? "销量" : "库存"}不是非负有限数值`);
  if (duplicateRows) issues.push(`${duplicateRows} 行日期、SKU、站点重复，不符合每日汇总格式`);

  let missingDays = 0, maxHistoryDays = 0;
  for (const pairDates of pairs.values()) {
    const timestamps = [...pairDates].map(date => Date.parse(`${date}T00:00:00Z`)).sort((a, b) => a - b);
    if (!timestamps.length) continue;
    const span = Math.round((timestamps.at(-1) - timestamps[0]) / DAY_MS) + 1;
    maxHistoryDays = Math.max(maxHistoryDays, span);
    missingDays += span - pairDates.size;
  }
  if (kind === "orders" && missingDays) {
    issues.push(`检测到 ${missingDays} 个日期缺口；快速追加要求每个 SKU/站点历史连续`);
  }
  const uniqueIssues = [...new Set(issues)];
  return {
    state: uniqueIssues.length ? "blocked" : "ready",
    issues: uniqueIssues,
    summary: {
      rows: dataRows.length,
      skuCount: skus.size,
      dateFrom: dates.sort()[0] || null,
      dateTo: dates.sort().at(-1) || null,
      maxHistoryDays,
    },
  };
}

async function inspectFile(file, kind) {
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    return { state: "blocked", issues: ["快速追加仅接收 XLSX 文件"] };
  }
  try {
    return inspectRows(await readXlsxFile(file), kind);
  } catch {
    return { state: "blocked", issues: ["文件无法解析为有效 XLSX，请检查文件是否损坏或加密"] };
  }
}

function FileCheck({ label, check, onUseStandard }) {
  if (!check) return null;
  if (check.state === "checking") {
    return <div className="forecast-quick-check checking"><ArrowsClockwiseIcon/><span>正在检查{label}的结构与每日数据连续性…</span></div>;
  }
  if (check.state === "ready") {
    return <div className="forecast-quick-check ready"><CheckCircleIcon weight="fill"/><span>
      {label}已通过预检 · {check.summary.rows} 行 · {check.summary.skuCount} 个 SKU · {check.summary.dateFrom} 至 {check.summary.dateTo}
    </span></div>;
  }
  return <div className="forecast-quick-check blocked" role="alert">
    <WarningCircleIcon weight="fill"/>
    <div><strong>{label}不适合快速追加</strong><ul>{check.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>
      <button type="button" onClick={onUseStandard}>转到标准数据建模</button>
    </div>
  </div>;
}

export function ForecastQuickAppend({ model, onPublished, onUseStandard }) {
  const [orders, setOrders] = useState(null);
  const [inventory, setInventory] = useState(null);
  const [ordersCheck, setOrdersCheck] = useState(null);
  const [inventoryCheck, setInventoryCheck] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [runs, setRuns] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const knownRuns = useRef(new Map());
  const publishRef = useRef(onPublished);
  publishRef.current = onPublished;

  useEffect(() => {
    let stopped = false;
    let timer;
    const load = async () => {
      try {
        const result = await api("/api/v1/forecast/training-runs?limit=20");
        if (stopped) return;
        const items = result.items || [];
        for (const run of items) {
          if (
            run.status === "succeeded"
            && knownRuns.current.has(run.training_uuid)
            && knownRuns.current.get(run.training_uuid) !== "succeeded"
          ) {
            await publishRef.current?.();
          }
          knownRuns.current.set(run.training_uuid, run.status);
        }
        setRuns(items);
        setError("");
      } catch (reason) {
        if (!stopped) setError(reason.message);
      } finally {
        if (!stopped) timer = window.setTimeout(load, 4000);
      }
    };
    load();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, []);

  const chooseFile = (kind, setter, setCheck) => async event => {
    const file = event.target.files?.[0] || null;
    setter(file);
    setCheck(file ? { state: "checking", issues: [] } : null);
    setConfirmed(false);
    setMessage("");
    setError("");
    if (!file) return;
    const checked = await inspectFile(file, kind);
    if (kind === "orders" && !model?.ready && checked.state === "ready" && checked.summary.maxHistoryDays < 140) {
      checked.state = "blocked";
      checked.issues = ["当前尚无企业模型，完整历史至少应覆盖 140 天；首次接入请使用标准数据建模核对口径"];
    }
    setCheck(checked);
  };

  const submit = async () => {
    if (!orders) return setError("请先选择每日汇总销量 Excel 文件");
    if (ordersCheck?.state !== "ready") return setError("每日汇总销量文件尚未通过快速追加预检");
    if (inventory && inventoryCheck?.state !== "ready") return setError("库存文件尚未通过快速追加预检");
    if (!confirmed) return setError("请先确认文件口径和覆盖规则");
    if (/库存|inventory/i.test(orders.name)) {
      return setError("订单区域不能上传库存文件，请把库存文件放到右侧可选区域");
    }
    const form = new FormData();
    form.append("orders_file", orders);
    form.append("allow_history_overwrite", "true");
    if (inventory) form.append("inventory_file", inventory);
    try {
      setSubmitting(true);
      setMessage("");
      setError("");
      const accepted = await api("/api/v1/forecast/append", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("forecast-append") },
        body: form,
      });
      knownRuns.current.set(accepted.training_uuid, accepted.status);
      setRuns(previous => [
        accepted,
        ...previous.filter(item => item.training_uuid !== accepted.training_uuid),
      ]);
      setOrders(null);
      setInventory(null);
      setOrdersCheck(null);
      setInventoryCheck(null);
      setConfirmed(false);
      setMessage("数据已提交，系统将在质量校验通过后训练并发布；当前模型在此期间保持不变。");
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSubmitting(false);
    }
  };
  const filesReady = ordersCheck?.state === "ready"
    && (!inventory || inventoryCheck?.state === "ready");

  return <div className="forecast-quick-append">
    <section className="forecast-quick-form">
      <header>
        <div>
          <span className="forecast-model-label">{model?.ready ? "增量维护" : "首次训练兼容入口"}</span>
          <h2>快速追加销量数据</h2>
          <p>{model?.ready
            ? `合并到本企业可信历史，当前数据更新至 ${model.data_through || "—"}。`
            : "当前没有可追加的企业模型；请上传完整历史，系统会按首次训练处理。"}</p>
        </div>
        <ArrowsClockwiseIcon />
      </header>
      {!model?.ready && <div className="forecast-quick-first-use">
        <WarningCircleIcon weight="fill"/>
        <div><strong>首次接入建议使用标准数据建模</strong>
          <span>标准流程可处理字段识别、日期格式、订单/退货口径和产品编码对照；快速追加仅保留为严格格式的兼容入口。</span>
          <button type="button" onClick={onUseStandard}>使用标准数据建模</button>
        </div>
      </div>}
      <div className="forecast-quick-contract">
        <strong>快速追加文件要求</strong>
        <ul>
          <li>仅 XLSX；首个工作表为数据表，表头唯一且不为空</li>
          <li>必含日期、SKU、站点、销量件数；日期使用 YYYY-MM-DD 或 Excel 日期</li>
          <li>每个 SKU / 站点按日连续，同一天只能有一条非负汇总销量</li>
          <li>订单明细、取消/退货、缺日期或复杂口径不受支持，请使用标准数据建模</li>
        </ul>
      </div>
      <div className="forecast-quick-files">
        <label className={[orders ? "has-file" : "", ordersCheck?.state || ""].filter(Boolean).join(" ")}>
          <UploadSimpleIcon />
          <span><strong>每日汇总销量（必选）</strong><small>{orders
            ? `${orders.name} · ${(orders.size / 1024).toFixed(1)} KB`
            : "XLSX，包含日期、SKU、站点与销量"}</small></span>
          <input aria-label="每日汇总销量文件" type="file" accept=".xlsx" onChange={chooseFile("orders", setOrders, setOrdersCheck)} />
        </label>
        <label className={[inventory ? "has-file" : "", inventoryCheck?.state || ""].filter(Boolean).join(" ")}>
          <UploadSimpleIcon />
          <span><strong>库存数据（可选）</strong><small>{inventory
            ? `${inventory.name} · ${(inventory.size / 1024).toFixed(1)} KB`
            : "XLSX，作为独立库存快照留存"}</small></span>
          <input aria-label="库存数据文件" type="file" accept=".xlsx" onChange={chooseFile("inventory", setInventory, setInventoryCheck)} />
        </label>
      </div>
      <FileCheck label="每日汇总销量文件" check={ordersCheck} onUseStandard={onUseStandard}/>
      <FileCheck label="库存文件" check={inventoryCheck} onUseStandard={onUseStandard}/>
      <div className="forecast-quick-rule">
        <WarningCircleIcon />
        <span><strong>覆盖规则</strong> 相同日期、SKU、站点保留本次数据；质量门槛未通过时不会替换当前模型。</span>
      </div>
      {filesReady && <div className="forecast-quick-confirm">
        <strong>提交前确认</strong>
        <p>{model?.ready
          ? "本次数据将合并到本企业可信历史，并触发重新训练。"
          : "当前无企业私有模型，系统会把该完整历史按首次训练处理。"}</p>
        <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>
          我确认这是完整的每日汇总数据，采用退货前售出件数口径，并接受同键数据以本次上传为准
        </label>
      </div>}
      {error && <p role="alert" className="enterprise-error">{error}</p>}
      {message && <p role="status" className="enterprise-notice">{message}</p>}
      <button className="forecast-training-submit" disabled={!filesReady || !confirmed || submitting} onClick={submit}>
        <ArrowsClockwiseIcon />{submitting
          ? "正在提交…"
          : model?.ready ? "追加数据并重新训练" : "上传完整历史并训练"}
      </button>
    </section>
    <section className="forecast-quick-history">
      <header>
        <div><h2>追加与训练记录</h2><p>任务失败或未达标不会影响当前模型。</p></div>
        <span>{runs.length} 条</span>
      </header>
      {error && !runs.length
        ? <p className="forecast-quick-empty">暂时无法读取训练记录。</p>
        : runs.length
          ? <div>{runs.map(run => <article key={run.training_uuid}>
            <span><strong>{run.orders_filename || "企业标准数据"}</strong><small>{new Date(run.created_at).toLocaleString("zh-CN")} · {run.update_kind === "append" ? "追加历史" : run.update_kind === "rebuild" ? "完整重建" : "首次建模"}</small></span>
            <em className={run.status}>{statusLabels[run.status] || run.status}</em>
            {run.error_message && <p>{run.error_message}</p>}
          </article>)}</div>
          : <p className="forecast-quick-empty">还没有追加或训练记录。</p>}
    </section>
  </div>;
}
