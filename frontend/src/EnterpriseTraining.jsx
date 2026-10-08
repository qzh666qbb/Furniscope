import { useEffect, useRef, useState } from "react";
import { UploadSimpleIcon } from "@phosphor-icons/react";
import { api, idempotencyKey } from "./api.js";
import { ForecastSkuMappings } from "./ForecastSkuMappings.jsx";
import { ForecastQuickAppend } from "./ForecastQuickAppend.jsx";
import { ImportSemantics, importExtraFields } from "./ImportSemantics.jsx";
import "./enterprise-workflows.css";

const root = "/api/v1/forecast";
const labels = { queued: "排队中", running: "训练中", succeeded: "已发布", rejected: "未达标", failed: "失败" };
const modes = { initial: "首次建模", append: "追加历史", rebuild: "完整重建" };
const percent = value => value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
export function saveJson(data, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function SampleTable({ rows, fields }) {
  return <div className="enterprise-table"><table><thead><tr>{fields.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{fields.map(([key]) => <td key={key}>{String(row[key] ?? "—")}</td>)}</tr>)}</tbody></table></div>;
}

export function TrainingEvaluation({ metrics = {} }) {
  const result = metrics.evaluation || metrics;
  if (!result.day && !result.week) return null;
  const segments = { mature: "成熟", sparse: "稀疏", new: "新品 / 短历史", dormant: "全零历史" };
  const methods = { xgboost: "独立模型", intermittent_mean: "间歇销量均值", recent_mean: "近期均值" };
  return <div className="training-evaluation"><p>{result.gate?.folds ? "逐 SKU 三窗口滚动评测" : "时间留出评测"} · WAPE 越低越好 · 误差不代表收益承诺</p>
    <SampleTable rows={["day", "week"].filter(key => result[key]).map(key => ({
      grain: key === "day" ? "日预测" : "周预测", wape: percent(result[key].wape),
      baseline: percent(Math.min(...Object.values(result[key].baselines || {}).map(item => item.wape ?? Infinity))),
      bias: percent(result[key].bias), coverage: percent(result[key].coverage), mae: result[key].mae?.toFixed(2),
      macro: percent(result[key].macro_wape), worst: percent(result[key].worst_sku_wape),
    }))} fields={[["grain", "粒度"], ["wape", "总 WAPE"], ["baseline", "最佳基线"], ["macro", "SKU 平均"], ["worst", "最差 SKU"], ["mae", "MAE"], ["bias", "偏差"], ["coverage", "已评测覆盖"]]}/>
    {["day", "week"].filter(key => result[key]?.series).map(key => <details key={key}><summary>{key === "day" ? "日" : "周"}预测 · 查看各 SKU 和验证窗口</summary>
      <p>新品和全零历史不计入精度验证。稀疏销量只验证整个窗口的总量，逐期 WAPE 仍完整展示。</p>
      {result[key].series.map(row => <article key={`${row.sku}-${row.site}`} className="enterprise-run">
        <strong>{row.sku} · {row.site} · {segments[row.segment]} · {methods[row.method]}</strong>
        <p>{row.history_periods} 个历史周期 · {row.validation_status === "validated" ? (row.validation_scope === "window_total" ? "窗口总量通过" : "逐期销量通过") : row.validation_status === "failed" ? "评测未通过" : "尚未验证精度"} · WAPE {percent(row.wape)}</p>
        {row.reasons?.length > 0 && <p>{row.reasons.join("；")}</p>}
        {row.folds?.length > 0 && <SampleTable rows={row.folds.map(fold => ({ ...fold, wape: percent(fold.wape), bias: percent(fold.bias), outcome: fold.passed ? "通过" : "未通过" }))}
          fields={[["fold", "窗口"], ["train_through", "训练截止"], ["validation_from", "验证开始"], ["validation_through", "验证截止"], ["wape", "WAPE"], ["bias", "总量偏差"], ["outcome", "结果"]]}/>}
      </article>)}
    </details>)}
  </div>;
}

