# FurniScope 产品数据字典 V1.0

## 1. 文档说明

| 文档项 | 内容 |
|---|---|
| 产品名称 | FurniScope——家具出海产品机会雷达 |
| 文档名称 | 产品数据字典 |
| 版本 | V1.0 |
| 适用范围 | 初赛方案、复赛 MVP、API Schema、数据库设计、AI 结构化输出和测试验收 |
| MVP 业务范围 | 单企业、沙发品类、单目标市场/平台数据集 |
| 编制日期 | 2026-08-08 |

### 1.1 字段表说明

每张字段表统一包含：

- **字段名称**：建议的 API/数据库逻辑字段名，使用 `snake_case`；
- **字段释义**：字段的唯一业务含义；
- **数据类型**：逻辑类型，实现时可映射到 PostgreSQL/Pydantic/TypeScript；
- **必填**：“是”为实体有效时必填，“条件”为满足指定条件时必填；
- **数据来源**：用户、企业文件、市场数据集、程序计算或 AI 模型；
- **使用场景**：字段的业务消费位置；
- **约束规则**：格式、枚举、范围、主外键、权限或口径。

### 1.2 通用数据类型

| 逻辑类型 | 说明 | PostgreSQL 建议类型 |
|---|---|---|
| UUID | 全局唯一标识 | `uuid` |
| STRING(n) | 最长 n 字符的字符串 | `varchar(n)` |
| TEXT | 长文本 | `text` |
| INT | 整数 | `integer` |
| BIGINT | 大整数 | `bigint` |
| DECIMAL(p,s) | 定点小数 | `numeric(p,s)` |
| BOOLEAN | 布尔值 | `boolean` |
| DATE | 日期 | `date` |
| DATETIME | 带时区时间 | `timestamptz` |
| ENUM | 受控枚举 | `varchar` + CHECK/枚举表 |
| JSON | 结构化扩展数据 | `jsonb` |
| ARRAY<T> | 同类型数组 | `jsonb` 或关联表 |
| VECTOR(d) | d 维向量 | `vector(d)` |
| URI | 受控资源地址 | `text` |

### 1.3 全局字段规则

1. 所有业务实体使用 UUID 主键，不向外暴露自增整数。
2. 所有租户业务表必须包含 `tenant_id`，查询必须先按租户过滤。
3. 所有可变业务实体必须包含创建、更新和版本字段。
4. 时间以 UTC 存储，前端按用户时区展示；时间格式为 ISO 8601。
5. 币种使用 ISO 4217 三位代码；国家使用 ISO 3166-1 alpha-2 代码；语言使用 BCP 47 代码。
6. 金额不使用浮点数；金额与币种必须成对出现。
7. “未知”不等于“否”或 0；三态数据使用 `yes/no/unknown`。
8. AI 推断字段必须同时记录来源、置信度、模型和 Prompt 版本。
9. 销量、搜索量不可得时，间接数据必须标记为代理指标。
10. 所有评分字段默认范围 0—100；置信度默认范围 0—1。

---

## 2. 公共字段与审计数据

### 2.1 公共实体字段 `BaseEntity`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 实体唯一标识 | UUID | 是 | 系统生成 | 主键、API 引用 | 主键，创建后不可变 |
| `tenant_id` | 所属企业租户 | UUID | 条件 | 认证上下文 | 数据隔离 | 租户业务表必填，外键指向 `tenant.id` |
| `created_at` | 创建时间 | DATETIME | 是 | 系统生成 | 审计、排序 | UTC，不可修改 |
| `created_by` | 创建人 | UUID | 条件 | 认证上下文 | 审计 | 系统任务可为系统账号 |
| `updated_at` | 最后更新时间 | DATETIME | 是 | 系统生成 | 并发控制、审计 | 每次有效更新自动刷新 |
| `updated_by` | 最后更新人 | UUID | 条件 | 认证上下文 | 审计 | 外键指向 `user.id` 或系统账号 |
| `version` | 乐观锁版本 | INT | 是 | 系统计算 | 防止并发覆盖 | 初始 1，每次更新 +1 |
| `is_deleted` | 逻辑删除标记 | BOOLEAN | 是 | 系统生成 | 数据保留 | 默认 `false` |
| `deleted_at` | 删除时间 | DATETIME | 条件 | 系统生成 | 删除审计 | `is_deleted=true` 时必填 |
| `deleted_by` | 删除操作人 | UUID | 条件 | 认证上下文 | 删除审计 | `is_deleted=true` 时必填 |

### 2.2 审计日志 `audit_log`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 日志 ID | UUID | 是 | 系统 | 审计 | 主键 |
| `tenant_id` | 租户 ID | UUID | 条件 | 认证上下文 | 租户隔离 | 平台级事件可为空 |
| `actor_id` | 操作人/系统账号 | UUID | 是 | 认证上下文 | 追责与排查 | 必须可识别人或系统 |
| `action` | 操作类型 | ENUM | 是 | 系统 | 审计筛选 | `create/read/export/update/delete/login/approve/reject` |
| `resource_type` | 资源类型 | STRING(64) | 是 | 系统 | 审计定位 | 使用稳定实体名 |
| `resource_id` | 资源 ID | UUID | 条件 | 系统 | 审计定位 | 登录等无资源事件可空 |
| `before_snapshot` | 变更前摘要 | JSON | 否 | 系统 | 追溯修改 | 敏感字段脱敏，不存密钥 |
| `after_snapshot` | 变更后摘要 | JSON | 否 | 系统 | 追溯修改 | 敏感字段脱敏 |
| `ip_address` | 请求 IP | STRING(45) | 否 | 网关 | 安全审计 | IPv4/IPv6，按合规周期保留 |
| `user_agent` | 客户端信息 | STRING(512) | 否 | HTTP 请求 | 安全排查 | 长度限制 512 |
| `occurred_at` | 事件时间 | DATETIME | 是 | 系统 | 时序追溯 | UTC，不可修改 |

---

## 3. 用户、租户与权限数据

### 3.1 企业租户 `tenant`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 租户 ID | UUID | 是 | 系统 | 数据隔离 | 主键 |
| `tenant_code` | 租户编码 | STRING(32) | 是 | 系统/管理员 | 运维识别 | 全局唯一，仅大写字母、数字、下划线 |
| `name` | 企业名称 | STRING(200) | 是 | 管理员 | 报告、页面 | 同租户内作为法定/展示名称 |
| `display_name` | 展示简称 | STRING(100) | 否 | 管理员 | UI 展示 | 为空时使用 `name` |
| `industry` | 行业 | ENUM | 是 | 管理员 | 行业本体路由 | V1 固定 `furniture_manufacturing` |
| `default_timezone` | 默认时区 | STRING(64) | 是 | 管理员 | 时间展示 | IANA 时区，如 `Asia/Shanghai` |
| `default_currency` | 默认币种 | STRING(3) | 是 | 管理员 | 金额展示 | ISO 4217 |
| `status` | 租户状态 | ENUM | 是 | 系统管理员 | 访问控制 | `trial/active/suspended/closed` |
| `data_retention_days` | 默认数据保留天数 | INT | 是 | 系统管理员 | 数据清理 | >0，不得超过合规上限 |

### 3.2 用户 `user`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 用户 ID | UUID | 是 | 系统 | 身份识别 | 主键 |
| `tenant_id` | 所属租户 | UUID | 是 | 管理员 | 数据隔离 | 外键指向 `tenant.id` |
| `email` | 登录邮箱 | STRING(254) | 是 | 用户/管理员 | 登录、通知 | 租户内唯一，小写归一化 |
| `phone` | 手机号 | STRING(32) | 否 | 用户 | 身份联系 | E.164 格式，敏感字段 |
| `name` | 用户姓名 | STRING(100) | 是 | 用户/管理员 | 协作、审计 | 1—100 字符 |
| `department` | 部门 | STRING(100) | 否 | 用户 | 用户画像 | 如产品、研发、外贸、运营 |
| `job_title` | 岗位 | STRING(100) | 否 | 用户 | 用户画像 | 不用于权限判断 |
| `locale` | 界面语言 | STRING(16) | 是 | 用户 | 国际化 | BCP 47，默认 `zh-CN` |
| `timezone` | 用户时区 | STRING(64) | 否 | 用户 | 时间展示 | 空时继承租户时区 |
| `status` | 用户状态 | ENUM | 是 | 管理员 | 访问控制 | `invited/active/disabled/locked` |
| `last_login_at` | 最后登录时间 | DATETIME | 否 | 认证系统 | 安全审计 | 登录成功时更新 |

> 密码哈希、MFA 密钥和会话 Token 属于认证安全子系统，不进入普通业务 API；严禁存储明文密码。

### 3.3 角色 `role` 与用户角色 `user_role`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `role.id` | 角色 ID | UUID | 是 | 系统 | RBAC | 主键 |
| `role.code` | 角色编码 | ENUM | 是 | 系统 | 权限判断 | `owner/product_rd/market_ops/sales/admin` |
| `role.name` | 角色名称 | STRING(64) | 是 | 系统 | UI 展示 | 与编码对应 |
| `role.permissions` | 权限集 | ARRAY<STRING> | 是 | 系统管理员 | API 鉴权 | 只使用已注册权限码 |
| `user_role.user_id` | 用户 ID | UUID | 是 | 管理员 | 角色分配 | 复合唯一键之一 |
| `user_role.role_id` | 角色 ID | UUID | 是 | 管理员 | 角色分配 | 复合唯一键之一 |
| `user_role.granted_by` | 授权人 | UUID | 是 | 认证上下文 | 审计 | 必须有授权权限 |
| `user_role.granted_at` | 授权时间 | DATETIME | 是 | 系统 | 审计 | UTC |

---

## 4. 企业与制造能力数据

