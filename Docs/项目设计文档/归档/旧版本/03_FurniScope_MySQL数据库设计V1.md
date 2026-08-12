# FurniScope MySQL 数据库设计 V1.0

## 1. 文档概述

| 项目 | 内容 |
|---|---|
| 适用产品 | FurniScope——家具出海产品机会雷达 |
| 数据库 | MySQL 8.0+ |
| 存储引擎 | InnoDB |
| 字符集/排序规则 | `utf8mb4` / `utf8mb4_0900_ai_ci` |
| 适用范围 | 黑客松 Demo、项目附件、后端开发基线 |
| 配套 SQL | `furniscope_mysql_v1.sql` |

### 1.1 设计原则

1. 按业务主数据、市场数据、任务中间数据和结果输出数据分层。
2. 所有企业数据携带 `tenant_id`，以联合索引和外键实现租户隔离。
3. 内部主键使用 `BIGINT UNSIGNED AUTO_INCREMENT`；需对外暴露的任务和报告另设 UUID。
4. 原始市场数据按快照追加，AI 任务固定产品、数据集、本体、Prompt 和评分版本。
5. 对页面高频读取的属性、评分和报告摘要适度使用 JSON 和结果冗余，避免 Demo 阶段过度范式化。
6. 事实数值由程序计算，AI 结果保留模型、Prompt、Schema、证据和置信度。
7. Listing 生成不属当前市场洞察 P0，但按用户要求作为扩展输出表保留。

---

## 2. 数据表分类

### 2.1 业务主数据表

| 表名 | 中文名称 | 核心用途 |
|---|---|---|
| `tenants` | 企业租户表 | 企业数据隔离 |
| `users` | 系统用户表 | 账号、角色与登录信息 |
| `enterprise_profiles` | 企业档案表 | 企业业务模式和市场背景 |
| `manufacturing_capabilities` | 制造能力表 | 材料、工艺、认证和交付能力 |
| `products` | 产品基础信息表 | 企业 SKU 主档和高频字段 |
| `product_attributes` | 产品属性表 | 版本化产品画像 |

### 2.2 市场与竞品数据表

| 表名 | 中文名称 | 核心用途 |
|---|---|---|
| `market_datasets` | 市场数据集表 | 锁定平台、国家、时间和数据授权口径 |
| `competitor_listings` | 竞品平台商品表 | 竞品价格、评分、卖点和快照 |
| `competitor_reviews` | 竞品用户评论表 | 原始评论、翻译和清洗状态 |

### 2.3 任务与 AI 中间表

| 表名 | 中文名称 | 核心用途 |
|---|---|---|
| `analysis_tasks` | 市场分析任务表 | 完整 AI 工作流主体 |
| `task_stage_runs` | 任务阶段运行表 | 节点状态、幂等和重试 |
| `ai_model_runs` | AI 模型调用表 | 模型版本、Token、耗时和成本 |
| `competitor_matches` | 竞品匹配中间表 | 相似分和人工竞品复核 |
| `review_aspects` | 评论观点 AI 结果表 | 观点级需求、情感、场景和证据 Span |
| `insight_clusters` | 需求聚类表 | 高频痛点和购买动机聚合 |
| `cluster_members` | 需求聚类成员表 | 聚类与评论观点多对多关系 |

### 2.4 结果与输出表

| 表名 | 中文名称 | 核心用途 |
|---|---|---|
| `market_opportunities` | 市场机会结果表 | 六维评分、企业适配和决策级别 |
| `product_recommendations` | 产品改进建议表 | 问题、根因假设、动作、风险和专家复核 |
| `evidence_links` | 统一证据链表 | 从结论回溯评论、竞品、指标或产品属性 |
| `analysis_reports` | 分析报告表 | 可发布、可复现的报告快照 |
| `listing_generation_records` | Listing 生成记录表 | 扩展的多平台 Listing 生成与人工审核 |
| `report_exports` | 系统报告导出表 | PDF/DOCX/XLSX/JSON 异步导出 |
| `audit_logs` | 审计日志表 | 敏感数据操作追溯 |

---

## 3. 表结构详细设计

> `PK` 表示主键，`FK` 表示外键，`UK` 表示唯一键。所有时间字段在服务端按 UTC 写入。

### 3.1 企业租户表 `tenants`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 租户主键 |
| `tenant_code` | VARCHAR(32) | 否 | UK | - | 全局唯一企业编码 |
| `tenant_name` | VARCHAR(200) | 否 | - | - | 企业正式名称 |
| `display_name` | VARCHAR(100) | 是 | - | NULL | 界面展示简称 |
| `industry` | VARCHAR(64) | 否 | - | `furniture_manufacturing` | 行业路由码 |
| `default_timezone` | VARCHAR(64) | 否 | - | `Asia/Shanghai` | 租户默认时区 |
| `default_currency` | CHAR(3) | 否 | - | `CNY` | ISO 4217 币种 |
| `status` | TINYINT UNSIGNED | 否 | IDX | 1 | 0 禁用、1 正常、2 挂起 |
| `data_retention_days` | INT UNSIGNED | 否 | - | 365 | 数据保留天数 |
| `created_at/updated_at/deleted_at` | DATETIME(3) | 部分 | - | 当前时间/NULL | 创建、更新和软删除时间 |

