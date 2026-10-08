import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise,
  ArrowLeft,
  Books,
  Check,
  CheckCircle,
  ClockCounterClockwise,
  CloudArrowUp,
  FileText,
  SpinnerGap,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, uploadApiForm } from "./api.js";
import { DocumentPreviewContent } from "./KnowledgeDocumentPreview.jsx";
import "./knowledge-base-center.css";

const pendingStatuses = new Set(["uploaded", "parsing", "chunking", "embedding"]);
const statusLabels = {
  uploaded: "等待索引",
  parsing: "内容解析",
  chunking: "内容切分",
  embedding: "向量生成",
  ready: "可检索",
  failed: "索引失败",
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  cancelled: "已取消",
};

const messageOf = (error) => error instanceof Error ? error.message : String(error);
const formatBytes = (value) => {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
};
const formatDate = (value) => value ? new Date(value).toLocaleString("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
}) : "—";

function Status({ value }) {
  return <span className={`kb-status ${value || "unknown"}`}>
    {pendingStatuses.has(value) || value === "queued" || value === "running"
      ? <SpinnerGap />
      : value === "ready" || value === "succeeded" ? <CheckCircle /> : <WarningCircle />}
    {statusLabels[value] || value || "未知"}
  </span>;
}

export function KnowledgeDocumentDetail({ Sidebar, Topbar }) {
  const query = useMemo(() => new URLSearchParams(location.hash.split("?")[1] || ""), []);
  const baseId = query.get("base") || "";
  const documentId = query.get("document") || "";
  const versionInput = useRef(null);
  const [base, setBase] = useState(null);
  const [document, setDocument] = useState(null);
  const [versions, setVersions] = useState([]);
  const [preview, setPreview] = useState(null);
  const [selectedVersion, setSelectedVersion] = useState(null);
  const [activeSheet, setActiveSheet] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [uploadProgress, setUploadProgress] = useState(null);
  const [notice, setNotice] = useState(null);

  const loadPreview = useCallback(async (version) => {
    if (!baseId || !documentId || !version) return;
    setPreview(null);
    setActiveSheet("");
    const value = await api(`/api/v1/knowledge-bases/${baseId}/documents/${documentId}/versions/${version}/preview`);
    setPreview(value);
    setSelectedVersion(version);
  }, [baseId, documentId]);

  const loadDocument = useCallback(async (preferredVersion = null) => {
    const [baseValue, documentValue, versionPage] = await Promise.all([
      api(`/api/v1/knowledge-bases/${baseId}`),
      api(`/api/v1/knowledge-bases/${baseId}/documents/${documentId}`),
      api(`/api/v1/knowledge-bases/${baseId}/documents/${documentId}/versions?page_size=100`),
    ]);
    setBase(baseValue);
    setDocument(documentValue);
    setVersions(versionPage.items || []);
    const targetVersion = preferredVersion || documentValue.version;
    await loadPreview(targetVersion);
    return documentValue;
  }, [baseId, documentId, loadPreview]);

  useEffect(() => {
    let active = true;
    if (!baseId || !documentId) {
      setNotice({ type: "error", text: "文档地址缺少必要参数" });
      setLoading(false);
      return undefined;
    }
    loadDocument()
      .catch((reason) => active && setNotice({ type: "error", text: messageOf(reason) }))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [baseId, documentId, loadDocument]);

  useEffect(() => {
    if (!document || !pendingStatuses.has(document.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const current = await api(`/api/v1/knowledge-bases/${baseId}/documents/${documentId}`);
        setDocument(current);
        if (!pendingStatuses.has(current.status)) await loadDocument(current.version);
      } catch {
        // Keep the last successful state while the background index job is polled.
      }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [baseId, document, documentId, loadDocument]);

  const backToList = () => {
    const params = new URLSearchParams();
    params.set("base", baseId);
    params.set("document", documentId);
    params.set("scroll", query.get("scroll") || "0");
    for (const key of ["workspace", "from", "return"]) {
      if (query.get(key)) params.set(key, query.get(key));
    }
    location.hash = `knowledge?${params.toString()}`;
  };

  const uploadVersion = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy("upload");
    setUploadProgress(0);
    setNotice(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const saved = await uploadApiForm(
        `/api/v1/knowledge-bases/${baseId}/documents/${documentId}/versions`,
        body,
        { onProgress: setUploadProgress },
      );
      await loadDocument(saved.version);
      setNotice({ type: "success", text: "新版本已上传，正在建立索引" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
      setUploadProgress(null);
    }
  };

  const restoreVersion = async (version) => {
    if (!window.confirm(`确认将 v${version} 回溯为新的当前版本？`)) return;
    setBusy(`restore:${version}`);
    setNotice(null);
    try {
      const saved = await api(
        `/api/v1/knowledge-bases/${baseId}/documents/${documentId}/versions/${version}:restore`,
        { method: "POST" },
      );
      await loadDocument(saved.version);
      setNotice({ type: "success", text: `已从 v${version} 创建新版本` });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  const reindex = async () => {
    setBusy("reindex");
    setNotice(null);
    try {
      await api(`/api/v1/knowledge-bases/${baseId}/documents/${documentId}:reindex`, { method: "POST" });
      const current = await api(`/api/v1/knowledge-bases/${baseId}/documents/${documentId}`);
      setDocument(current);
      setNotice({ type: "success", text: "已提交重新索引" });
    } catch (reason) {
      setNotice({ type: "error", text: messageOf(reason) });
    } finally {
      setBusy("");
    }
  };

  return <main className="workspace knowledge-document-page">
    <Sidebar page="knowledge" />
    <section className="workspace-main">
      <Topbar />
      <div className="kb-document-detail">
        <header className="kb-detail-header">
          <button type="button" className="kb-detail-back" onClick={backToList}><ArrowLeft />返回知识库</button>
          <div>
            <span><Books />{base?.name || "企业知识库"}</span>
            <h1 title={document?.filename}>{document?.filename || "文档详情"}</h1>
            <p>内容、索引与版本记录</p>
          </div>
          <div className="kb-detail-header-actions">
            <Status value={document?.status} />
            <button type="button" className="kb-primary" onClick={() => versionInput.current?.click()} disabled={!document || Boolean(busy)}>
              <CloudArrowUp />上传新版本
            </button>
            <input
              ref={versionInput}
              type="file"
              hidden
              accept={document?.filename ? `.${document.filename.split(".").pop()}` : ".pdf,.xlsx,.jpg,.jpeg,.png"}
              onChange={uploadVersion}
            />
          </div>
        </header>
        {notice && <div className={`kb-notice ${notice.type}`}>
          <span>{notice.type === "success" ? <Check /> : <WarningCircle />}{notice.text}</span>
          <button type="button" title="关闭提示" onClick={() => setNotice(null)}><X /></button>
        </div>}
        {uploadProgress != null && <div className="kb-detail-upload-progress">
          <span style={{ width: `${Math.max(3, uploadProgress * 100)}%` }} />
          <b>{Math.round(uploadProgress * 100)}%</b>
        </div>}
        {loading ? <div className="kb-detail-loading"><SpinnerGap />正在读取文档</div> : document ? <div className="kb-detail-layout">
          <section className="kb-detail-reader">
            <header>
              <div>
                <span>内容预览</span>
                <strong>版本 v{selectedVersion || document.version}</strong>
                {preview?.extraction_method && <b>{preview.extraction_method}</b>}
              </div>
              {selectedVersion !== document.version && <small>当前正在查看历史版本</small>}
            </header>
            <div className="kb-detail-preview">
              <DocumentPreviewContent
                preview={preview}
                activeSheet={activeSheet}
                onSheetChange={setActiveSheet}
              />
            </div>
          </section>
          <aside className="kb-detail-side">
            <section className="kb-detail-index">
              <header><span>索引状态</span><Status value={document.status} /></header>
              <dl>
                <div><dt>当前版本</dt><dd>v{document.version}</dd></div>
                <div><dt>解析范围</dt><dd>{preview?.page_or_sheet_count ? `${preview.page_or_sheet_count} 页/工作表` : "—"}</dd></div>
                <div><dt>更新时间</dt><dd>{formatDate(document.updated_at)}</dd></div>
              </dl>
              {document.error_message && <p>{document.error_message}</p>}
              <button type="button" onClick={reindex} disabled={busy === "reindex"}><ArrowClockwise />重新索引</button>
            </section>
            <section className="kb-detail-source">
              <header>来源信息</header>
              <dl>
                <div><dt>知识库</dt><dd>{base?.name || "—"}</dd></div>
                <div><dt>文件类型</dt><dd>{document.document_type || document.mime_type}</dd></div>
                <div><dt>文件大小</dt><dd>{formatBytes(document.byte_size)}</dd></div>
                <div><dt>SHA256</dt><dd title={document.sha256}>{document.sha256.slice(0, 16)}…</dd></div>
                <div><dt>创建时间</dt><dd>{formatDate(document.created_at)}</dd></div>
                <div><dt>文档标识</dt><dd title={document.document_uuid}>{document.document_uuid}</dd></div>
              </dl>
            </section>
            <section className="kb-detail-versions">
              <header><span>版本历史</span><b>{versions.length}</b></header>
              <div>
                {versions.map((item) => <article key={item.document_version_uuid} className={item.version === selectedVersion ? "selected" : ""}>
                  <button type="button" className="kb-version-preview" onClick={() => loadPreview(item.version)}>
                    <span><strong>v{item.version}</strong>{item.is_current && <b>当前</b>}</span>
                    <Status value={item.index_status} />
                    <small>{formatDate(item.created_at)} · {formatBytes(item.byte_size)}</small>
                  </button>
                  {!item.is_current && <button
                    type="button"
                    className="kb-version-restore"
                    title={`将 v${item.version} 回溯为新版本`}
                    disabled={busy === `restore:${item.version}`}
                    onClick={() => restoreVersion(item.version)}
                  >
                    <ClockCounterClockwise />
                  </button>}
                </article>)}
              </div>
            </section>
          </aside>
        </div> : <div className="kb-detail-loading"><FileText />无法读取文档</div>}
      </div>
    </section>
  </main>;
}
