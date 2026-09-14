import { useEffect, useMemo, useState } from "react";
import readXlsxFile from "read-excel-file";
import {
  ArrowRight,
  Check,
  CheckCircle,
  Clock,
  FileArrowUp,
  FilePdf,
  Images,
  MagnifyingGlass,
  PencilSimple,
  Plus,
  ShieldCheck,
  Sparkle,
  TrendUp,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api, apiWithMeta, idempotencyKey } from "./api.js";
import { ParentPageTab } from "./ParentPageTab.jsx";

const statusLabels = {
  ready: "解析完成",
  profile_pending: "待确认",
  draft: "未解析",
  archived: "已归档",
};

const sourceLabels = {
  confirmed_structured: "人工确认",
  user_input: "人工填写",
  document: "文档提取",
  image: "图像提取",
  inferred: "推断",
};
const attributeLabels = {
  dimensions: "产品尺寸",
  material: "材质工艺",
  factory_price: "出厂报价",
  moq: "最小起订量",
  customization: "定制能力",
  certifications: "产品认证",
  scenes: "适配场景",
  structure: "产品结构",
  style: "产品风格",
  target_market: "目标市场",
};

const emptyCreateForm = {
  sku: "",
  name: "",
  category_code: "sofa",
  dimensions: "",
  material: "",
  factory_price: "",
  moq: "",
  customization: "",
  certifications: "",
  scenes: "",
  description: "",
};

