# FurniScope 产品数据字典 V3.0

> 2026-10-05企业闭环复核增量已纳入。总体权威依据为[15总体设计](./15_FurniScope_企业决策与数据闭环总体设计V1.md)，新增实体和API详细契约见[实现说明](../开发文档/FurniScope_企业决策与标准数据API实现说明V1.md)；历史表字段未涉及者继续适用。

### 企业闭环新增实体

| 实体/字段 | 类型与含义 | 强制约束 |
|---|---|---|
| `enterprise_opportunity_policies` | tenant_id、version、config、created_by、created_at | 租户+版本唯一；config含名称、五权重、适配强度和必要能力；不可变 |
| `opportunity_feedback_events` | opportunity_id、analysis_job_id、revision、status、reason、score_snapshot、创建人/时间 | 同租户关联；事件不可变；状态accepted/rejected/pending_validation；原因必填 |
| `market_opportunities.decision_snapshot` | opportunity-features-v2、captured_at、完整opportunity和task事实 | 首次评分由数据库捕获；不可修改或删除；历史缺失不回填 |
| `opportunity_outcome_events` | accepted_feedback_id、revision、实施状态与日期、观察区间、result_label、evidence、financials、sales_snapshot | 同租户复合关联；不可变修订；新写关联最新采纳；业务标签排除回溯、未完成及过期关联 |
| `forecast_data_versions` | version_uuid、原始/标准文件引用与SHA256、rules、quality、columns_info、parent_version_id、auxiliary_sources、template_snapshot | 同租户父子关联；状态uploaded/previewed/confirmed；已确认记录不可原地修改；辅助版本UUID/SHA及模板修订冻结 |
| `forecast_import_templates` | template_uuid、tenant_id、name、revision、rules、columns_info、source_version_id、created_by | 来自本企业已确认数据；名称+修订唯一，修订不可变；来源及创建人复合租户外键 |
| `forecast_training_runs`新增字段 | data_version_id、parent_data_version_id、baseline_deployment_id、code_sha256、execution_token、execution_attempts、heartbeat_at、lease_expires_at | 同租户血缘与部署CAS；initial/append/rebuild；rejected；失效执行不得发布 |
| `tenant_forecast_sku_aliases` | tenant_id、source_context、product_sku、source_sku | 企业一一映射；旧forecast_sku_aliases不用于新企业训练 |
| `forecast_model_deployments.route_policy` | catalog_snapshot、血缘与路由策略 | 部署冻结目录，回滚原子恢复 |
| `forecast_jobs.routing_snapshot` | 产品SKU/参考SKU到来源SKU的路由 | 随模型在入队时冻结，后续目录变化不影响已排队预测 |
| `forecast_models`归属 | owner_tenant_id、model_scope | owner不可变；私有模型部署租户必须等于owner |
| `analysis_tasks.enterprise_profile_snapshot`增量 | opportunity_policy、product_facts、企业画像和能力、captured_at | 新任务创建时冻结，重试不重新读取当前策略 |

## 1. 文档说明

| 项目 | 内容 |
|---|---|
| 产品 | FurniScope——跨境家具超级 AI 员工 |
| 文档版本 | V3.0 |
| 文档状态 | 数据设计与后续 PostgreSQL/API 实现基线 |
| 上位依据 | PRD V2、Agent 工作流 V2、产品简化决策基线 V1 |
| 数据库基线 | PostgreSQL；物理表名使用复数 snake_case |
| 数据层次 | 数据库实体字段、Agent State 字段、API 衍生字段 |

本字典只简化组织、交互和过度范式化结构，不削弱产品画像、竞品可比性、评论原文与证据 Span、机会评分、独立置信度、企业制造适配、模型追溯、证据链、任务幂等、部分失败、Checkpoint 和安全恢复。

### 1.1 字段表规则

所有字段表统一包含：字段名称、字段释义、数据类型、必填、数据来源、使用场景和约束规则。“条件”表示满足约束中说明的条件时必填。

### 1.2 通用类型

| 类型 | 说明 |
|---|---|
| `BIGINT` | PostgreSQL 内部主键或外键，不直接对外暴露 |
| `UUID` | API、跨系统或长期稳定的公开标识 |
| `STRING(n)` | 最大长度为 n 的字符串，对应 `VARCHAR(n)` |
| `TEXT` | 长文本 |
| `DECIMAL(p,s)` | 精确数值，不使用浮点存储金额和评分 |
| `DATETIME` | UTC 时间，对应 `TIMESTAMPTZ` |
| `DATE` | 业务日期 |
| `ENUM` | 受控字符串枚举，由数据库 CHECK 或应用校验 |
| `JSON` | 有版本化 Schema 的 `JSONB` |
| `ARRAY<T>` | PostgreSQL 数组或受 Schema 约束的 JSON 数组 |
| `VECTOR` | pgvector 向量 |

### 1.3 数据来源标识

- **数据库：**持久化主事实或事务结果。
- **API 输入：**由当前 `user` 或 `admin` 提交并经鉴权校验。
- **Agent State：**LangGraph 运行态，只保存轻量引用和控制状态。
- **规则计算：**确定性程序、SQL 或版本化算法。
- **AI/Model Router：**受 Prompt、Schema 和证据约束的模型输出。
- **API 衍生：**查询聚合或投影，不反写源实体。

### 1.4 全局规则

1. 系统角色只能为 `user/admin`；岗位字段只可描述用户画像，不参与鉴权。
2. 所有租户业务查询强制带 `tenant_id`，关联对象必须同租户。
3. 内部 `id` 使用 BIGINT，任务和报告等对外对象同时使用 UUID。
4. 时间统一存 UTC；金额必须带币种；比例和置信度范围为 0—1；业务分范围为 0—100。
5. JSON 字段必须绑定 Schema 版本，不能作为绕过字段治理的自由容器。
6. 原始评论不可被翻译或 AI 内容覆盖；证据 Span 使用原文 Unicode 字符偏移。
7. 大文本、文件和向量不进入 LangGraph State 或审计快照。
8. 所有模型结果可追溯到模型、Prompt、输入哈希和 Schema 版本。

---

## 2. 数据库公共字段

除纯关联表和 LangGraph 官方内部表外，核心数据库实体默认包含下列字段。后续各实体字段表只列业务字段，公共字段视为实体完整字段的一部分。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 内部主键 | BIGINT | 是 | 数据库 | 内部关联 | Identity 主键，API不暴露 |
| `tenant_id` | 租户边界 | BIGINT | 条件 | 认证上下文/数据库 | 数据隔离 | 租户业务实体必填；外键指向`tenants.id` |
| `created_at` | 创建时间 | DATETIME | 是 | 数据库 | 审计、排序 | 默认当前UTC，不可回写 |
| `updated_at` | 更新时间 | DATETIME | 是 | 数据库 | 并发、同步 | 每次有效更新自动刷新 |
| `created_by` | 创建用户 | BIGINT | 条件 | 认证上下文 | 审计 | 用户创建对象必填；系统创建可空 |
| `updated_by` | 最后更新用户 | BIGINT | 否 | 认证上下文 | 审计 | 系统更新可空 |

---

## 3. 租户、用户与审计实体

### 3.1 企业租户 `tenants`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `tenant_code` | 租户编码 | STRING(32) | 是 | admin/系统 | 运维识别 | 全局唯一，大写字母、数字和下划线 |
| `name` | 企业名称 | STRING(200) | 是 | admin | 页面、报告 | 不得为空 |
| `display_name` | 企业简称 | STRING(100) | 否 | admin | UI展示 | 空时使用`name` |
| `industry` | 行业 | ENUM | 是 | admin | 本体路由 | P0固定`furniture_manufacturing` |
| `default_timezone` | 默认时区 | STRING(64) | 是 | admin | 时间展示 | IANA时区 |
| `default_currency` | 默认币种 | STRING(3) | 是 | admin | 金额默认口径 | ISO 4217 |
| `status` | 租户状态 | ENUM | 是 | admin | 访问控制 | 沿用`trial/active/suspended/closed` |
| `data_retention_days` | 默认保留天数 | INT | 是 | admin | 数据清理 | >0且不超过合规上限 |

### 3.2 系统用户 `users`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `tenant_id` | 所属租户 | BIGINT | 是 | admin/数据库 | 数据隔离 | 外键；admin账号仍需归属平台管理租户 |
| `email` | 登录邮箱 | STRING(254) | 是 | user/admin | 登录并自动定位租户 | 全系统唯一，小写归一化；服务端由user记录解析tenant_id |
| `password_hash` | 密码派生哈希 | STRING(255) | 是 | 认证系统 | 密码校验 | 仅存Argon2id/bcrypt等自描述哈希；无默认值；禁止明文、日志和API输出 |
| `phone` | 手机号 | STRING(32) | 否 | user | 联系、安全 | E.164；敏感字段 |
| `name` | 用户姓名 | STRING(100) | 是 | user/admin | 展示、审计 | 1—100字符 |
| `department` | 现实部门描述 | STRING(100) | 否 | user | 目标用户画像 | 不得用于权限、路由或审批 |
| `job_title` | 现实岗位描述 | STRING(100) | 否 | user | 目标用户画像 | 可写owner/研发/运营等文本，但不作为枚举 |
| `role_code` | 系统角色 | ENUM | 是 | admin | 静态鉴权 | **仅允许`user/admin`**；一个账号一个角色 |
| `locale` | 界面语言 | STRING(16) | 是 | user | 国际化 | BCP 47；默认`zh-CN` |
| `timezone` | 用户时区 | STRING(64) | 否 | user | 时间展示 | 空时继承租户 |
| `status` | 用户状态 | ENUM | 是 | admin | 账号管理 | 沿用`invited/active/disabled/locked` |
| `last_login_at` | 最后登录时间 | DATETIME | 否 | 认证系统 | 安全审计 | 登录成功后更新 |

> V3 删除 `roles`、`user_roles`，不保留动态权限集、角色继承或多角色分配。密码与会话仍属于认证子系统，但其必要持久化字段进入本字典；任何Token明文不得落库。

### 3.3 认证会话 `auth_sessions`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `session_uuid` | 会话公开标识 | UUID | 是 | 认证系统 | 会话审计、撤销 | 全局唯一，不作为Token |
| `tenant_id` | 租户边界 | BIGINT | 是 | 认证上下文 | 隔离 | 外键，与user一致 |
| `user_id` | 会话用户 | BIGINT | 是 | 认证系统 | Token主体 | 外键；删除user级联删除会话 |
| `refresh_token_hash` | Refresh Token单向摘要 | CHAR(64) | 是 | 认证系统 | 刷新校验、重放检测 | SHA-256十六进制且唯一；Token明文不落库 |
| `token_family_uuid` | 轮换家族标识 | UUID | 是 | 认证系统 | 复用检测后撤销整族 | 同一登录链路保持一致 |
| `expires_at` | 过期时间 | DATETIME | 是 | 认证策略 | 有效性判断 | 必须晚于created_at |
| `last_used_at` | 最后使用时间 | DATETIME | 否 | 认证系统 | 安全审计 | 每次成功刷新更新 |
| `revoked_at` | 撤销时间 | DATETIME | 否 | 认证系统/admin | 登出、封禁、重放处置 | 与revoke_reason同时为空或同时有值 |
| `revoke_reason` | 撤销原因码 | STRING(64) | 否 | 认证系统/admin | 安全审计 | 脱敏稳定码，不写Token |
| `replaced_by_session_uuid` | 轮换后的新会话 | UUID | 否 | 认证系统 | Token轮换链 | 指向后继公开标识；旧会话先撤销再返回新Token |

认证规则：Access Token采用短期签名Token且不落库；Refresh Token每次使用即轮换，仅其摘要入库；检测旧Token复用时撤销同一`token_family_uuid`的全部未撤销会话。

### 3.4 API幂等记录 `api_idempotency_records`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `tenant_id` | 租户边界 | BIGINT | 是 | 认证上下文 | 幂等隔离 | 客户端不得提交 |
| `actor_user_id` | 发起用户 | BIGINT | 是 | 认证上下文 | 审计 | 外键；仅业务写接口使用 |
| `route_code` | 稳定接口编号 | STRING(32) | 是 | 路由元数据 | 幂等作用域 | 使用`API-PRD-01`等正式编号 |
| `http_method` | HTTP方法 | ENUM | 是 | 路由 | 防错误复用 | `POST/PUT/PATCH/DELETE` |
| `idempotency_key` | 客户端幂等键 | STRING(128) | 是 | Header | 并发去重 | tenant+route_code+key唯一 |
| `request_hash` | 规范化请求摘要 | CHAR(64) | 是 | API服务 | 区分相同/冲突请求 | SHA-256；排除Authorization、Request-ID及文件字节，文件使用sha256清单 |
| `status` | 处理状态 | ENUM | 是 | API服务 | 并发裁决 | `processing/completed/failed` |
| `resource_type` | 结果资源类型 | STRING(64) | 否 | Service | 原结果定位 | 创建/受理成功时填写 |
| `resource_public_id` | 结果公开标识 | STRING(128) | 否 | Service | 原结果定位 | 不保存内部敏感标识 |
| `response_status` | 原HTTP状态 | INT | 条件 | API服务 | 重放响应 | completed/failed必填，200—599 |
| `response_body` | 原脱敏Envelope | JSON | 条件 | API服务 | 相同请求重放 | completed/failed必填；禁止Token、密码、文件、评论全文和密钥 |
| `expires_at` | 幂等记录过期时间 | DATETIME | 是 | 系统策略 | 清理 | 默认创建后24小时；必须晚于created_at |

统一规则：相同作用域和Key且`request_hash`相同，processing返回409 `IDEMPOTENCY_IN_PROGRESS`，完成后返回原状态与Envelope；hash不同返回409 `IDEMPOTENCY_CONFLICT`。认证登录/刷新不进入本表。

