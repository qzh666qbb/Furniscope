import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArrowRight,
  ArrowsOut,
  Check,
  CheckCircle,
  Clock,
  Database,
  Eye,
  FileArrowUp,
  FloppyDisk,
  Images,
  LinkSimple,
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
import { ProductImportWizard } from "./ProductImportWizard.jsx";
import { ProductFacts } from "./ProductFacts.jsx";

const statusLabels = {
  ready: "解析完成",
  profile_pending: "待确认",
  draft: "未解析",
  archived: "已归档",
};

const categoryLabels = {
  sofa: "沙发",
  chair: "椅类",
  table: "桌类",
  bed: "床类",
  storage: "收纳",
  other: "其他",
};

const lifecycleLabels = {
  concept: "概念",
  sample: "样品",
  active: "在售",
  discontinued: "停产",
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
  material_codes: "已核验材质",
  process_codes: "已核验工艺",
  packaging_codes: "已核验包装",
  certification_codes: "已核验认证",
  unit_cost: "单位成本",
  lead_time: "交期",
};

const emptyCreateForm = {
  sku: "",
  name: "",
  category_code: "sofa",
  lifecycle_status: "active",
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

function shortSha(value) {
  return value ? String(value).slice(0, 8) : "—";
}

function productCategory(product) {
  return categoryLabels[product.category_code] || product.category_code || "其他";
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
            lifecycle_status: form.lifecycle_status,
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
            <div className="product-field-grid four">
              <label>产品名称<input value={form.name} onChange={update("name")} placeholder="例如：云感模块沙发" /></label>
              <label>唯一 SKU<input value={form.sku} onChange={update("sku")} placeholder="例如：HF-A0396-1" /></label>
              <label>所属品类<select value={form.category_code} onChange={update("category_code")}>{Object.entries(categoryLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label>生命周期<select value={form.lifecycle_status} onChange={update("lifecycle_status")}>{Object.entries(lifecycleLabels).filter(([value]) => value !== "discontinued").map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
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

function ProductQuickPreview({ product, onClose, onOpenDetail }) {
  const [detail, setDetail] = useState(null);
  const [inventory, setInventory] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setDetail(null);
    setInventory(null);
    setError("");
    Promise.allSettled([
      api(`/api/v1/products/${product.product_id}`),
      api(`/api/v1/products/${product.product_id}/inventory-summary`),
    ]).then(([detailResult, inventoryResult]) => {
      if (!active) return;
      if (detailResult.status === "fulfilled") setDetail(detailResult.value);
      else setError(detailResult.reason.message);
      if (inventoryResult.status === "fulfilled") setInventory(inventoryResult.value);
    });
    return () => { active = false; };
  }, [product.product_id]);

  const current = detail || product;
  const attributes = detail?.attributes || [];
  const hasConflicts = product.has_conflicts || attributes.some((item) => item.confirmation_status === "conflicted");

  return (
    <div className="product-drawer-backdrop product-preview-backdrop" onMouseDown={onClose}>
      <aside className="product-quick-preview" role="dialog" aria-modal="true" aria-label={`${product.name} 快速预览`} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><span>QUICK PRODUCT VIEW</span><h2>{product.name}</h2><p>{product.sku} · {productCategory(product)}</p></div>
          <button type="button" onClick={onClose} aria-label="关闭快速预览"><X /></button>
        </header>
        <div className="product-preview-scroll">
          <div className="product-preview-image">
            <img src={productImage(current.sku)} alt={`${current.name} 产品图`} onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = "/assets/furniscope-mark.png"; }} />
          </div>
          {error ? <div className="detail-error"><WarningCircle />{error}</div> : <>
            <section className="product-preview-identity">
              <ProductStatus product={{ ...current, has_conflicts: hasConflicts }} />
              <dl>
                <div><dt>画像完整度</dt><dd>{detail ? `${Math.round((detail.completeness_score || 0) * 100)}%` : "读取中…"}</dd></div>
                <div><dt>画像版本</dt><dd>{detail ? `V${detail.profile_version || 0}` : "读取中…"}</dd></div>
                <div><dt>生命周期</dt><dd>{lifecycleLabels[current.lifecycle_status] || current.lifecycle_status || "—"}</dd></div>
                <div><dt>资料素材</dt><dd>{detail ? `${detail.source_summary?.file_count || 0} 份` : "读取中…"}</dd></div>
              </dl>
            </section>
            {hasConflicts && <section className="product-preview-conflict"><WarningCircle weight="fill" /><div><strong>存在待处理参数冲突</strong><p>请进入完整档案核验人工值与资料识别结果。</p></div></section>}
            <section className="product-preview-stock">
              <header><Database /><div><span>库存摘要</span><strong>{inventory ? `${inventory.total_inventory_units} 件` : "读取中…"}</strong></div></header>
              <p>{inventory?.as_of_date ? `数据截至 ${formatDate(inventory.as_of_date)}` : "库存事实独立管理，不随产品主档修改。"}</p>
              {inventory?.sites?.length > 0 && <div>{inventory.sites.slice(0, 3).map((site) => <span key={site.site}><b>{site.site}</b>{site.inventory_units} 件</span>)}</div>}
            </section>
          </>}
        </div>
        <footer>
          <button type="button" onClick={onClose}>关闭</button>
          <button type="button" className="primary-save" onClick={onOpenDetail}><ArrowsOut />查看完整档案</button>
        </footer>
      </aside>
    </div>
  );
}

function ProductProfile({ productId, onBack, onUpdated, onArchived, startAnalysis }) {
  const [detail, setDetail] = useState(null);
  const [etag, setEtag] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null);
  const [editValue, setEditValue] = useState("");
  const [masterEditing, setMasterEditing] = useState(false);
  const [masterForm, setMasterForm] = useState(null);
  const [relations, setRelations] = useState([]);
  const [inventory, setInventory] = useState(null);
  const [relationsError, setRelationsError] = useState("");
  const [inventoryError, setInventoryError] = useState("");
  const [saving, setSaving] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState(false);

  const load = async () => {
    setError("");
    const [detailResult, relationResult, inventoryResult] = await Promise.allSettled([
      apiWithMeta(`/api/v1/products/${productId}`),
      api(`/api/v1/products/${productId}/relations`),
      api(`/api/v1/products/${productId}/inventory-summary`),
    ]);
    if (detailResult.status === "rejected") {
      setError(detailResult.reason.message);
      return;
    }
    const { data, response } = detailResult.value;
    setDetail(data);
    setEtag(response.headers.get("etag") || "");
    setMasterForm({
      sku: data.sku,
      name: data.name,
      category_code: data.category_code,
      lifecycle_status: data.lifecycle_status,
      description: data.description || "",
    });
    if (relationResult.status === "fulfilled") {
      setRelations(relationResult.value.items || []);
      setRelationsError("");
    } else {
      setRelationsError(relationResult.reason.message);
    }
    if (inventoryResult.status === "fulfilled") {
      setInventory(inventoryResult.value);
      setInventoryError("");
    } else {
      setInventoryError(inventoryResult.reason.message);
    }
  };
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

  const saveMaster = async () => {
    if (!masterForm.sku.trim() || !masterForm.name.trim()) {
      setError("产品名称和 SKU 不能为空");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api(`/api/v1/products/${productId}`, {
        method: "PATCH",
        headers: { "If-Match": etag },
        body: JSON.stringify({
          sku: masterForm.sku.trim(),
          name: masterForm.name.trim(),
          category_code: masterForm.category_code,
          lifecycle_status: masterForm.lifecycle_status,
          description: masterForm.description.trim() || null,
        }),
      });
      setMasterEditing(false);
      await load();
      onUpdated();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  };

  const relationRoles = (type) => ({
    spu: ["parent", "variant"],
    variant: ["parent", "variant"],
    bundle: ["parent", "item"],
    bom: ["parent", "component"],
  }[type] || ["item"]);
  const relationRoleLabels = {
    parent: "当前 SKU 是主产品",
    variant: "当前 SKU 是变体",
    item: "当前 SKU 是套装成员",
    component: "当前 SKU 是 BOM 组件",
  };
  const updateRelation = (index, key, value) => setRelations((current) => current.map((item, itemIndex) => {
    if (index !== itemIndex) return item;
    const next = { ...item, [key]: value };
    if (key === "group_type" && !relationRoles(value).includes(next.member_role)) next.member_role = relationRoles(value)[0];
    return next;
  }));
  const addRelation = () => setRelations((current) => [...current, {
    group_type: "spu", group_code: "", group_name: "", member_role: "variant", quantity: "1",
  }]);
  const saveRelations = async () => {
    if (relations.some((item) => !String(item.group_code || "").trim() || !String(item.group_name || "").trim())) {
      setRelationsError("关系编码和名称不能为空");
      return;
    }
    setSaving(true);
    setRelationsError("");
    try {
      const next = await api(`/api/v1/products/${productId}/relations`, {
        method: "PUT",
        body: JSON.stringify({
          items: relations.map(({ group_type, group_code, group_name, member_role, quantity }) => ({
            group_type, group_code: group_code.trim(), group_name: group_name.trim(),
            member_role, quantity: String(quantity || "1"),
          })),
        }),
      });
      setRelations(next.items || []);
      onUpdated();
    } catch (reason) {
      setRelationsError(reason.message);
    } finally {
      setSaving(false);
    }
  };

  const archive = async () => {
    setSaving(true);
    setError("");
    try {
      await api(`/api/v1/products/${productId}`, {
        method: "DELETE",
        headers: { "If-Match": etag },
      });
      onArchived();
    } catch (reason) {
      setError(reason.message);
      setArchiveConfirm(false);
    } finally {
      setSaving(false);
    }
  };

  return (
      <section className="product-detail-drawer product-profile-panel">
        <header><div><ParentPageTab className="compact" label="产品中心" current="经营档案" onBack={onBack} /><span>PRODUCT OPERATING PROFILE</span><h2>{detail?.name || "正在读取产品档案…"}</h2><p>{detail ? `${detail.sku} · ${productCategory(detail)} · ${lifecycleLabels[detail.lifecycle_status] || detail.lifecycle_status}` : ""}</p></div><div className="detail-head-actions">{detail && <><button onClick={() => setMasterEditing(true)} title="编辑产品主档" aria-label="编辑产品主档"><PencilSimple /></button><button className="archive" onClick={() => setArchiveConfirm(true)} title="归档产品" aria-label="归档产品"><Archive /></button></>}</div></header>
        {error ? <div className="detail-error"><WarningCircle />{error}<button onClick={load}>重试</button></div> : detail && <>
          <div className="detail-scroll">
            <section className="detail-visual"><img src={productImage(detail.sku)} alt={`${detail.name} 产品图`} onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = "/assets/furniscope-mark.png"; }} /><div><ProductStatus product={{ ...detail, has_conflicts: detail.attributes.some((item) => item.confirmation_status === "conflicted") }} /><span>画像完整度 <b>{Math.round((detail.completeness_score || 0) * 100)}%</b></span><span>画像版本 <b>V{detail.profile_version || 0}</b></span></div></section>
            <section className="source-priority"><ShieldCheck /><div><strong>多源数据优先级</strong><p><b>人工填写</b><i />文档提取<i />AI 视觉识别</p></div></section>
            {detail.attributes.some((item) => item.confirmation_status === "conflicted") && <section className="conflict-alert"><WarningCircle weight="fill" /><div><strong>检测到参数冲突</strong><p>AI 识别结果与工厂人工参数不一致。当前以人工值为准，请核验后确认。</p></div></section>}
            <section className="detail-section master-data-section"><h3>基础资料 <button onClick={() => setMasterEditing((value) => !value)}><PencilSimple />{masterEditing ? "取消编辑" : "编辑主档"}</button></h3>{masterEditing ? <div className="master-edit-form">
              <label>产品名称<input value={masterForm.name} onChange={(event) => setMasterForm({ ...masterForm, name: event.target.value })} /></label>
              <label>SKU 编码<input value={masterForm.sku} onChange={(event) => setMasterForm({ ...masterForm, sku: event.target.value })} /></label>
              <label>所属品类<select value={masterForm.category_code} onChange={(event) => setMasterForm({ ...masterForm, category_code: event.target.value })}>{Object.entries(categoryLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label>生命周期<select value={masterForm.lifecycle_status} onChange={(event) => setMasterForm({ ...masterForm, lifecycle_status: event.target.value })}>{Object.entries(lifecycleLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label className="full">产品说明<textarea value={masterForm.description} onChange={(event) => setMasterForm({ ...masterForm, description: event.target.value })} /></label>
              <button className="save-inline" onClick={saveMaster} disabled={saving}><FloppyDisk />{saving ? "保存中…" : "保存主档"}</button>
            </div> : <dl><div><dt>产品名称</dt><dd>{detail.name}</dd></div><div><dt>SKU</dt><dd>{detail.sku}</dd></div><div><dt>所属品类</dt><dd>{productCategory(detail)}</dd></div><div><dt>生命周期</dt><dd>{lifecycleLabels[detail.lifecycle_status] || detail.lifecycle_status}</dd></div><div className="full"><dt>产品说明</dt><dd>{detail.description || "暂未填写"}</dd></div><div><dt>已解析素材</dt><dd>{detail.source_summary?.file_count || 0} 份</dd></div></dl>}</section>
            <section className="detail-section inventory-summary"><h3>当前库存摘要 <small>历史快照 · 非实时</small></h3>{inventoryError ? <p className="inline-error">{inventoryError}</p> : inventory ? <>
              <div className="inventory-head"><Database /><span><small>库存总量</small><strong>{inventory.total_inventory_units} 件</strong></span><span><small>数据截至</small><strong>{formatDate(inventory.as_of_date)}</strong></span><span><small>导入状态</small><strong>{({ none: "未导入", uploaded: "已上传", previewed: "已预检", confirmed: "已确认" })[inventory.import_status.status] || inventory.import_status.status}</strong></span></div>
              {inventory.sites.length ? <div className="inventory-sites">{inventory.sites.map((site) => <span key={site.site}><b>{site.site}</b><strong>{site.inventory_units} 件</strong><small>{formatDate(site.as_of_date)} · {shortSha(site.source_version_uuid)}</small></span>)}</div> : <p className="inventory-empty">尚无已确认的库存事实。库存写入保持独立链路，不会随产品主档编辑而改写。</p>}
              {inventory.import_status.filename && <p className="inventory-import-note">最近库存文件：{inventory.import_status.filename} · {formatDate(inventory.import_status.updated_at)}</p>}
            </> : <p className="inventory-empty">正在读取库存摘要…</p>}</section>
            <section className="detail-section relations-section"><h3>产品关系 <button onClick={addRelation}><Plus />新增关系</button></h3><p className="section-caption">一个 SKU 可同时关联 SPU、变体族、套装与 BOM；数量用于套装件数或 BOM 用量。</p>
              {relations.length ? <div className="relation-editor">{relations.map((item, index) => <div key={`${item.group_id || "new"}-${index}`}>
                <select value={item.group_type} onChange={(event) => updateRelation(index, "group_type", event.target.value)}><option value="spu">SPU</option><option value="variant">变体族</option><option value="bundle">套装</option><option value="bom">BOM</option></select>
                <input value={item.group_code} onChange={(event) => updateRelation(index, "group_code", event.target.value)} placeholder="关系编码" />
                <input value={item.group_name} onChange={(event) => updateRelation(index, "group_name", event.target.value)} placeholder="关系名称" />
                <select aria-label="当前 SKU 在关系中的角色" value={item.member_role} onChange={(event) => updateRelation(index, "member_role", event.target.value)}>{relationRoles(item.group_type).map((role) => <option value={role} key={role}>{relationRoleLabels[role]}</option>)}</select>
                <input type="number" min="0.000001" step="0.01" value={item.quantity} onChange={(event) => updateRelation(index, "quantity", event.target.value)} aria-label="关系数量" />
                <button onClick={() => setRelations((current) => current.filter((_, itemIndex) => itemIndex !== index))} title="移除关系"><X /></button>
              </div>)}</div> : <div className="relations-empty"><LinkSimple /><span><strong>尚未建立产品关系</strong><small>可关联 SPU、变体、套装或 BOM。</small></span></div>}
              {relationsError && <p className="inline-error">{relationsError}</p>}
              <button className="save-relations" onClick={saveRelations} disabled={saving}><FloppyDisk />{saving ? "保存中…" : "保存全部关系"}</button>
            </section>
            <ProductFacts detail={detail} etag={etag} onSaved={async () => { await load(); onUpdated(); }}/>
            <section className="detail-section"><h3>标准化产品参数 <small>{detail.attributes.length} 项</small></h3><div className="attribute-list">
              {detail.attributes.filter((attribute) => attribute.attribute_code !== "color").length ? detail.attributes.filter((attribute) => attribute.attribute_code !== "color").map((attribute) => {
                const conflict = attribute.confirmation_status === "conflicted";
                return <article className={conflict ? "conflicted" : ""} key={attribute.attribute_code}>
                  <div><strong>{attributeLabels[attribute.attribute_code] || attribute.attribute_name || attribute.attribute_code}</strong><span className={`source ${attribute.source_type}`}>{sourceLabels[attribute.source_type] || attribute.source_type}</span></div>
                  {editing === attribute.attribute_code ? <div className="attribute-edit"><input value={editValue} onChange={(event) => setEditValue(event.target.value)} autoFocus /><button onClick={() => correct(attribute)}><Check />确认</button><button onClick={() => setEditing(null)}>取消</button></div> : <>
                    <p>{String(attribute.attribute_value)} {attribute.unit || ""}</p>
                    {attribute.source_locator?.evidence_text && <blockquote>{attribute.source_locator.evidence_text}</blockquote>}
                    {attribute.source_locator?.conflicting_observations?.map((observation, i) => <blockquote key={i}>{String(observation.value)}：{observation.evidence_text}</blockquote>)}
                    <footer><span>{attribute.confirmation_status === "confirmed" ? "已确认" : conflict ? "待处理冲突" : "待人工确认"} · 置信度 {Math.round(attribute.confidence * 100)}%</span>{conflict && <span className="conflict-value">AI 识别：{String(attribute.source_locator?.conflicting_value ?? "存在差异")}</span>}{!attribute.attribute_code.endsWith("_codes") && <button onClick={() => { setEditing(attribute.attribute_code); setEditValue(String(attribute.attribute_value)); }}><PencilSimple />{conflict ? "一键修正" : "编辑"}</button>}</footer>
                  </>}
                </article>;
              }) : <div className="no-attributes"><Sparkle /><strong>尚未生成产品画像</strong><p>上传产品图片或资料后，AI 将自动提取标准化参数。</p></div>}
            </div></section>
            <section className="detail-section product-record-section"><h3>来源与档案记录</h3><dl><div><dt>资料来源</dt><dd>{detail.source_summary?.file_count || 0} 份已解析素材</dd></div><div><dt>当前画像</dt><dd>V{detail.profile_version || 0}</dd></div><div><dt>建档时间</dt><dd>{formatDate(detail.created_at)}</dd></div><div><dt>最近更新</dt><dd>{formatDate(detail.updated_at)}</dd></div></dl><p>参数卡片保留来源类型、原文证据和置信度；库存事实通过独立版本链路维护。</p></section>
          </div>
          <footer><button className="archive-product" onClick={() => setArchiveConfirm(true)}><Archive />归档产品</button><span /><button onClick={onBack}>返回产品中心</button><button className="primary-save" disabled={detail.analysis_status !== "ready" || detail.attributes.some((item) => item.confirmation_status === "conflicted")} onClick={() => startAnalysis(detail)}>使用该产品开始分析 <ArrowRight /></button></footer>
        </>}
        {archiveConfirm && <div className="archive-confirm"><section><Archive /><h3>归档 {detail?.sku}</h3><p>归档后产品会从默认列表移除，分析入口停用，现有库存事实与审计记录继续保留。</p><div><button onClick={() => setArchiveConfirm(false)}>取消</button><button className="confirm-archive" onClick={archive} disabled={saving}>{saving ? "归档中…" : "确认归档"}</button></div></section></div>}
      </section>
  );
}

function productListHash({ query, status, category, created, productId, scroll }) {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (status && status !== "all") params.set("status", status);
  if (category && category !== "all") params.set("category", category);
  if (created && created !== "all") params.set("created", created);
  if (productId) params.set("product", String(productId));
  if (Number(scroll) > 0) params.set("scroll", String(Math.round(Number(scroll))));
  const suffix = params.toString();
  return `products${suffix ? `?${suffix}` : ""}`;
}

export function ProductDetailPage({ Sidebar, Topbar }) {
  const routeQuery = useMemo(() => new URLSearchParams(location.hash.split("?")[1] || ""), []);
  const productId = routeQuery.get("product");
  const backToList = (archived = false) => {
    location.hash = productListHash({
      query: routeQuery.get("q") || "",
      status: routeQuery.get("status") || "all",
      category: routeQuery.get("category") || "all",
      created: routeQuery.get("created") || "all",
      productId: archived ? null : productId,
      scroll: routeQuery.get("scroll") || 0,
    });
  };
  const startAnalysis = (product) => {
    location.hash = `workflow?product=${product.product_id}&from=product-detail`;
  };

  return (
    <main className="workspace product-detail-page">
      <Sidebar page="products" />
      <section className="workspace-main">
        <Topbar />
        <div className="product-detail-canvas">
          {productId ? <ProductProfile
            productId={productId}
            onBack={() => backToList(false)}
            onUpdated={() => {}}
            onArchived={() => backToList(true)}
            startAnalysis={startAnalysis}
          /> : <div className="catalog-message error" role="alert"><WarningCircle /><span><strong>无法打开产品档案</strong>地址缺少产品标识。</span><button onClick={() => backToList(true)}>返回产品中心</button></div>}
        </div>
      </section>
    </main>
  );
}

export function ProductCatalog({ Sidebar, Topbar }) {
  const initialQuery = useMemo(() => new URLSearchParams(location.hash.split("?")[1] || ""), []);
  const canvasRef = useRef(null);
  const restoredScroll = useRef(false);
  const [products, setProducts] = useState([]);
  const [query, setQuery] = useState(() => initialQuery.get("q") || "");
  const [status, setStatus] = useState(() => initialQuery.get("status") || "all");
  const [category, setCategory] = useState(() => initialQuery.get("category") || "all");
  const [created, setCreated] = useState(() => initialQuery.get("created") || "all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(null);
  const [selectedId, setSelectedId] = useState(() => initialQuery.get("product"));
  const [previewId, setPreviewId] = useState(null);

  const categories = useMemo(() => [...new Set(products.map(productCategory))].sort((a, b) => a.localeCompare(b, "zh-CN")), [products]);
  const previewProduct = products.find((product) => String(product.product_id) === String(previewId));

  const load = () => {
    setLoading(true); setError("");
    return api("/api/v1/products?page_size=100")
      .then((page) => setProducts(page.items || []))
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (loading || restoredScroll.current || !canvasRef.current) return undefined;
    const frame = window.requestAnimationFrame(() => {
      canvasRef.current.scrollTop = Number(initialQuery.get("scroll") || 0);
      restoredScroll.current = true;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [initialQuery, loading]);
  useEffect(() => {
    if (!restoredScroll.current) return;
    const next = productListHash({
      query,
      status,
      category,
      created,
      productId: selectedId,
      scroll: canvasRef.current?.scrollTop || 0,
    });
    window.history.replaceState(null, "", `#${next}`);
  }, [category, created, query, selectedId, status]);

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
  const openDetail = (productOrId) => {
    const productId = typeof productOrId === "object" ? productOrId.product_id : productOrId;
    setPreviewId(null);
    location.hash = `product-detail?${productListHash({
      query,
      status,
      category,
      created,
      productId,
      scroll: canvasRef.current?.scrollTop || 0,
    }).split("?")[1] || `product=${productId}`}`;
  };
  const openPreview = (product) => {
    if (window.matchMedia("(max-width: 700px)").matches) {
      openDetail(product);
      return;
    }
    setSelectedId(String(product.product_id));
    setPreviewId(product.product_id);
  };
  const createdProduct = (productId) => {
    setModal(null);
    load().finally(() => openDetail(productId));
  };

  return (
    <main className="workspace product-catalog-page">
      <Sidebar page="products" />
      <section className="workspace-main"><Topbar /><div className="product-catalog-canvas" ref={canvasRef}>
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
          {filtered.map((product) => <article className={`product-record-card${String(selectedId) === String(product.product_id) ? " selected" : ""}`} key={product.product_id} onClick={() => openDetail(product)}>
            <div className="record-image"><img src={productImage(product.sku)} alt={`${product.name} 产品图`} onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = "/assets/furniscope-mark.png"; }} /></div>
            <div className="record-main"><div><ProductStatus product={product} /><span className="record-category">{productCategory(product)}</span></div><h2>{product.name}</h2><p>SKU <b>{product.sku}</b></p><div className="record-commercial"><span>MOQ <b>{product.moq || "—"}</b></span><span>出厂价 <b>{product.factory_price ? `$${product.factory_price}` : "—"}</b></span></div></div>
            <div className="record-meta"><span><Clock />创建于 {formatDate(product.created_at)}</span><span><Sparkle />{product.current_profile_version_id ? "标准化画像已生成" : "等待上传资料并解析"}</span></div>
            <div className="record-actions"><button className="record-preview" title="快速预览" aria-label={`快速预览 ${product.name}`} onClick={(event) => { event.stopPropagation(); openPreview(product); }}><Eye /></button><button onClick={(event) => { event.stopPropagation(); openDetail(product); }}>查看档案</button><button className="record-forecast" onClick={(event) => { event.stopPropagation(); startForecast(product); }}><TrendUp />销量预测</button><button className="record-analyze" disabled={product.analysis_status !== "ready" || product.has_conflicts} onClick={(event) => { event.stopPropagation(); startAnalysis(product); }}>{product.has_conflicts ? "先处理冲突" : product.analysis_status === "ready" ? "开始分析" : "先确认画像"}<ArrowRight /></button></div>
          </article>)}
        </section> : !error ? <div className="catalog-empty"><MagnifyingGlass /><strong>没有找到匹配的产品</strong><p>尝试修改搜索词或筛选条件。</p><button onClick={() => { setQuery(""); setStatus("all"); setCategory("all"); setCreated("all"); }}>清除筛选</button></div> : null}
      </div></section>
      {modal === "create" && <CreateProductModal onClose={() => setModal(null)} onCreated={createdProduct} />}
      {modal === "batch" && <ProductImportWizard onClose={() => setModal(null)} onImported={load} />}
      {previewProduct && <ProductQuickPreview product={previewProduct} onClose={() => setPreviewId(null)} onOpenDetail={() => openDetail(previewProduct)} />}
    </main>
  );
}
