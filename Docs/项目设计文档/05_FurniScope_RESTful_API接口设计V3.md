# FurniScope RESTful API 接口设计 V3.2

> 2026-10-04企业闭环增量接口见[企业决策与标准数据API实现说明](../开发文档/FurniScope_企业决策与标准数据API实现说明V1.md)，受[15总体设计](./15_FurniScope_企业决策与数据闭环总体设计V1.md)统一约束。新增`/enterprise/opportunity-policy`、机会反馈/导出、`/forecast/data-imports`及预检/确认/审计/修订、`/forecast/training-preview`、标准数据训练任务。该域以`expected_version/expected_revision/preview_sha256`进行显式并发校验，是通用If-Match约定的新增特例；训练创建仍需Idempotency-Key。

2026-10-05增加`/enterprise/fact-vocabulary`、机会`outcomes`修订及`opportunity-ranking/export`、`/forecast/sku-mappings`、`import-templates`及模板应用/辅助表对账；详见上述API实现说明。产品修改/确认继续使用If-Match；SKU映射使用摘要`expected_revision`，模板/实施用数字修订。排序导出固定`as_of`并按任务整组分页，保留缺标签及排除原因。

新增训练状态`rejected`表示未达标并保留旧部署。管理员Python直接替换返回409 `FORECAST_STANDARD_TRAINING_REQUIRED`。企业引擎`tenant-xgb-v2`按SKU滚动评测，兼容旧v1制品；两者均不接受价格/促销/库存弹性情景。未验证预测区间和自动库存/生产建议允许为空。普通企业API仅user可用；管理员从独立平台入口执行管理操作。

## 1. 文档说明

本文定义“跨境家具超级 AI 员工”V3 核心 API。接口字段以《FurniScope产品数据字典V3》为唯一口径，工作流语义以《FurniScope Agent工作流设计V2》为准，数据持久化以《FurniScope PostgreSQL数据库设计V3》为准。

V3 只有 `user/admin` 两类系统角色：`user` 完成全部业务操作；`admin` 额外管理 user、Prompt、模型路由、系统配置和运行诊断，但不能代替 user 回答业务确认。`owner/product_rd/market_ops/sales` 仅是目标用户画像，不进入 Token、Header、Body、权限枚举或错误码。

### 1.1 接口范围

- P0：比赛 Demo 和完整核心产品闭环必须实现。
- P1：平台管理与运行诊断能力；不进入 user 主流程。
- Internal：后端、LangGraph 与 Model Router 之间调用，不向浏览器开放。
- 已移出核心：Listing 生成、报告文件导出、验证任务、多人报告评审、岗位审批和 user 手工节点重试。

### 1.2 基础约定

- Base URL：`/api/v1`
- Content-Type：`application/json; charset=utf-8`；文件上传使用 `multipart/form-data`
- 时间：ISO 8601 UTC，如 `2026-08-09T10:30:00Z`
- 对外标识：任务使用 `task_uuid`，报告使用 `report_uuid`，确认使用 `confirmation_id`；不暴露数据库内部 ID，现有产品/数据集数值 ID 沿用 V2 兼容口径。
- 分页：`page` 从 1 开始，`page_size` 默认 20、最大 100。
- 排序：白名单字段；禁止客户端传入 SQL 片段。

### 1.3 通用 Header

| Header | 类型 | 必选 | 说明 |
|---|---|:---:|---|
| `Authorization` | string | 是（登录/刷新除外） | `Bearer <access_token>` |
| `X-Request-ID` | UUID | 否 | 链路追踪；缺失时服务端生成 |
| `Idempotency-Key` | string(128) | 写接口条件必选 | 创建任务、启动、确认、admin恢复等防重复 |
| `If-Match` | string | 更新接口条件必选 | 采用资源`updated_at`规范化生成的强ETag；服务端比较后更新，冲突返回409；客户端不得提交独立version字段 |

Token Claims 只包含稳定身份和范围。Access Token仅允许`RS256`，Header必须包含已发布公钥对应的`kid`；Claims固定为`iss=furniscope-api`、`aud=furniscope-web`、`sub=<user_id>`、`user_id`、`tenant_id`、`role_code`、`iat`、`nbf`、`exp`、`jti`。TTL为15分钟，允许时钟偏差60秒。签名私钥只从`FURNISCOPE_JWT_PRIVATE_KEY`或密钥管理服务读取；验签公钥集只从`FURNISCOPE_JWT_PUBLIC_KEYS_JSON`读取并按`kid`索引。拒绝无`kid`、未知`kid`、`alg=none`及算法降级。每次鉴权必须按Token中的user_id+tenant_id查询`users/tenants`，user非active或tenant非active立即拒绝；不另建Access Token黑名单。Refresh Token TTL为30天。日志只能记录`jti`摘要前12位，不记录Token。生产环境缺少密钥配置时应用启动失败。客户端不得传入或覆盖`tenant_id`。

### 1.4 通用响应

成功：

```json
{
  "success": true,
  "data": {},
  "request_id": "8ca3c18b-a76c-4f78-9df0-1e96553d9068",
  "timestamp": "2026-08-09T10:30:00Z"
}
```

失败：

```json
{
  "success": false,
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "请求资源不存在或不可访问",
    "details": []
  },
  "request_id": "8ca3c18b-a76c-4f78-9df0-1e96553d9068",
  "timestamp": "2026-08-09T10:30:00Z"
}
```

分页 `data`：`items/total/page/page_size/has_next`。业务逻辑只能依赖稳定 `error.code`，不得解析 message。

### 1.5 HTTP 状态

| 状态 | 场景 |
|---:|---|
| 200 | 查询或幂等重复提交成功 |
| 201 | 同步创建成功 |
| 202 | 异步任务、恢复或诊断控制已受理 |
| 400 | 格式、枚举或业务前置参数错误 |
| 401/403 | 未认证/无角色或租户权限 |
| 404 | 资源不存在或因租户隔离不可见 |
| 409 | 幂等、版本、状态或 Checkpoint 冲突 |
| 422 | JSON Schema 或字段语义校验失败 |
| 429 | 配额或限流 |
| 500/502/503/504 | 内部、上游或超时故障 |

## 2. 接口总览

| 编号 | 优先级 | 方法与路径 | 权限 | 用途 |
|---|---|---|---|---|
| API-AUTH-01 | P0 | POST `/auth/login` | Public | 登录 |
| API-AUTH-02 | P0 | POST `/auth/refresh` | Public | 刷新令牌 |
| API-AUTH-03 | P0 | GET `/users/me` | user/admin | 当前身份 |
| API-DSH-01 | P0 | GET `/dashboard/summary` | user/admin | 工作台聚合 |
| API-PRD-01 | P0 | POST `/products` | user | 创建产品 |
| API-PRD-02 | P0 | GET `/products` | user | 产品列表 |
| API-PRD-03 | P0 | GET `/products/{product_id}` | user | 产品与画像详情 |
| API-PRD-04 | P0 | PATCH `/products/{product_id}` | user | 更新产品/草稿画像 |
| API-PRD-05 | P0 | POST `/products/{product_id}/assets:parse` | user | 上传并解析资料 |
| API-PRD-06 | P0 | GET `/product-parse-jobs/{parse_job_id}` | user | 查询解析任务 |
| API-PRD-07 | P0 | POST `/products/{product_id}/profile:confirm` | user | 确认画像版本 |
| API-DAT-01 | P0 | POST `/market-datasets` | user | 创建授权数据集 |
| API-DAT-02 | P0 | POST `/market-datasets/{dataset_id}/imports` | user | 导入商品和评论 |
| API-DAT-03 | P0 | GET `/market-datasets` | user | 数据集列表 |
| API-DAT-04 | P0 | GET `/market-datasets/{dataset_id}` | user | 数据质量与范围 |
| API-INS-01 | P0 | POST `/analysis-tasks` | user | 创建分析任务 |
| API-INS-02 | P0 | POST `/analysis-tasks/{task_uuid}:start` | user | 启动超级AI员工 |
| API-INS-03 | P0 | GET `/analysis-tasks/{task_uuid}` | user/admin | 五阶段状态；admin可诊断下钻 |
| API-INS-04 | P0 | GET `/analysis-tasks/{task_uuid}/result` | user | 综合结果聚合入口 |
| API-CFM-01 | P0 | GET `/user-confirmations` | user | 查询待确认事项 |
| API-CFM-02 | P0 | POST `/user-confirmations/{confirmation_id}:respond` | user | 回答并从Checkpoint恢复 |
| API-CMP-01 | P0 | GET `/analysis-tasks/{task_uuid}/competitors` | user | 竞品下钻 |
| API-REV-01 | P0 | GET `/analysis-tasks/{task_uuid}/review-aspects` | user | 评论观点下钻 |
| API-REV-02 | P0 | GET `/analysis-tasks/{task_uuid}/insight-clusters` | user | 需求聚类下钻 |
| API-EVD-01 | P0 | GET `/analysis-tasks/{task_uuid}/evidence` | user | 证据下钻 |
| API-OPP-01 | P0 | GET `/analysis-tasks/{task_uuid}/opportunities` | user | 机会详情 |
| API-REC-01 | P0 | GET `/analysis-tasks/{task_uuid}/recommendations` | user | 工程建议详情 |
| API-RPT-01 | P0 | GET `/reports/{report_uuid}` | user | 在线报告 |
| API-ADM-01 | P1 | GET `/admin/users` | admin | user管理查询 |
| API-ADM-02 | P1 | PATCH `/admin/users/{user_id}` | admin | user角色/状态管理 |
| API-ADM-03 | P1 | GET `/admin/model-routes/{task_type}` | admin | 查询模型和算力配置 |
| API-ADM-04 | P1 | PUT `/admin/model-routes/{task_type}` | admin | 更新模型和算力配置 |
| API-ADM-05 | P1 | GET `/admin/prompt-templates` | admin | 查询Prompt版本 |
| API-ADM-06 | P1 | POST `/admin/prompt-templates` | admin | 创建Prompt版本 |
| API-ADM-07 | P1 | GET `/admin/analysis-tasks/{task_uuid}/diagnostics` | admin | 脱敏运行诊断 |
| API-ADM-08 | P1 | POST `/admin/analysis-tasks/{task_uuid}:recover` | admin | 安全诊断恢复/停止 |
| API-MDL-01 | Internal | POST `/internal/v1/model-router/invoke` | service | 结构化推理 |
| API-MDL-02 | Internal | POST `/internal/v1/model-router/embeddings` | service | 向量化 |
| API-MDL-03 | Internal | POST `/internal/v1/model-router/rerank` | service | 竞品重排 |