### 3.5 审计日志 `audit_logs`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `actor_user_id` | 操作用户 | BIGINT | 否 | 认证上下文 | 责任追溯 | 系统行为可空 |
| `action_code` | 操作码 | STRING(100) | 是 | 系统 | 审计筛选 | 使用注册动作码 |
| `resource_type` | 资源类型 | STRING(64) | 是 | 系统 | 对象定位 | 使用本字典稳定实体名 |
| `resource_id` | 资源内部ID | BIGINT | 否 | 系统 | 对象定位 | 资源存在时填写 |
| `request_id` | 请求链路标识 | STRING(64) | 否 | 网关 | 故障定位 | 同链路一致 |
| `before_snapshot` | 变更前摘要 | JSON | 否 | 系统 | 变更追溯 | 脱敏；不存密钥、评论全文、Prompt正文 |
| `after_snapshot` | 变更后摘要 | JSON | 否 | 系统 | 变更追溯 | 同上 |
| `ip_address` | 请求IP | STRING(45) | 否 | 网关 | 安全审计 | 按保留策略清理 |
| `user_agent` | 客户端信息 | STRING(512) | 否 | HTTP请求 | 安全排查 | 最大512字符 |
| `occurred_at` | 事件时间 | DATETIME | 是 | 系统 | 时序追溯 | UTC、不可修改 |

---

## 4. 企业能力与产品实体

### 4.1 企业档案 `enterprise_profiles`

企业成本与交付约束不再使用独立 `enterprise_constraints`，合并到 `constraints`。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `tenant_id` | 租户ID | BIGINT | 是 | 数据库 | 一租户一当前档案 | 唯一外键 |
| `business_model` | 业务模式 | ARRAY<ENUM> | 是 | user | 业务语境 | `B2B/B2C/OEM/ODM/brand`可多选 |
| `primary_categories` | 主营品类 | ARRAY<STRING> | 是 | user | 产品和市场匹配 | 使用品类本体码 |
| `export_markets` | 已出口市场 | ARRAY<STRING(2)> | 否 | user | 市场经验匹配 | ISO国家代码 |
| `sales_channels` | 销售渠道 | ARRAY<ENUM> | 否 | user | 场景理解 | 沿用V2渠道枚举 |
| `annual_capacity_note` | 产能说明 | TEXT | 否 | user | 交付语境 | 不直接计分 |
| `constraints` | 成本与交付约束集合 | JSON | 是 | user/数据库 | 机会门禁、企业适配 | 默认`[]`；每项沿用`constraint_type/operator/value/unit/hardness/effective_at/expires_at/sensitivity_level` Schema；禁止自由字段 |
| `profile_completeness` | 档案完整度 | DECIMAL(5,4) | 是 | 规则计算 | 适配置信度 | 0—1 |
| `confirmed_by` | 最后确认user | BIGINT | 否 | 认证上下文 | 来源等级 | 与`confirmed_at`同时有值或同时为空 |
| `confirmed_at` | 最后确认时间 | DATETIME | 否 | 系统 | 新鲜度 | UTC |

### 4.2 制造能力 `manufacturing_capabilities`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `capability_type` | 能力类型 | ENUM | 是 | user/本体 | 适配评分 | `category/material/process/customization/packaging/certification/delivery/equipment` |
| `capability_code` | 标准能力码 | STRING(100) | 是 | 本体/user | 规则匹配 | 租户+类型内唯一 |
| `capability_name` | 能力名称 | STRING(200) | 是 | 本体/user | 展示 | 不用自由文本代替标准码 |
| `availability` | 是否具备 | ENUM | 是 | user | 能力门禁 | `yes/no/unknown` |
| `min_value` | 能力下限 | DECIMAL(18,4) | 否 | user | 范围匹配 | 数值能力使用 |
| `max_value` | 能力上限 | DECIMAL(18,4) | 否 | user | 范围匹配 | ≥`min_value` |
| `unit` | 单位 | STRING(32) | 条件 | user/系统 | 单位换算 | 有数值时必填 |
| `valid_from` | 生效日期 | DATE | 否 | user | 新鲜度 | 不晚于`valid_until` |
| `valid_until` | 失效日期 | DATE | 否 | user | 过期判断 | 过期后不支持高置信结论 |
| `evidence_asset_id` | 证明文件 | BIGINT | 否 | user | 能力追溯 | 外键`file_assets.id`，同租户 |
| `source_type` | 来源类型 | ENUM | 是 | 系统/user | 置信度 | `confirmed_user/document/imported/inferred` |
| `confidence` | 能力置信度 | DECIMAL(5,4) | 是 | 规则计算 | 适配置信度 | 0—1；未知能力降低置信度 |
| `notes` | 能力说明 | TEXT | 否 | user | 上下文 | 不直接计分 |

### 4.3 产品主档 `products`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `sku` | 企业SKU | STRING(100) | 是 | user | 产品识别 | 租户内唯一 |
| `name` | 产品名称 | STRING(200) | 是 | user | 页面、报告 | AI不得覆盖原名 |
| `category_code` | 标准品类码 | STRING(100) | 是 | user确认/AI建议 | 竞品匹配 | 必须存在于品类本体 |
| `lifecycle_status` | 生命周期 | ENUM | 是 | user | 产品筛选 | `concept/sample/active/discontinued` |
| `analysis_status` | 分析准备状态 | ENUM | 是 | 系统 | 任务门禁 | `draft/profile_pending/ready/archived` |
| `primary_image_id` | 主图资产 | BIGINT | 条件 | user | 多模态、UI | 多模态分析时必填，同租户 |
| `current_profile_version_id` | 当前画像版本 | BIGINT | 否 | 系统 | 默认分析输入 | 外键`product_profile_versions.id` |
| `description` | 补充说明 | TEXT | 否 | user | 产品上下文 | 建议≤5000字符 |

### 4.4 文件资产 `file_assets`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `owner_type` | 所属对象类型 | ENUM | 是 | 系统 | 资产组织 | 核心仅`product/capability/dataset/other`；不含核心report导出 |
| `owner_id` | 所属对象ID | BIGINT | 是 | 系统 | 资产关联 | 与owner_type同租户 |
| `asset_type` | 文件类型 | ENUM | 是 | user/系统 | 解析路由 | `image/pdf/spreadsheet/csv/json/cad/other` |
| `original_filename` | 原文件名 | STRING(255) | 是 | API输入 | 展示 | 去除路径字符 |
| `storage_key` | 对象存储键 | STRING(512) | 是 | 系统 | 文件读取 | 全局唯一，不直接暴露 |
| `mime_type` | MIME类型 | STRING(100) | 是 | 文件检测 | 安全、解析 | 与文件真实内容一致 |
| `size_bytes` | 文件大小 | BIGINT | 是 | 系统 | 安全、配额 | >0且不超过类型上限 |
| `sha256` | 文件哈希 | STRING(64) | 是 | 系统 | 去重、完整性 | 小写SHA-256 |
| `security_status` | 安全状态 | ENUM | 是 | 安全扫描 | 解析门禁 | `pending/clean/rejected/quarantined` |
| `parse_status` | 解析状态 | ENUM | 是 | 解析服务 | 进度 | `not_started/processing/succeeded/partial/failed` |
| `retention_until` | 保留截止 | DATETIME | 否 | 系统 | 数据清理 | 按租户策略 |

### 4.5 产品资料解析任务 `product_parse_jobs`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `parse_job_id` | 对外解析任务UUID | UUID | 是 | 数据库 | API轮询 | 唯一、不可变 |
| `product_id` | 产品ID | BIGINT | 是 | API输入 | 解析归属 | 同租户外键 |
| `status` | 总体状态 | ENUM | 是 | 解析Supervisor | 轮询 | 沿用`queued/running/partial_succeeded/succeeded/failed` |
| `current_stage` | 当前解析阶段 | STRING(64) | 是 | 解析Supervisor | 进度 | 使用既有解析阶段码，不作为主分析五阶段 |
| `progress_percent` | 进度 | DECIMAL(5,2) | 是 | 系统 | UI | 0—100 |
| `file_count` | 文件总数 | INT | 是 | 程序 | 汇总 | >0 |
| `succeeded_file_count` | 成功文件数 | INT | 是 | 程序 | 汇总 | 0—file_count |
| `failed_file_count` | 失败文件数 | INT | 是 | 程序 | 汇总 | 0—file_count |
| `retryable` | 是否可自动恢复 | BOOLEAN | 是 | 错误分类 | Supervisor | 不向user暴露细粒度重试 |
| `failure_code` | 失败码 | STRING(100) | 条件 | 解析服务 | 错误反馈 | 失败时必填 |
| `failure_message` | 脱敏失败说明 | STRING(1000) | 条件 | 解析服务 | UI | 不含堆栈和敏感正文 |
| `idempotency_key` | 创建幂等键 | STRING(128) | 是 | API/系统 | 防重复任务 | 租户内唯一 |
| `started_at` | 开始时间 | DATETIME | 否 | 系统 | 性能 | UTC |
| `completed_at` | 完成时间 | DATETIME | 否 | 系统 | 性能 | 终态填写 |

### 4.6 产品解析文件结果 `product_parse_job_files`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `parse_job_id` | 解析任务ID | BIGINT | 是 | 系统 | 父任务关联 | 外键 |
| `file_asset_id` | 文件资产ID | BIGINT | 是 | API输入 | 文件关联 | 同任务内唯一 |
| `security_status` | 文件安全结果 | ENUM | 是 | 安全扫描 | 文件反馈 | 与资产最终安全状态一致 |
| `parse_status` | 文件解析结果 | ENUM | 是 | 解析服务 | 文件反馈 | 沿用文件解析枚举 |
| `output_ref` | 解析结果引用 | JSON | 否 | 解析服务 | 产品画像生成 | 只存ID/对象键/Schema版本 |
| `error_code` | 文件错误码 | STRING(100) | 条件 | 解析服务 | 局部失败 | 失败时必填 |
| `error_message` | 文件错误说明 | STRING(1000) | 条件 | 解析服务 | UI | 脱敏 |

### 4.7 产品画像版本 `product_profile_versions`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `product_id` | 产品ID | BIGINT | 是 | 系统 | 版本归属 | 外键 |
| `version_no` | 版本号 | INT | 是 | 系统 | 冻结输入 | 产品内递增、唯一 |
| `schema_version` | 画像Schema版本 | STRING(64) | 是 | 系统 | 可复现 | 创建后不可改 |
| `status` | 画像状态 | ENUM | 是 | 系统/user | 任务门禁 | `draft/parsed/confirmed/superseded` |
| `completeness_score` | 完整度 | DECIMAL(5,4) | 是 | 规则计算 | 确认触发 | 0—1 |
| `conflict_count` | 冲突数 | INT | 是 | 规则计算 | 冲突确认 | ≥0 |
| `source_summary` | 来源摘要 | JSON | 是 | 解析服务 | 追溯 | 只存资产ID和来源计数 |
| `confirmed_by` | 确认user | BIGINT | 条件 | 认证上下文 | 事实确认 | status=confirmed时必填 |
| `confirmed_at` | 确认时间 | DATETIME | 条件 | 系统 | 新鲜度 | 与confirmed_by联动 |

### 4.8 产品属性 `product_attributes`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `profile_version_id` | 画像版本ID | BIGINT | 是 | 系统 | 属性归属 | 外键；版本+属性码唯一 |
| `attribute_code` | 标准属性码 | STRING(100) | 是 | Schema | 竞品匹配 | 存在于沙发属性本体 |
| `value` | 属性值 | JSON | 是 | user/资料/AI | 产品画像 | 按属性Schema校验 |
| `unit` | 单位 | STRING(32) | 条件 | 系统/资料 | 单位统一 | 数值属性必填 |
| `source_type` | 来源类型 | ENUM | 是 | 系统 | 来源优先级 | `confirmed_structured/user_input/document/image/inferred` |
| `source_asset_id` | 来源文件 | BIGINT | 否 | 解析服务 | 证据下钻 | 同租户外键 |
| `source_locator` | 来源位置 | JSON | 否 | 解析服务 | 页码/单元格/图像区域 | 不存文件正文 |
| `confidence` | 属性置信度 | DECIMAL(5,4) | 是 | 规则/AI | 冲突判断 | 0—1 |
| `confirmation_status` | 确认状态 | ENUM | 是 | 系统/user | 阻断判断 | `unconfirmed/confirmed/conflicted/unknown` |

---

## 5. 市场、竞品与评论实体

### 5.1 市场数据集 `market_datasets`

