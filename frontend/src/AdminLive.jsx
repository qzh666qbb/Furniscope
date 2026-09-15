import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise, Brain, Buildings, CheckCircle, ClockCounterClockwise, Key,
  MagnifyingGlass, Plus, ShieldCheck, SignOut, Trash, UploadSimple, WarningCircle, X,
} from "@phosphor-icons/react";
import { api, clearSession, idempotencyKey } from "./api.js";

const tabs = [[Buildings, "企业用户"], [Brain, "预测模型"]];
const tenantLabels = { active: "正常", trial: "试用", suspended: "已暂停", closed: "已删除" };
const userLabels = { active: "可登录", invited: "待激活", disabled: "已停用", locked: "已锁定" };
const trainingLabels = { queued: "排队中", running: "训练中", succeeded: "已发布", failed: "失败", cancelled: "已取消" };
const formatDate = value => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";

function State({ loading, error, empty, onRetry, children }) {
  if (loading) return <div className="admin-empty"><ArrowsClockwise className="spin" />正在读取平台数据…</div>;
  if (error) return <div className="admin-empty"><WarningCircle />{error}<button onClick={onRetry}>重试</button></div>;
  if (empty) return <div className="admin-empty">暂无企业数据</div>;
  return children;
}

function EnterpriseCreateModal({ onClose, onCreated }) {
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const submit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const values = Object.fromEntries(form);
    const entitlements = form.getAll("entitlements");
    try {
      setSaving(true); setError("");
      await api("/api/v1/admin/enterprise-users", {
        method: "POST", headers: { "Idempotency-Key": idempotencyKey("enterprise") },
        body: JSON.stringify({ ...values, tenant_code: values.tenant_code.toUpperCase(), entitlements }),
      });
      onCreated();
    } catch (err) { setError(err.message); }
    finally { setSaving(false); }
  };
  return <div className="admin-modal-backdrop" onClick={onClose}><form className="admin-form-modal enterprise-create" onSubmit={submit} onClick={e => e.stopPropagation()}>
    <header><div><h2>开通企业用户</h2><p>保存后将同时创建独立租户和企业登录账号。</p></div><button type="button" onClick={onClose}><X /></button></header>
    <div className="field-grid"><label>企业名称<input name="enterprise_name" required placeholder="例如：杭州某某家居有限公司" /></label><label>租户编码<input name="tenant_code" required pattern="[A-Za-z0-9_]{2,32}" placeholder="例如：HZ_FURNITURE" /></label><label>联系人<input name="contact_name" required placeholder="企业账号使用人" /></label><label>登录邮箱<input name="email" type="email" required placeholder="name@company.com" /></label></div>
    <label>初始密码<input name="initial_password" type="password" required minLength="12" autoComplete="new-password" placeholder="至少 12 位，由管理员安全交付" /></label>
    <fieldset><legend>功能授权</legend><label><input type="checkbox" name="entitlements" value="sales_forecast" defaultChecked />销量预测</label><label><input type="checkbox" name="entitlements" value="market_analysis" />市场分析</label></fieldset>
    {error && <p className="admin-form-error"><WarningCircle />{error}</p>}
    <footer><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={saving}>{saving ? "正在开通…" : "开通企业用户"}</button></footer>
  </form></div>;
}

function ApproveApplicationModal({ item, onClose, onApproved }) {
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const submit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const entitlements = form.getAll("entitlements");
    try {
      setSaving(true); setError("");
      await api(`/api/v1/admin/registration-applications/${item.id}:approve`, {
        method: "POST", headers: { "Idempotency-Key": idempotencyKey("approve") },
        body: JSON.stringify({ tenant_code: String(form.get("tenant_code") || "").toUpperCase(), entitlements }),
      });
      onApproved();
    } catch (err) { setError(err.message); }
    finally { setSaving(false); }
  };
  return <div className="admin-modal-backdrop" onClick={onClose}><form className="admin-form-modal enterprise-create" onSubmit={submit} onClick={e => e.stopPropagation()}>
    <header><div><h2>审批开通 {item.enterprise_name}</h2><p>通过后将按申请信息创建独立租户，申请人可用原密码登录。</p></div><button type="button" onClick={onClose}><X /></button></header>
    <div className="field-grid"><label>企业名称<input value={item.enterprise_name} readOnly /></label><label>租户编码<input name="tenant_code" required pattern="[A-Za-z0-9_]{2,32}" defaultValue={item.suggested_tenant_code} /></label><label>联系人<input value={item.contact_name} readOnly /></label><label>登录邮箱<input value={item.email} readOnly /></label></div>
    <fieldset><legend>功能授权</legend><label><input type="checkbox" name="entitlements" value="sales_forecast" defaultChecked />销量预测</label><label><input type="checkbox" name="entitlements" value="market_analysis" defaultChecked />市场分析</label></fieldset>
    {error && <p className="admin-form-error"><WarningCircle />{error}</p>}
    <footer><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={saving}>{saving ? "正在开通…" : "通过并开通"}</button></footer>
  </form></div>;
}

