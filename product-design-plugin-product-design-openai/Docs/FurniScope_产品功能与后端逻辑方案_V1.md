# FurniScope 产品功能与后端逻辑方案 V1.0

> 基于当前 React UI、PRD V2、数据字典 V3、Agent 工作流 V2 和 API V3 整理。本文用于明确“页面做什么、后端如何实现、数据如何流转”，不替代数据库字段级设计。

## 1. 产品定位与闭环

FurniScope 是面向跨境家具制造企业的超级 AI 员工。它将企业产品资料、制造能力、授权市场数据和历史销售数据统一处理，输出可追溯的市场洞察、销量预测、工程建议和决策报告。

完整业务闭环：

```text
登录与租户隔离
→ AI 分析（对话式或向导式）
→ 产品/文件解析与数据质量检查
→ AI 五阶段任务执行
→ 必要时向用户确认
→ 市场洞察与销量预测
→ 决策报告与证据追溯
→ 结果沉淀到工作台和历史列表
```

产品只保留两种账号类型：

- `user`：企业用户，只访问本企业数据，使用业务功能和企业级 AI 配置。
- `admin`：平台管理员，通过独立入口登录，管理账号、平台模型目录和官方提示词模板，不参与企业业务决策。

## 2. 当前产品信息架构

### 2.1 企业用户端

| 一级模块 | 页面/状态 | 核心目的 |
|---|---|---|
| 登录 | 企业登录 | 建立用户会话和租户上下文 |
| AI 工作台 | 总览 | 查看任务、待确认、指标和近期报告 |
| AI 分析 | 对话首页、对话执行、向导模式 | 用自然语言或结构化表单创建分析 |
| 销量预测 | XGBoost Sales v4 | 上传历史销售数据，独立运行 SKU/Site 预测 |
| 市场洞察 | 洞察列表、洞察详情 | 查看竞品、需求、价格、机会、销量预测和证据 |
| 决策报告 | 报告列表、报告详情、下载 | 查看一项分析形成的正式决策结果 |
| AI 配置 | 模型与算力、提示词、运行诊断 | 企业自选模型路由、管理个人模板、诊断自己的任务 |

### 2.2 平台管理端

独立管理员登录后只包含：

1. 用户管理：用户查询、新建、编辑、启停、重置凭证、分页与审计。
2. 模型管理：供应商和模型上架、API 接入配置、启停、价格、能力标签、健康检查。
3. 官方提示词管理：模板创建、编辑、版本、发布、下架和推荐范围。

企业用户的模型路由、个人提示词和任务诊断不放在平台管理端，而放在企业用户的“AI 配置”中。

## 3. 各模块功能与后端逻辑

### 3.1 登录与身份体系

产品功能：

- 企业用户和平台管理员使用两个独立登录入口、独立会话 Cookie/Token 和独立路由守卫。
- 登录后返回用户、角色、`tenant_id`、账号状态和基础配置。
- 所有企业业务请求由后端从令牌中取得 `tenant_id`，前端不可自行指定或覆盖。

后端逻辑：

1. 校验账号、密码、状态和登录入口是否匹配角色。
2. 签发短期 Access Token 与可撤销 Refresh Token。
3. 在请求中间件注入 `actor_id`、`role`、`tenant_id`。
4. Repository 层默认追加租户条件，防止仅依靠 Controller 手工过滤。
5. 登录、失败登录、退出、敏感配置变更全部写审计日志。

### 3.2 AI 工作台

产品功能：

- 四项指标：运行中、待确认、已完成、报告/洞察数量。
- 最近任务及五阶段进度。
- 待确认事项入口。
- 最近报告卡片和快捷创建入口。

后端逻辑：

- 使用聚合接口一次返回首屏数据，避免前端分别调用多张表。
- 指标从任务表、确认表和报告表聚合；Redis 可缓存 30～60 秒。
- 最近任务按 `updated_at DESC` 查询；进度只输出对用户可见的五阶段。
- 待确认数量只统计当前用户可回答且状态为 `pending` 的事项。

### 3.3 AI 分析：对话模式

对话不是普通聊天，而是“会话 + 分析项目 + 可执行工具”的任务入口。

产品功能：