除 admin 接口外，表中 `user` 业务权限也允许 admin 在授权租户上下文中只读诊断；admin 不得调用 API-CFM-02 代答，也不默认获得跨租户正文访问。

## 3. 认证与工作台

### API-AUTH-01 用户登录

- 优先级：P0
- 路径/方法：`POST /api/v1/auth/login`
- 权限：Public
- Header：`Content-Type`、可选 `X-Request-ID`
- Path/Query：无
- Body：

| 参数 | 类型 | 必选 | 说明 |
|---|---|:---:|---|
| `email` | string(254) | 是 | 小写归一化；全系统唯一，服务端由user记录自动解析tenant_id，用户不输入tenant_code |
| `password` | string | 是 | 仅用于认证，不记录日志 |

成功 200：

```json
{"success":true,"data":{"access_token":"***","refresh_token":"***","token_type":"Bearer","expires_in":900,"user":{"user_id":12,"email":"user@example.com","name":"Demo User","role_code":"user","status":"active"}},"request_id":"...","timestamp":"..."}
```

认证存储规则：密码仅以`users.password_hash`保存自描述哈希；Access Token短期签名且不落库；Refresh Token仅以SHA-256摘要写入`auth_sessions`，每次刷新必须轮换。旧Refresh Token复用时撤销同一`token_family_uuid`全部会话并返回`AUTH_TOKEN_REUSE_DETECTED`。

失败：401 `AUTH_INVALID_CREDENTIALS`；403 `USER_DISABLED`/`TENANT_SUSPENDED`；429 `AUTH_RATE_LIMITED`。

### API-AUTH-02 刷新访问令牌

- 优先级：P0；路径/方法：`POST /api/v1/auth/refresh`；权限：Public
- Header：通用非鉴权 Header；Path/Query：无
- Body：`refresh_token:string`，必选。
- 成功 200：原会话在同一事务中撤销并建立后继`auth_sessions`，随后返回新的 `access_token/refresh_token/token_type/expires_in`；Token明文不得写日志或数据库。
- 失败：401 `AUTH_REFRESH_TOKEN_INVALID`/`AUTH_REFRESH_TOKEN_EXPIRED`；409 `AUTH_TOKEN_REUSE_DETECTED`。

### API-AUTH-03 获取当前用户

- 优先级：P0；路径/方法：`GET /api/v1/users/me`；权限：user/admin
- Header：通用鉴权 Header；Path/Query/Body：无
- 成功 200：

```json
{"success":true,"data":{"user_id":12,"email":"user@example.com","name":"Demo User","role_code":"user","status":"active","tenant":{"tenant_code":"DEMO","name":"Demo Furniture","default_timezone":"Asia/Shanghai","default_currency":"USD"}},"request_id":"...","timestamp":"..."}
```

- 失败：401 `AUTH_TOKEN_INVALID`；403 `TENANT_CONTEXT_MISMATCH`。

### API-DSH-01 获取工作台聚合摘要

- 优先级：P0；路径/方法：`GET /api/v1/dashboard/summary`；权限：user/admin只读
- Header：通用鉴权 Header；Path/Body：无
- Query：`recent_limit:int` 否，默认5、1—20。
- 成功 200：

```json
{"success":true,"data":{"metrics":{"active_product_count":3,"running_task_count":1,"waiting_confirmation_count":1,"online_report_count":2},"recent_tasks":[{"task_uuid":"...","job_name":"US Sofa Opportunity","product_name":"Modular Sofa","status":"waiting_human","stage":"researching_market","progress_percent":36.00,"updated_at":"..."}],"confirmation_todos":[{"confirmation_id":"...","confirmation_type":"low_confidence","question":"是否采用当前可比样本范围？","checkpoint_stage":"competitor_reranking","expires_at":null}],"recent_reports":[{"report_uuid":"...","title":"US Sofa Market Insight","overall_opportunity_score":78.20,"overall_confidence":0.81}]},"request_id":"...","timestamp":"..."}
```

- 失败：400 `DASHBOARD_FILTER_INVALID`；403 `TENANT_CONTEXT_MISMATCH`；500 `DASHBOARD_QUERY_FAILED`。

## 4. 产品与资料

### API-PRD-01 创建产品

- 优先级：P0；路径/方法：`POST /api/v1/products`；权限：user
- Header：鉴权、`Idempotency-Key`；Path/Query：无
- Body：`sku:string`必选、`name:string`必选、`category_code:string`必选（P0为sofa）、`description:text`可选。
- 成功 201：`{"product_id":101,"sku":"SOFA-001","name":"Modular Sofa","category_code":"sofa","analysis_status":"draft","current_profile_version_id":null}`。
- 失败：409 `PRODUCT_SKU_CONFLICT`/`IDEMPOTENCY_CONFLICT`；422 `PRODUCT_CATEGORY_INVALID`。

### API-PRD-02 查询产品列表

- 优先级：P0；路径/方法：`GET /api/v1/products`；权限：user
- Header：鉴权；Path/Body：无
- Query：`page/page_size`可选，`analysis_status`可选，`category_code`可选，`keyword`可选。
- 成功 200：分页 items，每项返回 `product_id/sku/name/category_code/analysis_status/current_profile_version_id/created_at/updated_at/has_conflicts/moq/factory_price`；`has_conflicts`表示当前画像是否存在待人工处理的多源参数冲突，`moq/factory_price`用于产品中心商业参数摘要。
- 失败：400 `PAGINATION_INVALID`；403 `TENANT_CONTEXT_MISMATCH`。

### API-PRD-03 获取产品与画像详情

- 优先级：P0；路径/方法：`GET /api/v1/products/{product_id}`；权限：user
- Header：鉴权；Path：`product_id:integer`必选；Query：`profile_version_id:integer`可选；Body：无。
- 成功 200：返回产品字段、`profile_version`、`completeness_score`、`source_summary`、`attributes[]`；属性含 `attribute_code/attribute_name/value_type/attribute_value/unit/source_type/source_locator/confidence/confirmation_status`。
- 失败：404 `PRODUCT_NOT_FOUND`/`PRODUCT_PROFILE_NOT_FOUND`；403 `TENANT_CONTEXT_MISMATCH`。

### API-PRD-04 更新产品与草稿画像

- 优先级：P0；路径/方法：`PATCH /api/v1/products/{product_id}`；权限：user
- Header：鉴权、`If-Match`；Path：product_id；Query：无
- Body：`name/description/analysis_status`均可选；`attributes`可选数组，条目使用 API-PRD-03 属性字段，不允许改已确认不可变版本。
- 成功 200：返回产品、最新草稿`profile_version_id`和`resource_version`；响应Header同时返回同值`ETag`。
- 失败：404 `PRODUCT_NOT_FOUND`；409 `RESOURCE_VERSION_CONFLICT`；422 `PRODUCT_PROFILE_IMMUTABLE`/`PRODUCT_ATTRIBUTE_INVALID`。

多源属性合并必须遵循 `人工填写/人工确认 > 文档提取 > AI视觉识别/推断`。高优先级值覆盖展示值时，低优先级差异必须保留在来源定位中并标记 `conflicted`，不得静默丢弃；人工修正基于完整当前画像创建草稿版本，不得只保留本次修改字段。

### API-PRD-05 上传并解析产品资料

- 优先级：P0；路径/方法：`POST /api/v1/products/{product_id}/assets:parse`；权限：user
- Header：鉴权、Idempotency-Key、`multipart/form-data`；Path：product_id；Query：无
- Body：`files:file[]`必选，`source_type:string`必选，`parse_config:JSON`可选且按版本化Schema校验。
- 成功 202：`{"parse_job_id":"uuid","product_id":101,"status":"queued","progress_percent":0,"current_stage":"queued"}`。
- 失败：400 `FILE_EMPTY`；413 `FILE_SIZE_EXCEEDED`；415 `FILE_TYPE_UNSUPPORTED`；422 `PARSE_CONFIG_INVALID`；409 `IDEMPOTENCY_CONFLICT`。

