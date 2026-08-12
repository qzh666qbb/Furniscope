# FurniScope 全套设计文档最终一致性校验报告 V1.0

## 1. 校验结论

**最终判定：未达到“文档闭环完成”。**

前端页面与 API V2 的接口编号和主路径已经闭环，未发现“待补充”占位内容；但数据字典、MySQL V1、Agent 工作流、PRD 实施范围及测试材料之间仍存在阻断级不一致。因此当前状态应标记为：**主交互链路已对齐，开发基线尚未闭环**。

| 校验项 | 结论 | 严重级别 |
|---|---|---|
| 页面调用接口是否均存在 | 通过 | — |
| API 返回字段是否全部进入数据字典 | 不通过 | P0 阻断 |
| Agent 状态、断点、重试和恢复是否有数据库承载 | 不通过 | P0 阻断 |
| P0/P1 和 Demo 范围是否统一 | 不通过 | P0 阻断 |
| 正式测试用例是否存在并覆盖主链路 | 不通过 | P0 阻断 |

## 2. 本次校验基线

| 文档类别 | 实际校验文件 | 状态 |
|---|---|---|
| PRD | `01_产品需求规格说明书SRS_PRD_V1.md` | 存在 |
| 数据字典 | `02_FurniScope产品数据字典V2.md` | 存在 |
| 数据库设计 | `03_FurniScope_MySQL数据库设计V1.md` | 存在 |
| Agent 工作流 | `04_FurniScope_Agent工作流设计V1.md` | 存在 |
| RESTful API | `05_FurniScope_RESTful_API接口设计V2.md` | 存在 |
| 页面交互原型 | `07_FurniScope页面交互原型说明V2.md` | 存在 |
| 测试用例 | 未找到正式测试用例文档 | 缺失；`Docs/提示词/测试提示词.md` 仅为排错提示词模板，不能作为测试用例 |

## 3. 页面与 API 一致性

### 3.1 已通过项

1. 交互原型共引用 38 个 API 编号，全部能在 API V2 中找到定义，未发现悬空接口编号。
2. 页面中使用的业务路径均能匹配 API V2；带 Query 参数的路径属于同一接口，不构成接口缺失。
3. 页面与 API V2 均未发现“待补充”字样。
4. P07 已使用 API-INS-03 的 `stage_runs`、`partial_failures`、`checkpoint_stage`、`retryable`、`report_uuid`。
5. P08 的确认动作已绑定 API-CMP-07，并明确通过 LangGraph Checkpoint 恢复工作流。
6. P07 已定义立即查询、前台 2 秒轮询、后台降频、终态停止、失败退避及恢复受理后的继续轮询规则。

### 3.2 非阻断说明

页面包含前端站内路由和浏览器访问短效下载 URL，这两类动作明确标注为“不调用业务 API”，不属于接口缺失。

## 4. API 返回字段与数据字典一致性

### 4.1 已通过的关键 V2 字段

以下关键字段已在数据字典中定义，并与 API-INS-03 或相关恢复接口的语义基本一致：

- `stage_runs`
- `partial_failures`
- `checkpoint_stage`
- `retryable`
- `report_uuid`
- `competitor_set_version`
- `resume_command_id`
- `resumed_from_checkpoint`
- `retry_unit_count`
- `cancel_requested`
- `report_exports`

### 4.2 P0 阻断问题：API 返回字段未全部逐字段定义

API V2 的成功响应示例包含多项字段，但数据字典中没有对应的独立字段定义。代表性缺口如下：

| API 模块 | 缺少显式定义的代表字段 |
|---|---|
| 用户认证 | `access_token`、`refresh_token`、`expires_in` |
| 数据导入 | `import_job_id`、`quality_metrics` 及其部分子字段 |
| 通用分页/响应 | `page`、`page_size`、`total`、`request_id`、`timestamp`、错误 `details[].field/reason` |
| Listing | `generation_id`、`generated_title`、`generated_bullets`、`generated_description`、`generated_keywords`、`compliance_warnings` |
| 报告导出 | `export_id`、`export_format`、`file_size_bytes`、`file_hash`、`download_count`、`last_downloaded_at` 等未全部形成逐字段数据字典行 |
| 竞品恢复 | `previous_checkpoint_stage` 仅出现在 API 响应，未形成清晰的独立字段定义 |

