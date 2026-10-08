# FurniScope XGBoost 销量预测集成说明 V1

> 2026-10-07复核增量：[企业决策与数据闭环总体设计](../项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md)及[标准数据API实现说明](./FurniScope_企业决策与标准数据API实现说明V1.md)是企业首次/追加/重建训练的现行依据。

## 1. 集成边界

系统保留既有 Sales Forecast V4 的参考推理适配器，企业新训练采用 `tenant-xgb-v2`，兼容历史v1制品。V4制品需要实际下载，Git LFS指针不代表模型可用；开户也不表示已用该企业数据训练。

| 引擎 | 历史来源 | 支持能力 | 限制 |
|---|---|---|---|
| 既有V4 | 原预置制品，完整性通过后可推理 | 原日/周、多站点、情景和冷启动接口 | 无可信企业标准血缘时不能直接追加 |
| `tenant-xgb-v1`（历史） | 本企业确认标准销量 | 保留已发布制品推理 | 原单留出评测记录不升级为新证据 |
| `tenant-xgb-v2` | 本企业确认标准销量 | 每SKU独立模型、滚动评测、稀疏/新品基线；首次/追加/重建 | 暂不支持价格、促销、库存弹性；未验证基线无误差带 |

企业训练严格区分两类转换：

| 环节 | 输入与输出 | 边界 |
|---|---|---|
| 数据清洗 | 原始文件 → `sales-contract-v2`标准记录 | 处理字段、日期、销量口径、重复、取消/退货、缺日期、站点和对账 |
| 产品编码对照 | 来源SKU → 产品中心SKU | 只做一一身份绑定；不改标准记录中的日期和销量，同编码自动关联 |

完整时序为“上传＋数据清洗 → 确认标准版本 → 训练范围预检 → 按需产品编码对照 → 创建训练 → 滚动评测 → 发布”。前端只在预检返回`catalog.can_publish=false`时展示“产品编码对照（可选）”；保存后旧训练预览失效，必须重新预览。

企业v2引擎逐SKU进行三个滚动窗口评测（日各28天、周各4周），与最近值和移动平均比较，通过后才全量拟合。140天/20完整自然周是参评门槛；短历史SKU保留近期均值路径并标记未验证。稀疏只验证窗口总量；全零WAPE无定义。日/周各至少一个SKU通过、所有参评SKU各窗口通过才发布。质量未达标、SHA异常、部署变化或磁盘故障均不覆盖原模型；完整分层及门槛见总体设计§6.4。工程合成测试不能代替真实企业精度。

训练预览通过`/api/v1/forecast/sku-mappings`按需维护来源SKU与产品中心SKU的一一对照；预览列出未关联与冲突项并阻止不完整发布。对照随训练和部署冻结，预测入队同时冻结路由，避免后续目录变化错绑历史。管理员回滚验证旧制品SHA并原子恢复旧目录；无可信目录快照的历史版本必须重建。对应迁移`v3_20_forecast_routing.sql`。

训练采用可终止子进程、数据库令牌/租约及Redis续租；硬中断等待过期后恢复，旧执行不得发布。真实SIGKILL演练已验证第二次执行成功发布与孤儿清理，证据见总体设计§11。升级前先停旧worker。新企业导入支持模板、取消/实际退货、仓库站点及独立销量/库存对账；价格事实仍不作为弹性特征。

可先运行`scripts/evaluate_enterprise_forecast.py`对企业原始文件和已核验规则做同协议滚动验收，输出输入/规则/代码SHA及逐SKU指标，不写部署。命令见[真实数据离线验收指南](./FurniScope_真实数据离线验收指南.md)。合成样本仅验证工具行为。

资产位于 `forecast_assets/`，完整性由 `MANIFEST.sha256` 校验。运行路径
仅由服务端配置，HTTP 请求不能提交模型路径或任意 pickle。

既有V4推理协议支持：