- 左侧按“分析项目 → 会话”组织历史记录，一个产品项目可有多轮研究会话。
- 用户输入目标、上传产品资料或销售数据。
- AI 先理解目标，缺少阻断信息时提问；信息充分时给出分析计划。
- 执行中逐步流式展示业务过程、工具结果、文件状态和阶段进度。
- 右侧执行上下文可折叠、可调整宽度，展示计划、资料、证据和整体进度。
- 首页快捷卡只填入推荐提示词，不直接跳转到其他页面。

后端逻辑：

```text
用户消息
→ 意图识别（问答 / 市场分析 / 销量预测 / 继续已有任务）
→ 提取产品、市场、时间范围、文件和输出目标
→ 检查必要输入
→ 需要补充：返回 clarification 消息
→ 输入充分：生成 analysis_plan
→ 用户确认启动或满足自动启动规则
→ 创建 analysis_task / forecast_job
→ 通过 SSE 推送任务事件和阶段结果
```

消息类型至少包括：`user_text`、`assistant_text`、`clarification`、`plan`、`file_status`、`tool_call`、`progress`、`confirmation`、`result_card`、`error`。

前端可以展示“正在解析文件”“正在计算价格分布”等业务级过程，但不输出模型隐藏思维链、内部 Agent 提示词或原始推理过程。流式信息来自结构化事件，而不是伪造逐字思考。

会话层级建议：

```text
analysis_project（产品/研究主题）
└── conversation（一次研究会话）
    ├── message
    ├── attachment
    ├── analysis_plan
    └── task/forecast_job 引用
```

### 3.4 AI 分析：向导模式

向导与对话模式共用同一任务创建服务，只是收集输入的方式不同。

四步功能：

1. 分析目标：目标描述、任务类型、目标市场、分析周期和输出偏好。
2. 产品与文件：选择/新建产品、上传图片/PDF/XLSX、解析产品属性、处理字段冲突。
3. 市场数据：选择已授权数据集，查看覆盖范围、质量、新鲜度；可选启用销量预测及其参数。
4. 检查并启动：展示输入快照、缺失项、预计耗时、预计成本、模型路由和风险提示。

后端逻辑：

- 每一步保存 `analysis_draft`，使用 `draft_version` 做乐观锁。
- 文件上传后异步进行病毒检测、格式校验、OCR/表格解析、Schema 映射和质量检查。
- 页面只允许使用状态为 `ready` 的资料和数据集启动任务。
- 启动时冻结产品画像版本、数据集版本、Prompt 版本、模型路由版本、评分算法版本和预测模型版本。
- 使用 `Idempotency-Key` 防止重复点击创建两个任务。

### 3.5 五阶段 AI 执行

对用户只展示五阶段：

1. 理解产品。
2. 研究市场。
3. 评估机会。
4. 生成建议。
5. 完成。

后端采用工作流编排器，内部可以有更细节点：预检、产品理解、数据质量、竞品筛选、评论抽取、需求聚类、市场计算、机会评分、建议生成、证据审计和报告生成。

核心规则：

- 任务、阶段和模型调用分别记录状态。
- 每个阶段使用输入版本哈希保证幂等。
- 节点成功后保存 Checkpoint；恢复时不重跑输入未变化的成功节点。
- 瞬时错误自动重试；可降级模型失败不打断用户。
- 非关键分支失败可继续，但必须写入 `partial_failures` 并降低置信度。
- 任务进度通过 SSE 为主、轮询为兜底推送。

### 3.6 用户确认

只有以下情况可以中断用户：关键事实冲突、关键数据不足、降级后仍低置信、高风险工程建议。

确认对象必须包含：问题、原因、推荐项、互斥选项、证据、不同选择的影响、恢复位置和过期时间。

提交逻辑：

1. 校验确认属于当前租户和当前任务。
2. 校验选项来自原始选项集合。
3. 使用 `confirmation_id` 保证重复提交不重复恢复。
4. 保存回答和审计记录，把任务从 `waiting_human` 置回 `queued`。
5. 从对应 Checkpoint 恢复，只重算受选择影响的下游节点。

### 3.7 独立商品销量预测

销量预测既是独立工具，也是 AI 分析可调用的一个确定性工具，不限定在市场洞察阶段。

三种入口共用同一个 Forecast Service：

