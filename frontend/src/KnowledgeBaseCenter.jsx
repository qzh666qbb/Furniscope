import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowLeft,
  Books,
  Check,
  CheckCircle,
  CloudArrowUp,
  Eye,
  FileText,
  LinkSimple,
  LockKey,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  SpinnerGap,
  Trash,
  UsersThree,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, uploadApiForm } from "./api.js";
import { DocumentPreviewContent, previewSummary } from "./KnowledgeDocumentPreview.jsx";
import "./knowledge-base-center.css";

const pendingStatuses = new Set(["uploaded", "parsing", "chunking", "embedding"]);
const statusLabels = {
  uploaded: "等待索引",
  parsing: "内容解析",
  chunking: "内容切分",
  embedding: "向量生成",
  ready: "可检索",
  failed: "索引失败",
  deleted: "已删除",
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  cancelled: "已取消",
};

const messageOf = (error) => error instanceof Error ? error.message : String(error);
const formatDate = (value) => value ? new Date(value).toLocaleString("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
}) : "—";
const formatBytes = (value) => {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};

function Status({ value }) {
  return <span className={`kb-status ${value || "unknown"}`}>
    {pendingStatuses.has(value) || value === "queued" || value === "running"
      ? <SpinnerGap />
      : value === "ready" || value === "succeeded" ? <CheckCircle /> : <WarningCircle />}
    {statusLabels[value] || value || "未知"}
  </span>;
}

function BaseForm({ value, busy, onClose, onSubmit }) {
  const [name, setName] = useState(value?.name || "");
  const [description, setDescription] = useState(value?.description || "");
  const [visibility, setVisibility] = useState(value?.visibility || "tenant");
  const editing = Boolean(value?.knowledge_base_uuid);
  return <div className="kb-modal-backdrop" role="presentation" onMouseDown={onClose}>
    <form className="kb-modal" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => {
      event.preventDefault();
      onSubmit({ name: name.trim(), description: description.trim() || null, visibility });
    }}>
      <header>
        <div><Books /><span><strong>{editing ? "编辑知识库" : "新建知识库"}</strong><small>{editing ? "更新资料资产信息" : "建立持久资料资产"}</small></span></div>
        <button type="button" title="关闭" onClick={onClose}><X /></button>
      </header>
      <label>名称<input autoFocus maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label>说明<textarea maxLength={2000} rows={4} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <fieldset>
        <legend>访问权限</legend>
        <label className={visibility === "tenant" ? "selected" : ""}>
          <input type="radio" name="visibility" value="tenant" checked={visibility === "tenant"} onChange={() => setVisibility("tenant")} />
          <UsersThree /><span><strong>企业共享</strong><small>同一企业用户可访问</small></span>
        </label>
        <label className={visibility === "user" ? "selected" : ""}>
          <input type="radio" name="visibility" value="user" checked={visibility === "user"} onChange={() => setVisibility("user")} />
          <LockKey /><span><strong>仅自己</strong><small>仅创建者可访问</small></span>
        </label>
      </fieldset>
      <footer>
        <button type="button" onClick={onClose}>取消</button>
        <button className="primary" type="submit" disabled={!name.trim() || busy}>{busy ? "保存中" : "保存"}</button>
      </footer>
    </form>
  </div>;
}

function DocumentInspector({
  document,
  preview,
  onClose,
  onOpenDetail,
}) {
  if (!document) return null;
  return <aside className="kb-inspector" aria-label="文档快速预览">
    <header>
      <div><FileText /><span><strong title={document.filename}>{document.filename}</strong><small>当前版本 v{document.version} · {formatBytes(document.byte_size)}</small></span></div>
      <button type="button" title="关闭快速预览" onClick={onClose}><X /></button>
    </header>
    <section className="kb-quick-meta">
      <div><span>索引状态</span><Status value={document.status} /></div>
      <div><span>文件类型</span><strong>{document.document_type || document.mime_type}</strong></div>
      <div><span>更新时间</span><strong>{formatDate(document.updated_at)}</strong></div>
    </section>
    <section className="kb-quick-summary">
      <span>内容摘要</span>
      <p>{previewSummary(preview)}</p>
    </section>
    <section className="kb-quick-content">
      <header><span>内容节选</span>{preview?.extraction_method && <b>{preview.extraction_method}</b>}</header>
      <DocumentPreviewContent preview={preview} compact />
    </section>
    <footer className="kb-inspector-footer">
      <button type="button" onClick={onOpenDetail}><FileText />全屏查看</button>
    </footer>
  </aside>;
}

