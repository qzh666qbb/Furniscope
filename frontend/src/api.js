const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const ACCESS_TOKEN_KEY = "furniscope-access-token";
const REFRESH_TOKEN_KEY = "furniscope-refresh-token";
let refreshPromise = null;
let unauthorizedDispatched = false;

function tokenExpiresSoon(token, leewaySeconds = 30) {
  try {
    const encoded = token.split(".")[1];
    const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=")));
    return typeof payload.exp === "number" && payload.exp <= Date.now() / 1000 + leewaySeconds;
  } catch {
    return false;
  }
}

function storeSession(data) {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
  sessionStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
  unauthorizedDispatched = false;
}

export function clearSession() {
  const hadSession = Boolean(
    sessionStorage.getItem(ACCESS_TOKEN_KEY) ||
      sessionStorage.getItem(REFRESH_TOKEN_KEY) ||
      sessionStorage.getItem("furniscope-auth") ||
      sessionStorage.getItem("furniscope-admin-auth"),
  );
  sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  sessionStorage.removeItem("furniscope-auth");
  sessionStorage.removeItem("furniscope-admin-auth");
  if (hadSession && !unauthorizedDispatched) {
    unauthorizedDispatched = true;
    window.dispatchEvent(new Event("furniscope:unauthorized"));
  }
}

function transientFailure(status, fallback) {
  const error = new Error(fallback);
  error.status = status;
  error.transient = status >= 500 || status === 0;
  return error;
}

async function refreshAccessToken() {
  const refreshToken = sessionStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) throw new Error("登录已过期，请重新登录");
  let response;
  try {
    response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } catch {
    throw transientFailure(0, "服务暂时不可用，请稍后重试");
  }
  const body = await response.json().catch(() => null);
  if (response.status >= 500) {
    throw transientFailure(response.status, body?.error?.message || "服务暂时不可用，请稍后重试");
  }
  if (!response.ok || !body?.data) throw new Error(body?.error?.message || "登录已过期，请重新登录");
  storeSession(body.data);
  return body.data.access_token;
}

async function request(path, options = {}, includeResponse = false) {
  const { skipRefresh = false, skipAuth = false, rawData = false, headers: extraHeaders, ...fetchOptions } = options;
  const isForm = fetchOptions.body instanceof FormData;
  const headers = { ...(isForm ? {} : { "Content-Type": "application/json" }), ...(extraHeaders || {}) };
  let token = skipAuth ? null : sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (!skipRefresh && !skipAuth && token && sessionStorage.getItem(REFRESH_TOKEN_KEY) && tokenExpiresSoon(token)) {
    try {
      refreshPromise ||= refreshAccessToken().finally(() => { refreshPromise = null; });
      token = await refreshPromise;
    } catch (error) {
      if (!error?.transient) clearSession();
      throw error;
    }
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...fetchOptions, headers });
  } catch {
    throw transientFailure(0, "服务暂时不可用，请稍后重试");
  }
  const body = await response.json().catch(() => null);
  if (response.status === 401) {
    if (!skipRefresh && !skipAuth && sessionStorage.getItem(REFRESH_TOKEN_KEY)) {
      try {
        refreshPromise ||= refreshAccessToken().finally(() => { refreshPromise = null; });
        const nextToken = await refreshPromise;
        return request(path, { ...options, skipRefresh: true, headers: { ...(extraHeaders || {}), Authorization: `Bearer ${nextToken}` } }, includeResponse);
      } catch (error) {
        if (!error?.transient) clearSession();
        throw error;
      }
    }
    if (!skipAuth) clearSession();
    throw new Error(body?.error?.message || (skipAuth ? "邮箱或密码错误" : "登录已过期，请重新登录"));
  }
  if (!response.ok) {
    const error = response.status >= 500
      ? transientFailure(response.status, body?.error?.message || `请求失败（${response.status}）`)
      : new Error(body?.error?.message || `请求失败（${response.status}）`);
    error.status = response.status;
    error.code = body?.error?.code;
    throw error;
  }
  return includeResponse ? { data: body.data, response } : rawData ? body : body.data;
}

export const api = (path, options = {}) => request(path, options, false);
export const apiWithMeta = (path, options = {}) => request(path, options, true);

export async function downloadApiFile(path, options = {}, retried = false) {
  let token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch {
    throw transientFailure(0, "文件下载服务暂时不可用");
  }
  if (response.status === 401 && !retried && sessionStorage.getItem(REFRESH_TOKEN_KEY)) {
    token = await refreshAccessToken();
    return downloadApiFile(path, {
      ...options,
      headers: { ...(options.headers || {}), Authorization: `Bearer ${token}` },
    }, true);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message || `文件下载失败（${response.status}）`);
  }
  return { blob: await response.blob(), response };
}

