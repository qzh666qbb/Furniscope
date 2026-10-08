import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowClockwise,
  BellRinging,
  Check,
  LinkSimple,
  Plus,
  ShieldWarning,
  SpinnerGap,
  Trash,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api } from "./api.js";

const messageOf = (error) => error instanceof Error ? error.message : String(error);
const formatDate = (value) => value
  ? new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
  : "尚未同步";
const plainText = (value) => {
  if (!value) return "";
  if (typeof DOMParser === "undefined") return String(value).replace(/<[^>]+>/g, " ");
  return new DOMParser().parseFromString(String(value), "text/html").body.textContent?.trim() || "";
};

const emptySource = {
  name: "",
  source_type: "official_rss",
  source_url: "",
  market_country: "",
  category_code: "",
  keywords: "",
  schedule_minutes: "1440",
};

function SourceForm({ busy, onClose, onSubmit }) {
  const [value, setValue] = useState(emptySource);
  const update = (key, next) => setValue((current) => ({ ...current, [key]: next }));
  return <form className="compliance-source-form" onSubmit={(event) => {
    event.preventDefault();
    onSubmit({
      name: value.name.trim(),
      source_type: value.source_type,
      source_url: value.source_url.trim(),
      market_country: value.market_country.trim().toUpperCase() || null,
      category_code: value.category_code.trim() || null,
      keywords: value.keywords.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
      authorization_reference: "企业配置的官方公开政策源",
      schedule_minutes: Number(value.schedule_minutes),
      enabled: true,
    });
  }}>
    <header>
      <div><Plus /><span><strong>新增官方来源</strong><small>仅接入有权访问的 RSS 或 JSON 公告源</small></span></div>
      <button type="button" title="关闭新增来源" onClick={onClose}><X /></button>
    </header>
    <div>
      <label>来源名称<input required minLength={2} maxLength={200} value={value.name} onChange={(event) => update("name", event.target.value)} placeholder="例如 CPSC 产品召回" /></label>
      <label>来源格式<select value={value.source_type} onChange={(event) => update("source_type", event.target.value)}><option value="official_rss">官方 RSS</option><option value="official_json">官方 JSON</option></select></label>
      <label className="wide">官方地址<input required type="url" value={value.source_url} onChange={(event) => update("source_url", event.target.value)} placeholder="https://..." /></label>
      <label>市场国家<input maxLength={2} value={value.market_country} onChange={(event) => update("market_country", event.target.value)} placeholder="US，可留空" /></label>
      <label>品类编码<input maxLength={80} value={value.category_code} onChange={(event) => update("category_code", event.target.value)} placeholder="sofa，可留空" /></label>
      <label className="wide">风险关键词<input value={value.keywords} onChange={(event) => update("keywords", event.target.value)} placeholder="recall, flammability, labeling" /></label>
      <label>同步周期（分钟）<input required type="number" min="5" max="10080" value={value.schedule_minutes} onChange={(event) => update("schedule_minutes", event.target.value)} /></label>
    </div>
    <footer>
      <button type="button" onClick={onClose}>取消</button>
      <button type="submit" className="primary" disabled={busy}>{busy ? "保存中…" : "保存并启用"}</button>
    </footer>
  </form>;
}