字段映射合并到 `field_mapping`，不再保留 `dataset_field_mappings`。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `name` | 数据集名称 | STRING(200) | 是 | user | 任务选择 | 租户内建议唯一 |
| `version_no` | 数据集版本 | INT | 是 | 系统 | 报告复现 | 数据集系列内递增 |
| `platform` | 平台 | ENUM | 是 | user/数据源 | 市场口径 | 沿用V2平台枚举 |
| `market_country` | 目标国家 | STRING(2) | 是 | 数据源 | 市场筛选 | ISO 3166-1 alpha-2 |
| `marketplace_code` | 站点码 | STRING(50) | 否 | 数据源 | 站点区分 | 如amazon.com |
| `category_code` | 内部品类码 | STRING(100) | 是 | 映射/user | 样本口径 | 必须在家具本体中 |
| `data_start_date` | 数据开始日 | DATE | 否 | 数据源 | 时间范围 | 趋势时必填 |
| `data_end_date` | 数据结束日 | DATE | 是 | 数据源 | 新鲜度 | ≥开始日且不晚于导入日 |
| `source_type` | 来源类型 | ENUM | 是 | user | 合规 | `enterprise_export/licensed_provider/public_authorized/demo_synthetic` |
| `source_name` | 来源名称 | STRING(200) | 是 | user | 报告证据 | Demo不得伪装官方数据 |
| `authorization_reference` | 授权依据 | TEXT | 条件 | user/admin | 合规审计 | 非公开授权数据必填 |
| `import_asset_id` | 原始导入文件 | BIGINT | 是 | 系统 | 数据追溯 | 外键file_assets |
| `field_mapping` | 导入字段映射 | JSON | 是 | user/系统 | 字段转换 | 数组项沿用`source_field/target_entity/target_field/transform_rule/mapping_status`；转换仅白名单 |
| `status` | 数据集状态 | ENUM | 是 | 系统 | 任务门禁 | `uploaded/validating/ready/rejected/archived` |
| `listing_count` | 商品数 | INT | 是 | 规则计算 | 样本范围 | ≥0 |
| `review_count` | 评论总数 | INT | 是 | 规则计算 | 样本范围 | ≥0 |
| `valid_review_count` | 有效评论数 | INT | 是 | 规则计算 | 比例分母 | 0—review_count |
| `quality_score` | 数据质量分 | DECIMAL(5,2) | 是 | 规则计算 | 置信度 | 0—100，规则版本化 |
| `quality_report` | 质量报告 | JSON | 是 | 规则计算 | 数据预览 | 含缺失、重复、异常、语言和关联完整度 |
| `limitations` | 数据限制 | ARRAY<TEXT> | 是 | 规则/user | 报告风险 | 可为空数组；原样进入报告 |

### 5.2 市场商品 `market_listings`

标准属性合并到 `normalized_attributes`，删除 `listing_attributes` 实体。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `dataset_id` | 数据集ID | BIGINT | 是 | 系统 | 样本范围 | 外键 |
| `platform_listing_id` | 平台商品ID | STRING(200) | 是 | 数据源 | 去重、追溯 | 数据集内唯一 |
| `listing_url` | 商品地址 | STRING(1000) | 否 | 数据源 | 证据跳转 | HTTP/HTTPS且遵守授权 |
| `brand` | 品牌 | STRING(200) | 否 | 数据源 | 品牌集中度 | 保留原文 |
| `seller_name` | 销售方名称 | STRING(200) | 否 | 数据源 | 竞争分析 | 不作为联系人数据 |
| `title` | 商品标题原文 | TEXT | 是 | 数据源 | 匹配、证据 | 不被AI覆盖 |
| `description` | 商品描述原文 | TEXT | 否 | 数据源 | 卖点抽取 | 保留原文 |
| `bullet_points` | 卖点原文 | ARRAY<TEXT> | 否 | 数据源 | 功能渗透 | 保留顺序 |
| `category_raw` | 原平台品类 | STRING(300) | 否 | 数据源 | 映射追溯 | 不直接参与内部评分 |
| `category_code` | 内部品类码 | STRING(100) | 是 | 映射规则 | 硬过滤 | 本体有效码 |
| `image_urls` | 商品图片 | ARRAY<STRING> | 否 | 数据源 | 视觉匹配 | 仅保存有权使用链接 |
| `currency` | 币种 | STRING(3) | 是 | 数据源 | 价格统计 | ISO4217 |
| `list_price` | 标价 | DECIMAL(18,4) | 否 | 数据源 | 促销口径 | ≥0 |
| `sale_price` | 售价 | DECIMAL(18,4) | 是 | 数据源 | 价格带 | ≥0 |
| `coupon_value` | 优惠信息 | JSON | 否 | 数据源 | 净价估算 | 标明金额或比例 |
| `rating` | 平均评分 | DECIMAL(3,2) | 否 | 数据源 | 满意度 | 0—5，缺失不记0 |
| `rating_count` | 评分数 | INT | 否 | 数据源 | 样本规模 | ≥0 |
| `review_count` | 评论数 | INT | 否 | 数据源 | 热度代理 | ≥0，不等于销量 |
| `rank_value` | 排名值 | INT | 否 | 数据源 | 表现代理 | 必须带rank_category和captured_at |
| `rank_category` | 排名类目 | STRING(300) | 条件 | 数据源 | 排名口径 | rank_value有值时必填 |
| `captured_at` | 数据采集/导出时间 | DATETIME | 是 | 数据源 | 快照、趋势 | UTC |
| `first_available_date` | 首次可用日期 | DATE | 否 | 数据源 | 新品分析 | 不等同上架日时需说明 |
| `normalized_attributes` | 标准商品属性 | JSON | 是 | 规则/AI | 匹配与解释 | 默认`{}`；键为attribute_code；每项保留value/unit/source_locator/confidence；Schema版本化 |
| `raw_payload` | 脱敏原始载荷 | JSON | 否 | 数据源 | 导入排错 | 不存未授权个人数据 |

### 5.3 竞品匹配 `competitor_matches`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 分析任务ID | BIGINT | 是 | 系统 | 任务关联 | 外键 |
| `listing_id` | 市场商品ID | BIGINT | 是 | 系统 | 候选关联 | 同任务+商品唯一 |
| `product_profile_version_id` | 产品画像版本 | BIGINT | 是 | 任务冻结 | 可复现 | 外键 |
| `competitor_type` | 竞品类型 | ENUM | 是 | 规则/AI | 样本分层 | `direct/benchmark/substitute/excluded` |
| `category_score` | 品类相似分 | DECIMAL(5,2) | 是 | 规则 | 匹配解释 | 0—100 |
| `function_score` | 功能相似分 | DECIMAL(5,2) | 是 | 规则/AI属性 | 匹配解释 | 0—100 |
| `style_score` | 风格相似分 | DECIMAL(5,2) | 是 | 规则/AI属性 | 匹配解释 | 0—100 |
| `price_score` | 价格相似分 | DECIMAL(5,2) | 是 | 规则 | 匹配解释 | 0—100，币种统一 |
| `material_score` | 材质相似分 | DECIMAL(5,2) | 是 | 规则/AI属性 | 匹配解释 | 0—100 |
| `scenario_score` | 场景相似分 | DECIMAL(5,2) | 是 | 规则/AI属性 | 匹配解释 | 0—100 |
| `overall_score` | 综合相似分 | DECIMAL(5,2) | 是 | 规则计算 | 排序 | 0—100，权重版本化 |
| `rerank_score` | 模型重排分 | DECIMAL(8,6) | 否 | Model Router | 排序 | 不与业务分混用 |
| `match_reasons` | 匹配原因 | ARRAY<TEXT> | 是 | 规则/AI | 解释 | 只引用已有属性 |
| `set_version` | 竞品集合版本 | INT | 是 | Supervisor | Checkpoint恢复 | 任务内递增；AI自主或user确认后冻结 |

V3删除商品级`review_status/reviewed_by`等岗位复核字段。重大歧义通过统一`user_confirmations`记录答案，不覆盖原匹配结果。

### 5.4 原始评论 `reviews`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `dataset_id` | 数据集ID | BIGINT | 是 | 系统 | 样本范围 | 外键 |
| `listing_id` | 商品ID | BIGINT | 是 | 系统 | 评论归属 | 外键，同数据集 |
| `platform_review_id` | 平台评论ID | STRING(200) | 是 | 数据源 | 去重 | 数据集内唯一 |
| `rating` | 评分 | DECIMAL(3,2) | 否 | 数据源 | 情感/星级分析 | 0—5，缺失不记0 |
| `title_original` | 评论标题原文 | TEXT | 否 | 数据源 | 证据 | **不可被翻译或AI覆盖** |
| `content_original` | 评论正文原文 | TEXT | 是 | 数据源 | 证据Span | **只读原始事实；内容哈希校验** |
| `language_code` | 原文语言 | STRING(16) | 是 | 数据源/检测 | 翻译路由 | BCP47或ISO语言码 |
| `title_translated` | 标题翻译 | TEXT | 否 | AI | 中文阅读 | 与原文分列 |
| `content_translated` | 正文翻译 | TEXT | 否 | AI | 中文阅读 | 不作为证据Span基准 |
| `reviewed_at` | 评论时间 | DATETIME | 否 | 数据源 | 时间分析 | UTC |
| `verified_purchase` | 验证购买 | BOOLEAN | 否 | 数据源 | 样本标记 | 缺失不推断false |
| `helpful_count` | 有用票数 | INT | 否 | 数据源 | 证据排序 | ≥0 |
| `is_valid` | 是否有效 | BOOLEAN | 是 | 清洗规则 | 样本门禁 | 无效原因必须可查 |
| `invalid_reason` | 无效原因 | STRING(100) | 条件 | 清洗规则 | 质量报告 | is_valid=false时必填 |
| `content_hash` | 原文哈希 | STRING(64) | 是 | 系统 | 去重、防篡改 | 基于规范化原文SHA-256 |

### 5.5 评论观点 `review_aspects`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 任务隔离 | 外键 |
| `review_id` | 原评论ID | BIGINT | 是 | 系统 | 证据定位 | 外键 |
| `aspect_index` | 评论内观点序号 | INT | 是 | 抽取程序 | 幂等 | 任务+评论+序号唯一，≥0 |
| `taxonomy_code` | 家具需求本体码 | STRING(100) | 是 | AI/本体映射 | 需求统计 | 属于版本化本体 |
| `sentiment` | 观点情感 | ENUM | 是 | AI | 正负需求 | `positive/negative/neutral/mixed` |
| `severity` | 严重度 | ENUM | 否 | AI | 痛点排序 | 受控等级 |
| `person_codes` | 人群码 | ARRAY<STRING> | 否 | AI | 目标人群 | 必须有原文依据 |
| `scenario_codes` | 场景码 | ARRAY<STRING> | 否 | AI | 使用场景 | 必须有原文依据 |
| `product_attribute_codes` | 关联产品属性 | ARRAY<STRING> | 否 | AI/本体 | 工程转换 | 只用注册属性码 |
| `evidence_start` | 原文证据起点 | INT | 是 | AI/程序 | 原文高亮 | ≥0，Unicode字符偏移 |
| `evidence_end` | 原文证据终点 | INT | 是 | AI/程序 | 原文高亮 | >start且≤原文长度 |
| `evidence_quote` | 原文证据片段 | TEXT | 是 | 程序截取 | 证据展示 | 必须严格等于原文[start:end] |
| `extraction_confidence` | 抽取置信度 | DECIMAL(5,4) | 是 | 校准规则 | 样本权重 | 0—1 |
| `model_run_id` | 模型运行ID | BIGINT | 是 | AI网关 | 可追溯 | 外键`ai_model_runs.id` |

### 5.6 需求聚类 `insight_clusters`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 任务关联 | 外键 |
| `cluster_code` | 任务内聚类码 | STRING(64) | 是 | 系统 | 定位 | 任务内唯一 |
| `taxonomy_code` | 本体码 | STRING(100) | 是 | 聚类/AI | 类别展示 | 有效本体码 |
| `name` | 聚类短名称 | STRING(200) | 是 | AI+规则 | UI | 不得超出成员事实 |
| `summary` | 聚类摘要 | TEXT | 是 | AI | 洞察 | 仅使用成员观点 |
| `sentiment_distribution` | 情感分布 | JSON | 是 | 规则计算 | 洞察 | 各分项计数/比例可复算 |
| `aspect_count` | 观点数 | INT | 是 | 规则计算 | 热度 | >0 |
| `review_count` | 评论数 | INT | 是 | 规则计算 | 分母 | >0且≤aspect_count |
| `listing_count` | 覆盖商品数 | INT | 是 | 规则计算 | 跨商品一致性 | >0 |
| `mention_rate` | 提及率 | DECIMAL(7,6) | 是 | 规则计算 | 需求热度 | 0—1且明确分母 |
| `importance_score` | 重要度 | DECIMAL(5,2) | 是 | 规则计算 | 排序 | 0—100，规则版本化 |
| `cluster_confidence` | 聚类置信度 | DECIMAL(5,4) | 是 | 规则计算 | 报告风险 | 0—1 |
| `representative_aspect_ids` | 代表观点 | ARRAY<BIGINT> | 是 | 规则/Rerank | 证据首展 | 至少1条，同任务 |

### 5.7 聚类成员 `cluster_members`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `cluster_id` | 聚类ID | BIGINT | 是 | 聚类程序 | 关系 | 复合主键之一 |
| `review_aspect_id` | 观点ID | BIGINT | 是 | 聚类程序 | 关系 | 复合主键之一 |
| `similarity_score` | 与中心相似度 | DECIMAL(8,6) | 是 | 向量计算 | 聚类解释 | 0—1 |
| `is_representative` | 是否代表证据 | BOOLEAN | 是 | 规则 | 证据排序 | 默认false |

### 5.8 市场指标 `market_metrics`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 任务关联 | 外键 |
| `metric_code` | 指标码 | STRING(100) | 是 | 指标引擎 | 指标识别 | 使用注册指标码 |
| `dimension_type` | 维度类型 | STRING(64) | 是 | 指标引擎 | 分组 | 如overall/price_band/cluster/brand |
| `dimension_value` | 维度值 | STRING(200) | 否 | 指标引擎 | 分组 | overall可空 |
| `metric_value` | 指标值 | DECIMAL(24,8) | 条件 | 规则计算 | 图表、评分 | 非数值指标使用JSON值 |
| `metric_json` | 结构指标值 | JSON | 否 | 规则计算 | 分布、矩阵 | 必须有Schema |
| `numerator` | 分子 | DECIMAL(24,8) | 否 | 规则计算 | 比例解释 | 比例指标建议填写 |
| `denominator` | 分母 | DECIMAL(24,8) | 否 | 规则计算 | 比例解释 | 比例时>0 |
| `unit` | 单位 | STRING(32) | 否 | 指标定义 | 展示 | 与指标码一致 |
| `period_start` | 指标开始 | DATETIME | 否 | 数据范围 | 趋势 | 与数据范围一致 |
| `period_end` | 指标结束 | DATETIME | 否 | 数据范围 | 趋势 | ≥period_start |
| `sample_size` | 样本数 | INT | 是 | 规则计算 | 可信度 | ≥0 |
| `confidence` | 指标置信度 | DECIMAL(5,4) | 是 | 规则计算 | 报告风险 | 0—1 |
| `formula_version` | 公式版本 | STRING(64) | 是 | 系统 | 可复算 | 创建后不可改 |

