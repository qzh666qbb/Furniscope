import { useEffect, useState } from "react";
import { Check, CloudArrowUp, Database, FileText, Pulse, WarningCircle } from "@phosphor-icons/react";
import { api, apiWithMeta, idempotencyKey } from "./api.js";

const steps = ["产品画像", "市场数据", "确认并启动", "执行结果"];
const today = () => new Date().toISOString().slice(0, 10);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const taskStatusLabels = {
  draft: "准备中",
  queued: "等待开始",
  running: "分析中",
  waiting_human: "等待确认",
  partial_succeeded: "部分完成",
  succeeded: "分析完成",
  failed: "分析失败",
  cancelled: "已取消",
};
const taskStageLabels = {
  understanding_product: "理解产品",
  researching_market: "研究市场",
  evaluating_opportunity: "评估机会",
  generating_recommendation: "生成建议",
  completed: "完成",
};

function message(error) {
  return error instanceof Error ? error.message : String(error);
}

export function LiveAnalysisWizard({ Sidebar, Topbar }) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [products, setProducts] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [product, setProduct] = useState(null);
  const [dataset, setDataset] = useState(null);
  const [task, setTask] = useState(null);
  const [result, setResult] = useState(null);
  const [productFiles, setProductFiles] = useState([]);
  const [parsedProduct, setParsedProduct] = useState(null);
  const [productForm, setProductForm] = useState({
    sku: `FS-${Date.now().toString().slice(-6)}`,
    name: "待解析家具产品",
    category: "sofa",
  });
  const [datasetForm, setDatasetForm] = useState({
    name: `市场数据 ${today()}`,
    platform: "amazon",
    country: "US",
    sourceType: "enterprise_export",
    authorizationReference: "",
  });
  const [datasetFile, setDatasetFile] = useState(null);

  const reload = async () => {
    const [productPage, datasetPage] = await Promise.all([
      api("/api/v1/products?page_size=100"),
      api("/api/v1/market-datasets?page_size=100"),
    ]);
    const nextProducts = productPage.items || [];
    setProducts(nextProducts);
    setDatasets(datasetPage.items || []);
    const query = location.hash.split("?")[1] || "";
    const requestedProductId = new URLSearchParams(query).get("product");
    const requestedProduct = nextProducts.find(
      (item) => String(item.product_id) === requestedProductId,
    );
    if (requestedProduct) {
      setProduct(requestedProduct);
      setProductForm({
        sku: requestedProduct.sku,
        name: requestedProduct.name,
        category: requestedProduct.category_code,
      });
      if (requestedProduct.current_profile_version_id) setStep(1);
    }
  };

  useEffect(() => {
    reload().catch((e) => setError(message(e)));
  }, []);

  const run = async (action) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const createProduct = () =>
    run(async () => {
      if (!productFiles.length)
        throw new Error("请上传至少一份 PDF、XLSX、JPG 或 PNG 产品资料");
      const created = await api("/api/v1/products", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("product") },
        body: JSON.stringify({
          sku: productForm.sku,
          name: productForm.name,
          category_code: productForm.category,
          description: "由产品资料解析流水线创建",
        }),
      });
      const form = new FormData();
      form.append(
        "source_type",
        productFiles.every((file) => file.type.startsWith("image/"))
          ? "image"
          : "document",
      );
      productFiles.forEach((file) => form.append("files", file));
      const accepted = await api(
        `/api/v1/products/${created.product_id}/assets:parse`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey("product-parse") },
          body: form,
        },
      );
      let parseJob;
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await delay(1000);
        parseJob = await api(
          `/api/v1/product-parse-jobs/${accepted.parse_job_id}?include_files=true`,
        );
        if (
          ["succeeded", "partial_succeeded", "failed"].includes(parseJob.status)
        )
          break;
      }
      if (!parseJob || parseJob.status === "failed") {
        const fileError = parseJob?.file_results?.find(
          (item) => item.error_message,
        )?.error_message;
        throw new Error(
          fileError || parseJob?.failure_message || "产品资料解析失败",
        );
      }
      const detail = await apiWithMeta(
        `/api/v1/products/${created.product_id}`,
      );
      if (
        !detail.data.current_profile_version_id ||
        !detail.data.attributes.length
      ) {
        throw new Error("未能从文件中识别有效的产品信息，请检查文件内容后重试");
      }
      setParsedProduct({
        ...detail.data,
        etag: detail.response.headers.get("ETag"),
        parseJob,
      });
      setProduct(null);
    });

  const confirmParsedProduct = () =>
    run(async () => {
      const codes = parsedProduct.attributes.map((item) => item.attribute_code);
      await api(
        `/api/v1/products/${parsedProduct.product_id}/profile:confirm`,
        {
          method: "POST",
          headers: {
            "If-Match": parsedProduct.etag,
            "Idempotency-Key": idempotencyKey("profile-confirm"),
          },
          body: JSON.stringify({
            profile_version_id: parsedProduct.current_profile_version_id,
            confirmed_attribute_codes: codes,
          }),
        },
      );
      const selected = { ...parsedProduct };
      setProduct(selected);
      setProducts((items) => [
        selected,
        ...items.filter((item) => item.product_id !== selected.product_id),
      ]);
      setStep(1);
    });

  const useProduct = async (id) => {
    const selected = products.find((item) => String(item.product_id) === id);
    setProduct(selected || null);
  };

  const importDataset = () =>
    run(async () => {
      if (!datasetFile) throw new Error("请选择数据文件");
      if (!datasetForm.authorizationReference.trim())
        throw new Error("请填写数据来源说明，便于后续核验数据授权");
      const created = await api("/api/v1/market-datasets", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dataset") },
        body: JSON.stringify({
          name: datasetForm.name,
          platform: datasetForm.platform,
          market_country: datasetForm.country.toUpperCase(),
          category_code: productForm.category,
          data_end_date: today(),
          source_type: datasetForm.sourceType,
          source_name: datasetFile.name,
          authorization_reference: datasetForm.authorizationReference || null,
          field_mapping: [],
        }),
      });
      const form = new FormData();
      form.append("deduplication_strategy", "platform_id_latest");
      form.append("files", datasetFile);
      await api(`/api/v1/market-datasets/${created.dataset_id}/imports`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dataset-import") },
        body: form,
      });
      let current;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        await delay(700);
        current = await api(`/api/v1/market-datasets/${created.dataset_id}`);
        if (["ready", "rejected"].includes(current.status)) break;
      }
      if (current?.status !== "ready")
        throw new Error(
          current?.status === "rejected"
            ? "数据文件内容无法识别，请检查后重新上传"
            : "数据仍在处理中，请稍后从已有市场数据中选择",
        );
      setDataset(current);
      setDatasets((items) => [current, ...items]);
      setStep(2);
    });

  const chooseDataset = async (id) => {
    if (!id) return setDataset(null);
    try {
      setDataset(await api(`/api/v1/market-datasets/${id}`));
    } catch (e) {
      setError(message(e));
    }
  };

  const pollTask = async (taskUuid) => {
    let current;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      current = await api(
        `/api/v1/analysis-tasks/${taskUuid}?include_stage_runs=true`,
      );
      setTask(current);
      if (
        [
          "succeeded",
          "failed",
          "partial_succeeded",
          "cancelled",
          "waiting_human",
        ].includes(current.status)
      )
        break;
      await delay(1000);
    }
    if (!current || !["succeeded", "failed", "partial_succeeded", "cancelled", "waiting_human"].includes(current.status))
      throw new Error("任务仍在执行，请稍后回到工作台查看最新状态");
    if (["succeeded", "partial_succeeded"].includes(current.status))
      setResult(await api(`/api/v1/analysis-tasks/${taskUuid}/result`));
    if (current.status === "failed")
      throw new Error(current.failure_message || "分析任务执行失败，请检查数据后重试");
    if (current.status === "cancelled")
      throw new Error("分析任务已取消");
  };

  const launch = () =>
    run(async () => {
      if (!product?.current_profile_version_id)
        throw new Error("请选择已确认画像的产品");
      if (dataset?.status !== "ready")
        throw new Error("请选择可用的市场数据");
      const created = await api("/api/v1/analysis-tasks", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("task") },
        body: JSON.stringify({
          job_name: `${product.name} 市场机会评估`,
          job_type: "product_market_fit",
          product_id: product.product_id,
          product_profile_version_id: product.current_profile_version_id,
          dataset_id: dataset.dataset_id,
          target_country: dataset.market_country.trim(),
          target_platform: dataset.platform,
          analysis_currency: "USD",
          analysis_config: {
            source: "live_web_wizard",
            include_forecast: true,
            data_class: "authorized_market_data",
          },
        }),
      });
      setTask(created);
      setStep(3);
      await api(`/api/v1/analysis-tasks/${created.task_uuid}:start`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("task-start") },
        body: JSON.stringify({}),
      });
      await pollTask(created.task_uuid);
    });

  const answer = (option) =>
    run(async () => {
      await api(
        `/api/v1/analysis-tasks/confirmations/${task.user_confirmation.confirmation_id}:answer`,
        {
          method: "POST",
          body: JSON.stringify({ selected_option: option }),
        },
      );
      await pollTask(task.task_uuid);
    });

  return (
    <main className="workspace wizard-page">
      <Sidebar page="analysis" showSessions={false} />
      <section className="workspace-main">
        <Topbar />
        <div className="wizard-canvas">
          <header className="wizard-title-row">
            <div>
              <h1>市场分析</h1>
              <p>上传产品与市场资料后，系统将逐步完成分析并生成可追溯报告。</p>
            </div>
            <span>
              <Pulse />
              分析流程
            </span>
          </header>
          <div className="wizard-stepper">
            {steps.map((name, index) => (
              <button
                key={name}
                className={`${index === step ? "current" : ""} ${index < step ? "complete" : ""}`}
                disabled={index > step}
                onClick={() => index < step && !busy && setStep(index)}
                aria-current={index === step ? "step" : undefined}
              >
                <i>{index < step ? <Check /> : index + 1}</i>
                <span>{name}</span>
              </button>
            ))}
          </div>
          <div className="wizard-grid">
            <section className="wizard-form live-wizard">
              {step === 0 && (
                <>
                  <h2>选择分析产品</h2>
                  <p>可以选择产品中心中的产品，也可以添加新产品。</p>
                  <label>
                    选择产品
                    <select
                      value={product?.product_id || ""}
                      onChange={(e) => useProduct(e.target.value)}
                    >
                      <option value="">添加新产品</option>
                      {products.map((x) => (
                        <option key={x.product_id} value={x.product_id}>
                          {x.sku} · {x.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {product ? (
                    <div className="live-selection">
                      <Check />
                      <strong>{product.name}</strong>
                      <span>{product.current_profile_version_id ? "产品资料已确认" : "产品资料待确认"}</span>
                    </div>
                  ) : parsedProduct ? (
                    <>
                      <div className="live-selection">
                        <Database />
                        <strong>{parsedProduct.name}</strong>
                        <span>
                          已从 {parsedProduct.parseJob.summary.succeeded_file_count} 个文件识别{" "}
                          {parsedProduct.attributes.length} 项产品信息
                        </span>
                      </div>
                      <div className="live-attributes">
                        {parsedProduct.attributes.map((item) => (
                          <div
                            key={item.attribute_code}
                            className={
                              item.confidence < 0.7 ? "low-confidence" : ""
                            }
                          >
                            <strong>
                              {item.attribute_name || item.attribute_code}
                            </strong>
                            <span>
                              {typeof item.attribute_value === "string"
                                ? item.attribute_value
                                : JSON.stringify(item.attribute_value)}
                            </span>
                            <b>
                              {Math.round(item.confidence * 100)}%
                              {item.confidence < 0.7 ? " · 待人工确认" : ""}
                            </b>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="field-pair">
                        <label>
                          SKU
                          <input
                            value={productForm.sku}
                            onChange={(e) =>
                              setProductForm({
                                ...productForm,
                                sku: e.target.value,
                              })
                            }
                          />
                        </label>
                        <label>
                          产品名称
                          <input
                            value={productForm.name}
                            onChange={(e) =>
                              setProductForm({
                                ...productForm,
                                name: e.target.value,
                              })
                            }
                          />
                        </label>
                      </div>
                      <label>
                        产品品类
                        <select
                          value={productForm.category}
                          onChange={(e) =>
                            setProductForm({
                              ...productForm,
                              category: e.target.value,
                            })
                          }
                        >
                          <option value="sofa">家具座椅</option>
                        </select>
                        <small>系统会自动匹配同类市场数据</small>
                      </label>
                      <label className="live-file-field">
                        产品资料
                        <span className={`live-upload ${productFiles.length ? "has-files" : ""}`}>
                          <CloudArrowUp />
                          <span>
                            <strong>{productFiles.length ? `已选择 ${productFiles.length} 个文件` : "点击选择或拖放产品资料"}</strong>
                            <small>{productFiles.length ? productFiles.map((file) => file.name).join("、") : "支持产品目录、规格表和产品图片"}</small>
                          </span>
                          <input
                            type="file"
                            multiple
                            accept="application/pdf,.pdf,.xlsx,image/jpeg,image/png"
                            onChange={(e) => setProductFiles(Array.from(e.target.files || []))}
                          />
                        </span>
                        <small>
                          文件会进行格式校验、内容解析和可信度检查
                        </small>
                      </label>
                    </>
                  )}
                  <button
                    className="live-primary"
                    disabled={
                      busy || (product && !product.current_profile_version_id)
                    }
                    onClick={
                      product
                        ? () => setStep(1)
                        : parsedProduct
                          ? confirmParsedProduct
                          : createProduct
                    }
                  >
                    {busy
                      ? "正在解析或保存…"
                      : product
                        ? "使用该产品"
                        : parsedProduct
                          ? "确认全部字段并继续"
                          : "上传并解析产品资料"}
                  </button>
                </>
              )}
              {step === 1 && (
                <>
                  <h2>{product ? `为 ${product.sku} 选择市场数据` : "选择或导入市场数据"}</h2>
                  <p>
                    已选择“{product?.name || "当前产品"}”。请选择已有市场数据，或上传新的市场数据文件。
                  </p>
                  <label>
                    已有市场数据
                    <select
                      value={dataset?.dataset_id || ""}
                      onChange={(e) => chooseDataset(e.target.value)}
                    >
                      <option value="">上传新的市场数据</option>
                      {datasets
                        .filter((x) => x.status === "ready")
                        .map((x) => (
                          <option key={x.dataset_id} value={x.dataset_id}>
                            {x.name} · {x.market_country}
                          </option>
                        ))}
                    </select>
                  </label>
                  {dataset ? (
                    <div className="live-selection">
                      <Database />
                      <strong>{dataset.name}</strong>
                      <span>
                        {dataset.listing_count} 商品 /{" "}
                        {dataset.valid_review_count} 有效评论
                      </span>
                    </div>
                  ) : (
                    <>
                      <section className="market-data-guide">
                        <header>
                          <FileText />
                          <div>
                            <h3>需要准备什么数据？</h3>
                            <p>准备一份市场数据工作簿，其中包含市场商品和消费者评价两个工作表。</p>
                          </div>
                          <a href="/api/v1/market-datasets/import-template">下载填写模板</a>
                        </header>
                        <div>
                          <span><i>1</i><strong>取得数据</strong><small>使用企业已有导出、合作数据服务商或获得授权的公开资料</small></span>
                          <span><i>2</i><strong>填写商品信息</strong><small>商品编号、标题、售价、币种和采集时间</small></span>
                          <span><i>3</i><strong>填写消费者评价</strong><small>对应商品编号、评价编号、评分和评价内容</small></span>
                        </div>
                        <p><WarningCircle />系统不会自动采集第三方平台数据，请确保上传资料已获得使用授权。</p>
                      </section>
                      <div className="field-pair">
                        <label>
                          数据名称
                          <input
                            value={datasetForm.name}
                            onChange={(e) =>
                              setDatasetForm({
                                ...datasetForm,
                                name: e.target.value,
                              })
                            }
                          />
                        </label>
                        <label>
                          目标市场
                          <select
                            value={datasetForm.country}
                            onChange={(e) =>
                              setDatasetForm({
                                ...datasetForm,
                                country: e.target.value,
                              })
                            }
                          >
                            <option value="US">美国</option>
                            <option value="CA">加拿大</option>
                            <option value="GB">英国</option>
                            <option value="DE">德国</option>
                            <option value="FR">法国</option>
                            <option value="AU">澳大利亚</option>
                          </select>
                        </label>
                      </div>
                      <div className="field-pair">
                        <label>
                          平台
                          <select
                            value={datasetForm.platform}
                            onChange={(e) =>
                              setDatasetForm({
                                ...datasetForm,
                                platform: e.target.value,
                              })
                            }
                          >
                            <option value="amazon">Amazon</option>
                            <option value="walmart">Walmart</option>
                            <option value="wayfair">Wayfair</option>
                          </select>
                        </label>
                        <label>
                          数据来源
                          <select
                            value={datasetForm.sourceType}
                            onChange={(e) =>
                              setDatasetForm({
                                ...datasetForm,
                                sourceType: e.target.value,
                              })
                            }
                          >
                            <option value="licensed_provider">
                              数据服务商
                            </option>
                            <option value="enterprise_export">
                              企业内部数据
                            </option>
                            <option value="public_authorized">
                              公开市场数据
                            </option>
                          </select>
                        </label>
                      </div>
                      <label>
                        数据来源说明
                        <input
                          required
                          value={datasetForm.authorizationReference}
                          onChange={(e) =>
                            setDatasetForm({
                              ...datasetForm,
                              authorizationReference: e.target.value,
                            })
                          }
                          placeholder="例如：供应商协议编号或内部归档号"
                        />
                      </label>
                      <label className="live-file-field">
                        市场数据工作簿
                        <span className={`live-upload ${datasetFile ? "has-files" : ""}`}>
                          {datasetFile ? <FileText /> : <CloudArrowUp />}
                          <span>
                            <strong>{datasetFile ? datasetFile.name : "选择填写好的市场数据工作簿"}</strong>
                            <small>{datasetFile ? "文件已选择，可以继续" : "包含“商品信息”和“消费者评价”两个工作表"}</small>
                          </span>
                          <input
                            type="file"
                            accept="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx"
                            onChange={(e) => setDatasetFile(e.target.files?.[0] || null)}
                          />
                        </span>
                      </label>
                    </>
                  )}
                  <button
                    className="live-primary"
                    disabled={busy}
                    onClick={dataset ? () => setStep(2) : importDataset}
                  >
                    {busy
                      ? "正在检查数据…"
                      : dataset
                        ? "使用该市场数据"
                        : "上传并继续"}
                  </button>
                </>
              )}
              {step === 2 && (
                <>
                  <h2>确认分析范围</h2>
                  <p>请确认产品、市场和数据范围，提交后系统将开始分析。</p>
                  <dl className="live-review">
                    <dt>产品</dt>
                    <dd>
                      {product?.name} · {product?.sku}
                    </dd>
                    <dt>市场数据</dt>
                    <dd>{dataset?.name}</dd>
                    <dt>市场</dt>
                    <dd>
                      {dataset?.market_country} · {dataset?.platform}
                    </dd>
                    <dt>分析内容</dt>
                    <dd>产品市场匹配 + 销量预测</dd>
                  </dl>
                  <button
                    className="live-primary"
                    disabled={busy}
                    onClick={launch}
                  >
                    {busy ? "正在创建任务…" : "启动分析任务"}
                  </button>
                </>
              )}
              {step === 3 && (
                <>
                  <h2>任务执行</h2>
                  {task && (
                    <div className="live-task">
                      <Pulse />
                      <strong>{taskStatusLabels[task.status] || task.status}</strong>
                      <span>
                        {taskStageLabels[task.stage] || task.stage} · {Math.round(task.progress_percent || 0)}%
                      </span>
                      <progress max="100" value={task.progress_percent || 0} />
                    </div>
                  )}
                  {task?.user_confirmation && (
                    <div className="live-confirm">
                      <WarningCircle />
                      <strong>{task.user_confirmation.question}</strong>
                      <div>
                        {task.user_confirmation.options.map((option) => (
                          <button
                            disabled={busy}
                            key={option.value || option.code || option.label}
                            onClick={() =>
                              answer(
                                option.value || option.code || option.label,
                              )
                            }
                          >
                            {option.label || option.value || option.code}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {result && (
                    <>
                      <div className="live-result">
                        <Check />
                        <h3>{result.report_summary.title}</h3>
                        <p>{result.report_summary.executive_summary}</p>
                        <strong>
                          机会评分{" "}
                          {result.report_summary.overall_opportunity_score} ·
                          置信度 {Math.round(result.report_summary.overall_confidence * 100)}%
                        </strong>
                      </div>
                      <div className="live-result-modules">
                        <strong>逐项验证分析结果</strong>
                        <div>
                          {[
                            ["market-decisions?capability=competitors&view=prices", "竞品监测"],
                            ["market-decisions?capability=reviews&view=stream", "舆情监测"],
                            [`report-detail?id=${result.report_uuid}&from=workbench`, "机会评分"],
                          ].map(([hash, label]) => (
                            <button key={label} onClick={() => {
                              location.hash = hash;
                            }}>{label}</button>
                          ))}
                          <button className="primary" onClick={() => {
                            location.hash = `report-detail?id=${result.report_uuid}&from=workbench`;
                          }}>决策报告</button>
                        </div>
                      </div>
                    </>
                  )}
                  {!result &&
                    task &&
                    ![
                      "waiting_human",
                      "failed",
                      "cancelled",
                      "partial_succeeded",
                    ].includes(task.status) && (
                      <p>正在更新分析进度，你可以继续停留在当前页面。</p>
                    )}
                  {task && ["failed", "cancelled"].includes(task.status) && (
                    <button className="live-secondary" disabled={busy} onClick={() => setStep(2)}>
                      返回检查任务范围
                    </button>
                  )}
                </>
              )}
              {error && (
                <div className="live-error">
                  <WarningCircle />
                  <span>{error}</span>
                </div>
              )}
            </section>
            <aside className="ai-summary">
              <h2>数据与结果</h2>
              <div className="summary-block ok">
                <Check />
                <strong>企业数据安全</strong>
                <p>数据只对当前企业账号可见。</p>
              </div>
              <div className="summary-block ok">
                <Database />
                <strong>数据来源可核验</strong>
                <p>仅使用企业提供或已获得授权的数据。</p>
              </div>
              <div className="summary-block">
                <Pulse />
                <strong>进度自动更新</strong>
                <p>任务进度、确认事项和结果会自动同步。</p>
              </div>
            </aside>
          </div>
        </div>
        <footer className="wizard-footer">
          <button
            disabled={busy || step === 0 || step === 3}
            onClick={() => setStep((value) => Math.max(0, value - 1))}
          >
            上一步
          </button>
          <span />
        </footer>
      </section>
    </main>
  );
}