### 3.2 系统用户表 `users`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 用户主键 |
| `tenant_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 所属企业 |
| `email` | VARCHAR(254) | 否 | UK组合 | - | 小写归一化登录邮箱 |
| `password_hash` | VARCHAR(255) | 否 | - | - | 密码哈希，严禁明文 |
| `user_name` | VARCHAR(100) | 否 | - | - | 用户姓名 |
| `phone` | VARCHAR(32) | 是 | - | NULL | E.164 手机号 |
| `department/job_title` | VARCHAR(100) | 是 | - | NULL | 部门和岗位 |
| `role_code` | VARCHAR(32) | 否 | IDX组合 | `market_ops` | 老板/产品/运营/外贸/管理员 |
| `locale` | VARCHAR(16) | 否 | - | `zh-CN` | 界面语言 |
| `timezone` | VARCHAR(64) | 是 | - | NULL | 个人时区，空时继承租户 |
| `status` | TINYINT UNSIGNED | 否 | IDX组合 | 1 | 账号状态 |
| `last_login_at` | DATETIME(3) | 是 | - | NULL | 最后登录时间 |
| `created_at/updated_at/deleted_at` | DATETIME(3) | 部分 | - | 当前时间/NULL | 审计时间 |

### 3.3 企业档案表 `enterprise_profiles`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 档案主键 |
| `tenant_id` | BIGINT UNSIGNED | 否 | FK/UK | - | 一租户一当前档案 |
| `business_models` | JSON | 否 | - | - | B2B/B2C/OEM/ODM/brand |
| `primary_categories` | JSON | 否 | - | - | 主营品类数组 |
| `export_markets` | JSON | 是 | - | NULL | 已出口国家 |
| `sales_channels` | JSON | 是 | - | NULL | 线下买家、代理、展会、Amazon 等 |
| `annual_capacity_note` | VARCHAR(1000) | 是 | - | NULL | 未结构化产能补充 |
| `profile_completeness` | DECIMAL(5,4) | 否 | - | 0 | 0—1 档案完整度 |
| `confirmed_by/confirmed_at` | BIGINT/DATETIME | 是 | FK/- | NULL | 人工确认信息 |
| `created_at/updated_at` | DATETIME(3) | 否 | - | 当前时间 | 审计时间 |

### 3.4 制造能力表 `manufacturing_capabilities`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 能力主键 |
| `tenant_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 所属企业 |
| `capability_type` | VARCHAR(32) | 否 | UK/IDX组合 | - | 材料、工艺、定制、包装、认证等类型 |
| `capability_code/name` | VARCHAR(100/200) | 否 | UK组合/- | - | 标准能力码与名称 |
| `availability` | TINYINT UNSIGNED | 否 | IDX组合 | 2 | 0 否、1 是、2 未知 |
| `min_value/max_value/unit` | DECIMAL/VARCHAR | 是 | - | NULL | 可量化能力范围 |
| `valid_from/valid_until` | DATE | 是 | IDX组合 | NULL | 认证、价格等能力有效期 |
| `source_type/confidence` | VARCHAR/DECIMAL | 否 | - | `confirmed_user`/1 | 来源和 0—1 置信度 |
| `evidence_url/notes` | VARCHAR | 是 | - | NULL | 证明文件和备注 |
| `created_by` | BIGINT UNSIGNED | 是 | FK | NULL | 创建人 |
| `created_at/updated_at/deleted_at` | DATETIME(3) | 部分 | - | 当前时间/NULL | 审计时间 |

### 3.5 产品基础信息表 `products`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 产品主键 |
| `tenant_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 所属企业 |
| `sku` | VARCHAR(100) | 否 | UK组合 | - | 企业内唯一 SKU |
| `product_name` | VARCHAR(200) | 否 | - | - | 产品名称 |
| `category_code/sofa_type` | VARCHAR | 部分 | IDX/- | `sofa`/NULL | 品类和沙发类型 |
| `lifecycle_status/analysis_status` | VARCHAR(24) | 否 | 联合IDX | `active`/`draft` | 产品和分析准备状态 |
| `description` | TEXT | 是 | - | NULL | 产品说明 |
| `primary_image_url/image_urls/source_files` | VARCHAR/JSON | 是 | - | NULL | 图片与参数资料对象存储地址 |
| `factory_price/currency/price_term` | DECIMAL/CHAR/VARCHAR | 是 | - | NULL | 敏感出厂报价口径 |
| `moq/moq_unit/lead_time_days` | INT/VARCHAR | 是 | - | NULL/`piece`/NULL | 最小起订和交付周期 |
| `profile_version/status/completeness` | INT/VARCHAR/DECIMAL | 否 | - | 1/`unreviewed`/0 | 产品画像版本与完整度 |
| `profile_confirmed_by/at` | BIGINT/DATETIME | 是 | FK/- | NULL | 产品画像确认 |
| `created_by` | BIGINT UNSIGNED | 是 | FK | NULL | 创建人 |
| `created_at/updated_at/deleted_at` | DATETIME(3) | 部分 | - | 当前时间/NULL | 审计时间 |

### 3.6 产品属性表 `product_attributes`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 属性主键 |
| `tenant_id/product_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 租户和产品 |
| `profile_version` | INT UNSIGNED | 否 | UK组合 | - | 画像版本 |
| `attribute_code/name` | VARCHAR(100) | 否 | UK组合/- | - | 标准属性码和名称 |
| `value_type/attribute_value/unit` | VARCHAR/JSON/VARCHAR | 部分 | - | -/NULL/NULL | 属性类型、值和单位 |
| `raw_value` | VARCHAR(1000) | 是 | - | NULL | 文件中的原始值 |
| `source_type/source_locator` | VARCHAR/JSON | 部分 | - | -/NULL | 来源类型与页码/单元格位置 |
| `confidence` | DECIMAL(5,4) | 否 | - | 0 | AI/规则置信度 |
| `confirmation_status` | VARCHAR(24) | 否 | IDX组合 | `unreviewed` | 人工确认状态 |
| `is_sensitive` | TINYINT(1) | 否 | - | 0 | 字段级敏感标记 |
| `created_at/updated_at` | DATETIME(3) | 否 | - | 当前时间 | 审计时间 |