### 5.9 价格带 `price_bands`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 任务关联 | 外键 |
| `band_code` | 价格带码 | STRING(50) | 是 | 规则 | 定位 | 任务内唯一 |
| `currency` | 币种 | STRING(3) | 是 | 任务 | 价格口径 | ISO4217 |
| `lower_bound` | 下界 | DECIMAL(18,4) | 是 | 规则 | 区间 | ≥0 |
| `upper_bound` | 上界 | DECIMAL(18,4) | 否 | 规则 | 区间 | >lower_bound；开放上界可空 |
| `listing_count` | 商品数 | INT | 是 | 规则 | 竞争密度 | ≥0 |
| `median_rating` | 评分中位数 | DECIMAL(3,2) | 否 | 规则 | 满意度 | 0—5 |
| `review_share` | 评论份额 | DECIMAL(7,6) | 否 | 规则 | 热度代理 | 0—1，明确非销售份额 |
| `feature_codes` | 高渗透功能 | ARRAY<STRING> | 否 | 规则 | 卖点分析 | 关联功能渗透指标 |

---

## 6. 分析任务、恢复与模型实体

### 6.1 分析任务 `analysis_tasks`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_uuid` | 对外任务UUID | UUID | 是 | 数据库 | API、thread_id | 唯一不可变 |
| `job_name` | 任务名称 | STRING(200) | 是 | user/系统 | 工作台 | 默认产品+市场+日期 |
| `job_type` | 目标任务类型 | ENUM | 是 | user | 工作流路由 | 保留PRD核心`product_market_fit/product_improvement`；旧非核心类型不得新建 |
| `product_id` | 主产品ID | BIGINT | 是 | API输入 | 分析对象 | 同租户外键 |
| `product_profile_version_id` | 冻结画像版本 | BIGINT | 是 | 系统 | 可复现 | 启动后不可漂移 |
| `dataset_id` | 冻结数据集ID | BIGINT | 是 | API输入 | 样本范围 | status=ready且同租户 |
| `target_country` | 国家 | STRING(2) | 是 | user/数据集 | 市场口径 | 与数据集一致 |
| `target_platform` | 平台 | ENUM | 是 | 数据集 | 市场口径 | 与数据集一致 |
| `analysis_currency` | 分析币种 | STRING(3) | 是 | user/市场 | 价格统一 | ISO4217 |
| `status` | 内部总体状态 | ENUM | 是 | Supervisor | 运行控制 | `draft/queued/running/waiting_human/partial_succeeded/succeeded/failed/cancelled` |
| `external_stage` | 用户展示阶段 | ENUM | 是 | Supervisor | 页面/API | 仅五阶段：`understanding_product/researching_market/evaluating_opportunity/generating_recommendation/completed` |
| `internal_stage` | 当前内部阶段 | ENUM | 是 | Supervisor | 路由、诊断 | 使用Agent V2内部Stage；不含competitor_review/expert_review |
| `progress_percent` | 进度 | DECIMAL(5,2) | 是 | Supervisor | 页面 | 0—100、对外单调 |
| `checkpoint_stage` | 最近安全恢复阶段 | STRING(64) | 否 | Checkpointer | 安全恢复 | 对应is_safe_resume=true记录 |
| `analysis_config` | 分析配置快照 | JSON | 是 | API/admin配置 | 阈值、Top-K、权重 | 启动后冻结、Schema版本化 |
| `ontology_version` | 家具本体版本 | STRING(64) | 是 | 系统 | 可复现 | 启动冻结 |
| `scoring_version` | 评分版本 | STRING(64) | 是 | 系统 | 可复现 | 启动冻结 |
| `prompt_bundle_version` | Prompt组版本 | STRING(64) | 是 | AI网关 | 可复现 | 启动冻结 |
| `model_route_version` | 模型路由版本 | STRING(64) | 是 | admin配置 | 模型追溯 | 启动冻结 |
| `idempotency_key` | 任务幂等键 | STRING(128) | 是 | API/系统 | 防重复创建 | 租户内唯一 |
| `started_at` | 开始时间 | DATETIME | 否 | Supervisor | 性能 | 首节点开始时 |
| `completed_at` | 完成时间 | DATETIME | 否 | Supervisor | 性能 | 终态填写 |
| `failure_code` | 阻断失败码 | STRING(100) | 条件 | Supervisor | 错误反馈 | failed时必填 |
| `failure_message` | 脱敏失败说明 | TEXT | 条件 | Supervisor | 页面 | 不含内部堆栈 |

### 6.2 节点运行 `task_stage_runs`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_id` | 任务ID | BIGINT | 是 | Supervisor | 任务关联 | 外键 |
| `stage_code` | 内部Stage码 | STRING(64) | 是 | Supervisor | 节点诊断 | 与Agent V2一致 |
| `attempt_no` | 尝试序号 | INT | 是 | Supervisor | 重试链 | 同任务+Stage从1递增 |
| `idempotency_key` | 节点幂等键 | STRING(128) | 是 | Supervisor | 防重复执行 | 全局唯一；含输入版本哈希 |
| `status` | 节点状态 | ENUM | 是 | Supervisor | 路由、诊断 | `queued/running/waiting_human/retry_scheduled/succeeded/partial_succeeded/failed/skipped/cancelled` |
| `input_ref` | 输入引用 | JSON | 是 | Agent State | 可复现 | 只存ID/版本/哈希 |
| `output_ref` | 输出引用 | JSON | 条件 | 节点 | 下游输入 | succeeded/partial时必填 |
| `started_at` | 开始时间 | DATETIME | 否 | 系统 | 耗时 | 运行时填写 |
| `ended_at` | 结束时间 | DATETIME | 否 | 系统 | 耗时 | 终态填写且≥开始时间 |
| `error_code` | 错误码 | STRING(100) | 条件 | 节点 | 自动策略 | 失败时必填 |
| `error_message` | 脱敏错误说明 | STRING(1000) | 条件 | 节点 | admin诊断 | 不含密钥/正文 |
| `retryable` | 是否可自动重试 | BOOLEAN | 条件 | 错误分类 | Supervisor | 失败时必填；不等于前端操作权限 |
| `retry_parent_run_id` | 上次尝试ID | BIGINT | 条件 | Supervisor | 尝试追溯 | attempt_no>1时必填 |

### 6.3 工作流部分失败 `workflow_partial_failures`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_id` | 任务ID | BIGINT | 是 | Supervisor | 任务关联 | 外键 |
| `stage_run_id` | 来源运行ID | BIGINT | 是 | Reducer/Join | 失败来源 | 外键 |
| `stage_code` | 失败Stage | STRING(64) | 是 | Agent | API/报告 | 与来源Stage一致 |
| `unit_type` | 失败单元类型 | ENUM | 是 | Agent | 局部范围 | `review_batch/opportunity/model_call/file/branch/other` |
| `failed_unit_ids` | 失败单元ID | ARRAY<STRING> | 是 | Agent | 局部恢复 | 非空、不存正文 |
| `failed_count` | 失败数 | INT | 是 | 规则计算 | 影响说明 | 等于去重ID数 |
| `total_count` | 总数 | INT | 是 | 规则计算 | 失败比例 | ≥failed_count且>0 |
| `impact` | 业务影响 | STRING(1000) | 是 | 规则/Agent | 页面、报告 | 说明覆盖率/置信度影响 |
| `confidence_cap` | 置信度上限 | DECIMAL(5,4) | 否 | 质量规则 | 评分限制 | 0—1 |
| `retryable` | 是否可由Supervisor局部重试 | BOOLEAN | 是 | 错误分类 | 自动恢复 | user不直接操作 |
| `resolved_status` | 处理状态 | ENUM | 是 | Supervisor | 恢复闭环 | 沿用`open/retry_scheduled/resolved/accepted_limit` |
| `resolved_stage_run_id` | 解决运行ID | BIGINT | 条件 | Supervisor | 追溯 | resolved时必填 |
| `resolved_at` | 解决时间 | DATETIME | 条件 | 系统 | 审计 | 关闭时必填 |

### 6.4 LangGraph Checkpoint投影 `workflow_checkpoints`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `checkpoint_id` | LangGraph Checkpoint ID | STRING(128) | 是 | LangGraph | 恢复 | 全局唯一 |
| `thread_id` | Graph线程ID | STRING(128) | 是 | LangGraph | 线程关联 | 建议等于task_uuid |
| `task_id` | 任务ID | BIGINT | 是 | 系统 | 业务关联 | 外键 |
| `stage_code` | Checkpoint内部Stage | STRING(64) | 是 | Supervisor | 恢复位置 | Agent V2 Stage |
| `checkpoint_version` | 版本 | BIGINT | 是 | LangGraph | 并发控制 | 任务内单调递增 |
| `state_snapshot` | 轻量State快照 | JSON | 是 | Agent State | 故障恢复 | 只存ID、版本、引用和控制状态 |
| `state_hash` | State哈希 | STRING(64) | 是 | 程序 | 完整性 | SHA-256 |
| `is_safe_resume` | 是否安全恢复 | BOOLEAN | 是 | Supervisor | 恢复门禁 | 业务结果不完整时为false |
| `status` | 生命周期状态 | ENUM | 是 | LangGraph | Checkpoint管理 | `active/consumed/superseded/corrupted` |
| `consumed_at` | 使用时间 | DATETIME | 否 | Supervisor | 恢复审计 | 使用后填写 |

### 6.5 统一用户确认 `user_confirmations`（V3新增）

本实体替代 `competitor_set_confirmations` 及建议专家复核字段，持久化同一个 `user_confirmation` 协议。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `confirmation_id` | 对外确认UUID | UUID | 是 | 数据库 | API、幂等恢复 | 全局唯一、不可变 |
| `task_id` | 分析任务ID | BIGINT | 是 | Agent State | 任务关联 | 外键，同租户 |
| `confirmation_type` | 确认类型 | ENUM | 是 | Supervisor | 中断路由 | 仅`fact_conflict/insufficient_data/low_confidence/high_risk_recommendation` |
| `question` | 单一确认问题 | TEXT | 是 | Supervisor | 确认卡片 | 具体、可回答，不要求审批整份结果 |
| `recommended_option` | 推荐选项编码 | STRING(100) | 是 | Supervisor | 默认展示 | 必须存在于options |
| `options` | 互斥选项 | JSON | 是 | Supervisor | user选择 | 非空数组；每项含稳定编码和含义；不得包含岗位转交 |
| `evidence_refs` | 证据引用 | JSON | 是 | 证据/质量结果 | 确认依据 | 非空；只引用同任务对象 |
| `impact` | 选项影响 | JSON | 是 | Supervisor/规则 | 风险说明 | 说明样本、评分、置信度或建议影响 |
| `checkpoint_stage` | 安全恢复Stage | STRING(64) | 是 | Checkpoint | Command恢复 | 必须存在同任务safe Checkpoint |
| `expires_at` | 到期时间 | DATETIME | 否 | 系统配置 | 过期判断 | 到期不自动采用推荐项 |
| `status` | 确认生命周期 | ENUM | 是 | Supervisor | 唯一活动确认 | `pending/responded/expired/cancelled`；任务同一时刻最多一个pending |
| `selected_option` | user选择 | STRING(100) | 条件 | API输入 | 恢复命令 | responded时必填且属于options |
| `user_input` | 必要补充事实 | JSON | 否 | API输入 | 冲突/缺失补充 | 按选项Schema校验，不允许自由扩展业务字段 |
| `responded_by` | 回答user | BIGINT | 条件 | 认证上下文 | 审计 | responded时必填；必须为任务所属租户user |
| `responded_at` | 回答时间 | DATETIME | 条件 | 系统 | 审计 | responded时必填 |
| `idempotency_key` | 回答幂等键 | STRING(128) | 是 | API/系统 | 防重复恢复 | 租户内唯一 |
| `checkpoint_id` | 实际Checkpoint ID | STRING(128) | 条件 | Supervisor | 恢复追溯 | 响应受理时必填 |

### 6.6 工作流控制事件 `workflow_control_events`

V3仅保留Supervisor恢复与内部Outbox用途，不作为user细粒度运维接口。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `event_uuid` | 事件UUID | UUID | 是 | 系统 | Outbox追踪 | 唯一 |
| `task_id` | 任务ID | BIGINT | 是 | Supervisor | 任务关联 | 外键 |
| `event_type` | 控制类型 | ENUM | 是 | Supervisor | 内部控制 | 核心仅`resume_confirmation/auto_retry/safe_stop` |
| `checkpoint_id` | Checkpoint ID | STRING(128) | 条件 | Supervisor | 恢复 | resume/auto_retry时必填 |
| `stage_code` | 目标Stage | STRING(64) | 否 | Supervisor | 自动重试 | 与Agent V2一致 |
| `payload` | 控制载荷 | JSON | 是 | Supervisor | Worker消费 | 只含ID、版本、原因和失败单元 |
| `idempotency_key` | 幂等键 | STRING(128) | 是 | 系统 | 防重复控制 | 租户内唯一 |
| `status` | Outbox状态 | ENUM | 是 | Worker | 投递 | `pending/enqueued/consumed/failed/cancelled` |
| `delivery_attempts` | 投递领取次数 | INT | 是 | Worker | 重启恢复、诊断 | 默认0；每次领取或租约超时重领加1 |
| `enqueued_at` | 最近领取时间 | DATETIME | 条件 | Worker | 租约判定 | enqueued时必填；超过5分钟可由其他Worker重领 |
| `requested_by` | 发起用户 | BIGINT | 否 | 认证上下文 | 审计 | 自动重试可空；确认恢复填user |
| `requested_at` | 请求时间 | DATETIME | 是 | 系统 | 延迟统计 | UTC |
| `consumed_at` | 消费时间 | DATETIME | 否 | Worker | 延迟统计 | 成功消费填写 |
| `error_code` | 错误码 | STRING(100) | 条件 | Worker | admin诊断 | failed时必填 |