### 4.1 企业档案 `enterprise_profile`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 企业档案 ID | UUID | 是 | 系统 | 主键 | 一个租户对应一个当前档案 |
| `tenant_id` | 租户 ID | UUID | 是 | 系统 | 数据隔离 | 唯一外键 |
| `business_model` | 业务模式 | ARRAY<ENUM> | 是 | 企业管理员 | 业务语境 | `B2B/B2C/OEM/ODM/brand` 可多选 |
| `primary_categories` | 主营品类 | ARRAY<STRING> | 是 | 企业 | 产品与市场匹配 | 使用品类本体 ID |
| `export_markets` | 已出口市场 | ARRAY<STRING(2)> | 否 | 企业 | 市场经验匹配 | ISO 国家代码 |
| `sales_channels` | 现有销售渠道 | ARRAY<ENUM> | 否 | 企业 | 场景路由 | `offline_buyer/distributor/agent/exhibition/amazon/alibaba/shopify/other` |
| `annual_capacity_note` | 产能补充说明 | TEXT | 否 | 企业 | 交付可行性 | 未结构化信息，不直接计分 |
| `profile_completeness` | 档案完整度 | DECIMAL(5,4) | 是 | 程序计算 | 置信度 | 0—1，基于必要字段权重 |
| `confirmed_by` | 最后确认人 | UUID | 否 | 企业管理员 | 证据级别 | 确认后必填 |
| `confirmed_at` | 最后确认时间 | DATETIME | 否 | 系统 | 新鲜度判断 | 与 `confirmed_by` 同时为空或有值 |

### 4.2 制造能力项 `manufacturing_capability`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 能力项 ID | UUID | 是 | 系统 | 实体引用 | 主键 |
| `tenant_id` | 租户 ID | UUID | 是 | 系统 | 数据隔离 | 外键 |
| `capability_type` | 能力类型 | ENUM | 是 | 企业 | 适配评分 | `category/material/process/customization/packaging/certification/delivery/equipment` |
| `capability_code` | 能力标准码 | STRING(100) | 是 | 行业本体/企业 | 规则匹配 | 租户 + 类型内唯一 |
| `capability_name` | 能力名称 | STRING(200) | 是 | 行业本体/企业 | UI 展示 | 不以自由文本代替标准码 |
| `availability` | 是否具备 | ENUM | 是 | 企业 | 能力门禁 | `yes/no/unknown` |
| `min_value` | 能力下限 | DECIMAL(18,4) | 否 | 企业 | 范围匹配 | 数值能力时使用 |
| `max_value` | 能力上限 | DECIMAL(18,4) | 否 | 企业 | 范围匹配 | 必须 ≥ `min_value` |
| `unit` | 能力单位 | STRING(32) | 条件 | 企业/系统 | 单位换算 | 有数值范围时必填，使用标准单位 |
| `valid_from` | 有效开始日期 | DATE | 否 | 企业 | 新鲜度 | 认证/报价类能力建议必填 |
| `valid_until` | 有效截止日期 | DATE | 否 | 企业 | 过期提醒 | 必须 ≥ `valid_from` |
| `evidence_asset_id` | 证明文件 ID | UUID | 否 | 企业 | 能力追溯 | 外键指向 `file_asset.id` |
| `source_type` | 信息来源 | ENUM | 是 | 系统/用户 | 置信度 | `confirmed_user/document/imported/inferred` |
| `confidence` | 能力置信度 | DECIMAL(5,4) | 是 | 规则/人工 | 适配置信度 | 0—1；人工确认可为 1 |
| `notes` | 能力说明 | TEXT | 否 | 企业 | 专家复核 | 不用于直接评分 |

### 4.3 企业成本与交付约束 `enterprise_constraint`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 约束 ID | UUID | 是 | 系统 | 主键 | 主键 |
| `constraint_type` | 约束类型 | ENUM | 是 | 企业 | 机会门禁 | `cost/moq/lead_time/package_volume/certification/material/process/market` |
| `operator` | 比较符 | ENUM | 是 | 用户 | 规则计算 | `eq/ne/lt/lte/gt/gte/between/in/not_in` |
| `value` | 约束值 | JSON | 是 | 企业 | 规则匹配 | 根据类型校验 Schema |
| `unit` | 单位 | STRING(32) | 条件 | 企业 | 数值换算 | 金额/数值类约束必填 |
| `hardness` | 约束强度 | ENUM | 是 | 企业 | 推荐规则 | `hard/soft`；硬约束不满足时不得强推荐 |
| `effective_at` | 生效时间 | DATETIME | 是 | 企业 | 版本选择 | 不得晚于失效时间 |
| `expires_at` | 失效时间 | DATETIME | 否 | 企业 | 新鲜度 | 过期后不用于高置信度决策 |
| `sensitivity_level` | 敏感级别 | ENUM | 是 | 数据管理员 | 字段级权限 | `internal/confidential/restricted` |

---

## 5. 产品数据

### 5.1 产品主档 `product`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 产品 ID | UUID | 是 | 系统 | 主键 | 主键 |
| `tenant_id` | 租户 ID | UUID | 是 | 系统 | 数据隔离 | 外键 |
| `sku` | 企业 SKU | STRING(100) | 是 | 企业 | 产品识别 | 租户内唯一，去除首尾空格 |
| `name` | 产品名称 | STRING(200) | 是 | 企业 | UI、报告 | 不以 AI 推断覆盖原名 |
| `category_code` | 标准品类码 | STRING(100) | 是 | 用户确认/AI 建议 | 竞品匹配 | 必须存在于品类本体 |
| `lifecycle_status` | 产品生命周期 | ENUM | 是 | 企业 | 分析场景 | `concept/sample/active/discontinued` |
| `analysis_status` | 分析准备状态 | ENUM | 是 | 系统 | 任务门禁 | `draft/profile_pending/ready/archived` |
| `primary_image_id` | 主图资产 ID | UUID | 条件 | 用户 | 视觉理解、UI | 发起多模态分析时必填 |
| `current_profile_version_id` | 当前产品画像版本 | UUID | 否 | 系统 | 分析输入 | 外键指向 `product_profile_version.id` |
| `description` | 产品补充说明 | TEXT | 否 | 企业 | 资料解析 | 最长建议 5,000 字符 |

### 5.2 文件资产 `file_asset`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 文件 ID | UUID | 是 | 系统 | 资产关联 | 主键 |
| `tenant_id` | 租户 ID | UUID | 是 | 系统 | 访问控制 | 外键 |
| `owner_type` | 所属实体类型 | ENUM | 是 | 系统 | 资产组织 | `product/capability/dataset/report/other` |
| `owner_id` | 所属实体 ID | UUID | 是 | 系统 | 资产组织 | 与 `owner_type` 一致 |
| `asset_type` | 资产类型 | ENUM | 是 | 用户/系统 | 处理路由 | `image/pdf/spreadsheet/csv/json/cad/report/other` |
| `original_filename` | 原始文件名 | STRING(255) | 是 | 上传请求 | UI 展示 | 过滤路径字符，不作存储键 |
| `storage_key` | 对象存储键 | STRING(512) | 是 | 系统 | 文件读取 | 租户前缀，全局唯一，不直接暴露 |
| `mime_type` | MIME 类型 | STRING(100) | 是 | 文件检测 | 安全与解析 | 必须与实际文件内容一致 |
| `size_bytes` | 文件大小 | BIGINT | 是 | 系统 | 配额、安全 | >0，不超过按类型配置的上限 |
| `sha256` | 文件哈希 | STRING(64) | 是 | 系统 | 去重、完整性 | 64 位小写十六进制 |
| `security_status` | 安全检测状态 | ENUM | 是 | 安全扫描 | 访问门禁 | `pending/clean/rejected/quarantined` |
| `parse_status` | 解析状态 | ENUM | 是 | 解析服务 | 任务进度 | `not_started/processing/succeeded/partial/failed` |
| `retention_until` | 保留截止时间 | DATETIME | 否 | 系统 | 数据清理 | 按租户策略计算 |

### 5.3 产品画像版本 `product_profile_version`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 画像版本 ID | UUID | 是 | 系统 | 分析版本固定 | 主键 |
| `product_id` | 产品 ID | UUID | 是 | 系统 | 产品关联 | 外键，产品删除时受保护 |
| `version_no` | 画像版本号 | INT | 是 | 系统 | 版本对比 | 产品内从 1 递增 |
| `schema_version` | 产品 Schema 版本 | STRING(32) | 是 | 系统 | 兼容性 | 如 `sofa-1.0` |
| `status` | 画像状态 | ENUM | 是 | 用户/系统 | 分析门禁 | `draft/needs_confirmation/confirmed/superseded` |
| `completeness_score` | 完整度 | DECIMAL(5,4) | 是 | 程序 | 置信度 | 0—1，按必要属性计算 |
| `conflict_count` | 未解决冲突数 | INT | 是 | 程序 | 确认门禁 | ≥0；关键冲突>0 时不得确认 |
| `confirmed_by` | 确认人 | UUID | 条件 | 用户 | 审计 | `status=confirmed` 时必填 |
| `confirmed_at` | 确认时间 | DATETIME | 条件 | 系统 | 审计 | `status=confirmed` 时必填 |

### 5.4 产品属性 `product_attribute`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 属性记录 ID | UUID | 是 | 系统 | 主键 | 主键 |
| `profile_version_id` | 画像版本 ID | UUID | 是 | 系统 | 版本固定 | 外键 |
| `attribute_code` | 属性标准码 | STRING(100) | 是 | 品类 Schema | 匹配、报告 | 同版本内唯一 |
| `attribute_name` | 属性名称 | STRING(100) | 是 | 品类 Schema | UI 展示 | 与标准码对应 |
| `value_type` | 值类型 | ENUM | 是 | Schema | 值校验 | `string/number/boolean/enum/array/range` |
| `value` | 属性值 | JSON | 否 | 企业资料/用户/AI | 分析与匹配 | 必须符合 `value_type` 和 Schema |
| `unit` | 标准单位 | STRING(32) | 条件 | 系统 | 单位化计算 | 数值/范围且属性需单位时必填 |
| `raw_value` | 原始字段值 | TEXT | 否 | 文件解析 | 追溯与单位校验 | 不用于直接计算 |
| `source_type` | 来源类型 | ENUM | 是 | 系统 | 信息优先级 | `structured_file/user_input/pdf/vision/model_inference` |
| `source_asset_id` | 来源文件 ID | UUID | 条件 | 系统 | 证据回溯 | 文件来源时必填 |
| `source_locator` | 文件内位置 | JSON | 否 | 解析服务 | 证据高亮 | 如 sheet/cell/page/bounding_box |
| `confidence` | 属性置信度 | DECIMAL(5,4) | 是 | 规则/AI | 冲突解决 | 0—1；人工确认后为 1 |
| `confirmation_status` | 确认状态 | ENUM | 是 | 用户/系统 | 分析门禁 | `unreviewed/confirmed/rejected/unknown` |
| `is_sensitive` | 是否敏感 | BOOLEAN | 是 | Schema | 字段级权限 | 成本、出厂价默认 `true` |