### 3.7 市场数据集表 `market_datasets`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 数据集主键 |
| `tenant_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 所属企业 |
| `dataset_name/version_no` | VARCHAR/INT | 否 | UK组合 | -/1 | 数据集名与版本 |
| `platform/market_country/marketplace_code` | VARCHAR/CHAR/VARCHAR | 部分 | 联合IDX | -/-/NULL | 平台、国家和站点 |
| `category_code` | VARCHAR(100) | 否 | IDX组合 | - | 标准品类 |
| `data_start_date/data_end_date` | DATE | 部分 | IDX组合 | NULL/- | 数据时间范围 |
| `source_type/source_name` | VARCHAR | 否 | - | - | 企业导出、授权数据或 Demo 数据 |
| `authorization_reference/import_file_url` | VARCHAR(1000) | 是 | - | NULL | 数据授权与原始文件 |
| `status` | VARCHAR(24) | 否 | IDX组合 | `uploaded` | 上传、校验、可用或驳回 |
| `listing_count/review_count/valid_review_count` | INT UNSIGNED | 否 | - | 0 | 数据集样本数 |
| `quality_score` | DECIMAL(5,2) | 否 | - | 0 | 0—100 数据质量分 |
| `quality_report/limitations` | JSON | 是 | - | NULL | 缺失率、重复率、异常和限制 |
| `created_by` | BIGINT UNSIGNED | 是 | FK | NULL | 导入人 |
| `created_at/updated_at/deleted_at` | DATETIME(3) | 部分 | - | 当前时间/NULL | 审计时间 |

### 3.8 竞品平台商品表 `competitor_listings`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 竞品快照主键 |
| `tenant_id/dataset_id` | BIGINT UNSIGNED | 否 | FK | - | 租户和数据集 |
| `platform_listing_id` | VARCHAR(200) | 否 | UK组合 | - | ASIN 或平台商品 ID |
| `listing_url` | VARCHAR(1000) | 是 | - | NULL | 授权商品页地址 |
| `brand/seller_name` | VARCHAR(200) | 是 | IDX/- | NULL | 品牌和销售方 |
| `title/description` | TEXT/MEDIUMTEXT | 部分 | - | -/NULL | 标题和详情文本 |
| `bullet_points` | JSON | 是 | - | NULL | 卖点列表 |
| `category_raw/category_code` | VARCHAR | 部分 | IDX组合 | NULL/- | 原品类和内部品类 |
| `image_urls` | JSON | 是 | - | NULL | 竞品图片 |
| `normalized_attributes/feature_codes` | JSON | 是 | - | NULL | Demo 查询冗余属性和卖点 |
| `currency/list_price/sale_price/coupon_info` | CHAR/DECIMAL/JSON | 部分 | 价格IDX | -/NULL/-/NULL | 价格及促销口径 |
| `rating/rating_count/review_count` | DECIMAL/INT | 是 | 联合IDX | NULL | 评分和评论规模 |
| `rank_value/rank_category` | INT/VARCHAR | 是 | - | NULL | 排名代理信号 |
| `sales_value/type/period` | DECIMAL/VARCHAR | 是 | - | NULL | 销量数值及真实/估算/代理口径 |
| `first_available_at` | DATETIME(3) | 是 | - | NULL | 首次上架时间 |
| `availability_status` | VARCHAR(24) | 是 | - | `unknown` | 可售状态 |
| `observed_at` | DATETIME(3) | 否 | UK/IDX组合 | - | 商品快照时间 |
| `raw_payload` | JSON | 是 | - | NULL | 脱敏原始数据 |
| `created_at` | DATETIME(3) | 否 | - | 当前时间 | 入库时间 |

### 3.9 竞品用户评论表 `competitor_reviews`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 评论主键 |
| `tenant_id/dataset_id/listing_id` | BIGINT UNSIGNED | 否 | FK | - | 租户、数据集和商品 |
| `platform_review_id` | VARCHAR(200) | 是 | - | NULL | 平台评论 ID |
| `rating` | DECIMAL(3,2) | 否 | IDX组合 | - | 0—5 星级 |
| `review_title/body_original` | TEXT/MEDIUMTEXT | 部分 | - | NULL/- | 评论标题与不可覆盖的原文 |
| `language/body_translated_zh` | VARCHAR/MEDIUMTEXT | 部分 | IDX/- | `und`/NULL | 原语言和中文翻译 |
| `reviewed_at` | DATETIME(3) | 是 | IDX组合 | NULL | 评论发布时间 |
| `verified_purchase` | TINYINT UNSIGNED | 否 | - | 2 | 0 否、1 是、2 未知 |
| `helpful_votes/variant_info` | INT/JSON | 是 | - | NULL | 有用票和购买变体 |
| `is_duplicate/is_spam/is_valid` | TINYINT(1) | 否 | 联合IDX | 0/0/1 | 清洗标记和有效样本门禁 |
| `invalid_reason` | VARCHAR(64) | 是 | - | NULL | 无效原因 |
| `content_hash` | CHAR(64) | 否 | UK组合 | - | 内容去重与 AI 缓存 |
| `created_at` | DATETIME(3) | 否 | - | 当前时间 | 入库时间 |

### 3.10 市场分析任务表 `analysis_tasks`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id/task_uuid` | BIGINT/CHAR(36) | 否 | PK/UK | 自增/- | 内部主键和对外 UUID |
| `tenant_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 所属企业 |
| `task_name/task_type` | VARCHAR | 否 | - | -/`product_market_fit` | 任务名与分析类型 |
| `product_id/product_profile_version` | BIGINT/INT | 否 | FK/IDX | - | 锁定产品和画像版本 |
| `dataset_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 锁定市场数据集 |
| `target_country/target_platform` | CHAR/VARCHAR | 否 | - | - | 目标市场口径 |
| `analysis_currency` | CHAR(3) | 否 | - | - | 统一价格币种 |
| `status/current_stage/progress_percent` | VARCHAR/DECIMAL | 否 | 联合IDX | `draft`/`draft`/0 | 任务状态、阶段和进度 |
| `ontology/scoring/prompt_bundle_version` | VARCHAR(64) | 否 | - | - | 锁定 AI 和评分版本 |
| `analysis_config` | JSON | 是 | - | NULL | 冻结筛选、阈值和权重 |
| `sample_listing_count/sample_review_count` | INT UNSIGNED | 否 | - | 0 | 实际样本规模冗余 |
| `started_at/completed_at` | DATETIME(3) | 是 | - | NULL | 任务耗时 |
| `failure_code/failure_message` | VARCHAR | 是 | - | NULL | 用户可读异常 |
| `created_by` | BIGINT UNSIGNED | 否 | FK | - | 任务发起人 |
| `created_at/updated_at` | DATETIME(3) | 否 | - | 当前时间 | 审计时间 |