### 6.7 AI模型运行 `ai_model_runs`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_id` | 任务ID | BIGINT | 是 | AI网关 | 成本、追溯 | 外键 |
| `stage_run_id` | 节点运行ID | BIGINT | 是 | AI网关 | 节点定位 | 外键 |
| `provider` | 路由/提供方 | STRING(100) | 是 | Model Router | 追溯 | 不存API Key |
| `model_id` | 实际模型ID | STRING(200) | 是 | Model Router | 质量回归 | 记录真实返回模型 |
| `task_type` | 模型任务类型 | ENUM | 是 | AI网关 | 路由、成本 | `vision_extract/text_extract/translate/embed/rerank/reason/report` |
| `prompt_template_id` | Prompt模板ID | BIGINT | 条件 | AI网关 | 可复现 | 生成/抽取任务必填 |
| `prompt_version` | Prompt版本 | STRING(64) | 条件 | AI网关 | 回归 | 使用Prompt时必填 |
| `input_hash` | 脱敏输入哈希 | STRING(64) | 是 | AI网关 | 缓存、追溯 | SHA-256，不存敏感输入 |
| `output_schema_version` | 输出Schema版本 | STRING(64) | 条件 | AI网关 | 结构校验 | 结构化输出必填 |
| `input_tokens` | 输入Token | INT | 否 | Model Router | 算力 | ≥0 |
| `output_tokens` | 输出Token | INT | 否 | Model Router | 算力 | ≥0 |
| `image_count` | 图片数 | INT | 是 | AI网关 | 多模态成本 | 默认0 |
| `latency_ms` | 耗时 | INT | 是 | AI网关 | 性能 | ≥0 |
| `estimated_cost` | 估算成本 | DECIMAL(18,8) | 否 | 成本规则 | 预算 | 有值需cost_unit |
| `cost_unit` | 成本单位 | STRING(16) | 条件 | 成本规则 | 预算 | `CNY/USD/credit` |
| `status` | 调用状态 | ENUM | 是 | AI网关 | 重试、诊断 | Agent V2：`queued/running/succeeded/schema_failed/rate_limited/timeout/content_blocked/failed/cached` |
| `retry_count` | 重试次数 | INT | 是 | AI网关 | 稳定性 | ≥0 |
| `schema_valid` | Schema是否通过 | BOOLEAN | 条件 | 校验器 | 幻觉控制 | 结构化输出必填 |
| `error_code` | 错误码 | STRING(100) | 条件 | AI网关 | admin诊断 | 非成功状态按需填写 |
| `error_message` | 脱敏模型错误说明 | STRING(1000) | 条件 | AI网关 | admin诊断 | 不含API Key、Prompt正文、评论全文或模型原始敏感输入；user不返回 |

### 6.8 Prompt模板 `prompt_templates`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `code` | Prompt码 | STRING(100) | 是 | admin/开发 | 调用路由 | 全局稳定 |
| `version` | 版本 | STRING(64) | 是 | admin/开发 | 可复现 | code+version唯一 |
| `task_type` | 任务类型 | ENUM | 是 | admin | 模型路由 | 与model_run一致 |
| `template_content` | Prompt正文 | TEXT | 是 | admin/开发 | 模型调用 | 变更创建新版本，不原地覆盖 |
| `output_schema` | 输出JSON Schema | JSON | 条件 | admin/开发 | 结构校验 | 结构化任务必填 |
| `status` | 模板状态 | ENUM | 是 | admin | 发布管理 | `draft/testing/active/retired` |
| `evaluation_set_version` | 评测集版本 | STRING(64) | 条件 | 评测系统 | 发布门禁 | active时必填 |

### 6.9 模型与运行配置 `model_route_configs`

`compute_quota` 不再是独立实体，合并为admin管理的 `compute_config`。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_type` | 任务类型 | ENUM | 是 | admin | 模型选择 | 与模型运行一致 |
| `config_version` | 配置版本 | STRING(64) | 是 | 系统 | 任务冻结 | task_type+version唯一 |
| `primary_model_id` | 主模型 | STRING(200) | 是 | admin | 默认路由 | Model Router可用 |
| `fallback_model_ids` | 备选模型 | ARRAY<STRING> | 否 | admin | 自动降级 | 有序且不重复主模型 |
| `timeout_ms` | 超时 | INT | 是 | admin | 稳定性 | >0 |
| `max_retries` | 自动重试上限 | INT | 是 | admin | Supervisor | 0—5 |
| `batch_size` | 批大小 | INT | 否 | admin/压测 | 评论抽取 | 批任务使用且满足Token上限 |
| `concurrency_limit` | 并发上限 | INT | 是 | admin | 限流 | ≥1 |
| `compute_config` | 算力与配额配置 | JSON | 是 | admin | 预算、预警、硬限制 | 沿用`quota_unit/quota_total/warning_threshold/hard_limit`语义；`quota_used`由模型运行聚合，不写入配置；Schema版本化 |
| `active` | 是否启用 | BOOLEAN | 是 | admin | 路由发布 | 每task_type仅一个active版本 |

---

## 7. 机会、建议、证据与报告实体

### 7.1 市场机会 `market_opportunities`

V3将`opportunity_scores`、`manufacturing_requirements`和`enterprise_fit_details`合并到本实体。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 任务关联 | 外键 |
| `opportunity_code` | 任务内机会码 | STRING(50) | 是 | 系统 | 定位 | 任务内唯一 |
| `title` | 机会名称 | STRING(200) | 是 | AI+事实 | 报告 | 至少含品类/人群/场景/功能中两项 |
| `description` | 机会说明 | TEXT | 是 | AI | 报告 | 只用验证事实 |
| `target_country` | 国家 | STRING(2) | 是 | 任务 | 市场定位 | 与任务一致 |
| `target_platform` | 平台 | ENUM | 是 | 任务 | 市场定位 | 与任务一致 |
| `target_user_codes` | 目标人群 | ARRAY<STRING> | 否 | 评论洞察 | 用户画像 | 必须有证据 |
| `usage_scenario_codes` | 使用场景 | ARRAY<STRING> | 否 | 评论洞察 | 场景定位 | 必须有证据 |
| `primary_cluster_ids` | 核心需求聚类 | ARRAY<BIGINT> | 是 | 系统 | 证据链 | 非空且同任务 |
| `price_band_id` | 建议价格带 | BIGINT | 否 | 价格分析 | 定价参考 | 同任务外键 |
| `status` | 机会状态 | ENUM | 是 | 系统 | 展示 | 核心仅`generated/data_needed`；不承载多人评审流转 |
| `demand_heat_score` | 需求热度分 | DECIMAL(5,2) | 条件 | 评分程序 | 五维市场评分 | 0—100且有指标依据 |
| `demand_growth_score` | 需求增长分 | DECIMAL(5,2) | 条件 | 评分程序 | 五维市场评分 | 可比时间点不足时为空 |
| `unmet_need_score` | 未满足程度 | DECIMAL(5,2) | 条件 | 评分程序 | 五维市场评分 | 0—100；优先使用负观点比例 |
| `competition_space_score` | 竞争空间 | DECIMAL(5,2) | 条件 | 评分程序 | 五维市场评分 | 0—100，高分空间大 |
| `profit_space_score` | 利润空间代理 | DECIMAL(5,2) | 条件 | 评分程序 | 五维市场评分 | 同币种价格和成本才比较，不表示真实利润 |
| `enterprise_fit_score` | 企业适配分F | DECIMAL(5,2) | 条件 | 确定性规则 | 企业修正与条件检查 | 已知检查中满足比例；完全未知为空 |
| `market_score` | 市场原分 | DECIMAL(5,2) | 条件 | 评分程序 | 可解释排序 | 可用权重归一；无可用因子为空 |
| `adjusted_score` | 企业修正分 | DECIMAL(5,2) | 条件 | 评分程序 | 可解释排序 | market_score×((1−α)+αF/100)；F未知不应用乘数 |
| `policy_snapshot` | 本机会策略快照 | JSON | 是 | 任务快照 | 可复算 | 版本、五权重、修正强度、必要能力；旧结果不重写 |
| `base_score` | 兼容排序分 | DECIMAL(5,2) | 条件 | 评分程序 | 旧接口排序 | 新记录与adjusted_score一致；原分使用market_score |
| `confidence` | 机会评分置信度 | DECIMAL(5,4) | 是 | 规则计算 | 风险提示 | 0—1；与base_score分离 |
| `recommendation_level` | 建议结论 | ENUM | 是 | 规则 | 报告 | `prioritize_validate/collect_more_data/capability_gap/limited_opportunity` |
| `weight_config` | 实际权重 | JSON | 是 | 评分程序 | 可复算 | 记录原权重和缺项归一权重 |
| `scoring_version` | 评分实现版本 | STRING(64) | 是 | 系统 | 可复算 | 新记录enterprise-policy-v1；结合policy_snapshot还原权重 |
| `manufacturing_fit` | 制造条件检查 | JSON数组 | 是 | 确定性规则 | 工厂适配下钻 | 新记录首项status/blocked/signals/fit_score/policy_version；signals逐项pass/blocked/unknown与证据原因；兼容旧数组 |
| `calculated_at` | 计算时间 | DATETIME | 是 | 系统 | 新鲜度 | UTC |

现行默认市场权重为30%需求热度、15%增长、25%未满足、20%竞争空间、10%利润代理，允许企业版本化配置；缺失因子按可用权重归一。市场原分再经企业适配强度α修正（默认0.3），硬条件独立决定建议是否暂缓。该规则尚未用真实企业标签校准；置信度独立展示。

### 7.2 产品建议 `product_recommendations`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `opportunity_id` | 机会ID | BIGINT | 是 | 系统 | 机会关联 | 外键 |
| `recommendation_type` | 建议类型 | ENUM | 是 | AI/RAG | 分组 | 沿用`dimension/material/structure/function/color/packaging/assembly/cost/testing/positioning` |
| `problem_statement` | 市场问题 | TEXT | 是 | 需求聚类 | 建议起点 | 可追溯到证据 |
| `root_cause_hypotheses` | 根因假设 | ARRAY<TEXT> | 是 | RAG+AI | 工程讨论 | 明确为假设，允许多个 |
| `recommended_action` | 建议动作 | TEXT | 是 | RAG+AI | 决策报告 | 不写成生产指令 |
| `target_attribute_code` | 影响属性码 | STRING(100) | 否 | AI/规则 | 产品关联 | 有明确属性时填写 |
| `target_value` | 建议目标值 | JSON | 否 | 规范/已确认事实 | 定量建议 | 无可靠依据必须空 |
| `expected_benefit` | 预期改善 | TEXT | 是 | AI | 决策对比 | 使用预期/待验证口径 |
| `impact_dimensions` | 影响维度 | ARRAY<ENUM> | 是 | RAG+AI | 风险 | 沿用成本、重量、舒适、耐用、包装、物流、合规、交期 |
| `cost_impact_min` | 成本影响下界 | DECIMAL(18,4) | 否 | BOM/报价 | 利润判断 | 无BOM/报价必须空 |
| `cost_impact_max` | 成本影响上界 | DECIMAL(18,4) | 否 | BOM/报价 | 利润判断 | ≥下界 |
| `cost_currency` | 成本币种 | STRING(3) | 条件 | 企业数据 | 成本口径 | 有成本值时必填 |
| `priority` | 优先级 | ENUM | 是 | 规则/AI | 排序 | `high/medium/low` |
| `confidence` | 建议置信度 | DECIMAL(5,4) | 是 | 规则 | 风险 | 0—1 |
| `validation_method` | 验证方法 | TEXT | 是 | RAG+AI | 线下参考 | 只描述方法，不创建validation_task |
| `evidence_cluster_ids` | 证据聚类 | ARRAY<BIGINT> | 是 | 系统 | 证据下钻 | 至少1个 |
| `risk_level` | 风险等级 | ENUM | 是 | 规则 | 确认触发 | 使用现有风险分级配置；高风险触发统一确认 |
| `model_run_id` | 生成模型运行 | BIGINT | 是 | AI网关 | 可复现 | 外键 |

删除`expert_review_status/expert_review_note`，高风险建议的user选择存于`user_confirmations`，不覆盖AI建议。

### 7.3 统一证据链 `evidence_links`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 任务隔离 | 外键 |
| `claim_type` | 结论对象类型 | ENUM | 是 | 系统 | 多态关联 | 核心仅`report/opportunity/recommendation/score/manufacturing_fit` |
| `claim_id` | 结论对象ID | BIGINT | 是 | 系统 | 结论定位 | 与claim_type一致 |
| `claim_path` | JSON内部结论路径 | STRING(500) | 否 | 系统 | 合并JSON字段定位 | sections/manufacturing_fit内部结论必填 |
| `claim_category` | 结论性质 | ENUM | 是 | 系统/AI | 可信标记 | `observation/inference/recommendation/validation_required` |
| `evidence_type` | 证据类型 | ENUM | 是 | 系统 | 证据定位 | `review_aspect/listing/market_metric/product_attribute/capability/file/data_quality` |
| `evidence_id` | 证据对象ID | BIGINT | 是 | 系统 | 下钻 | 与类型一致、同任务/租户 |
| `support_type` | 支持关系 | ENUM | 是 | 系统/AI | 解释 | `supports/contradicts/context/limitation` |
| `relevance_score` | 相关度 | DECIMAL(5,4) | 是 | Rerank/规则 | 排序 | 0—1 |
| `is_primary` | 是否主证据 | BOOLEAN | 是 | 证据审计 | 首展 | 每个核心结论至少1条 |
| `display_order` | 展示顺序 | INT | 是 | 系统 | UI | 同结论从1递增 |

### 7.4 在线分析报告 `analysis_reports`

报告章节合并到`sections`；报告完成后直接在线可查看，不包含多人发布审批。

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `report_uuid` | 对外报告UUID | UUID | 是 | 数据库 | API、页面 | 唯一不可变 |
| `analysis_job_id` | 任务ID | BIGINT | 是 | 系统 | 报告关联 | 外键；一任务可多版本 |
| `report_version` | 报告版本 | INT | 是 | 系统 | 版本 | 任务内递增唯一 |
| `title` | 报告标题 | STRING(300) | 是 | 系统/AI | 页面 | 包含产品和市场 |
| `status` | 报告状态 | ENUM | 是 | 系统 | 可见性 | 复用`draft/superseded`；V3的draft为已生成且可在线查看，不进入human_review/published审批 |
| `executive_summary` | 执行摘要 | TEXT | 是 | AI+结构结果 | 首屏 | 不含无证据新数值 |
| `decision_recommendation` | 总体建议 | ENUM | 是 | 评分规则 | 决策 | 与机会建议等级一致 |
| `overall_opportunity_score` | 综合机会分 | DECIMAL(5,2) | 是 | 评分引擎 | 首屏 | 与主机会一致 |
| `overall_confidence` | 总体置信度 | DECIMAL(5,4) | 是 | 规则 | 风险 | 0—1且与机会分分离 |
| `data_scope_snapshot` | 数据范围快照 | JSON | 是 | 数据集/规则 | 报告复现 | **必须含平台、国家、时间、商品数、有效评论数、数据集版本、限制和代理指标口径** |
| `product_profile_snapshot` | 产品画像快照 | JSON | 是 | 产品画像 | 报告复现 | 脱敏且不随主档变化 |
| `enterprise_profile_snapshot` | 企业能力快照 | JSON | 是 | 企业档案/能力 | 适配复现 | 成本等敏感字段按权限脱敏 |
| `target_user_summary` | 人群摘要 | JSON | 否 | 评论洞察 | 定位 | 必须有证据 |
| `price_summary` | 价格摘要 | JSON | 否 | 价格指标 | 定价参考 | 含币种、时间、促销口径 |
| `risk_summary` | 风险摘要 | JSON | 是 | 规则/AI | 决策风险 | 至少包含数据限制和能力缺口 |
| `pending_validation_items` | 待验证事项 | JSON | 是 | 建议/规则 | 线下参考 | 可空数组；不创建验证任务 |
| `sections` | 报告章节 | JSON | 是 | 规则/AI | Web渲染 | 合并V2 section_code/title/sort_order/content_type/structured_content/narrative_text/chart_config；章节码唯一、顺序唯一、图表只引用已存指标 |
| `partial_failures_snapshot` | 部分失败快照 | JSON | 是 | 工作流 | 报告限制 | 可空数组；不可因后续恢复静默改变旧报告 |
| `version_bundle` | 版本快照 | JSON | 是 | 任务/模型 | 可复现 | 含本体、评分、Prompt、路由和Schema版本 |
| `generated_model_run_id` | 报告模型运行ID | BIGINT | 是 | AI网关 | 模型追溯 | 外键；模板兜底仍记录逻辑运行 |

---

## 8. Agent State字段

本节字段属于`FurniScopeGraphState`，不等于每项都要成为任务表列。长期恢复由`workflow_checkpoints.state_snapshot`承载，大集合优先保存查询引用。

### 8.1 主State

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_id` | 内部任务ID | BIGINT | 是 | 数据库→State | 全图关联 | 不对API暴露 |
| `task_uuid` | 对外任务UUID | UUID | 是 | 数据库→State | thread_id、日志 | 与任务一致 |
| `tenant_id` | 租户ID | BIGINT | 是 | 认证/数据库→State | 工具隔离 | 每个工具二次校验 |
| `status` | 内部总体状态 | ENUM | 是 | Supervisor | 条件边 | 与analysis_tasks.status一致 |
| `external_stage` | 外部五阶段 | ENUM | 是 | Supervisor | API投影 | 仅五个规定枚举 |
| `internal_stage` | 内部Stage | ENUM | 是 | Supervisor | 节点路由 | Agent V2枚举 |
| `progress_percent` | 进度 | DECIMAL(5,2) | 是 | Supervisor | API投影 | 0—100 |
| `product_id` | 产品ID | BIGINT | 是 | 任务→State | 产品上下文 | 启动冻结 |
| `product_profile_version` | 产品画像版本 | INT | 是 | 数据库→State | 可复现 | 不随主档变化 |
| `dataset_id` | 数据集ID | BIGINT | 是 | 任务→State | 市场查询 | 启动冻结 |
| `target_market` | 目标市场 | JSON | 是 | API/数据库→State | 全图共享 | 国家、平台、币种 |
| `version_bundle` | 版本包 | JSON | 是 | 系统→State | 可复现 | 本体、评分、Prompt、路由、Schema |
| `analysis_config` | 分析配置 | JSON | 是 | 任务→State | 阈值、Top-K、权重 | 运行中不可修改 |
| `product_context_ref` | 产品与能力引用 | JSON | 否 | I02→State | 下游读取 | 只存ID、版本、对象键 |
| `valid_listing_ids` | 有效商品引用 | ARRAY<BIGINT> | 否 | I03→State | 竞品筛选 | 大集合改存查询引用 |
| `valid_review_ids` | 有效评论引用 | ARRAY<BIGINT> | 否 | I03→State | 批次规划 | 大集合改存批次引用 |
| `competitor_set_version` | 竞品集合版本 | INT | 否 | I06/I07→State | 评论范围 | 与持久化集合一致 |
| `quality_flags` | 质量限制 | ARRAY<JSON> | 是 | I03→State | 置信度、报告 | 可空数组，不为null |
| `trend_eligible` | 可否趋势分析 | BOOLEAN | 是 | I03→State | I12-B路由 | 至少两个可比时间点才true |
| `stage_results` | 节点结果引用 | JSON | 是 | 各节点→State | 下游输入 | 只存引用和摘要 |
| `user_confirmation` | 当前统一确认 | JSON | 否 | Supervisor→State | interrupt/resume | 仅九个业务字段；与活动记录一致 |
| `retry_context` | 自动重试上下文 | JSON | 否 | Supervisor→State | 节点恢复 | stage、attempt、错误、失败单元引用 |
| `partial_failures` | 部分失败 | ARRAY<JSON> | 是 | Reducer/Join→State | 报告限制 | 与持久化实体投影一致 |
| `fatal_error` | 阻断错误 | JSON | 否 | 节点→State | 失败路由 | 稳定错误码、用户消息、request_id |
| `cancel_requested` | 安全停止标记 | BOOLEAN | 是 | 数据库→State | 原子边界停止 | 默认false |