| 入口 | 触发方式 | 结果去向 |
|---|---|---|
| 独立“销量预测”页面 | 用户主动上传并运行 | 预测历史与结果页，可选择同步洞察 |
| AI 对话 | 用户明确提出预测，或 AI 询问后用户同意 | 结果卡回写会话，并关联分析任务 |
| 向导分析 | 用户在市场数据步骤启用 | 作为“研究市场”阶段的一个并行分支 |

预测流程：

```text
上传 XLSX
→ 文件安全与模板字段校验
→ 时间、SKU、Site、销量、库存、促销等字段标准化
→ 缺失/异常/重复处理
→ 判断历史覆盖是否达到最低要求
→ 按 SKU × Site 切分时间序列
→ 特征构造与 XGBoost v4 推理
→ 回测/质量指标计算
→ 预测区间计算
→ 生产与备货规则计算
→ 保存结果、模型版本和数据版本
```

必要约束：

- 前端只接受 `.xlsx` 不代表后端可信，后端必须重新校验 MIME、扩展名、大小和内容。
- 默认至少需要 8 周日度历史数据；不足时拒绝或明确标记探索性预测。
- XGBoost 输出预测值；置信区间、MAPE/WAPE、备货量和安全库存由确定性程序计算。
- 对话中的 AI 负责解释预测和提出建议，不得修改模型原始数值。
- 每次预测保存 `model_version`、`feature_version`、`input_dataset_hash`、参数和评估指标，保证可复现。

### 3.8 市场洞察

产品功能：

- 洞察列表支持搜索、产品/市场筛选和分页。
- 详情包含总览、竞品、评价需求、价格、机会、销量预测、证据七类内容。
- 机会得分与置信度分开显示。
- 点击结论打开证据抽屉，支持追溯来源和统计口径。

后端逻辑：

- 洞察不是实时拼接页面，而是任务阶段产生的版本化结构化结果。
- 表格、比例、价格和评分由计算服务生成；LLM 只做解释和文本组织。
- 销量预测 Tab 查询与当前产品、市场或任务关联的最新 `forecast_run`；无结果时展示发起预测入口。
- 洞察状态建议为 `generating`、`ready`、`partial`、`superseded`、`failed`。

### 3.9 决策报告

产品功能：

- 报告首页是列表，不是单页；支持搜索、筛选、分页和下载。
- 报告详情包含结论、机会、工程建议、制造适配、风险、验证项和证据抽屉。
- 下载支持 PDF，后续可扩展 DOCX；下载的是已冻结版本。

后端逻辑：

- 报告只消费通过证据审计的结构化结果，不能再次自由生成关键数值。
- 高优先级结论必须至少关联一条有效证据。
- 报告生成成功与任务完成在同一业务事务/Outbox 边界提交。
- 报告更新生成新版本，不覆盖旧报告；下载记录写审计日志。

### 3.10 企业用户 AI 配置

模型与算力：

- 用户只能从平台已上架且已启用的模型目录中选择。
- 按工作流阶段配置主模型、备用模型、兜底模型，并设置超时、重试、并发和单任务成本上限。
- 企业配置保存为版本；新任务冻结当前版本，运行中的任务不被配置变更影响。
- API Key 原则上由平台托管；若支持企业自带 Key，必须进入密钥管理服务加密保存，任何接口不回传明文。

提示词：

- 展示平台推荐模板和用户自建模板。
- 点击模板可填入 AI 分析输入框，也可复制后编辑。
- 用户可创建、编辑、复制、归档自己的模板；官方模板不可直接修改。
- 模板发布需做变量校验、长度检查和敏感信息检查。

运行诊断：

- 只展示本租户任务，支持搜索、状态/日期筛选和分页。
- 详情展示五阶段、尝试次数、模型、耗时、Token、成本、错误摘要和恢复记录。
- 普通用户只看到可行动信息；内部堆栈、密钥和跨租户信息不得返回。

### 3.11 平台管理后台

用户管理：创建企业用户、编辑基本信息、启停、重置密码/发送邀请、分页、操作审计。删除默认使用软删除或归档，不能连带删除企业业务数据。

模型管理：