function ResetPasswordModal({ item, onClose, onReset }) {
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const submit = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("new_password") || "");
    const confirm = String(form.get("confirm_password") || "");
    if (password.length < 12) return setError("新密码至少需要 12 位");
    if (password !== confirm) return setError("两次输入的密码不一致");
    try {
      setSaving(true); setError("");
      await api(`/api/v1/admin/enterprise-users/${item.tenant_id}:reset-password?user_id=${item.user_id}`, {
        method: "POST",
        headers: { "If-Match": item.resource_version },
        body: JSON.stringify({ new_password: password }),
      });
      onReset();
    } catch (err) { setError(err.message); }
    finally { setSaving(false); }
  };
  return <div className="admin-modal-backdrop" onClick={onClose}><form className="admin-form-modal enterprise-create" onSubmit={submit} onClick={e => e.stopPropagation()}>
    <header><div><h2>重置 {item.enterprise_name} 的密码</h2><p>将立即作废该账号的已登录会话。请把新密码安全交付给企业联系人。</p></div><button type="button" onClick={onClose}><X /></button></header>
    <label>登录邮箱<input value={item.email || ""} readOnly /></label>
    <div className="field-grid"><label>新密码<input name="new_password" type="password" required minLength="12" autoComplete="new-password" placeholder="至少 12 位" /></label><label>确认密码<input name="confirm_password" type="password" required minLength="12" autoComplete="new-password" placeholder="再次输入" /></label></div>
    {error && <p className="admin-form-error"><WarningCircle />{error}</p>}
    <footer><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={saving}>{saving ? "正在重置…" : "确认重置密码"}</button></footer>
  </form></div>;
}

function DeleteEnterpriseModal({ item, onClose, onDeleted }) {
  const [deleting, setDeleting] = useState(false), [error, setError] = useState("");
  const remove = async () => {
    try {
      setDeleting(true); setError("");
      await api(`/api/v1/admin/enterprise-users/${item.tenant_id}`, {
        method: "DELETE", headers: { "If-Match": item.resource_version },
      });
      onDeleted();
    } catch (err) { setError(err.message); setDeleting(false); }
  };
  return <div className="admin-modal-backdrop" onClick={onClose}><div className="admin-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-enterprise-title" onClick={event => event.stopPropagation()}>
    <Trash />
    <h2 id="delete-enterprise-title">删除企业账号？</h2>
    <p>删除“{item.enterprise_name}”后，该租户下的企业账号会立即退出且无法登录，并从默认企业列表和预测模型列表中移除。业务数据与审计记录会保留，可在“已删除”筛选中恢复。</p>
    {error && <p className="admin-form-error"><WarningCircle />{error}</p>}
    <footer><button type="button" onClick={onClose} disabled={deleting}>取消</button><button type="button" className="danger" onClick={remove} disabled={deleting}>{deleting ? "正在删除…" : "确认删除"}</button></footer>
  </div></div>;
}