### 8.2 `user_confirmation` State投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `confirmation_id` | 确认标识 | UUID | 是 | 数据库→State | interrupt/resume | 与实体一致 |
| `confirmation_type` | 确认类型 | ENUM | 是 | Supervisor | 路由 | 四种规定类型 |
| `question` | 确认问题 | TEXT | 是 | Supervisor | 卡片 | 单一可回答问题 |
| `recommended_option` | 推荐选项 | STRING(100) | 是 | Supervisor | 默认展示 | 存在于options |
| `options` | 互斥选项 | JSON | 是 | Supervisor | user选择 | 非空、Schema合法 |
| `evidence_refs` | 证据引用 | JSON | 是 | 业务结果 | 解释 | 同任务且非空 |
| `impact` | 选项影响 | JSON | 是 | 规则/Supervisor | 风险 | 说明对结果影响 |
| `checkpoint_stage` | 恢复Stage | STRING(64) | 是 | Checkpoint | Command恢复 | 对应安全点 |
| `expires_at` | 到期时间 | DATETIME | 否 | 数据库 | 过期门禁 | 到期不自动选择 |

### 8.3 并行单元State `parallel_unit_state`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `unit_id` | 单元ID | STRING(64) | 是 | Agent | Map/Reduce | 任务+Stage内唯一 |
| `unit_type` | 单元类型 | ENUM | 是 | Agent | Reducer | `review_batch/opportunity/analytics_branch` |
| `status` | 单元状态 | ENUM | 是 | Agent | 并行进度 | task_stage_runs状态子集 |
| `input_ref` | 输入引用 | JSON | 是 | Agent | Map执行 | 只存ID和哈希 |
| `output_ref` | 输出引用 | JSON | 条件 | Agent | Reduce | 成功时必填 |
| `attempt_no` | 尝试次数 | INT | 是 | Supervisor | 局部重试 | 从1递增 |
| `error_code` | 错误码 | STRING(100) | 条件 | Agent | 部分失败 | 失败时必填 |
| `retryable` | 可否自动重试 | BOOLEAN | 条件 | 错误分类 | Supervisor | 失败时必填 |

---

## 9. API衍生字段

下列字段由数据库、State或规则聚合，不代表同名数据库列，不得由前端反写。

### 9.0 通用响应、分页与认证投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `success` | 请求是否成功 | BOOLEAN | 是 | API网关 | 统一Envelope | HTTP成功为true、业务失败为false |
| `data` | 成功载荷 | JSON/null | 是 | API服务 | 统一Envelope | 失败时为null |
| `error.code` | 稳定业务错误码 | STRING(100) | 条件 | API服务 | 客户端分支 | 失败必填；客户端不得解析message |
| `error.message` | 脱敏错误提示 | STRING(500) | 条件 | API服务 | 用户反馈 | 不含密钥、Token、评论全文、SQL和堆栈 |
| `error.details` | 字段级错误摘要 | JSON/null | 否 | 校验器 | 修正输入 | 仅暴露安全字段路径和原因码 |
| `request_id` | 请求链路ID | STRING(64) | 是 | 网关 | 诊断 | 单次请求全链路一致 |
| `timestamp` | 响应时间 | DATETIME | 是 | 网关 | 客户端时序 | UTC ISO 8601 |
| `items` | 分页数据项 | ARRAY<JSON> | 条件 | 查询聚合 | 列表接口 | 分页响应必填 |
| `total` | 总记录数 | BIGINT | 条件 | 数据库聚合 | 分页 | ≥0 |
| `page` | 当前页 | INT | 条件 | API输入/输出 | 分页 | 从1开始 |
| `page_size` | 每页数 | INT | 条件 | API输入/输出 | 分页 | 1—100，默认20 |
| `has_next` | 是否有下一页 | BOOLEAN | 条件 | API衍生 | 分页 | 由total/page/page_size计算 |
| `access_token` | 短期访问Token | STRING | 条件 | 认证系统 | Bearer鉴权 | 只在登录/刷新响应出现，不落库、不写日志 |
| `refresh_token` | 轮换刷新Token | STRING | 条件 | 认证系统 | 换取Access Token | 只在登录/刷新响应出现；仅摘要落库；每次使用即轮换 |
| `token_type` | Token类型 | ENUM | 条件 | 认证系统 | 客户端组装Header | 固定`Bearer` |
| `expires_in` | Access Token剩余秒数 | INT | 条件 | 认证系统 | 客户端刷新调度 | >0 |
| `resource_version` | 乐观并发版本 | STRING | 条件 | `updated_at`衍生 | `ETag/If-Match` | 使用UTC updated_at规范化后强ETag；不新增业务列 |
| `user_id` | 用户受控公开标识 | BIGINT | 条件 | users.id→API | 当前用户和admin管理 | V3兼容口径允许暴露；只能在鉴权后的本租户/admin范围返回，不因此允许客户端覆盖tenant_id |

### 9.1 任务状态投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `task_uuid` | 任务UUID | UUID | 是 | analysis_tasks→API | 路径、轮询 | 不暴露内部ID |
| `status` | 内部总体状态 | ENUM | 是 | analysis_tasks→API | 异常/完成判断 | 前端不直接映射节点 |
| `stage` | 外部展示阶段 | ENUM | 是 | external_stage→API | 五阶段进度 | 仅规定五枚举 |
| `progress_percent` | 进度 | DECIMAL(5,2) | 是 | 任务→API | 进度条 | 0—100 |
| `stage_runs` | 内部运行摘要 | ARRAY<JSON> | 否 | stage_runs聚合→API | admin诊断/受控详情 | user仅见`stage_code/attempt_no/status/started_at/ended_at/retryable`；admin可额外见`error_code`及脱敏`error_message`；均不返回input_ref/output_ref |
| `partial_failures` | 部分失败摘要 | ARRAY<JSON> | 是 | 失败实体/State→API | 范围限制 | 无失败返回[]；包含unit_type/count/impact/retryable，user不直接重试 |
| `checkpoint_stage` | 最近安全恢复Stage | STRING/null | 是 | Checkpoint+任务→API | 确认与诊断 | 无安全点为null；普通user不据此选择节点 |
| `retryable` | Supervisor是否可安全自动恢复 | BOOLEAN | 是 | 状态+Checkpoint计算→API | 运行说明 | 不用于显示手工阶段重试按钮 |
| `user_confirmation` | 当前确认卡片 | JSON/null | 是 | 活动确认→API | S04确认 | 无pending确认返回null；仅九个State字段 |
| `report_uuid` | 在线报告UUID | UUID/null | 是 | reports→API | 结果入口 | 完成前null |

