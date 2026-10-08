# FurniScope PostgreSQL 数据库初始化说明 V3.0

> 2026-10-07：现行增量顺序为v3.15 → v3.18 → v3.16 → v3.17 → v3.19 → v3.20 → v3.21 → v3.22 → v3.23 → v3.24 → v3.25 → v3.26 → v3.27 → v3.28 → v3.29 → v3.30 → v3.31 → v3.32 → v3.33 → v3.34，均已纳入根SQL。18先于16使客户记忆表纳入统一RLS；v3.33增加租户数据分类，v3.34增加受控SKU事实身份同步。升级前停旧worker；旧库在既有结构上执行增量，不可重跑CREATE TABLE基线。文件全名与边界见[数据库运行与升级说明](../开发文档/FurniScope_数据库运行与升级说明.md)。

独立验收脚本`scripts/verify_enterprise_migrations.py`创建全新测试库，覆盖空库、旧基线存量升级、重复迁移及启动RLS检查，不重置已有库。v3.15-v3.30结果及SHA见[多租户隔离验收证据](../../artifacts/tenant-isolation-20261006/README.md)；v3.31复验见[受控问数迁移证据](../../artifacts/data-query-migrations-20261006-final/migration-summary.json)；v3.32专项断言见`tests/test_product_imports.py`；v3.33-v3.34最终验收见[整改迁移摘要](../../artifacts/system-review-20261007-fixes/migration-v34/migration-summary.json)。

## 1. 文件清单

- 从零建库：项目根目录`furniscope_postgresql_v3.sql`
- V2.1→V3 迁移：项目根目录`migrations/v2_1_to_v3.sql`
- 结构设计：`Docs/项目设计文档/03_FurniScope_PostgreSQL数据库设计V3.md`

## 2. 环境要求

- PostgreSQL 16.x；执行账户具有建库、pgcrypto和创建/授予受限角色的权限。pgvector可选，缺失时知识检索使用FTS/JSONB/Python cosine降级。迁移账号与应用账号必须分离，部署账号由`scripts/provision_database_roles.sh`按职责授权。
- UTF-8 数据库；推荐时区 UTC，应用层按用户时区展示。
- 生产连接、密码、Model Router API Key 由环境变量或密钥管理服务注入，不写入 SQL。

## 3. 从零初始化

```bash
createdb furniscope
psql -v ON_ERROR_STOP=1 -d furniscope -f furniscope_postgresql_v3.sql
```

各迁移使用各自事务，失败时停止；基线与全部增量不是单一大事务。迁移创建NOLOGIN职责角色，Compose最后由`provision_database_roles.sh`创建独立LOGIN账号，不创建业务用户、默认业务密码或模型密钥。若存量数据违反跨租户关联约束，先排查脏关联，不自动删除。Backend/Worker启动会检查迁移、职责分离、Cell placement及tenant表FORCE RLS，未完整升级时拒绝运行。

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

`core_table_count=35`只适用于2026-08-09核心基线；现行建库还包括所引用迁移新增表，不能再用35作为总表数断言。非法角色/阶段应为0；表注释缺失作为结构治理检查单独报告。企业闭环还必须通过Backend/Worker启动时的全部租户业务表RLS检查，并运行`tests/test_tenant_boundaries.py`验证跨租户关联、连接重用及事务重绑定。

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
| V3.2 | 2026-10-06 | 登记v3.31受控问数事实投影、审计表、RLS及fresh/upgrade/repeat验收 |
| V3.3 | 2026-10-07 | 登记v3.32产品主档比较键、组合关系、批量导入任务、RLS及启动门禁 |
| V3.4 | 2026-10-07 | 登记v3.33租户数据分类和v3.34 SKU事实身份同步；fresh/upgrade/repeat及启动检查通过 |

## 13. 本次变更摘要

- 初始化角色改为 `users.role_code`，不再写角色表。
- 明确无默认密码、无硬编码密钥、无默认模型配置。
- 增加从零验证、迁移副本验证、归档检查与回滚流程。
- 明确业务 Checkpoint 与 LangGraph 官方 Checkpointer 的职责分离。
- 记录 PostgreSQL 16.14 空库及带数据迁移的实际结果。
- 修正并复验 `business_model` 与五类分析结果实体的 `analysis_job_id` 字段命名。

## 14. v3.31 受控问数增量

`migrations/v3_31_controlled_data_query.sql`增加：

