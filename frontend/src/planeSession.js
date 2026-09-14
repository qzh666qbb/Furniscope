import { api, createUuid, isUuid } from "./api.js";

export const PLANE_SOURCE = "node_workflow_canvas";
export const PLANE_TASKS_PATH = `/api/v1/analysis-tasks?page_size=100&source=${PLANE_SOURCE}`;
export const WORKSPACES_PATH = "/api/v1/analysis-workspaces?page_size=100";

const CHAT_KEY = "furniscope-plane-chats-v2";
const WORKSPACE_KEY = "furniscope-plane-workspaces";
const LEGACY_CHAT_KEYS = ["furniscope-plane-chats"];

export function chatTurn(role, text, extras = {}) {
  return {
    kind: "text",
    client_message_id: extras.client_message_id || createUuid(),
    ...extras,
    role,
    text,
  };
}

export const planeGreeting = (name = "", workspaceUuid = "") => [chatTurn(
  "assistant",
  name
    ? `这是分析工作台「${name}」。右侧画布查看节点与报告，你可以在同一工作台中发起多次分析。`
    : "告诉我想分析的产品与目标市场，我会载入企业档案并启动分析。后续分析与对话都会保留在当前工作台。",
  {
    kind: "greeting",
    client_message_id: workspaceUuid ? `greeting:${workspaceUuid}` : undefined,
  },
)];

export function reusePlaneWorkspace(history = [], product, currentId) {
  const productId = product?.product_id;
  if (productId) {
    const existing = history.find((item) => String(item.product_id) === String(productId)
      && (Number(item.analysis_count || 0) > 0 || item.task_uuid));
    if (existing) return workspaceId(existing);
  }
  return isUuid(currentId) ? String(currentId) : createUuid();
}

export function workspaceId(item) {
  return String(item?.workspace_uuid || item?.task_uuid || "");
}

export function groupWorkspaceTasks(tasks = []) {
  const groups = new Map();
  tasks.forEach((item) => {
    const id = workspaceId(item);
    if (!id) return;
    const current = groups.get(id);
    if (!current) groups.set(id, { ...item, workspace_uuid: id, analysis_count: 1, runs: [item] });
    else {
      const runs = [...current.runs, item];
      const count = current.analysis_count + 1;
      if (new Date(item.updated_at) > new Date(current.updated_at)) groups.set(id, { ...item, workspace_uuid: id, analysis_count: count, runs });
      else groups.set(id, { ...current, analysis_count: count, runs });
    }
  });
  return [...groups.values()].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
}

function readStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CHAT_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function planeChatKey(id) {
  if (!id || String(id).startsWith("new")) return null;
  return String(id);
}

export function loadPlaneChat(id) {
  const key = planeChatKey(id);
  if (!key) return null;
  const messages = withoutFailureNotices(readStore()[key]);
  return Array.isArray(messages) && messages.length ? messages : null;
}

export function savePlaneChat(id, messages) {
  const key = planeChatKey(id);
  if (!key) return;
  const list = withoutFailureNotices(messages || []);
  const hasUserTurn = list.some((item) => item.role === "user");
  if (!hasUserTurn && list.length <= 1) return;
  const store = readStore();
  store[key] = list.slice(-40);
  localStorage.setItem(CHAT_KEY, JSON.stringify(store));
  const named = list.find((item) => item.role === "assistant")?.text?.match(/分析工作台「([^」]+)」/);
  savePlaneWorkspace({
    workspace_uuid: key,
    job_name: named?.[1] || "未命名工作台",
    source: PLANE_SOURCE,
    status: "draft",
  });
}