数据字典虽然定义了部分聚合对象，例如 `report_exports`、`summary`、`scores`，但“聚合对象已定义”不等于其中所有 API 返回子字段均已定义，不满足“API 所有返回字段全部在数据字典中有定义”的严格要求。

### 4.3 修复要求

以 API V2 的全部成功和失败响应 Schema 为输入，给数据字典增加“API 通用信封字段”和“各接口响应 DTO 字段”章节；所有嵌套对象必须展开到叶子字段，并标明类型、必填条件、来源、约束和适用接口。

## 5. Agent、API 与数据库一致性

### 5.1 逻辑层已对齐项

1. Agent、API-INS-03 对任务状态的核心枚举基本一致：`draft/queued/running/waiting_human/partial_succeeded/succeeded/failed/cancelled`。
2. Agent 与 API 对阶段运行状态基本一致，包含 `waiting_human`、`retry_scheduled`、`partial_succeeded`、`skipped` 和 `cancelled`。
3. 竞品确认使用 `competitor_set_version + checkpoint_stage` 做并发和恢复点校验，API-CMP-07 通过 Outbox/恢复命令受理 LangGraph `Command(resume=...)`，逻辑方向一致。
4. API-INS-07 的阶段重试、API-INS-08 的协作式取消与 Agent 的安全 Checkpoint、批次边界检查和幂等策略一致。

### 5.2 P0 阻断问题：MySQL V1 无法承载 V2 工作流

数据字典 V2 已明确声明 MySQL V1 尚未完整承载 V2 需要的数据。数据库设计中缺少或未补齐：

| 缺失或未补齐对象 | 影响 |
|---|---|
| `product_parse_jobs` / 文件级解析结果表 | API-PRD-07 进度、错误、重试状态无法可靠持久化 |
| `workflow_checkpoints` | `checkpoint_stage` 与安全恢复点没有正式长期事实表 |
| `workflow_partial_failures` | `partial_failures` 只能依赖 Agent State，服务重启后的完整性不足 |
| `workflow_control_events` / Outbox | 竞品确认、人工恢复、重试、取消存在数据库提交与 Graph 命令不一致风险 |
| `competitor_set_confirmations` | 竞品集合版本、确认快照、幂等键和恢复命令无正式审计实体 |
| `analysis_tasks.checkpoint_stage` | API 查询缺少直接任务投影字段 |
| `analysis_tasks.cancel_requested` 及取消审计字段 | API-INS-08 的协作式取消无法按 V2 契约落库 |
| `task_stage_runs` 重试链字段 | `retry_parent_run_id/retry_scope/retry_reason/retry_requested_*` 未进入 MySQL V1 |

`task_stage_runs.retryable` 和 `analysis_reports.report_uuid` 已存在，但不足以覆盖 V2 的断点、局部失败、恢复命令和取消机制。

### 5.3 其他技术基线冲突

PRD 的建议业务数据库仍写为 PostgreSQL/pgvector，数据库设计及数据字典 V2 已切换为 MySQL 8.0。测试提示词也仍写 PostgreSQL。开发团队无法据此确定唯一数据库基线。

### 5.4 修复要求

1. 输出 MySQL 数据库设计 V2，并给出可直接执行的迁移 SQL。
2. 补齐上述实体、字段、外键、唯一键、状态约束和业务索引。
3. 统一 PRD、测试文档及架构描述中的数据库技术栈；若向量能力独立使用 pgvector，必须明确 MySQL 与向量库的职责边界，而非同时称为业务主库。

## 6. P0/P1 与 Demo 范围一致性

### 6.1 已通过项

- Listing 模块在 API 中标记为 P1 扩展，交互原型全部统一标注“【P1迭代功能，本次Demo暂不实现】”。
- API-INS-07、API-INS-08、API-RPT-05 在页面中均使用统一 P1 标识。
- Dashboard、解析任务查询、任务竞品查询和竞品确认恢复均标记为 P0，符合当前主链路定位。

### 6.2 P0 阻断问题

| 冲突位置 | 当前定义 | 冲突 |
|---|---|---|
| PRD P1 | “报告 PDF 导出和团队协作”属于 P1 | 页面 P13 将 API-RPT-03/04 创建导出与查询导出状态作为本次 Demo 可用功能，仅把历史 API-RPT-05 标为 P1 |
| 数据库 V1 | `report_exports` 列为 P1 可延后表 | 页面和 API 将创建、轮询导出作为可执行主功能，没有 P0 数据承载 |
| PRD P0 | 明确要求任务失败后保留阶段并支持失败节点重试 | API-INS-07 人工重试被定义为 P1；需要明确 P0 是否只实现 Agent 自动重试、现场是否提供人工重试入口 |
| API V2 | 仅部分接口逐项写出 P0/P1，其他接口依赖模块语义推断 | 不满足“全接口优先级统一标注”的严格基线要求 |

