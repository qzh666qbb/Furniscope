# FurniScope RESTful API 接口设计 V2.0

## 1. 文档说明

| 项目 | 内容 |
|---|---|
| 产品 | FurniScope——家具出海产品机会雷达 |
| 适用范围 | 黑客松 Demo、复赛技术附件、前后端联调 |
| API 风格 | RESTful JSON API |
| 基础路径 | `/api/v1` |
| 字符编码 | UTF-8 |
| 时间格式 | ISO 8601，统一返回 UTC，例如 `2026-08-08T08:30:00.000Z` |
| 金额格式 | 字符串形式十进制金额，并显式携带 ISO 4217 币种 |
| 身份认证 | Bearer JWT；Model Router 内部接口使用服务身份凭证 |
| 主要依赖 | 阿里云百炼 Model Router、大模型、Embedding/Rerank 服务、对象存储 |
| 工作流依赖 | LangGraph PostgreSQL Checkpointer、异步任务队列、PostgreSQL `analysis_tasks/task_stage_runs` |
| 修订范围 | 新增 4 个 P0、3 个 P1 接口，并扩展 API-INS-03 工作流状态响应 |

### 1.1 接口分层

- **前端业务 API：**供 FurniScope Web Demo 调用，路径位于 `/api/v1/*`。
- **内部封装 API：**仅供后端工作流和异步任务调用，路径位于 `/internal/v1/*`，不暴露到公网。
- **外部大模型 API：**由 Model Router 适配器调用阿里云百炼；前端和业务服务不得直接持有百炼 API Key。

### 1.2 接口优先级

- **P0：**黑客松 Demo 主链路必须实现。
- **P1：**已纳入契约、建议后续迭代实现；Demo 未实现时前端必须隐藏或禁用入口，不得模拟成功。

### 1.3 通用请求头

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `Authorization` | string | 是（登录除外） | `Bearer <access_token>` |
| `Content-Type` | string | 写请求是 | `application/json`；上传文件时为 `multipart/form-data` |
| `X-Tenant-Id` | integer | 是 | 当前企业租户 ID，服务端必须与 Token 权限交叉校验 |
| `X-Request-Id` | string | 否 | 调用链 ID；未传时由网关生成 |
| `Idempotency-Key` | string | 创建任务类接口是 | 1—128 字符，同一租户内 24 小时唯一 |
| `Accept-Language` | string | 否 | 错误消息语言，默认 `zh-CN` |

### 1.4 通用响应结构

成功响应：

```json
{
  "code": "0",
  "message": "success",
  "data": {},
  "request_id": "req_01J4YQ8M2K",
  "timestamp": "2026-08-08T08:30:00.000Z"
}
```

失败响应：

```json
{
  "code": "PRODUCT_PROFILE_NOT_CONFIRMED",
  "message": "产品画像尚未确认，不能启动分析",
  "details": [{"field": "profile_status", "reason": "expected confirmed"}],
  "request_id": "req_01J4YQ8M2K",
  "timestamp": "2026-08-08T08:30:00.000Z"
}
```

### 1.5 HTTP 状态码和分页

| HTTP 状态 | 使用场景 |
|---|---|
| `200` | 查询、更新、业务动作成功 |
| `201` | 资源创建成功 |
| `202` | 异步任务已受理 |
| `204` | 删除成功且无响应体 |
| `400` | 参数格式或业务前置条件错误 |
| `401/403` | 未认证/无权限 |
| `404/409` | 资源不存在/状态冲突或幂等冲突 |
| `422` | 文件或结构化数据校验失败 |
| `429` | 请求或模型调用限流 |
| `500/502/503` | 系统异常/上游异常/暂不可用 |

列表接口统一使用 `page`、`page_size`，默认 `1`、`20`，`page_size` 最大 `100`；返回 `items`、`page`、`page_size`、`total`。

---

## 2. 用户模块

### API-USR-01 用户登录

- **路径/方式：**`POST /api/v1/auth/login`
- **说明：**校验租户、邮箱和密码，签发访问令牌及刷新令牌。
- **请求头：**`Content-Type: application/json`；无需 `Authorization` 和 `X-Tenant-Id`。
- **路径参数：**无。
- **Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `tenant_code` | string | 是 | 企业租户编码 |
| `email` | string | 是 | 登录邮箱，转小写后校验 |
| `password` | string | 是 | 8—64 字符，不写入日志 |

```json
{"tenant_code":"DEMO_FURNITURE","email":"pm@example.com","password":"Demo@2026"}
```

- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"access_token":"eyJ...","refresh_token":"eyJ...","expires_in":7200,"user":{"id":12,"user_name":"李明","role_code":"product_manager","tenant_id":1}},"request_id":"req_001","timestamp":"2026-08-08T08:30:00.000Z"}
```

- **失败响应 `401`：**

```json
{"code":"AUTH_INVALID_CREDENTIALS","message":"租户、邮箱或密码错误","details":[],"request_id":"req_001","timestamp":"2026-08-08T08:30:00.000Z"}
```

- **业务错误码：**`AUTH_INVALID_CREDENTIALS` 凭证错误；`AUTH_USER_DISABLED` 用户停用；`TENANT_DISABLED` 租户停用；`AUTH_TOO_MANY_ATTEMPTS` 登录尝试过多。

### API-USR-02 刷新访问令牌

- **路径/方式：**`POST /api/v1/auth/refresh`
- **说明：**使用一次性轮换刷新令牌换取新令牌。
- **请求头：**`Content-Type: application/json`。
- **路径参数/Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `refresh_token` | string | 是 | 尚未过期且未被撤销的刷新令牌 |

- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"access_token":"eyJ.new","refresh_token":"eyJ.rotate","expires_in":7200},"request_id":"req_002","timestamp":"2026-08-08T08:31:00.000Z"}
```

- **失败响应 `401`：**

```json
{"code":"AUTH_REFRESH_TOKEN_INVALID","message":"刷新令牌无效或已过期","details":[],"request_id":"req_002","timestamp":"2026-08-08T08:31:00.000Z"}
```

- **业务错误码：**`AUTH_REFRESH_TOKEN_INVALID` 令牌无效；`AUTH_SESSION_REVOKED` 会话已撤销。

### API-USR-03 获取当前用户

- **路径/方式：**`GET /api/v1/users/me`
- **说明：**返回当前用户、租户和权限集合。
- **请求头：**通用请求头。
- **路径参数/Query 参数/Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"id":12,"tenant_id":1,"user_name":"李明","email":"pm@example.com","role_code":"product_manager","permissions":["product:write","analysis:create","report:read"]},"request_id":"req_003","timestamp":"2026-08-08T08:32:00.000Z"}
```

- **失败响应 `401`：**

```json
{"code":"AUTH_TOKEN_EXPIRED","message":"访问令牌已过期","details":[],"request_id":"req_003","timestamp":"2026-08-08T08:32:00.000Z"}
```

- **业务错误码：**`AUTH_TOKEN_EXPIRED` Token 过期；`AUTH_TOKEN_INVALID` Token 无效；`TENANT_CONTEXT_MISMATCH` 租户头与 Token 不一致。

---

## 3. 工作台模块

### API-DSH-01 获取工作台聚合摘要

- **优先级：**P0。
- **路径/方式：**`GET /api/v1/dashboard/summary`
- **说明：**按当前租户和用户权限聚合产品、分析任务、人工待办及最近报告，减少工作台多接口瀑布请求。敏感成本不在本接口返回。
- **请求头：**通用请求头；必须包含 `Authorization` 和 `X-Tenant-Id`。
- **路径参数/Body：**无。
- **Query 参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `date_range` | string | 否 | `7d/30d/90d`，默认 `30d` |
| `task_status` | array<string> | 否 | 可重复 Query；任务状态过滤 |
| `product_keyword` | string | 否 | SKU 或产品名，最长 100 字符 |
| `recent_limit` | integer | 否 | 最近任务和报告数量，默认 5，范围 1—20 |

- **成功响应 `200`：**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "metrics": {
      "active_product_count": 18,
      "running_task_count": 2,
      "waiting_human_count": 1,
      "published_report_count": 6
    },
    "recent_tasks": [
      {
        "task_uuid": "e8af1ca0-14e4-4d20-8d88-b2f8bd08d001",
        "task_name": "SF-MOD-001 美国站机会分析",
        "product_id": 101,
        "product_name": "三座模块化布艺沙发",
        "status": "waiting_human",
        "current_stage": "competitor_review",
        "progress_percent": 36.0,
        "updated_at": "2026-08-08T12:02:00.000Z"
      }
    ],
    "human_todos": [
      {
        "todo_type": "competitor_review",
        "task_uuid": "e8af1ca0-14e4-4d20-8d88-b2f8bd08d001",
        "title": "确认竞品集合",
        "target_path": "/analysis-tasks/e8af1ca0-14e4-4d20-8d88-b2f8bd08d001/competitors"
      }
    ],
    "recent_reports": [
      {
        "report_uuid": "b4fb642e-cd45-49fd-97f1-38adfa3d0001",
        "title": "SF-MOD-001 美国市场机会报告",
        "status": "published",
        "overall_opportunity_score": 78.6,
        "confidence": 0.81
      }
    ]
  },
  "request_id": "req_dsh_001",
  "timestamp": "2026-08-08T08:40:00.000Z"
}
```

- **失败响应 `400`：**

```json
{"code":"DASHBOARD_FILTER_INVALID","message":"工作台筛选参数无效","details":[{"field":"date_range","reason":"must be 7d, 30d or 90d"}],"request_id":"req_dsh_001","timestamp":"2026-08-08T08:40:00.000Z"}
```

- **业务错误码：**`DASHBOARD_FILTER_INVALID` 筛选参数非法；`DASHBOARD_QUERY_FAILED` 聚合查询失败；`TENANT_CONTEXT_MISMATCH` 租户上下文不匹配；`PERMISSION_DENIED` 无工作台访问权限。

---

## 4. 产品管理模块

### API-PRD-01 创建产品