### API-PRD-06 查询产品解析任务

- 优先级：P0；路径/方法：`GET /api/v1/product-parse-jobs/{parse_job_id}`；权限：user
- Header：鉴权；Path：`parse_job_id:UUID`；Query：`include_files:boolean`默认true；Body：无
- 成功 200：

```json
{"success":true,"data":{"parse_job_id":"...","product_id":101,"status":"running","progress_percent":65,"current_stage":"schema_mapping","summary":{"file_count":2,"succeeded_file_count":1,"failed_file_count":0},"file_results":[{"file_name":"spec.pdf","security_status":"clean","parse_status":"succeeded"}],"retryable":true,"failure_code":null,"failure_message":null},"request_id":"...","timestamp":"..."}
```

- 失败：404 `PARSE_JOB_NOT_FOUND`；403 `TENANT_CONTEXT_MISMATCH`。

### API-PRD-07 确认产品画像

- 优先级：P0；路径/方法：`POST /api/v1/products/{product_id}/profile:confirm`；权限：user
- Header：鉴权、Idempotency-Key、If-Match；Path：product_id；Query：无
- Body：`profile_version_id:integer`必选；`confirmed_attribute_codes:string[]`必选；冲突属性必须先通过统一确认或明确为unknown。
- 成功 200：返回 `product_id/profile_version_id/status=confirmed/completeness_score/confirmed_at`。
- 失败：409 `RESOURCE_VERSION_CONFLICT`；422 `PRODUCT_PROFILE_CONFLICTED`/`PRODUCT_PROFILE_INCOMPLETE`；404 `PRODUCT_PROFILE_NOT_FOUND`。

## 5. 市场数据集

### API-DAT-01 创建市场数据集

- 优先级：P0；路径/方法：`POST /api/v1/market-datasets`；权限：user
- Header：鉴权、Idempotency-Key；Path/Query：无
- Body：`name/platform/market_country/category_code/data_start_date/data_end_date/source_type/source_name`必选；`authorization_reference`按授权类型条件必选；`field_mapping`可选数组。
- 成功 201：返回 `dataset_id/name/status=uploaded/platform/market_country/category_code/version_no`。
- 失败：422 `DATASET_SCOPE_INVALID`/`DATASET_AUTHORIZATION_REQUIRED`/`FIELD_MAPPING_INVALID`；409 `IDEMPOTENCY_CONFLICT`。

### API-DAT-02 导入竞品和评论

- 优先级：P0；路径/方法：`POST /api/v1/market-datasets/{dataset_id}/imports`；权限：user
- Header：鉴权、Idempotency-Key、multipart；Path：dataset_id；Query：无
- Body：`files:file[]`必选；`field_mapping:JSON[]`可选；`deduplication_strategy:string`必选且使用既有规则。
- 成功 202：返回 `dataset_id/status=validating` 及导入受理时间；进度通过 API-DAT-04 查询。
- 失败：404 `DATASET_NOT_FOUND`；409 `DATASET_IMPORT_IN_PROGRESS`；422 `FIELD_MAPPING_INVALID`/`DATASET_FILE_INVALID`。

### API-DAT-03 查询数据集列表

- 优先级：P0；路径/方法：`GET /api/v1/market-datasets`；权限：user
- Header：鉴权；Path/Body：无
- Query：page/page_size、platform、market_country、category_code、status 均可选。
- 成功 200：分页返回 `dataset_id/name/platform/market_country/category_code/status/listing_count/review_count/valid_review_count/quality_score/data_start_date/data_end_date`。
- 失败：400 `DATASET_FILTER_INVALID`；403 `TENANT_CONTEXT_MISMATCH`。

### API-DAT-04 获取数据集质量与范围

- 优先级：P0；路径/方法：`GET /api/v1/market-datasets/{dataset_id}`；权限：user
- Header：鉴权；Path：dataset_id；Query/Body：无
- 成功 200：返回数据集完整字段、`field_mapping/quality_report/limitations/listing_count/review_count/valid_review_count/quality_score`。
- 失败：404 `DATASET_NOT_FOUND`；409 `DATASET_NOT_READY`（仅在调用方要求ready语义时）；403 `TENANT_CONTEXT_MISMATCH`。

## 6. 超级 AI 员工任务

### API-INS-01 创建分析任务

- 优先级：P0；路径/方法：`POST /api/v1/analysis-tasks`；权限：user
- Header：鉴权、Idempotency-Key；Path/Query：无
- Body：

| 参数 | 类型 | 必选 | 说明 |
|---|---|:---:|---|
| `job_name` | string(200) | 是 | 任务名称 |
| `job_type` | enum | 是 | `product_market_fit/product_improvement` |
| `product_id` | integer | 是 | 同租户产品 |
| `product_profile_version_id` | integer | 是 | confirmed画像 |
| `dataset_id` | integer | 是 | ready数据集 |
| `target_country` | string(2) | 是 | 与数据集一致 |
| `target_platform` | string | 是 | 与数据集一致 |
| `analysis_currency` | string(3) | 是 | ISO 4217 |
| `analysis_config` | object | 是 | 版本化阈值、Top-K、权重快照 |

- 成功 201：返回 `task_uuid/job_name/job_type/status=draft/stage=understanding_product/progress_percent=0/report_uuid=null`。
- 失败：404 `PRODUCT_NOT_FOUND`/`DATASET_NOT_FOUND`；409 `IDEMPOTENCY_CONFLICT`；422 `PRODUCT_PROFILE_NOT_CONFIRMED`/`DATASET_NOT_READY`/`TASK_SCOPE_MISMATCH`。

### API-INS-02 启动分析任务

- 优先级：P0；路径/方法：`POST /api/v1/analysis-tasks/{task_uuid}:start`；权限：user
- Header：鉴权、Idempotency-Key；Path：task_uuid；Query/Body：无
- 成功 202：返回 `task_uuid/status=queued/stage=understanding_product/progress_percent/checkpoint_stage/retryable=false`。
- 失败：404 `TASK_NOT_FOUND`；409 `TASK_ALREADY_STARTED`/`IDEMPOTENCY_CONFLICT`；422 `TASK_PREFLIGHT_FAILED`；429 `COMPUTE_QUOTA_EXCEEDED`。

### API-INS-03 查询任务状态

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}`；权限：user/admin
- Header：鉴权；Path：task_uuid；Body：无
- Query：`include_stage_runs:boolean`可选，user默认false、admin可true；`stage_run_limit:int`可选，最大100。
- 刷新规则：任务非终态且页面可见时每3秒轮询；连续5次无变化退避至5秒；页面后台暂停；收到 `waiting_human` 立即展示确认；终态停止。`updated_at`/状态变化后重置3秒。
- 成功 200：

```json
{
  "success": true,
  "data": {
    "task_uuid": "4c1e...",
    "status": "waiting_human",
    "stage": "researching_market",
    "progress_percent": 36.00,
    "stage_runs": [{"stage_code":"competitor_reranking","attempt_no":1,"status":"waiting_human","started_at":"...","ended_at":null,"retryable":null}],
    "partial_failures": [{"unit_type":"review_batch","failed_count":2,"total_count":10,"impact":"相关主题置信度降低","retryable":true}],
    "checkpoint_stage": "competitor_reranking",
    "retryable": true,
    "user_confirmation": {"confirmation_id":"...","confirmation_type":"low_confidence","question":"是否采用当前可比样本范围？","recommended_option":"use_current_scope","options":[{"code":"use_current_scope","label":"采用当前范围"}],"evidence_refs":[{"source_type":"competitor_match","source_id":"101"}],"impact":{"confidence":"reduced"},"checkpoint_stage":"competitor_reranking","expires_at":null},
    "report_uuid": null
  },
  "request_id":"...","timestamp":"..."
}
```

`stage` 只能是 `understanding_product/researching_market/evaluating_opportunity/generating_recommendation/completed`；`stage_runs` 为脱敏诊断摘要，不构成 user 手工运维入口；`retryable` 表示 Supervisor 可安全自动恢复，不显示“重试节点”按钮。

- 失败：404 `TASK_NOT_FOUND`；403 `TENANT_CONTEXT_MISMATCH`；409 `WORKFLOW_STATE_INCONSISTENT`。

### API-INS-04 获取任务综合结果

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/result`；权限：user
- Header：鉴权；Path：task_uuid；Query：`opportunity_limit`默认5、`recommendation_limit`默认10；Body：无
- 说明：S05/S06 主结果入口；返回摘要和下钻链接，不在客户端编排多个 Agent 接口。
- 成功 200：

