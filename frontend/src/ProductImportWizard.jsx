import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowsClockwise,
  CaretDown,
  Check,
  CheckCircle,
  DownloadSimple,
  FileArrowUp,
  FileXls,
  PencilSimple,
  SlidersHorizontal,
  SpinnerGap,
  ShieldCheck,
  Trash,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, downloadApiFile, idempotencyKey, uploadApiForm } from "./api.js";

const DICTIONARIES = {
  category_code: ["sofa", "chair", "table", "bed", "storage", "other"],
  lifecycle_status: ["concept", "sample", "active", "discontinued"],
  dimension_unit: ["mm", "cm", "m", "in"],
  weight_unit: ["g", "kg", "lb"],
  currency: ["CNY", "USD", "EUR", "GBP"],
};

const STATUS_LABELS = {
  preflighting: "预检中",
  ready: "可提交",
  blocked: "存在阻断错误",
  importing: "写入中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const CATEGORY_LABELS = {
  sofa: "沙发",
  chair: "椅类",
  table: "桌类",
  bed: "床类",
  storage: "收纳",
  other: "其他",
};

const ACTION_LABELS = {
  create: "新增",
  update: "更新",
  skip: "跳过",
  invalid: "不可导入",
};

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function shortSha(value) {
  return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : "—";
}

function formatDateTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function issueCounts(job) {
  const counts = {};
  (job?.rows || []).forEach((row) => {
    [...row.validation_errors, ...row.validation_warnings].forEach((issue) => {
      counts[issue.code] = (counts[issue.code] || 0) + 1;
    });
  });
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

function InspectionIssues({
  inspection,
  mapping,
  setMapping,
  dictionaryMapping,
  setDictionaryMapping,
  unitMapping,
  setUnitMapping,
}) {
  const setValue = (code, source, target) => {
    if (code === "dimension_unit" || code === "weight_unit") {
      setUnitMapping((current) => {
        const next = { ...current };
        if (target) next[source] = target;
        else delete next[source];
        return next;
      });
      return;
    }
    setDictionaryMapping((current) => {
      const next = { ...current, [code]: { ...(current[code] || {}) } };
      if (target) next[code][source] = target;
      else delete next[code][source];
      return next;
    });
  };

  return (
    <section className="import-resolution">
      <header>
        <div><WarningCircle /><span><h3>需要确认 {inspection.mapping_issues.length + inspection.value_issues.length} 个问题</h3><p>只修正服务端无法确定的字段，其余列已自动处理。</p></span></div>
      </header>
      {inspection.warnings.map((warning) => <p className="import-structure-warning" key={warning}><WarningCircle />{warning}</p>)}
      <div className="import-resolution-list">
        {inspection.mapping_issues.map((issue) => {
          const options = [...new Set([...(issue.candidates || []), ...inspection.headers])];
          return <label key={issue.field_code} className="import-resolution-row">
            <span><strong>{issue.title}{issue.required && <b>必填</b>}</strong><small>{issue.message}</small></span>
            <select value={mapping[issue.field_code] || issue.current_header || ""} onChange={(event) => setMapping((current) => ({ ...current, [issue.field_code]: event.target.value }))}>
              <option value="">{issue.required ? "请选择文件中的对应列" : "忽略该字段"}</option>
              {options.map((header) => <option key={header} value={header}>{header}</option>)}
            </select>
          </label>;
        })}
        {inspection.value_issues.flatMap((issue) => issue.source_values.map((source) => {
          const value = issue.field_code === "dimension_unit" || issue.field_code === "weight_unit"
            ? unitMapping[source] || ""
            : dictionaryMapping[issue.field_code]?.[source] || "";
          return <label key={`${issue.field_code}-${source}`} className="import-resolution-row">
            <span><strong>{issue.title}</strong><small>文件值“{source}”需要转换为标准代码</small></span>
            <select value={value} onChange={(event) => setValue(issue.field_code, source, event.target.value)}>
              <option value="">请选择标准代码</option>
              {issue.allowed_values.map((option) => <option key={option} value={option}>{issue.field_code === "category_code" ? `${option} · ${CATEGORY_LABELS[option]}` : option}</option>)}
            </select>
          </label>;
        }))}
      </div>
    </section>
  );
}

function RowEditor({ row, onSaved }) {
  const [values, setValues] = useState(row.normalized_values);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setValues(row.normalized_values), [row]);
  const update = (key) => (event) => setValues((current) => ({ ...current, [key]: event.target.value || null }));
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const next = await api(`/api/v1/product-imports/${row.job_uuid}/rows/${row.source_row_number}`, {
        method: "PATCH",
        body: JSON.stringify({ normalized_values: values }),
      });
      onSaved(next);
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  };
  const issues = [...row.validation_errors, ...row.validation_warnings];
  return (
    <tr className={row.validation_errors.length ? "has-error" : row.validation_warnings.length ? "has-warning" : ""}>
      <td className="row-number">{row.source_row_number}</td>
      <td><input value={values.sku || ""} onChange={update("sku")} aria-label={`第 ${row.source_row_number} 行 SKU`} /></td>
      <td><input value={values.name || ""} onChange={update("name")} aria-label={`第 ${row.source_row_number} 行产品名称`} /></td>
      <td><select value={values.category_code || ""} onChange={update("category_code")}><option value="">请选择</option>{DICTIONARIES.category_code.map((item) => <option key={item} value={item}>{item}</option>)}</select></td>
      <td><select value={values.lifecycle_status || "active"} onChange={update("lifecycle_status")}>{DICTIONARIES.lifecycle_status.map((item) => <option key={item} value={item}>{item}</option>)}</select></td>
      {["length", "width", "height"].map((key) => <td key={key}><input type="number" min="0" value={values[key] || ""} onChange={update(key)} /></td>)}
      <td><select value={values.dimension_unit || ""} onChange={update("dimension_unit")}><option value="">—</option>{DICTIONARIES.dimension_unit.map((item) => <option key={item} value={item}>{item}</option>)}</select></td>
      <td><span className={`import-action ${row.planned_action}`}>{ACTION_LABELS[row.planned_action] || row.planned_action}</span></td>
      <td className="row-issues">{issues.length ? issues.map((issue) => <span key={`${issue.code}-${issue.field}`} title={issue.suggestion}>{issue.message}</span>) : <span className="ok"><Check />通过</span>}{error && <span>{error}</span>}</td>
      <td><button className="icon-save-row" onClick={save} disabled={saving} title="保存该行修正"><PencilSimple />{saving ? "保存中" : "保存"}</button></td>
    </tr>
  );
}