export function ComplianceWorkspace({ overview = {}, scope = {}, onOverviewChange }) {
  const [sources, setSources] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [severity, setSeverity] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [adding, setAdding] = useState(false);

  const loadResources = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (severity) params.set("severity", severity);
      if (unreadOnly) params.set("unread_only", "true");
      const [sourceItems, alertItems] = await Promise.all([
        api("/api/v1/market-signals/policy-sources"),
        api(`/api/v1/market-signals/policy-alerts?${params}`),
      ]);
      setSources(sourceItems || []);
      setAlerts(alertItems || []);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setLoading(false);
    }
  }, [severity, unreadOnly]);

  useEffect(() => { loadResources(); }, [loadResources]);

  const refreshAll = async (message) => {
    await loadResources();
    await onOverviewChange?.();
    if (message) setNotice(message);
  };

  const installDefaults = async () => {
    setBusy("install");
    setError("");
    setNotice("");
    try {
      const installed = await api("/api/v1/market-signals/policy-sources:install-defaults", { method: "POST" });
      const results = await Promise.allSettled((installed || []).filter((item) => item.enabled).map((item) =>
        api(`/api/v1/market-signals/policy-sources/${item.source_id}/fetch`, { method: "POST" }),
      ));
      const failed = results.filter((item) => item.status === "rejected").length;
      await refreshAll(failed ? `默认来源已安装，${failed} 个来源同步失败` : "默认来源已安装并完成首次同步");
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy("");
    }
  };

  const createSource = async (payload) => {
    setBusy("create");
    setError("");
    setNotice("");
    try {
      const saved = await api("/api/v1/market-signals/policy-sources", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setAdding(false);
      await refreshAll(`已启用来源“${saved.name}”`);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy("");
    }
  };

  const syncSource = async (source) => {
    setBusy(`sync:${source.source_id}`);
    setError("");
    setNotice("");
    try {
      const result = await api(`/api/v1/market-signals/policy-sources/${source.source_id}/fetch`, { method: "POST" });
      await refreshAll(`“${source.name}”同步完成：新增 ${result.inserted_count} 条预警`);
    } catch (reason) {
      setError(messageOf(reason));
      await loadResources();
    } finally {
      setBusy("");
    }
  };

  const syncAll = async () => {
    const enabled = sources.filter((item) => item.enabled);
    if (!enabled.length) return;
    setBusy("sync-all");
    setError("");
    setNotice("");
    try {
      const results = await Promise.allSettled(enabled.map((item) =>
        api(`/api/v1/market-signals/policy-sources/${item.source_id}/fetch`, { method: "POST" }),
      ));
      const failed = results.filter((item) => item.status === "rejected").length;
      await refreshAll(failed ? `同步完成，${failed} 个来源失败` : `已同步 ${enabled.length} 个启用来源`);
    } finally {
      setBusy("");
    }
  };

  const toggleSource = async (source) => {
    setBusy(`toggle:${source.source_id}`);
    setError("");
    try {
      await api(`/api/v1/market-signals/policy-sources/${source.source_id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !source.enabled }),
      });
      await refreshAll(source.enabled ? `已停用“${source.name}”` : `已启用“${source.name}”`);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy("");
    }
  };

  const removeSource = async (source) => {
    if (!window.confirm(`确认删除政策来源“${source.name}”？`)) return;
    setBusy(`delete:${source.source_id}`);
    setError("");
    try {
      await api(`/api/v1/market-signals/policy-sources/${source.source_id}`, { method: "DELETE" });
      await refreshAll(`已删除“${source.name}”`);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy("");
    }
  };

  const markRead = async (alert) => {
    setBusy(`read:${alert.alert_id}`);
    setError("");
    try {
      await api(`/api/v1/market-signals/policy-alerts/${alert.alert_id}/read`, { method: "POST" });
      setAlerts((current) => unreadOnly
        ? current.filter((item) => item.alert_id !== alert.alert_id)
        : current.map((item) => item.alert_id === alert.alert_id ? { ...item, is_read: true } : item));
      await onOverviewChange?.();
      setNotice("预警已标记为已读");
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy("");
    }
  };

  const enabledCount = useMemo(() => sources.filter((item) => item.enabled).length, [sources]);

  return <div className="compliance-workspace">
    <p className="compliance-scope">
      企业政策监控 · 当前匹配范围：
      {overview.matched_scope?.market_country || scope.market_country || "全部市场"}{" / "}
      {overview.matched_scope?.category_code || scope.category_code || "全部品类"}
    </p>
    <div className="intelligence-kpis">
      <span><small>启用来源</small><strong>{overview.source_count ?? enabledCount}</strong></span>
      <span><small>未读预警</small><strong>{overview.unread_count || 0}</strong></span>
      <span><small>高风险</small><strong>{overview.high_risk_count || 0}</strong></span>
    </div>
    {error && <div className="compliance-message error"><WarningCircle />{error}</div>}
    {notice && <div className="compliance-message"><Check />{notice}</div>}

    <section className="compliance-source-panel">
      <header>
        <div><ShieldWarning /><span><strong>政策来源</strong><small>维护抓取范围并查看每个来源的实际同步状态</small></span></div>
        <div>
          {!sources.length && <button type="button" onClick={installDefaults} disabled={Boolean(busy)}><ShieldWarning />安装默认源</button>}
          <button type="button" onClick={() => setAdding(true)} disabled={Boolean(busy)}><Plus />新增来源</button>
          {!!sources.length && <button type="button" className="primary" onClick={syncAll} disabled={Boolean(busy) || !enabledCount}><ArrowClockwise />{busy === "sync-all" ? "同步中…" : "同步全部"}</button>}
        </div>
      </header>
      {adding && <SourceForm busy={busy === "create"} onClose={() => setAdding(false)} onSubmit={createSource} />}
      {loading ? <div className="compliance-loading"><SpinnerGap />正在读取政策来源</div> : (
        <div className="compliance-source-list">
          {sources.map((source) => <article key={source.source_id}>
            <span className={`compliance-source-status ${source.last_status || "idle"}`}><i />{source.last_status === "failed" ? "同步失败" : source.last_status === "succeeded" ? "同步正常" : "等待同步"}</span>
            <div>
              <strong>{source.name}</strong>
              <small>{source.market_country || "全部市场"} · {source.category_code || "全部品类"} · 每 {source.schedule_minutes} 分钟</small>
              <small>最近同步：{formatDate(source.last_fetched_at)}{source.last_error ? ` · ${source.last_error}` : ""}</small>
            </div>
            <label className="compliance-toggle">
              <input type="checkbox" aria-label={`${source.name}启用状态`} checked={source.enabled} disabled={Boolean(busy)} onChange={() => toggleSource(source)} />
              <i /><span>{source.enabled ? "已启用" : "已停用"}</span>
            </label>
            <div className="compliance-source-actions">
              <button type="button" title={`同步${source.name}`} disabled={Boolean(busy) || !source.enabled} onClick={() => syncSource(source)}>{busy === `sync:${source.source_id}` ? <SpinnerGap /> : <ArrowClockwise />}</button>
              <a title={`打开${source.name}原始来源`} href={source.source_url} target="_blank" rel="noreferrer"><LinkSimple /></a>
              <button type="button" className="danger" title={`删除${source.name}`} disabled={Boolean(busy)} onClick={() => removeSource(source)}><Trash /></button>
            </div>
          </article>)}
          {!sources.length && <div className="compliance-empty"><ShieldWarning /><strong>尚未配置政策来源</strong><small>安装默认官方源，或新增企业确认可访问的官方公告源。</small></div>}
        </div>
      )}
    </section>

    <section className="compliance-alert-panel">
      <header>
        <div><BellRinging /><span><strong>政策预警</strong><small>完整告警列表，可筛选并完成已读处置</small></span></div>
        <div>
          <select aria-label="风险等级" value={severity} onChange={(event) => setSeverity(event.target.value)}>
            <option value="">全部风险</option><option value="high">高风险</option><option value="medium">中风险</option><option value="low">低风险</option>
          </select>
          <label><input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} />仅看未读</label>
          <button type="button" title="刷新预警" onClick={loadResources} disabled={loading}><ArrowClockwise /></button>
        </div>
      </header>
      <div className="compliance-alert-list">
        {alerts.map((alert) => <article key={alert.alert_id} className={alert.is_read ? "read" : ""}>
          <i className={alert.severity} />
          <div>
            <span><b>{alert.severity === "high" ? "高风险" : alert.severity === "medium" ? "中风险" : "低风险"}</b>{!alert.is_read && <em>未读</em>}</span>
            <strong>{alert.title}</strong>
            {alert.summary && <p>{plainText(alert.summary)}</p>}
            <small>{alert.source_name || "官方来源"} · {formatDate(alert.published_at || alert.created_at)}{alert.matched_keywords?.length ? ` · 命中 ${alert.matched_keywords.join("、")}` : ""}</small>
          </div>
          <footer>
            {alert.url && <a href={alert.url} target="_blank" rel="noreferrer">查看原文</a>}
            {!alert.is_read && <button type="button" disabled={Boolean(busy)} onClick={() => markRead(alert)}>{busy === `read:${alert.alert_id}` ? "处理中…" : "标记已读"}</button>}
          </footer>
        </article>)}
        {!loading && !alerts.length && <div className="compliance-empty"><BellRinging /><strong>{sources.length ? "当前筛选下暂无预警" : "配置来源后可接收预警"}</strong></div>}
      </div>
    </section>
  </div>;
}