```json
{"success":true,"data":{"task_uuid":"...","report_uuid":"...","report_summary":{"title":"US Sofa Insight","executive_summary":"...","decision_recommendation":"prioritize_validate","overall_opportunity_score":78.2,"overall_confidence":0.81},"data_scope":{"target_country":"US","target_platform":"amazon","data_start_date":"2026-01-01","data_end_date":"2026-07-31","listing_count":120,"valid_review_count":8600,"limitations":[]},"competitor_summary":{"competitor_set_version":2,"direct":12,"benchmark":6,"substitute":4,"excluded":18,"available_review_count":3100},"insight_clusters":[{"cluster_id":31,"cluster_name":"seat_support","sentiment":"negative","importance_score":86.4,"cluster_confidence":0.84}],"opportunities":[{"opportunity_id":51,"opportunity_code":"OP-01","title":"Durable seat support","base_score":78.2,"confidence":0.81,"recommendation_level":"prioritize_validate"}],"recommendations":[{"recommendation_id":71,"opportunity_id":51,"recommendation_type":"structure","recommended_action":"...","priority":"high","confidence":0.79}],"partial_failures":[]},"request_id":"...","timestamp":"..."}
```

- 失败：404 `TASK_NOT_FOUND`；409 `TASK_RESULT_NOT_READY`; 422 `TASK_RESULT_INCOMPLETE`（关键证据审计未通过）。

## 7. 统一 user_confirmation

### API-CFM-01 查询确认事项

- 优先级：P0；路径/方法：`GET /api/v1/user-confirmations`；权限：user；admin不可代答，仅可通过诊断接口查看脱敏状态
- Header：鉴权；Path/Body：无
- Query：`status`可选（默认pending）、`task_uuid`可选、page/page_size可选。
- 成功 200：分页返回 `confirmation`；pending确认只投影九字段：`confirmation_id/confirmation_type/question/recommended_option/options/evidence_refs/impact/checkpoint_stage/expires_at`。
- 失败：400 `CONFIRMATION_FILTER_INVALID`；403 `CONFIRMATION_USER_REQUIRED`。

### API-CFM-02 提交确认并恢复工作流

- 优先级：P0；路径/方法：`POST /api/v1/user-confirmations/{confirmation_id}:respond`；权限：仅任务所属租户的 user
- Header：鉴权、Idempotency-Key；Path：`confirmation_id:UUID`；Query：无
- Body：

| 参数 | 类型 | 必选 | 说明 |
|---|---|:---:|---|
| `selected_option` | string(100) | 是 | 必须为 options 中稳定 code |
| `user_input` | object | 否 | 按该选项 Schema 校验的必要事实 |

- 原子受理规则：锁定 pending confirmation→校验身份/到期/选项→校验同任务 safe `checkpoint_id/checkpoint_stage`→写入回答与 `responded_by/responded_at`→写 `workflow_control_events(event_type=resume_confirmation)`→提交事务。Worker 事务提交后消费 Outbox，以以下逻辑载荷调用 LangGraph：

```json
{"confirmation_id":"...","selected_option":"use_current_scope","user_input":{}}
```

并执行 `Command(resume=payload)`。相同幂等键与相同答案返回原受理结果，不重复恢复。

- 成功 202：

```json
{"success":true,"data":{"confirmation_id":"...","accepted":true,"resumed_from_checkpoint":true,"task_uuid":"...","status":"responded","checkpoint_stage":"competitor_reranking"},"request_id":"...","timestamp":"..."}
```

事务与恢复语义：Body不得包含`checkpoint_stage/checkpoint_id/tenant_id`。服务端联表锁定confirmation、task、回答user与同任务同租户的safe active Checkpoint；同一事务将confirmation置为responded、`user_confirmation` Stage置为succeeded、task置为queued并写`resume_confirmation` Outbox。响应中的`accepted=true/resumed_from_checkpoint=true`只表示恢复事件已可靠受理，不表示LangGraph已完成后续执行。独立Worker消费Outbox，以服务端载荷调用`Command(resume=...)`；成功后事件和业务Checkpoint置为consumed。

`accepted` 只表示回答和恢复 Outbox 已提交；不表示下游分析完成。随后轮询 API-INS-03。

- 失败：404 `CONFIRMATION_NOT_FOUND`；403 `CONFIRMATION_USER_REQUIRED`/`TENANT_CONTEXT_MISMATCH`；409 `CONFIRMATION_ALREADY_RESPONDED`/`CONFIRMATION_EXPIRED`/`WORKFLOW_CHECKPOINT_CONFLICT`/`IDEMPOTENCY_CONFLICT`；422 `CONFIRMATION_OPTION_INVALID`/`CONFIRMATION_INPUT_INVALID`；503 `WORKFLOW_RESUME_UNAVAILABLE`。

## 8. 结果下钻接口

这些接口只服务“为什么”和详细分析，不用于浏览器编排 Agent 主流程。

### API-CMP-01 查询任务竞品集合

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/competitors`；权限：user
- Header：鉴权；Path：task_uuid；Body：无
- Query：`competitor_type`可选、`set_version`可选默认当前版、page/page_size、`sort_by`可选（rerank_score/review_count/sale_price）。
- 成功 200：返回 `competitor_set_version/competitor_summary/available_review_count/items[]`；item含市场商品基础字段、`competitor_type/category_score/function_score/style_score/price_score/material_score/scenario_score/overall_score/rerank_score/match_reasons/set_version`。
- 失败：404 `TASK_NOT_FOUND`；409 `COMPETITOR_RESULT_NOT_READY`；400 `COMPETITOR_FILTER_INVALID`。

### API-REV-01 查询评论观点

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/review-aspects`；权限：user
- Header：鉴权；Path：task_uuid；Body：无
- Query：taxonomy_code、sentiment、listing_id、min_confidence、page/page_size 可选。
- 成功 200：items含 `aspect_id/review_id/taxonomy_code/opinion_text/sentiment/evidence_start/evidence_end/evidence_quote/extraction_confidence/model_run_id`；原文和翻译分列。
- 失败：409 `REVIEW_ANALYSIS_NOT_READY`；404 `TASK_NOT_FOUND`；400 `REVIEW_FILTER_INVALID`。

### API-REV-02 查询需求聚类

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/insight-clusters`；权限：user
- Header：鉴权；Path：task_uuid；Body：无
- Query：sentiment、taxonomy_code、min_confidence、page/page_size 可选。
- 成功 200：items含 `cluster_id/cluster_code/cluster_name/taxonomy_code/sentiment/aspect_count/review_count/listing_count/mention_rate/cross_listing_rate/importance_score/cluster_confidence/representative_aspect_ids`。
- 失败：409 `INSIGHT_RESULT_NOT_READY`；404 `TASK_NOT_FOUND`。

### API-EVD-01 查询证据链

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/evidence`；权限：user
- Header：鉴权；Path：task_uuid；Body：无
- Query：`claim_type`必选、`claim_id`必选、`claim_path`可选、page/page_size可选。
- 成功 200：返回 `claim_type/claim_id/claim_path/evidence_drawer`；证据项使用 `evidence_type/evidence_id/support_type/relevance_score/is_primary/display_order`，评论原文Span由关联 `review_aspects` 返回。
- 失败：404 `EVIDENCE_NOT_FOUND`；422 `EVIDENCE_CLAIM_INVALID`；409 `EVIDENCE_AUDIT_INCOMPLETE`。