### 3.11 任务阶段运行表 `task_stage_runs`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 节点运行主键 |
| `task_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 分析任务 |
| `stage_code/attempt_no` | VARCHAR/INT | 否 | UK组合 | -/1 | 阶段码和重试次数 |
| `idempotency_key` | VARCHAR(128) | 否 | UK | - | 防止重复执行/重复计费 |
| `status` | VARCHAR(24) | 否 | IDX组合 | `queued` | 节点状态 |
| `input_ref/output_ref` | JSON | 部分 | - | -/NULL | 输入快照和输出引用 |
| `started_at/ended_at` | DATETIME(3) | 是 | - | NULL | 节点耗时 |
| `error_code/error_message/retryable` | VARCHAR/TINYINT | 是 | - | NULL | 错误和重试策略 |
| `created_at` | DATETIME(3) | 否 | - | 当前时间 | 创建时间 |

### 3.12 AI 模型调用表 `ai_model_runs`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 模型调用主键 |
| `task_id/stage_run_id` | BIGINT UNSIGNED | 部分 | FK/IDX | -/NULL | 任务和工作流节点 |
| `provider/model_id` | VARCHAR | 否 | IDX组合 | `aliyun_model_router`/- | 模型提供方和实际 ID |
| `task_type` | VARCHAR(32) | 否 | IDX组合 | - | 视觉、抽取、向量、重排、推理或报告 |
| `prompt_code/prompt_version` | VARCHAR | 是 | - | NULL | Prompt 模板及版本 |
| `input_hash/output_schema_version` | CHAR/VARCHAR | 部分 | - | -/NULL | 缓存键与输出 Schema |
| `input_tokens/output_tokens/image_count` | INT UNSIGNED | 部分 | - | NULL/NULL/0 | 算力用量 |
| `latency_ms` | INT UNSIGNED | 否 | - | 0 | 模型耗时 |
| `estimated_cost/cost_unit` | DECIMAL/VARCHAR | 是 | - | NULL | 估算成本和币种/credit |
| `status/retry_count/schema_valid` | VARCHAR/INT/TINYINT | 部分 | IDX | -/0/NULL | 调用、重试和 Schema 状态 |
| `error_code/error_message` | VARCHAR | 是 | - | NULL | 错误排查 |
| `created_at` | DATETIME(3) | 否 | IDX组合 | 当前时间 | 调用时间 |

### 3.13 竞品匹配中间表 `competitor_matches`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 匹配主键 |
| `task_id/listing_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 任务和竞品快照 |
| `competitor_type` | VARCHAR(16) | 否 | IDX组合 | `direct` | 直接/标杆/替代/排除 |
| `category/function/style/price/material/scenario_score` | DECIMAL(5,2) | 否 | - | 0 | 六项相似分 |
| `overall_score/rerank_score` | DECIMAL | 部分 | IDX组合 | 0/NULL | 综合分和模型重排分 |
| `match_reasons` | JSON | 否 | - | - | 可解释匹配原因 |
| `review_status/review_note` | VARCHAR | 部分 | IDX组合 | `pending`/NULL | 人工纳入、排除或改类 |
| `reviewed_by/reviewed_at` | BIGINT/DATETIME | 是 | FK/- | NULL | 复核人和时间 |
| `created_at/updated_at` | DATETIME(3) | 否 | - | 当前时间 | 审计时间 |