- **路径/方式：**`POST /api/v1/products`
- **说明：**创建企业 SKU 主档；敏感价格字段按权限脱敏。
- **请求头：**通用请求头，必须包含 `Idempotency-Key`。
- **路径参数/Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `sku_code` | string | 是 | 租户内唯一，1—64 字符 |
| `product_name` | string | 是 | 产品名称，1—200 字符 |
| `category_code` | string | 是 | V1 建议 `sofa` |
| `description` | string | 否 | 产品说明 |
| `factory_price` | string | 否 | 出厂价，十进制字符串 |
| `currency` | string | 条件必选 | 传价格时必传，如 `USD` |
| `moq` | integer | 否 | 最小起订量，正整数 |
| `lead_time_days` | integer | 否 | 交期天数，正整数 |

```json
{"sku_code":"SF-MOD-001","product_name":"三座模块化布艺沙发","category_code":"sofa","factory_price":"285.00","currency":"USD","moq":20,"lead_time_days":35}
```

- **成功响应 `201`：**

```json
{"code":"0","message":"success","data":{"id":101,"sku_code":"SF-MOD-001","profile_version":1,"profile_status":"unreviewed","analysis_status":"draft"},"request_id":"req_101","timestamp":"2026-08-08T09:00:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"PRODUCT_SKU_DUPLICATE","message":"当前企业已存在相同 SKU","details":[{"field":"sku_code","reason":"duplicate"}],"request_id":"req_101","timestamp":"2026-08-08T09:00:00.000Z"}
```

- **业务错误码：**`PRODUCT_SKU_DUPLICATE` SKU 重复；`PRODUCT_CATEGORY_UNSUPPORTED` 品类不支持；`CURRENCY_REQUIRED` 缺少币种；`PERMISSION_DENIED` 无写权限。

### API-PRD-02 查询产品列表

- **路径/方式：**`GET /api/v1/products`
- **说明：**按品类、状态或关键词分页查询租户产品。
- **请求头：**通用请求头。
- **路径参数/Body：**无。
- **Query 参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `page` | integer | 否 | 页码 |
| `page_size` | integer | 否 | 每页数量 |
| `category_code` | string | 否 | 品类过滤 |
| `lifecycle_status` | string | 否 | `draft/active/inactive` |
| `profile_status` | string | 否 | `unreviewed/confirmed/conflict` |
| `keyword` | string | 否 | 匹配 SKU 或产品名，最长 100 字符 |

- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"items":[{"id":101,"sku_code":"SF-MOD-001","product_name":"三座模块化布艺沙发","category_code":"sofa","profile_status":"confirmed","completeness":86.5}],"page":1,"page_size":20,"total":1},"request_id":"req_102","timestamp":"2026-08-08T09:01:00.000Z"}
```

- **失败响应 `400`：**

```json
{"code":"INVALID_PAGINATION","message":"page_size 不能超过 100","details":[{"field":"page_size","reason":"max 100"}],"request_id":"req_102","timestamp":"2026-08-08T09:01:00.000Z"}
```

- **业务错误码：**`INVALID_PAGINATION` 分页非法；`INVALID_ENUM_VALUE` 枚举非法；`TENANT_CONTEXT_MISMATCH` 租户不匹配。

### API-PRD-03 获取产品详情与画像

- **路径/方式：**`GET /api/v1/products/{product_id}`
- **说明：**返回产品主档、当前画像属性、来源、置信度、冲突和缺失项。
- **请求头：**通用请求头。
- **路径参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `product_id` | integer | 是 | 产品 ID |

- **Query 参数：**`profile_version`（integer，否；默认当前版本）。
- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"id":101,"sku_code":"SF-MOD-001","product_name":"三座模块化布艺沙发","profile_version":2,"profile_status":"unreviewed","attributes":[{"code":"seat_depth_cm","value":58,"source_type":"spec_file","confidence":0.99,"confirmation_status":"confirmed"},{"code":"foam_density","value":null,"source_type":"unknown","confidence":0,"confirmation_status":"unknown"}],"conflicts":[],"missing_required":["frame_material"]},"request_id":"req_103","timestamp":"2026-08-08T09:02:00.000Z"}
```

- **失败响应 `404`：**

```json
{"code":"PRODUCT_NOT_FOUND","message":"产品不存在或无权访问","details":[],"request_id":"req_103","timestamp":"2026-08-08T09:02:00.000Z"}
```

- **业务错误码：**`PRODUCT_NOT_FOUND` 产品不存在；`PRODUCT_PROFILE_VERSION_NOT_FOUND` 画像版本不存在；`SENSITIVE_FIELD_FORBIDDEN` 无权读取敏感字段。

### API-PRD-04 更新产品及画像属性

- **路径/方式：**`PATCH /api/v1/products/{product_id}`
- **说明：**局部更新产品；画像字段变更生成新版本，禁止覆盖已被任务锁定的历史版本。
- **请求头：**通用请求头；`If-Match`（是）传当前资源版本，如 `"v2"`。
- **路径参数：**`product_id`（integer，是，产品 ID）。
- **Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `product_name` | string | 否 | 产品名称 |
| `factory_price` | string | 否 | 敏感字段，需价格编辑权限 |
| `currency` | string | 否 | 币种 |
| `attributes` | array<object> | 否 | 要修改的画像属性 |
| `attributes[].code` | string | 是 | 属性码 |
| `attributes[].value` | any | 是 | 值；显式未知可为 `null` |
| `attributes[].source_note` | string | 否 | 人工修正依据 |

- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"id":101,"profile_version":3,"profile_status":"unreviewed","updated_fields":["frame_material"]},"request_id":"req_104","timestamp":"2026-08-08T09:03:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"RESOURCE_VERSION_CONFLICT","message":"产品已被其他用户更新，请刷新后重试","details":[{"field":"If-Match","reason":"current version is v3"}],"request_id":"req_104","timestamp":"2026-08-08T09:03:00.000Z"}
```

- **业务错误码：**`RESOURCE_VERSION_CONFLICT` 乐观锁冲突；`PRODUCT_ATTRIBUTE_INVALID` 属性不符合 Schema；`SENSITIVE_FIELD_FORBIDDEN` 无价格权限；`PRODUCT_NOT_FOUND` 产品不存在。

### API-PRD-05 上传并解析产品资料

- **路径/方式：**`POST /api/v1/products/{product_id}/assets:parse`
- **说明：**上传图片、PDF、CSV 或 XLSX，异步提取产品画像。
- **请求头：**通用请求头；`Content-Type: multipart/form-data`；必须包含 `Idempotency-Key`。
- **路径参数：**`product_id`（integer，是）。
- **Query 参数：**无。
- **Body（表单）：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `files` | file[] | 是 | 1—10 个文件；单文件不超过 20 MB |
| `asset_type` | string | 是 | `image/spec/pdf/quotation` |
| `language` | string | 否 | 文档语言，默认自动检测 |

- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"parse_job_id":"pj_01J4Y","status":"queued","product_id":101},"request_id":"req_105","timestamp":"2026-08-08T09:04:00.000Z"}
```

- **失败响应 `422`：**

```json
{"code":"PRODUCT_FILE_UNSUPPORTED","message":"不支持的文件类型","details":[{"field":"files[0]","reason":"extension .exe"}],"request_id":"req_105","timestamp":"2026-08-08T09:04:00.000Z"}
```

- **业务错误码：**`PRODUCT_FILE_UNSUPPORTED` 文件类型不支持；`FILE_TOO_LARGE` 文件过大；`FILE_SECURITY_REJECTED` 安全扫描不通过；`PRODUCT_PARSE_FAILED` 解析失败；`MODEL_UPSTREAM_UNAVAILABLE` 模型暂不可用。

### API-PRD-06 确认产品画像

- **路径/方式：**`POST /api/v1/products/{product_id}/profile:confirm`
- **说明：**人工确认当前画像，作为发起市场分析的硬前置条件。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`。
- **路径参数：**`product_id`（integer，是）。
- **Query 参数：**无。
- **Body：**`profile_version`（integer，是）；`confirmation_note`（string，否）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"product_id":101,"profile_version":3,"profile_status":"confirmed","confirmed_by":12,"confirmed_at":"2026-08-08T09:05:00.000Z"},"request_id":"req_106","timestamp":"2026-08-08T09:05:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"PRODUCT_PROFILE_HAS_CONFLICTS","message":"画像存在未处理的关键字段冲突","details":[{"field":"seat_depth_cm","reason":"two confirmed values"}],"request_id":"req_106","timestamp":"2026-08-08T09:05:00.000Z"}
```

- **业务错误码：**`PRODUCT_PROFILE_HAS_CONFLICTS` 存在冲突；`PRODUCT_PROFILE_INCOMPLETE` 关键字段缺失；`PRODUCT_PROFILE_VERSION_STALE` 版本已过期；`PERMISSION_DENIED` 无确认权限。

### API-PRD-07 查询产品资料解析任务

- **优先级：**P0。
- **路径/方式：**`GET /api/v1/product-parse-jobs/{parse_job_id}`
- **说明：**查询 API-PRD-05 创建的异步产品资料解析任务，返回文件安全扫描、文本抽取、多模态识别、Schema 映射和冲突检测进度。
- **请求头：**通用请求头。
- **路径参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `parse_job_id` | string | 是 | 解析任务 ID，如 `pj_01J4Y` |