### 5.5 沙发产品 Schema 必要属性

| `attribute_code` | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `sofa_type` | 沙发类型 | ENUM | 是 | 用户确认/AI 建议 | 竞品硬过滤 | `standard/sectional/modular/sleeper/loveseat/recliner/other` |
| `seat_count` | 标称座位数 | INT | 否 | 参数/用户 | 竞品匹配 | 1—10 |
| `style_codes` | 风格标签 | ARRAY<ENUM> | 是 | 用户确认/AI | 审美匹配 | 使用受控风格本体，允许多选 |
| `primary_color` | 主颜色 | STRING(50) | 是 | 图片/用户 | 审美分析 | 使用标准颜色族，可保留原始色名 |
| `upholstery_material` | 面料 | ENUM | 条件 | 参数表/用户 | 清洁、触感、耐用分析 | 不得仅凭图片确认真皮等材质 |
| `frame_material` | 框架材料 | ARRAY<ENUM> | 否 | 参数/用户 | 结构与耐用性 | 不得仅凭图片推断 |
| `filling_material` | 填充材料 | ARRAY<ENUM> | 否 | 参数/用户 | 舒适与耐用性 | 允许多层材料 |
| `foam_density` | 海绵密度 | DECIMAL(8,2) | 否 | 参数/实验 | 耐久建议 | 必须带单位，禁止视觉推断 |
| `overall_width_cm` | 总宽 | DECIMAL(8,2) | 是 | 参数/用户 | 户型匹配 | >0，存储单位 cm |
| `overall_depth_cm` | 总深 | DECIMAL(8,2) | 是 | 参数/用户 | 户型匹配 | >0 |
| `overall_height_cm` | 总高 | DECIMAL(8,2) | 是 | 参数/用户 | 尺寸匹配 | >0 |
| `seat_width_cm` | 座面宽 | DECIMAL(8,2) | 否 | 参数/用户 | 舒适性 | >0 |
| `seat_depth_cm` | 座深 | DECIMAL(8,2) | 条件 | 参数/用户 | 舒适与人群匹配 | 舒适性结论涉及座深时必须已知 |
| `seat_height_cm` | 座高 | DECIMAL(8,2) | 否 | 参数/用户 | 舒适性 | >0 |
| `weight_capacity_kg` | 承重 | DECIMAL(8,2) | 否 | 参数/测试 | 安全与人群 | 禁止视觉推断，必须有参数/测试依据 |
| `net_weight_kg` | 产品净重 | DECIMAL(8,2) | 否 | 参数 | 运输评估 | >0 |
| `assembly_required` | 是否需安装 | BOOLEAN | 是 | 参数/用户 | 安装痛点 | 不得为空 |
| `assembly_time_min` | 建议安装时间 | INT | 条件 | 企业测试 | 安装体验 | `assembly_required=true` 时可填，≥0 |
| `removable_cover` | 座套/面套是否可拆 | ENUM | 是 | 参数/用户 | 清洁需求 | `yes/no/partial/unknown` |
| `washable_cover` | 是否可水洗 | ENUM | 是 | 参数/用户 | 清洁需求 | `yes/no/unknown`；不由可拆自动推断 |
| `modular` | 是否模块化 | BOOLEAN | 是 | 参数/用户 | 功能匹配 | 需有结构依据 |
| `convertible_function` | 可变功能 | ARRAY<ENUM> | 否 | 参数/用户 | 功能卖点 | `bed/storage/recliner/chaise/reconfigurable/other` |
| `package_count` | 包装件数 | INT | 否 | 包装资料 | 履约与安装 | ≥1 |
| `package_volume_cbm` | 总包装体积 | DECIMAL(10,4) | 否 | 包装资料 | 物流可行性 | >0，单位 m³ |
| `compression_packaging` | 是否压缩包装 | ENUM | 否 | 企业 | 物流优化 | `yes/no/unknown` |
| `factory_price` | 出厂报价 | DECIMAL(18,4) | 条件 | 报价/用户 | 利润空间 | 计算利润时必填，敏感字段 |
| `factory_price_currency` | 出厂价币种 | STRING(3) | 条件 | 报价/用户 | 币种换算 | `factory_price` 有值时必填 |
| `price_term` | 贸易术语 | ENUM | 条件 | 报价/用户 | 价格口径 | `EXW/FOB/CIF/DDP/other`；有报价时必填 |
| `moq` | 最小订购量 | INT | 否 | 报价/用户 | B2B 适配 | ≥1，必须同时有 MOQ 单位 |
| `lead_time_days` | 交付周期 | INT | 否 | 企业 | 交付适配 | ≥0，明确是生产还是总交付周期 |
| `certification_codes` | 已有认证 | ARRAY<STRING> | 否 | 认证文件/用户 | 市场准入 | 必须与能力证据关联，不由 AI 自动声明 |

---

## 6. 市场数据集

### 6.1 市场数据集 `market_dataset`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 数据集 ID | UUID | 是 | 系统 | 分析输入 | 主键 |
| `tenant_id` | 所属租户 | UUID | 是 | 系统 | 数据隔离 | 外键 |
| `name` | 数据集名称 | STRING(200) | 是 | 用户 | 任务选择 | 租户内建议唯一 |
| `version_no` | 数据集版本 | INT | 是 | 系统 | 报告复现 | 同数据集系列递增 |
| `platform` | 数据平台 | ENUM | 是 | 用户/数据源 | 市场口径 | `amazon/wayfair/walmart/alibaba/shopify/other` |
| `market_country` | 目标国家 | STRING(2) | 是 | 数据源 | 市场筛选 | ISO 3166-1 alpha-2 |
| `marketplace_code` | 站点代码 | STRING(50) | 否 | 数据源 | 站点区分 | 如 `amazon.com`，不以国家代码替代 |
| `category_code` | 品类码 | STRING(100) | 是 | 数据导入/用户 | 样本口径 | 映射到内部品类本体 |
| `data_start_date` | 数据开始日期 | DATE | 否 | 数据源 | 时间范围 | 趋势分析时必填 |
| `data_end_date` | 数据结束日期 | DATE | 是 | 数据源 | 新鲜度 | 不得晚于导入日期 |
| `source_type` | 数据来源类型 | ENUM | 是 | 用户 | 合规审计 | `enterprise_export/licensed_provider/public_authorized/demo_synthetic` |
| `source_name` | 来源名称 | STRING(200) | 是 | 用户 | 证据说明 | Demo 数据不得伪装为平台官方数据 |
| `authorization_reference` | 授权/合规依据 | TEXT | 条件 | 用户/管理员 | 合规审计 | 非公开授权数据时必填 |
| `import_asset_id` | 导入文件 ID | UUID | 是 | 系统 | 原始数据追溯 | 外键指向 `file_asset.id` |
| `status` | 数据集状态 | ENUM | 是 | 系统 | 任务门禁 | `uploaded/validating/ready/rejected/archived` |
| `listing_count` | 商品数 | INT | 是 | 程序计算 | 样本说明 | ≥0 |
| `review_count` | 原始评论数 | INT | 是 | 程序计算 | 样本说明 | ≥0 |
| `valid_review_count` | 有效评论数 | INT | 是 | 程序计算 | 指标分母 | 0—`review_count` |
| `quality_score` | 数据质量分 | DECIMAL(5,2) | 是 | 规则计算 | 置信度 | 0—100，使用版本化规则 |
| `quality_report` | 质量报告摘要 | JSON | 是 | 程序计算 | 数据预览 | 包含缺失率、重复率、异常率和警告 |
| `limitations` | 数据限制 | ARRAY<TEXT> | 否 | 程序/用户 | 报告风险 | 必须原样传入最终报告 |

### 6.2 导入字段映射 `dataset_field_mapping`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `dataset_id` | 数据集 ID | UUID | 是 | 系统 | 映射关联 | 外键 |
| `source_field` | 原始字段名 | STRING(200) | 是 | 导入文件 | 数据解析 | 保留原文 |
| `target_entity` | 目标实体 | ENUM | 是 | 用户/系统 | 映射规则 | `listing/review/market_metric` |
| `target_field` | 目标字段 | STRING(100) | 是 | 用户/系统 | 标准化 | 必须是注册字段 |
| `transform_rule` | 转换规则 | JSON | 否 | 用户/系统 | 单位、枚举转换 | 只允许白名单转换，不执行任意代码 |
| `mapping_status` | 映射状态 | ENUM | 是 | 用户/系统 | 导入门禁 | `auto_suggested/confirmed/ignored/error` |

---

## 7. 竞品与商品数据