| 对象 | 用途 |
|---|---|
| `data_metric_catalog` | 10项白名单指标的名称、来源、类型和单位 |
| `data_fact_projections` | canonical数据版本到事实层的幂等投影及SHA |
| `sales_facts_daily` | 按日期、SKU、站点保存不可变销量事实 |
| `inventory_facts_daily` | 按日期、SKU、站点保存不可变库存事实 |
| `data_query_executions` | 保存QueryPlan、过滤条件、源版本SHA、结果和结果SHA |

四张租户表均启用`ENABLE ROW LEVEL SECURITY`和`FORCE ROW LEVEL SECURITY`，写入后
业务角色不能UPDATE/DELETE。应用启动要求`schema_migrations`至少包含
`v3_31_controlled_data_query`至`v3_34_sku_fact_identity`且核心结构完整，否则readiness失败。

本地隔离验收命令：

```bash
PYTHONPATH=.:backend python scripts/verify_enterprise_migrations.py \
  --admin-url postgresql://<local-admin>@localhost/postgres \
  --suffix <unique-suffix> \
  --output artifacts/data-query-migrations-<date>
```

脚本只允许本地`postgres`管理库，使用全新数据库名，拒绝重置已有库。验收必须同时满足：
fresh安装、存量哨兵upgrade、全部迁移重复执行稳定、4张问数租户表强制RLS、指标目录
恰为10项、启动隔离检查通过。

## 15. v3.32 产品主档导入增量

`migrations/v3_32_product_catalog_imports.sql`必须在v3.31后执行，增加：

| 对象 | 用途 |
|---|---|
| `products.sku_compare_key` | 统一大小写及首尾空格后的租户内SKU唯一比较键 |
| `product_groups` / `product_group_members` | SPU、变体、套装和BOM关系 |
| `product_import_jobs` / `product_import_rows` | 预检、逐行错误、修正、幂等提交和恢复 |

四张新增租户表启用RLS、写栅栏和审计所需约束。应用启动门禁会检查
`schema_migrations.version='v3_32_product_catalog_imports'`；缺失时直接拒绝启动，而不是降级跳过。

## 16. v3.33—v3.34 系统复盘整改增量

`migrations/v3_33_tenant_data_class.sql`必须在v3.32后执行，为`tenants`增加
`data_class`及`business/test/demo`约束。历史租户默认按`business`处理，测试和演示夹具
必须显式更新分类；管理员业务列表及KPI只读取`business`。

`migrations/v3_34_sku_fact_identity.sql`在v3.33后执行，安装受控函数
`furniscope.rename_tenant_sku_facts`。产品SKU改名通过该函数在同一事务内同步产品主档、
预测目录、别名、销量事实和库存事实；冲突时整体回滚。迁移撤销`PUBLIC`执行权限，只向
租户运行角色和平台管理角色授权，并固定函数`search_path`。

升级后至少检查：

```sql
SELECT version
FROM furniscope.schema_migrations
WHERE version IN ('v3_33_tenant_data_class', 'v3_34_sku_fact_identity');

SELECT data_type, column_default
FROM information_schema.columns
WHERE table_schema='furniscope'
  AND table_name='tenants'
  AND column_name='data_class';

SELECT has_function_privilege(
  'public',
  'furniscope.rename_tenant_sku_facts(bigint,text,text)',
  'EXECUTE'
);
```

最后一项必须为`false`。独立fresh/upgrade库、重复迁移、存量租户哨兵、启动隔离和
SKU事实身份探针已通过，结构化结果见
[`migration-summary.json`](../../artifacts/system-review-20261007-fixes/migration-v34/migration-summary.json)。

## 17. v3.36 市场决策数据治理增量

`migrations/v3_36_market_intelligence_governance.sql`在v3.35后执行，新增市场决策批次
与逐记录血缘表，并安装不可变触发器、租户RLS、平台管理策略、写栅栏和最小授权。

```bash
psql -X -v ON_ERROR_STOP=1 -d <database> \
  -f migrations/v3_36_market_intelligence_governance.sql
```

升级后检查：

```sql
SELECT version FROM furniscope.schema_migrations
WHERE version='v3_36_market_intelligence_governance';

SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='furniscope'
  AND c.relname IN (
    'market_intelligence_batches','market_intelligence_lineage'
  );
```

版本必须存在，两张表的两个RLS布尔值均必须为`true`。Backend启动门槛会重复检查该结构。