- 日预测（1～365 天）和周预测（1～52 周）；
- 多 SKU × 多站点批量预测；
- 价格、折扣、库存、促销情景；
- 新品基准日销量或参考 SKU 冷启动；
- 预测上下界与 A～D 可靠度。

## 2. 数据契约

迁移 `migrations/v3_forecast_integration.sql` 新增：

- `forecast_models`：可信状态指纹、版本和训练数据截止日期；
- `forecast_model_deployments`：租户当前启用的模型，任务启动时冻结部署；
- `tenant_data_sources`：租户授权数据源及密钥引用（不保存明文凭据）；
- `tenant_sku_catalog`：租户 SKU×站点、品类、生命周期、历史覆盖和模型可用性；
- `forecast_training_runs`：租户训练数据快照、算法/特征版本和训练结果；
- `forecast_jobs`：租户范围、输入、幂等键和执行状态；
- `forecast_runs`：一次实际推理及其冻结模型版本和指标；
- `forecast_results`：SKU × Site × 时间桶预测值、区间和可靠度。

模型原始预测值不会交给 LLM 修改。安全库存为预测上界合计减去点预测合计，
建议生产量为点预测与安全库存之和；这两个值由确定性程序计算。v2新品、手动基线、参考SKU及超出验证范围的预测不提供区间；只要一组没有区间，安全库存与建议生产量为空。稀疏只给完整窗口总量参考带，逐期区间为空。预测汇总保存方法、验证范围和逐SKU历史截止日期。

## 3. API

| Operation ID | Method and path | Purpose |
| --- | --- | --- |
| API-FRC-00 | `GET /api/v1/forecast/status` | 模型就绪状态与版本 |
| API-FRC-SKU | `GET /api/v1/forecast/skus` | 可用 SKU、站点与历史周数 |
| API-FRC-02 | `POST /api/v1/forecast-jobs` | 创建租户预测任务 |
| API-FRC-03 | `POST /api/v1/forecast-jobs/{uuid}:start` | 异步启动预测 |
| API-FRC-04 | `GET /api/v1/forecast-jobs` | 当前租户预测历史 |
| API-FRC-05 | `GET /api/v1/forecast-jobs/{uuid}` | 查询状态 |
| API-FRC-06 | `GET /api/v1/forecast-jobs/{uuid}/result` | 查询逐期结果和生产建议 |

创建和启动接口均要求 `Idempotency-Key`。全部预测接口都要求 FurniScope JWT；
模型状态和 SKU 目录也按令牌中的租户解析，客户端不能覆盖。

企业训练接口补充：

| Method and path | Purpose | 关键约束 |
|---|---|---|
| `POST /api/v1/forecast/data-imports` | 上传CSV/XLSX/JSON原文件 | 仅创建原始版本，不代表可训练 |
| `POST /api/v1/forecast/data-imports/{uuid}/preflight` | 按显式规则清洗与预检 | 输出质量、标准样本与SHA256 |
| `POST /api/v1/forecast/data-imports/{uuid}/confirm` | 确认不可变标准版本 | 预检SHA必须一致 |
| `POST /api/v1/forecast/training-preview` | 预览合并历史和产品身份 | 返回日期缺口、覆盖行及`catalog`问题 |
| `GET/PUT /api/v1/forecast/sku-mappings` | 读取/保存产品编码对照 | 租户内一一对应，使用revision防并发覆盖 |
| `POST /api/v1/forecast/training-runs` | 创建首次/追加/重建任务 | 日期、身份、改写确认和评测门均不可绕过 |
| `POST /api/v1/forecast/append` | 严格格式快速追加 | 仅日粒度ISO/Excel日期、退货前非负汇总件数；自动选择`initial`或`append` |

`/forecast/append`是兼容入口而不是另一套训练协议。服务端仍依次调用上传、预检、确认和`training-runs`创建逻辑；有当前`tenant_private`部署时选择`append`，否则选择`initial`。非标准文件必须改走交互式预检，不能在该接口内猜测日期、退货或字段语义。