function EnterpriseUsersPanel() {
  const [state, setState] = useState({ loading: true, error: "", items: [] });
  const [applications, setApplications] = useState([]);
  const [keyword, setKeyword] = useState(""), [status, setStatus] = useState(""), [creating, setCreating] = useState(false);
  const [approving, setApproving] = useState(null), [deleting, setDeleting] = useState(null), [resetting, setResetting] = useState(null);
  const load = () => {
    setState(previous => ({ ...previous, loading: true, error: "" }));
    const query = new URLSearchParams({ page_size: "100" });
    if (keyword.trim()) query.set("keyword", keyword.trim());
    if (status) query.set("status", status);
    Promise.all([
      api(`/api/v1/admin/enterprise-users?${query}`),
      api("/api/v1/admin/registration-applications?status=pending&page_size=100"),
    ]).then(([data, pending]) => {
      setState({ loading: false, error: "", items: data.items || [] });
      setApplications(pending.items || []);
    }).catch(error => setState({ loading: false, error: error.message, items: [] }));
  };
  useEffect(() => { load(); }, [status]);
  const stats = useMemo(() => ({ total: state.items.length, active: state.items.filter(x => x.tenant_status === "active" && x.user_status === "active").length,
    enabled: state.items.filter(x => (x.entitlements || []).includes("sales_forecast")).length,
    suspended: state.items.filter(x => ["suspended", "closed"].includes(x.tenant_status)).length }), [state.items]);
  const overview = [
    { label: "企业账号", value: stats.total, note: "真实企业登录账号", Icon: Buildings, tone: "tenants" },
    { label: "正常使用", value: stats.active, note: "账号和租户均可用", Icon: CheckCircle, tone: "healthy" },
    { label: "已开通预测", value: stats.enabled, note: "拥有独立模型范围", Icon: Brain, tone: "forecast" },
    { label: "待审批申请", value: applications.length, note: "来自企业注册页", Icon: ClockCounterClockwise, tone: "pending" },
  ];
  const update = async (item, changes) => {
    try {
      await api(`/api/v1/admin/enterprise-users/${item.tenant_id}?user_id=${item.user_id}`, { method: "PATCH",
        headers: { "If-Match": item.resource_version }, body: JSON.stringify(changes) });
      load();
    } catch (error) { setState(previous => ({ ...previous, error: error.message })); }
  };
  const toggleEntitlement = (item, code, enabled) => {
    const current = item.entitlements || [];
    const next = enabled ? [...new Set([...current, code])] : current.filter(x => x !== code);
    update(item, { entitlements: next });
  };
  const reject = async item => {
    const reason = window.prompt(`拒绝 ${item.enterprise_name} 的注册申请，请填写原因：`, "资料不完整，请重新申请");
    if (!reason || !reason.trim()) return;
    try {
      await api(`/api/v1/admin/registration-applications/${item.id}:reject`, {
        method: "POST", body: JSON.stringify({ reason: reason.trim() }),
      });
      load();
    } catch (error) { setState(previous => ({ ...previous, error: error.message })); }
  };
  return <section className="control-module">
    <header className="control-heading"><div><span>ENTERPRISE ACCOUNTS</span><h1>企业用户</h1><p>仅展示数据库中的真实企业账号；同一企业下的账号共享该租户的数据与预测模型。</p></div><button className="primary-action" onClick={() => setCreating(true)}><Plus />开通企业用户</button></header>
    <div className="control-kpis" aria-label="企业用户概览">
      {overview.map(({ label, value, note, Icon, tone }) => (
        <article className={`control-kpi ${tone}`} key={label}>
          <div className="control-kpi-copy"><small>{label}</small><strong>{value}</strong><span>{note}</span></div>
          <i className="control-kpi-icon" aria-hidden="true"><Icon weight="duotone" /></i>
        </article>
      ))}
    </div>
    {applications.length > 0 && <section className="application-queue"><header><strong>待审批注册申请</strong><span>{applications.length} 条</span></header><div className="control-table compact"><table><thead><tr><th>企业</th><th>联系人</th><th>邮箱</th><th>建议租户编码</th><th>提交时间</th><th>操作</th></tr></thead><tbody>{applications.map(item => <tr key={item.id}><td><strong>{item.enterprise_name}</strong></td><td>{item.contact_name}</td><td>{item.email}</td><td><code>{item.suggested_tenant_code}</code></td><td>{formatDate(item.created_at)}</td><td><div className="row-actions"><button className="primary-text" onClick={() => setApproving(item)}>通过</button><button className="danger" onClick={() => reject(item)}>拒绝</button></div></td></tr>)}</tbody></table></div></section>}
    <div className="control-toolbar"><form onSubmit={e => { e.preventDefault(); load(); }}><MagnifyingGlass /><input value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="搜索企业、租户编码或邮箱" /><button>搜索</button></form><select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option><option value="active">正常</option><option value="trial">试用</option><option value="suspended">暂停</option><option value="closed">已删除</option></select><button onClick={load}><ArrowsClockwise />刷新</button></div>
    <State {...state} empty={!state.items.length} onRetry={load}><div className="control-table"><table><thead><tr><th>企业用户</th><th>租户</th><th>账号状态</th><th>功能授权</th><th>当前模型</th><th>最近登录</th><th>操作</th></tr></thead><tbody>{state.items.map(item => <tr key={item.user_id}><td><strong>{item.enterprise_name}</strong><small>{item.contact_name || "未设置联系人"} · {item.email || "无登录账号"}</small></td><td><code>{item.tenant_code}</code><em className={`state ${item.tenant_status}`}>{tenantLabels[item.tenant_status]}</em></td><td><em className={`state ${item.user_status}`}>{userLabels[item.user_status] || "无账号"}</em></td><td><div className="entitlement-stack"><label className="entitlement-toggle"><input type="checkbox" disabled={item.tenant_status === "closed"} checked={(item.entitlements || []).includes("sales_forecast")} onChange={event => toggleEntitlement(item, "sales_forecast", event.target.checked)} /><span />销量预测</label><label className="entitlement-toggle"><input type="checkbox" disabled={item.tenant_status === "closed"} checked={(item.entitlements || []).includes("market_analysis")} onChange={event => toggleEntitlement(item, "market_analysis", event.target.checked)} /><span />市场分析</label></div></td><td><strong>{item.model_version || "待分配"}</strong><small>{item.training_data_through ? `数据至 ${item.training_data_through}` : `${item.sku_count || 0} 个 SKU`}</small></td><td>{formatDate(item.last_login_at)}</td><td><div className="row-actions"><button className={item.user_status === "active" ? "danger" : ""} onClick={() => update(item, item.tenant_status === "closed" ? { tenant_status: "active", user_status: "active" } : { user_status: item.user_status === "active" ? "disabled" : "active" })}>{item.tenant_status === "closed" ? "恢复账号" : item.user_status === "active" ? "停用账号" : "启用账号"}</button>{item.tenant_status !== "closed" && item.email && <button onClick={() => setResetting(item)}><Key />重置密码</button>}{item.tenant_status !== "closed" && <button className="danger" onClick={() => setDeleting(item)}><Trash />删除</button>}</div></td></tr>)}</tbody></table></div></State>
    {creating && <EnterpriseCreateModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load(); }} />}
    {approving && <ApproveApplicationModal item={approving} onClose={() => setApproving(null)} onApproved={() => { setApproving(null); load(); }} />}
    {resetting && <ResetPasswordModal item={resetting} onClose={() => setResetting(null)} onReset={() => { setResetting(null); load(); }} />}
    {deleting && <DeleteEnterpriseModal item={deleting} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); load(); }} />}
  </section>;
}