- 维护供应商、模型 ID、能力、上下文长度、结构化输出能力、价格、限流和地区。
- API 密钥只写不读，保存在 KMS/Secrets Manager。
- 上架前执行连通性、延迟、Schema 和安全测试。
- 停用模型前检查正在使用它的企业路由，并要求配置替代模型或执行受控降级。

官方提示词管理：草稿、测试、发布、下架和版本回滚；发布后旧任务仍引用旧版本。模板可配置推荐场景，但不能跨租户读取用户输入。

## 4. 后端服务划分

| 服务 | 主要职责 |
|---|---|
| Auth/Tenant Service | 登录、Token、角色、租户隔离、会话撤销 |
| Workspace BFF | 工作台聚合、列表查询、前端适配 |
| Project/Conversation Service | 项目、会话、消息、附件、对话计划 |
| Product Service | 产品、画像版本、企业能力、资料解析 |
| Dataset Service | 授权数据集、导入、清洗、质量报告 |
| Analysis Orchestrator | 五阶段任务、Checkpoint、确认、重试和恢复 |
| AI Gateway/Model Router | 模型目录、路由、限流、结构化输出、成本记录 |
| Forecast Service | XGBoost 数据校验、特征、推理、评估和备货规则 |
| Insight Service | 竞品、需求簇、价格、机会评分和洞察版本 |
| Evidence Service | 证据对象、原始引用、结论关联和权限过滤 |
| Report Service | 报告版本、在线渲染、PDF 生成和下载 |
| Configuration Service | 企业路由、用户模板、平台模板和配置版本 |
| Diagnosis/Audit Service | 阶段运行、模型调用、错误、审计和指标 |

推荐基础设施：PostgreSQL + pgvector、Redis、对象存储、任务队列、LangGraph Checkpoint、SSE 网关和 KMS。

## 5. 核心数据对象

在现有数据字典基础上，当前 UI 至少需要补齐或确认以下对象：

| 领域 | 核心对象 |
|---|---|
| 身份 | tenants、users、refresh_tokens、audit_logs |
| 项目会话 | analysis_projects、conversations、messages、message_attachments、analysis_plans |
| 产品资料 | products、product_files、product_profile_versions、capability_profiles |
| 市场数据 | datasets、dataset_versions、dataset_import_jobs、data_quality_reports |
| AI 任务 | analysis_tasks、task_stage_runs、task_checkpoints、confirmations、partial_failures |
| 模型运行 | model_catalog、tenant_model_routes、model_route_versions、ai_model_runs |
| 提示词 | official_prompt_templates、tenant_prompt_templates、prompt_versions |
| 销量预测 | forecast_datasets、forecast_jobs、forecast_runs、forecast_results、stock_recommendations |
| 结果证据 | insights、insight_versions、opportunities、recommendations、evidence_items、evidence_links |
| 报告 | reports、report_versions、report_downloads |

所有业务主表必须带 `tenant_id`；所有可复现结果必须记录输入数据版本、算法/模型版本和创建时间。

## 6. 建议 API 清单

### 6.1 用户端

```text
POST   /v1/auth/login
POST   /v1/auth/refresh
POST   /v1/auth/logout
GET    /v1/me

GET    /v1/dashboard
GET    /v1/projects
POST   /v1/projects
GET    /v1/projects/{id}/conversations
POST   /v1/conversations
GET    /v1/conversations/{id}/messages
POST   /v1/conversations/{id}/messages
GET    /v1/conversations/{id}/events          # SSE

POST   /v1/files/uploads
GET    /v1/files/{id}/status
POST   /v1/analysis-drafts
PATCH  /v1/analysis-drafts/{id}
POST   /v1/analysis-tasks
POST   /v1/analysis-tasks/{id}/start
GET    /v1/analysis-tasks/{id}
GET    /v1/analysis-tasks/{id}/events         # SSE
GET    /v1/confirmations
POST   /v1/confirmations/{id}/responses

POST   /v1/forecast/files/validate
POST   /v1/forecast/jobs
GET    /v1/forecast/jobs
GET    /v1/forecast/jobs/{id}
GET    /v1/forecast/jobs/{id}/results
POST   /v1/forecast/jobs/{id}/sync-to-insight
GET    /v1/forecast/jobs/{id}/export

GET    /v1/insights
GET    /v1/insights/{id}
GET    /v1/insights/{id}/evidence
GET    /v1/reports
GET    /v1/reports/{id}
POST   /v1/reports/{id}/exports

GET    /v1/settings/models/catalog
GET    /v1/settings/model-routes
PUT    /v1/settings/model-routes/{stage}
GET    /v1/settings/prompts
POST   /v1/settings/prompts
PATCH  /v1/settings/prompts/{id}
DELETE /v1/settings/prompts/{id}
GET    /v1/diagnostics/tasks
GET    /v1/diagnostics/tasks/{id}
```