- **Query 参数：**`include_file_results`（boolean，否，默认 `true`；是否返回逐文件结果）。
- **Body：**无。
- **成功响应 `200`：**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "parse_job_id": "pj_01J4Y",
    "product_id": 101,
    "status": "partial",
    "current_stage": "schema_mapping",
    "progress_percent": 82.5,
    "profile_version": 3,
    "summary": {
      "file_count": 3,
      "succeeded_count": 2,
      "failed_count": 1,
      "extracted_attribute_count": 28,
      "conflict_count": 2
    },
    "file_results": [
      {"file_id":"file_01","file_name":"sofa-front.jpg","security_status":"clean","parse_status":"succeeded","error_code":null},
      {"file_id":"file_02","file_name":"spec.xlsx","security_status":"clean","parse_status":"succeeded","error_code":null},
      {"file_id":"file_03","file_name":"catalog.pdf","security_status":"clean","parse_status":"failed","error_code":"PRODUCT_PDF_PARSE_FAILED"}
    ],
    "retryable": true,
    "started_at": "2026-08-08T09:04:00.000Z",
    "completed_at": null
  },
  "request_id": "req_prd_107",
  "timestamp": "2026-08-08T09:04:12.000Z"
}
```

- **失败响应 `404`：**

```json
{"code":"PRODUCT_PARSE_JOB_NOT_FOUND","message":"产品资料解析任务不存在或无权访问","details":[],"request_id":"req_prd_107","timestamp":"2026-08-08T09:04:12.000Z"}
```

- **业务错误码：**`PRODUCT_PARSE_JOB_NOT_FOUND` 解析任务不存在；`PRODUCT_PARSE_JOB_ACCESS_DENIED` 无访问权限；`PRODUCT_PARSE_JOB_STATE_INVALID` 解析状态不一致；`PRODUCT_PDF_PARSE_FAILED` PDF 解析失败；`MODEL_UPSTREAM_UNAVAILABLE` 多模态模型不可用。

---

## 5. 竞品采集模块

> Demo 默认通过有授权的 CSV/XLSX/JSON 数据导入，不提供绕过平台规则的实时爬虫接口。数据集必须记录来源和使用权限。

### API-CMP-01 创建市场数据集

- **路径/方式：**`POST /api/v1/market-datasets`
- **说明：**创建不可变版本的数据集容器，固定国家、平台、品类和时间范围。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`。
- **路径参数/Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `dataset_name` | string | 是 | 数据集名称 |
| `source_type` | string | 是 | `authorized_export/manual_upload/demo_seed` |
| `source_description` | string | 是 | 来源说明 |
| `authorization_basis` | string | 是 | 使用授权或合规依据 |
| `country_code` | string | 是 | ISO 3166-1 alpha-2 |
| `platform` | string | 是 | `amazon/wayfair/walmart/other` |
| `category_code` | string | 是 | 家具品类码 |
| `currency` | string | 是 | ISO 4217 |
| `data_start_at` | string(datetime) | 否 | 数据起始时间 |
| `data_end_at` | string(datetime) | 否 | 数据结束时间 |

- **成功响应 `201`：**

```json
{"code":"0","message":"success","data":{"id":201,"dataset_version":1,"status":"draft","quality_status":"unchecked"},"request_id":"req_201","timestamp":"2026-08-08T10:00:00.000Z"}
```

- **失败响应 `400`：**

```json
{"code":"DATASET_AUTHORIZATION_REQUIRED","message":"必须说明数据授权依据","details":[{"field":"authorization_basis","reason":"required"}],"request_id":"req_201","timestamp":"2026-08-08T10:00:00.000Z"}
```

- **业务错误码：**`DATASET_AUTHORIZATION_REQUIRED` 缺少授权依据；`DATASET_SCOPE_INVALID` 市场范围非法；`INVALID_DATE_RANGE` 时间范围非法。

### API-CMP-02 导入竞品与评论文件

- **路径/方式：**`POST /api/v1/market-datasets/{dataset_id}/imports`
- **说明：**上传竞品和评论文件，执行字段映射、去重、关联和质量检查。
- **请求头：**通用请求头；`Content-Type: multipart/form-data`；必须包含 `Idempotency-Key`。
- **路径参数：**`dataset_id`（integer，是）。
- **Query 参数：**`dry_run`（boolean，否，默认 `false`；为真时只校验不落库）。
- **Body（表单）：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `listing_file` | file | 是 | CSV/XLSX/JSON，最大 50 MB |
| `review_file` | file | 否 | CSV/XLSX/JSON，最大 100 MB |
| `field_mapping` | JSON string | 是 | 源字段到标准字段映射 |
| `timezone` | string | 否 | 原始时间时区，默认 `UTC` |

- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"import_job_id":"ij_01J4Z","dataset_id":201,"status":"validating"},"request_id":"req_202","timestamp":"2026-08-08T10:01:00.000Z"}
```

- **失败响应 `422`：**

```json
{"code":"DATASET_FIELD_MAPPING_INVALID","message":"字段映射缺少商品唯一标识","details":[{"field":"platform_listing_id","reason":"not mapped"}],"request_id":"req_202","timestamp":"2026-08-08T10:01:00.000Z"}
```

- **业务错误码：**`DATASET_NOT_FOUND` 数据集不存在；`DATASET_IMMUTABLE` 已被任务使用不可覆盖；`DATASET_FIELD_MAPPING_INVALID` 映射错误；`DATASET_FILE_INVALID` 文件错误；`DATASET_IMPORT_FAILED` 导入失败。

### API-CMP-03 获取数据集质量结果

- **路径/方式：**`GET /api/v1/market-datasets/{dataset_id}`
- **说明：**查询数据量、时间范围、重复率、无效率、可分析样本和警告。
- **请求头：**通用请求头。
- **路径参数：**`dataset_id`（integer，是）。
- **Query 参数/Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"id":201,"status":"ready","quality_status":"warning","listing_count":126,"review_count":3860,"valid_review_count":3521,"language_distribution":{"en":0.94,"es":0.06},"quality_metrics":{"duplicate_rate":0.031,"invalid_rate":0.057,"orphan_review_count":18},"warnings":[{"code":"SAMPLE_TIME_RANGE_SHORT","message":"时间跨度不足 90 天，不输出趋势结论"}]},"request_id":"req_203","timestamp":"2026-08-08T10:02:00.000Z"}
```

- **失败响应 `404`：**

```json
{"code":"DATASET_NOT_FOUND","message":"数据集不存在或无权访问","details":[],"request_id":"req_203","timestamp":"2026-08-08T10:02:00.000Z"}
```

- **业务错误码：**`DATASET_NOT_FOUND` 数据集不存在；`DATASET_IMPORT_IN_PROGRESS` 尚在导入；`DATASET_QUALITY_FAILED` 质量检查未通过。

### API-CMP-04 查询竞品商品

- **路径/方式：**`GET /api/v1/market-datasets/{dataset_id}/competitor-listings`
- **说明：**分页查看竞品快照，支持价格、评分和关键词筛选。
- **请求头：**通用请求头。
- **路径参数：**`dataset_id`（integer，是）。
- **Query 参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `page/page_size` | integer | 否 | 分页参数 |
| `price_min/price_max` | string | 否 | 数据集币种下的价格区间 |
| `rating_min` | number | 否 | 最低评分，0—5 |
| `review_count_min` | integer | 否 | 最低评论数 |
| `keyword` | string | 否 | 标题或品牌关键词 |
| `sort` | string | 否 | `rating_desc/reviews_desc/price_asc/price_desc` |

- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"items":[{"id":301,"platform_listing_id":"B0DEMO01","title":"Modern Modular Sofa","brand":"DemoHome","price":"699.99","currency":"USD","rating":4.3,"review_count":852}],"page":1,"page_size":20,"total":126},"request_id":"req_204","timestamp":"2026-08-08T10:03:00.000Z"}
```

- **失败响应 `400`：**

```json
{"code":"PRICE_RANGE_INVALID","message":"price_min 不能大于 price_max","details":[],"request_id":"req_204","timestamp":"2026-08-08T10:03:00.000Z"}
```

- **业务错误码：**`DATASET_NOT_FOUND` 数据集不存在；`PRICE_RANGE_INVALID` 价格范围非法；`INVALID_SORT_FIELD` 排序字段非法。

### API-CMP-05 复核任务竞品集合

- **路径/方式：**`PATCH /api/v1/analysis-tasks/{task_uuid}/competitors/{listing_id}`
- **说明：**人工纳入、排除或调整直接/标杆/替代竞品；变更写入审计日志。
- **请求头：**通用请求头；`If-Match`（是）传任务版本。
- **路径参数：**`task_uuid`（string，是）；`listing_id`（integer，是）。
- **Query 参数：**无。
- **Body：**`review_status`（string，是，`included/excluded`）；`competitor_type`（string，条件必选，`direct/benchmark/substitute`）；`review_note`（string，是）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"listing_id":301,"review_status":"included","competitor_type":"direct","reviewed_by":12,"task_version":4},"request_id":"req_205","timestamp":"2026-08-08T10:04:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"TASK_STAGE_NOT_REVIEWABLE","message":"当前任务阶段不能修改竞品集合","details":[{"field":"status","reason":"published"}],"request_id":"req_205","timestamp":"2026-08-08T10:04:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`COMPETITOR_NOT_IN_TASK` 商品不在候选集；`TASK_STAGE_NOT_REVIEWABLE` 阶段不可复核；`RESOURCE_VERSION_CONFLICT` 版本冲突。

### API-CMP-06 查询任务竞品匹配集合

- **优先级：**P0。
- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}/competitors`
- **说明：**查询任务维度的竞品候选、六维相似度、Rerank 分数、匹配原因和人工复核状态。区别于 API-CMP-04 的原始数据集商品查询。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是，分析任务 UUID）。
- **Query 参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `page/page_size` | integer | 否 | 默认 1/20，`page_size` 最大 100 |
| `competitor_type` | string | 否 | `direct/benchmark/substitute/excluded` |
| `review_status` | string | 否 | `pending/included/excluded/type_changed` |
| `score_min` | number | 否 | 最低综合匹配分，0—100 |
| `sort` | string | 否 | `score_desc/rerank_desc/reviews_desc/price_asc/price_desc` |