function productImage(sku) {
  return `/assets/hf-products/${encodeURIComponent(sku)}.webp`;
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function productCategory(product) {
  const inferred = String(product.name || "").replace(String(product.sku || ""), "").trim();
  return inferred || "其他家具";
}

function normalizedStatus(product) {
  if (product.has_conflicts) return "conflict";
  return product.analysis_status;
}

function ProductStatus({ product }) {
  const value = normalizedStatus(product);
  return (
    <span className={`catalog-status ${value}`}>
      {value === "ready" ? <CheckCircle weight="fill" /> : <WarningCircle weight="fill" />}
      {value === "conflict" ? "参数冲突" : statusLabels[value] || value}
    </span>
  );
}

function UploadZone({ files, setFiles }) {
  const previews = files.filter((file) => file.type.startsWith("image/")).slice(0, 4);
  return (
    <div className="product-upload-block">
      <label className="product-upload-zone">
        <input
          type="file"
          multiple
          accept=".jpg,.jpeg,.png,.webp,.pdf,.xlsx"
          onChange={(event) => setFiles(Array.from(event.target.files || []))}
        />
        <UploadSimple />
        <strong>上传产品实拍图、细节图与产品资料</strong>
        <span>支持 JPG、PNG、WEBP、PDF、XLSX，可多选</span>
      </label>
      {files.length > 0 && (
        <div className="product-file-preview">
          {previews.map((file) => <img key={`${file.name}-${file.size}`} src={URL.createObjectURL(file)} alt={file.name} />)}
          <span><Images /><b>{files.length}</b><small>份素材待上传</small></span>
          <button type="button" onClick={() => setFiles([])}><X /> 清空</button>
        </div>
      )}
    </div>
  );
}

function CreateProductModal({ onClose, onCreated }) {
  const [form, setForm] = useState(emptyCreateForm);
  const [files, setFiles] = useState([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [parseReady, setParseReady] = useState(null);
  const update = (key) => (event) => setForm((value) => ({ ...value, [key]: event.target.value }));

  useEffect(() => {
    api("/health/ready")
      .then((data) => setParseReady(data?.model_router === "configured"))
      .catch(() => setParseReady(false));
  }, []);

  const attributes = () => [
    ["dimensions", "产品尺寸", form.dimensions, null],
    ["material", "材质工艺", form.material, null],
    ["factory_price", "出厂报价", form.factory_price, "USD"],
    ["moq", "最小起订量", form.moq, "件"],
    ["customization", "定制能力", form.customization, null],
    ["certifications", "产品认证", form.certifications, null],
    ["scenes", "适配场景", form.scenes, null],
  ].filter(([, , value]) => value.trim()).map(([code, name, value, unit]) => ({
    attribute_code: code,
    attribute_name: name,
    value_type: "string",
    attribute_value: value.trim(),
    unit,
    source_type: "user_input",
    source_locator: { input: "product_create_form", priority: 1 },
    confidence: 1,
    confirmation_status: "confirmed",
  }));

  const uploadGroup = async (productId, sourceType, uploadFiles) => {
    if (!uploadFiles.length) return;
    const body = new FormData();
    body.append("source_type", sourceType);
    uploadFiles.forEach((file) => body.append("files", file));
    await api(`/api/v1/products/${productId}/assets:parse`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey(`product-assets-${sourceType}`) },
      body,
    });
  };

  const submit = async (event) => {
    event.preventDefault();
    if (!form.sku.trim() || !form.name.trim()) return setMessage("请填写产品名称和唯一 SKU");
    setSaving(true);
    setMessage("正在创建产品档案…");
    try {
      const created = await api("/api/v1/products", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("product-create") },
        body: JSON.stringify({
          sku: form.sku.trim(),
          name: form.name.trim(),
          category_code: form.category_code,
          description: form.description.trim() || null,
        }),
      });
      const manualAttributes = attributes();
      if (manualAttributes.length) {
        const detail = await apiWithMeta(`/api/v1/products/${created.product_id}`);
        await api(`/api/v1/products/${created.product_id}`, {
          method: "PATCH",
          headers: { "If-Match": detail.response.headers.get("etag") },
          body: JSON.stringify({ analysis_status: "draft", attributes: manualAttributes }),
        });
      }
      const images = files.filter((file) => file.type.startsWith("image/"));
      const documents = files.filter((file) => !file.type.startsWith("image/"));
      setMessage(files.length
        ? (parseReady === false
          ? "档案已创建。模型服务未配置，上传资料不会进入解析队列。"
          : "档案已创建，正在解析上传素材…")
        : "产品档案创建成功");
      if (parseReady !== false) {
        await uploadGroup(created.product_id, "image", images);
        await uploadGroup(created.product_id, "document", documents);
      }
      onCreated(created.product_id);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="product-modal-backdrop" onMouseDown={onClose}>
      <form className="product-create-modal" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()}>
        <header><div><span>NEW PRODUCT PROFILE</span><h2>新增产品档案</h2><p>{parseReady === false
          ? "资料解析依赖已配置的模型服务；当前未配置时上传资料会被拒绝，请先填写工厂参数。"
          : "人工参数具有最高优先级，资料解析不会静默覆盖工厂真实数据。"}</p></div><button type="button" onClick={onClose}><X /></button></header>
        <div className="product-create-scroll">
          <UploadZone files={files} setFiles={setFiles} />
          <section className="product-form-section">
            <h3>基础信息 <small>必填</small></h3>
            <div className="product-field-grid three">
              <label>产品名称<input value={form.name} onChange={update("name")} placeholder="例如：云感模块沙发" /></label>
              <label>唯一 SKU<input value={form.sku} onChange={update("sku")} placeholder="例如：HF-A0396-1" /></label>
              <label>所属品类<select value={form.category_code} onChange={update("category_code")}><option value="sofa">沙发</option></select></label>
            </div>
          </section>
          <section className="product-form-section">
            <h3>工厂真实参数 <small>选填 · 人工填写优先</small></h3>
            <div className="product-field-grid">
              <label>产品尺寸<input value={form.dimensions} onChange={update("dimensions")} placeholder="225 × 95 × 86 cm" /></label>
              <label>材质工艺<input value={form.material} onChange={update("material")} placeholder="科技布 / 实木框架" /></label>
              <label>出厂报价（USD）<input type="number" value={form.factory_price} onChange={update("factory_price")} placeholder="299" /></label>
              <label>MOQ（件）<input type="number" value={form.moq} onChange={update("moq")} placeholder="20" /></label>
              <label>定制能力<input value={form.customization} onChange={update("customization")} placeholder="面料、颜色、尺寸可定制" /></label>
              <label>产品认证<input value={form.certifications} onChange={update("certifications")} placeholder="FSC、CARB、BS5852" /></label>
              <label className="full">适配场景<input value={form.scenes} onChange={update("scenes")} placeholder="小户型客厅、公寓、家庭影音室" /></label>
              <label className="full">产品说明<textarea value={form.description} onChange={update("description")} placeholder="补充产品卖点、包装方式与制造限制" /></label>
            </div>
          </section>
        </div>
        <footer><span>{message}</span><button type="button" onClick={onClose}>取消</button><button className="primary-save" disabled={saving}>{saving ? "正在保存…" : files.length ? "创建并解析资料" : "创建产品档案"}</button></footer>
      </form>
    </div>
  );
}