所有列表接口统一支持 `page`、`page_size`、`query`、`status`、`sort` 和模块特定筛选条件。

### 6.2 平台管理端

```text
POST   /v1/admin/auth/login
GET    /v1/admin/users
POST   /v1/admin/users
PATCH  /v1/admin/users/{id}
POST   /v1/admin/users/{id}/enable
POST   /v1/admin/users/{id}/disable
POST   /v1/admin/users/{id}/reset-credential

GET    /v1/admin/models
POST   /v1/admin/models
PATCH  /v1/admin/models/{id}
POST   /v1/admin/models/{id}/health-check
POST   /v1/admin/models/{id}/publish
POST   /v1/admin/models/{id}/disable

GET    /v1/admin/prompt-templates
POST   /v1/admin/prompt-templates
PATCH  /v1/admin/prompt-templates/{id}
POST   /v1/admin/prompt-templates/{id}/publish
POST   /v1/admin/prompt-templates/{id}/unpublish
GET    /v1/admin/audit-logs
```

## 7. 关键状态机

分析任务：

```text
draft → queued → running
                    ├→ waiting_human → queued → running
                    ├→ partial_succeeded → running
                    ├→ succeeded
                    ├→ failed → queued（受控重试）
                    └→ cancelled
```

销量预测：

```text
uploaded → validating → ready → queued → preprocessing
→ predicting → evaluating → succeeded
                    ├→ failed
                    └→ partial_succeeded
```

文件：

```text
uploading → scanning → parsing → validating → ready
                          └→ rejected / parse_failed
```

## 8. 安全、可信与可观测性

- 租户隔离必须在 Token、服务层、Repository、缓存键、对象存储路径和向量检索条件中同时生效。
- 原始资料、销售数据和企业成本数据加密存储；日志中脱敏。
- 所有结论区分事实、程序计算、AI 推断、建议和待验证项。
- 机会分与置信度分别计算，任何页面不得合并或互相替代。
- 模型调用记录输入哈希、输出引用、模型、Prompt、Token、延迟、成本、重试和错误，不保存无必要的完整敏感 Prompt。
- 关键操作使用 Outbox 保证数据库提交与异步事件一致。
- 指标至少覆盖任务成功率、阶段耗时、确认等待时长、模型失败率、预测误差、报告证据覆盖率和单任务成本。

## 9. 分阶段落地建议

### P0：把当前 UI 跑通为完整产品

1. 登录、租户隔离和独立管理员会话。
2. 项目/会话、文件上传解析、对话消息与 SSE。
3. 对话和向导共用的任务创建、五阶段执行、确认与恢复。
4. 独立 XGBoost 预测后端、历史记录及同步洞察。
5. 洞察/报告列表、详情、证据抽屉和 PDF 下载。
6. 企业模型选择、个人提示词 CRUD、任务诊断分页与详情。
7. 管理端用户、模型和官方提示词完整 CRUD。

### P1：增强质量与运营能力

- 模型自动健康路由、成本预算告警、Prompt 灰度发布。
- 预测模型漂移监控、定期回测和重训练流程。
- 洞察版本对比、报告批量导出和到期数据清理。

## 10. 后端实现时最重要的五项原则

1. 对话、向导和独立预测是不同入口，但必须复用同一任务、文件、预测和结果服务。
2. 市场洞察和决策报告是版本化结果，不是页面打开时临时让大模型生成。
3. LLM 负责理解与解释，数值、评分、预测、区间和成本使用可复算程序。
4. 用户看到业务过程和证据，不暴露内部思维链、Agent 节点和敏感运行信息。
5. 任一结果都必须能追溯到租户、输入版本、数据版本、模型/算法版本、Prompt 版本和证据。