- **Body：**无。
- **成功响应 `200`：**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "task_uuid": "e8af1ca0-14e4-4d20-8d88-b2f8bd08d001",
    "task_status": "waiting_human",
    "current_stage": "competitor_review",
    "competitor_set_version": 3,
    "checkpoint_stage": "competitor_review",
    "summary": {"direct":18,"benchmark":6,"substitute":4,"excluded":2,"pending":9,"available_review_count":2870},
    "items": [
      {
        "listing_id": 301,
        "title": "Modern Modular Sofa",
        "brand": "DemoHome",
        "price": "699.99",
        "currency": "USD",
        "rating": 4.3,
        "review_count": 852,
        "competitor_type": "direct",
        "scores": {"category":98,"function":91,"style":84,"price":89,"material":82,"scenario":94,"overall":90.3},
        "rerank_score": 0.91,
        "match_reasons": ["模块结构一致","价格带重叠","小户型场景一致"],
        "review_status": "pending",
        "review_note": null
      }
    ],
    "page": 1,
    "page_size": 20,
    "total": 30
  },
  "request_id": "req_cmp_206",
  "timestamp": "2026-08-08T10:04:10.000Z"
}
```

- **失败响应 `409`：**

```json
{"code":"COMPETITOR_MATCH_NOT_READY","message":"竞品匹配结果尚未生成","details":[{"field":"current_stage","reason":"competitor_embedding"}],"request_id":"req_cmp_206","timestamp":"2026-08-08T10:04:10.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`COMPETITOR_MATCH_NOT_READY` 匹配未完成；`COMPETITOR_FILTER_INVALID` 筛选非法；`TASK_ACCESS_DENIED` 无任务访问权限。

### API-CMP-07 确认竞品集合并恢复工作流

- **优先级：**P0。
- **路径/方式：**`POST /api/v1/analysis-tasks/{task_uuid}/competitors:confirm`
- **说明：**冻结人工复核后的竞品集合，校验 LangGraph Checkpoint，并以 `Command(resume=...)` 恢复 N07 后的评论分析流程。接口成功只表示恢复请求已持久化并重新入队，不表示 Worker 已开始运行。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`；`If-Match`（是）传当前任务版本。
- **路径参数：**`task_uuid`（string，是，分析任务 UUID）。
- **Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `competitor_set_version` | integer | 是 | API-CMP-06 返回的集合版本 |
| `checkpoint_stage` | string | 是 | 必须为 `competitor_review` |
| `confirmed_listing_ids` | array<integer> | 是 | 最终纳入的竞品商品 ID，不能为空 |
| `confirmation_note` | string | 否 | 集合确认说明，最长 1000 字符 |
| `confirm_sample_scope` | boolean | 是 | 明确确认竞品类型及评论样本范围，必须为 `true` |

```json
{"competitor_set_version":3,"checkpoint_stage":"competitor_review","confirmed_listing_ids":[301,302,305,309],"confirmation_note":"排除价格带与结构均不匹配的商品","confirm_sample_scope":true}
```

- **成功响应 `202`：**

```json
{
  "code": "0",
  "message": "accepted",
  "data": {
    "task_uuid": "e8af1ca0-14e4-4d20-8d88-b2f8bd08d001",
    "status": "queued",
    "current_stage": "review_preprocessing",
    "previous_checkpoint_stage": "competitor_review",
    "competitor_set_version": 4,
    "confirmed_listing_count": 4,
    "confirmed_review_count": 2318,
    "resume_command_id": "resume_01J4ZQ",
    "resumed_from_checkpoint": true
  },
  "request_id": "req_cmp_207",
  "timestamp": "2026-08-08T10:05:00.000Z"
}
```

- **失败响应 `409`：**

```json
{"code":"WORKFLOW_CHECKPOINT_CONFLICT","message":"任务恢复点或竞品集合版本已变化，请刷新后重新确认","details":[{"field":"competitor_set_version","reason":"expected 4, received 3"}],"request_id":"req_cmp_207","timestamp":"2026-08-08T10:05:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`TASK_NOT_WAITING_COMPETITOR_REVIEW` 任务不在竞品人工中断点；`COMPETITOR_SET_VERSION_CONFLICT` 集合版本冲突；`COMPETITOR_SAMPLE_INSUFFICIENT` 商品或评论样本不足；`COMPETITOR_CONFIRMATION_REQUIRED` 未确认样本范围；`WORKFLOW_CHECKPOINT_NOT_FOUND` Checkpoint 不存在；`WORKFLOW_CHECKPOINT_CONFLICT` Checkpoint 与任务状态冲突；`WORKFLOW_RESUME_ENQUEUE_FAILED` 恢复命令持久化后入队失败；`IDEMPOTENCY_CONFLICT` 幂等冲突。

**事务与恢复要求：**服务端必须在同一事务中锁定任务、校验 `waiting_human + competitor_review`、冻结竞品集、完成 N07 Stage、将任务更新为 `queued/review_preprocessing` 并写入恢复 Outbox；事务提交后由 Outbox 投递器调用 LangGraph `Command(resume=...)`。投递失败可补偿重试，不得重复生成评论观点。

---

## 6. 评论分析模块

### API-REV-01 查询评论观点

- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}/review-aspects`
- **说明：**按需求本体、情感、人群、场景、星级或时间筛选观点级结果。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是）。
- **Query 参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `page/page_size` | integer | 否 | 分页参数 |
| `taxonomy_code` | string | 否 | 家具需求本体码 |
| `sentiment` | string | 否 | `positive/negative/neutral/mixed` |
| `severity_min` | integer | 否 | 1—5 |
| `user_profile_code` | string | 否 | 人群标签 |
| `scenario_code` | string | 否 | 使用场景标签 |
| `rating` | integer | 否 | 原评论星级 1—5 |
| `date_from/date_to` | string(date) | 否 | 评论日期区间 |
| `validation_status` | string | 否 | `unreviewed/accepted/corrected/rejected` |

- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"items":[{"aspect_id":501,"review_id":401,"taxonomy_code":"comfort.seat_support","opinion_text":"坐垫数月后下陷","sentiment":"negative","severity":4,"evidence_quote":"the cushions sank after three months","extraction_confidence":0.94}],"page":1,"page_size":20,"total":238},"request_id":"req_301","timestamp":"2026-08-08T11:00:00.000Z"}
```

- **失败响应 `400`：**

```json
{"code":"REVIEW_FILTER_INVALID","message":"评论筛选条件无效","details":[{"field":"severity_min","reason":"must be 1..5"}],"request_id":"req_301","timestamp":"2026-08-08T11:00:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`REVIEW_ANALYSIS_NOT_READY` 评论分析未完成；`REVIEW_FILTER_INVALID` 过滤条件非法。

### API-REV-02 获取需求聚类列表

- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}/insight-clusters`
- **说明：**返回正向购买动机、负向痛点及中性偏好，并明确比例分母。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是）。
- **Query 参数：**`sentiment`（string，否）；`taxonomy_code`（string，否）；`sort`（string，否，`importance_desc/mention_desc/confidence_desc`）。
- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"items":[{"cluster_id":601,"cluster_name":"坐垫长期支撑不足","taxonomy_code":"comfort.seat_support","sentiment":"negative","review_count":186,"listing_count":24,"mention_rate":0.0528,"denominator_type":"valid_reviews","cross_listing_rate":0.64,"importance_score":82.4,"cluster_confidence":0.88}]},"request_id":"req_302","timestamp":"2026-08-08T11:01:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"INSIGHT_CLUSTER_NOT_READY","message":"需求聚类尚未完成","details":[{"field":"current_stage","reason":"aspect_extracting"}],"request_id":"req_302","timestamp":"2026-08-08T11:01:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`INSIGHT_CLUSTER_NOT_READY` 聚类未完成；`INVALID_SORT_FIELD` 排序非法。

### API-REV-03 获取聚类证据

- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}/insight-clusters/{cluster_id}/evidence`
- **说明：**下钻到原始评论、翻译、商品和证据 Span，支持结论追溯。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是）；`cluster_id`（integer，是）。
- **Query 参数：**`page/page_size`（integer，否）；`primary_only`（boolean，否，默认 `false`）。
- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"cluster_id":601,"items":[{"aspect_id":501,"listing_id":301,"listing_title":"Modern Modular Sofa","rating":2,"reviewed_at":"2026-05-10T00:00:00.000Z","body_original":"The cushions sank after three months.","body_translated":"坐垫三个月后下陷。","evidence_quote":"cushions sank after three months","support_type":"supports"}],"page":1,"page_size":20,"total":186},"request_id":"req_303","timestamp":"2026-08-08T11:02:00.000Z"}
```

- **失败响应 `404`：**

```json
{"code":"INSIGHT_CLUSTER_NOT_FOUND","message":"需求聚类不存在","details":[],"request_id":"req_303","timestamp":"2026-08-08T11:02:00.000Z"}
```

- **业务错误码：**`INSIGHT_CLUSTER_NOT_FOUND` 聚类不存在；`EVIDENCE_NOT_AVAILABLE` 无可展示证据；`TASK_RESOURCE_MISMATCH` 聚类不属于该任务。

### API-REV-04 人工校正评论观点

- **路径/方式：**`PATCH /api/v1/analysis-tasks/{task_uuid}/review-aspects/{aspect_id}`
- **说明：**接受、修正或驳回 AI 观点；保留模型原值和人工审计记录。
- **请求头：**通用请求头；`If-Match`（是）传观点版本。
- **路径参数：**`task_uuid`（string，是）；`aspect_id`（integer，是）。
- **Query 参数：**无。
- **Body：**`validation_status`（string，是，`accepted/corrected/rejected`）；`taxonomy_code`（string，修正时可选）；`sentiment`（string，修正时可选）；`severity`（integer，修正时可选）；`review_note`（string，是）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"aspect_id":501,"validation_status":"corrected","taxonomy_code":"durability.cushion_resilience","version":2},"request_id":"req_304","timestamp":"2026-08-08T11:03:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"REPORT_ALREADY_PUBLISHED","message":"已发布报告引用该观点，不能原地修改；请创建新分析版本","details":[],"request_id":"req_304","timestamp":"2026-08-08T11:03:00.000Z"}
```

- **业务错误码：**`REVIEW_ASPECT_NOT_FOUND` 观点不存在；`REVIEW_ASPECT_VALUE_INVALID` 修正值非法；`REPORT_ALREADY_PUBLISHED` 结果已冻结；`RESOURCE_VERSION_CONFLICT` 版本冲突。

---

## 7. AI 市场洞察模块

### API-INS-01 创建市场分析任务

- **路径/方式：**`POST /api/v1/analysis-tasks`
- **说明：**锁定产品画像版本、市场数据集版本、目标市场及算法版本，创建可复现任务。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`。
- **路径参数/Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `task_name` | string | 是 | 任务名称 |
| `product_id` | integer | 是 | 产品 ID |
| `product_profile_version` | integer | 是 | 已确认画像版本 |
| `dataset_id` | integer | 是 | 状态为 ready 的数据集 |
| `target_country` | string | 是 | ISO 国家码 |
| `target_platform` | string | 是 | 目标平台 |
| `analysis_currency` | string | 是 | 统一分析币种 |
| `analysis_config` | object | 否 | 价格带、样本上限、过滤阈值、评分权重 |

```json
{"task_name":"SF-MOD-001 美国站机会分析","product_id":101,"product_profile_version":3,"dataset_id":201,"target_country":"US","target_platform":"amazon","analysis_currency":"USD","analysis_config":{"price_band":{"min":"500","max":"1000"},"review_limit":4000}}
```

- **成功响应 `201`：**