### 3.14 评论观点 AI 结果表 `review_aspects`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 观点主键 |
| `task_id/review_id` | BIGINT UNSIGNED | 否 | FK/UK组合 | - | 任务和原始评论 |
| `aspect_index` | INT UNSIGNED | 否 | UK组合 | - | 一条评论内的观点序号 |
| `taxonomy_code` | VARCHAR(150) | 否 | IDX组合 | - | 家具需求本体码 |
| `product_attribute_code` | VARCHAR(100) | 是 | - | NULL | 对应产品属性 |
| `opinion_text` | VARCHAR(1000) | 否 | - | - | 规范化观点 |
| `sentiment/severity` | VARCHAR/TINYINT | 部分 | IDX组合 | -/NULL | 观点情感与 1—5 严重度 |
| `user_profile_codes/usage_scenario_codes` | JSON | 是 | - | NULL | 受影响人群和场景 |
| `purchase_driver` | TINYINT(1) | 否 | - | 0 | 是否购买动机 |
| `evidence_start/evidence_end/evidence_quote` | INT/INT/VARCHAR | 否 | - | - | 原文证据 Span 及快照 |
| `extraction_confidence` | DECIMAL(5,4) | 否 | - | - | 0—1 抽取置信度 |
| `embedding` | JSON | 是 | - | NULL | Demo 向量冗余，商用可迁移向量库 |
| `model_run_id` | BIGINT UNSIGNED | 否 | FK | - | 生成该观点的模型调用 |
| `validation_status` | VARCHAR(24) | 否 | - | `unreviewed` | 人工接受、修正或驳回 |
| `created_at` | DATETIME(3) | 否 | - | 当前时间 | 创建时间 |

### 3.15 需求聚类表 `insight_clusters`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 聚类主键 |
| `task_id/cluster_no` | BIGINT/INT | 否 | FK/UK组合 | - | 任务和聚类序号 |
| `cluster_name/taxonomy_code/sentiment` | VARCHAR | 否 | IDX组合 | - | 聚类名、需求本体和情感 |
| `aspect_count/review_count/listing_count` | INT UNSIGNED | 否 | - | - | 观点、评论和商品样本数 |
| `mention_rate/denominator_type` | DECIMAL/VARCHAR | 否 | - | - | 提及率及明确分母 |
| `cross_listing_rate` | DECIMAL(7,6) | 否 | - | - | 跨商品覆盖率 |
| `avg_severity` | DECIMAL(4,2) | 是 | - | NULL | 负向问题平均严重度 |
| `freshness/importance_score` | DECIMAL | 否 | IDX组合 | 0 | 时间新鲜度和重要度 |
| `cluster_confidence` | DECIMAL(5,4) | 否 | - | 0 | 0—1 聚类置信度 |
| `representative_aspect_ids` | JSON | 是 | - | NULL | 报告默认展示的代表证据 |
| `algorithm_version` | VARCHAR(64) | 否 | - | - | 聚类算法版本 |
| `created_at` | DATETIME(3) | 否 | - | 当前时间 | 创建时间 |

### 3.16 需求聚类成员表 `cluster_members`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `cluster_id` | BIGINT UNSIGNED | 否 | PK/FK | - | 需求聚类 |
| `aspect_id` | BIGINT UNSIGNED | 否 | PK/FK/IDX | - | 评论观点 |
| `distance` | DECIMAL(12,10) | 否 | - | 0 | 观点与聚类中心距离 |
| `is_representative` | TINYINT(1) | 否 | - | 0 | 是否作为代表证据 |

