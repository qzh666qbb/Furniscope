# FurniScope PostgreSQL 数据库初始化说明 V2.1

## 1. 环境要求

- PostgreSQL 16+
- UTF-8 数据库
- 可创建 Schema、扩展、表、索引、触发器和外键的数据库账号
- Python 应用使用独立低权限账号，生产环境不得使用超级用户连接

## 2. 创建数据库与执行 DDL

```bash
createdb furniscope
psql "postgresql://<user>:<password>@<host>:5432/furniscope" \
  -v ON_ERROR_STOP=1 \
  -f furniscope_postgresql_v2.sql
```

脚本在单个事务中创建 `furniscope` Schema、业务表、约束、索引、注释和 `updated_at` 触发器。任一语句失败时整体回滚。

应用连接串通过环境变量提供：

```dotenv
DATABASE_URL=postgresql+asyncpg://furniscope_app:<password>@localhost:5432/furniscope
LANGGRAPH_DATABASE_URL=postgresql://furniscope_app:<password>@localhost:5432/furniscope
```

不得将真实密码、阿里云 Model Router API Key 或对象存储密钥写入 SQL、代码仓库或 Demo 数据。

## 3. 初始化 LangGraph Checkpointer

安装依赖：

```bash
pip install "langgraph-checkpoint-postgres" "psycopg[binary,pool]"
```

首次启动时调用一次官方迁移：

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def setup_langgraph(database_url: str) -> None:
    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        await checkpointer.setup()
```

- 官方 Checkpointer 内部表由依赖包管理，不得复制到业务 DDL。
- `furniscope.workflow_checkpoints` 是 API/审计使用的安全恢复点投影，不替代官方内部表。
- Redis 仅保存缓存、运行锁、限流和短期队列状态，不作为 Checkpoint 长期事实来源。

## 4. 基础枚举初始化

业务状态枚举由数据字典、Pydantic Schema 和数据库 `CHECK` 约束共同管理，不使用可被运行时随意修改的枚举配置表。DDL 只初始化数据字典明确规定的 5 个 RBAC 角色；权限数组暂为空，必须在权限码注册完成后由正式 Seed 更新，不在数据库脚本中编造权限。

| 角色码 | 名称 |
|---|---|
| `owner` | 企业负责人 |
| `product_rd` | 产品研发 |
| `market_ops` | 市场运营 |
| `sales` | 外贸销售 |
| `admin` | 系统管理员 |

必须在后端保持一致的核心枚举包括：

| 枚举 | 初始值 |
|---|---|
| `analysis_tasks.status` | `draft/queued/running/waiting_human/partial_succeeded/succeeded/failed/cancelled` |
| `task_stage_runs.status` | `queued/running/waiting_human/retry_scheduled/succeeded/partial_succeeded/failed/skipped/cancelled` |
| `workflow_control_events.event_type` | `resume_competitors/resume_expert_review/retry_stage/cancel_task` |
| `workflow_control_events.status` | `pending/enqueued/consumed/failed/cancelled` |
| `workflow_checkpoints.status` | `active/consumed/superseded/corrupted` |
| `workflow_partial_failures.resolved_status` | `open/retry_scheduled/resolved/accepted_limit` |
| `product_parse_jobs.status` | `queued/processing/succeeded/partial/failed/cancelled` |
| `report_exports.status` | `queued/generating/succeeded/failed/expired` |

若后续需要动态配置展示名称，应先修订数据字典和数据库设计，再新增字典表；本次 Demo 不自行扩展。

## 5. Demo 基础数据

DDL 不写入默认账号或默认密码。Demo 初始化顺序为：

1. 创建一个脱敏企业租户；
2. 通过密码初始化脚本生成 Argon2/bcrypt 哈希后创建管理员用户；
3. 导入企业档案与制造能力；
4. 上传沙发产品资料并由 API 创建产品与解析任务；
5. 通过 API 导入固定、授权、脱敏的竞品和评论数据集。

真实客户数据不得直接写入公开 SQL。测试数据应由正式测试用例或独立 Seed 脚本维护。

## 6. 初始化验证

```sql
SET search_path TO furniscope, public;

SELECT current_database(), current_schema();

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'furniscope'
ORDER BY table_name;

SELECT extname
FROM pg_extension
WHERE extname = 'pgcrypto';

SELECT code, name
FROM furniscope.roles
ORDER BY id;
```

需要重点确认以下表存在：

```text
analysis_tasks
task_stage_runs
product_parse_jobs
workflow_checkpoints
workflow_partial_failures
workflow_control_events
competitor_set_confirmations
roles
user_roles
product_profile_versions
market_metrics
price_bands
opportunity_scores
manufacturing_requirements
enterprise_fit_details
analysis_reports
```

最后执行一次最小事务测试：创建草稿任务、阶段运行记录和安全 Checkpoint 投影，回滚后确认没有残留数据。