function StandardEnterpriseTraining({ model, onPublished }) {
  const [versions, setVersions] = useState([]), [runs, setRuns] = useState([]);
  const [templates, setTemplates] = useState([]), [templateId, setTemplateId] = useState(""), [templateName, setTemplateName] = useState("");
  const [auxiliary, setAuxiliary] = useState({});
  const [data, setData] = useState(null), [rules, setRules] = useState(null), [preview, setPreview] = useState(null);
  const [mode, setMode] = useState("initial"), [merge, setMerge] = useState(null), [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const publishRef = useRef(onPublished), knownRuns = useRef(new Map()), requestKey = useRef(null);
  publishRef.current = onPublished;
  useEffect(() => {
    setMode(model?.ready ? "append" : "initial");
    resetMerge();
  }, [model?.ready]);
  const load = async () => {
    const [files, tasks, saved] = await Promise.all([api(`${root}/data-imports`), api(`${root}/training-runs`), api(`${root}/import-templates`)]);
    setVersions(files.items); setRuns(tasks.items); setTemplates(saved.items);
    for (const run of tasks.items) {
      if (run.status === "succeeded" && knownRuns.current.has(run.training_uuid) && knownRuns.current.get(run.training_uuid) !== "succeeded") {
        await publishRef.current?.();
      }
      knownRuns.current.set(run.training_uuid, run.status);
    }
  };
  useEffect(() => {
    let stopped = false, timer;
    const poll = async () => {
      try { if (!stopped) await load(); }
      catch (reason) { if (!stopped) setError(reason.message); }
      finally { if (!stopped) { setLoading(false); timer = setTimeout(poll, 4000); } }
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  const perform = async action => {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  const resetMerge = () => { setMerge(null); setOverwrite(false); requestKey.current = null; };
  const selectVersion = row => {
    setData(row); setRules(row.rules); setPreview(row.status !== "uploaded" && row.preview_sha256 ? row : null); resetMerge();
    setAuxiliary(Object.fromEntries(Object.entries(row.auxiliary_sources || {}).map(([key, item]) => [key, item.version_uuid])));
    setTemplateId(""); setTemplateName("");
  };
  const editRules = patch => {
    setRules(current => ({ ...current, ...patch })); setPreview(null); resetMerge();
  };
  const upload = event => {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    setData(null); setRules(null); setPreview(null); resetMerge();
    perform(async () => {
      const form = new FormData(); form.append("file", file);
      const row = await api(`${root}/data-imports`, { method: "POST", body: form });
      selectVersion(await api(`${root}/data-imports/${row.version_uuid}`)); await load();
    });
  };
  const preflight = () => perform(async () => {
    const row = await api(`${root}/data-imports/${data.version_uuid}/preflight`, { method: "POST", body: JSON.stringify({ rules, auxiliary_versions: auxiliary }) });
    setPreview(row); setData(row); resetMerge(); await load();
  });
  const confirm = () => perform(async () => {
    const row = await api(`${root}/data-imports/${data.version_uuid}/confirm`, { method: "POST", body: JSON.stringify({ preview_sha256: preview.preview_sha256 }) });
    setData(row); setNotice("标准数据已确认，可预览训练范围。"); await load();
  });
  const trainingBody = () => ({ data_version_uuid: data.version_uuid, mode, allow_history_overwrite: overwrite });
  const start = () => perform(async () => {
    requestKey.current ||= idempotencyKey("enterprise-training");
    const run = await api(`${root}/training-runs`, { method: "POST", headers: { "Idempotency-Key": requestKey.current }, body: JSON.stringify(trainingBody()) });
    knownRuns.current.set(run.training_uuid, run.status);
    setNotice("训练任务已创建，评测通过后才会切换模型。可在下方查看结果。"); resetMerge(); await load();
  });
  const quality = preview?.quality;
  const confirmed = data?.status === "confirmed";
  const currentRun = data?.version_uuid
    ? runs.find(run => (run.data_versions || []).includes(data.version_uuid))
    : null;
  const trainingStepClass = currentRun?.status === "succeeded"
    ? "done"
    : ["queued", "running"].includes(currentRun?.status)
      ? "active"
      : ["rejected", "failed"].includes(currentRun?.status)
        ? "error"
        : "";
  const stepLabels = [
    "上传与数据清洗",
    "确认标准数据",
    "产品编码对照",
    `训练评测与发布${currentRun ? ` · ${labels[currentRun.status] || currentRun.status}` : ""}`,
  ];
  const renderMapping = ([key, label]) => <label key={key}>{label}<select aria-label={`映射${label}`} value={rules.mapping[key] || ""} onChange={event => {
    const mapping = { ...rules.mapping }; if (event.target.value) mapping[key] = event.target.value; else delete mapping[key]; editRules({ mapping });
  }}><option value="">请选择原始列</option>{data.columns_info.map(column => <option key={column} value={column}>{column}</option>)}</select></label>;
  return <div className="enterprise-training enterprise-flow">
    <header className="enterprise-heading"><div><span>标准数据建模 · 首次接入推荐</span><h2>从原始数据到可验证的模型</h2><p>先完成数据清洗和标准版本确认；只有检测到未关联 SKU 时才配置产品编码对照。成熟 SKU 以 140 天 / 20 个完整自然周进行滚动评测。</p></div></header>
    <label className={`enterprise-upload-zone ${busy ? "disabled" : ""}`}>
      <UploadSimpleIcon />
      <span><strong>上传企业销量数据</strong><small>拖拽文件到这里，或点击选择本地文件</small></span>
      <span className="enterprise-upload-meta"><b>CSV</b><b>XLSX</b><b>JSON</b><small>保留原始文件与 SHA256 血缘</small></span>
      <em>选择文件</em>
      <input aria-label="选择数据文件" type="file" accept=".csv,.xlsx,.json" disabled={busy} onChange={upload}/>
    </label>
    <ol className="enterprise-steps">{stepLabels.map((label, index) => <li key={index} className={index === 0 && data || index === 1 && confirmed || index === 2 && merge?.catalog?.can_publish ? "done" : index === 3 ? trainingStepClass : ""}><b>{index + 1}</b>{label}</li>)}</ol>
    {error && <div role="alert" className="enterprise-error">{error}<button disabled={busy} onClick={() => perform(load)}>重试读取</button></div>}
    {notice && <p role="status" className="enterprise-notice">{notice}</p>}
    {loading && <p>正在读取企业数据与训练记录…</p>}
    {data && <section className="enterprise-section"><header><div><h3>{data.filename}</h3><small>{confirmed ? "已确认 · 修改规则需新建版本" : "请检查字段含义及业务口径"}</small></div>
      {confirmed && <button disabled={busy} onClick={() => perform(async () => selectVersion(await api(`${root}/data-imports/${data.version_uuid}/revisions`, { method: "POST" })))}>新建清洗版本</button>}
      {!confirmed && <button disabled={busy} onClick={() => perform(async () => {
        const suggestion = await api(`${root}/data-imports/${data.version_uuid}/mapping-suggestion`, { method: "POST" });
        const allowed = ["date", "sku", "site", rules.kind, "status", ...importExtraFields.filter(([key]) => rules.kind === "sales" || key === "warehouse").map(([key]) => key)];
        editRules({ mapping: Object.fromEntries(Object.entries(suggestion.mapping).filter(([key]) => allowed.includes(key))) });
        setNotice(suggestion.message);
      })}>建议字段映射</button>}</header>
      <div className="enterprise-inline">
        {!confirmed && <><label>复用企业模板<select disabled={busy} value={templateId} onChange={event => setTemplateId(event.target.value)}>
          <option value="">选择已保存的模板</option>{templates.map(item => <option key={item.template_uuid} value={item.template_uuid}>{item.name} · 第 {item.revision} 版</option>)}
        </select></label><button disabled={busy || !templateId} onClick={() => perform(async () => {
          selectVersion(await api(`${root}/data-imports/${data.version_uuid}/apply-template`, { method: "POST", body: JSON.stringify({ template_uuid: templateId }) }));
          setNotice("模板已应用，请核对本次文件口径并重新预检。"); await load();
        })}>应用导入模板</button></>}
        {confirmed && <><label>企业模板名称<input maxLength={80} disabled={busy} value={templateName} onChange={event => setTemplateName(event.target.value)}/></label>
          <button disabled={busy || !templateName.trim()} onClick={() => perform(async () => {
            const name = templateName.trim(), existing = templates.find(item => item.name === name);
            await api(`${root}/import-templates`, { method: "POST", body: JSON.stringify({ name, data_version_uuid: data.version_uuid, expected_revision: existing?.revision || 0 }) });
            await load(); setNotice("企业模板已保存，可在下次上传时复用。");
          })}>保存为企业模板</button></>}
      </div>
      {data.template_snapshot && <p>已采用模板：{data.template_snapshot.name} · 第 {data.template_snapshot.revision} 版</p>}
      <fieldset disabled={busy || confirmed} className="enterprise-fields">
        <label>数据类型<select value={rules.kind} onChange={event => {
          setAuxiliary({}); editRules({ kind: event.target.value, grain: "daily", mapping: Object.fromEntries(Object.entries(rules.mapping).filter(([key]) => ["date", "sku", "site", "status", "warehouse"].includes(key))) });
        }}><option value="sales">销量件数</option><option value="inventory">每日库存</option></select></label>
        {[["date", "日期"], ["sku", "SKU"], ["site", "站点"], [rules.kind, rules.kind === "sales" ? "销量（件数）" : "库存（件数）"], ["status", "销售状态（可选）"]].map(renderMapping)}
        <label>原始粒度<select value={rules.grain} onChange={event => editRules({ grain: event.target.value })}><option value="daily">每日汇总（重复视为错误）</option>{rules.kind === "sales" && <option value="transactions">订单明细（按日累加）</option>}</select></label>
        <label>销量口径<select value={rules.sales_basis} onChange={event => editRules({ sales_basis: event.target.value })}><option value="gross_units">售出件数（退货前）</option><option value="net_units">净件数（扣退货）</option></select></label>
        <label>日期格式<select value={rules.date_format} onChange={event => editRules({ date_format: event.target.value })}>{[["%Y-%m-%d", "年-月-日"], ["%Y/%m/%d", "年/月/日"], ["%d/%m/%Y", "日/月/年"], ["%m/%d/%Y", "月/日/年"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>无站点列时使用<input value={rules.default_site || ""} placeholder="例如 US；共享库存保留 EU" onChange={event => editRules({ default_site: event.target.value || null })}/></label>
        <label>缺失日期<select value={rules.missing_dates} onChange={event => editRules({ missing_dates: event.target.value, complete_export_confirmed: false })}><option value="unknown">保留未知</option>{rules.kind === "sales" && <option value="zero">确认完整后补零</option>}</select></label>
        {rules.missing_dates === "zero" && <label className="enterprise-checkbox"><input type="checkbox" checked={rules.complete_export_confirmed} onChange={event => editRules({ complete_export_confirmed: event.target.checked })}/>确认持续销售，且文件完整导出了该期间全部销售记录</label>}
      </fieldset>
      <fieldset disabled={busy || confirmed}>
        <ImportSemantics key={data.version_uuid} rules={rules} editRules={editRules} renderMapping={renderMapping}/>
        {rules.kind === "sales" && <details><summary>关联对账表与库存（可选）</summary>
          <p>先分别上传并确认辅助文件，再选择对应版本。按日期、SKU、站点逐项对账；不填补未来库存，不拆分共享仓。</p>
          <div className="enterprise-fields">{[["control", "销量对账版本", "sales"], ["inventory", "库存快照版本", "inventory"]].map(([key, label, kind]) => <label key={key}>{label}
            <select value={auxiliary[key] || ""} onChange={event => {
              const next = { ...auxiliary }; if (event.target.value) next[key] = event.target.value; else delete next[key];
              setAuxiliary(next); setPreview(null); resetMerge();
            }}><option value="">不关联</option>{versions.filter(item => item.version_uuid !== data.version_uuid && item.status === "confirmed" && item.rules.kind === kind).map(item => <option key={item.version_uuid} value={item.version_uuid}>{item.filename} · {new Date(item.created_at).toLocaleString("zh-CN")}</option>)}</select>
          </label>)}</div>
        </details>}
      </fieldset>
      {!confirmed && <button className="primary" disabled={busy || rules.missing_dates === "zero" && !rules.complete_export_confirmed} onClick={preflight}>{busy ? "处理中…" : "运行质量预检"}</button>}
      {quality && <div className="enterprise-quality">
        <div className="enterprise-metrics">{[["原始行", quality.source_rows], ["标准行", quality.canonical_rows], ["错误", quality.error_count], ["补零行", quality.filled_rows], ["SKU / 站点", quality.pair_count], ["数量对账", quality.quantity_reconciled ? "一致" : "待处理"]].map(([label, value]) => <div key={label}><small>{label}</small><strong>{value}</strong></div>)}</div>
        <p>{quality.date_from || "—"} 至 {quality.date_to || "—"} · 原始总量 {quality.input_quantity_total ?? "—"} → 标准总量 {quality.output_quantity_total ?? "—"}</p>
        {quality.adjustments && <p>重复去除 {quality.adjustments.duplicate_units} 件 · 取消排除 {quality.adjustments.cancelled_units} 件 · 退货扣减 {quality.adjustments.refunded_units} 件 · 成交事实 {quality.price_fact_rows} 行</p>}
        {Object.entries(quality.reconciliation || {}).map(([key, item]) => <p key={key}>{key === "control" ? "销量对账" : "库存关联"}：匹配 {item.matched_rows} 行 · 缺失 {item.missing_count} 行 · 多余 {item.extra_count} 行 · 差额 {item.difference_count} 行</p>)}
        <ul>{[...(quality.errors || []).map(item => `原始行 ${item.row ?? "汇总"}：${item.reason}`), ...(quality.warnings || []).map(item => typeof item === "string" ? item : JSON.stringify(item)), ...(quality.training_blockers || [])].map((message, index) => <li key={index}>{message}</li>)}</ul>
        {rules.kind === "inventory" && <p>库存可独立确认和审计，当前销量模型尚未使用库存特征。</p>}
        <details><summary>标准数据样本（前 20 行）</summary><SampleTable rows={preview.sample || []} fields={[["date", "日期"], ["sku", "SKU"], ["site", "站点"], [rules.kind, "件数"], ["status", "销售状态"], ["unit_price", "成交单价"], ["discount", "折扣比例"], ["currency", "币种"], ...(rules.kind === "sales" ? [["inventory", "关联库存"]] : [])]}/></details>
        <details><summary>数据校验与血缘</summary><p className="enterprise-digest">原文件 SHA256：{data.raw_sha256}<br/>标准文件 SHA256：{preview.canonical_sha256}</p><button onClick={() => perform(async () => saveJson(await api(`${root}/data-imports/${data.version_uuid}/audit`, { rawData: true }), `data-audit-${data.version_uuid}.json`))} disabled={busy}>下载行级审计</button></details>
        {!confirmed && <button className="primary" disabled={busy || !quality.can_confirm} onClick={confirm}>确认此标准数据版本</button>}
      </div>}
      {confirmed && data.quality.trainable && <div className="enterprise-training-preview"><h3>训练范围</h3><label>训练方式<select disabled={busy} value={mode} onChange={event => { setMode(event.target.value); resetMerge(); }}>{Object.entries(modes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <p>{mode === "initial" ? "从本企业完整历史独立训练。" : mode === "append" ? "与现用企业模型的可信历史合并；不读取共享模型历史。" : "仅使用本次标准版本重建；原模型保留至新模型验证通过。"}</p>
        <button disabled={busy} onClick={() => perform(async () => { setMerge(await api(`${root}/training-preview`, { method: "POST", body: JSON.stringify(trainingBody()) })); setOverwrite(false); })}>预览训练范围</button>
        {merge && <div><p>输入 {merge.input_rows} 行 · 合并后 {merge.merged_rows} 行 · 重叠 {merge.overlap_rows} 行 · 改写销量 {merge.overwritten_rows} 行 · 日期缺口 {merge.missing_days} 天</p>
          {merge.catalog && <div><p>已关联 {merge.catalog.items.length} 个 SKU / 站点 · 未关联 {merge.catalog.unmapped.length} 个 · 冲突 {merge.catalog.conflicts.length} 个</p>
            {merge.catalog.can_publish
              ? <p className="enterprise-notice">产品身份已对齐。编码相同的 SKU 已自动关联，无需额外设置。</p>
              : <ForecastSkuMappings
                  issues={merge.catalog}
                  onSaved={() => {
                    resetMerge();
                    setNotice("产品编码对照已保存，请重新预览训练范围。当前模型和已排队任务不受影响。");
                  }}
                />}
          </div>}
          {!!merge.overwrites?.length && <SampleTable rows={merge.overwrites} fields={[["date", "日期"], ["sku", "SKU"], ["site", "站点"], ["old_sales", "原销量"], ["new_sales", "新销量"]]}/>}
          {!!merge.gaps?.length && <SampleTable rows={merge.gaps} fields={[["sku", "SKU"], ["site", "站点"], ["after", "缺口开始"], ["before", "缺口结束"], ["missing_days", "天数"]]}/>}
          {merge.overwritten_rows > 0 && <label className="enterprise-checkbox"><input type="checkbox" checked={overwrite} disabled={busy} onChange={event => { setOverwrite(event.target.checked); requestKey.current = null; }}/>确认以上历史销量更正</label>}
          <button className="primary" disabled={busy || merge.catalog?.can_publish === false || merge.missing_days > 0 || merge.overwritten_rows > 0 && !overwrite} onClick={start}>启动{modes[mode]}</button>
        </div>}
      </div>}
    </section>}
    <section className="enterprise-section"><header><h3>数据版本</h3><small>最近 20 份 · 原始文件和确认版本均保留</small></header>
      {!loading && !versions.length && <p>上传企业销售历史，开始首次建模。</p>}
      <div className="enterprise-version-list">{versions.map(item => <button key={item.version_uuid} disabled={busy} className={data?.version_uuid === item.version_uuid ? "selected" : ""} onClick={() => perform(async () => selectVersion(await api(`${root}/data-imports/${item.version_uuid}`)))}><strong>{item.filename}</strong><span>{item.status === "confirmed" ? "已确认" : item.status === "previewed" ? "已预检" : "待预检"} · {new Date(item.created_at).toLocaleString("zh-CN")}</span></button>)}</div>
    </section>
    <section className="enterprise-section"><header><h3>训练与评测记录</h3><small>最近 20 次 · 失败或未达标保留现用模型</small></header>
      {!loading && !runs.length && <p>尚无训练任务。</p>}
      {runs.map(run => <article className="enterprise-run" key={run.training_uuid}><header><div><strong>{run.orders_filename || "企业标准数据"}</strong><small>{new Date(run.created_at).toLocaleString("zh-CN")} · {modes[run.update_kind] || "数据训练"}</small></div><span className={`enterprise-state ${run.status}`}>{labels[run.status] || run.status}</span></header>
        {run.error_message && <p className="enterprise-error">{run.error_message}</p>}
        <TrainingEvaluation metrics={run.metrics}/>
      </article>)}
    </section>
  </div>;
}

export function EnterpriseTraining({ model, onPublished }) {
  const [activeView, setActiveView] = useState(() => model?.ready ? "append" : "standard");
  return <section className="forecast-training-page">
    <nav className="forecast-training-view-tabs" aria-label="模型训练方式">
      <button className={activeView === "standard" ? "active" : ""} onClick={() => setActiveView("standard")}>
        <strong>标准数据建模</strong><span>首次接入与复杂数据</span>
      </button>
      <button className={activeView === "append" ? "active" : ""} onClick={() => setActiveView("append")}>
        <strong>快速追加</strong><span>模型上线后的日常更新</span>
      </button>
    </nav>
    {activeView === "append"
      ? <ForecastQuickAppend model={model} onPublished={onPublished} onUseStandard={() => setActiveView("standard")} />
      : <StandardEnterpriseTraining model={model} onPublished={onPublished} />}
  </section>;
}