### 9.2 工作台聚合

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `metrics.active_product_count` | 有效产品数 | INT | 是 | products聚合 | S02指标 | 当前租户 |
| `metrics.running_task_count` | 运行任务数 | INT | 是 | tasks聚合 | S02指标 | queued/running/partial_succeeded |
| `metrics.waiting_confirmation_count` | 待user确认数 | INT | 是 | confirmations聚合 | S02提醒 | 仅pending且未过期 |
| `metrics.online_report_count` | 在线报告数 | INT | 是 | reports聚合 | S02指标 | status=draft且已完成最终事务 |
| `recent_tasks` | 最近任务 | ARRAY<JSON> | 是 | task+product联查 | 工作台 | 当前租户、更新时间倒序 |
| `recent_tasks[].product_name` | 产品名 | STRING(200) | 是 | product联查 | 任务识别 | 展示字段，不写任务表 |
| `confirmation_todos` | 当前确认列表 | ARRAY<JSON> | 是 | pending confirmations | 快捷入口 | 当前user所属租户，无岗位分派 |
| `recent_reports` | 最近在线报告 | ARRAY<JSON> | 是 | reports | 报告入口 | draft当前版本 |

### 9.3 产品解析投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `summary` | 解析汇总 | JSON | 是 | parse_jobs聚合 | S03 | 含文件总数、成功和失败数 |
| `file_results` | 文件结果 | ARRAY<JSON> | 否 | parse_job_files+assets | 文件反馈 | 请求详情时返回 |
| `file_results[].file_name` | 文件名 | STRING(255) | 是 | asset→API | 展示 | 净化后的原名 |
| `file_results[].security_status` | 安全状态 | ENUM | 是 | asset/result→API | 安全反馈 | 拒绝文件不解析 |
| `file_results[].parse_status` | 解析状态 | ENUM | 是 | result→API | 进度 | 与实体一致 |

### 9.4 竞品与综合洞察投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `competitor_set_version` | 竞品集合版本 | INT | 是 | matches聚合 | 结果一致性 | 当前任务版本 |
| `competitor_summary` | 类型计数 | JSON | 是 | matches聚合 | S05 | direct/benchmark/substitute/excluded |
| `available_review_count` | 可用评论数 | INT | 是 | listing+reviews聚合 | 样本说明 | 只计有效评论 |
| `scores` | 匹配分摘要 | JSON | 是 | matches→API | 竞品解释 | 六项业务分与rerank分分离 |
| `data_scope` | 数据范围 | JSON | 是 | dataset+task快照 | 洞察口径 | 国家、平台、时间、商品、有效评论、限制 |
| `opportunities` | 机会摘要 | ARRAY<JSON> | 是 | opportunities→API | S05/S06 | base_score与confidence必须同时返回 |

### 9.5 统一确认API投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `confirmation` | 确认对象 | JSON | 是 | user_confirmations→API | S04卡片 | 只返回九个State字段 |
| `selected_option` | user选择 | STRING(100) | 是 | API输入 | Command恢复 | 必须来自options |
| `user_input` | 必要补充事实 | JSON | 否 | API输入 | 事实补充 | 按选项Schema校验 |
| `accepted` | 是否受理 | BOOLEAN | 是 | Supervisor→API | 提交反馈 | 只表示事务与Outbox成功 |
| `resumed_from_checkpoint` | 是否受理恢复 | BOOLEAN | 是 | Supervisor→API | 提交反馈 | 不表示下游已完成 |

### 9.6 Internal Model Router衍生投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `output_payload` | 结构化推理结果 | JSON | 条件 | Model Router | API-MDL-01 | 必须通过请求指定Schema |
| `dimension` | 向量维度 | INT | 条件 | 配置/Model Router | API-MDL-02 | P0固定1024；响应每条向量长度必须一致 |
| `vectors` | 稠密向量集合 | ARRAY<ARRAY<DECIMAL>> | 条件 | Model Router | 内部向量写入 | 与输入texts同序同数；不进入日志或普通API |
| `token_count` | 向量输入Token数 | INT | 条件 | Model Router usage | 成本追溯 | ≥0 |
| `results[].candidate_id` | 重排候选ID | STRING(128) | 条件 | 请求映射 | API-MDL-03 | 由上游index映射，不能由模型自由生成 |
| `results[].rerank_score` | 重排相关分 | DECIMAL(8,6) | 条件 | qwen3-rerank | 排序 | 0—1，仅在单次请求内比较 |
| `results[].rank` | 重排名次 | INT | 条件 | API适配器 | 排序 | 从1递增且不重复 |

### 9.7 报告投影

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `report_uuid` | 报告UUID | UUID | 是 | report→API | 路径 | 当前租户可访问 |
| `sections` | 报告章节 | JSON | 是 | report.sections→API | Web渲染 | 按sort_order，Schema校验 |
| `evidence_drawer` | 证据抽屉数据 | JSON | 条件 | evidence+源实体聚合 | 点击“为什么” | 原文、翻译分列；Span针对原文 |
| `model_trace` | 模型追溯摘要 | JSON | 是 | version_bundle+model_runs | 可信AI | 不暴露Prompt正文、密钥或敏感输入 |
| `limitations` | 限制集合 | ARRAY<JSON> | 是 | 数据质量+部分失败+证据审计 | 风险展示 | 可空数组，不为null |

---

## 10. 主要枚举与统一口径

### 10.1 系统角色

`users.role_code`：仅`user/admin`。`owner/product_rd/market_ops/sales`禁止进入权限枚举。

### 10.2 外部阶段

`understanding_product/researching_market/evaluating_opportunity/generating_recommendation/completed`。

### 10.3 确认类型

`fact_conflict/insufficient_data/low_confidence/high_risk_recommendation`。

### 10.4 结论性质

`observation/inference/recommendation/validation_required`。事实、推断、建议和待验证不得混写。

### 10.5 来源优先级

```text
已确认结构化参数
> user明确输入
> 企业文档/产品资料
> 图片可见信息
> 模型推断
```

### 10.6 评分与置信度

- 业务分0—100；置信度0—1。
- 机会分表示相对吸引力，置信度表示证据充分程度。
- 未知能力不按0处理；缺失分项按版本规则归一权重。
- 需求增长没有两个可比时间点时为空，不从截面推断。

---

## 11. 实体关系、主外键与完整性

### 11.1 核心关系

```text
tenants
├── users
├── auth_sessions
├── api_idempotency_records
├── enterprise_profiles (constraints JSON)
├── manufacturing_capabilities
├── products
│   ├── file_assets
│   ├── product_parse_jobs ──< product_parse_job_files
│   └── product_profile_versions ──< product_attributes
├── market_datasets (field_mapping JSON)
│   └── market_listings (normalized_attributes JSON)
│       └── reviews
└── analysis_tasks
    ├── task_stage_runs
    │   ├── ai_model_runs
    │   └── workflow_partial_failures
    ├── workflow_checkpoints
    ├── user_confirmations
    ├── workflow_control_events
    ├── competitor_matches
    ├── review_aspects ──< cluster_members >── insight_clusters
    ├── market_metrics
    ├── price_bands
    ├── market_opportunities (score + manufacturing_fit JSON)
    │   └── product_recommendations
    ├── evidence_links
    └── analysis_reports (sections JSON)

prompt_templates ──< ai_model_runs
model_route_configs ── version snapshot in analysis_tasks
```

### 11.2 关键主外键

| 子实体字段 | 父实体 | 删除规则 | 完整性要求 |
|---|---|---|---|
| `users.tenant_id` | `tenants.id` | RESTRICT | 用户不可跨租户 |
| `auth_sessions.tenant_id/user_id` | `tenants.id/users.id` | CASCADE | Refresh会话必须归属同租户有效用户 |
| `api_idempotency_records.tenant_id/actor_user_id` | `tenants.id/users.id` | CASCADE/SET NULL | 幂等记录保持租户边界；用户删除后保留审计事实 |
| `enterprise_profiles.tenant_id` | `tenants.id` | RESTRICT | 一租户一当前档案 |
| `products.tenant_id` | `tenants.id` | RESTRICT | SKU租户内唯一 |
| `product_profile_versions.product_id` | `products.id` | CASCADE/归档策略 | 产品内版本唯一 |
| `market_listings.dataset_id` | `market_datasets.id` | CASCADE/归档策略 | 平台ID数据集内唯一 |
| `reviews.listing_id` | `market_listings.id` | CASCADE/归档策略 | 评论与商品同数据集 |
| `analysis_tasks.product_id/dataset_id` | 产品/数据集 | RESTRICT | 启动后冻结 |
| `task_stage_runs.task_id` | `analysis_tasks.id` | CASCADE/保留策略 | Stage attempt唯一 |
| `user_confirmations.task_id` | `analysis_tasks.id` | CASCADE/保留策略 | 同任务最多一个pending |
| `review_aspects.review_id` | `reviews.id` | RESTRICT | evidence Span与原文一致 |
| `market_opportunities.analysis_job_id` | `analysis_tasks.id` | CASCADE/版本保留 | 机会码任务内唯一 |
| `product_recommendations.opportunity_id` | `market_opportunities.id` | CASCADE | 同租户/任务 |
| `analysis_reports.analysis_job_id` | `analysis_tasks.id` | RESTRICT | report_version任务内唯一 |
| `evidence_links.analysis_job_id` | `analysis_tasks.id` | CASCADE/版本保留 | claim与evidence同任务/租户 |

### 11.3 关键完整性规则

1. 数据库外键不能直接保证多态证据同租户时，由事务服务和约束触发器二次校验。
2. `evidence_quote`必须等于`content_original[evidence_start:evidence_end]`。
3. `market_opportunity`每个非空分项必须有指标或manufacturing_fit依据。
4. `analysis_report`的范围、产品和企业快照创建后不可随主档更新。
5. `completed`任务必须存在可在线查看的`draft`报告，二者在I19同事务提交。
6. user确认答案不得覆盖AI原始结果；重算产生新结果版本。

---

## 12. 索引需求

| 索引 | 字段 | 查询场景 | 类型/约束 |
|---|---|---|---|
| `uk_users_email` | `email` | 登录、自动定位唯一租户、重复校验 | UNIQUE |
| `idx_users_tenant_role_status` | `tenant_id,role_code,status` | admin查user | B-tree |
| `uk_auth_sessions_refresh_hash` | `refresh_token_hash` | Refresh轮换与重放检测 | UNIQUE |
| `idx_auth_sessions_user_status_expire` | `user_id,status,expires_at` | 会话状态复核与清理 | B-tree |
| `uk_api_idempotency_scope` | `tenant_id,route_code,idempotency_key` | 写接口请求去重 | UNIQUE |
| `idx_api_idempotency_expiry` | `expires_at` | 过期记录清理 | B-tree |
| `uk_enterprise_profile_tenant` | `tenant_id` | 当前企业档案 | UNIQUE |
| `uk_capability_tenant_type_code` | `tenant_id,capability_type,capability_code` | 能力匹配 | UNIQUE |
| `uk_products_tenant_sku` | `tenant_id,sku` | SKU查询 | UNIQUE |
| `idx_product_profile_product_version` | `product_id,version_no` | 画像版本 | UNIQUE |
| `idx_dataset_tenant_market_status` | `tenant_id,platform,market_country,status` | 选择数据集 | B-tree |
| `uk_listing_dataset_platform_id` | `dataset_id,platform_listing_id` | 商品去重 | UNIQUE |
| `idx_listing_dataset_price` | `dataset_id,currency,sale_price` | 价格带 | B-tree |
| `idx_listing_normalized_attributes` | `normalized_attributes` | 属性过滤 | GIN；仅确有查询时建立 |
| `uk_review_dataset_platform_id` | `dataset_id,platform_review_id` | 评论去重 | UNIQUE |
| `idx_review_listing_valid_time` | `listing_id,is_valid,reviewed_at` | 评论下钻、时间分布 | B-tree |
| `idx_task_tenant_status_time` | `tenant_id,status,updated_at` | 工作台任务 | B-tree |
| `idx_task_tenant_external_stage` | `tenant_id,external_stage,status` | 五阶段聚合 | B-tree |
| `uk_stage_idempotency` | `idempotency_key` | 节点幂等 | UNIQUE |
| `idx_stage_task_status` | `task_id,status,stage_code` | Supervisor恢复 | B-tree |
| `uk_confirmation_uuid` | `confirmation_id` | API确认 | UNIQUE |
| `uk_confirmation_pending_task` | `task_id` WHERE `status='pending'` | 单活动确认 | Partial UNIQUE |
| `idx_confirmation_tenant_status_expire` | `tenant_id,status,expires_at` | 待确认和过期 | B-tree |
| `uk_checkpoint_id` | `checkpoint_id` | Command恢复 | UNIQUE |
| `idx_checkpoint_task_safe_version` | `task_id,is_safe_resume,checkpoint_version DESC` | 最近安全点 | B-tree |
| `idx_partial_failure_task_open` | `task_id,resolved_status` | 未解决失败 | B-tree |
| `idx_model_run_task_type_status` | `task_id,task_type,status` | 成本和诊断 | B-tree |
| `uk_match_task_listing_version` | `analysis_job_id,listing_id,set_version` | 竞品版本 | UNIQUE |
| `uk_aspect_task_review_index` | `analysis_job_id,review_id,aspect_index` | Map幂等 | UNIQUE |
| `idx_aspect_task_taxonomy_sentiment` | `analysis_job_id,taxonomy_code,sentiment` | 评论洞察 | B-tree |
| `idx_cluster_task_importance` | `analysis_job_id,importance_score DESC` | Top需求 | B-tree |
| `idx_metric_task_code_dimension` | `analysis_job_id,metric_code,dimension_type` | 指标查询 | B-tree |
| `idx_opportunity_task_score_conf` | `analysis_job_id,base_score DESC,confidence DESC` | 机会排序 | B-tree |
| `idx_manufacturing_fit_gin` | `manufacturing_fit` | 适配下钻 | GIN；压测后决定 |
| `idx_recommendation_opportunity_priority` | `opportunity_id,priority` | 建议列表 | B-tree |
| `idx_evidence_task_claim` | `analysis_job_id,claim_type,claim_id,display_order` | “为什么”下钻 | B-tree |
| `uk_report_task_version` | `analysis_job_id,report_version` | 报告版本 | UNIQUE |
| `uk_report_uuid` | `report_uuid` | API报告路径 | UNIQUE |