function readWorkspaceStore() {
  try {
    const parsed = JSON.parse(localStorage.getItem(WORKSPACE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function savePlaneWorkspace(entry) {
  const id = String(entry?.workspace_uuid || "");
  if (!id) return;
  const current = readWorkspaceStore().filter((item) => String(item.workspace_uuid) !== id);
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify([
    { ...entry, workspace_uuid: id, updated_at: entry.updated_at || new Date().toISOString() },
    ...current,
  ].slice(0, 50)));
}

export function removePlaneWorkspace(id) {
  const key = String(id || "");
  if (!key) return;
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(readWorkspaceStore().filter((item) => String(item.workspace_uuid) !== key)));
  const store = readStore();
  delete store[key];
  localStorage.setItem(CHAT_KEY, JSON.stringify(store));
}

function workspaceFromChat(id, messages) {
  const greeting = (messages || []).find((item) => item.role === "assistant")?.text || "";
  const named = greeting.match(/分析工作台「([^」]+)」/);
  return {
    workspace_uuid: id,
    job_name: named?.[1] || "未命名工作台",
    status: "draft",
    analysis_count: 0,
    product_sku: "",
    product_name: "",
    target_country: "",
    target_platform: "",
    source: PLANE_SOURCE,
    updated_at: new Date().toISOString(),
    runs: [],
    local_only: true,
  };
}

export function mergeWorkspaceLists(workspaceItems = [], taskItems = []) {
  const grouped = groupWorkspaceTasks(taskItems);
  const byTask = new Map(grouped.map((item) => [workspaceId(item), item]));
  const seen = new Set();
  const merged = [];
  workspaceItems.forEach((item) => {
    const id = workspaceId(item);
    if (!id || seen.has(id)) return;
    seen.add(id);
    const groupedItem = byTask.get(id);
    merged.push({
      ...item,
      ...(groupedItem || {}),
      job_name: item.job_name,
      analysis_count: Math.max(item.analysis_count || 0, groupedItem?.analysis_count || 0),
      workspace_uuid: id,
      source: item.source || groupedItem?.source || PLANE_SOURCE,
      runs: groupedItem?.runs || (item.task_uuid ? [item] : []),
      local_only: false,
    });
  });
  grouped.forEach((item) => {
    const id = workspaceId(item);
    if (!id || seen.has(id)) return;
    seen.add(id);
    merged.push(item);
  });
  readWorkspaceStore().forEach((item) => {
    const id = String(item.workspace_uuid || "");
    if (!id || seen.has(id)) return;
    if (!item.task_uuid && !(Number(item.analysis_count) > 0)) return;
    seen.add(id);
    merged.push({ ...workspaceFromChat(id, loadPlaneChat(id) || []), ...item, analysis_count: item.analysis_count || 0, runs: [], local_only: true });
  });
  Object.entries(readStore()).forEach(([id, messages]) => {
    if (!id || seen.has(id) || !Array.isArray(messages) || !messages.some((item) => item.role === "user")) return;
    seen.add(id);
    merged.push(workspaceFromChat(id, messages));
  });
  return merged.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
}

export function isPlaneTask(item) {
  return (item?.source || item?.analysis_config?.source) === PLANE_SOURCE;
}

export const TASK_STATUS_LABELS = {
  draft: "待运行", queued: "排队中", running: "运行中", waiting_human: "待确认",
  partial_succeeded: "部分完成", succeeded: "已完成", failed: "异常", cancelled: "已取消",
};

export function workspaceTaskUuids(item) {
  const runs = item?.runs?.length ? item.runs : item ? [item] : [];
  return [...new Set(runs.map((run) => run.task_uuid).filter(Boolean))];
}

export function matchesWorkspaceQuery(item, query, status) {
  if (status && item.status !== status) return false;
  const text = query.trim().toLowerCase();
  if (!text) return true;
  return [
    item.job_name, item.product_sku, item.product_name,
    item.target_country, item.target_platform, workspaceId(item),
  ].some((value) => String(value || "").toLowerCase().includes(text));
}

export function withoutFailureNotices(items = []) {
  return (items || []).filter((item) => {
    const text = String(item?.text || item?.content || "").replace(/\s+/g, "");
    if (!text) return true;
    if (text.includes("本次分析执行失败") || text.includes("异常原因") || text.includes("allow_generated_fixture")) return false;
    if (item?.kind === "error" && (text.includes("失败") || text.includes("异常"))) return false;
    return true;
  });
}

export function toUiMessage(item) {
  return chatTurn(item.role, item.content || item.text, {
    kind: item.message_kind || item.kind || "text",
    client_message_id: item.client_message_id,
    message_uuid: item.message_uuid,
    evidence_refs: item.evidence_refs || [],
  });
}

export async function persistWorkspace(entry) {
  const id = String(entry?.workspace_uuid || "");
  const name = String(entry?.job_name || "").trim();
  if (!id || !name) return null;
  savePlaneWorkspace(entry);
  if (!isUuid(id)) return null;
  try {
    const saved = await api("/api/v1/analysis-workspaces", {
      method: "POST",
      body: JSON.stringify({
        workspace_uuid: id,
        name,
        source: entry.source || PLANE_SOURCE,
        product_id: entry.product_id || undefined,
      }),
    });
    savePlaneWorkspace({ ...entry, ...saved, local_only: false });
    return saved;
  } catch {
    return null;
  }
}

export async function fetchWorkspaces() {
  const page = await api(WORKSPACES_PATH);
  return page.items || [];
}

export async function fetchWorkspaceMessages(id) {
  const key = planeChatKey(id);
  if (!key) return loadPlaneChat(id) || [];
  if (!isUuid(key)) return loadPlaneChat(key) || [];
  try {
    const page = await api(`/api/v1/analysis-workspaces/${key}/messages?page_size=100`);
    const items = withoutFailureNotices((page.items || []).map(toUiMessage));
    const local = withoutFailureNotices(loadPlaneChat(key) || []);
    const merged = items.length ? items : local;
    if (merged.length) savePlaneChat(key, merged);
    return merged;
  } catch {
    return loadPlaneChat(key) || [];
  }
}

function messageKind(item, index) {
  if (item.kind) return item.kind;
  if (index === 0 && item.role === "assistant") return "greeting";
  return "text";
}

export async function persistWorkspaceMessages(id, messages, extra = {}) {
  const key = planeChatKey(id);
  if (!key) return;
  const list = withoutFailureNotices(messages || []);
  savePlaneChat(key, list);
  if (!list.some((item) => item.role === "user")) return;
  if (!isUuid(key)) return;
  await persistWorkspace({
    workspace_uuid: key,
    job_name: extra.job_name || "未命名工作台",
    source: PLANE_SOURCE,
    product_id: extra.product_id,
    status: extra.status || "draft",
  });
  for (let index = 0; index < list.length; index += 1) {
    const item = list[index];
    const clientId = item.client_message_id || `local:${key}:${index}:${item.role}:${String(item.text || "").slice(0, 48)}`;
    try {
      await api(`/api/v1/analysis-workspaces/${key}/messages`, {
        method: "POST",
        body: JSON.stringify({
          role: item.role,
          content: item.text,
          message_kind: messageKind(item, index),
          client_message_id: clientId,
          analysis_task_uuid: extra.task_uuid || undefined,
          evidence_refs: item.evidence_refs || [],
          metadata: extra.execution_target ? { execution_target: extra.execution_target } : {},
        }),
      });
    } catch {
      break;
    }
  }
}

export async function archiveWorkspace(item) {
  const id = workspaceId(item);
  if (!id) return;
  try {
    if (isUuid(id)) await api(`/api/v1/analysis-workspaces/${id}:archive`, { method: "POST" });
  } catch {
    const ids = workspaceTaskUuids(item);
    if (ids.length) {
      await api("/api/v1/analysis-tasks:archive", {
        method: "POST",
        body: JSON.stringify({ task_uuids: ids }),
      });
    }
  }
  removePlaneWorkspace(id);
}

if (typeof localStorage !== "undefined") {
  try {
    LEGACY_CHAT_KEYS.forEach((key) => localStorage.removeItem(key));
  } catch {
    /* ignore */
  }
}