### 7.1 市场商品 `market_listing`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 内部商品 ID | UUID | 是 | 系统 | 关联评论 | 主键 |
| `dataset_id` | 数据集 ID | UUID | 是 | 系统 | 样本范围 | 外键 |
| `platform_listing_id` | 平台商品 ID | STRING(200) | 是 | 数据源 | 去重与追溯 | 平台 + 站点内唯一，如 ASIN |
| `listing_url` | 商品页地址 | URI | 否 | 数据源 | 证据跳转 | 只允许 HTTP/HTTPS，访问权受数据条款限制 |
| `brand` | 品牌 | STRING(200) | 否 | 数据源 | 品牌集中度 | 去除首尾空格，保留原文 |
| `seller_name` | 销售方名称 | STRING(200) | 否 | 数据源 | 销售方分析 | 不作为个人联系信息 |
| `title` | 商品标题 | TEXT | 是 | 数据源 | 竞品匹配、卖点抽取 | 保留原文 |
| `description` | 商品描述 | TEXT | 否 | 数据源 | 卖点抽取 | 保留原文，清理脚本标记 |
| `bullet_points` | 卖点要点 | ARRAY<TEXT> | 否 | 数据源 | 功能渗透分析 | 保留原始顺序 |
| `category_raw` | 平台原始品类 | STRING(300) | 否 | 数据源 | 映射追溯 | 不直接用于内部匹配 |
| `category_code` | 内部品类码 | STRING(100) | 是 | 映射规则/用户 | 竞品过滤 | 必须在品类本体中 |
| `image_urls` | 商品图片地址 | ARRAY<URI> | 否 | 数据源 | 视觉匹配 | 仅存储有权使用的链接/缓存 |
| `currency` | 币种 | STRING(3) | 是 | 数据源 | 价格统计 | ISO 4217 |
| `list_price` | 划线/标价 | DECIMAL(18,4) | 否 | 数据源 | 促销分析 | ≥0，不等于成交价 |
| `sale_price` | 当前售价 | DECIMAL(18,4) | 是 | 数据源 | 价格带 | ≥0，与币种成对 |
| `coupon_value` | 优惠券金额/比例 | JSON | 否 | 数据源 | 净价估算 | 必须标记是金额还是比例 |
| `rating` | 平均评分 | DECIMAL(3,2) | 否 | 数据源 | 满意度 | 范围 0—5，缺失不得记为 0 |
| `rating_count` | 评分数 | INT | 否 | 数据源 | 样本规模 | ≥0 |
| `review_count` | 评论数 | INT | 否 | 数据源 | 样本规模 | ≥0，不假设等于 `rating_count` |
| `rank_value` | 排名数值 | INT | 否 | 数据源 | 表现代理指标 | 必须同时有排名类目和抓取时间 |
| `rank_category` | 排名所属类目 | STRING(300) | 条件 | 数据源 | 排名口径 | `rank_value` 有值时必填 |
| `sales_value` | 销量数值 | DECIMAL(18,4) | 否 | 授权数据源 | 需求热度 | 只有明确真实/估算口径时才可填 |
| `sales_value_type` | 销量数值类型 | ENUM | 条件 | 数据源 | 口径标记 | `actual/estimated/proxy`；`sales_value` 有值时必填 |
| `sales_period` | 销量统计周期 | ENUM | 条件 | 数据源 | 可比性 | `daily/weekly/monthly/annual/unknown` |
| `first_available_at` | 首次上架时间 | DATETIME | 否 | 数据源 | 新品分析 | 不得晚于数据时间 |
| `observed_at` | 本条快照时间 | DATETIME | 是 | 数据源 | 时序分析 | UTC，价格/排名必须有快照时间 |
| `availability_status` | 可售状态 | ENUM | 否 | 数据源 | 竞品有效性 | `in_stock/out_of_stock/unavailable/unknown` |
| `raw_payload` | 脱敏原始记录 | JSON | 否 | 导入数据 | 排查与重处理 | 不存储超范围个人数据 |

### 7.2 商品标准属性 `listing_attribute`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `listing_id` | 商品 ID | UUID | 是 | 系统 | 外键 | 外键 |
| `attribute_code` | 标准属性码 | STRING(100) | 是 | 规则/AI | 竞品匹配 | 与产品 Schema 对齐 |
| `value` | 属性值 | JSON | 否 | 商品页/AI 抽取 | 相似度 | 符合属性 Schema |
| `unit` | 单位 | STRING(32) | 条件 | 系统 | 尺寸可比 | 数值属性按标准单位 |
| `source_locator` | 商品内容位置 | JSON | 否 | 抽取系统 | 证据追溯 | 如 title/bullet/image/description |
| `confidence` | 抽取置信度 | DECIMAL(5,4) | 是 | AI/规则 | 匹配权重 | 0—1 |

### 7.3 竞品匹配 `competitor_match`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 匹配 ID | UUID | 是 | 系统 | 竞品集版本 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 任务隔离 | 外键 |
| `product_profile_version_id` | 企业产品画像版本 | UUID | 是 | 系统 | 可复现匹配 | 外键 |
| `listing_id` | 候选竞品 ID | UUID | 是 | 系统 | 竞品关联 | 外键 |
| `competitor_type` | 竞品类型 | ENUM | 是 | 系统/用户 | 分组统计 | `direct/benchmark/substitute/excluded` |
| `category_score` | 品类相似分 | DECIMAL(5,2) | 是 | 程序 | 可解释匹配 | 0—100 |
| `function_score` | 功能相似分 | DECIMAL(5,2) | 是 | 程序/AI属性 | 可解释匹配 | 0—100 |
| `style_score` | 风格相似分 | DECIMAL(5,2) | 是 | 程序/AI属性 | 可解释匹配 | 0—100 |
| `price_score` | 价格带相似分 | DECIMAL(5,2) | 是 | 程序 | 可解释匹配 | 币种统一后计算，0—100 |
| `material_score` | 材质相似分 | DECIMAL(5,2) | 是 | 程序/AI属性 | 可解释匹配 | 0—100 |
| `scenario_score` | 场景相似分 | DECIMAL(5,2) | 是 | 程序/AI属性 | 可解释匹配 | 0—100 |
| `overall_score` | 综合相似分 | DECIMAL(5,2) | 是 | 程序 | 候选排序 | 0—100，按版本化权重计算 |
| `rerank_score` | 模型重排分 | DECIMAL(8,6) | 否 | Rerank 模型 | 最终排序 | 不与 0—100 业务分混用 |
| `match_reasons` | 匹配原因 | ARRAY<TEXT> | 是 | 程序/AI | 竞品复核 | 必须基于已有属性 |
| `review_status` | 人工复核状态 | ENUM | 是 | 用户 | 样本门禁 | `pending/included/excluded/type_changed` |
| `review_note` | 人工复核原因 | TEXT | 条件 | 用户 | 迭代学习 | 排除或改类时必填 |
| `reviewed_by` | 复核人 | UUID | 条件 | 用户 | 审计 | 已复核时必填 |
| `reviewed_at` | 复核时间 | DATETIME | 条件 | 系统 | 审计 | 已复核时必填 |

---

## 8. 评论与消费需求数据

### 8.1 原始评论 `review`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 内部评论 ID | UUID | 是 | 系统 | 证据关联 | 主键 |
| `dataset_id` | 数据集 ID | UUID | 是 | 系统 | 样本口径 | 外键 |
| `listing_id` | 所属商品 ID | UUID | 是 | 导入映射 | 商品覆盖统计 | 外键，无法关联时评论不进入有效样本 |
| `platform_review_id` | 平台评论 ID | STRING(200) | 否 | 数据源 | 去重 | 存在时在平台/站点内唯一 |
| `rating` | 评论星级 | DECIMAL(3,2) | 是 | 数据源 | 情感校验 | 0—5，不以星级直接替代观点情感 |
| `title` | 评论标题 | TEXT | 否 | 数据源 | 语义抽取 | 保留原文 |
| `body_original` | 评论原文 | TEXT | 是 | 数据源 | AI 抽取、证据展示 | 不得被翻译或清洗结果覆盖 |
| `language` | 原文语言 | STRING(16) | 是 | 语言检测 | 模型路由 | BCP 47；低置信度时记录 `und` |
| `body_translated_zh` | 中文翻译 | TEXT | 否 | AI/翻译服务 | 中文用户查看 | 必须与原文并存，标记翻译模型 |
| `reviewed_at` | 评论发布时间 | DATETIME | 否 | 数据源 | 时间新鲜度 | 不得晚于观测时间 |
| `verified_purchase` | 是否验证购买 | ENUM | 是 | 数据源 | 可信度 | `yes/no/unknown` |
| `helpful_votes` | 有用票数 | INT | 否 | 数据源 | 评论权重 | ≥0，缺失不记为 0 |
| `variant_info` | 购买变体信息 | JSON | 否 | 数据源 | 颜色/尺寸问题定位 | 只保留产品变体，不保留个人身份信息 |
| `is_duplicate` | 是否重复 | BOOLEAN | 是 | 清洗程序 | 有效样本筛选 | 默认 `false` |
| `is_spam` | 是否疑似垃圾 | BOOLEAN | 是 | 规则/AI | 有效样本筛选 | 需保留判定原因 |
| `is_valid` | 是否进入分析 | BOOLEAN | 是 | 规则 | 分析样本 | 根据关联、重复、垃圾和内容规则计算 |
| `invalid_reason` | 无效原因 | ENUM | 条件 | 清洗程序 | 质量报告 | `is_valid=false` 时必填 |
| `content_hash` | 归一化内容哈希 | STRING(64) | 是 | 程序 | 去重与缓存 | SHA-256 |

### 8.2 评论观点 `review_aspect`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 观点 ID | UUID | 是 | 系统 | 聚类、证据 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 模型版本隔离 | 外键 |
| `review_id` | 原始评论 ID | UUID | 是 | 系统 | 证据追溯 | 外键 |
| `aspect_index` | 评论内观点序号 | INT | 是 | 系统 | 稳定定位 | 同评论内从 1 递增 |
| `taxonomy_code` | 需求本体码 | STRING(150) | 是 | AI + 本体校验 | 需求统计 | 必须是当前本体有效节点 |
| `product_attribute_code` | 对应产品属性 | STRING(100) | 否 | AI/规则 | 工程化转换 | 必须是产品 Schema 属性 |
| `opinion_text` | 观点规范化表达 | TEXT | 是 | AI | 聚类和报告 | 不得添加原文不存在的事实 |
| `sentiment` | 观点情感 | ENUM | 是 | AI | 正负需求统计 | `positive/negative/neutral/mixed` |
| `severity` | 问题严重度 | INT | 条件 | AI + 规则 | 问题优先级 | 负向观点必填，1—5，需有 rubric |
| `user_profile_codes` | 受影响人群 | ARRAY<STRING> | 否 | AI | 人群洞察 | 仅抽取文本中显式/可合理推断信息 |
| `usage_scenario_codes` | 使用场景 | ARRAY<STRING> | 否 | AI | 场景洞察 | 使用受控本体 |
| `purchase_driver` | 是否购买动机 | BOOLEAN | 是 | AI | 正向卖点提取 | 不以正向情感自动等同 |
| `evidence_start` | 原文证据起始位置 | INT | 是 | AI/程序 | 原文高亮 | ≥0，以 Unicode 字符偏移计 |
| `evidence_end` | 原文证据结束位置 | INT | 是 | AI/程序 | 原文高亮 | > `evidence_start`，不超过原文长度 |
| `evidence_quote` | 证据文本快照 | TEXT | 是 | 程序截取 | 报告证据 | 必须与原文 span 完全一致 |
| `extraction_confidence` | 抽取置信度 | DECIMAL(5,4) | 是 | AI/校准规则 | 样本权重 | 0—1 |
| `model_run_id` | 模型运行 ID | UUID | 是 | AI 网关 | 结果追溯 | 外键指向 `model_run.id` |
| `validation_status` | 验证状态 | ENUM | 是 | 系统/人工 | 测试与反馈 | `unreviewed/accepted/corrected/rejected` |

