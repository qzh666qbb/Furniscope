# FurniScope PostgreSQL 数据库初始化说明 V3.0

## 1. 文件清单

- 从零建库：`/Users/bytedance/AI_Cross_Border/furniscope_postgresql_v3.sql`
- V2.1→V3 迁移：`/Users/bytedance/AI_Cross_Border/migrations/v2_1_to_v3.sql`
- 结构设计：`Docs/项目设计文档/03_FurniScope_PostgreSQL数据库设计V3.md`

## 2. 环境要求

- PostgreSQL 16.x，执行账户具有建库和 `CREATE EXTENSION pgcrypto` 权限。
- UTF-8 数据库；推荐时区 UTC，应用层按用户时区展示。
- 生产连接、密码、Model Router API Key 由环境变量或密钥管理服务注入，不写入 SQL。

## 3. 从零初始化

```bash
createdb furniscope
psql -v ON_ERROR_STOP=1 -d furniscope -f /Users/bytedance/AI_Cross_Border/furniscope_postgresql_v3.sql
```

脚本使用事务；任一语句失败即回滚。脚本不会创建数据库登录账户、默认业务用户、默认密码、API Key 或模型密钥。

## 4. 必要基础配置

V3 不再初始化角色表：`users.role_code` 直接且仅允许 `user/admin`。业务用户应通过认证服务创建，`password_hash` 必须由认证层使用批准算法生成，不得把明文或示例密码写入数据库。

首次启用前由 admin 在系统配置中完成：

1. 创建版本化 `prompt_templates`，包括 Prompt 文本、输入/输出 Schema 和状态。
2. 创建 `model_route_configs`，配置 task_type、主备模型、超时、重试、并发和 `compute_config`。
3. Model Router 凭据只设置到后端环境变量；表中只保存模型标识和非敏感策略。
4. 创建租户和首个 admin 时采用应用服务事务，确保 tenant_id 与审计记录一致。

系统枚举使用 CHECK，不插入字典表。重点枚举：

- role：`user/admin`
- 外部阶段：`understanding_product/researching_market/evaluating_opportunity/generating_recommendation/completed`
- confirmation_type：按数据字典 V3 的事实冲突、样本不足、低置信度、高风险建议口径
- confirmation status：`pending/responded/expired/cancelled`

## 5. 从零建库验证

```bash
psql -d furniscope -v ON_ERROR_STOP=1 <<'SQL'
SET search_path=furniscope,public;
SELECT count(*) AS core_table_count
FROM information_schema.tables
WHERE table_schema='furniscope' AND table_type='BASE TABLE';
SELECT count(*) AS invalid_roles FROM users WHERE role_code NOT IN ('user','admin');
SELECT count(*) AS invalid_stage
FROM analysis_tasks
WHERE external_stage NOT IN ('understanding_product','researching_market','evaluating_opportunity','generating_recommendation','completed');
SELECT count(*) AS missing_comments
FROM information_schema.tables t
WHERE t.table_schema='furniscope' AND t.table_type='BASE TABLE'
  AND obj_description((quote_ident(t.table_schema)||'.'||quote_ident(t.table_name))::regclass) IS NULL;
SQL
```

预期：`core_table_count=35`，其余均为 0。

## 6. V2.1→V3 迁移

迁移前必须停写或进入维护窗口，并先做物理备份：

```bash
pg_dump -Fc -d furniscope_v2_1 -f furniscope_v2_1_before_v3.dump
createdb furniscope_v2_1_migration_test
pg_restore -d furniscope_v2_1_migration_test furniscope_v2_1_before_v3.dump
psql -v ON_ERROR_STOP=1 -d furniscope_v2_1_migration_test \
  -f /Users/bytedance/AI_Cross_Border/migrations/v2_1_to_v3.sql
```

在副本验证通过后，按相同方式迁移正式库。脚本不可重复执行；会检查 V2.1 基线和 V3 标志表。整个结构转换、数据合并、验证及旧表删除位于同一事务，失败时自动回滚。

迁移遵循“先归档、再合并、再校验、最后删除”。原始退役行写入 `furniscope_archive.retired_entity_rows`，含来源表、主键、tenant、完整 JSON 行和迁移版本。不要在业务验收前清理该 Schema。

## 7. 迁移后验证 SQL

```sql
SET search_path=furniscope,public;

SELECT count(*) AS core_table_count
FROM information_schema.tables
WHERE table_schema='furniscope' AND table_type='BASE TABLE';

SELECT role_code,count(*) FROM users GROUP BY role_code;
SELECT count(*) AS invalid_roles FROM users WHERE role_code NOT IN ('user','admin');
SELECT count(*) AS legacy_review_stages
FROM analysis_tasks WHERE internal_stage IN ('competitor_review','expert_review');
SELECT count(*) AS invalid_confirmations
FROM user_confirmations
WHERE jsonb_typeof(options)<>'array' OR jsonb_array_length(options)=0
   OR jsonb_typeof(evidence_refs)<>'array' OR jsonb_array_length(evidence_refs)=0;
SELECT count(*) AS reports_without_sections
FROM analysis_reports WHERE jsonb_typeof(sections)<>'array' OR jsonb_array_length(sections)=0;
SELECT source_table,count(*)
FROM furniscope_archive.retired_entity_rows
WHERE migration_version='v2_1_to_v3'
GROUP BY source_table ORDER BY source_table;
```

