# FurniScope FastAPI基础设施实现说明 V1

## 1. 实现结论

本轮完成FastAPI基础设施，不实现产品、数据集、分析任务、确认、洞察、报告或admin业务路由。实现遵循API V3.2、数据字典V3.2、PostgreSQL V3和FastAPI接入契约V1.1。

准入状态：**PASS，可进入下一轮业务接口分模块实现。**

当前本机`furniscope`数据库已完成V2.1→V3迁移：`furniscope` Schema包含35张核心表，`public` Schema保留4张LangGraph官方Checkpoint表；liveness与readiness均返回200。

## 2. 已实现范围

| 能力 | 文件 | 实现口径 |
|---|---|---|
| 应用工厂与生命周期 | `backend/furniscope_api/app.py` | 工厂注入Settings/Database；关闭时释放连接池；只注册基础设施路由 |
| 环境配置 | `config.py` | PostgreSQL asyncpg；JWT TTL固定；production缺密钥启动失败；密钥使用SecretStr |
| PostgreSQL连接池 | `database.py` | SQLAlchemy AsyncEngine；pool_pre_ping；事务Session；readiness校验V3五张关键表 |
| 统一Envelope | `schemas.py` | success/data/error/request_id/timestamp；分页items/total/page/page_size/has_next |
| 统一异常 | `errors.py` | BusinessError、422、404和500统一为API V3 Envelope；不返回内部异常正文 |
| 请求ID | `middleware.py`、`context.py` | 接受合法UUID或服务端生成；响应Header、Body和日志一致 |
| 日志脱敏 | `logging.py` | 屏蔽Authorization、密码、Token、API Key、Prompt、评论全文；JSON结构日志 |
| RS256鉴权 | `auth.py` | 固定算法、kid、公钥集、issuer/audience、必需claims、时钟偏差；未知kid/算法降级拒绝 |
| 数据库状态复核 | `auth.py` | 每次鉴权按user_id+tenant_id查询users/tenants；角色与Token一致；状态失效立即拒绝 |
| 角色与租户上下文 | `auth.py`、`context.py` | 只允许user/admin；admin独立依赖；tenant_id仅来自Token+数据库，不接受客户端覆盖 |
| 分页依赖 | `dependencies.py` | page从1开始；page_size默认20、最大100 |
| 健康检查 | `routes/health.py` | `/health/live`与`/health/ready`；后者要求数据库可达且V3关键表存在 |

## 3. 工程决策

1. 健康检查属于基础设施，不进入业务API编号，不返回业务数据。
2. 鉴权依赖已经可复用于后续Router；本轮不实现API-AUTH-01/02/03。
3. 401/403通过测试应用挂载的`/_test/*`路由验证，测试路由不进入生产应用和OpenAPI。
4. readiness不仅执行`SELECT 1`，还验证`tenants/users/auth_sessions/api_idempotency_records/analysis_tasks`，防止连接到错误或未初始化数据库后误报健康。
5. 没有建立Access Token黑名单；每次请求按文档查询用户与租户状态。Refresh轮换逻辑留待认证业务接口迭代。

## 4. 启动方式

```bash
cd /Users/bytedance/AI_Cross_Border
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=backend uvicorn furniscope_api.app:app --host 127.0.0.1 --port 8000
```

生产环境必须配置：`DATABASE_URL`、`FURNISCOPE_JWT_PRIVATE_KEY`、`FURNISCOPE_JWT_PUBLIC_KEYS_JSON`。密钥不得提交到仓库。

## 5. 实际验证结果

| 验证项 | 结果 |
|---|---|
| Python compileall | PASS |
| pytest | 12 passed；包含真实PostgreSQL Agent事务、确认恢复和端到端报告持久化测试 |
| OpenAPI `/openapi.json` | HTTP 200 |
| Liveness `/health/live` | HTTP 200，统一成功Envelope，Header/Body request_id一致 |
| Readiness `/health/ready` | HTTP 200；数据库连接与V3关键表检查均通过 |
| 404异常 | HTTP 404，`RESOURCE_NOT_FOUND`统一Envelope |
| 缺失Token | HTTP 401，`AUTH_TOKEN_INVALID` |
| user访问admin依赖 | HTTP 403，`ADMIN_REQUIRED` |
| admin与tenant上下文 | PASS，身份由Token和数据库行共同确认 |
| kid、禁用用户 | 未知kid返回401；disabled user返回403 |
| 日志与OpenAPI敏感词扫描 | 无Token、密码、API Key或评论全文泄漏 |

测试环境警告：当前FastAPI TestClient提示Starlette未来将切换`httpx2`，不影响本轮结果，后续依赖升级时处理。

## 6. 未实现项

- API-AUTH-01/02/03及Refresh Token事务；
- 通用幂等Repository和业务写接口集成；
- 产品、数据集、Agent任务、确认、洞察、报告及admin业务路由；
- React 19 + Vite前端；

上述均属于后续迭代，不是本轮占位代码。

## 7. 下一迭代准入条件

1. 保留迁移前备份，数据库变更继续使用事务迁移脚本。
2. 先实现认证与通用幂等Repository，再按API模块逐批增加业务路由。

## 8. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-10 | 完成FastAPI基础设施、自动化测试、真实进程启动和HTTP验证 |
| V1.1 | 2026-08-10 | 完成本机V2.1→V3迁移、迁移兼容修复和真实数据库全量测试；readiness更新为PASS |