### 8.3 需求聚类 `insight_cluster`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 聚类 ID | UUID | 是 | 系统 | 需求洞察 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 任务隔离 | 外键 |
| `cluster_no` | 任务内聚类序号 | INT | 是 | 程序 | 稳定定位 | 任务内唯一 |
| `cluster_name` | 聚类名称 | STRING(200) | 是 | AI + 本体映射 | 报告展示 | 需描述具体问题/动机，避免“其他” |
| `taxonomy_code` | 对应需求本体 | STRING(150) | 是 | 规则/AI | 分类统计 | 本体内有效 |
| `sentiment` | 聚类主情感 | ENUM | 是 | 程序 | 洞察分组 | 由观点分布计算，可为 `mixed` |
| `aspect_count` | 观点数 | INT | 是 | 程序 | 提及频率 | ≥1 |
| `review_count` | 去重评论数 | INT | 是 | 程序 | 提及频率 | ≤ `aspect_count` |
| `listing_count` | 覆盖商品数 | INT | 是 | 程序 | 跨商品覆盖 | ≥1 |
| `mention_rate` | 提及率 | DECIMAL(7,6) | 是 | 程序 | 需求热度 | 0—1，必须同时记录分母口径 |
| `cross_listing_rate` | 跨商品覆盖率 | DECIMAL(7,6) | 是 | 程序 | 防止单品偏差 | 0—1 |
| `avg_severity` | 平均严重度 | DECIMAL(4,2) | 条件 | 程序 | 痛点优先级 | 负向聚类必填，1—5 |
| `freshness_score` | 时间新鲜分 | DECIMAL(5,4) | 是 | 程序 | 问题重要度 | 0—1，算法版本化 |
| `importance_score` | 问题/动机重要度 | DECIMAL(5,2) | 是 | 程序 | Top 洞察排序 | 0—100，不由模型主观生成 |
| `cluster_confidence` | 聚类置信度 | DECIMAL(5,4) | 是 | 程序 | 报告置信度 | 0—1 |
| `centroid_vector` | 聚类中心向量 | VECTOR(d) | 否 | 聚类算法 | 增量归类 | d 与 embedding 模型一致 |
| `algorithm_version` | 聚类算法版本 | STRING(64) | 是 | 系统 | 可复现 | 不得为空 |

### 8.4 聚类成员 `insight_cluster_member`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `cluster_id` | 聚类 ID | UUID | 是 | 系统 | 聚类关联 | 复合主键 |
| `review_aspect_id` | 观点 ID | UUID | 是 | 系统 | 证据列表 | 复合主键 |
| `distance` | 与聚类中心距离 | DECIMAL(10,8) | 是 | 聚类算法 | 成员质量 | 距离口径与算法一致 |
| `is_representative` | 是否代表证据 | BOOLEAN | 是 | 程序/人工 | 报告引用 | 每个聚类建议至少 3 条代表证据，数据不足除外 |

---

## 9. 市场指标与价格数据

### 9.1 市场指标 `market_metric`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 指标 ID | UUID | 是 | 系统 | 证据链 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 任务口径 | 外键 |
| `metric_code` | 指标码 | STRING(100) | 是 | 指标定义 | 计算和展示 | 使用版本化指标库 |
| `metric_name` | 指标名称 | STRING(200) | 是 | 指标定义 | UI 展示 | 与指标码对应 |
| `dimension_type` | 统计维度 | ENUM | 是 | 程序 | 分组分析 | `market/price_band/brand/feature/cluster/time/product` |
| `dimension_value` | 维度值 | JSON | 是 | 程序 | 细分证据 | 符合维度 Schema |
| `metric_value` | 指标值 | DECIMAL(24,8) | 是 | 程序 | 图表、评分 | 不由大模型生成 |
| `metric_unit` | 指标单位 | STRING(32) | 是 | 指标定义 | 数值解释 | 如 `count/ratio/USD/score` |
| `numerator` | 分子 | DECIMAL(24,8) | 否 | 程序 | 比例口径 | 比率指标建议必填 |
| `denominator` | 分母 | DECIMAL(24,8) | 否 | 程序 | 比例口径 | >0，比率指标建议必填 |
| `value_type` | 数值可信类型 | ENUM | 是 | 指标定义 | 报告标记 | `observed/calculated/estimated/proxy` |
| `period_start` | 统计期开始 | DATE | 否 | 数据集 | 趋势口径 | 趋势指标必填 |
| `period_end` | 统计期结束 | DATE | 否 | 数据集 | 趋势口径 | ≥ `period_start` |
| `sample_size` | 样本量 | INT | 是 | 程序 | 置信度 | ≥0 |
| `confidence` | 指标置信度 | DECIMAL(5,4) | 是 | 规则 | 报告风险 | 0—1 |
| `formula_version` | 公式版本 | STRING(64) | 是 | 系统 | 结果复现 | 不得为空 |

### 9.2 价格带 `price_band`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 价格带 ID | UUID | 是 | 系统 | 价格分布 | 主键 |
| `analysis_job_id` | 任务 ID | UUID | 是 | 系统 | 任务口径 | 外键 |
| `currency` | 统一币种 | STRING(3) | 是 | 任务配置 | 价格比较 | ISO 4217 |
| `lower_bound` | 价格下界 | DECIMAL(18,4) | 是 | 程序 | 分箱 | ≥0，包含下界 |
| `upper_bound` | 价格上界 | DECIMAL(18,4) | 否 | 程序 | 分箱 | > `lower_bound`，最高档可为空 |
| `listing_count` | 商品数 | INT | 是 | 程序 | 价格分布 | ≥0 |
| `median_rating` | 评分中位数 | DECIMAL(3,2) | 否 | 程序 | 价格—满意度 | 0—5 |
| `review_share` | 评论份额 | DECIMAL(7,6) | 否 | 程序 | 热度代理 | 0—1，明确是评论份额而非销售份额 |
| `feature_codes` | 高渗透功能 | ARRAY<STRING> | 否 | 程序 | 卖点对比 | 必须关联功能渗透指标 |

---

## 10. AI 分析任务与模型运行数据

### 10.1 分析任务 `analysis_job`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 任务 ID | UUID | 是 | 系统 | 工作流主键 | 主键 |
| `tenant_id` | 租户 ID | UUID | 是 | 认证上下文 | 数据隔离 | 外键 |
| `job_name` | 任务名称 | STRING(200) | 是 | 用户/系统 | 任务列表 | 默认由产品+市场+日期生成 |
| `job_type` | 任务类型 | ENUM | 是 | 用户 | 工作流路由 | `product_market_fit/product_improvement/opportunity_compare/post_launch_review` |
| `product_id` | 产品 ID | UUID | 是 | 用户 | 主分析对象 | V1 单任务只允许一个主产品 |
| `product_profile_version_id` | 产品画像版本 | UUID | 是 | 系统 | 可复现 | 发起时固定，不随产品更新漂移 |
| `dataset_id` | 市场数据集 ID | UUID | 是 | 用户 | 分析样本 | 必须 `status=ready` |
| `target_country` | 目标国家 | STRING(2) | 是 | 用户/数据集 | 市场口径 | 必须与数据集一致 |
| `target_platform` | 目标平台 | ENUM | 是 | 数据集 | 市场口径 | 必须与数据集一致 |
| `analysis_currency` | 分析币种 | STRING(3) | 是 | 用户/市场 | 价格统一 | ISO 4217 |
| `status` | 任务状态 | ENUM | 是 | 工作流 | 进度与门禁 | `draft/profile_review/data_check/competitor_review/queued/running/human_review/published/partial/failed/cancelled` |
| `current_stage` | 当前阶段 | ENUM | 是 | 工作流 | SSE 进度 | 与状态机一致 |
| `progress_percent` | 进度百分比 | DECIMAL(5,2) | 是 | 工作流 | UI 进度 | 0—100，不得回退，重试除外 |
| `ontology_version` | 家具本体版本 | STRING(64) | 是 | 系统 | 可复现 | 发起时固定 |
| `scoring_version` | 评分版本 | STRING(64) | 是 | 系统 | 可复现 | 发起时固定 |
| `prompt_bundle_version` | Prompt 组版本 | STRING(64) | 是 | AI 网关 | 可复现 | 发起时固定 |
| `started_at` | 开始运行时间 | DATETIME | 否 | 工作流 | 性能统计 | 首个执行节点启动时记录 |
| `completed_at` | 完成时间 | DATETIME | 否 | 工作流 | 性能统计 | 终态时记录 |
| `failure_code` | 失败码 | STRING(100) | 条件 | 工作流 | 异常处理 | `status=failed/partial` 时必填 |
| `failure_message` | 用户可读失败说明 | TEXT | 条件 | 工作流 | UI 错误 | 不包含密钥和敏感数据 |

### 10.2 工作流节点 `analysis_stage_run`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 节点运行 ID | UUID | 是 | 系统 | 幂等与重试 | 主键 |
| `analysis_job_id` | 任务 ID | UUID | 是 | 系统 | 任务关联 | 外键 |
| `stage_code` | 阶段码 | ENUM | 是 | 工作流 | 流程编排 | `product_parse/data_quality/competitor_match/review_extract/embedding/cluster/market_metric/capability_match/scoring/recommendation/report` |
| `attempt_no` | 尝试次数 | INT | 是 | 工作流 | 重试 | 从 1 递增 |
| `idempotency_key` | 幂等键 | STRING(128) | 是 | 工作流 | 防止重复执行 | 全局唯一 |
| `status` | 节点状态 | ENUM | 是 | 工作流 | 进度 | `queued/running/succeeded/failed/skipped/cancelled` |
| `input_ref` | 输入快照引用 | JSON | 是 | 工作流 | 可复现 | 存 ID/版本/哈希，不重复存敏感原文 |
| `output_ref` | 输出引用 | JSON | 否 | 工作流 | 下游节点 | 成功时必填 |
| `started_at` | 开始时间 | DATETIME | 否 | 系统 | 耗时 | 运行时必填 |
| `ended_at` | 结束时间 | DATETIME | 否 | 系统 | 耗时 | 终态必填，≥开始时间 |
| `error_code` | 错误码 | STRING(100) | 条件 | 系统 | 重试策略 | 失败时必填 |
| `retryable` | 是否可重试 | BOOLEAN | 条件 | 错误分类 | 重试策略 | 失败时必填 |

