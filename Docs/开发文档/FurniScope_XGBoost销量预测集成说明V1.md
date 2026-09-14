# FurniScope XGBoost 销量预测集成说明 V1

## 1. 集成边界

FurniScope 已内置 Sales Forecast V4 推理与训练引擎、当前生效的日/周
模型权重、特征状态、编码器、全量订单/库存数据和追加样例。部署
`/data/projects/sales_forecast/Furniscope` 时不再需要访问父目录。

资产位于 `forecast_assets/`，完整性由 `MANIFEST.sha256` 校验。运行路径
仅由服务端配置，HTTP 请求不能提交模型路径或任意 pickle。

预测引擎支持：

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
建议生产量为点预测与安全库存之和；这两个值由确定性程序计算。

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

`user` 是租户业务员；`admin` 继承同租户全部业务权限，并额外管理本租户用户、
数据源和 SKU。平台模型注册/部署使用内部服务身份，不增加第三种浏览器角色。

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

当前任务通过 FastAPI 后台任务执行，适合已有原型和单实例部署。多实例生产部署时，
应将 `ForecastService.execute` 投递到独立任务队列；数据库任务、运行和结果契约无需改变。

## 5. 前端

UI V2 的销量预测页已改为调用正式 API：读取模型状态、创建/启动任务、轮询状态、
显示逐期预测、导出 CSV/JSON，并展示安全库存和建议生产量。Vite 开发服务器把
`/api` 代理到 `127.0.0.1:8000`；独立部署可设置 `VITE_API_BASE_URL`。