function BatchImportModal({ onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!file) return setProgress("请先选择 Excel 文件");
    setBusy(true);
    try {
      const rows = await readXlsxFile(file);
      const headers = rows[0].map((value) => String(value || "").trim().toLowerCase());
      const find = (...names) => headers.findIndex((header) => names.includes(header));
      const skuIndex = find("sku", "sku编码", "产品sku");
      const nameIndex = find("产品名称", "name", "名称");
      const descriptionIndex = find("产品说明", "description", "说明");
      if (skuIndex < 0 || nameIndex < 0) throw new Error("模板必须包含“SKU”和“产品名称”列");
      const dataRows = rows.slice(1).filter((row) => row[skuIndex] && row[nameIndex]);
      if (!dataRows.length) throw new Error("Excel 中没有可导入的产品记录");
      let succeeded = 0;
      const failures = [];
      for (const [index, row] of dataRows.entries()) {
        setProgress(`正在导入 ${index + 1} / ${dataRows.length}`);
        try {
          await api("/api/v1/products", {
            method: "POST",
            headers: { "Idempotency-Key": idempotencyKey("product-bulk") },
            body: JSON.stringify({
              sku: String(row[skuIndex]).trim(),
              name: String(row[nameIndex]).trim(),
              category_code: "sofa",
              description: descriptionIndex >= 0 && row[descriptionIndex] ? String(row[descriptionIndex]) : null,
            }),
          });
          succeeded += 1;
        } catch (error) {
          failures.push(`${row[skuIndex]}：${error.message}`);
        }
      }
      setProgress(`导入完成：成功 ${succeeded} 条${failures.length ? `，失败 ${failures.length} 条` : ""}`);
      onImported();
    } catch (error) {
      setProgress(error.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="product-modal-backdrop" onMouseDown={onClose}>
      <section className="product-batch-modal" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><span>BULK IMPORT</span><h2>Excel 批量建档</h2><p>适合工厂一次导入大批量产品基础档案</p></div><button onClick={onClose}><X /></button></header>
        <div className="batch-template"><FilePdf /><span><strong>Excel 模板字段</strong><small>必填：SKU、产品名称　选填：产品说明</small></span></div>
        <label className="batch-file-picker"><input type="file" accept=".xlsx" onChange={(event) => setFile(event.target.files?.[0] || null)} /><FileArrowUp /><strong>{file?.name || "选择 .xlsx 文件"}</strong><span>点击选择或拖入产品档案表</span></label>
        <p className="batch-progress">{progress}</p>
        <footer><button onClick={onClose}>取消</button><button className="primary-save" onClick={submit} disabled={busy}>{busy ? "导入中…" : "开始批量导入"}</button></footer>
      </section>
    </div>
  );
}

function ProductDetailDrawer({ productId, onClose, onUpdated, startAnalysis }) {
  const [detail, setDetail] = useState(null);
  const [etag, setEtag] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);
  const [editValue, setEditValue] = useState("");

  const load = () => apiWithMeta(`/api/v1/products/${productId}`)
    .then(({ data, response }) => { setDetail(data); setEtag(response.headers.get("etag") || ""); setError(""); })
    .catch((reason) => setError(reason.message));
  useEffect(() => {
    load();
  }, [productId]);

  const correct = async (attribute) => {
    try {
      await api(`/api/v1/products/${productId}`, {
        method: "PATCH",
        headers: { "If-Match": etag },
        body: JSON.stringify({ attributes: [{
          ...attribute,
          attribute_name: attribute.attribute_name || attributeLabels[attribute.attribute_code] || attribute.attribute_code,
          attribute_value: editValue,
          source_type: "user_input",
          source_locator: { input: "manual_conflict_resolution", priority: 1 },
          confidence: 1,
          confirmation_status: "confirmed",
        }] }),
      });
      setEditing(null);
      await load();
      onUpdated();
    } catch (reason) { setError(reason.message); }
  };

  return (
    <div className="product-drawer-backdrop" onMouseDown={onClose}>
      <aside className="product-detail-drawer" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><ParentPageTab className="compact" label="产品中心" current="产品档案" onBack={onClose} /><span>PRODUCT PROFILE</span><h2>{detail?.name || "正在读取产品档案…"}</h2><p>{detail ? `${detail.sku} · ${productCategory(detail)}` : ""}</p></div><button onClick={onClose} aria-label="关闭产品档案"><X /></button></header>
        {error ? <div className="detail-error"><WarningCircle />{error}<button onClick={load}>重试</button></div> : detail && <>
          <div className="detail-scroll">
            <section className="detail-visual"><img src={productImage(detail.sku)} alt={`${detail.name} 产品图`} onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = "/assets/furniscope-mark.png"; }} /><div><ProductStatus product={{ ...detail, has_conflicts: detail.attributes.some((item) => item.confirmation_status === "conflicted") }} /><span>画像完整度 <b>{Math.round((detail.completeness_score || 0) * 100)}%</b></span><span>画像版本 <b>V{detail.profile_version || 0}</b></span></div></section>
            <section className="source-priority"><ShieldCheck /><div><strong>多源数据优先级</strong><p><b>人工填写</b><i />文档提取<i />AI 视觉识别</p></div></section>
            {detail.attributes.some((item) => item.confirmation_status === "conflicted") && <section className="conflict-alert"><WarningCircle weight="fill" /><div><strong>检测到参数冲突</strong><p>AI 识别结果与工厂人工参数不一致。当前以人工值为准，请核验后确认。</p></div></section>}
            <section className="detail-section"><h3>基础资料</h3><dl><div><dt>产品名称</dt><dd>{detail.name}</dd></div><div><dt>SKU</dt><dd>{detail.sku}</dd></div><div><dt>所属品类</dt><dd>{productCategory(detail)}</dd></div><div><dt>产品说明</dt><dd>{detail.description || "暂未填写"}</dd></div><div><dt>已解析素材</dt><dd>{detail.source_summary?.file_count || 0} 份</dd></div></dl></section>
            <section className="detail-section"><h3>标准化产品参数 <small>{detail.attributes.length} 项</small></h3><div className="attribute-list">
              {detail.attributes.filter((attribute) => attribute.attribute_code !== "color").length ? detail.attributes.filter((attribute) => attribute.attribute_code !== "color").map((attribute) => {
                const conflict = attribute.confirmation_status === "conflicted";
                return <article className={conflict ? "conflicted" : ""} key={attribute.attribute_code}>
                  <div><strong>{attributeLabels[attribute.attribute_code] || attribute.attribute_name || attribute.attribute_code}</strong><span className={`source ${attribute.source_type}`}>{sourceLabels[attribute.source_type] || attribute.source_type}</span></div>
                  {editing === attribute.attribute_code ? <div className="attribute-edit"><input value={editValue} onChange={(event) => setEditValue(event.target.value)} autoFocus /><button onClick={() => correct(attribute)}><Check />确认</button><button onClick={() => setEditing(null)}>取消</button></div> : <>
                    <p>{String(attribute.attribute_value)} {attribute.unit || ""}</p>
                    <footer><span>置信度 {Math.round(attribute.confidence * 100)}%</span>{conflict && <span className="conflict-value">AI 识别：{String(attribute.source_locator?.conflicting_value ?? "存在差异")}</span>}<button onClick={() => { setEditing(attribute.attribute_code); setEditValue(String(attribute.attribute_value)); }}><PencilSimple />{conflict ? "一键修正" : "编辑"}</button></footer>
                  </>}
                </article>;
              }) : <div className="no-attributes"><Sparkle /><strong>尚未生成产品画像</strong><p>上传产品图片或资料后，AI 将自动提取标准化参数。</p></div>}
            </div></section>
          </div>
          <footer><button onClick={onClose}>关闭</button><button className="primary-save" disabled={detail.analysis_status !== "ready" || detail.attributes.some((item) => item.confirmation_status === "conflicted")} onClick={() => startAnalysis(detail)}>使用该产品开始分析 <ArrowRight /></button></footer>
        </>}
      </aside>
    </div>
  );
}