### API-OPP-01 查询机会详情

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/opportunities`；权限：user
- Header：鉴权；Path：task_uuid；Body：无
- Query：recommendation_level、min_confidence、page/page_size可选。
- 成功 200：items含机会定义、六项评分、`base_score/overall_score/confidence/recommendation_level/weight_config/scoring_version/manufacturing_fit/evidence_refs`。
- 失败：409 `OPPORTUNITY_RESULT_NOT_READY`；404 `TASK_NOT_FOUND`。

### API-REC-01 查询产品工程建议

- 优先级：P0；路径/方法：`GET /api/v1/analysis-tasks/{task_uuid}/recommendations`；权限：user
- Header：鉴权；Path：task_uuid；Body：无
- Query：opportunity_id、recommendation_type、priority、page/page_size可选。
- 成功 200：items含 `recommendation_id/opportunity_id/recommendation_type/problem_statement/root_cause_hypotheses/recommended_action/expected_benefit/impact_dimensions/priority/confidence/validation_method/evidence_cluster_ids/model_run_id`；不含专家审核字段。
- 失败：409 `RECOMMENDATION_RESULT_NOT_READY`；404 `TASK_NOT_FOUND`。

### API-RPT-01 获取在线分析报告

- 优先级：P0；路径/方法：`GET /api/v1/reports/{report_uuid}`；权限：user
- Header：鉴权；Path：report_uuid；Query：`include_model_trace:boolean`默认true；Body：无
- 成功 200：返回 `report_uuid/task_uuid/report_version/title/status/executive_summary/decision_recommendation/overall_opportunity_score/overall_confidence/data_scope_snapshot/product_profile_snapshot/enterprise_profile_snapshot/target_user_summary/price_summary/risk_summary/pending_validation_items/sections/model_trace/limitations/partial_failures_snapshot/version_bundle/generated_model_run_id`。
- 失败：404 `REPORT_NOT_FOUND`；409 `REPORT_NOT_READY`/`REPORT_EVIDENCE_AUDIT_FAILED`；403 `TENANT_CONTEXT_MISMATCH`。

V3 只提供在线报告，不提供发布审批、文件导出或导出历史。

## 9. Admin 管理与诊断（P1）

### API-ADM-01 查询 user

- 优先级：P1；路径/方法：`GET /api/v1/admin/users`；权限：admin
- Header：鉴权；Path/Body：无；Query：tenant_id、status、keyword、page/page_size可选。
- 成功 200：items含 `user_id/tenant_id/email/name/role_code/status/last_login_at/created_at`。
- 失败：403 `ADMIN_REQUIRED`；400 `USER_FILTER_INVALID`。

### API-ADM-02 更新 user 状态或静态角色

- 优先级：P1；路径/方法：`PATCH /api/v1/admin/users/{user_id}`；权限：admin
- Header：鉴权、If-Match；Path：user_id；Query：无
- Body：`name`可选、`status`可选（invited/active/disabled/locked）、`role_code`可选且仅user/admin。
- 成功 200：返回更新后的 user 和 version；写 audit_logs。
- 失败：403 `ADMIN_REQUIRED`；404 `USER_NOT_FOUND`；409 `RESOURCE_VERSION_CONFLICT`；422 `USER_ROLE_INVALID`。

### API-ADM-03 查询模型路由

- 优先级：P1；路径/方法：`GET /api/v1/admin/model-routes/{task_type}`；权限：admin
- Header：鉴权；Path：task_type；Query/Body：无。
- 成功 200：返回 `task_type/primary_model_id/fallback_model_ids/timeout_ms/max_retries/batch_size/concurrency_limit/compute_config/active/updated_at`；不返回任何API Key。
- 失败：403 `ADMIN_REQUIRED`；404 `MODEL_ROUTE_NOT_FOUND`；422 `MODEL_ROUTE_CONFIG_INVALID`；409 `RESOURCE_VERSION_CONFLICT`。

### API-ADM-04 更新模型路由

- 优先级：P1；路径/方法：`PUT /api/v1/admin/model-routes/{task_type}`；权限：admin
- Header：鉴权、Idempotency-Key、If-Match；Path：task_type；Query：无。
- Body：`primary_model_id/fallback_model_ids/timeout_ms/max_retries/batch_size/concurrency_limit/compute_config/active`，均采用数据字典V3口径。
- 成功 200：返回更新后的完整非敏感配置和 `updated_at`；写审计日志。
- 失败：403 `ADMIN_REQUIRED`；404 `MODEL_ROUTE_NOT_FOUND`；422 `MODEL_ROUTE_CONFIG_INVALID`；409 `RESOURCE_VERSION_CONFLICT`/`IDEMPOTENCY_CONFLICT`。

### API-ADM-05 查询 Prompt 模板版本

- 优先级：P1；路径/方法：`GET /api/v1/admin/prompt-templates`；权限：admin
- Header：鉴权；Path/Body：无；Query：code/task_type/status/version/page/page_size可选。
- 成功 200：分页返回 `code/version/task_type/template_content/output_schema/status/created_at/updated_at`；诊断响应不得携带模型密钥。
- 失败：403 `ADMIN_REQUIRED`；400 `PROMPT_FILTER_INVALID`。

### API-ADM-06 创建 Prompt 模板版本

- 优先级：P1；路径/方法：`POST /api/v1/admin/prompt-templates`；权限：admin
- Header：鉴权、Idempotency-Key；Path/Query：无。
- Body：`code/version/task_type/template_content/output_schema/status`；版本发布后不可原地改写。
- 成功 201：返回新模板全部字段；写审计日志。
- 失败：403 `ADMIN_REQUIRED`；409 `PROMPT_VERSION_CONFLICT`/`IDEMPOTENCY_CONFLICT`；422 `PROMPT_SCHEMA_INVALID`。

### API-ADM-07 获取任务运行诊断

- 优先级：P1；路径/方法：`GET /api/v1/admin/analysis-tasks/{task_uuid}/diagnostics`；权限：admin
- Header：鉴权；Path：task_uuid；Query：`include_model_runs/include_control_events/include_partial_failures`可选；Body：无
- 成功 200：返回任务 `internal_stage/stage_runs/checkpoint_stage/retryable/partial_failures`、脱敏 `model_runs`、`workflow_control_events` 和版本束；不返回完整评论正文、Prompt正文、密钥或企业敏感输入。
- 失败：403 `ADMIN_REQUIRED`/`ADMIN_DIAGNOSTIC_SCOPE_DENIED`；404 `TASK_NOT_FOUND`。

### API-ADM-08 安全恢复或停止任务

- 优先级：P1；路径/方法：`POST /api/v1/admin/analysis-tasks/{task_uuid}:recover`；权限：admin
- Header：鉴权、Idempotency-Key；Path：task_uuid；Query：无
- Body：`event_type`必选，仅 `auto_retry/safe_stop`；`checkpoint_id`在auto_retry时条件必选；`stage_code`在auto_retry时条件必选；`payload`必选且只含版本、原因和失败单元引用。
- 成功 202：返回 `event_uuid/task_uuid/event_type/status=pending/checkpoint_id/stage_code/requested_at`。恢复由 Supervisor 校验并投递，不允许指定任意内部节点或替user回答确认。
- 失败：409 `WORKFLOW_CHECKPOINT_CONFLICT`/`WORKFLOW_CONFIRMATION_PENDING`/`WORKFLOW_NOT_RECOVERABLE`；403 `ADMIN_REQUIRED`；422 `WORKFLOW_RECOVERY_INPUT_INVALID`。

## 10. Model Router 内部封装

内部接口只允许服务身份（mTLS或服务Token），不使用user/admin Token，不向前端暴露。上游依赖阿里云百炼Model Studio工作空间专属域名；API Key只从`ALIYUN_MODEL_ROUTER_API_KEY`读取。聊天、向量和重排分别使用`ALIYUN_MODEL_ROUTER_CHAT_BASE_URL`、`ALIYUN_MODEL_ROUTER_EMBEDDING_BASE_URL`、`ALIYUN_MODEL_ROUTER_RERANK_BASE_URL`，必须为HTTPS且不得由请求覆盖。

### API-MDL-01 统一结构化推理

- 优先级：Internal；路径/方法：`POST /internal/v1/model-router/invoke`；权限：service
- Header：`Authorization: Bearer <service_token>`、X-Request-ID、Idempotency-Key；Path/Query：无
- Body：`task_uuid/stage_run_id/task_type/prompt_code/prompt_version/input_payload/input_hash/output_schema`必选；`model_hint`可选且不能绕过路由配置。
- 成功 200：返回 `model_run_id/provider/model_id/prompt_version/status/output_payload/input_tokens/output_tokens/latency_ms/schema_valid/retry_count`。
- 失败：422 `MODEL_OUTPUT_SCHEMA_INVALID`；429 `MODEL_RATE_LIMITED`; 502 `MODEL_UPSTREAM_ERROR`; 504 `MODEL_UPSTREAM_TIMEOUT`。

### API-MDL-02 文本向量化

- 优先级：Internal；路径/方法：`POST /internal/v1/model-router/embeddings`；权限：service
- Header：同API-MDL-01；Path/Query：无
- Body：`task_uuid/stage_run_id/task_type/input_hash/texts:string[]`必选。
- 成功 200：返回 `model_run_id/model_id/dimension/vectors/status/token_count/latency_ms`。
- 失败：422 `EMBEDDING_INPUT_INVALID`; 502 `MODEL_UPSTREAM_ERROR`; 504 `MODEL_UPSTREAM_TIMEOUT`。

阿里云上游契约锁定：`POST {ALIYUN_MODEL_ROUTER_EMBEDDING_BASE_URL}/embeddings`，Base URL形如工作空间专属`/compatible-mode/v1`；请求为`{"model":"text-embedding-v4","input":texts,"dimensions":1024,"encoding_format":"float"}`。texts数量1—10，每项非空且不超过8192 tokens。响应读取`data[].index/data[].embedding`、`model`和`usage.total_tokens`；按index恢复输入顺序，数量或维度不一致返回`MODEL_OUTPUT_SCHEMA_INVALID`。P0只使用1024维稠密向量。

### API-MDL-03 竞品重排序

- 优先级：Internal；路径/方法：`POST /internal/v1/model-router/rerank`；权限：service
- Header：同API-MDL-01；Path/Query：无
- Body：`task_uuid/stage_run_id/query/candidates/input_hash/top_k`必选；candidate只含获授权的必要字段。
- 成功 200：返回 `model_run_id/model_id/results[{candidate_id,rerank_score,rank}]/status/latency_ms`。
- 失败：422 `RERANK_INPUT_INVALID`; 502 `MODEL_UPSTREAM_ERROR`; 504 `MODEL_UPSTREAM_TIMEOUT`。

阿里云上游契约锁定：P0使用`qwen3-rerank`，`POST {ALIYUN_MODEL_ROUTER_RERANK_BASE_URL}/reranks`，Base URL形如工作空间专属`/compatible-api/v1`；请求为`model/query/documents/top_n/instruct`，documents由candidates的必要文本按原顺序生成，最多500项，top_n不得超过候选数。响应读取顶层`results[].index/relevance_score`、`model/id/usage.total_tokens`；`relevance_score`范围0—1，仅在本次请求内比较。适配器按index映射回candidate_id并生成从1开始的rank，索引越界、重复或分数非法返回`MODEL_OUTPUT_SCHEMA_INVALID`。不再使用已公告停服的旧gte-rerank系列作为P0默认模型。

上游协议依据：阿里云官方[OpenAI兼容Embedding接口](https://help.aliyun.com/en/model-studio/embedding-interfaces-compatible-with-openai)与[Text Rerank API](https://help.aliyun.com/en/model-studio/text-rerank-api)，接入测试时仍需使用赛事实际WorkspaceId、区域和可用模型清单。

## 11. 统一错误码

| 模块 | 错误码 |
|---|---|
| 认证 | `AUTH_INVALID_CREDENTIALS`、`AUTH_TOKEN_INVALID`、`AUTH_REFRESH_TOKEN_INVALID`、`AUTH_REFRESH_TOKEN_EXPIRED`、`AUTH_TOKEN_REUSE_DETECTED`、`AUTH_RATE_LIMITED` |
| 权限/租户 | `ADMIN_REQUIRED`、`CONFIRMATION_USER_REQUIRED`、`PERMISSION_DENIED`、`TENANT_CONTEXT_MISMATCH`、`TENANT_SUSPENDED` |
| 通用 | `RESOURCE_NOT_FOUND`、`RESOURCE_VERSION_CONFLICT`、`IDEMPOTENCY_IN_PROGRESS`、`IDEMPOTENCY_CONFLICT`、`PAGINATION_INVALID` |
| 产品/解析 | `PRODUCT_NOT_FOUND`、`PRODUCT_SKU_CONFLICT`、`PRODUCT_PROFILE_NOT_CONFIRMED`、`PRODUCT_PROFILE_IMMUTABLE`、`PARSE_JOB_NOT_FOUND`、`FILE_TYPE_UNSUPPORTED` |
| 数据集 | `DATASET_NOT_FOUND`、`DATASET_NOT_READY`、`DATASET_AUTHORIZATION_REQUIRED`、`FIELD_MAPPING_INVALID` |
| 任务 | `TASK_NOT_FOUND`、`TASK_ALREADY_STARTED`、`TASK_PREFLIGHT_FAILED`、`TASK_SCOPE_MISMATCH`、`TASK_RESULT_NOT_READY`、`WORKFLOW_STATE_INCONSISTENT` |
| 确认/恢复 | `CONFIRMATION_NOT_FOUND`、`CONFIRMATION_ALREADY_RESPONDED`、`CONFIRMATION_EXPIRED`、`CONFIRMATION_OPTION_INVALID`、`CONFIRMATION_INPUT_INVALID`、`WORKFLOW_CHECKPOINT_CONFLICT`、`WORKFLOW_RESUME_UNAVAILABLE` |
| 洞察/证据 | `COMPETITOR_RESULT_NOT_READY`、`REVIEW_ANALYSIS_NOT_READY`、`INSIGHT_RESULT_NOT_READY`、`OPPORTUNITY_RESULT_NOT_READY`、`RECOMMENDATION_RESULT_NOT_READY`、`EVIDENCE_NOT_FOUND`、`EVIDENCE_AUDIT_INCOMPLETE` |
| 报告 | `REPORT_NOT_FOUND`、`REPORT_NOT_READY`、`REPORT_EVIDENCE_AUDIT_FAILED` |
| 配置/模型 | `MODEL_ROUTE_CONFIG_INVALID`、`PROMPT_SCHEMA_INVALID`、`COMPUTE_QUOTA_EXCEEDED`、`MODEL_OUTPUT_SCHEMA_INVALID`、`MODEL_UPSTREAM_TIMEOUT` |

## 12. 安全、幂等、性能与审计

1. 所有业务资源按认证上下文注入 tenant scope；跨租户统一返回404或403，禁止通过时差枚举资源。
2. admin诊断遵循最小披露并写 `audit_logs`；admin不能通过诊断接口读取完整评论正文或代替user确认。
3. 需要`Idempotency-Key`的业务写接口统一使用`api_idempotency_records`。规范化请求排除Authorization、X-Request-ID及multipart文件字节，文件以有序sha256清单参与SHA-256。tenant+route_code+key唯一：同hash且processing返回409 `IDEMPOTENCY_IN_PROGRESS`；同hash且completed/failed重放原HTTP状态和脱敏Envelope；不同hash返回409 `IDEMPOTENCY_CONFLICT`。默认保留24小时。API-AUTH-01/02禁止写入该表。API-PRD-05和API-INS-01仍保留领域幂等键，必须与通用记录在同一业务事务完成；API-CFM-02的Header键映射通用记录，confirmation/outbox键继续保证恢复语义幂等。
4. 产品画像、数据范围、算法、本体、Prompt和模型路由版本在启动任务时冻结。
5. 原始评论与翻译分列；证据Span以原文为准；报告结论必须可回溯证据、模型运行与数据范围快照。
6. 普通查询P95≤500ms；任务状态查询P95≤300ms；文件导入受理≤2s；外部模型不占用同步HTTP长连接。
7. Supervisor自动处理重试、降级、并行部分失败与安全恢复；user只回答业务确认或查看结果。

## 13. V2→V3 废弃与兼容映射

| V2接口/语义 | V3处理 | 兼容策略 |
|---|---|---|
| API-CMP-05 单商品人工复核 | 废弃 | 返回410 `API_DEPRECATED`；竞品为AI结果下钻，重大歧义走API-CFM-02 |
| API-CMP-07 `competitors:confirm` | 合并到API-CFM-02 | 过渡期可307到对应confirmation URL；不得继续写旧确认表 |
| API-INS-06 专家复核建议 | 合并到API-CFM-02 | 高风险建议生成统一confirmation；旧路径410 |
| API-REV-04 人工校正观点 | 移出核心 | 不提供岗位校正入口；证据/抽取异常由Supervisor和admin诊断处理 |
| API-INS-07 user手动阶段重试 | 改为API-ADM-08 | user路径410；Supervisor优先自动恢复 |
| API-INS-08 user取消任务 | 改为admin safe_stop | 旧路径过渡期403并提示admin诊断；不作为核心业务动作 |
| API-LST-01—03 | 移出核心 | 410；非核心扩展另行版本化，不复用V3核心实体 |
| API-RPT-02 发布报告 | 删除 | 在线报告完成即作为当前结果版本，无多人审批 |
| API-RPT-03—05 导出 | 移出核心 | 410；V3只保留API-RPT-01在线报告 |
| API-CMP-04 数据集商品列表 | 收敛 | 不作为核心接口；任务竞品下钻使用API-CMP-01 |
| API-INS-03 `current_stage` | 改为`stage` | 兼容期可只读别名并附Deprecation Header；值必须映射五阶段 |
| API-INS-03 `checkpoint_stage/retryable` | 保留但改语义 | 仅表示Supervisor安全恢复能力，不驱动user运维按钮 |
| V2岗位Token/权限矩阵 | 禁止 | 旧岗位登录迁移为user；非user/admin Token拒绝并要求重新签发 |

兼容响应 Header：`Deprecation: true`、`Sunset: <date>`、`Link: <V3文档>; rel="successor-version"`。比赛Demo不实现旧写路径双写。

## 14. P0联调闭环

1. API-AUTH-01/03进入系统。
2. API-PRD-01/05/06/07完成产品及可信画像。
3. API-DAT-01/02/04完成授权市场数据与质量验证。
4. API-INS-01/02创建并启动超级AI员工任务。
5. API-INS-03只展示五阶段；出现确认时用API-CFM-01/02回答并从Checkpoint恢复。
6. 完成后通过API-INS-04一次取得综合结果和下钻入口。
7. API-CMP/REV/EVD/OPP/REC解释细节，API-RPT-01呈现在线决策报告。

## 15. 后续跨文档依赖

1. 页面交互原型V3需按本接口重写为6个user页面+1个admin页面，并删除竞品/专家独立审批页、Listing和导出页。
2. Mermaid图V3需使用统一确认、五阶段、综合结果接口及admin诊断边界。
3. 测试用例V2需覆盖所有P0接口、user/admin边界、租户隔离、幂等、Checkpoint冲突、重复确认和部分失败披露。
4. FastAPI Schema/ORM需与数据字典V3、PostgreSQL V3逐字段对齐；响应聚合字段不得私自改名。
5. LangGraph适配层需实现事务Outbox消费和 `Command(resume=...)`，并验证官方Checkpointer与业务投影一致。
6. 部署文档需定义Model Router环境变量、服务身份、限流、审计脱敏和旧接口Sunset日期。

## 16. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V2.0 | 2026-08-08 | 多模块、多复核点与扩展接口版本 |
| V3.0 | 2026-08-09 | 按超级AI员工简化基线重构为user/admin、五阶段、统一确认和综合结果聚合接口 |
| V3.1 | 2026-08-10 | 锁定Refresh Token轮换、updated_at强ETag及确认事务Outbox/服务端Checkpoint恢复语义 |
| V3.2 | 2026-08-10 | 统一字段与枚举，新增持久化HTTP幂等契约，锁定RS256认证及阿里云Embedding/qwen3-rerank正式协议 |
| V3.3 | 2026-09-29 | 增加五项市场智能聚合、定价试算与官方政策源安装接口 |

### 16.1 决赛市场智能增量接口

| 编号 | 方法与路径 | 说明 |
|---|---|---|
| API-MKT-01 | `GET /api/v1/market-intelligence/overview` | 按可选 `dataset_id`、`product_id` 聚合智能选品、竞品追踪、评论深挖、定价与合规预警 |
| API-MKT-02 | `POST /api/v1/market-intelligence/pricing` | 输入数据集、可选结构化可比竞品组、单位成本和目标毛利，返回组内价格带、建议售价、促销底线、单位利润、实际毛利、主导约束、场景方案、弹性状态与置信度 |
| API-MKT-03 | `GET /api/v1/market-intelligence/opportunities` | 按 `dataset_id/page/page_size` 分页查询候选机会方向；每条是需求主题形成的验证方向，不是商品或 SKU |
| API-MKT-04 | `GET /api/v1/market-intelligence/opportunities/{opportunity_id}` | 查询机会评分、企业适配、证据主题和处理状态详情 |
| API-MKT-05 | `GET /api/v1/market-intelligence/competitor-alerts` | 按 `dataset_id/page/page_size` 分页查询竞品价格、标题、图片、促销和上新动态 |
| API-MKT-06 | `GET /api/v1/market-intelligence/competitor-alerts/{alert_id}` | 查询竞品变更前后值、监控范围和最近快照 |
| API-MKT-07 | `GET /api/v1/market-intelligence/review-clusters` | 按 `dataset_id/page/page_size` 分页查询评论需求主题 |
| API-MKT-08 | `GET /api/v1/market-intelligence/review-clusters/{cluster_id}` | 查询主题情绪分布及最多 30 条关联原文证据 |
| API-POL-01 | `POST /api/v1/market-signals/policy-sources:install-defaults` | 幂等安装 CPSC 与 Federal Register 官方政策源 |

上述接口继续由服务端注入 `tenant_id`。三个概览列表均使用稳定排序并通过 URL 保留
`page/page_size` 和详情返回位置；API-MKT-01 只返回轻量预览及总数。没有成本时
API-MKT-02 默认选择样本最多且不少于3个价格的
`market_listings.normalized_attributes.category.value`可比组，禁止把不同结构化商品类目
混入同一价格分布；请求可通过`comparator_group`切换组。没有成本时必须返回
`market_anchor_only`，没有足够历史变化样本时`elasticity_status`必须为
`insufficient_history`。API-POL-01 只安装官方公开 URL，不写入预制政策事件。

## 17. 本次变更摘要

- 登录身份收敛为user/admin，删除旧岗位Token、岗位分配和多人审批语义；v3.27租户内权限映射只控制业务操作，不改变身份枚举。
- 新增统一确认查询与提交接口，明确Checkpoint、Outbox和`Command(resume=...)`恢复事务边界。
- API-INS-03只投影五阶段，同时保留脱敏stage_runs供受控诊断。
- 新增API-INS-04综合结果入口，把竞品、评论、机会、建议和证据接口降为下钻能力。
- 将手工阶段重试和安全停止收口到admin诊断；user不操作内部节点。
- 从核心文档删除Listing、报告文件导出、验证任务、多人评审与发布审批接口。
- 提供V2→V3逐项废弃、替代和兼容映射。

## 18. 工作台上下文生命周期增量（V3.4）

### 18.1 原子 Turn

| 方法与路径 | 说明 |
|---|---|
| `POST /api/v1/analysis-workspaces/{workspace_uuid}/turns` | 非流式提交完整Turn |
| `POST /api/v1/analysis-workspaces/{workspace_uuid}/turns:stream` | 标准SSE提交；必须传`Idempotency-Key` |
| `GET /api/v1/analysis-workspaces/{workspace_uuid}/turns` | 分页查询Turn |
| `GET /api/v1/analysis-workspaces/{workspace_uuid}/turns/{turn_uuid}` | 查询完整Turn |
| `POST /api/v1/analysis-workspaces/{workspace_uuid}/turns/{turn_uuid}:regenerate` | 保留原回答并创建关联新Turn |
| `POST /api/v1/analysis-workspaces/{workspace_uuid}/turns/{turn_uuid}:cancel` | 仅取消未完成Turn |

请求字段为`client_turn_id/question/product_id/dataset_id/task_uuid`；兼容字段
`memory_mode`仍接受`policy/temporary/workspace/user`，但当前前端不再发送。工作台问询与任务问询只由可选
`task_uuid`区分。完成响应固定包含`turn_uuid`、两条消息UUID、`answer`、`citations`、
`memory_candidates`、`context_sources`、`tool_results`、`suggested_actions`、`context_snapshot_uuid`、
`resolved_state`、`context_conflicts`和`resolution_log`。SSE事件只允许：

```text
turn_started -> progress -> answer_delta -> citation
             -> tool_result -> memory_candidate -> action -> done