另外人工抽样核对：企业约束、字段映射、商品规范属性、最新机会评分、制造适配、报告分节、旧竞品确认及其 safe checkpoint 引用。

## 8. 配额迁移特别说明

旧 `compute_quotas` 先完整归档，并在存在模型路由配置时写入 `model_route_configs.compute_config.legacy_compute_quotas`。如果旧库没有任何路由配置，脚本不会虚构 `task_type/model_id` 来承载配额；admin 应依据归档行创建新的运行策略。归档保证原值可追溯。

## 9. LangGraph Checkpointer

业务 DDL 只创建 `workflow_checkpoints` 安全投影。LangGraph 官方 Checkpointer 表必须由所选 LangGraph PostgreSQL Checkpointer 版本自行初始化，建议使用同实例独立 Schema/数据库账户。应用恢复前同时校验：同 tenant、同 task、`is_safe_resume=true`、confirmation 未过期，并将 `Command(resume=...)` 写入 `workflow_control_events` 审计。

## 10. 实际执行记录

2026-08-09 使用 PostgreSQL 16.14 临时实例完成：

- V3.2 从零DDL：成功；35张核心表、35个主键、91个外键、108个索引，表注释缺失0。
- 数据字典字段覆盖：V3 数据字典的 434 个数据库实体字段全部存在于实际建库结果，缺失 0。
- V2.1 空副本迁移：成功；所有角色、阶段、确认、报告、制造适配和 Checkpoint 完整性检查为 0 异常。
- V2.1 带代表数据副本迁移：成功；1 个旧岗位用户映射为 user；1 个确认恢复到统一确认；13 行退役记录归档；15 个退役表残留 0。
- 合并抽样：MOQ 约束、asin 字段映射、seat_depth 属性、机会评分 73/0.82、报告 scope 分节均保持。
- 首轮带数据迁移发现 V2.1 缺陷触发器；事务自动回滚。迁移脚本已改为摘除旧触发器并仅对含 `updated_at` 的 V3 表重建，随后同副本成功迁移。

## 11. 后续跨文档依赖

1. ORM/Alembic 与 API Schema 必须同步 V3 表名和字段。
2. API、交互和测试文档必须统一 `user_confirmation` 与五阶段投影。
3. 部署文档需补充对象存储、LangGraph Checkpointer、环境变量和备份恢复配置。
4. 生产上线前需执行真实数据量下的索引计划、锁时长、归档容量和回滚演练。

### 11.1 本机开发库初始化记录（2026-08-10）

- 初始化目标：本机数据库`furniscope`。
- 识别基线：`furniscope` Schema为V2.1业务表；`public` Schema为4张LangGraph官方Checkpoint表。
- 恢复备份：`backups/furniscope_pre_v3_20260810.dump`，SHA-256为`82e32e19ee0ca631e6999db8068669fd10b19530ba060e3fdd60b65417888df0`。
- 迁移入口：`migrations/v2_1_to_v3.sql`；事务执行成功。
- 迁移结果：35张V3核心表、4张Checkpoint表、5行退役角色配置归档；旧核心表和旧关键列残留均为0。
- 兼容修复：删除已由`product_profile_version_id`替代且阻断V3写入的旧`analysis_tasks.product_profile_version`；补齐`analysis_reports.target_user_summary`；V2兼容报告摘要列保留数据但取消V3写入必填约束。
- 全量验证：真实PostgreSQL测试12项全部通过，包括任务、报告、确认、Outbox、Checkpoint和安全恢复。
- 开发数据：测试生成12个`.invalid`合成用户租户、6个合成数据集/任务、1份合成报告和3条确认记录；全部标记为测试/合成用途。
- FastAPI：`/health/live`、`/health/ready`和`/openapi.json`均返回HTTP 200。

## 12. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V3.0 | 2026-08-09 | 新增 V3 从零初始化、V2.1 事务迁移、归档及真实验证说明 |
| V3.1 | 2026-08-10 | 记录本机开发库备份、V2.1→V3迁移、兼容修复、合成测试数据和最终验证结果 |

## 13. 本次变更摘要

- 初始化角色改为 `users.role_code`，不再写角色表。
- 明确无默认密码、无硬编码密钥、无默认模型配置。
- 增加从零验证、迁移副本验证、归档检查与回滚流程。
- 明确业务 Checkpoint 与 LangGraph 官方 Checkpointer 的职责分离。
- 记录 PostgreSQL 16.14 空库及带数据迁移的实际结果。
- 修正并复验 `business_model` 与五类分析结果实体的 `analysis_job_id` 字段命名。
