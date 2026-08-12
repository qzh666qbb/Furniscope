# FurniScope Agent业务引擎实现说明 V1

## 1. 实现范围

本模块实现 FurniScope“跨境家具超级 AI 员工”的 LangGraph 业务编排核心，不包含 FastAPI Web 接口，也不把内部能力包装成用户角色。系统鉴权角色仍只有 `user/admin`。

实现依据：

- `04_FurniScope_Agent工作流设计V2.md`
- `02_FurniScope产品数据字典V3.md`
- `03_FurniScope_PostgreSQL数据库设计V3.md`
- `furniscope_postgresql_v3.sql`

## 2. 代码目录

```text
backend/furniscope_agent/
├── config.py          # 环境变量配置
├── contracts.py       # 工具和持久化端口
├── state.py           # LangGraph State与锁定枚举
├── errors.py          # Supervisor错误分类
├── model_router.py    # 阿里云Model Router请求、重试与Schema校验
├── repository.py      # PostgreSQL任务、Stage、Checkpoint、确认和模型追溯
├── nodes.py           # I00—I19节点实现
├── graph.py           # 条件边、Map/Reduce、并行Join和恢复入口
├── synthetic_toolbox.py # 真实数据库合成案例适配器，仅开发/测试启用
├── demo_support.py    # 仅用于编排冒烟测试的确定性适配器
└── demo.py            # 本地可运行演示
```

## 3. 端到端伪代码

```text
run(task_id, tenant_id):
  I00 读取已事务创建的draft任务并冻结版本
  I01 校验租户、状态、版本、数据集、预算和停止标记
  I02 加载产品画像与企业能力
       if 关键事实冲突或阻断缺失: user_confirmation
  I03 校验授权、样本、关联、时间和数据质量
       if 不可分析: failed
       if 可探索但需用户选择: user_confirmation
  I04 竞品硬过滤
  I05 向量召回；失败时规则召回降级
  I06 重排、分类、解释；重大歧义时user_confirmation
  I08 清洗评论并生成批次引用
  I09 Send(每个评论批次)并行抽取观点与原文Span
  I10 Reduce批次结果；记录partial_failures并限制置信度
  I11 聚类；失败时按家具本体规则聚合
  I12 Fork:
       A 价格与竞争（必需）
       B 趋势（无可比时间点则skipped）
       C 企业适配（必需）
  I13 Join；只补跑失败分支
  I14 按20/15/20/15/10/20计算机会分，置信度独立输出
  I15 Send(Top机会)并行生成工程建议
       高风险建议: user_confirmation
  I17 审计证据、Span、分母、反证和限制
  I18 模型生成在线报告；失败时确定性模板降级
  I19 最终事务确认报告存在并写succeeded/completed/100
```

统一确认：

```text
同一事务持久化 confirmation + waiting_human Stage + safe checkpoint + task waiting_human
interrupt(user_confirmation)
user仅提交 confirmation_id/selected_option/user_input
服务端校验租户、user角色、状态、过期、原始options和safe active checkpoint
同一事务写responded + waiting Stage succeeded + resume_confirmation Outbox + task queued
Worker领取Outbox并graph.ainvoke(Command(resume=服务端payload), thread_id=task_uuid)
成功后Outbox与业务Checkpoint置为consumed；Worker崩溃超过5分钟可重领
```

## 4. 持久化实现

`PostgresWorkflowRepository`直接对齐以下 V3 表：

- `analysis_tasks`
- `task_stage_runs`
- `workflow_checkpoints`
- `workflow_partial_failures`
- `user_confirmations`
- `workflow_control_events`
- `analysis_reports`
- `ai_model_runs`

Stage写入使用任务级事务 advisory lock分配`attempt_no`。成功Stage的`output_ref`保存轻量恢复信封；相同输入再次执行时直接复用结果引用，不重复调用模型。失败重试创建新attempt，不覆盖历史失败记录。