error
```

`answer_delta`只在完成事务提交成功后释放；`progress`是可展示执行摘要，不是私有思维链。
每个事件带稳定`id`。客户端最多自动重连一次，复用原`Idempotency-Key`并按事件ID去重；
服务端对已完成Turn重放持久化结果，不重新调用模型。同一工作台已有`pending` Turn时返回
409 `WORKSPACE_TURN_IN_PROGRESS`。旧工作台和任务聊天路由仅保留隐藏兼容，不进入
OpenAPI，也不得维护独立上下文逻辑。

### 18.2 客户记忆

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/customer-memories` | 按状态、作用域、工作台、类型分页 |
| `GET/PATCH/DELETE /api/v1/customer-memories/{memory_uuid}` | 读取、修改候选或归档指定版本 |
| `POST /api/v1/customer-memories/{memory_uuid}:confirm` | 确认新版本并将旧确认版本标为`superseded` |
| `POST /api/v1/customer-memories/{memory_uuid}:reject` | 将候选归档 |
| `GET /api/v1/customer-memories/{memory_uuid}/history` | 查询同类型版本链 |
| `POST /api/v1/customer-memories:batch-confirm` | 批量确认UUID |
| `GET/PUT /api/v1/customer-memory-policy` | 查询或更新提取、确认、作用域、保留期和允许类型 |