export function ProductImportWizard({ onClose, onImported }) {
  const [stage, setStage] = useState("setup");
  const [file, setFile] = useState(null);
  const [inspection, setInspection] = useState(null);
  const [mapping, setMapping] = useState({});
  const [unitMapping, setUnitMapping] = useState({});
  const [dictionaryMapping, setDictionaryMapping] = useState({});
  const [importMode, setImportMode] = useState("create_only");
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [recent, setRecent] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [progress, setProgress] = useState({ percent: 0, label: "" });
  const [confirmed, setConfirmed] = useState(false);
  const [templateMeta, setTemplateMeta] = useState(null);

  const loadRecent = () => api("/api/v1/product-imports/recent?limit=6")
    .then((data) => setRecent(data.items || []))
    .catch(() => setRecent([]));
  useEffect(() => {
    loadRecent();
  }, []);

  const mappedValues = useMemo(() => Object.fromEntries(
    Object.entries(mapping).filter(([, header]) => header),
  ), [mapping]);
  const unresolvedRequired = inspection?.mapping_issues.some(
    (issue) => issue.required && !mappedValues[issue.field_code],
  ) || false;
  const unresolvedValues = inspection?.value_issues.some((issue) => issue.source_values.some((source) => (
    issue.field_code === "dimension_unit" || issue.field_code === "weight_unit"
      ? !unitMapping[source]
      : !dictionaryMapping[issue.field_code]?.[source]
  ))) || false;
  const resolutionReady = Boolean(
    inspection
    && !unresolvedRequired
    && !unresolvedValues
    && !(inspection.warnings || []).length,
  );
  const stats = useMemo(() => issueCounts(job), [job]);
  const fileErrors = job?.schema_snapshot?.file_errors || [];
  const aliasErrors = job?.schema_snapshot?.alias_errors || [];

  const downloadTemplate = async () => {
    setBusy(true);
    setMessage("");
    try {
      const { blob, response } = await downloadApiFile("/api/v1/product-imports/template");
      const meta = {
        version: response.headers.get("x-template-version"),
        fileSha: response.headers.get("x-content-sha256"),
        schemaSha: response.headers.get("x-schema-sha256"),
      };
      setTemplateMeta(meta);
      saveBlob(blob, `FurniScope-Product-Master-${meta.version || "current"}.xlsx`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const runPreflight = async (
    nextInspection = inspection,
    nextFile = file,
    nextMapping = mappedValues,
    nextUnitMapping = unitMapping,
    nextDictionaryMapping = dictionaryMapping,
    fallbackStage = "resolve",
  ) => {
    if (!nextFile || !nextInspection) return setMessage("请重新上传 XLSX 文件");
    setBusy(true);
    setStage("inspecting");
    setMessage("");
    setProgress({ percent: 45, label: "结构识别完成，正在执行全量预检" });
    const form = new FormData();
    form.append("file", nextFile);
    form.append("sheet_name", nextInspection.sheet_name);
    form.append("header_row", String(nextInspection.header_row));
    form.append("field_mapping", JSON.stringify(nextMapping));
    form.append("unit_mapping", JSON.stringify(nextUnitMapping));
    form.append("dictionary_mapping", JSON.stringify(nextDictionaryMapping));
    form.append("import_mode", importMode);
    try {
      const next = await uploadApiForm("/api/v1/product-imports:preflight", form, {
        headers: { "Idempotency-Key": idempotencyKey("product-import-preflight") },
        onProgress: (ratio) => setProgress({
          percent: 45 + Math.round(ratio * 50),
          label: ratio >= 1 ? "文件上传完成，服务端正在执行全量校验" : "正在上传文件",
        }),
      });
      setProgress({ percent: 100, label: "预检完成" });
      setJob(next);
      setStage("preview");
      setConfirmed(false);
      loadRecent();
    } catch (error) {
      setMessage(error.message);
      setProgress({ percent: 0, label: "" });
      setStage(fallbackStage);
    } finally {
      setBusy(false);
    }
  };

  const selectFile = async (nextFile) => {
    setFile(nextFile);
    setInspection(null);
    setMapping({});
    setUnitMapping({});
    setDictionaryMapping({});
    setJob(null);
    setResult(null);
    setMessage("");
    setProgress({ percent: 0, label: "" });
    if (!nextFile) return setStage("setup");
    if (!nextFile.name.toLowerCase().endsWith(".xlsx")) {
      setStage("setup");
      return setMessage("仅支持 .xlsx 文件");
    }
    if (nextFile.size > 20 * 1024 * 1024) {
      setStage("setup");
      return setMessage("文件不能超过 20 MB");
    }
    setBusy(true);
    setStage("inspecting");
    setProgress({ percent: 3, label: "正在上传并识别工作簿结构" });
    const form = new FormData();
    form.append("file", nextFile);
    try {
      const next = await uploadApiForm("/api/v1/product-imports:inspect", form, {
        onProgress: (ratio) => setProgress({
          percent: Math.max(3, Math.round(ratio * 40)),
          label: ratio >= 1 ? "上传完成，正在识别 Sheet、表头和字段" : "正在上传文件",
        }),
      });
      const nextMapping = { ...(next.field_mapping || {}) };
      const nextUnitMapping = { ...(next.unit_mapping || {}) };
      const nextDictionaryMapping = { ...(next.dictionary_mapping || {}) };
      setInspection(next);
      setMapping(nextMapping);
      setUnitMapping(nextUnitMapping);
      setDictionaryMapping(nextDictionaryMapping);
      const needsResolution = next.mapping_issues.length
        || next.value_issues.length
        || next.warnings.length;
      if (needsResolution) {
        setProgress({ percent: 100, label: "自动识别完成，需要确认少量问题" });
        setStage("resolve");
      } else {
        await runPreflight(
          next,
          nextFile,
          nextMapping,
          nextUnitMapping,
          nextDictionaryMapping,
          "setup",
        );
      }
    } catch (error) {
      setMessage(error.message);
      setProgress({ percent: 0, label: "" });
      setStage("setup");
    } finally {
      setBusy(false);
    }
  };

  const resume = async (item) => {
    setBusy(true);
    setMessage("");
    try {
      const next = await api(`/api/v1/product-imports/${item.job_uuid}?row_limit=500`);
      setJob(next);
      setStage(next.status === "completed" ? "done" : "preview");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    setBusy(true);
    try {
      const next = await api(`/api/v1/product-imports/${job.job_uuid}:cancel`, { method: "POST" });
      setJob(next);
      setMessage("任务已取消，原文件和预检记录仍保留在审计日志中");
      loadRecent();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const downloadErrors = async () => {
    setBusy(true);
    try {
      const { blob } = await downloadApiFile(`/api/v1/product-imports/${job.job_uuid}/error-report`);
      saveBlob(blob, `product-import-errors-${job.job_uuid}.xlsx`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    setBusy(true);
    setMessage("");
    try {
      const imported = await api(`/api/v1/product-imports/${job.job_uuid}:commit`, {
        method: "POST",
        body: JSON.stringify({ preview_sha256: job.preview_sha256 }),
      });
      setResult(imported);
      setStage("done");
      await onImported?.();
      loadRecent();
    } catch (error) {
      setMessage(error.message);
      if (/预览|变化/.test(error.message)) await resume(job);
    } finally {
      setBusy(false);
    }
  };

  const close = () => {
    onClose();
  };

  const resetUpload = () => {
    setStage("setup");
    setFile(null);
    setInspection(null);
    setMapping({});
    setUnitMapping({});
    setDictionaryMapping({});
    setJob(null);
    setResult(null);
    setConfirmed(false);
    setProgress({ percent: 0, label: "" });
    setMessage("");
  };

  return (
    <div className="product-modal-backdrop import-wizard-backdrop" onMouseDown={close}>
      <section className="product-import-wizard" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><span>CONTROLLED PRODUCT IMPORT</span><h2>产品主档批量导入</h2><p>上传 Excel，系统完成识别与预检；如有歧义，只需确认问题字段。</p></div>
          <button type="button" onClick={close} aria-label="关闭导入向导"><X /></button>
        </header>
        <nav className="import-steps" aria-label="导入步骤">
          {["上传文件", "系统预检", "确认导入"].map((label, index) => {
            const active = stage === "setup" ? 0 : ["inspecting", "resolve"].includes(stage) ? 1 : 2;
            return <span className={index === active ? "active" : index < active ? "done" : ""} key={label}><i>{index < active ? <Check /> : index + 1}</i>{label}</span>;
          })}
        </nav>
        <div className="import-wizard-body">
          {stage === "setup" && <>
            <section className="import-template-band">
              <FileXls />
              <div><strong>没有现成文件？可使用标准模板</strong><span>模板包含必填字段、受控词表和填写示例，下载不是导入前置条件。</span>{templateMeta && <small>版本 {templateMeta.version} · 文件 SHA256 {shortSha(templateMeta.fileSha)} · Schema {shortSha(templateMeta.schemaSha)}</small>}</div>
              <button type="button" onClick={downloadTemplate} disabled={busy}><DownloadSimple />下载模板（可选）</button>
            </section>
            <section className="import-upload-first">
              <header><div><h3>上传产品主档</h3><p>选择文件后立即开始预检，系统会处理 Sheet、表头、字段、数据类型、单位和枚举。</p></div></header>
              <label className="import-file-picker primary"><input type="file" disabled={busy} accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => selectFile(event.target.files?.[0] || null)} /><FileArrowUp /><strong>拖拽或点击上传产品主档</strong><span>支持 20 MB 内 XLSX，最多 20 个 Sheet、20,000 行</span></label>
              <details className="import-advanced-options">
                <summary>
                  <span><SlidersHorizontal />高级设置</span>
                  <small>重复 SKU：{importMode === "create_only" ? "跳过" : "更新"}</small>
                  <CaretDown />
                </summary>
                <div className="import-advanced-content">
                  <div className="import-mode-field">
                    <span>重复 SKU 处理方式</span>
                    <div className="import-mode-buttons" role="group" aria-label="重复 SKU 处理方式">
                      <button type="button" className={importMode === "create_only" ? "selected" : ""} aria-pressed={importMode === "create_only"} onClick={() => setImportMode("create_only")}>
                        <span><strong>仅新增</strong><small>已有 SKU 跳过</small></span>
                        <i><Check /></i>
                      </button>
                      <button type="button" className={importMode === "upsert" ? "selected" : ""} aria-pressed={importMode === "upsert"} onClick={() => setImportMode("upsert")}>
                        <span><strong>新增并更新</strong><small>已有 SKU 按文件更新</small></span>
                        <i><Check /></i>
                      </button>
                    </div>
                    <small>{importMode === "create_only"
                      ? "默认方式。已有 SKU 不修改，只创建文件中的新 SKU。"
                      : "新 SKU 将创建；已有 SKU 仅更新文件中实际提供的字段。"}</small>
                  </div>
                  {importMode === "upsert" && <p className="import-mode-warning"><WarningCircle />更新已有 SKU 会创建新的草稿画像，请在预检中确认变更。</p>}
                </div>
              </details>
            </section>
            {recent.some((item) => !["completed", "cancelled"].includes(item.status)) && <section className="import-recent">
              <header><div><h3>未完成任务</h3><p>服务中断或关闭页面后可从这里恢复。</p></div></header>
              {recent.filter((item) => !["completed", "cancelled"].includes(item.status)).map((item) => <article key={item.job_uuid}><FileXls /><div><strong>{item.original_filename}</strong><span>{STATUS_LABELS[item.status]} · {item.total_rows} 行 · {formatDateTime(item.updated_at)}</span></div><button type="button" onClick={() => resume(item)} disabled={busy}><ArrowsClockwise />恢复</button></article>)}
            </section>}
          </>}

          {stage === "inspecting" && <section className="import-inspecting">
            <span><SpinnerGap /></span>
            <h3>{progress.label || "正在识别产品主档"}</h3>
            <p>{file?.name} · 自动分析 Sheet、表头和字段口径</p>
            <div><i style={{ width: `${progress.percent}%` }} /></div>
            <strong>{progress.percent}%</strong>
          </section>}

          {stage === "resolve" && inspection && <>
            <section className="import-detection-summary">
              <header><div><CheckCircle /><span><h3>文件结构识别完成</h3><p>{file?.name}</p></span></div><b>{inspection.total_rows} 行</b></header>
              <dl>
                <div><dt>数据工作表</dt><dd>{inspection.sheet_name}</dd></div>
                <div><dt>表头位置</dt><dd>第 {inspection.header_row} 行</dd></div>
                <div><dt>已识别字段</dt><dd>{inspection.detected_fields.length} 项</dd></div>
                <div><dt>自动忽略</dt><dd>{inspection.ignored_columns.length} 列</dd></div>
              </dl>
            </section>
            <InspectionIssues
              inspection={inspection}
              mapping={mapping}
              setMapping={setMapping}
              dictionaryMapping={dictionaryMapping}
              setDictionaryMapping={setDictionaryMapping}
              unitMapping={unitMapping}
              setUnitMapping={setUnitMapping}
            />
          </>}

          {stage === "preview" && job && <>
            <section className="import-summary-strip">
              <div><span>任务状态</span><strong className={job.status}>{STATUS_LABELS[job.status]}</strong></div>
              <div><span>总行数</span><strong>{job.total_rows}</strong></div>
              <div><span>通过校验</span><strong>{job.valid_rows}</strong></div>
              <div><span>阻断错误</span><strong className={job.error_rows ? "danger" : ""}>{job.error_rows}</strong></div>
              <div><span>警告行</span><strong className={job.warning_rows ? "warning" : ""}>{job.warning_rows}</strong></div>
              <div><span>预览 SHA256</span><code title={job.preview_sha256}>{shortSha(job.preview_sha256)}</code></div>
            </section>
            {(fileErrors.length || aliasErrors.length || stats.length) > 0 && <section className="import-issue-summary">
              <header><div><h3>校验结果</h3><p>阻断错误必须修正；警告允许提交，但应先确认业务含义。</p></div>{(job.error_rows || fileErrors.length || aliasErrors.length) > 0 && <button type="button" onClick={downloadErrors} disabled={busy}><DownloadSimple />错误 Excel</button>}</header>
              <div>{[...fileErrors, ...aliasErrors].map((issue, index) => <span className="blocking" key={`${issue.code}-${index}`}><WarningCircle />{issue.message}</span>)}{stats.map(([code, count]) => <span key={code}><b>{count}</b>{code}</span>)}</div>
            </section>}
            <section className="import-preview-section">
              <header><div><h3>标准化预览</h3><p>仅支持前 20 行在线快速修正；其余阻断行请下载错误 Excel，离线修复后重新上传。修正会重新计算整批重复、库内冲突和预览 SHA。</p></div><span>{job.rows.length > 20 ? `共读取 ${job.rows.length} 行，仅显示前 20 行` : `${job.rows.length} 行`}</span></header>
              <div className="import-preview-table"><table><thead><tr><th>原行</th><th>SKU</th><th>产品名称</th><th>品类</th><th>生命周期</th><th>长</th><th>宽</th><th>高</th><th>单位</th><th>动作</th><th>错误 / 警告</th><th>操作</th></tr></thead><tbody>{job.rows.slice(0, 20).map((row) => <RowEditor key={row.source_row_number} row={{ ...row, job_uuid: job.job_uuid }} onSaved={(next) => { setJob(next); setConfirmed(false); }} />)}</tbody></table></div>
            </section>
            <section className="import-commit-check">
              <ShieldCheck />
              <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={job.status !== "ready"} /><span><strong>确认以当前标准化预览执行事务写入</strong><small>任何一行失败都会回滚整批数据；提交使用上方 SHA256 锁定当前预览。</small></span></label>
            </section>
          </>}

          {stage === "done" && <section className="import-complete">
            <CheckCircle weight="fill" />
            <span>TRANSACTION COMMITTED</span>
            <h3>产品主档已完成事务写入</h3>
            <p>任务 {result?.job_uuid || job?.job_uuid}</p>
            <div><strong>{result?.imported_rows ?? job?.imported_rows ?? 0}<small>写入总数</small></strong><strong>{result?.created_rows ?? job?.schema_snapshot?.created_rows ?? 0}<small>新增产品</small></strong><strong>{result?.updated_rows ?? job?.schema_snapshot?.updated_rows ?? 0}<small>更新产品</small></strong><strong>{result?.skipped_rows ?? job?.schema_snapshot?.skipped_rows ?? 0}<small>跳过已有</small></strong><strong>{result?.alias_rows ?? job?.schema_snapshot?.alias_rows ?? 0}<small>SKU 别名</small></strong></div>
          </section>}
          {message && <p className="import-message" role="alert"><WarningCircle />{message}</p>}
        </div>
        <footer>
          {stage === "resolve" && <button type="button" onClick={resetUpload} disabled={busy}><ArrowLeft />重新选择文件</button>}
          {stage === "preview" && <button type="button" onClick={resetUpload} disabled={busy}><ArrowLeft />重新上传</button>}
          {stage === "preview" && !["completed", "cancelled"].includes(job?.status) && <button type="button" className="danger-action" onClick={cancel} disabled={busy}><Trash />取消任务</button>}
          <span />
          <button type="button" onClick={close}>{stage === "done" ? "关闭" : "稍后处理"}</button>
          {stage === "resolve" && <button type="button" className="primary-save" onClick={() => runPreflight()} disabled={busy || !resolutionReady}>{busy ? "处理中…" : "应用修正并重新预检"}</button>}
          {stage === "preview" && <button type="button" className="primary-save" onClick={commit} disabled={busy || !confirmed || job.status !== "ready"}>{busy ? "提交中…" : "确认事务写入"}</button>}
        </footer>
      </section>
    </div>
  );
}