export function KnowledgeBaseCenter({ Sidebar, Topbar }) {
  const query = useMemo(() => new URLSearchParams(location.hash.split("?")[1] || ""), []);
  const workspaceUuid = query.get("workspace") || "";
  const returnRoute = query.get("return") || "analysis";
  const restoredBaseId = query.get("base") || "";
  const restoredDocumentId = query.get("document") || "";
  const restoredScrollTop = Math.max(0, Number(query.get("scroll") || 0) || 0);
  const contentRef = useRef(null);
  const restoredScroll = useRef(false);
  const [bases, setBases] = useState([]);
  const [activeId, setActiveId] = useState("");
  const [documents, setDocuments] = useState([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState(restoredDocumentId);
  const [search, setSearch] = useState("");
  const [retrievalQuery, setRetrievalQuery] = useState("");
  const [retrievalResult, setRetrievalResult] = useState(null);
  const [retrievalError, setRetrievalError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null);
  const [formValue, setFormValue] = useState(undefined);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [inspector, setInspector] = useState(null);
  const [preview, setPreview] = useState(null);
  const [workspaceContext, setWorkspaceContext] = useState(null);

  const active = bases.find((item) => String(item.knowledge_base_uuid) === activeId);
  const boundIds = (workspaceContext?.knowledge_base_uuids || []).map(String);
  const visibleBases = bases.filter((item) => (
    !search.trim() || `${item.name} ${item.description || ""}`.toLowerCase().includes(search.trim().toLowerCase())
  ));
  const readyCount = bases.reduce((sum, item) => sum + Number(item.ready_document_count || 0), 0);
  const pendingCount = documents.filter((item) => pendingStatuses.has(item.status)).length;

  const loadBases = useCallback(async (preferredId = "") => {
    const page = await api("/api/v1/knowledge-bases?page_size=100");
    const items = page.items || [];
    setBases(items);
    setActiveId((current) => {
      const target = preferredId || current;
      return items.some((item) => String(item.knowledge_base_uuid) === String(target))
        ? String(target)
        : String(items[0]?.knowledge_base_uuid || "");
    });
    return items;
  }, []);

  const loadDocuments = useCallback(async (baseId) => {
    if (!baseId) {
      setDocuments([]);
      return [];
    }
    const page = await api(`/api/v1/knowledge-bases/${baseId}/documents?page_size=100`);
    const items = page.items || [];
    setDocuments(items);
    setSelectedDocumentId((current) => (
      items.some((item) => item.document_uuid === current) ? current : ""
    ));
    setInspector((current) => current
      ? items.find((item) => item.document_uuid === current.document_uuid) || null
      : null);
    return items;
  }, []);

  const loadWorkspaceContext = useCallback(async () => {
    if (!workspaceUuid) return;
    try {
      setWorkspaceContext(await api(`/api/v1/analysis-workspaces/${workspaceUuid}/context`));
    } catch {
      setWorkspaceContext(null);
    }
  }, [workspaceUuid]);

  useEffect(() => {
    let activeRequest = true;
    Promise.all([loadBases(restoredBaseId), loadWorkspaceContext()])
      .catch((reason) => activeRequest && setNotice({ type: "error", text: messageOf(reason) }))
      .finally(() => activeRequest && setLoading(false));
    return () => { activeRequest = false; };
  }, [loadBases, loadWorkspaceContext, restoredBaseId]);

  useEffect(() => {
    setInspector(null);
    setPreview(null);
    setRetrievalResult(null);
    setRetrievalError("");
    loadDocuments(activeId).catch((reason) => setNotice({ type: "error", text: messageOf(reason) }));
  }, [activeId, loadDocuments]);

  useEffect(() => {
    if (
      restoredScroll.current
      || loading
      || !contentRef.current
      || (restoredBaseId && activeId !== restoredBaseId)
    ) return;
    restoredScroll.current = true;
    window.requestAnimationFrame(() => {
      if (contentRef.current) contentRef.current.scrollTop = restoredScrollTop;
    });
  }, [activeId, documents, loading, restoredBaseId, restoredScrollTop]);

  useEffect(() => {
    if (!activeId || !documents.some((item) => pendingStatuses.has(item.status))) return undefined;
    const timer = window.setInterval(() => {
      loadDocuments(activeId).catch(() => undefined);
      loadBases(activeId).catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [activeId, documents, loadBases, loadDocuments]);

  const saveBase = async (payload) => {
    const editing = Boolean(formValue?.knowledge_base_uuid);
    setBusy("base");
    setNotice(null);
    try {
      const saved = await api(editing
        ? `/api/v1/knowledge-bases/${formValue.knowledge_base_uuid}`
        : "/api/v1/knowledge-bases", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      setFormValue(undefined);
      await loadBases(String(saved.knowledge_base_uuid));
      setNotice({ type: "success", text: editing ? "知识库已更新" : "知识库已创建" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  const removeBase = async () => {
    if (!active || !window.confirm(`确认归档知识库“${active.name}”？`)) return;
    setBusy("base");
    setNotice(null);
    try {
      await api(`/api/v1/knowledge-bases/${activeId}`, { method: "DELETE" });
      setInspector(null);
      await loadBases();
      setNotice({ type: "success", text: "知识库已归档" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  const upload = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !activeId) return;
    setBusy("upload");
    setUploadProgress(0);
    setNotice(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const saved = await uploadApiForm(
        `/api/v1/knowledge-bases/${activeId}/documents`,
        body,
        { onProgress: setUploadProgress },
      );
      await loadDocuments(activeId);
      await loadBases(activeId);
      setSelectedDocumentId(saved.document_uuid);
      setNotice({ type: "success", text: "文档已上传，正在建立索引" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
      setUploadProgress(null);
    }
  };

  const loadPreview = async (document, version) => {
    setPreview(null);
    try {
      setPreview(await api(`/api/v1/knowledge-bases/${activeId}/documents/${document.document_uuid}/versions/${version}/preview`));
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    }
  };

  const detailRouteFor = (document) => {
    const params = new URLSearchParams({
      base: activeId,
      document: document.document_uuid,
      scroll: String(Math.round(contentRef.current?.scrollTop || 0)),
    });
    for (const key of ["workspace", "from", "return"]) {
      if (query.get(key)) params.set(key, query.get(key));
    }
    return `knowledge-document?${params.toString()}`;
  };

  const openDocumentDetail = (document) => {
    setSelectedDocumentId(document.document_uuid);
    location.hash = detailRouteFor(document);
  };

  const openInspector = async (document) => {
    setSelectedDocumentId(document.document_uuid);
    if (window.matchMedia("(max-width: 700px)").matches) {
      openDocumentDetail(document);
      return;
    }
    setInspector(document);
    setPreview(null);
    await loadPreview(document, document.version);
  };

  const reindex = async (document) => {
    setBusy(`reindex:${document.document_uuid}`);
    setNotice(null);
    try {
      await api(`/api/v1/knowledge-bases/${activeId}/documents/${document.document_uuid}:reindex`, { method: "POST" });
      await loadDocuments(activeId);
      setNotice({ type: "success", text: "已提交重新索引" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  const removeDocument = async (document) => {
    if (!window.confirm(`确认删除文档“${document.filename}”？`)) return;
    setBusy(`delete:${document.document_uuid}`);
    try {
      await api(`/api/v1/knowledge-bases/${activeId}/documents/${document.document_uuid}`, { method: "DELETE" });
      setInspector(null);
      await Promise.all([loadDocuments(activeId), loadBases(activeId)]);
      setNotice({ type: "success", text: "文档已删除" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  const verifyRetrieval = async (event) => {
    event.preventDefault();
    const value = retrievalQuery.trim();
    if (!value || !activeId) return;
    setBusy("search");
    setRetrievalError("");
    setRetrievalResult(null);
    try {
      setRetrievalResult(await api("/api/v1/knowledge-bases:search", {
        method: "POST",
        body: JSON.stringify({
          query: value,
          knowledge_base_uuids: [activeId],
          workspace_uuid: workspaceUuid || null,
          top_k: 8,
          filters: { document_type: [] },
        }),
      }));
    } catch (reason) {
      setRetrievalError(messageOf(reason));
    } finally {
      setBusy("");
    }
  };

  const toggleBinding = async (checked) => {
    if (!workspaceContext || !activeId) return;
    setBusy("binding");
    try {
      const nextIds = checked
        ? [...new Set([...boundIds, activeId])]
        : boundIds.filter((id) => id !== activeId);
      const { context_uuid: _contextUuid, revision: _revision, ...configuration } = workspaceContext;
      const saved = await api(`/api/v1/analysis-workspaces/${workspaceUuid}/context`, {
        method: "PUT",
        body: JSON.stringify({ ...configuration, knowledge_base_uuids: nextIds }),
      });
      setWorkspaceContext(saved);
      setNotice({ type: "success", text: checked ? "已用于当前工作台" : "已从当前工作台移除" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  return <main className="workspace knowledge-center-page">
    <Sidebar page="knowledge" />
    <section className="workspace-main">
      <Topbar />
      <div className={`knowledge-center ${inspector ? "inspector-open" : ""}`}>
        <header className="kb-page-header">
          <div>
            {query.get("from") && <button type="button" className="kb-back" onClick={() => { location.hash = returnRoute; }}><ArrowLeft />返回工作台</button>}
            <h1>企业知识库</h1>
            <p>企业资料资产</p>
          </div>
          <div className="kb-header-stats">
            <span><small>知识库</small><strong>{bases.length}</strong></span>
            <span><small>可检索文档</small><strong>{readyCount}</strong></span>
            <button type="button" className="kb-primary" onClick={() => setFormValue(null)}><Plus />新建知识库</button>
          </div>
        </header>
        {notice && <div className={`kb-notice ${notice.type}`}><span>{notice.type === "success" ? <Check /> : <WarningCircle />}{notice.text}</span><button type="button" title="关闭提示" onClick={() => setNotice(null)}><X /></button></div>}
        <div className="kb-layout">
          <aside className="kb-library-list">
            <label><MagnifyingGlass /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索知识库" /></label>
            <nav>
              {visibleBases.map((item) => <button type="button" key={item.knowledge_base_uuid} className={String(item.knowledge_base_uuid) === activeId ? "active" : ""} onClick={() => {
                setSelectedDocumentId("");
                setActiveId(String(item.knowledge_base_uuid));
              }}>
                <span className="kb-base-icon">{item.visibility === "tenant" ? <UsersThree /> : <LockKey />}</span>
                <span><strong>{item.name}</strong><small>{item.ready_document_count || 0}/{item.document_count || 0} 可检索</small></span>
                {boundIds.includes(String(item.knowledge_base_uuid)) && <LinkSimple title="用于当前工作台" />}
              </button>)}
              {!loading && !visibleBases.length && <div className="kb-list-empty"><Books /><span>{search ? "没有匹配的知识库" : "尚未创建知识库"}</span></div>}
            </nav>
          </aside>
          <section className="kb-content" ref={contentRef}>
            {loading ? <div className="kb-loading"><SpinnerGap />正在读取知识库</div> : active ? <>
              <header className="kb-content-header">
                <div>
                  <span>{active.visibility === "tenant" ? <><UsersThree />企业共享</> : <><LockKey />仅自己</>}</span>
                  <h2>{active.name}</h2>
                  <p>{active.description || "未填写说明"}</p>
                </div>
                <div>
                  <button type="button" title="编辑知识库" onClick={() => setFormValue(active)}><PencilSimple /></button>
                  <button type="button" title="刷新" onClick={() => Promise.all([loadBases(activeId), loadDocuments(activeId)])}><ArrowClockwise /></button>
                  <button type="button" className="danger" title="归档知识库" onClick={removeBase} disabled={busy === "base"}><Trash /></button>
                  <label className="kb-upload-button"><CloudArrowUp /><span>上传文档</span><input type="file" accept=".pdf,.xlsx,.jpg,.jpeg,.png" onChange={upload} disabled={Boolean(busy)} /></label>
                </div>
              </header>
              {workspaceUuid && <label className={`kb-binding ${workspaceContext ? "" : "disabled"}`}>
                <span><LinkSimple /><span><strong>用于当前工作台</strong><small>{workspaceContext ? "限定该工作台的资料检索范围" : "当前工作台尚未同步"}</small></span></span>
                <input type="checkbox" disabled={!workspaceContext || busy === "binding"} checked={boundIds.includes(activeId)} onChange={(event) => toggleBinding(event.target.checked)} />
              </label>}
              {uploadProgress != null && <div className="kb-upload-progress"><span style={{ width: `${Math.max(3, uploadProgress * 100)}%` }} /><b>{Math.round(uploadProgress * 100)}%</b></div>}
              <section className="kb-retrieval">
                <header>
                  <div><MagnifyingGlass /><span><strong>检索验真</strong><small>直接验证当前知识库能否召回原文证据</small></span></div>
                  {retrievalResult && <span>相关度阈值 {Math.round(Number(retrievalResult.relevance_threshold || 0) * 100)}%</span>}
                </header>
                <form onSubmit={verifyRetrieval}>
                  <label>
                    <span className="sr-only">检索问题</span>
                    <input value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="输入问题或原文关键词" maxLength={2000} />
                  </label>
                  <button type="submit" disabled={!retrievalQuery.trim() || busy === "search"}>{busy === "search" ? <SpinnerGap /> : <MagnifyingGlass />}{busy === "search" ? "检索中" : "检索"}</button>
                </form>
                {retrievalError && <p role="alert" className="kb-retrieval-error"><WarningCircle />{retrievalError}</p>}
                {retrievalResult?.refused && <div className="kb-retrieval-empty"><WarningCircle /><span><strong>未找到达到阈值的证据</strong><small>系统不会把低相关片段作为依据；可改用文档中的具体术语重试。</small></span></div>}
                {!!retrievalResult?.matches?.length && <div className="kb-retrieval-results">
                  {retrievalResult.matches.map((match) => <article key={match.citation_uuid}>
                    <div>
                      <span><FileText /><strong>{match.document_name}</strong></span>
                      <small>v{match.document_version}{match.page ? ` · 第 ${match.page} 页` : ""} · 相关度 {Math.round(Number(match.score || 0) * 100)}%</small>
                      <p>{match.chunk_text}</p>
                    </div>
                    <button type="button" onClick={() => openDocumentDetail({ document_uuid: String(match.document_uuid) })}>打开文档</button>
                  </article>)}
                </div>}
              </section>
              <div className="kb-index-summary">
                <span><small>全部文档</small><strong>{documents.length}</strong></span>
                <span><small>可检索</small><strong>{documents.filter((item) => item.status === "ready").length}</strong></span>
                <span><small>处理中</small><strong>{pendingCount}</strong></span>
                <span><small>异常</small><strong>{documents.filter((item) => item.status === "failed").length}</strong></span>
              </div>
              <div className="kb-document-table">
                <table>
                  <thead><tr><th>文档</th><th>版本</th><th>索引状态</th><th>大小</th><th>更新时间</th><th><span className="sr-only">操作</span></th></tr></thead>
                  <tbody>{documents.map((item) => <tr
                    key={item.document_uuid}
                    className={item.document_uuid === selectedDocumentId ? "selected" : ""}
                    onClick={() => openDocumentDetail(item)}
                  >
                    <td><FileText /><span><strong>{item.filename}</strong><small>{item.document_type || item.mime_type}</small></span></td>
                    <td>v{item.version}</td>
                    <td><Status value={item.status} />{item.error_message && <small className="kb-row-error">{item.error_message}</small>}</td>
                    <td>{formatBytes(item.byte_size)}</td>
                    <td>{formatDate(item.updated_at)}</td>
                    <td>
                      <button type="button" className="kb-row-detail" onClick={(event) => { event.stopPropagation(); openDocumentDetail(item); }}><FileText /><span>查看详情</span></button>
                      <button type="button" title="快速预览" onClick={(event) => { event.stopPropagation(); openInspector(item); }}><Eye /></button>
                      <button type="button" title="重新索引" disabled={busy === `reindex:${item.document_uuid}`} onClick={(event) => { event.stopPropagation(); reindex(item); }}><ArrowClockwise /></button>
                      <button type="button" className="danger" title="删除文档" disabled={busy === `delete:${item.document_uuid}`} onClick={(event) => { event.stopPropagation(); removeDocument(item); }}><Trash /></button>
                    </td>
                  </tr>)}</tbody>
                </table>
                {!documents.length && <div className="kb-document-empty"><CloudArrowUp /><strong>暂无文档</strong><small>上传 PDF、XLSX、JPG 或 PNG</small></div>}
              </div>
            </> : <div className="kb-content-empty"><Books /><strong>尚未创建知识库</strong></div>}
          </section>
        </div>
        <DocumentInspector
          document={inspector}
          preview={preview}
          onClose={() => setInspector(null)}
          onOpenDetail={() => openDocumentDetail(inspector)}
        />
      </div>
    </section>
    {formValue !== undefined && <BaseForm value={formValue} busy={busy === "base"} onClose={() => setFormValue(undefined)} onSubmit={saveBase} />}
  </main>;
}