### 3.17 市场机会结果表 `market_opportunities`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 机会主键 |
| `task_id/opportunity_code` | BIGINT/VARCHAR | 否 | FK/UK组合 | - | 任务和任务内机会码 |
| `title/description` | VARCHAR/TEXT | 否 | - | - | 机会名称与解释 |
| `target_country/target_platform` | CHAR/VARCHAR | 否 | - | - | 目标市场 |
| `target_user_codes/usage_scenario_codes` | JSON | 是 | - | NULL | 目标人群和场景 |
| `primary_cluster_ids` | JSON | 否 | - | - | 核心需求证据 |
| `price_band_min/max/currency` | DECIMAL/CHAR | 是 | - | NULL | 建议价格带 |
| `demand_heat_score` | DECIMAL(5,2) | 否 | - | 0 | 需求热度分 |
| `demand_growth_score` | DECIMAL(5,2) | 是 | - | NULL | 有时序数据时的需求增长分 |
| `unmet_need_score` | DECIMAL(5,2) | 否 | - | 0 | 未满足程度分 |
| `competition_space_score` | DECIMAL(5,2) | 否 | - | 0 | 竞争空间分 |
| `profit_space_score` | DECIMAL(5,2) | 是 | - | NULL | 成本和价格足够时的利润空间分 |
| `enterprise_fit_score` | DECIMAL(5,2) | 否 | - | 0 | 企业制造适配分 |
| `overall_score/confidence` | DECIMAL | 否 | 联合IDX | 0 | 综合机会分与独立置信度 |
| `recommendation_level` | VARCHAR(32) | 否 | - | - | 优先验证/补数据/能力缺口/机会有限 |
| `capability_gaps` | JSON | 是 | - | NULL | 工厂能力缺口冗余 |
| `weight_config/scoring_version` | JSON/VARCHAR | 否 | - | - | 实际权重与评分版本 |
| `status` | VARCHAR(24) | 否 | IDX组合 | `generated` | 生成、复核、优先、驳回等状态 |
| `created_at/updated_at` | DATETIME(3) | 否 | - | 当前时间 | 审计时间 |

### 3.18 产品改进建议表 `product_recommendations`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 建议主键 |
| `task_id/opportunity_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 任务和市场机会 |
| `recommendation_type` | VARCHAR(24) | 否 | - | - | 尺寸、材料、结构、包装等类型 |
| `problem_statement` | TEXT | 否 | - | - | 有证据的用户/市场问题 |
| `root_cause_hypotheses` | JSON | 否 | - | - | 一个或多个根因假设 |
| `recommended_action` | TEXT | 否 | - | - | 待工程复核的产品动作 |
| `target_attribute_code/value` | VARCHAR/JSON | 是 | - | NULL | 目标产品属性和值 |
| `expected_benefit` | TEXT | 否 | - | - | 待验证改善假设 |
| `impact_dimensions` | JSON | 否 | - | - | 成本、重量、舒适、包装等影响 |
| `cost_impact_min/max/currency` | DECIMAL/CHAR | 是 | - | NULL | 有 BOM/报价依据时的成本变化 |
| `priority/confidence` | VARCHAR/DECIMAL | 否 | 联合IDX | - | 优先级和 0—1 置信度 |
| `validation_method` | TEXT | 否 | - | - | 打样、实验或用户测试方法 |
| `evidence_cluster_ids` | JSON | 否 | - | - | 需求聚类证据 |
| `expert_review_status/note` | VARCHAR | 部分 | IDX组合 | `pending`/NULL | 工程专家复核 |
| `reviewed_by/reviewed_at` | BIGINT/DATETIME | 是 | FK/- | NULL | 复核人和时间 |
| `model_run_id` | BIGINT UNSIGNED | 否 | FK | - | 生成建议的模型调用 |
| `created_at/updated_at` | DATETIME(3) | 否 | - | 当前时间 | 审计时间 |

### 3.19 统一证据链表 `evidence_links`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 证据链主键 |
| `task_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 分析任务 |
| `claim_type/claim_id` | VARCHAR/BIGINT | 否 | UK/IDX组合 | - | 机会、建议、报告或评分结论 |
| `claim_category` | VARCHAR(24) | 否 | - | - | 事实/推断/建议/待验证 |
| `evidence_type/evidence_id` | VARCHAR/BIGINT | 否 | UK/反向IDX | - | 评论观点、商品、聚类、指标或产品属性 |
| `support_type` | VARCHAR(16) | 否 | - | `supports` | 支持、反证、背景或限制 |
| `relevance_score` | DECIMAL(5,4) | 否 | - | 1 | 0—1 证据相关度 |
| `is_primary/display_order` | TINYINT/INT | 否 | IDX组合 | 0/1 | 主证据与展示顺序 |
| `created_at` | DATETIME(3) | 否 | - | 当前时间 | 创建时间 |

> 该表采用多态关联，因为证据可指向多类结果和多类原始数据。MySQL 无法对 `claim_id/evidence_id` 声明类型动态外键，因此完整性由服务层和定期校验任务保证。