```json
{"code":"0","message":"success","data":{"task_uuid":"e8af1ca0-14e4-4d20-8d88-b2f8bd08d001","status":"draft","current_stage":"draft","progress_percent":0,"ontology_version":"sofa-ontology-v1","scoring_version":"opportunity-v1"},"request_id":"req_401","timestamp":"2026-08-08T12:00:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"PRODUCT_PROFILE_NOT_CONFIRMED","message":"指定产品画像版本尚未确认","details":[{"field":"product_profile_version","reason":"status unreviewed"}],"request_id":"req_401","timestamp":"2026-08-08T12:00:00.000Z"}
```

- **业务错误码：**`PRODUCT_PROFILE_NOT_CONFIRMED` 画像未确认；`DATASET_NOT_READY` 数据集不可分析；`ANALYSIS_SCOPE_MISMATCH` 数据集与目标市场冲突；`ANALYSIS_CONFIG_INVALID` 配置非法；`IDEMPOTENCY_CONFLICT` 幂等键对应不同请求。

### API-INS-02 启动分析任务

- **路径/方式：**`POST /api/v1/analysis-tasks/{task_uuid}:start`
- **说明：**异步启动竞品筛选、观点抽取、聚类、评分、工程建议和报告生成流水线。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`。
- **路径参数：**`task_uuid`（string，是）。
- **Query 参数：**无。
- **Body：**`confirm_data_scope`（boolean，是）；`max_model_cost`（string，否，单任务成本保护阈值）；`cost_unit`（string，条件必选）。
- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"task_uuid":"e8af1ca0-14e4-4d20-8d88-b2f8bd08d001","status":"queued","current_stage":"preflight_check","progress_percent":1},"request_id":"req_402","timestamp":"2026-08-08T12:01:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"TASK_ALREADY_STARTED","message":"任务已启动，不能重复启动","details":[{"field":"status","reason":"running"}],"request_id":"req_402","timestamp":"2026-08-08T12:01:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`TASK_ALREADY_STARTED` 已启动；`DATASET_SAMPLE_INSUFFICIENT` 样本不足；`DATA_SCOPE_NOT_CONFIRMED` 未确认范围；`MODEL_BUDGET_INVALID` 成本阈值非法。

### API-INS-03 查询任务状态

- **优先级：**P0（V2 扩展）。
- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}`
- **说明：**供页面轮询任务进度，返回总体状态、节点运行、部分失败、Checkpoint、可重试性和报告入口。V2 将原 `stages` 正式更名并扩展为 `stage_runs`。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是）。
- **Query 参数：**`include_stage_runs`（boolean，否，默认 `true`）；`include_partial_failures`（boolean，否，默认 `true`）。兼容期继续接受 `include_stages`，但响应只返回 `stage_runs`。
- **Body：**无。
- **成功响应 `200`：**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "task_uuid": "e8af1ca0-14e4-4d20-8d88-b2f8bd08d001",
    "status": "partial_succeeded",
    "current_stage": "market_analytics",
    "progress_percent": 71.0,
    "sample_listing_count": 84,
    "sample_review_count": 3521,
    "checkpoint_stage": "need_clustering",
    "retryable": true,
    "report_uuid": null,
    "stage_runs": [
      {
        "stage_code": "review_extracting",
        "attempt_no": 1,
        "status": "partial_succeeded",
        "started_at": "2026-08-08T12:01:15.000Z",
        "ended_at": "2026-08-08T12:01:54.000Z",
        "duration_ms": 39000,
        "retryable": true,
        "error_code": "REVIEW_BATCH_PARTIAL_FAILED",
        "error_message": "2 个批次失败，成功覆盖率 96.8%"
      },
      {
        "stage_code": "trend_analytics",
        "attempt_no": 1,
        "status": "skipped",
        "started_at": null,
        "ended_at": "2026-08-08T12:02:00.000Z",
        "duration_ms": 0,
        "retryable": false,
        "error_code": null,
        "error_message": "仅有一个可比较时间点，趋势分支已跳过"
      },
      {
        "stage_code": "enterprise_fit",
        "attempt_no": 1,
        "status": "running",
        "started_at": "2026-08-08T12:02:01.000Z",
        "ended_at": null,
        "duration_ms": null,
        "retryable": null,
        "error_code": null,
        "error_message": null
      }
    ],
    "partial_failures": [
      {
        "stage_code": "review_extracting",
        "unit_type": "review_batch",
        "failed_unit_ids": ["batch_07","batch_12"],
        "failed_count": 2,
        "total_count": 62,
        "impact": "评论观点覆盖率下降，最终置信度上限调整为 0.85",
        "retryable": true
      }
    ],
    "warnings": ["趋势分析已跳过，报告仅支持截面结论"]
  },
  "request_id": "req_403",
  "timestamp": "2026-08-08T12:02:00.000Z"
}
```

字段约束：

| 字段 | 类型 | 是否始终返回 | 说明 |
|---|---|:---:|---|
| `stage_runs` | array<object> | 条件 | `include_stage_runs=true` 时返回，按阶段开始时间升序 |
| `partial_failures` | array<object> | 条件 | `include_partial_failures=true` 时返回；无失败返回空数组 |
| `checkpoint_stage` | string/null | 是 | 最近一次已持久化且可安全恢复的阶段；草稿任务可为 `null` |
| `retryable` | boolean | 是 | 当前任务是否允许人工重试；不是单个 Stage 的重试标记 |
| `report_uuid` | string/null | 是 | N19 最终入库成功后返回报告 UUID；此前为 `null` |

`stage_runs.status` 枚举为：`queued/running/waiting_human/retry_scheduled/succeeded/partial_succeeded/failed/skipped/cancelled`。任务总体状态枚举为：`draft/queued/running/waiting_human/partial_succeeded/succeeded/failed/cancelled`。

- **失败响应 `404`：**

```json
{"code":"TASK_NOT_FOUND","message":"分析任务不存在或无权访问","details":[],"request_id":"req_403","timestamp":"2026-08-08T12:02:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`TASK_ACCESS_DENIED` 无权访问；`TASK_STATE_CORRUPTED` 任务与阶段状态异常；`WORKFLOW_CHECKPOINT_INCONSISTENT` Checkpoint 与业务结果不一致。

### API-INS-04 查询市场机会结果

- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}/opportunities`
- **说明：**返回机会总分、六维分数、独立置信度、推荐等级和能力缺口。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是）。
- **Query 参数：**`status`（string，否）；`recommendation_level`（string，否）；`sort`（string，否，默认 `score_desc`）。
- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"items":[{"opportunity_code":"OPP-001","title":"小户型模块沙发的耐久支撑升级","overall_score":78.6,"confidence":0.81,"recommendation_level":"priority_validate","scores":{"demand_heat":82,"demand_growth":null,"unmet_need":86,"competition_space":69,"profit_space":72,"enterprise_fit":84},"score_notes":["无足够时间序列，需求增长项未计入并已归一化权重"],"capability_gaps":["缺少坐垫耐久测试数据"]}]},"request_id":"req_404","timestamp":"2026-08-08T12:03:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"OPPORTUNITY_RESULT_NOT_READY","message":"市场机会结果尚未生成","details":[{"field":"current_stage","reason":"need_clustering"}],"request_id":"req_404","timestamp":"2026-08-08T12:03:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`OPPORTUNITY_RESULT_NOT_READY` 结果未完成；`SCORE_VERSION_UNSUPPORTED` 评分版本不支持。

### API-INS-05 获取产品改进建议

- **路径/方式：**`GET /api/v1/analysis-tasks/{task_uuid}/recommendations`
- **说明：**返回问题、证据、根因假设、动作、影响、风险和验证方法。
- **请求头：**通用请求头。
- **路径参数：**`task_uuid`（string，是）。
- **Query 参数：**`opportunity_id`（integer，否）；`expert_review_status`（string，否）；`priority`（string，否）。
- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"items":[{"id":801,"opportunity_id":701,"recommendation_type":"material_structure","problem_statement":"部分竞品坐垫使用后下陷","root_cause_hypotheses":["回弹材料性能不足","层结构支撑不足"],"recommended_action":"对高回弹材料和分层支撑方案进行打样对比，具体密度由工程测试确定","impact_dimensions":["comfort","cost","weight"],"cost_impact":null,"priority":"high","confidence":0.78,"validation_method":"3 万次耐久测试与盲测对比","expert_review_status":"pending","evidence_count":186}]},"request_id":"req_405","timestamp":"2026-08-08T12:04:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"RECOMMENDATION_NOT_READY","message":"工程化建议尚未生成","details":[],"request_id":"req_405","timestamp":"2026-08-08T12:04:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`RECOMMENDATION_NOT_READY` 建议未生成；`OPPORTUNITY_NOT_FOUND` 机会不存在。

### API-INS-06 专家复核产品建议

- **路径/方式：**`PATCH /api/v1/analysis-tasks/{task_uuid}/recommendations/{recommendation_id}/review`
- **说明：**工程人员将建议标记为可行、待确认或不可行；系统不自动触发打样。若任务位于 LangGraph `expert_review` 人工中断点，服务端在所有必需建议完成复核后恢复证据审计流程。
- **请求头：**通用请求头；`If-Match`（是）传建议版本。
- **路径参数：**`task_uuid`（string，是）；`recommendation_id`（integer，是）。
- **Query 参数：**无。
- **Body：**`expert_review_status`（string，是，`feasible/needs_validation/infeasible`）；`review_note`（string，是）；`revised_action`（string，否）；`estimated_cost_impact`（object，否，必须含依据、上下限和币种）；`expected_checkpoint_stage`（string，任务在人工中断时是，固定为 `expert_review`）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"recommendation_id":801,"expert_review_status":"needs_validation","review_note":"需先取得供应商材料样本并测试","reviewed_by":12,"version":2,"workflow":{"resumed":true,"status":"queued","current_stage":"evidence_auditing","resume_command_id":"resume_01J501"}},"request_id":"req_406","timestamp":"2026-08-08T12:05:00.000Z"}
```

- **失败响应 `422`：**

```json
{"code":"COST_EVIDENCE_REQUIRED","message":"填写精确成本影响时必须提供 BOM 或报价依据","details":[{"field":"estimated_cost_impact.evidence","reason":"required"}],"request_id":"req_406","timestamp":"2026-08-08T12:05:00.000Z"}
```