### 10.3 模型运行 `model_run`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 模型运行 ID | UUID | 是 | AI 网关 | 追溯 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 成本汇总 | 外键 |
| `stage_run_id` | 工作流节点 ID | UUID | 是 | 系统 | 耗时定位 | 外键 |
| `provider` | 模型提供方/路由 | STRING(100) | 是 | Model Router | 模型追溯 | 不存 API Key |
| `model_id` | 实际模型 ID | STRING(200) | 是 | Model Router | 质量回归 | 记录实际返回 ID |
| `task_type` | 模型任务类型 | ENUM | 是 | AI 网关 | 成本和质量分析 | `vision_extract/text_extract/translate/embed/rerank/reason/report` |
| `prompt_template_id` | Prompt 模板 ID | UUID | 条件 | AI 网关 | 可复现 | 生成/抽取类任务必填 |
| `prompt_version` | Prompt 版本 | STRING(64) | 条件 | AI 网关 | 回归测试 | 使用 Prompt 时必填 |
| `input_hash` | 脱敏输入哈希 | STRING(64) | 是 | AI 网关 | 缓存与追溯 | SHA-256，不存敏感原输入 |
| `output_schema_version` | 输出 Schema 版本 | STRING(64) | 条件 | AI 网关 | 结构验证 | 结构化输出必填 |
| `input_tokens` | 输入 Token | INT | 否 | Model Router | 算力统计 | ≥0 |
| `output_tokens` | 输出 Token | INT | 否 | Model Router | 算力统计 | ≥0 |
| `image_count` | 图片输入数 | INT | 是 | AI 网关 | 多模态成本 | 默认 0 |
| `latency_ms` | 调用耗时 | INT | 是 | AI 网关 | 性能监控 | ≥0 |
| `estimated_cost` | 估算成本 | DECIMAL(18,8) | 否 | 成本规则 | 算力预算 | 必须带币种/额度单位 |
| `cost_unit` | 成本单位 | STRING(16) | 条件 | 成本规则 | 算力预算 | `CNY/USD/credit`；有成本时必填 |
| `status` | 调用状态 | ENUM | 是 | AI 网关 | 重试与监控 | `succeeded/failed/timeout/schema_failed/cached` |
| `retry_count` | 重试次数 | INT | 是 | AI 网关 | 稳定性 | ≥0 |
| `schema_valid` | Schema 是否通过 | BOOLEAN | 条件 | 校验器 | 幻觉控制 | 结构化输出必填 |
| `error_code` | 模型错误码 | STRING(100) | 条件 | AI 网关 | 异常排查 | 失败时必填 |

### 10.4 Prompt 模板 `prompt_template`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | Prompt 模板 ID | UUID | 是 | 系统 | 运行关联 | 主键 |
| `code` | 模板码 | STRING(100) | 是 | 开发者 | 调用路由 | 全局唯一 |
| `version` | 模板版本 | STRING(64) | 是 | 开发者 | 回归评测 | 模板码+版本唯一 |
| `task_type` | 任务类型 | ENUM | 是 | 开发者 | 模型路由 | 与 `model_run.task_type` 对齐 |
| `template_content` | Prompt 内容 | TEXT | 是 | 开发者 | 模型调用 | 变更必须新版本，不原地覆盖 |
| `output_schema` | 输出 JSON Schema | JSON | 条件 | 开发者 | 结构验证 | 结构化任务必填 |
| `status` | 模板状态 | ENUM | 是 | 管理员 | 发布管理 | `draft/testing/active/retired` |
| `evaluation_set_version` | 关联评测集版本 | STRING(64) | 条件 | 评测系统 | 发布门禁 | `active` 时必填 |

---

## 11. 产品机会、制造适配与决策数据

### 11.1 市场机会 `market_opportunity`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 机会 ID | UUID | 是 | 系统 | 报告主实体 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 任务关联 | 外键 |
| `opportunity_code` | 任务内机会码 | STRING(50) | 是 | 系统 | 定位与导出 | 任务内唯一 |
| `title` | 机会名称 | STRING(200) | 是 | AI + 结构化事实 | 报告展示 | 必须包含品类/人群/场景/功能中至少两项 |
| `description` | 机会说明 | TEXT | 是 | AI | 报告 | 只使用已验证的结构化事实 |
| `target_country` | 目标国家 | STRING(2) | 是 | 任务 | 市场定位 | 与任务一致 |
| `target_platform` | 目标平台 | ENUM | 是 | 任务 | 市场口径 | 与任务一致 |
| `target_user_codes` | 目标人群 | ARRAY<STRING> | 否 | 评论洞察/AI | 用户画像 | 必须有人群证据 |
| `usage_scenario_codes` | 目标场景 | ARRAY<STRING> | 否 | 评论洞察/AI | 场景定位 | 必须有场景证据 |
| `primary_cluster_ids` | 核心需求聚类 | ARRAY<UUID> | 是 | 系统 | 证据链 | 至少 1 个，必须属于同任务 |
| `price_band_id` | 建议价格带 | UUID | 否 | 价格分析 | 定价参考 | 外键指向同任务 `price_band` |
| `status` | 机会状态 | ENUM | 是 | 系统/用户 | 业务流转 | `generated/reviewing/prioritized/data_needed/rejected/archived` |
| `evidence_confidence` | 数据证据置信度 | DECIMAL(5,4) | 是 | 规则计算 | 决策标记 | 0—1，不由 LLM 主观生成 |

### 11.2 机会评分 `opportunity_score`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 评分 ID | UUID | 是 | 系统 | 评分版本 | 主键 |
| `opportunity_id` | 机会 ID | UUID | 是 | 系统 | 机会关联 | 外键 |
| `demand_heat_score` | 需求热度分 | DECIMAL(5,2) | 是 | 程序 | 分项评分 | 0—100，需指标依据 |
| `demand_growth_score` | 需求增长分 | DECIMAL(5,2) | 条件 | 程序 | 分项评分 | 有至少两时点可比数据时必填；无数据时为空 |
| `unmet_need_score` | 未满足程度分 | DECIMAL(5,2) | 是 | 程序 | 分项评分 | 0—100 |
| `competition_space_score` | 竞争空间分 | DECIMAL(5,2) | 是 | 程序 | 分项评分 | 高分表示空间大，0—100 |
| `profit_space_score` | 利润空间分 | DECIMAL(5,2) | 条件 | 程序 | 分项评分 | 有成本和价格口径时才可填 |
| `enterprise_fit_score` | 企业适配分 | DECIMAL(5,2) | 是 | 程序 | 分项评分 | 0—100，未知能力不按 0 处理 |
| `base_score` | 机会基础分 | DECIMAL(5,2) | 是 | 程序 | 排序 | 只对可用子项按归一化权重计算 |
| `confidence` | 评分置信度 | DECIMAL(5,4) | 是 | 规则计算 | 决策口径 | 0—1，与基础分分离 |
| `recommendation_level` | 决策级别 | ENUM | 是 | 规则 | 报告结论 | `prioritize_validate/collect_more_data/capability_gap/limited_opportunity` |
| `weight_config` | 实际权重 | JSON | 是 | 评分引擎 | 可复现 | 权重和为 1；子项缺失时记录归一后权重 |
| `scoring_version` | 评分算法版本 | STRING(64) | 是 | 系统 | 可复现 | 与任务版本一致 |
| `calculated_at` | 计算时间 | DATETIME | 是 | 系统 | 新鲜度 | UTC |

### 11.3 制造要求 `manufacturing_requirement`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 制造要求 ID | UUID | 是 | 系统 | 适配评分 | 主键 |
| `opportunity_id` | 机会 ID | UUID | 是 | 系统 | 机会关联 | 外键 |
| `requirement_type` | 要求类型 | ENUM | 是 | RAG/AI/规则 | 能力匹配 | `attribute/material/process/equipment/packaging/cost/moq/certification/delivery/test` |
| `requirement_code` | 要求标准码 | STRING(150) | 是 | 行业本体 | 规则匹配 | 必须映射到属性或能力本体 |
| `description` | 要求说明 | TEXT | 是 | AI | 工程复核 | 不得超越证据确定具体参数 |
| `target_value` | 目标值/范围 | JSON | 否 | 已知规范/人工 | 定量匹配 | 无可靠依据时必须为空 |
| `unit` | 单位 | STRING(32) | 条件 | 系统 | 定量匹配 | 有数值时必填 |
| `priority` | 要求优先级 | ENUM | 是 | 规则/AI | 工程复核 | `must/should/could` |
| `evidence_cluster_ids` | 需求证据聚类 | ARRAY<UUID> | 是 | 系统 | 证据链 | 至少 1 个 |
| `validation_required` | 是否需验证 | BOOLEAN | 是 | 规则 | 人机协同 | V1 工程要求默认 `true` |

### 11.4 企业适配明细 `enterprise_fit_detail`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 适配明细 ID | UUID | 是 | 系统 | 证据链 | 主键 |
| `opportunity_id` | 机会 ID | UUID | 是 | 系统 | 机会关联 | 外键 |
| `requirement_id` | 制造要求 ID | UUID | 是 | 系统 | 要求关联 | 外键 |
| `capability_id` | 企业能力 ID | UUID | 否 | 匹配引擎 | 能力证据 | 无对应能力时可为空 |
| `fit_status` | 适配状态 | ENUM | 是 | 匹配引擎 | 能力缺口 | `matched/partial/gap/unknown/not_applicable` |
| `fit_score` | 该要求适配分 | DECIMAL(5,2) | 条件 | 程序 | 企业适配聚合 | `unknown` 时为空，其他为 0—100 |
| `hard_constraint_breached` | 是否违反硬约束 | BOOLEAN | 是 | 规则引擎 | 推荐门禁 | `true` 时机会不得标为强推荐 |
| `explanation` | 适配解释 | TEXT | 是 | 程序/AI | 报告下钻 | 必须引用要求和能力字段 |
| `confidence` | 适配置信度 | DECIMAL(5,4) | 是 | 规则 | 报告风险 | 0—1，能力未知时降低 |

