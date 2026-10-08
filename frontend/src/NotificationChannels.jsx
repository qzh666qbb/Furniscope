import { useEffect, useState } from "react";
import {
  ArrowClockwiseIcon,
  BellRingingIcon,
  CaretDownIcon,
  CheckCircleIcon,
  FlaskIcon,
  GearSixIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
  WarningCircleIcon,
  XIcon,
} from "@phosphor-icons/react";
import { api } from "./api.js";
import "./market-intelligence.css";

const typeLabels = {
  webhook: "通用 Webhook",
  dingtalk: "钉钉",
  slack: "Slack",
  email_gateway: "邮件网关",
  sms_gateway: "短信网关",
};

export function NotificationChannels({ defaultOpen = false }) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const [channels, setChannels] = useState([]);
  const [events, setEvents] = useState([]);
  const [creating, setCreating] = useState(false);
  const [editingChannel, setEditingChannel] = useState(null);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = async () => {
    try {
      const [nextChannels, nextEvents] = await Promise.all([
        api("/api/v1/notification-channels"),
        api("/api/v1/notification-channels/events?limit=10"),
      ]);
      setChannels(nextChannels || []);
      setEvents(nextEvents || []);
      setError("");
    } catch (reason) {
      setError(reason.message);
    }
  };
  useEffect(() => { load(); }, []);

  const saveChannel = async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selectedEvents = form.getAll("events");
    setBusy(true);
    setError("");
    try {
      const endpoint = editingChannel
        ? `/api/v1/notification-channels/${editingChannel.channel_id}`
        : "/api/v1/notification-channels";
      await api(endpoint, {
        method: editingChannel ? "PATCH" : "POST",
        body: JSON.stringify({
          name: form.get("name"),
          channel_type: form.get("channel_type"),
          target_url: form.get("target_url"),
          secret_env: form.get("secret_env") || null,
          events: selectedEvents,
          enabled: editingChannel?.enabled ?? true,
        }),
      });
      setCreating(false);
      setEditingChannel(null);
      setNotice(editingChannel ? "通知渠道已更新" : "通知渠道已创建");
      await load();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };
  const openCreate = () => {
    setEditingChannel(null);
    setCreating(true);
    setError("");
  };
  const openEdit = channel => {
    setEditingChannel(channel);
    setCreating(true);
    setError("");
  };
  const closeForm = () => {
    setCreating(false);
    setEditingChannel(null);
  };
  const update = async (channel, enabled) => {
    setError("");
    try {
      await api(`/api/v1/notification-channels/${channel.channel_id}`, {
        method: "PATCH", body: JSON.stringify({ enabled }),
      });
      await load();
    } catch (reason) {
      setError(reason.message);
    }
  };
  const test = async channel => {
    setBusy(true);
    try {
      await api(`/api/v1/notification-channels/${channel.channel_id}/test`, {
        method: "POST",
      });
      setNotice(`已为“${channel.name}”创建连通性验证任务`);
      await load();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async channel => {
    if (!window.confirm(`确认删除通知渠道“${channel.name}”？`)) return;
    setError("");
    try {
      await api(`/api/v1/notification-channels/${channel.channel_id}`, {
        method: "DELETE",
      });
      await load();
    } catch (reason) {
      setError(reason.message);
    }
  };
  const retry = async item => {
    setBusy(true);
    setError("");
    try {
      await api(`/api/v1/notification-channels/events/${item.event_id}:retry`, {
        method: "POST",
      });
      setNotice(`“${item.title}”已重新进入投递队列`);
      setSelectedEvent(null);
      await load();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  };

  const enabledCount = channels.filter(channel => channel.enabled).length;
  return <details className="notification-channel-manager" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary className="notification-channel-summary">
      <i><GearSixIcon /></i>
      <div>
        <span>AUTOMATION</span>
        <h2>自动化投递配置</h2>
        <p>把竞品变化和政策预警投递到外部接收端；此模块不负责网页或评论采集。</p>
      </div>
      <em><strong>{enabledCount}</strong> / {channels.length} 个渠道启用 <CaretDownIcon /></em>
    </summary>
    <div className="notification-channel-body">
      <header>
        <p>渠道配置通常保持稳定，仅在接收地址或事件范围变化时调整。</p>
        <button type="button" onClick={creating ? closeForm : openCreate}>
          {creating ? <XIcon /> : <PlusIcon />}{creating ? "收起表单" : "新增渠道"}
        </button>
      </header>
      {error && <p className="notification-message error"><WarningCircleIcon />{error}</p>}
      {notice && <p className="notification-message"><CheckCircleIcon />{notice}</p>}
      {creating && <form key={editingChannel?.channel_id || "new"} className="notification-channel-form" onSubmit={saveChannel}>
        <label>渠道名称<input name="name" required minLength={2} maxLength={200} defaultValue={editingChannel?.name || ""} /></label>
        <label>渠道类型<select name="channel_type" defaultValue={editingChannel?.channel_type || "webhook"}>{Object.entries(typeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label className="wide">接收地址<input name="target_url" type="url" required placeholder="https://example.com/furniscope-alerts" defaultValue={editingChannel?.target_url || ""} /></label>
        <label className="wide">签名密钥环境变量<input name="secret_env" pattern="[A-Z][A-Z0-9_]{1,127}" placeholder="可选，例如 FURNISCOPE_DINGTALK_SECRET" defaultValue={editingChannel?.secret_env || ""} /></label>
        <fieldset><legend>接收事件</legend><label><input type="checkbox" name="events" value="competitor_alert" defaultChecked={!editingChannel || editingChannel.events?.includes("competitor_alert")} />竞品变化</label><label><input type="checkbox" name="events" value="policy_alert" defaultChecked={!editingChannel || editingChannel.events?.includes("policy_alert")} />政策预警</label></fieldset>
        <footer><button type="button" onClick={closeForm}>取消</button><button className="primary" disabled={busy}>{busy ? "保存中…" : editingChannel ? "保存修改" : "保存渠道"}</button></footer>
      </form>}
      <div className="notification-channel-list">
        {channels.map(channel => <article key={channel.channel_id}>
          <BellRingingIcon />
          <span><strong>{channel.name}</strong><small>{typeLabels[channel.channel_type] || channel.channel_type} · {(channel.events || []).map(item => item === "competitor_alert" ? "竞品变化" : "政策预警").join("、")}</small><code>{channel.target_url}</code></span>
          <label className="notification-toggle"><input type="checkbox" checked={channel.enabled} onChange={event => update(channel, event.target.checked)} /><i />{channel.enabled ? "已启用" : "已停用"}</label>
          <div><button type="button" title="编辑渠道" onClick={() => openEdit(channel)}><PencilSimpleIcon /></button><button type="button" title="发送连通性验证" disabled={busy} onClick={() => test(channel)}><FlaskIcon /></button><button type="button" title="删除渠道" onClick={() => remove(channel)}><TrashIcon /></button></div>
        </article>)}
        {!channels.length && <p className="notification-empty">尚未配置通知渠道。</p>}
      </div>
      {!!events.length && <details className="notification-events"><summary>最近投递记录</summary>{events.map(item => <button type="button" key={item.event_id} onClick={() => setSelectedEvent(item)}><span>{item.channel_name} · {item.title}</span><em className={item.status}>{item.status === "delivered" ? "已送达" : item.status === "failed" ? "失败" : "待投递"}</em></button>)}</details>}
    </div>
    {selectedEvent && <div className="notification-event-backdrop" role="presentation" onClick={() => setSelectedEvent(null)}>
      <section className="notification-event-detail" role="dialog" aria-modal="true" aria-label="投递详情" onClick={event => event.stopPropagation()}>
        <header><div><small>{typeLabels[selectedEvent.channel_type] || selectedEvent.channel_type}</small><h3>{selectedEvent.title}</h3></div><button type="button" title="关闭" onClick={() => setSelectedEvent(null)}><XIcon /></button></header>
        <dl>
          <div><dt>接收渠道</dt><dd>{selectedEvent.channel_name}</dd></div>
          <div><dt>事件类型</dt><dd>{selectedEvent.event_type === "competitor_alert" ? "竞品变化" : selectedEvent.event_type === "policy_alert" ? "政策预警" : "连通性验证"}</dd></div>
          <div><dt>投递状态</dt><dd>{selectedEvent.status === "delivered" ? "已送达" : selectedEvent.status === "failed" ? "失败" : "待投递"}</dd></div>
          <div><dt>尝试次数</dt><dd>{selectedEvent.attempts}</dd></div>
          <div><dt>创建时间</dt><dd>{new Date(selectedEvent.created_at).toLocaleString("zh-CN")}</dd></div>
          <div><dt>送达时间</dt><dd>{selectedEvent.delivered_at ? new Date(selectedEvent.delivered_at).toLocaleString("zh-CN") : "—"}</dd></div>
        </dl>
        <p>{selectedEvent.content}</p>
        {selectedEvent.last_error && <code>{selectedEvent.last_error}</code>}
        <footer>
          {selectedEvent.status === "failed" && <button type="button" disabled={busy} onClick={() => retry(selectedEvent)}><ArrowClockwiseIcon />重新投递</button>}
          <button type="button" onClick={() => setSelectedEvent(null)}>关闭</button>
        </footer>
      </section>
    </div>}
  </details>;
}