### 6.3 建议的唯一口径

- P0：Agent 自动有界重试、错误展示、固定 Demo 链路恢复兜底；不开放用户手动阶段重试和取消按钮。
- P1：API-INS-07、API-INS-08 及其页面入口。
- 报告在线查看为 P0；PDF/DOCX/XLSX/JSON 导出整体为 P1。若比赛必须现场演示 PDF 导出，则应将 API-RPT-03/04、`report_exports` 表和 P13 相应功能整体提升为 P0，并同步修改 PRD。
- API 文档每个接口增加明确的“优先级”和“Demo 实现状态”，不得只依赖章节标题。

## 7. 测试用例一致性

### 7.1 P0 阻断问题：第七份正式文档缺失

当前项目没有发现可作为正式交付件的测试用例文档。`Docs/提示词/测试提示词.md` 是故障排查 Prompt 集合，不包含以下测试用例必需信息：

- 用例编号与关联需求/API；
- 前置条件和测试数据；
- 请求参数、操作步骤和预期结果；
- 状态迁移与数据库断言；
- P0/P1、正向/异常/权限/幂等/恢复分类；
- Checkpoint 冲突、重复确认、局部重试、协作取消等核心场景覆盖；
- 页面轮询、空态、部分失败和报告入口的交互断言。

此外，该提示词使用 PostgreSQL，与 MySQL V1 技术基线直接冲突。

### 7.2 最低补齐范围

至少需要形成一份《FurniScope 测试用例 V1》，覆盖：

1. P0 主链路：登录、产品创建与解析、画像确认、数据导入、任务创建启动、竞品确认恢复、评论洞察、机会报告。
2. 状态链路：正常完成、`waiting_human`、`partial_succeeded`、失败、自动重试、报告生成。
3. 恢复链路：Checkpoint 一致、Checkpoint 冲突、竞品集合版本冲突、幂等重复提交。
4. P1 契约：人工阶段重试、取消、Listing、报告导出历史，统一标记本次 Demo 不执行。
5. 数据库断言：任务、阶段运行、Checkpoint、控制事件、部分失败、报告 UUID 的事务一致性。

## 8. 问题清单与关闭条件

| 编号 | 问题 | 优先级 | 关闭条件 |
|---|---|---|---|
| DOC-CONS-001 | API 返回字段未全部进入数据字典 | P0 | API 全量响应 Schema 与数据字典逐叶字段映射为 100% |
| DOC-CONS-002 | MySQL V1 缺少 V2 工作流持久化实体和字段 | P0 | 发布数据库设计 V2 与迁移 SQL，覆盖解析、Checkpoint、部分失败、控制事件、竞品确认和取消 |
| DOC-CONS-003 | PRD PostgreSQL、数据库 MySQL、测试提示词 PostgreSQL 冲突 | P0 | 七份文档采用唯一主库口径 |
| DOC-CONS-004 | 报告导出 P0/P1 范围冲突 | P0 | PRD、API、数据库、页面统一选择 P0 或 P1 |
| DOC-CONS-005 | 手动重试与 P0 自动重试边界不清 | P1 | PRD/API/页面明确自动与人工重试的实施范围 |
| DOC-CONS-006 | API 并非每个接口都有显式优先级与 Demo 状态 | P1 | 全接口补充优先级和实现状态 |
| DOC-CONS-007 | 正式测试用例文档缺失 | P0 | 形成测试用例文档并覆盖 P0 主链路、异常、权限、幂等和恢复 |

## 9. 最终判定

当前不能判定“文档闭环完成”。

建议按以下顺序修复后重新校验：

1. 先确定唯一 P0/P1 范围和数据库技术栈；
2. 发布 MySQL 数据库设计 V2 与迁移 SQL；
3. 将 API 全量响应字段补入数据字典；
4. 生成与需求、API、数据库、Agent 状态可追踪的正式测试用例；
5. 运行第二轮自动扫描和人工状态机复核。

当 DOC-CONS-001～004、007 全部关闭，且页面接口扫描保持零缺失、零占位时，方可将七份文档判定为闭环完成。