### 3.20 分析报告表 `analysis_reports`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id/report_uuid` | BIGINT/CHAR(36) | 否 | PK/UK | 自增/- | 内部主键和对外 UUID |
| `tenant_id/task_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 租户和分析任务 |
| `report_version` | INT UNSIGNED | 否 | UK组合 | 1 | 任务内报告版本 |
| `title/status` | VARCHAR | 否 | IDX组合 | -/`draft` | 报告标题和发布状态 |
| `executive_summary` | MEDIUMTEXT | 否 | - | - | 执行摘要 |
| `decision_recommendation` | VARCHAR(32) | 否 | - | - | 优先验证/补数据/能力缺口/机会有限 |
| `overall_opportunity_score/confidence` | DECIMAL | 否 | - | - | 机会分与独立置信度 |
| `data_scope_summary` | JSON | 否 | - | - | 平台、市场、时间和样本数 |
| `product_profile_snapshot` | JSON | 否 | - | - | 报告生成时的产品画像 |
| `competitor/user_need/price_summary` | JSON | 是 | - | NULL | 竞品、用户需求和价格摘要 |
| `opportunity/recommendation_summary` | JSON | 否 | - | - | 机会与产品建议首屏数据 |
| `risk_summary/pending_validation_items` | JSON | 否 | - | - | 风险与待验证事项 |
| `generated_model_run_id` | BIGINT UNSIGNED | 否 | FK | - | 报告生成模型调用 |
| `published_by/published_at` | BIGINT/DATETIME | 是 | FK/- | NULL | 发布人和时间 |
| `created_at/updated_at` | DATETIME(3) | 否 | IDX组合 | 当前时间 | 审计时间 |

### 3.21 Listing 生成记录表 `listing_generation_records`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | Listing 生成记录主键 |
| `tenant_id/product_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 租户和产品 |
| `source_task_id` | BIGINT UNSIGNED | 是 | FK/IDX | NULL | 可选的市场洞察上游任务 |
| `target_platform/country/language` | VARCHAR/CHAR | 否 | IDX组合 | - | 目标平台、国家和语言 |
| `generation_type` | VARCHAR(32) | 否 | - | - | 标题、五点、描述、关键词或完整 Listing |
| `input_snapshot` | JSON | 否 | - | - | 产品、市场洞察和限制快照 |
| `generated_title` | TEXT | 是 | - | NULL | AI 标题 |
| `generated_bullets` | JSON | 是 | - | NULL | AI 五点描述 |
| `generated_description` | MEDIUMTEXT | 是 | - | NULL | AI 详情描述 |
| `generated_keywords` | JSON | 是 | - | NULL | 关键词数组 |
| `compliance_warnings` | JSON | 是 | - | NULL | 合规和幻觉警告 |
| `model_run_id` | BIGINT UNSIGNED | 否 | FK | - | 生成模型调用 |
| `status` | VARCHAR(24) | 否 | IDX组合 | `generated` | 生成、审核、批准或驳回 |
| `reviewed_by/reviewed_at` | BIGINT/DATETIME | 是 | FK/- | NULL | 人工审核 |
| `created_at/updated_at` | DATETIME(3) | 否 | IDX组合 | 当前时间 | 审计时间 |

### 3.22 系统报告导出表 `report_exports`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 导出任务主键 |
| `tenant_id/report_id` | BIGINT UNSIGNED | 否 | FK/IDX | - | 租户和报告 |
| `export_format` | VARCHAR(16) | 否 | - | - | PDF/DOCX/XLSX/JSON |
| `file_url` | VARCHAR(1000) | 是 | - | NULL | 对象存储键/短效地址 |
| `file_size_bytes/file_hash` | BIGINT/CHAR(64) | 是 | - | NULL | 文件大小和 SHA-256 |
| `status` | VARCHAR(24) | 否 | IDX组合 | `queued` | 排队、生成、成功、失败或过期 |
| `error_message` | VARCHAR(1000) | 是 | - | NULL | 导出失败信息 |
| `requested_by/requested_at` | BIGINT/DATETIME | 否 | FK/IDX | -/当前时间 | 申请人和申请时间 |
| `completed_at/expires_at` | DATETIME(3) | 是 | - | NULL | 完成与过期时间 |
| `download_count/last_downloaded_at` | INT/DATETIME | 部分 | - | 0/NULL | 下载统计 |

### 3.23 审计日志表 `audit_logs`

| 字段名 | 数据类型 | 允许空 | 键 | 默认值 | 业务说明 |
|---|---|:---:|---|---|---|
| `id` | BIGINT UNSIGNED | 否 | PK | 自增 | 日志主键 |
| `tenant_id/actor_id` | BIGINT UNSIGNED | 是 | FK/IDX | NULL | 租户和操作人，系统事件可空 |
| `action` | VARCHAR(32) | 否 | - | - | 新建、修改、导出、删除、批准等 |
| `resource_type/resource_id` | VARCHAR/BIGINT | 部分 | 联合IDX | -/NULL | 被操作资源 |
| `before_snapshot/after_snapshot` | JSON | 是 | - | NULL | 脱敏的变更前后摘要 |
| `ip_address/user_agent` | VARCHAR | 是 | - | NULL | 安全排查 |
| `occurred_at` | DATETIME(3) | 否 | 多个IDX | 当前时间 | 事件时间 |

---

## 4. 索引设计与查询场景