`user` 是企业业务账号；`admin` 是独立平台管理员，普通企业API要求user身份。平台模型注册/部署使用内部服务身份，不增加第三种浏览器角色。管理员上传Python直接替换入口已返回409 `FORECAST_STANDARD_TRAINING_REQUIRED`，需通过标准数据与独立评测协议。

## 4. 部署

```bash
psql -d furniscope -v ON_ERROR_STOP=1 -f migrations/v3_forecast_integration.sql
```

```dotenv
FORECAST_ENABLED=true
FORECAST_ENGINE_ROOT=backend/furniscope_forecast
FORECAST_STATE_DIR=forecast_assets/state
FORECAST_ARTIFACT_ROOT=forecast_assets/tenants
FORECAST_MODEL_VERSION=sales-forecast-v4
FORECAST_MAX_PAIRS=100
```

相对路径始终相对 FurniScope 项目根目录解析，不受进程启动目录影响。
共享模型只能使用 `server-managed://default`；租户私有模型只能指向
`server-managed://tenant/{tenant_id}/...`，并解析到 `FORECAST_ARTIFACT_ROOT` 下。
HTTP 业务请求不能提交文件路径。内部部署入口会校验实际制品指纹、显式绑定租户并同步
该模型的 SKU×站点目录；历史任务保留部署、模型版本和校验和。

已有 V3 数据库升级执行：

```bash
psql -d furniscope -v ON_ERROR_STOP=1 -f migrations/v3_1_tenant_forecast.sql
```

内部服务显式为租户部署当前模型：

```http
POST /internal/v1/forecast/tenants/{tenant_id}/deploy
X-Internal-Token: <service-token>
Content-Type: application/json

{"version":"sales-forecast-v4","state_uri":"server-managed://default","model_scope":"shared_base"}
```

新品/短历史 SKU 不会被伪装成完整训练样本。目录标记 `model_eligible=false` 时，
创建任务必须提供 `scenario.baseline` 或 `scenario.reference_sku`，调用 V4 已有的确定性
冷启动分支；结果指标记录 `baseline_cold_start`、`reference_sku_transfer` 或 `v4` 路由。

资产校验：

```bash
cd forecast_assets
sha256sum -c MANIFEST.sha256
```

开发环境支持 FastAPI 后台任务；生产要求Redis队列和独立Worker。租户会话及后台训练执行受限角色，制品路径、内容SHA及owner必须一致。

## 5. 前端

销量预测页读取模型状态、创建/启动预测、查询历史、导出CSV/JSON并展示生产建议。“模型训练”页签分为：

1. **标准数据建模**：新企业默认入口，支持CSV/XLSX/JSON上传、字段与业务口径、质量样本、SHA及审计下载、确认标准版本、按需产品编码对照、训练范围预览与首次/追加/重建；
2. **快速追加**：企业模型发布后的默认日常入口，仅接收XLSX。浏览器预检表头、ISO/Excel日期、非负件数、日键重复、日期连续性和复杂订单/退货字段；不符合时列明原因并切换标准路径；
3. **页面内二次确认**：文件通过后显示行数、SKU和日期范围，用户确认完整日汇总、退货前件数及同键覆盖规则后才可提交，不使用不可审阅的原生确认框。

文件、规则或产品编码对照变动会使旧训练预览失效。成功发布后刷新模型状态和SKU；`rejected`单独显示为“未达标”。后端校验错误列出部分未关联/冲突SKU及处理方法，并在`details`保留完整目录预览。

Vite默认代理`127.0.0.1:8001`，可通过`VITE_API_PROXY_TARGET`指定本地API；独立部署可设置`VITE_API_BASE_URL`。企业引擎禁用尚不支持的情景参数，结果误差带不称95%置信区间。
