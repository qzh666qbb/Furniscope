# FurniScope 分析任务与 Agent API 实现说明 V1

## 1. 实现结论

本轮完成 API V3 的四个 P0 分析任务接口：

- API-INS-01 `POST /api/v1/analysis-tasks`；
- API-INS-02 `POST /api/v1/analysis-tasks/{task_uuid}:start`；
- API-INS-03 `GET /api/v1/analysis-tasks/{task_uuid}`；
- API-INS-04 `GET /api/v1/analysis-tasks/{task_uuid}/result`。

真实 PostgreSQL 全量验证结果为 **30 passed、0 skipped**，满足进入统一 `user_confirmation` API 迭代的代码准入条件。

## 2. 实现范围

### 2.1 任务创建与启动

- 创建任务只写入 `draft`，不启动 LangGraph；
- 同事务校验产品、confirmed 画像、ready 数据集、租户和国家/平台/品类范围；
- 创建和启动均复用 `api_idempotency_records`；
- `analysis_tasks.idempotency_key` 继续作为任务创建的领域唯一键；
- 启动事务锁定任务并从 `draft` 原子变为 `queued`；
- HTTP 提交后才调度 Agent，避免未提交任务被 Worker 读取；
- development/test 使用 `demo_only` 合成执行适配器；production 禁止启用该适配器。

### 2.2 Agent 接入

- 复用既有 `FurniScopeAgentEngine`、`build_graph` 和 `PostgresWorkflowRepository`；
- 使用 LangGraph 官方 PostgreSQL Checkpointer；
- 未复制 I00— I19 节点；
- Worker 从任务冻结字段构造只含 ID、版本和引用的 State；
- Stage 幂等逻辑继续由既有 Agent Repository 负责，重复启动重放不会再次调度；
- Worker 异常写入脱敏 `WORKER_EXECUTION_FAILED`，不写堆栈和密钥。

### 2.3 状态与结果投影

- API-INS-03 只返回五个外部阶段；
- `stage_runs` 支持最大 100 条的受控查询；
- user 仅见六个白名单字段，admin 可增加 `error_code` 和脱敏 `error_message`；
- 两种角色均不返回 `input_ref/output_ref`；
- `partial_failures/checkpoint_stage/retryable/report_uuid` 均从真实持久化表计算；
- API-INS-04 只聚合已落库结果，不调用模型；
- 未完成任务返回 `TASK_RESULT_NOT_READY`。

## 3. 数据库兼容迁移

新增 `migrations/v3_analysis_task_api_alignment.sql`：

1. 对齐 V2.1 升级库中 `analysis_tasks.analysis_config/external_stage/internal_stage` 的 V3 默认值和非空约束；
2. 为旧迁移库的 `competitor_matches/insight_clusters/market_opportunities` 回填 `tenant_id`；
3. 为旧 `insight_clusters` 补齐 V3 兼容投影列；
4. 回填完成后增加外键、非空约束和联合索引；
5. 全部操作在单事务中执行，先迁移数据，再收紧约束，不删除历史列和业务数据。

## 4. 配置边界

新增非敏感版本配置：

- `ANALYSIS_ONTOLOGY_VERSION`；
- `ANALYSIS_SCORING_VERSION`；
- `ANALYSIS_PROMPT_BUNDLE_VERSION`；
- `ANALYSIS_MODEL_ROUTE_VERSION`；
- `ANALYSIS_WORKER_MODE=demo_only|external`。

这些配置在创建任务时冻结到既有数据库字段。`demo_only` 在 production 环境会被配置校验拒绝。

## 5. 验证结果

| 验证项 | 结果 |
|---|---|
| Python compileall | PASS |
| OpenAPI 四接口编号、方法、路径 | PASS |
| draft 创建与幂等重放/冲突 | PASS |
| confirmed 画像、ready 数据集、市场范围校验 | PASS |
| Agent 主链与在线报告持久化 | PASS |
| 五阶段、Stage 脱敏、user/admin 差异 | PASS |
| partial_failures、retryable、report_uuid | PASS |
| 结果未就绪与完整结果 | PASS |
| 跨租户隐藏 | PASS |
| 重复启动不重复 Stage | PASS |
| 全量真实 PostgreSQL 测试 | **30 passed、0 skipped** |

数据库只读断言：

- `analysis_tasks_null_projection=0`；
- `result_tenant_mismatch=0`；
- `orphan_reports=0`；
- 测试清理后 `API-INS-01/02` 幂等记录为 0。

## 6. 已知限制与下一步

1. development可选择进程内后台任务；生产强制使用Redis Streams和独立Worker。外部工具模式读取授权数据，AI员工Runtime及其触发器本期保持关闭；
2. 真实阿里云 Model Router 业务工具尚未替换合成工具箱；合成报告已明确标记 `synthetic_demo`；
3. 本轮未实现 API-CFM-01/02、洞察下钻、报告详情、Dashboard、Admin 和前端；
4. 下一轮应实现统一 `user_confirmation` 查询、回答和 Outbox 恢复 API。

## 7. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-12 | 完成 API-INS-01—04、Agent Adapter、迁移库兼容对齐及真实 PostgreSQL 零跳过验证 |
