# FurniScope 产品数据字典 V2.0 变更清单

## 1. 新增数据库实体与字段

| 变更对象 | 新增内容 | 原因 |
|---|---|---|
| `product_parse_job` | 解析任务 ID、状态、阶段、进度、画像版本、文件统计、属性数、冲突数、可重试性、失败信息、幂等和审计字段 | 支撑 API-PRD-07 和解析任务持久化 |
| `product_parse_job_file` | 文件级安全状态、解析状态、阶段、属性数、错误和结果引用 | 支撑部分成功和文件级重试 |
| `competitor_set_confirmation` | 集合版本、Checkpoint 阶段、确认商品、样本统计、确认人、恢复命令和 Outbox 状态 | 支撑 API-CMP-07 冻结集合并恢复 LangGraph |
| `analysis_job` | `task_uuid`、`checkpoint_stage`、`cancel_requested`、取消原因、申请人和取消时间 | 支撑对外任务标识、Checkpoint 和 API-INS-08 |
| `analysis_stage_run` | 错误消息、父运行、重试原因、申请人、时间和重试范围 | 支撑 API-INS-07 断点与局部重试 |
| `workflow_partial_failure` | 失败单元、数量、影响、置信度上限、可重试性和解决状态 | 持久化并行批次/分支的部分失败 |
| `workflow_checkpoint` | Checkpoint、Thread、阶段、版本、State 快照、哈希和安全恢复标记 | 支撑 LangGraph 长期恢复 |
| `workflow_control_event` | 恢复竞品、恢复专家复核、重试和取消事件及 Outbox 状态 | 保证数据库事务与 Graph 控制一致 |
| `analysis_report` | `report_uuid` | 支撑 API-INS-03 到报告页面的稳定跳转 |

## 2. 新增 Agent 内部状态字段

- `partial_failures`
- `checkpoint_stage`
- `retry_context`
- `human_review`
- `cancel_requested`
- `competitor_set_version`
- `report_uuid`
- `stage_results`
- `parallel_unit_state`

这些字段属于 LangGraph State；长期恢复通过数据库 Checkpoint 和业务实体投影，禁止把评论原文、完整文件或大向量写入 State。

## 3. 新增 API 输出衍生字段

| API | 新增字段 |
|---|---|
| API-INS-03 | `stage_runs`、`partial_failures`、`checkpoint_stage`、`retryable`、`report_uuid` |
| API-DSH-01 | `metrics`、`recent_tasks`、`human_todos`、`recent_reports` |
| API-PRD-07 | `summary`、`file_results` |
| API-CMP-06/07 | `competitor_set_version`、竞品汇总、有效评论数、`resume_command_id`、`resumed_from_checkpoint` |
| API-INS-07 | `retry_unit_count`、重试 Stage 和 attempt 信息 |
| API-INS-08 | `cancel_requested`、安全停止说明 |
| API-RPT-05 | `report_exports`、短效 `download_url`、`expires_at` |

## 4. 修改字段与规则

| 字段/规则 | V1 | V2 |
|---|---|---|
| 数据库类型口径 | PostgreSQL/全 UUID 建议 | MySQL 8.0；内部 BIGINT，对外任务和报告 UUID |
| `analysis_job.status` | 混合阶段和发布状态 | `draft/queued/running/waiting_human/partial_succeeded/succeeded/failed/cancelled` |
| `analysis_job.current_stage` | 未统一 | 对齐 Agent N01—N19 正式阶段 |
| `analysis_stage_run.status` | 缺少人工等待、重试计划和部分成功 | 增加 `waiting_human/retry_scheduled/partial_succeeded` |
| 任务终态 | `published/partial/failed/cancelled` | `succeeded/failed/cancelled`；`partial_succeeded` 非终态 |
| API `stages` | 简化名称 | V2 更名为 `stage_runs` |
| `retryable` | 单一节点失败标记 | 区分数据库 Stage、Agent 单元和 API 任务级衍生口径 |

## 5. 数据库迁移影响

MySQL V1 尚未完整承载新增解析任务、Checkpoint、部分失败、控制事件和竞品集合确认实体。开发前需要新增 DDL，并扩展 `analysis_tasks`、`task_stage_runs`；不得仅使用 Redis 或 Worker 内存作为长期事实来源。
