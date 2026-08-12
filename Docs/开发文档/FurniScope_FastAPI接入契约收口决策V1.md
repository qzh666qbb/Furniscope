# FurniScope FastAPI 接入契约收口决策 V1

## 1. 决策结论

本文件锁定 FastAPI 开发前仍有歧义的工程契约，不改变 FurniScope 核心业务范围、`user/admin` 角色、五阶段投影或家具洞察规则。

| ADR | 决策 | 实现口径 |
|---|---|---|
| ADR-01 | V3 从零 DDL 是新环境唯一物理基线 | `furniscope_postgresql_v3.sql`；V2.1迁移必须满足V3代码查询兼容和数据无损，但历史库不以默认值、废弃附加列名等目录对象逐字同构作为上线门禁 |
| ADR-02 | 认证采用短期Access Token + 轮换Refresh Token | Access Token不落库；Refresh Token仅存SHA-256摘要到`auth_sessions`；旧Token复用撤销整个token family |
| ADR-03 | 乐观并发不新增业务`version`列 | 使用`updated_at`规范化强ETag；更新接口要求`If-Match`；比较与更新处于同一事务 |
| ADR-04 | HTTP幂等与领域幂等双层承载 | 要求`Idempotency-Key`的写接口统一使用轻量`api_idempotency_records`持久化请求哈希与脱敏Envelope；领域唯一键继续防止重复计费、恢复和结果写入；认证接口不写通用幂等表 |
| ADR-05 | JSONB双层校验 | FastAPI/Pydantic校验完整嵌套Schema；PostgreSQL约束顶层类型、必需数组非空及关键可索引列；Schema版本随任务或配置冻结 |
| ADR-06 | 技术详情分级投影 | user仅见stage_code、attempt_no、status、started_at、ended_at、retryable；admin额外见error_code和脱敏error_message；两者均不见input_ref/output_ref、Prompt正文、评论全文或密钥 |
| ADR-07 | 确认恢复使用事务Outbox | 回答事务不直接调用LangGraph；独立Worker消费`resume_confirmation`并执行服务端构造的`Command(resume=...)` |
| ADR-08 | Model Router协议按能力分离配置 | Chat使用OpenAI兼容`/chat/completions`，Embedding使用`/embeddings`，qwen3-rerank使用`/reranks`；三类Base URL、Path和API Key来自环境，正式协议以API V3.2为准 |
| ADR-09 | 合成数据可用于开发和案例演示 | 必须标识`demo_synthetic/synthetic_demo`，报告显著披露“非真实市场数据”，不得与客户真实结论混用 |

## 2. V2.1迁移关闭条件

迁移验收分为两层：

1. 强制门禁：事务执行成功、33+认证会话核心表齐全、V3代码使用字段存在、旧关键字段为0、角色仅user/admin、迁移数据计数和JSON形态断言通过、失败可整体回滚。
2. 目录差异报告：历史库保留的兼容附加列、不同默认值和旧约束名称必须生成差异清单；不得伪称物理同构。新业务代码不得依赖这些历史附加列。

迁移当前必须保证：`enterprise_profiles.business_model`存在，八张结果/报告表使用`analysis_job_id`，对应旧`business_models/task_id`为0。

## 3. user_confirmation事务边界

中断发布事务必须同时完成：safe业务Checkpoint、`task_stage_runs(user_confirmation,waiting_human)`、pending confirmation、任务`waiting_human/user_confirmation/checkpoint_stage`。

回答事务必须同时完成：

1. 锁定confirmation、task、回答user和safe active Checkpoint；
2. 校验同tenant、user角色、pending、未过期、选项和幂等；
3. confirmation→responded；等待Stage→succeeded；task→queued；
4. 写入唯一`resume_confirmation` Outbox；
5. 返回accepted，不同步等待图执行。

Worker成功执行`Command(resume=服务端载荷)`后，Outbox与业务Checkpoint转为consumed。客户端不得提交`checkpoint_stage`、`checkpoint_id`或`tenant_id`。

## 4. 合成数据治理

- 数据集`source_type=demo_synthetic`；JSON快照带`data_class=synthetic_demo`。
- 合成邮箱使用`.invalid`，测试密码哈希不得构成可登录凭据。
- 合成评论允许用于验证Span、聚类、评分和报告流程，但所有UI、报告和答辩截图需标记“演示合成数据”。
- 将来接入真实数据时复用同一导入Schema、质量规则和证据链，不改API字段。

## 5. 影响文件

- 已同步：V3 DDL、V2.1→V3迁移SQL、数据字典V3、API V3、Agent Repository/Graph、PostgreSQL集成测试。
- 后续同步：数据库设计V3表清单、Agent实现说明、准入复核报告、项目索引、FastAPI认证与Worker实现。

## 6. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-10 | 锁定认证、并发、幂等、JSON、诊断、迁移、确认Outbox及合成数据契约 |
| V1.1 | 2026-08-10 | 按已准入API V3.2同步通用持久化幂等与阿里云Embedding/Rerank正式协议，关闭同层冲突 |