function ModelUpdateModal({ tenant, onClose, onAccepted }) {
  const [code, setCode] = useState(null), [parameters, setParameters] = useState(null);
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const currentEngine = tenant.engine || "xgboost_lightgbm_v4";
  const submit = async event => {
    event.preventDefault();
    if (!code) return setError("请选择模型代码文件");
    if (!parameters) return setError("请选择模型参数文件");
    const form = new FormData(event.currentTarget);
    form.append("code_file", code); form.append("parameters_file", parameters);
    try {
      setSaving(true); setError("");
      await api(`/api/v1/admin/forecast-models/${tenant.tenant_id}/replace`, { method: "POST", body: form });
      onAccepted();
    } catch (err) { setError(err.message); }
    finally { setSaving(false); }
  };
  return <div className="admin-modal-backdrop" onClick={onClose}><form className="admin-form-modal model-update" onSubmit={submit} onClick={e => e.stopPropagation()}>
    <header><div><h2>更新 {tenant.enterprise_name} 的模型</h2><p>为租户 {tenant.tenant_code} 替换专用预测引擎与参数。</p></div><button type="button" onClick={onClose}><X /></button></header>
    <div className="current-model-summary"><Brain/><span><small>当前使用模型（默认保持不变）</small><strong>{tenant.version || "系统基础模型"}</strong><em>{currentEngine}</em></span></div>
    <div className="field-grid"><label>新模型版本<input name="version" required maxLength="64" placeholder="例如：hefeng-lightgbm-v1" /></label><label>算法方案<select name="algorithm" defaultValue="current"><option value="current">当前方案 · {currentEngine}</option><option value="xgboost">XGBoost</option><option value="lightgbm">LightGBM</option><option value="custom">自定义兼容模型</option></select></label></div>
    <label className="control-upload"><UploadSimple /><span><strong>模型代码（必选）</strong><small>{code ? code.name : "选择审核通过的 .py 文件"}</small></span><input type="file" accept=".py,text/x-python" onChange={e => setCode(e.target.files?.[0] || null)} /></label>
    <label className="control-upload"><UploadSimple /><span><strong>模型参数（必选）</strong><small>{parameters ? parameters.name : "选择 .json 参数文件"}</small></span><input type="file" accept=".json,application/json" onChange={e => setParameters(e.target.files?.[0] || null)} /></label>
    <label>变更说明<textarea name="change_note" maxLength="500" placeholder="说明更换原因、适用业务场景和验证结论" /></label>
    <div className="publish-note"><ShieldCheck /><span><strong>校验后切换</strong>代码必须实现 FurniScope 预测接口。系统会使用该租户现有数据重新训练并做就绪校验；只有通过后才替换当前模型。</span></div>
    {error && <p className="admin-form-error"><WarningCircle />{error}</p>}
    <footer><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={!code || !parameters || saving}>{saving ? "正在校验并切换…" : "上传并切换模型"}</button></footer>
  </form></div>;
}

