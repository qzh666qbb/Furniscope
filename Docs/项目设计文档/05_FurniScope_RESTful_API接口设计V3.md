# FurniScope RESTful API 接口设计 V3.2

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
- 成功 200：分页 items，每项返回 `product_id/sku/name/category_code/analysis_status/current_profile_version_id/updated_at`。
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

## 17. 本次变更摘要

- 权限收敛为user/admin，删除动态RBAC、岗位分配和多人审批语义。
- 新增统一确认查询与提交接口，明确Checkpoint、Outbox和`Command(resume=...)`恢复事务边界。
- API-INS-03只投影五阶段，同时保留脱敏stage_runs供受控诊断。
- 新增API-INS-04综合结果入口，把竞品、评论、机会、建议和证据接口降为下钻能力。
- 将手工阶段重试和安全停止收口到admin诊断；user不操作内部节点。
- 从核心文档删除Listing、报告文件导出、验证任务、多人评审与发布审批接口。
- 提供V2→V3逐项废弃、替代和兼容映射。