- **业务错误码：**`RECOMMENDATION_NOT_FOUND` 建议不存在；`EXPERT_REVIEW_STATUS_INVALID` 状态非法；`COST_EVIDENCE_REQUIRED` 成本缺少依据；`EXPERT_REVIEW_INCOMPLETE` 仍有必需建议未复核，保存本条但暂不恢复；`WORKFLOW_CHECKPOINT_CONFLICT` 恢复点冲突；`WORKFLOW_RESUME_ENQUEUE_FAILED` 恢复入队失败；`PERMISSION_DENIED` 无复核权限；`RESOURCE_VERSION_CONFLICT` 版本冲突。

### API-INS-07 重试失败工作流阶段

- **优先级：**P1。
- **路径/方式：**`POST /api/v1/analysis-tasks/{task_uuid}/stages/{stage_code}:retry`
- **说明：**管理员或获授权用户从最近安全 Checkpoint 重试失败阶段；已成功 Stage 和已提交业务结果不得重复执行或重复计费。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`；`If-Match`（是）传任务版本。
- **路径参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `task_uuid` | string | 是 | 分析任务 UUID |
| `stage_code` | string | 是 | API-INS-03 返回且 `retryable=true` 的失败阶段码 |

- **Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `failed_unit_ids` | array<string> | 否 | 仅重试失败批次；为空时重试整个 Stage |
| `reason` | string | 是 | 人工重试原因，最长 500 字符 |
| `expected_checkpoint_stage` | string | 是 | 防止从过期恢复点重试 |

- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"task_uuid":"e8af1ca0-14e4-4d20-8d88-b2f8bd08d001","stage_code":"review_extracting","attempt_no":2,"status":"retry_scheduled","checkpoint_stage":"review_preprocessing","retry_unit_count":2,"resume_command_id":"retry_01J500"},"request_id":"req_ins_407","timestamp":"2026-08-08T12:06:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"STAGE_NOT_RETRYABLE","message":"该阶段不可重试或已成功完成","details":[{"field":"stage_code","reason":"status succeeded"}],"request_id":"req_ins_407","timestamp":"2026-08-08T12:06:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`STAGE_NOT_FOUND` 阶段不存在；`STAGE_NOT_RETRYABLE` 阶段不可重试；`RETRY_UNIT_INVALID` 批次不属于失败集合；`WORKFLOW_CHECKPOINT_NOT_FOUND` 无安全恢复点；`WORKFLOW_CHECKPOINT_CONFLICT` 恢复点过期；`TASK_LOCKED` 任务已有 Worker 执行；`IDEMPOTENCY_CONFLICT` 幂等冲突。

### API-INS-08 取消分析任务

- **优先级：**P1。
- **路径/方式：**`POST /api/v1/analysis-tasks/{task_uuid}:cancel`
- **说明：**提交协作式取消请求。Worker 在当前原子写或模型调用返回后安全停止，保留已成功阶段和中间结果，不删除已产生费用记录。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`；`If-Match`（是）传任务版本。
- **路径参数：**`task_uuid`（string，是，分析任务 UUID）。
- **Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `reason_code` | string | 是 | `user_cancelled/data_issue/scope_changed/cost_control/other` |
| `reason_note` | string | 条件必选 | `other` 时必填，最长 1000 字符 |

- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"task_uuid":"e8af1ca0-14e4-4d20-8d88-b2f8bd08d001","cancel_requested":true,"status":"running","current_stage":"review_extracting","message":"将在当前原子操作完成后安全停止"},"request_id":"req_ins_408","timestamp":"2026-08-08T12:07:00.000Z"}
```

再次查询 API-INS-03，安全停止完成后返回 `status=cancelled`，活跃 `stage_runs.status=cancelled`。

- **失败响应 `409`：**

```json
{"code":"TASK_NOT_CANCELLABLE","message":"任务已完成或正在提交最终事务，不能取消","details":[{"field":"status","reason":"succeeded"}],"request_id":"req_ins_408","timestamp":"2026-08-08T12:07:00.000Z"}
```

- **业务错误码：**`TASK_NOT_FOUND` 任务不存在；`TASK_NOT_CANCELLABLE` 状态不可取消；`TASK_CANCEL_ALREADY_REQUESTED` 已申请取消；`CANCEL_REASON_INVALID` 原因非法；`RESOURCE_VERSION_CONFLICT` 任务版本冲突；`PERMISSION_DENIED` 无取消权限。

---

## 8. Listing 生成模块（P1 扩展）

> Listing 生成为洞察结果的受控下游，不是 V1 核心评分能力。所有输出必须人工审核，禁止直接发布到电商平台。

### API-LST-01 生成 Listing

- **路径/方式：**`POST /api/v1/listing-generations`
- **说明：**组合已确认产品画像、可选市场洞察和平台约束，异步生成 Listing。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`。
- **路径参数/Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `product_id` | integer | 是 | 已确认画像的产品 |
| `source_task_uuid` | string | 否 | 上游市场分析任务 |
| `target_platform` | string | 是 | 目标平台 |
| `country_code` | string | 是 | 目标国家 |
| `language` | string | 是 | BCP 47 语言码 |
| `generation_type` | string | 是 | `title/bullets/description/keywords/full` |
| `tone` | string | 否 | `professional/lifestyle/concise` |
| `constraints` | object | 否 | 字数、禁用词、必须覆盖卖点 |

- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"generation_id":901,"status":"generating"},"request_id":"req_501","timestamp":"2026-08-08T13:00:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"LISTING_PRODUCT_PROFILE_NOT_CONFIRMED","message":"产品画像未确认，不能生成 Listing","details":[],"request_id":"req_501","timestamp":"2026-08-08T13:00:00.000Z"}
```

- **业务错误码：**`LISTING_PRODUCT_PROFILE_NOT_CONFIRMED` 画像未确认；`LISTING_PLATFORM_UNSUPPORTED` 平台不支持；`LISTING_CONSTRAINT_INVALID` 约束非法；`SOURCE_TASK_NOT_READY` 上游洞察未完成；`MODEL_CONTENT_BLOCKED` 内容被安全策略拦截。

### API-LST-02 获取 Listing 生成结果

- **路径/方式：**`GET /api/v1/listing-generations/{generation_id}`
- **说明：**查询生成状态、内容、合规警告和来源快照摘要。
- **请求头：**通用请求头。
- **路径参数：**`generation_id`（integer，是）。
- **Query 参数/Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"generation_id":901,"status":"generated","target_platform":"amazon","language":"en-US","generated_title":"Modular 3-Seat Sofa with Supportive Cushions","generated_bullets":["Flexible modular layout for compact living rooms","Support-focused cushion structure; performance subject to product specification"],"generated_description":"...","generated_keywords":["modular sofa","small space sofa"],"compliance_warnings":[{"level":"warning","field":"generated_bullets[1]","message":"耐久表达需由测试报告验证"}]} ,"request_id":"req_502","timestamp":"2026-08-08T13:01:00.000Z"}
```

- **失败响应 `404`：**

```json
{"code":"LISTING_GENERATION_NOT_FOUND","message":"Listing 生成记录不存在","details":[],"request_id":"req_502","timestamp":"2026-08-08T13:01:00.000Z"}
```

- **业务错误码：**`LISTING_GENERATION_NOT_FOUND` 记录不存在；`LISTING_GENERATION_FAILED` 生成失败；`LISTING_GENERATION_PENDING` 尚未完成。

### API-LST-03 审核 Listing

- **路径/方式：**`PATCH /api/v1/listing-generations/{generation_id}/review`
- **说明：**人工修改并批准或驳回生成内容，仅记录结果，不自动发布。
- **请求头：**通用请求头；`If-Match`（是）传记录版本。
- **路径参数：**`generation_id`（integer，是）。
- **Query 参数：**无。
- **Body：**`status`（string，是，`approved/rejected`）；`review_note`（string，是）；`edited_content`（object，否，人工修改后的标题、五点、描述和关键词）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"generation_id":901,"status":"approved","reviewed_by":12,"reviewed_at":"2026-08-08T13:02:00.000Z","version":2},"request_id":"req_503","timestamp":"2026-08-08T13:02:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"LISTING_NOT_REVIEWABLE","message":"当前 Listing 状态不可审核","details":[{"field":"status","reason":"generating"}],"request_id":"req_503","timestamp":"2026-08-08T13:02:00.000Z"}
```

- **业务错误码：**`LISTING_NOT_REVIEWABLE` 状态不可审核；`LISTING_COMPLIANCE_BLOCKED` 存在阻断级合规警告；`RESOURCE_VERSION_CONFLICT` 版本冲突；`PERMISSION_DENIED` 无审核权限。

---

## 9. 报告导出模块

### API-RPT-01 获取分析报告

- **路径/方式：**`GET /api/v1/reports/{report_uuid}`
- **说明：**返回固定数据范围、产品快照、机会、建议、风险、待验证项和证据入口。
- **请求头：**通用请求头。
- **路径参数：**`report_uuid`（string，是）。
- **Query 参数：**`version`（integer，否，默认最新可见版本）。
- **Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"report_uuid":"b4fb642e-cd45-49fd-97f1-38adfa3d0001","report_version":1,"status":"draft","title":"SF-MOD-001 美国市场机会报告","decision_recommendation":"priority_validate","overall_opportunity_score":78.6,"confidence":0.81,"data_scope_summary":{"country":"US","platform":"amazon","listing_count":84,"valid_review_count":3521,"date_range":["2026-01-01","2026-06-30"]},"executive_summary":"...","opportunities":[{"code":"OPP-001","score":78.6}],"risk_summary":["缺少工厂耐久测试数据"],"pending_validation_items":["验证坐垫结构方案"]},"request_id":"req_601","timestamp":"2026-08-08T14:00:00.000Z"}
```

- **失败响应 `404`：**

```json
{"code":"REPORT_NOT_FOUND","message":"报告不存在或无权访问","details":[],"request_id":"req_601","timestamp":"2026-08-08T14:00:00.000Z"}
```

- **业务错误码：**`REPORT_NOT_FOUND` 报告不存在；`REPORT_VERSION_NOT_FOUND` 版本不存在；`REPORT_DRAFT_FORBIDDEN` 无权查看草稿；`REPORT_GENERATION_INCOMPLETE` 报告未生成完成。

### API-RPT-02 发布报告