function ForecastModelsPanel() {
  const [state, setState] = useState({ loading: true, error: "", items: [] });
  const [selected, setSelected] = useState(null), [detail, setDetail] = useState(null), [updating, setUpdating] = useState(false);
  const load = () => api("/api/v1/admin/forecast-models").then(data => {
    const items = data.items || []; setState({ loading: false, error: "", items });
    setSelected(previous => items.find(x => x.tenant_id === previous?.tenant_id) || items[0] || null);
  }).catch(error => setState({ loading: false, error: error.message, items: [] }));
  const loadDetail = tenant => tenant && api(`/api/v1/admin/forecast-models/${tenant.tenant_id}`).then(setDetail)
    .catch(error => setState(previous => ({ ...previous, error: error.message })));
  useEffect(() => { load(); }, []);
  useEffect(() => { setDetail(null); loadDetail(selected); }, [selected?.tenant_id]);
  useEffect(() => {
    if (!detail?.training_runs?.some(run => ["queued", "running"].includes(run.status))) return undefined;
    const timer = setInterval(() => { load(); loadDetail(selected); }, 3000); return () => clearInterval(timer);
  }, [detail, selected?.tenant_id]);
  const rollback = async deployment => {
    if (!window.confirm(`确认将 ${selected.enterprise_name} 回滚到 ${deployment.version}？新预测任务将立即使用该版本。`)) return;
    try { await api(`/api/v1/admin/forecast-models/${selected.tenant_id}/deployments/${deployment.deployment_uuid}:rollback`, { method: "POST" }); await load(); await loadDetail(selected); }
    catch (error) { setState(previous => ({ ...previous, error: error.message })); }
  };
  const active = detail?.deployments?.find(x => x.deployment_status === "active");
  return <section className="control-module">
    <header className="control-heading"><div><span>MODEL DEPLOYMENTS</span><h1>预测模型</h1><p>企业开户后自动生成模型记录；在各企业模型空间内维护专用模型。</p></div></header>
    <State {...state} empty={!state.items.length} onRetry={load}><div className="model-control-grid"><aside className="tenant-model-list"><header><strong>企业模型</strong><span>{state.items.length}</span></header>{state.items.map(item => <button className={selected?.tenant_id === item.tenant_id ? "active" : ""} onClick={() => setSelected(item)} key={item.tenant_id}><span><strong>{item.enterprise_name}</strong><small>{item.tenant_code}</small></span><em className={item.latest_training_status || "ready"}>{item.latest_training_status ? trainingLabels[item.latest_training_status] : item.version ? "已就绪" : "待配置"}</em></button>)}</aside>
      <main className="model-detail">{selected && <><header><div><span className="model-company-mark">{selected.enterprise_name.slice(0, 1)}</span><div><h2>{selected.enterprise_name}</h2><p>{selected.tenant_code} · 企业私有模型空间</p></div></div><div className="model-space-actions"><em className={`state ${selected.tenant_status}`}>{tenantLabels[selected.tenant_status]}</em><button className="primary-action" onClick={() => setUpdating(true)} disabled={selected.tenant_status !== "active"}><UploadSimple />更新模型</button></div></header>
        <div className="model-kpis"><article><small>当前版本</small><strong>{active?.version || selected.version || "尚未部署"}</strong><span>{active?.model_scope === "tenant_private" ? "租户私有模型" : "系统基础模型"}</span></article><article><small>数据更新至</small><strong>{active?.training_data_through || selected.training_data_through || "—"}</strong><span>{selected.sku_count || 0} 个 SKU 可用</span></article><article><small>最近发布</small><strong>{formatDate(active?.deployed_at || selected.deployed_at)}</strong><span>失败不会替换正式版本</span></article></div>
        <section className="version-section"><header><div><h3>版本历史</h3><p>当前版本被冻结用于已创建的预测任务。</p></div><ClockCounterClockwise /></header><div className="control-table compact"><table><thead><tr><th>版本</th><th>范围</th><th>训练数据</th><th>发布时间</th><th>状态</th><th>操作</th></tr></thead><tbody>{(detail?.deployments || []).map(version => <tr key={version.deployment_uuid}><td><strong>{version.version}</strong><small>{version.engine}</small></td><td>{version.model_scope === "tenant_private" ? "企业私有" : "系统基础"}</td><td>{version.training_data_through || "—"}</td><td>{formatDate(version.deployed_at)}</td><td><em className={`state ${version.deployment_status}`}>{version.deployment_status === "active" ? "使用中" : "历史版本"}</em></td><td>{version.deployment_status !== "active" && <button className="text-action" onClick={() => rollback(version)}>回滚</button>}</td></tr>)}</tbody></table></div></section>
        <section className="training-section"><header><div><h3>训练记录</h3><p>查看每家企业的数据追加和发布结果。</p></div><ArrowsClockwise /></header>{detail?.training_runs?.length ? <div>{detail.training_runs.map(run => <article key={run.training_uuid}><CheckCircle /><span><strong>{run.orders_filename || "订单数据"}</strong><small>{formatDate(run.created_at)}{run.inventory_filename ? ` · 库存 ${run.inventory_filename}` : ""}</small></span><em className={run.status}>{trainingLabels[run.status]}</em>{run.status === "failed" && <p className="run-error">{run.error_message}</p>}{run.status === "succeeded" && <p className="run-metrics">新增 {run.metrics?.inserted_rows ?? 0} 行 · 覆盖 {run.metrics?.overwritten_rows ?? 0} 行 · 数据至 {run.metrics?.after_last_date || "—"}</p>}</article>)}</div> : <p className="inline-empty">尚无训练记录</p>}</section>
      </>}</main></div></State>
    {updating && selected && <ModelUpdateModal tenant={{ ...selected, ...(active || {}) }} onClose={() => setUpdating(false)} onAccepted={() => { setUpdating(false); load(); loadDetail(selected); }} />}
  </section>;
}

export function LiveAdminControlCenter() {
  const [active, setActive] = useState("企业用户");
  return <div className="admin-control admin-control-v2"><div className="admin-shell"><aside className="admin-sidebar"><div className="admin-brand"><img src="./assets/furniscope-mark.png" alt=""/><span>FurniScope</span></div><div className="admin-badge"><ShieldCheck />平台管理模式</div><nav className="admin-only-nav">{tabs.map(([Icon, label]) => <button className={active === label ? "active" : ""} onClick={() => setActive(label)} key={label}><Icon/><span>{label}</span></button>)}</nav><div className="admin-health"><strong>隔离状态</strong><span><i/>租户边界生效</span><small>企业数据与模型独立部署</small><button onClick={() => { clearSession(); location.hash="admin-login"; }}><SignOut />退出管理后台</button></div></aside><main className="admin-main-area"><header className="admin-topbar"><div><strong>平台控制中心</strong><small>只管理企业账号与预测模型</small></div></header><section className="admin-content">{active === "企业用户" ? <EnterpriseUsersPanel/> : <ForecastModelsPanel/>}</section></main></div></div>;
}