业务`workflow_checkpoints`只保存安全轻量投影；LangGraph官方Checkpointer仍通过`langgraph-checkpoint-postgres`独立初始化和传入`build_graph(checkpointer=...)`。

## 5. Model Router

`ModelRouterClient`实现：

- Bearer Key仅从`ALIYUN_MODEL_ROUTER_API_KEY`读取；
- Base URL和请求路径由环境变量配置；
- 429、408、部分5xx和网络超时有界指数退避；
- Pydantic结构化Schema校验；
- 内容拒绝不无限重试；
- 每次最终成功或失败写`ai_model_runs`追溯。

项目资料没有锁定阿里云Model Router的最终HTTP路径，因此使用`ALIYUN_MODEL_ROUTER_CHAT_PATH`配置，默认兼容`/chat/completions`。接入真实环境前必须以赛事提供的网关说明确认该路径和响应Envelope。

## 6. 运行

安装依赖后执行编排冒烟演示：

```bash
cd /Users/bytedance/AI_Cross_Border
.venv/bin/python -m backend.furniscope_agent.demo
```

该演示只验证I00—I19、Map/Reduce、并行Join和最终状态，不产生真实市场结论。

生产装配流程：

```python
pool = await asyncpg.create_pool(settings.database_url)
repository = PostgresWorkflowRepository(pool)
checkpointer = ...  # AsyncPostgresSaver，按所用版本初始化
tools = FurnitureCapabilityToolbox(...)  # 真实查询/规则/向量/RAG实现
graph = build_graph(repository, tools, checkpointer=checkpointer)
engine = FurniScopeAgentEngine(graph, repository)
result = await engine.run(initial_state)
```

## 7. 文档未锁定、未擅自实现的内容

以下信息不足，代码只定义接口，不生成虚构业务逻辑：

1. 数据质量、候选数、覆盖率、低置信度等具体阈值；必须来自`analysis_tasks.analysis_config`的版本化Schema。
2. 家具本体内容、向量模型、聚类算法参数、规则重排权重和知识库来源。
3. 各节点Prompt正文、输出JSON Schema和阿里云Model Router正式HTTP协议。
4. I18生产报告生成算法仍需真实Prompt和Schema；当前`SyntheticFurnitureToolbox`会写合法在线报告与模型追溯，并显著标记`synthetic_demo`。I19在最终事务中校验报告后完成任务。
5. Agent State未包含创建任务必需的`created_by/job_name/job_type/idempotency_key`完整命令，所以业务服务需先事务创建`draft analysis_tasks`，再启动I00；没有擅自扩展锁定State。
6. Redis任务队列部署拓扑未实现；确认恢复Worker已实现数据库Outbox领取、5分钟租约重领、成功消费与失败状态。

## 8. 已验证结果

- Python编译检查通过。
- I00—I19完整链路执行成功，最终状态为`succeeded/completed/100`。
- 评论Map、三分支Analytics Join、机会Strategy Map均实际经过LangGraph执行。
- `user_confirmation → interrupt → Outbox Worker → Command(resume)`恢复执行成功。
- Stage运行总数为21，所有运行均进入`succeeded/skipped/partial_succeeded`之一。
- 真实PostgreSQL合成案例I00—I19执行成功，任务为`succeeded/completed/100`，在线报告与模型追溯已入库。
- 真实PostgreSQL确认事务验证通过：约束失败全量回滚、同答案幂等、异答案冲突、跨租户拒绝、非safe Checkpoint拒绝、Worker租约重领和重复消费保护。

## 9. 合成数据边界

合成适配器只能在`ENABLE_SYNTHETIC_DEMO=true`且非production环境启用。数据集使用`demo_synthetic`，报告和追溯带`synthetic_demo`，结论必须展示“非真实市场数据”声明。它用于建立可演示案例和验证未来真实数据接入Schema，不得伪装为客户或平台真实事实。