- **路径/方式：**`POST /api/v1/reports/{report_uuid}:publish`
- **说明：**在人工复核后发布报告；冻结当前报告快照并写入审计日志。
- **请求头：**通用请求头；必须包含 `Idempotency-Key` 和 `If-Match`。
- **路径参数：**`report_uuid`（string，是）。
- **Query 参数：**无。
- **Body：**`confirm_evidence_reviewed`（boolean，是）；`confirm_risk_disclosed`（boolean，是）；`publish_note`（string，否）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"report_uuid":"b4fb642e-cd45-49fd-97f1-38adfa3d0001","status":"published","published_by":10,"published_at":"2026-08-08T14:01:00.000Z"},"request_id":"req_602","timestamp":"2026-08-08T14:01:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"REPORT_VALIDATION_PENDING","message":"报告仍有阻断发布的待复核项","details":[{"field":"recommendation:801","reason":"expert review pending"}],"request_id":"req_602","timestamp":"2026-08-08T14:01:00.000Z"}
```

- **业务错误码：**`REPORT_NOT_FOUND` 报告不存在；`REPORT_VALIDATION_PENDING` 复核未完成；`REPORT_EVIDENCE_INCOMPLETE` 结论缺少证据；`REPORT_ALREADY_PUBLISHED` 已发布；`PERMISSION_DENIED` 无发布权限。

### API-RPT-03 创建报告导出任务

- **优先级：**P1【P1迭代功能，本次Demo暂不实现】。
- **路径/方式：**`POST /api/v1/reports/{report_uuid}/exports`
- **说明：**异步导出 PDF、DOCX、XLSX 或 JSON；默认仅允许导出已发布报告。
- **请求头：**通用请求头；必须包含 `Idempotency-Key`。
- **路径参数：**`report_uuid`（string，是）。
- **Query 参数：**无。
- **Body：**`export_format`（string，是，`pdf/docx/xlsx/json`）；`include_raw_evidence`（boolean，否，默认 `false`）；`locale`（string，否，默认 `zh-CN`）。
- **成功响应 `202`：**

```json
{"code":"0","message":"accepted","data":{"export_id":1001,"status":"queued","export_format":"pdf"},"request_id":"req_603","timestamp":"2026-08-08T14:02:00.000Z"}
```

- **失败响应 `409`：**

```json
{"code":"REPORT_NOT_PUBLISHED","message":"报告尚未发布，不能导出正式版本","details":[],"request_id":"req_603","timestamp":"2026-08-08T14:02:00.000Z"}
```

- **业务错误码：**`REPORT_NOT_PUBLISHED` 未发布；`EXPORT_FORMAT_UNSUPPORTED` 格式不支持；`RAW_EVIDENCE_EXPORT_FORBIDDEN` 无原始证据导出权限；`EXPORT_TASK_DUPLICATE` 重复导出任务。

### API-RPT-04 查询导出状态

- **优先级：**P1【P1迭代功能，本次Demo暂不实现】。
- **路径/方式：**`GET /api/v1/report-exports/{export_id}`
- **说明：**轮询导出进度，成功后返回短效下载地址及文件摘要。
- **请求头：**通用请求头。
- **路径参数：**`export_id`（integer，是）。
- **Query 参数/Body：**无。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"export_id":1001,"status":"succeeded","export_format":"pdf","file_size_bytes":842135,"file_hash":"5b8c...sha256","download_url":"https://oss.example.com/signed/...","expires_at":"2026-08-08T14:18:00.000Z"},"request_id":"req_604","timestamp":"2026-08-08T14:03:00.000Z"}
```

- **失败响应 `410`：**

```json
{"code":"EXPORT_FILE_EXPIRED","message":"导出文件已过期，请重新发起导出","details":[],"request_id":"req_604","timestamp":"2026-08-08T14:03:00.000Z"}
```

- **业务错误码：**`EXPORT_NOT_FOUND` 导出任务不存在；`EXPORT_GENERATION_FAILED` 生成失败；`EXPORT_FILE_EXPIRED` 文件过期；`EXPORT_ACCESS_DENIED` 无下载权限。

### API-RPT-05 查询报告导出历史

- **优先级：**P1【P1迭代功能，本次Demo暂不实现】。
- **路径/方式：**`GET /api/v1/reports/{report_uuid}/exports`
- **说明：**分页查询指定报告的历史导出任务，供导出页面展示状态、格式、申请人、文件有效期和下载统计。短效下载 URL 仅对尚未过期且有权限的成功记录返回。
- **请求头：**通用请求头。
- **路径参数：**`report_uuid`（string，是，报告 UUID）。
- **Query 参数：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `page/page_size` | integer | 否 | 默认 1/20，`page_size` 最大 100 |
| `status` | string | 否 | `queued/generating/succeeded/failed/expired` |
| `export_format` | string | 否 | `pdf/docx/xlsx/json` |
| `requested_by_me` | boolean | 否 | 只查询当前用户发起的记录，默认 `false` |

- **Body：**无。
- **成功响应 `200`：**

```json
{
  "code": "0",
  "message": "success",
  "data": {
    "report_uuid": "b4fb642e-cd45-49fd-97f1-38adfa3d0001",
    "items": [
      {
        "export_id": 1001,
        "export_format": "pdf",
        "status": "succeeded",
        "file_size_bytes": 842135,
        "file_hash": "5b8c...sha256",
        "download_url": "https://oss.example.com/signed/...",
        "requested_by": 12,
        "requested_at": "2026-08-08T14:02:00.000Z",
        "completed_at": "2026-08-08T14:03:00.000Z",
        "expires_at": "2026-08-08T14:18:00.000Z",
        "download_count": 1,
        "last_downloaded_at": "2026-08-08T14:05:00.000Z"
      }
    ],
    "page": 1,
    "page_size": 20,
    "total": 1
  },
  "request_id": "req_rpt_605",
  "timestamp": "2026-08-08T14:06:00.000Z"
}
```

- **失败响应 `404`：**

```json
{"code":"REPORT_NOT_FOUND","message":"报告不存在或无权访问","details":[],"request_id":"req_rpt_605","timestamp":"2026-08-08T14:06:00.000Z"}
```

- **业务错误码：**`REPORT_NOT_FOUND` 报告不存在；`EXPORT_HISTORY_FILTER_INVALID` 筛选非法；`EXPORT_ACCESS_DENIED` 无导出历史访问权限；`REPORT_EXPORT_HISTORY_QUERY_FAILED` 历史查询失败。

---

## 10. Model Router 大模型调用封装

### 10.1 调用边界

```text
Web 前端
  → FurniScope 业务 API
    → 异步工作流/任务阶段
      → FurniScope Model Router 内部接口
        → 阿里云百炼 Model Router / 模型服务（外部）
```

- API Key 仅保存在服务端密钥管理系统，不进入数据库、日志、前端或报告。
- 业务服务传 `task_type` 和质量/成本约束，不直接绑定具体模型；Router 负责选模、限流、重试、降级、Schema 校验和用量记录。
- 所有调用写入 `ai_model_runs`，记录实际模型、Token、延迟、费用估算和错误，但不默认持久化完整敏感 Prompt。
- 百炼返回内容必须先通过 JSON Schema、安全策略和证据约束校验，才能进入业务结果表。

### API-MDL-01 统一生成/结构化推理（内部）

- **可见性：**内部封装接口，不对前端和公网开放。
- **路径/方式：**`POST /internal/v1/model-router/generations`
- **说明：**封装视觉理解、观点抽取、聚类命名、工程建议、报告和 Listing 生成。
- **请求头：**`Authorization: Service <service_token>`；`X-Service-Name`（是）；`X-Request-Id`（是）；`Idempotency-Key`（是）；`Content-Type: application/json`。
- **路径参数/Query 参数：**无。
- **Body：**

| 参数名 | 类型 | 必选 | 参数说明 |
|---|---|:---:|---|
| `tenant_id` | integer | 是 | 计费、隔离和审计租户 |
| `task_uuid` | string | 否 | 所属业务任务 |
| `stage_run_id` | integer | 否 | 工作流阶段运行 ID |
| `task_type` | string | 是 | `vision/extraction/reasoning/report/listing` |
| `prompt_code` | string | 是 | 服务端 Prompt 模板码 |
| `prompt_version` | string | 是 | Prompt 版本 |
| `input` | object | 是 | 脱敏后的业务输入 |
| `attachments` | array<object> | 否 | 对象存储短效地址、媒体类型和哈希 |
| `output_schema` | object | 是 | JSON Schema 或已注册 Schema 引用 |
| `routing_policy` | object | 否 | `quality_tier/max_latency_ms/max_cost/fallback_allowed` |
| `safety_policy` | object | 否 | 敏感信息和内容安全策略 |

- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"model_run_id":1101,"provider":"aliyun_model_router","model_id":"router-selected-model","output":{"taxonomy_code":"comfort.seat_support","sentiment":"negative","evidence_start":4,"evidence_end":39},"usage":{"input_tokens":620,"output_tokens":86,"image_count":0},"latency_ms":1280,"estimated_cost":"0.0032","cost_unit":"CNY","schema_valid":true},"request_id":"req_701","timestamp":"2026-08-08T15:00:00.000Z"}
```

- **失败响应 `502`：**

```json
{"code":"MODEL_OUTPUT_SCHEMA_INVALID","message":"模型输出未通过结构校验，重试后仍失败","details":[{"field":"evidence_end","reason":"required"}],"request_id":"req_701","timestamp":"2026-08-08T15:00:00.000Z"}
```

- **业务错误码：**`MODEL_ROUTE_NOT_FOUND` 无可用模型路由；`MODEL_UPSTREAM_TIMEOUT` 上游超时；`MODEL_UPSTREAM_RATE_LIMITED` 百炼限流；`MODEL_OUTPUT_SCHEMA_INVALID` 输出结构非法；`MODEL_CONTENT_BLOCKED` 安全拦截；`MODEL_BUDGET_EXCEEDED` 超出预算；`MODEL_ALL_FALLBACKS_FAILED` 降级均失败。

### API-MDL-02 文本向量化（内部）

- **可见性：**内部封装接口；依赖阿里云百炼 Model Router 的 Embedding 能力。
- **路径/方式：**`POST /internal/v1/model-router/embeddings`
- **说明：**批量生成产品、竞品或评论观点向量，输入按哈希缓存。
- **请求头：**同 API-MDL-01。
- **路径参数/Query 参数：**无。
- **Body：**`tenant_id`（integer，是）；`task_uuid`（string，否）；`texts`（array<string>，是，1—128 条）；`purpose`（string，是，`product_match/review_cluster`）；`dimensions`（integer，否）；`normalize`（boolean，否，默认 `true`）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"model_run_id":1102,"model_id":"router-selected-embedding-model","vectors":[{"index":0,"embedding":[0.012,-0.044,0.108]}],"dimensions":1024,"usage":{"input_tokens":42},"cache_hits":0},"request_id":"req_702","timestamp":"2026-08-08T15:01:00.000Z"}
```