| 索引 | 索引字段 | 主要查询场景 |
|---|---|---|
| `uk_users_tenant_email` | `tenant_id,email` | 租户内用户登录与重复校验 |
| `idx_users_tenant_role_status` | `tenant_id,role_code,status` | 按企业查找有效产品/运营人员 |
| `uk_products_tenant_sku` | `tenant_id,sku` | SKU 唯一性和精确查询 |
| `idx_products_tenant_category_status` | `tenant_id,category_code,lifecycle_status` | 产品列表按品类和状态筛选 |
| `idx_dataset_tenant_market_status` | `tenant_id,platform,market_country,status` | 选择某市场可用数据集 |
| `idx_listing_dataset_price` | `dataset_id,sale_price` | 某数据集价格带统计 |
| `idx_listing_dataset_rating` | `dataset_id,rating,review_count` | 高评分/高评论竞品筛选 |
| `idx_review_listing_valid_time` | `listing_id,is_valid,reviewed_at` | 查看某竞品的有效评论和时间分布 |
| `idx_review_dataset_rating_valid` | `dataset_id,rating,is_valid` | 数据集正负样本分层 |
| `idx_task_tenant_status_created` | `tenant_id,status,created_at` | 任务首页按状态和时间查询 |
| `idx_task_product_created` | `tenant_id,product_id,created_at` | 查看某 SKU 历史分析 |
| `idx_stage_task_status` | `task_id,status,stage_code` | 进度和失败节点检索 |
| `idx_model_run_task_type` | `task_id,task_type,status` | 任务算力、错误和模型成本统计 |
| `idx_match_task_type_score` | `task_id,competitor_type,overall_score` | 按类型展示 Top 竞品 |
| `idx_aspect_task_taxonomy_sentiment` | `task_id,taxonomy_code,sentiment` | 需求标签和情感看板 |
| `idx_cluster_task_importance` | `task_id,importance_score` | Top 高频痛点/购买动机 |
| `idx_opportunity_task_score` | `task_id,overall_score,confidence` | 机会排序，同时查看置信度 |
| `idx_recommendation_task_priority` | `task_id,priority,expert_review_status` | 高优先级且待复核的建议 |
| `idx_evidence_task_claim` | `task_id,claim_type,claim_id,display_order` | 点击“为什么”下钻证据 |
| `idx_reports_tenant_status_time` | `tenant_id,status,created_at` | 报告列表与已发布报告 |
| `idx_export_report_status` | `report_id,status,requested_at` | 报告导出进度轮询 |

### 4.1 索引使用规则

- 后端租户表查询条件必须以 `tenant_id` 开始，以利用联合索引左前缀。
- 分析看板优先按 `task_id` 读取，不从原始全表动态聚合跨任务数据。
- JSON 字段不用于核心大范围过滤；如 P1 出现高频 JSON 子字段查询，应增加生成列与索引。
- `evidence_links` 的多态 ID 通过服务层检查，不在数据库中伪造无法成立的外键。

---

## 5. 冗余与范式化取舍

| 冗余位置 | 设计原因 | 一致性策略 |
|---|---|---|
| `products` 保留报价、MOQ、交期和当前画像状态 | 产品列表和任务创建高频读取 | 产品确认事务中同步更新 |
| `competitor_listings.normalized_attributes` | Demo 阶段避免为竞品属性创建大量 EAV 联表 | 导入标准化时一次性生成，数据集不原地修改 |
| `analysis_tasks` 保留市场口径、样本数和版本 | 任务列表、演示和审计无需多表回溯 | 任务启动时冻结，后续不更改 |
| `market_opportunities` 保留六项分、置信度和能力缺口 | 机会页需要一次查询排序展示 | 同一评分事务一次写入，评分版本固定 |
| `analysis_reports` 保留产品和各类摘要快照 | 报告发布后需永久复现，不应随主数据漂移 | 报告只追加版本，不覆盖已发布版本 |

---

## 6. 外键、删除与事务策略

1. 核心业务外键不使用 `ON DELETE CASCADE`，避免误删产品时破坏任务、证据和报告。
2. 企业、用户、产品和数据集使用软删除；原始评论、AI 中间结果和报告按数据保留策略由后台任务清理。
3. 启动分析任务的事务必须同时固定产品版本、数据集、配置和任务状态。
4. 阶段运行使用 `idempotency_key`；重试不得重复写入已成功中间结果或重复计费。
5. 报告发布操作必须在同一事务中更新报告状态、发布人、发布时间和审计日志。

---

## 7. SQL 执行说明

```bash
mysql -h <host> -P 3306 -u <user> -p < furniscope_mysql_v1.sql
```

执行前提：

- MySQL 版本为 8.0 或更高；
- 执行账号具有建库、建表、索引和外键权限；
- 如已由平台创建数据库，可删除 SQL 开头的 `CREATE DATABASE` 和 `USE` 语句；
- SQL 使用 `CREATE TABLE IF NOT EXISTS`，但不会自动升级旧表；后续版本需通过 Flyway/Alembic 等迁移工具管理。

---

## 8. Demo 开发建议

### P0 必建表

`tenants`、`users`、`enterprise_profiles`、`manufacturing_capabilities`、`products`、`product_attributes`、`market_datasets`、`competitor_listings`、`competitor_reviews`、`analysis_tasks`、`task_stage_runs`、`ai_model_runs`、`competitor_matches`、`review_aspects`、`insight_clusters`、`cluster_members`、`market_opportunities`、`product_recommendations`、`evidence_links`、`analysis_reports`。

### P1 可延后表

- `listing_generation_records`：市场洞察验收后再接入上新场景；
- `report_exports`：复赛主流程稳定后增加；
- `audit_logs`：Demo 可先记录发布、导出、删除和敏感字段修改事件。

---

## 9. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-08 | 根据 FurniScope SRS/PRD V1.0 和产品数据字典 V1.0，完成 MySQL 8.0 Demo 数据库设计 |