export async function uploadApiForm(path, body, { headers: extraHeaders = {}, onProgress } = {}, retried = false) {
  let token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${API_BASE}${path}`);
    Object.entries(extraHeaders).forEach(([key, value]) => request.setRequestHeader(key, value));
    if (token) request.setRequestHeader("Authorization", `Bearer ${token}`);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });
    request.addEventListener("error", () => reject(transientFailure(0, "文件上传服务暂时不可用")));
    request.addEventListener("load", async () => {
      const payload = (() => {
        try {
          return JSON.parse(request.responseText);
        } catch {
          return null;
        }
      })();
      if (request.status === 401 && !retried && sessionStorage.getItem(REFRESH_TOKEN_KEY)) {
        try {
          token = await refreshAccessToken();
          resolve(await uploadApiForm(path, body, {
            headers: { ...extraHeaders, Authorization: `Bearer ${token}` },
            onProgress,
          }, true));
        } catch (error) {
          if (!error?.transient) clearSession();
          reject(error);
        }
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        reject(request.status >= 500
          ? transientFailure(request.status, payload?.error?.message || `请求失败（${request.status}）`)
          : new Error(payload?.error?.message || `请求失败（${request.status}）`));
        return;
      }
      resolve(payload?.data);
    });
    request.send(body);
  });
}

export function subscribeTaskEvents(taskUuid, onMessage, onError) {
  const controller = new AbortController();
  const connect = async (retried = false) => {
    let token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
    let response = await fetch(`${API_BASE}/api/v1/analysis-tasks/${taskUuid}/events`, {
      headers: token ? { Authorization: `Bearer ${token}`, Accept: "text/event-stream" } : { Accept: "text/event-stream" },
      signal: controller.signal,
    });
    if (response.status === 401 && !retried && sessionStorage.getItem(REFRESH_TOKEN_KEY)) {
      token = await refreshAccessToken();
      response = await fetch(`${API_BASE}/api/v1/analysis-tasks/${taskUuid}/events`, {
        headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
        signal: controller.signal,
      });
    }
    if (!response.ok || !response.body) throw new Error(`实时任务流连接失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        const data = block.split("\n").filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim()).join("\n");
        if (data) onMessage(JSON.parse(data));
      }
    }
    if (!controller.signal.aborted) throw new Error("实时任务流已结束");
  };
  connect().catch((error) => {
    if (error.name !== "AbortError") onError?.(error);
  });
  return () => controller.abort();
}

export async function postSse(path, body, onEvent, options = {}) {
  const controller = new AbortController();
  const seenEventIds = new Set();
  const connect = async (authRetried = false) => {
    let token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
    const headers = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(options.headers || {}),
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    let response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      if (error?.name === "AbortError") throw error;
      throw transientFailure(0, "对话流连接中断");
    }
    if (response.status === 401 && !authRetried && sessionStorage.getItem(REFRESH_TOKEN_KEY)) {
      token = await refreshAccessToken();
      headers.Authorization = `Bearer ${token}`;
      return connect(true);
    }
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok || !response.body) {
      const payload = await response.json().catch(() => null);
      const message = payload?.error?.message || `请求失败（${response.status}）`;
      if (
        response.status >= 500
        || payload?.error?.code === "WORKSPACE_TURN_IN_PROGRESS"
      ) {
        throw transientFailure(response.status, message);
      }
      throw new Error(message);
    }
    if (contentType && !contentType.includes("text/event-stream") && !contentType.includes("octet-stream")) {
      const payload = await response.json().catch(() => null);
      throw new Error(payload?.error?.message || `请求失败（${response.status}）`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let sawDone = false;
    let errorMessage = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        let eventName = "message";
        let eventId = "";
        const dataLines = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("id:")) eventId = line.slice(3).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;
        if (eventId && seenEventIds.has(eventId)) continue;
        const data = JSON.parse(dataLines.join("\n"));
        if (eventId) seenEventIds.add(eventId);
        if (eventName === "done") sawDone = true;
        if (eventName === "error") errorMessage = data?.message || "暂时无法完成这次问询。";
        onEvent?.(eventName, data, eventId);
      }
    }
    if (errorMessage) throw new Error(errorMessage);
    if (!sawDone) throw transientFailure(0, "对话流已中断，请重试");
  };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await connect();
      return;
    } catch (error) {
      if (controller.signal.aborted || !error?.transient || attempt === 1) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, 300));
    }
  }
}

export async function login(email, password) {
  const data = await api("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
    skipAuth: true,
    skipRefresh: true,
  });
  storeSession(data);
  return data;
}

export async function adminLogin(email, password) {
  const data = await api("/api/v1/auth/admin/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
    skipAuth: true,
    skipRefresh: true,
  });
  storeSession(data);
  return data;
}

export async function submitRegistration(payload) {
  return api("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
    skipAuth: true,
    skipRefresh: true,
  });
}

export async function requestPasswordReset(email) {
  return api("/api/v1/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
    skipAuth: true,
    skipRefresh: true,
  });
}

export async function confirmPasswordReset(payload) {
  return api("/api/v1/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify(payload),
    skipAuth: true,
    skipRefresh: true,
  });
}

export async function currentUser() {
  return api("/api/v1/users/me");
}

export async function logout() {
  const refreshToken = sessionStorage.getItem(REFRESH_TOKEN_KEY);
  try {
    if (refreshToken) await api("/api/v1/auth/logout", {
      method: "POST", body: JSON.stringify({ refresh_token: refreshToken }), skipRefresh: true,
    });
  } finally {
    clearSession();
  }
}

export function hasSession() {
  return Boolean(sessionStorage.getItem(ACCESS_TOKEN_KEY) || sessionStorage.getItem(REFRESH_TOKEN_KEY));
}

function fallbackUuid() {
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export function createUuid() {
  const randomUuid = globalThis.crypto?.randomUUID;
  return typeof randomUuid === "function"
    ? randomUuid.call(globalThis.crypto)
    : fallbackUuid();
}

export function isUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(value || "").trim());
}

export function idempotencyKey(prefix) {
  return `${prefix}-${createUuid()}`;
}