### 11.5 产品改进建议 `product_recommendation`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 建议 ID | UUID | 是 | 系统 | 报告与反馈 | 主键 |
| `opportunity_id` | 机会 ID | UUID | 是 | 系统 | 机会关联 | 外键 |
| `recommendation_type` | 建议类型 | ENUM | 是 | AI/RAG | 分组展示 | `dimension/material/structure/function/color/packaging/assembly/cost/testing/positioning` |
| `problem_statement` | 用户/市场问题 | TEXT | 是 | 需求聚类 | 建议起点 | 必须可追溯到聚类 |
| `root_cause_hypotheses` | 可能根因 | ARRAY<TEXT> | 是 | RAG + AI | 工程复核 | 明确标记为假设，允许多个 |
| `recommended_action` | 建议动作 | TEXT | 是 | RAG + AI | 产品评审 | 不得将未验证工程假设写为强制指令 |
| `target_attribute_code` | 影响的产品属性 | STRING(100) | 否 | AI/规则 | 产品 Schema 关联 | 有具体属性时必填 |
| `target_value` | 建议目标值 | JSON | 否 | 规范/人工 | 定量建议 | 没有可靠依据时留空 |
| `expected_benefit` | 预期改善 | TEXT | 是 | AI | 决策对比 | 使用“预期/待验证”口径，不承诺销量 |
| `impact_dimensions` | 可能影响维度 | ARRAY<ENUM> | 是 | RAG + AI | 风险复核 | `cost/weight/comfort/durability/package/logistics/compliance/lead_time` |
| `cost_impact_min` | 成本增减下界 | DECIMAL(18,4) | 否 | BOM/人工评估 | 利润评估 | 无 BOM/报价依据时必须为空 |
| `cost_impact_max` | 成本增减上界 | DECIMAL(18,4) | 否 | BOM/人工评估 | 利润评估 | ≥下界，与币种成对 |
| `cost_currency` | 成本币种 | STRING(3) | 条件 | 企业 | 成本口径 | 有成本影响值时必填 |
| `priority` | 建议优先级 | ENUM | 是 | 规则/AI | 产品评审 | `high/medium/low` |
| `confidence` | 建议置信度 | DECIMAL(5,4) | 是 | 规则 | 风险提示 | 0—1，受证据和知识库影响 |
| `validation_method` | 验证方法 | TEXT | 是 | RAG + AI/人工 | 打样与测试 | 必须可执行，如对照打样/耐久测试/用户测试 |
| `evidence_cluster_ids` | 证据聚类 | ARRAY<UUID> | 是 | 系统 | 证据下钻 | 至少 1 个 |
| `expert_review_status` | 专家复核状态 | ENUM | 是 | 用户 | 人机协同 | `pending/feasible/needs_info/not_feasible/adopted` |
| `expert_review_note` | 专家备注 | TEXT | 条件 | 产品/工程人员 | 反馈闭环 | 非 `pending` 时必填 |
| `model_run_id` | 生成模型运行 ID | UUID | 是 | AI 网关 | 可复现 | 外键 |

### 11.6 验证任务 `validation_task`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 验证任务 ID | UUID | 是 | 系统 | 反馈闭环 | 主键 |
| `recommendation_id` | 建议 ID | UUID | 是 | 系统 | 建议关联 | 外键 |
| `validation_type` | 验证类型 | ENUM | 是 | 产品/工程人员 | 测试规划 | `expert_review/prototype/lab_test/user_test/market_test/cost_quote` |
| `owner_id` | 负责人 | UUID | 是 | 用户 | 任务跟进 | 外键指向同租户用户 |
| `due_date` | 截止日期 | DATE | 否 | 用户 | 项目管理 | 不得早于创建日期 |
| `status` | 任务状态 | ENUM | 是 | 用户 | 闭环跟踪 | `planned/in_progress/passed/failed/cancelled` |
| `result_summary` | 验证结果 | TEXT | 条件 | 用户 | 后续校准 | `passed/failed` 时必填 |
| `result_metrics` | 验证指标 | JSON | 否 | 测试/用户 | 学习数据 | 必须记录指标单位和方法 |
| `evidence_asset_ids` | 验证文件 | ARRAY<UUID> | 否 | 用户 | 反馈证据 | 外键指向 `file_asset` |
| `completed_at` | 完成时间 | DATETIME | 条件 | 系统 | 周期统计 | 终态时必填 |

---

## 12. 输出报告与证据数据

### 12.1 分析报告 `analysis_report`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 报告 ID | UUID | 是 | 系统 | 报告主键 | 主键 |
| `analysis_job_id` | 分析任务 ID | UUID | 是 | 系统 | 任务关联 | 外键，一任务可有多报告版本 |
| `report_version` | 报告版本 | INT | 是 | 系统 | 版本管理 | 任务内从 1 递增 |
| `title` | 报告标题 | STRING(300) | 是 | 系统/AI | 页面和导出 | 必须包含产品和市场 |
| `status` | 报告状态 | ENUM | 是 | 用户/系统 | 发布流程 | `draft/human_review/published/superseded` |
| `executive_summary` | 执行摘要 | TEXT | 是 | AI + 结构化结果 | 报告首屏 | 不得包含无证据新数值 |
| `decision_recommendation` | 总体决策建议 | ENUM | 是 | 评分规则 | 管理者决策 | `prioritize_validate/collect_more_data/capability_gap/limited_opportunity` |
| `overall_opportunity_score` | 综合机会分 | DECIMAL(5,2) | 是 | 评分引擎 | 报告首屏 | 必须与当前机会评分一致 |
| `overall_confidence` | 总体置信度 | DECIMAL(5,4) | 是 | 规则引擎 | 风险标记 | 0—1，不与机会分混合 |
| `data_scope_summary` | 数据范围摘要 | JSON | 是 | 数据集/程序 | 报告口径 | 包含平台、国家、时间、商品数、有效评论数 |
| `product_profile_snapshot` | 产品画像快照 | JSON | 是 | 产品画像 | 报告复现 | 脱敏，不随主档更新 |
| `target_user_summary` | 目标人群摘要 | JSON | 否 | 需求洞察/AI | 市场定位 | 必须有证据链 |
| `price_summary` | 价格带摘要 | JSON | 否 | 价格统计 | 定价参考 | 必须含币种、时间和促销口径 |
| `risk_summary` | 风险摘要 | ARRAY<JSON> | 是 | 规则/AI | 决策风险 | 至少包含数据限制和能力缺口 |
| `pending_validation_items` | 待验证事项 | ARRAY<JSON> | 是 | 建议/规则 | 后续动作 | 可为空数组，不为 null |
| `generated_model_run_id` | 报告生成模型运行 | UUID | 是 | AI 网关 | 报告追溯 | 外键 |
| `published_by` | 发布人 | UUID | 条件 | 用户 | 审计 | `status=published` 时必填 |
| `published_at` | 发布时间 | DATETIME | 条件 | 系统 | 审计 | `status=published` 时必填 |

### 12.2 报告章节 `report_section`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 章节 ID | UUID | 是 | 系统 | 报告组装 | 主键 |
| `report_id` | 报告 ID | UUID | 是 | 系统 | 报告关联 | 外键 |
| `section_code` | 章节码 | ENUM | 是 | 系统 | 固定信息架构 | `scope/product/competitor/user_need/price/opportunity/fit/recommendation/risk/evidence` |
| `title` | 章节标题 | STRING(200) | 是 | 系统 | UI 展示 | 报告内唯一章节码 |
| `sort_order` | 排序 | INT | 是 | 系统 | 报告展示 | ≥1，报告内唯一 |
| `content_type` | 内容类型 | ENUM | 是 | 系统 | 渲染 | `structured/text/chart/mixed` |
| `structured_content` | 结构化内容 | JSON | 条件 | 程序/AI | Web 渲染 | `structured/mixed` 时必填，符合章节 Schema |
| `narrative_text` | 叙述文本 | TEXT | 条件 | AI | 报告阅读 | `text/mixed` 时必填 |
| `chart_config` | 图表配置 | JSON | 否 | 程序 | ECharts 渲染 | 只引用已存市场指标，不嵌入伪造数据 |

### 12.3 统一证据引用 `evidence_link`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 证据链 ID | UUID | 是 | 系统 | 证据下钻 | 主键 |
| `analysis_job_id` | 任务 ID | UUID | 是 | 系统 | 任务隔离 | 外键 |
| `claim_type` | 结论对象类型 | ENUM | 是 | 系统 | 统一关联 | `report_section/opportunity/recommendation/score/fit_detail` |
| `claim_id` | 结论对象 ID | UUID | 是 | 系统 | 证据下钻 | 与 `claim_type` 一致 |
| `claim_category` | 结论性质 | ENUM | 是 | 系统/AI | 可信标记 | `observation/inference/recommendation/validation_required` |
| `evidence_type` | 证据类型 | ENUM | 是 | 系统 | 证据渲染 | `review_aspect/listing/market_metric/product_attribute/capability/file/expert_feedback` |
| `evidence_id` | 证据对象 ID | UUID | 是 | 系统 | 证据定位 | 与 `evidence_type` 一致 |
| `support_type` | 支持关系 | ENUM | 是 | 系统/AI | 证据解释 | `supports/contradicts/context/limitation` |
| `relevance_score` | 证据相关度 | DECIMAL(5,4) | 是 | Rerank/规则 | 证据排序 | 0—1 |
| `is_primary` | 是否主证据 | BOOLEAN | 是 | 系统/人工 | 报告首展 | 每个关键结论至少一条主证据 |
| `display_order` | 展示顺序 | INT | 是 | 系统 | UI | 同结论内从 1 递增 |

### 12.4 报告人工评审 `report_review`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 评审 ID | UUID | 是 | 系统 | 评审流程 | 主键 |
| `report_id` | 报告 ID | UUID | 是 | 系统 | 报告关联 | 外键 |
| `reviewer_id` | 评审人 | UUID | 是 | 认证上下文 | 责任追溯 | 需具备评审权限 |
| `decision` | 评审结论 | ENUM | 是 | 评审人 | 业务流转 | `approve/revise/reject/hold` |
| `usability_score` | 报告可用性评分 | INT | 是 | 评审人 | MVP 业务验收 | 1—5 |
| `accuracy_score` | 需求准确性主观评分 | INT | 是 | 评审人 | 质量反馈 | 1—5，不代替标注集指标 |
| `actionability_score` | 建议可执行性 | INT | 是 | 评审人 | 业务验收 | 1—5 |
| `comment` | 评审意见 | TEXT | 条件 | 评审人 | 迭代优化 | 非 `approve` 时必填 |
| `reviewed_at` | 评审时间 | DATETIME | 是 | 系统 | 审计 | UTC |

---

## 13. 系统配置、算力与可观测数据