export function ProductCatalog({ Sidebar, Topbar }) {
  const [products, setProducts] = useState([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [created, setCreated] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(null);
  const [selectedId, setSelectedId] = useState(() => new URLSearchParams(location.hash.split("?")[1] || "").get("product"));

  const categories = useMemo(() => [...new Set(products.map(productCategory))].sort((a, b) => a.localeCompare(b, "zh-CN")), [products]);

  const load = () => {
    setLoading(true); setError("");
    api("/api/v1/products?page_size=100")
      .then((page) => setProducts(page.items || []))
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const now = Date.now();
    return products.filter((product) => {
      const productStatus = normalizedStatus(product);
      const days = created === "all" ? Infinity : Number(created);
      const inDate = days === Infinity || now - new Date(product.created_at || product.updated_at).getTime() <= days * 86400000;
      return (status === "all" || productStatus === status) &&
        (category === "all" || productCategory(product) === category) && inDate &&
        (!keyword || `${product.sku} ${product.name}`.toLowerCase().includes(keyword));
    });
  }, [products, query, status, category, created]);

  const startAnalysis = (product) => { location.hash = `workflow?product=${product.product_id}&from=products`; };
  const startForecast = (product) => {
    location.hash = `forecast?sku=${encodeURIComponent(product.sku)}&product=${product.product_id}&from=products`;
  };
  const createdProduct = (productId) => { setModal(null); load(); setSelectedId(productId); };

  return (
    <main className="workspace product-catalog-page">
      <Sidebar page="products" />
      <section className="workspace-main"><Topbar /><div className="product-catalog-canvas">
        <header className="product-catalog-header"><div><span className="eyebrow">ENTERPRISE PRODUCT PROFILES</span><h1>产品中心</h1><p>统一管理工厂产品资料，由多模态 AI 生成可校验的标准化产品画像。</p></div><div className="catalog-header-actions"><button onClick={() => setModal("batch")}><FileArrowUp /> Excel 批量导入</button><button className="catalog-primary" onClick={() => setModal("create")}><Plus /> 新增产品</button></div></header>
        <section className="catalog-toolbar-card">
          <label className="catalog-search"><MagnifyingGlass /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索产品名称或 SKU" aria-label="搜索产品" /></label>
          <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="产品品类"><option value="all">全部品类</option>{categories.map((item) => <option value={item} key={item}>{item}</option>)}</select>
          <select value={created} onChange={(event) => setCreated(event.target.value)} aria-label="创建时间"><option value="all">全部创建时间</option><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="90">近 90 天</option></select>
          <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="解析状态"><option value="all">全部解析状态</option><option value="ready">解析完成</option><option value="draft">未解析</option><option value="profile_pending">待确认</option><option value="conflict">参数冲突</option></select>
          <span>共 <b>{filtered.length}</b> 个产品</span>
        </section>
        {error && <div className="catalog-message error" role="alert"><WarningCircle /><span><strong>产品列表加载失败</strong>{error}</span><button onClick={load}>重新加载</button></div>}
        {loading ? <div className="catalog-loading">{Array.from({ length: 6 }, (_, index) => <i key={index} />)}</div> : filtered.length ? <section className="product-record-list">
          {filtered.map((product) => <article className="product-record-card" key={product.product_id} onClick={() => setSelectedId(product.product_id)}>
            <div className="record-image"><img src={productImage(product.sku)} alt={`${product.name} 产品图`} onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = "/assets/furniscope-mark.png"; }} /></div>
            <div className="record-main"><div><ProductStatus product={product} /><span className="record-category">{productCategory(product)}</span></div><h2>{product.name}</h2><p>SKU <b>{product.sku}</b></p><div className="record-commercial"><span>MOQ <b>{product.moq || "—"}</b></span><span>出厂价 <b>{product.factory_price ? `$${product.factory_price}` : "—"}</b></span></div></div>
            <div className="record-meta"><span><Clock />创建于 {formatDate(product.created_at)}</span><span><Sparkle />{product.current_profile_version_id ? "标准化画像已生成" : "等待上传资料并解析"}</span></div>
            <div className="record-actions"><button onClick={(event) => { event.stopPropagation(); setSelectedId(product.product_id); }}>查看档案</button><button className="record-forecast" onClick={(event) => { event.stopPropagation(); startForecast(product); }}><TrendUp />销量预测</button><button className="record-analyze" disabled={product.analysis_status !== "ready" || product.has_conflicts} onClick={(event) => { event.stopPropagation(); startAnalysis(product); }}>{product.has_conflicts ? "先处理冲突" : product.analysis_status === "ready" ? "开始分析" : "先确认画像"}<ArrowRight /></button></div>
          </article>)}
        </section> : !error ? <div className="catalog-empty"><MagnifyingGlass /><strong>没有找到匹配的产品</strong><p>尝试修改搜索词或筛选条件。</p><button onClick={() => { setQuery(""); setStatus("all"); setCategory("all"); setCreated("all"); }}>清除筛选</button></div> : null}
      </div></section>
      {modal === "create" && <CreateProductModal onClose={() => setModal(null)} onCreated={createdProduct} />}
      {modal === "batch" && <BatchImportModal onClose={() => setModal(null)} onImported={load} />}
      {selectedId && <ProductDetailDrawer productId={selectedId} onClose={() => { setSelectedId(null); window.history.replaceState(null, "", "#products"); }} onUpdated={load} startAnalysis={startAnalysis} />}
    </main>
  );
}