---

## 13. P0数据实现清单

| 优先级 | 实体/字段 | P0落地说明 |
|---|---|---|
| P0 | `tenants/users/auth_sessions/api_idempotency_records` | 两角色静态边界、租户隔离、Refresh安全和持久化写请求幂等 |
| P0 | `enterprise_profiles.constraints`、`manufacturing_capabilities` | 企业能力与约束上下文 |
| P0 | `products/file_assets/product_parse_jobs/product_parse_job_files/product_profile_versions/product_attributes` | 产品输入和画像 |
| P0 | `market_datasets.field_mapping`、`market_listings.normalized_attributes`、`reviews` | 合规市场样本 |
| P0 | `analysis_tasks/task_stage_runs` | 五阶段投影和内部执行 |
| P0 | `workflow_checkpoints/workflow_partial_failures/workflow_control_events` | Checkpoint、部分失败和安全恢复 |
| P0 | `user_confirmations` | 唯一人工中断协议 |
| P0 | `competitor_matches/review_aspects/insight_clusters/cluster_members` | 竞品可比性与评论证据 |
| P0 | `market_metrics/price_bands` | 确定性市场指标 |
| P0 | `market_opportunities.manufacturing_fit` | 六维评分、置信度和企业适配 |
| P0 | `product_recommendations/evidence_links` | 工程建议和证据审计 |
| P0 | `analysis_reports.sections` | 在线综合决策报告和范围快照 |
| P0 | `ai_model_runs/prompt_templates/model_route_configs.compute_config` | 模型、Prompt、成本与admin配置 |
| P0 | `audit_logs` | 确认、恢复、配置和敏感操作追溯 |

---

## 14. 非核心扩展附录

### 14.1 Listing生成

`listing_generation_records` 不属于核心市场洞察产品，V3核心数据模型不再引用。旧数据如需保留，应只读归档；未来若独立立项，必须重新定义产品范围，不得从旧实体自动恢复页面或API。

### 14.2 报告文件导出

`report_exports` 不属于核心产品。在线`analysis_reports`是唯一P0报告载体。旧导出记录可只读归档，但不进入P0建表、API导航、页面或任务状态。

### 14.3 明确删除且不归档为核心扩展

`validation_tasks`、`report_reviews`、`product_events`、`roles`、`user_roles`不属于未来核心扩展，V3不定义其字段或关系。

---

## 15. 实体级保留/合并/删除/新增变更表

| V2实体 | V3处理 | V3落点 | 说明 |
|---|---|---|---|
| `tenant` | 保留 | `tenants` | 租户隔离 |
| `user` | 修改保留 | `users.role_code` | 仅user/admin |
| `role` | 删除 | 无 | 删除动态RBAC实体 |
| `user_role` | 删除 | 无 | 删除多角色分配 |
| `enterprise_profile` | 修改保留 | `enterprise_profiles.constraints` | 合并企业约束 |
| `enterprise_constraint` | 合并 | `enterprise_profiles.constraints` | JSON Schema保留原字段语义 |
| `manufacturing_capability` | 保留 | `manufacturing_capabilities` | 企业适配依据 |
| `product` | 保留 | `products` | 核心产品主档 |
| `file_asset` | 修改保留 | `file_assets` | 移除核心报告导出类型 |
| `product_parse_job` | 保留 | `product_parse_jobs` | 异步资料解析 |
| `product_parse_job_file` | 保留 | `product_parse_job_files` | 文件级结果 |
| `product_profile_version` | 保留 | `product_profile_versions` | 冻结产品画像 |
| `product_attribute` | 保留 | `product_attributes` | 来源和置信度 |
| `market_dataset` | 修改保留 | `market_datasets.field_mapping` | 合并字段映射 |
| `dataset_field_mapping` | 合并 | `market_datasets.field_mapping` | 删除独立实体 |
| `market_listing` | 修改保留 | `market_listings.normalized_attributes` | 合并商品属性 |
| `listing_attribute` | 合并 | `market_listings.normalized_attributes` | 删除独立实体 |
| `competitor_match` | 修改保留 | `competitor_matches` | 删除商品级人工复核字段 |
| `competitor_set_confirmation` | 删除/替代 | `user_confirmations` | 统一确认协议 |
| `review` | 保留 | `reviews` | 原文保护 |
| `review_aspect` | 保留 | `review_aspects` | Span和模型追溯 |
| `insight_cluster` | 保留 | `insight_clusters` | 需求聚类 |
| `insight_cluster_member` | 保留 | `cluster_members` | 聚类关系 |
| `market_metric` | 保留 | `market_metrics` | 确定性指标 |
| `price_band` | 保留 | `price_bands` | 价格口径 |
| `analysis_job` | 修改保留 | `analysis_tasks` | 内外阶段分离 |
| `analysis_stage_run` | 修改保留 | `task_stage_runs` | Supervisor自动运维 |
| `workflow_partial_failure` | 保留 | `workflow_partial_failures` | 部分失败 |
| `workflow_checkpoint` | 保留 | `workflow_checkpoints` | 安全恢复 |
| `workflow_control_event` | 修改保留 | `workflow_control_events` | 统一恢复和内部Outbox |
| 无 | 新增 | `user_confirmations` | 九字段State及回答审计 |
| `model_run` | 保留 | `ai_model_runs` | 模型追溯 |
| `prompt_template` | 保留 | `prompt_templates` | Prompt版本 |
| `model_route_config` | 修改保留 | `model_route_configs.compute_config` | 合并算力配置 |
| `compute_quota` | 合并 | `model_route_configs.compute_config` | 不再独立实体 |
| `market_opportunity` | 扩展保留 | `market_opportunities` | 合并评分和适配 |
| `opportunity_score` | 合并 | `market_opportunities`评分字段 | 删除独立实体 |
| `manufacturing_requirement` | 合并 | `market_opportunities.manufacturing_fit` | 保留要求语义 |
| `enterprise_fit_detail` | 合并 | `market_opportunities.manufacturing_fit` | 保留适配语义 |
| `product_recommendation` | 修改保留 | `product_recommendations` | 删除专家复核字段 |
| `validation_task` | 删除 | 无 | 验证方法仍保留在建议中 |
| `evidence_link` | 修改保留 | `evidence_links` | 支持JSON内部claim_path |
| `analysis_report` | 修改保留 | `analysis_reports.sections` | 在线报告直接可见 |
| `report_section` | 合并 | `analysis_reports.sections` | 删除独立实体 |
| `report_review` | 删除 | 无 | 删除多人评审 |
| `product_event` | 删除 | 无 | 删除产品运营事件 |
| `listing_generation_record` | 移出核心 | 非核心扩展附录 | 不进入P0 |
| `report_export` | 移出核心 | 非核心扩展附录 | 不进入P0 |

---

## 16. 后续跨文档依赖

1. **PostgreSQL数据库设计V3：**依据本字典生成精简DDL、CHECK、JSON Schema校验策略、部分唯一索引及V2→V3无损迁移；旧表在验证前不得删除。
2. **RESTful API V3：**使用五阶段、统一确认、聚合任务状态和在线报告字段；移除竞品/专家专用确认与user细粒度重试接口。
3. **页面交互原型V3：**6+1页面只绑定API V3；统一确认卡片使用九字段State。
4. **Mermaid V3：**更新ER图，体现六组合并、统一确认及非核心实体移出。
5. **测试用例V2：**覆盖role_code约束、租户隔离、JSON Schema、评论原文Span、评分/置信度、确认幂等、Checkpoint和合并数据迁移。
6. **最终一致性校验：**校验PRD→Agent→数据字典→数据库→API→页面→测试的字段和枚举闭环。

仍需后续文档最终确定：V2物理表到V3的迁移批次、合并JSON字段的PostgreSQL CHECK/应用Schema实现、历史竞品/专家确认数据迁移到统一确认的规则，以及API V3路径和响应包装结构。

---

## 17. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-08 | 建立FurniScope核心业务数据字段基线 |
| V2.0 | 2026-08-09 | 补充工作流恢复、部分失败、Checkpoint、控制事件及API投影 |
| V3.0 | 2026-08-09 | 按超级AI员工形态精简角色和实体，统一user_confirmation并完成六组数据结构合并 |
| V3.1 | 2026-08-10 | 补齐认证会话、Token投影、统一Envelope、分页、ETag、模型错误和Outbox租约字段 |
| V3.2 | 2026-08-10 | 新增持久化API幂等实体，锁定user_id兼容投影及Embedding/Rerank内部响应字段 |

## 18. 本次变更摘要

- `users.role_code`收敛为`user/admin`，删除`roles/user_roles`；
- 新增统一`user_confirmations`数据库实体、Agent State和API投影；
- 将企业约束、数据集字段映射和商品标准属性分别合并为JSON字段；
- 将机会评分、制造要求和企业适配合并进`market_opportunities`；
- 将报告章节合并进`analysis_reports.sections`；
- 删除`validation_tasks/report_reviews/product_events`；
- 将算力配额合并为admin管理的`model_route_configs.compute_config`；
- 将Listing生成和文件导出移出核心数据字典；
- 区分数据库实体、Agent State和API衍生字段；
- 保留评论原文保护、证据Span、评分置信度、模型运行、数据范围快照、证据链、幂等、部分失败和Checkpoint；
- 重建实体关系、主外键、索引需求和P0实现清单；
- 增加实体级保留/合并/删除/新增变更表和后续跨文档依赖。

## 19. 产品主档与批量导入增量（V3.3，2026-10-07）

### 19.1 `products` 增量字段与规则

| 字段 | 类型 | 规则 |
|---|---|---|
| `sku_compare_key` | TEXT GENERATED | `upper(btrim(sku))`；与`tenant_id`组成唯一索引 |
| `category_code` | ENUM语义 | `sofa/chair/table/bed/storage/other` |
| `lifecycle_status` | ENUM语义 | `concept/sample/active/discontinued` |
| `deleted_at` | TIMESTAMPTZ | 非空表示已归档，不进入正常列表和详情 |

SKU 原文用于展示、导出和外部契约，唯一性判断只使用比较键。品类变化令
`analysis_status=draft`；属性修改进入草稿画像，不覆盖已确认版本。

### 19.2 产品组合关系

| 表 | 关键字段 | 完整性 |
|---|---|---|
| `product_groups` | `tenant_id/group_type/code/name/description` | `group_type=spu/variant/bundle/bom`；租户+类型+编码唯一 |
| `product_group_members` | `tenant_id/group_id/product_id/member_role/quantity` | 同租户复合外键；角色受控；`quantity>0` |

`spu/variant`允许`parent/variant`，`bundle`允许`parent/item`，`bom`允许
`parent/component`。一个产品可属于多个组。

### 19.3 产品导入任务

| 表 | 关键字段 | 规则 |
|---|---|---|
| `product_import_jobs` | `job_uuid/idempotency_key/source_sha256/template_version/sheet_name/header_row/field_mapping/unit_mapping/dictionary_mapping/import_mode/status/preview_sha256/*_rows/schema_snapshot` | 租户内幂等键唯一；文件不超过20 MiB；`create_only/upsert`；状态为`preflighting/ready/blocked/importing/completed/failed/cancelled` |
| `product_import_rows` | `job_id/source_row_number/source_values/normalized_values/validation_errors/validation_warnings/planned_action/is_user_edited/imported_product_id` | 每任务原行号唯一；动作`create/update/skip/invalid`；JSON结构受CHECK约束 |

任务与行均启用并强制 RLS。`imported_product_id`删除时只清空该列并保留
`tenant_id`，使导入证据不因产品归档或清理而跨租户失真。

### 19.4 模板与校验字典

模板版本为`product-master-2026.10.1`，工作表固定为
`填写说明/Product_Master/SKU_Alias/Dictionaries/Examples`。受控词表包括：

- `category_code`：`sofa/chair/table/bed/storage/other`
- `lifecycle_status`：`concept/sample/active/discontinued`
- `dimension_unit`：`mm/cm/m/in`
- `weight_unit`：`g/kg/lb`
- `currency`：`CNY/USD/EUR/GBP`

阻断校验至少包括必填、文本 SKU、长度、非负数、未知枚举、条件单位、文件内大小写
碰撞、库内重复、公式单元格、别名非一一对应和别名目标缺失。非 sofa 画像能力属于警告，
不阻断主档维护。

## 20. 五项市场决策治理增量（V3.4，2026-10-07）

| 实体 | 关键字段 | 完整性规则 |
|---|---|---|
| `market_intelligence_batches` | `batch_uuid/tenant_id/dataset_id/product_id/package_id/package_version/package_sha256/schema_version/status/source_summary/scope_snapshot/imported_by/*_at` | 企业+包ID+版本唯一；同包最多一个active；身份字段不可修改；状态单向迁移 |
| `market_intelligence_lineage` | `record_uuid/tenant_id/batch_id/capability_code/record_kind/source_class/target_table/target_record_id/natural_key/source_locator/derivation_rule/rule_version/input_sha256/payload_sha256/confidence/observed_at/valid_from/valid_until` | 批次+目标表+自然键唯一；双SHA格式校验；置信度0—1；写入后不可修改或删除 |

`capability_code`仅允许`smart_selection/competitor_tracking/review_mining/pricing/
compliance`。`source_class`仅允许`authorized_source_record/derived_result/
planning_assumption/official_source_registry`。两表均启用并强制RLS，普通企业只能访问
当前`tenant_id`，同时受租户迁移写栅栏约束。

业务结果仍写入`market_opportunities`、竞品三表、评论观点与聚类、企业约束及政策源；
治理实体不保存完整评论正文、附件字节或凭据。