### 13.1 模型路由配置 `model_route_config`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 路由配置 ID | UUID | 是 | 系统 | 模型管理 | 主键 |
| `task_type` | 任务类型 | ENUM | 是 | 管理员 | 模型选择 | 与 `model_run.task_type` 对齐 |
| `primary_model_id` | 主模型 ID | STRING(200) | 是 | 管理员 | 默认调用 | 必须在 Model Router 可用 |
| `fallback_model_ids` | 备选模型 | ARRAY<STRING> | 否 | 管理员 | 失败兜底 | 按顺序调用，不与主模型重复 |
| `timeout_ms` | 超时时间 | INT | 是 | 管理员 | 稳定性 | >0，按任务类型设置 |
| `max_retries` | 最大重试数 | INT | 是 | 管理员 | 稳定性 | 0—5，不包括首次调用 |
| `batch_size` | 批处理条数 | INT | 否 | 管理员/压测 | 评论抽取 | 批任务必填，需符合 token 上限 |
| `concurrency_limit` | 并发上限 | INT | 是 | 管理员 | 配额与限流 | ≥1 |
| `active` | 是否启用 | BOOLEAN | 是 | 管理员 | 路由发布 | 同任务类型只能有一个当前配置 |

### 13.2 算力配额 `compute_quota`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 配额 ID | UUID | 是 | 系统 | 配额管理 | 主键 |
| `tenant_id` | 租户 ID | UUID | 是 | 系统 | 租户限额 | 外键 |
| `period_start` | 配额周期开始 | DATETIME | 是 | 系统 | 用量汇总 | UTC |
| `period_end` | 配额周期结束 | DATETIME | 是 | 系统 | 用量汇总 | > 开始时间 |
| `quota_unit` | 配额单位 | ENUM | 是 | 系统 | 赛事/商业计费 | `credit/token/currency/job` |
| `quota_total` | 总配额 | DECIMAL(24,8) | 是 | 管理员 | 用量门禁 | ≥0 |
| `quota_used` | 已使用配额 | DECIMAL(24,8) | 是 | 程序汇总 | 用量展示 | 0—总配额，幂等计费 |
| `warning_threshold` | 预警比例 | DECIMAL(5,4) | 是 | 管理员 | 配额预警 | 0—1，默认 0.8 |
| `hard_limit` | 是否硬限制 | BOOLEAN | 是 | 管理员 | 任务门禁 | `true` 时超额禁止新任务 |

### 13.3 业务与质量事件 `product_event`

| 字段名称 | 字段释义 | 数据类型 | 必填 | 数据来源 | 使用场景 | 约束规则 |
|---|---|---|:---:|---|---|---|
| `id` | 事件 ID | UUID | 是 | 系统 | 产品分析 | 主键 |
| `tenant_id` | 租户 ID | UUID | 是 | 系统 | 数据隔离 | 外键 |
| `user_id` | 用户 ID | UUID | 否 | 认证上下文 | 用户旅程 | 系统事件可空 |
| `event_name` | 事件名 | STRING(100) | 是 | 前后端 | 产品指标 | 使用注册事件名，如 `analysis_started/evidence_opened/report_approved` |
| `entity_type` | 业务实体类型 | STRING(64) | 否 | 系统 | 事件定位 | 使用稳定实体名 |
| `entity_id` | 业务实体 ID | UUID | 否 | 系统 | 事件定位 | 与类型一致 |
| `properties` | 事件属性 | JSON | 否 | 前后端 | 分析分组 | 不包含敏感文本、成本或原始评论 |
| `occurred_at` | 发生时间 | DATETIME | 是 | 系统 | 漏斗分析 | UTC |

---

## 14. 主要枚举与口径

### 14.1 数据来源优先级

| 优先级 | `source_type` | 说明 | 默认置信上限 |
|---:|---|---|---:|
| 1 | `confirmed_user` / `structured_file` | 经企业人员确认的结构化参数 | 1.00 |
| 2 | `user_input` | 企业用户明确填写 | 1.00 |
| 3 | `pdf` / `document` | 企业正式资料抽取 | 0.95，需解析质量校正 |
| 4 | `vision` | 图片中可见特征 | 0.85，仅限可见属性 |
| 5 | `model_inference` | 模型根据语境推断 | 0.70，关键字段必须人工确认 |

> 上表为 V1 策略上限，不是模型实际准确率；实际置信度需结合评测集校准。

### 14.2 结论性质 `claim_category`

| 枚举值 | 业务定义 | 展示要求 |
|---|---|---|
| `observation` | 数据中直接观察到的事实/统计 | 必须显示样本和口径 |
| `inference` | 从多条证据得出的解释 | 必须显示推断标记和置信度 |
| `recommendation` | 基于证据和企业能力的建议 | 必须包含风险和验证方法 |
| `validation_required` | 数据不足或高风险，需人工/实验验证 | 不得作为确定性结论 |

### 14.3 数据敏感级别

| 级别 | 示例 | 权限与日志规则 |
|---|---|---|
| `public` | 经授权使用的公开商品信息 | 可在企业内报告展示，仍需记录来源 |
| `internal` | 产品名称、一般参数 | 同租户用户按角色访问 |
| `confidential` | 出厂报价、MOQ、交付周期 | 仅管理者、产品/研发及被授权用户；日志脱敏 |
| `restricted` | 历史订单、客户邮件、联系人 | V1 不接入；后续需单独授权、脱敏和保留策略 |

### 14.4 任务终态

| 状态 | 定义 | 是否允许发布报告 |
|---|---|:---:|
| `published` | 完整分析并经人工发布 | 是 |
| `partial` | 部分节点完成，存在明确缺失 | 条件，必须显示缺失与限制 |
| `failed` | 关键节点失败 | 否 |
| `cancelled` | 用户取消 | 否 |

---

## 15. 主外键与实体关系

```text
tenant
├── user ──< user_role >── role
├── enterprise_profile
├── manufacturing_capability
├── enterprise_constraint
├── product
│   ├── file_asset
│   └── product_profile_version
│       └── product_attribute
└── market_dataset
    ├── dataset_field_mapping
    ├── market_listing
    │   ├── listing_attribute
    │   └── review
    └── quality_report

analysis_job
├── analysis_stage_run
│   └── model_run
├── competitor_match
├── review_aspect
├── insight_cluster
│   └── insight_cluster_member
├── market_metric
├── price_band
├── market_opportunity
│   ├── opportunity_score
│   ├── manufacturing_requirement
│   │   └── enterprise_fit_detail
│   └── product_recommendation
│       └── validation_task
└── analysis_report
    ├── report_section
    ├── evidence_link
    └── report_review
```

### 15.1 关键引用完整性规则

1. `analysis_job` 必须固定产品画像版本、数据集版本、本体、Prompt 和评分版本。
2. `review_aspect` 必须可回溯到 `review`，`review` 必须可回溯到 `market_listing` 和 `market_dataset`。
3. `product_recommendation` 必须至少关联一个需求聚类，并通过 `evidence_link` 回溯原始评论或市场指标。
4. `opportunity_score` 的每一个非空子分必须有对应 `market_metric` 或适配明细。
5. 报告发布后不得修改原版本；修改必须创建新报告版本。
6. 不允许跨租户外键；关联实体的 `tenant_id` 必须一致。

---

## 16. API 与数据库落地约定

### 16.1 API 输入输出

- API 使用 JSON，字段命名与本字典一致；
- 请求不允许未定义字段，扩展数据必须进入明确的 `metadata`/JSON 字段；
- 条件必填字段使用 Pydantic/JSON Schema 跨字段校验；
- 枚举对外返回稳定代码，中文文案由前端国际化层映射；
- 敏感字段无权时应省略或返回明确脱敏值，不得在错误信息中泄露；
- 所有列表 API 支持分页、稳定排序和租户过滤。

### 16.2 存储与索引

- 所有租户表建立以 `tenant_id` 为前导列的查询索引；
- `product(tenant_id, sku)` 建立唯一索引；
- `market_listing(dataset_id, platform_listing_id, observed_at)` 建立唯一/快照索引；
- `review(dataset_id, content_hash)` 建立去重索引；
- `review_aspect(analysis_job_id, taxonomy_code, sentiment)` 建立组合索引；
- `market_metric(analysis_job_id, metric_code, dimension_type)` 建立组合索引；
- 向量索引按实际数据量选择 HNSW/IVFFlat，MVP 数据小时先保证正确性；
- 市场原始快照、AI 中间结果和业务报告分层存储。

### 16.3 数据更新原则

- 原始市场数据和原始评论只追加、不原地覆盖；
- 产品画像、能力、数据集、Prompt、评分和报告使用版本管理；
- AI 中间结果由输入哈希 + 模型 + Prompt/Schema 版本唯一确定；
- 人工修正不覆盖 AI 原输出，以状态和修正记录保留双方结果。

---

## 17. MVP 数据实现优先级

| 优先级 | 实体 | 落地说明 |
|---|---|---|
| P0 | `tenant`、`user`、`role`、`user_role` | 最小租户和 RBAC，Demo 可预置账号 |
| P0 | `enterprise_profile`、`manufacturing_capability`、`enterprise_constraint` | 企业能力可先预置单客户数据 |
| P0 | `product`、`file_asset`、`product_profile_version`、`product_attribute` | 产品多模态理解核心 |
| P0 | `market_dataset`、`market_listing`、`review` | 固定合规数据集 |
| P0 | `analysis_job`、`analysis_stage_run`、`model_run` | 异步工作流、成本和可复现 |
| P0 | `competitor_match` | 竞品可比性复核 |
| P0 | `review_aspect`、`insight_cluster`、`insight_cluster_member` | 评论需求洞察核心 |
| P0 | `market_metric`、`price_band` | 确定性市场统计 |
| P0 | `market_opportunity`、`opportunity_score`、`manufacturing_requirement`、`enterprise_fit_detail` | 市场机会与工厂适配 |
| P0 | `product_recommendation`、`analysis_report`、`evidence_link` | 证据化报告与核心 Demo |
| P1 | `validation_task`、`report_review`、`product_event` | 企业试用和效果闭环 |
| P2 | 多平台连接、历史订单与客户数据 | 需额外数据授权、隐私和商用容量设计 |

---

## 18. 版本记录

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-08 | 基于 FurniScope SRS/PRD V1.0 建立全域产品数据字典，覆盖用户、企业、产品、市场、竞品、评论、AI 分析、机会评分、建议、报告、证据、算力与审计数据 |