状态为`candidate/confirmed/superseded/archived/invalidated`，作用域为
`workspace/user/tenant`，敏感级别为`internal/confidential/restricted`。资源包含
`effective_at/expires_at/source_message_uuid/supersedes_memory_uuid/confirmed_by`；
依赖画像的记忆在来源版本变化后记录失效原因。`restricted`记忆不得进入模型上下文。
自然语言“忘记”只返回`forget_memory`确认动作；客户端确认后才调用DELETE。所有操作
以`memory_uuid`寻址，禁止按`memory_key`覆盖或模糊删除。当前版本通过
`MEMORY_AUTO_EXTRACT_ENABLED=false`关闭所有新记忆自动抽取入口；策略接口固定返回并
保存`auto_extract=false`，已有记忆的读取、确认、归档和历史查询保持可用。

### 18.3 Context 与 Citation

| 方法与路径 | 说明 |
|---|---|
| `GET/PUT /api/v1/analysis-workspaces/{workspace_uuid}/context` | 读取或创建上下文配置版本 |
| `POST /api/v1/analysis-workspaces/{workspace_uuid}/context:preview` | 返回来源、估算Token和裁剪来源 |
| `GET /api/v1/analysis-workspaces/{workspace_uuid}/state` | 读取服务端维护的多轮语义状态 |
| `GET /api/v1/citations/{citation_uuid}` | 解析来源类型、版本、定位器、摘录和分数 |
| `GET /api/v1/analysis-workspaces/{workspace_uuid}/messages` | 支持`before_seq/after_seq/limit/message_kind/task_uuid` |

Context显式绑定`product_id/dataset_ids/knowledge_base_uuids/memory_scope/task_uuids`
和`retrieval_policy`。Preview同时返回`resolved_state/conflicts/resolution_log`。
服务端状态显式记录当前产品、市场、比较市场、数据集、任务、分析阶段、待确认项、
上一意图和指代解析。每轮只能引用实际装配并冻结到快照的来源。

### 18.4 企业文档知识库

知识库提供`POST/GET/PATCH/DELETE /api/v1/knowledge-bases`及详情接口；文档提供上传、
列表、详情、状态、删除和`:reindex`。同一文档的版本接口为：

| 方法与路径 | 说明 |
|---|---|
| `POST /api/v1/knowledge-bases/{kb}/documents/{document}/versions` | 上传同文件类型的新版本并异步索引 |
| `GET /api/v1/knowledge-bases/{kb}/documents/{document}/versions` | 分页查询SHA、大小、索引状态和当前版本标记 |
| `GET /api/v1/knowledge-bases/{kb}/documents/{document}/versions/{version}/preview` | 预览已提取文本和页/工作表信息 |
| `POST /api/v1/knowledge-bases/{kb}/documents/{document}/versions/{version}:restore` | 克隆历史内容为最新版本并重新索引，不改写旧版本 |

上传支持PDF、XLSX、JPG/JPEG和PNG，返回
`document_uuid/version/sha256/index_job_uuid/status`。索引状态按
`uploaded -> parsing -> chunking -> embedding -> ready|failed`推进。