- **失败响应 `400`：**

```json
{"code":"EMBEDDING_BATCH_TOO_LARGE","message":"单批文本数量不能超过 128","details":[{"field":"texts","reason":"size 256"}],"request_id":"req_702","timestamp":"2026-08-08T15:01:00.000Z"}
```

- **业务错误码：**`EMBEDDING_INPUT_EMPTY` 输入为空；`EMBEDDING_BATCH_TOO_LARGE` 批次过大；`MODEL_ROUTE_NOT_FOUND` 无向量模型；`MODEL_UPSTREAM_TIMEOUT` 上游超时；`MODEL_BUDGET_EXCEEDED` 超预算。

### API-MDL-03 竞品重排序（内部）

- **可见性：**内部封装接口；优先使用百炼可用的 Rerank 路由，无专用模型时可按配置降级为结构化推理。
- **路径/方式：**`POST /internal/v1/model-router/rerank`
- **说明：**根据产品画像和候选竞品的功能、风格、价格、材质及场景进行重排。
- **请求头：**同 API-MDL-01。
- **路径参数/Query 参数：**无。
- **Body：**`tenant_id`（integer，是）；`task_uuid`（string，是）；`query`（object，是，产品画像摘要）；`candidates`（array<object>，是，1—200 条，含 `listing_id` 和标准化属性）；`top_n`（integer，是，1—100）；`criteria_weights`（object，是，六项权重之和为 1）。
- **成功响应 `200`：**

```json
{"code":"0","message":"success","data":{"model_run_id":1103,"items":[{"listing_id":301,"rerank_score":0.91,"competitor_type":"direct","reasons":["模块结构一致","价格带重叠","小户型场景一致"]}]},"request_id":"req_703","timestamp":"2026-08-08T15:02:00.000Z"}
```

- **失败响应 `422`：**

```json
{"code":"RERANK_CANDIDATE_INVALID","message":"候选竞品缺少 listing_id","details":[{"field":"candidates[2].listing_id","reason":"required"}],"request_id":"req_703","timestamp":"2026-08-08T15:02:00.000Z"}
```

- **业务错误码：**`RERANK_CANDIDATE_INVALID` 候选数据错误；`RERANK_BATCH_TOO_LARGE` 候选过多；`RERANK_WEIGHT_INVALID` 权重非法；`MODEL_ALL_FALLBACKS_FAILED` 重排和降级均失败。

### 10.2 对外阿里云百炼调用约定

该调用不是 FurniScope 对前端提供的 REST 接口，而是 `AliyunBailianAdapter` 发起的外部依赖调用。实际 URL、模型标识和鉴权字段以赛事部署时开通的百炼 Model Router 官方配置为准，不在业务代码硬编码。

| 项目 | 约定 |
|---|---|
| 调用方 | 仅 FurniScope Model Router 服务 |
| 鉴权 | 服务端密钥管理系统注入百炼 API Key；日志全量脱敏 |
| 请求映射 | 内部 `task_type + routing_policy` 映射为百炼路由参数和消息体 |
| 超时 | 连接 3 秒；生成默认 60 秒；Embedding/Rerank 默认 20 秒，可按任务覆盖 |
| 重试 | 仅对超时、429、部分 5xx 重试；指数退避加抖动，最多 2 次 |
| 幂等 | `model_run_id + attempt_no`；生成任务禁止因客户端超时无限重放 |
| 降级 | 主路由失败后按允许的质量档位降级；结构化结果仍必须通过同一 Schema |
| 数据安全 | 上传前去除密码、API Key、无关个人信息；敏感价格数据按最小必要原则传输 |
| 可观测性 | 保存实际模型、Token、延迟、状态、费用估算；不保存密钥和完整敏感响应 |

外部上游异常统一映射：

| 上游情况 | FurniScope 错误码 | 处理 |
|---|---|---|
| 百炼认证失败 | `MODEL_UPSTREAM_AUTH_FAILED` | 不重试，告警并熔断该凭证 |
| 429 限流 | `MODEL_UPSTREAM_RATE_LIMITED` | 有界退避重试，任务保持可重试 |
| 超时/5xx | `MODEL_UPSTREAM_TIMEOUT` / `MODEL_UPSTREAM_ERROR` | 有界重试后尝试降级 |
| 内容安全拒绝 | `MODEL_CONTENT_BLOCKED` | 不重试，返回可解释原因 |
| 返回非预期 JSON | `MODEL_OUTPUT_SCHEMA_INVALID` | 允许一次修复 Prompt 重试 |

---

## 11. 统一业务错误码

| 错误码范围 | 模块 | 典型错误码 |
|---|---|---|
| `AUTH_*` | 认证 | `AUTH_TOKEN_INVALID`、`AUTH_INVALID_CREDENTIALS` |
| `TENANT_*` / `PERMISSION_*` | 租户与授权 | `TENANT_CONTEXT_MISMATCH`、`PERMISSION_DENIED` |
| `DASHBOARD_*` | 工作台 | `DASHBOARD_FILTER_INVALID`、`DASHBOARD_QUERY_FAILED` |
| `PRODUCT_*` | 产品 | `PRODUCT_NOT_FOUND`、`PRODUCT_PROFILE_NOT_CONFIRMED` |
| `DATASET_*` / `COMPETITOR_*` | 市场数据与竞品 | `DATASET_NOT_READY`、`COMPETITOR_NOT_IN_TASK` |
| `REVIEW_*` / `INSIGHT_*` | 评论分析 | `REVIEW_ANALYSIS_NOT_READY`、`INSIGHT_CLUSTER_NOT_FOUND` |
| `TASK_*` / `OPPORTUNITY_*` | AI 洞察任务 | `TASK_ALREADY_STARTED`、`OPPORTUNITY_RESULT_NOT_READY` |
| `RECOMMENDATION_*` / `COST_*` | 产品建议 | `RECOMMENDATION_NOT_FOUND`、`COST_EVIDENCE_REQUIRED` |
| `LISTING_*` | Listing | `LISTING_COMPLIANCE_BLOCKED`、`LISTING_GENERATION_FAILED` |
| `REPORT_*` / `EXPORT_*` | 报告 | `REPORT_VALIDATION_PENDING`、`EXPORT_FILE_EXPIRED` |
| `MODEL_*` / `EMBEDDING_*` / `RERANK_*` | 模型路由 | `MODEL_UPSTREAM_TIMEOUT`、`MODEL_OUTPUT_SCHEMA_INVALID` |
| `WORKFLOW_*` / `STAGE_*` | 工作流恢复 | `WORKFLOW_CHECKPOINT_CONFLICT`、`STAGE_NOT_RETRYABLE` |
| `RESOURCE_*` / `IDEMPOTENCY_*` | 通用并发 | `RESOURCE_VERSION_CONFLICT`、`IDEMPOTENCY_CONFLICT` |

业务错误码必须稳定，不允许前端依赖自然语言 `message` 判断逻辑。相同业务错误在同步和异步链路中使用同一错误码。

---

## 12. 安全、幂等与非功能约束

### 12.1 权限和数据隔离

- 所有业务查询必须同时包含 `tenant_id` 条件；不得只凭资源 ID 查询。
- 管理者、产品/研发、市场运营、外贸和管理员按 PRD 权限矩阵授权。
- 出厂价、成本、MOQ、供应商报价属于敏感字段；外贸只读角色默认不返回。
- 对象存储文件使用 15 分钟短效签名 URL；数据库仅保存对象键，不保存永久公网地址。

### 12.2 幂等和并发

- 创建任务、启动分析、竞品集合确认、阶段重试、任务取消、模型调用、生成 Listing、发布和导出必须使用 `Idempotency-Key`。
- 同一幂等键与相同请求体返回原结果；请求体不同返回 `409 IDEMPOTENCY_CONFLICT`。
- 产品画像、竞品复核、建议审核和 Listing 审核使用 `If-Match` 乐观锁。
- 已发布报告及其引用结果不可原地覆盖，只能创建新任务或新报告版本。
- `competitors:confirm` 和专家复核恢复必须校验任务状态、Checkpoint 与业务版本，在事务中写入恢复 Outbox；不得在数据库提交前直接恢复 LangGraph。
- 阶段重试只允许从最近安全 Checkpoint 执行；成功批次和业务结果必须通过唯一键复用。

### 12.3 性能目标

| 场景 | Demo 目标 |
|---|---|
| 普通列表/详情 API | P95 ≤ 500 ms，不含外部模型调用 |
| 文件导入受理 | ≤ 2 秒返回异步任务 ID |
| 任务状态轮询 | P95 ≤ 300 ms；前端建议 2—5 秒轮询一次 |
| 1,000 条评论完整分析 | 目标 ≤ 5 分钟 |
| Model Router 可用性 | Demo 展示期 ≥ 99%，具备超时、重试和降级 |
| 报告导出受理 | ≤ 2 秒；生成过程异步完成 |

### 12.4 Demo 联调顺序

1. 登录并创建/查询产品；
2. 上传资料、修正并确认画像；
3. 创建数据集、导入竞品与评论、查看质量结果；
4. 创建并启动分析任务、轮询状态；
5. 使用 API-CMP-06 复核竞品，并通过 API-CMP-07 从 `competitor_review` Checkpoint 恢复；
6. 通过扩展后的 API-INS-03 查看并行 Stage、部分失败、跳过、恢复点和报告 UUID；
7. 查看评论聚类、机会和建议，完成专家复核并恢复证据审计；
8. 发布并导出报告；
9. P1 再接入阶段重试、任务取消、导出历史和 Listing 生成审核。

---

## 13. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-08 | 基于 FurniScope SRS/PRD、产品数据字典和初版数据库设计，完成比赛级 RESTful API 设计 |
| V2.0 | 2026-08-08 | 新增工作台、解析任务、任务竞品查询、竞品确认恢复、阶段重试、任务取消和导出历史接口；扩展 API-INS-03，并对齐 LangGraph 两个人工中断点 |