`POST /api/v1/knowledge-bases:search`接收查询、知识库UUID、可选工作台、`top_k`和
文档类型过滤，返回带`citation_uuid/document_uuid/page/chunk_text/score/
document_version`的匹配项。缺少Embedding或Rerank模型配置时明确失败，不生成伪向量。
知识库可见性为`tenant/user`；文档内容按不可信数据处理，不能覆盖系统规则。所有绑定、
检索、重索引、删除和Citation解析均校验租户及用户可见性。

### 18.5 权限、审计与删除治理

`user/admin`仍是登录身份。认证后的企业成员操作再按v3.27权限目录校验；内置
`tenant_owner/data_admin/analyst/operator/auditor/viewer`不是JWT角色枚举。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/v1/admin/tenant-roles` | 查询当前企业角色及权限 |
| `GET /api/v1/admin/tenant-members` | 查询企业成员角色 |
| `PUT /api/v1/admin/tenant-members/{user_id}/roles` | 替换成员角色；禁止移除最后一个tenant_owner |
| `GET /api/v1/admin/audit-events` | 查询脱敏审计事件 |
| `GET /api/v1/admin/audit-chain:verify` | 验证租户审计哈希链 |
| `GET /api/v1/admin/tenant-deletions` | 查询保留期内、受法务保留阻断或已完成的租户删除请求 |
| `POST /api/v1/admin/enterprise-users/{tenant_id}/legal-holds` | 设置法务保留 |
| `POST /api/v1/admin/enterprise-users/{tenant_id}/legal-holds/{hold_uuid}:release` | 释放法务保留 |

删除企业先关闭租户、禁用账号并撤销会话，再按`data_retention_days`创建异步删除请求；
保留期内恢复租户会取消未执行请求。到期物理清理由独立Migrator/Superuser工具执行，
必须二次确认租户编码；法务保留会阻断执行，完成后生成不可变删除证明。审计日志保留
脱敏事件和哈希链，不保留被删除业务正文。

### 18.6 版本记录补充

| 版本 | 日期 | 说明 |
|---|---|---|
| V3.4 | 2026-10-06 | 统一Turn、版本化记忆、Context快照、Citation及企业知识库资源 |
| V3.5 | 2026-10-06 | 增加租户内RBAC、append-only审计链及带保留期/法务保留的租户删除生命周期 |
| V3.6 | 2026-10-06 | 增加受控智能问数、服务端工具编排、RAG阈值拒答和`tool_result`事件 |
| V3.7 | 2026-10-07 | 增加知识文档版本上传、预览与回溯接口；全局关闭自动记忆抽取 |

## 19. 受控智能问数与工具编排（V3.6）

### 19.1 数据查询资源

| 方法与路径 | 权限 | 说明 |
|---|---|---|
| `GET /api/v1/data-queries/metrics` | `dataset.read` | 返回当前启用的受控指标目录 |
| `POST /api/v1/data-queries:execute` | `dataset.read` | 执行结构化`DataQueryPlan`并持久化审计结果 |
| `GET /api/v1/data-queries/{query_uuid}` | `dataset.read` | 按租户读取已执行查询、数据版本和结果SHA |

`DataQueryPlan`只允许`metrics/grain/group_by/filters/data_version_uuid/limit`。指标为：

```text
sales_units, average_daily_sales, sales_revenue, average_selling_price,
active_days, sku_count, site_count,
inventory_units, average_inventory, stockout_days
```

`grain`只允许`total/daily/weekly/monthly`，`group_by`只允许`sku/site`，`limit`最大500。
请求不能携带SQL、表名、列名或表达式，不能混合销量和库存指标。服务端只执行固定、
参数化SQL模板；响应固定披露`query_uuid/source_version/resolved_filters/result_sha256/
duration_ms/limitations`。

### 19.2 Turn 内工具结果

Turn在模型调用前执行服务端Intent Router。确定性问数不调用LLM；RAG只有达到相关性阈值
才把证据交给模型；预测请求只返回预测入口；工作流请求只返回`run_workflow`动作。

`tool_results[]`结构为：

```json
{
  "tool": "data_query",
  "status": "succeeded",
  "data": {
    "query_uuid": "uuid",
    "source_version": {"version_uuid": "uuid", "canonical_sha256": "sha256"},
    "resolved_filters": {},
    "rows": [],
    "result_sha256": "sha256"
  }
}
```

`tool`只允许`data_query/rag/workflow/forecast`；`status`只允许
`succeeded/rejected/needs_input`。流式和完成Turn重放都必须返回相同`tool_result`，
不能因断线重新执行查询。

### 19.3 知识检索拒答契约

`POST /api/v1/knowledge-bases:search`响应增加`relevance_threshold`和`refused`。
没有可访问知识库、没有匹配或最高分低于阈值时，`refused=true`，Agent必须明确说明
证据不足。知识读取/搜索要求`knowledge.read`，创建、上传、修改、删除和重索引要求
`knowledge.write`。

### 19.4 NL2SQL边界

当前版本不接受自由SQL或模型生成SQL。未来NL2SQL接口不得复用
`/api/v1/data-queries:execute`绕过`DataQueryPlan`；须另行完成只读AST、表列白名单、
RLS、timeout、LIMIT、成本控制、审计和对抗测试后再登记新版本。

## 20. 产品主档与批量导入增量（V3.7）

### 20.1 产品主档

| ID | 方法与路径 | 权限 | 说明 |
|---|---|---|---|
| API-PRD-09 | `DELETE /api/v1/products/{product_id}` | `product.write` | 使用`If-Match`归档产品 |
| API-PRD-10 | `GET /api/v1/products/{product_id}/relations` | 登录企业用户 | 查询SPU、变体、套装、BOM关系 |
| API-PRD-11 | `PUT /api/v1/products/{product_id}/relations` | `product.write` | 全量替换关系；同一事务删除孤立组 |
| API-PRD-12 | `GET /api/v1/products/{product_id}/inventory-summary` | 登录企业用户 | 返回非实时库存摘要、站点快照、来源版本和导入状态 |

API-PRD-01/04 的`category_code`扩为
`sofa/chair/table/bed/storage/other`，并支持`lifecycle_status`。API-PRD-04 可修改
`sku/name/category_code/lifecycle_status/description`；仍要求`If-Match`。响应中的空
`description`表示显式清空，不与“未提交该字段”混淆。

### 20.2 产品批量导入

| ID | 方法与路径 | 说明 |
|---|---|---|
| API-PRD-IMP-01 | `GET /api/v1/product-imports/template` | 下载动态五工作表模板；Header返回版本、文件SHA、Schema SHA |
| API-PRD-IMP-02 | `GET /api/v1/product-imports/recent` | 查询当前租户最近任务，用于恢复 |
| API-PRD-IMP-03 | `POST /api/v1/product-imports:preflight` | multipart上传XLSX并预检；要求`Idempotency-Key` |
| API-PRD-IMP-04 | `GET /api/v1/product-imports/{job_uuid}` | 查询任务和预览行 |
| API-PRD-IMP-05 | `PATCH /api/v1/product-imports/{job_uuid}/rows/{source_row_number}` | 修正标准化行并重算整任务规则与SHA |
| API-PRD-IMP-06 | `POST /api/v1/product-imports/{job_uuid}:commit` | 携带`preview_sha256`单事务提交 |
| API-PRD-IMP-07 | `POST /api/v1/product-imports/{job_uuid}:cancel` | 取消未完成任务 |
| API-PRD-IMP-08 | `GET /api/v1/product-imports/{job_uuid}/error-report` | 下载含原行号、错误码、详情和建议的XLSX |

预检 multipart 字段为`file/sheet_name/header_row/field_mapping/unit_mapping/
dictionary_mapping/import_mode`；三个 mapping 使用 JSON 对象字符串。`import_mode`默认
`create_only`，批量更新必须显式使用`upsert`。相同租户、幂等键、来源SHA和配置重放
原任务；键相同但文件或配置不同返回`IDEMPOTENCY_CONFLICT`。

提交只接受`status=ready`且 SHA 未变化的任务。任一行、别名、画像或关系写入失败时整个
事务回滚；已完成任务重复提交返回原统计。跨租户任务统一按不存在返回 404。

新增错误码包括`PRODUCT_IMPORT_FILE_TYPE/PRODUCT_IMPORT_MIME_TYPE/
PRODUCT_IMPORT_FILE_SIZE/PRODUCT_IMPORT_WORKBOOK_INVALID/PRODUCT_IMPORT_SHEET_COUNT/
PRODUCT_IMPORT_SHEET_NOT_FOUND/PRODUCT_IMPORT_ROW_LIMIT/PRODUCT_IMPORT_MAPPING_INVALID/
PRODUCT_IMPORT_BLOCKED/PRODUCT_IMPORT_PREVIEW_STALE/PRODUCT_IMPORT_IMMUTABLE`。

### 20.3 进度与恢复边界

前端上传使用 XHR 展示字节级进度；服务端预检完成后持久化任务、行、计数和错误，页面可
通过`recent`恢复。当前解析在 API-PRD-IMP-03 请求内同步执行，不提供虚假的后台解析
百分比；网络在任务持久化前中断时，客户端以同一文件和幂等键重传。
