# FurniScope 初赛附件内容清单与阅读指南

> 项目：FurniScope——跨境家具超级 AI 员工  
> 赛道：赛道三——AI 市场洞察  
> 代码仓库：https://github.com/qzh666qbb/Furniscope  
> 附件版本：Idea 初赛附件 V1

## 1. 附件用途

本附件用于证明 FurniScope 已形成从产品需求、数据模型、Agent 工作流、API、数据库到 React UI 和自动化测试的完整设计与开发基线。附件中的实现状态以开发说明为准；合成数据仅用于 Demo，不代表真实平台统计或已验证商业结论。

## 2. 建议阅读顺序

1. `00_ATTACHMENT_MANIFEST.md`：附件总清单，即当前文件。
2. `Docs/参赛提交/FurniScope_Idea附件说明V1.md`：项目价值、业务闭环、架构和实现进度。
3. `Docs/项目设计文档/01_产品需求规格说明书SRS_PRD_V2.md`：完整产品定义与 P0 范围。
4. `Docs/项目设计文档/06_FurniScope_Mermaid图V3.md`：业务流程、系统架构、ER 和 Agent 映射图。
5. `archive/ui-design-v2/Docs/FurniScope_完整UI页面设计稿_V1.pdf`：完整 UI 页面视觉方案。
6. Agent、API、数据库文档及开发实现说明。
7. `backend/`、`tests/` 和 PostgreSQL DDL：核心代码与可验证实现。

## 3. 根目录文件

| 文件 | 内容 | 评审价值 |
|---|---|---|
| `00_ATTACHMENT_MANIFEST.md` | 附件清单和阅读导航 | 解压后首先阅读 |
| `furniscope_postgresql_v3.sql` | PostgreSQL V3 从零建库 DDL | 验证真实数据模型、约束和索引 |
| `requirements.txt` | Python 运行依赖 | 验证 FastAPI、LangGraph 与 PostgreSQL 技术栈 |
| `.env.example` | 无密钥环境变量模板 | 展示数据库、百炼 Model Router 和 JWT 配置边界 |

## 4. `Docs/项目设计文档/`

该目录保存当前有效的核心产品与技术契约。

| 文件 | 主要内容 |
|---|---|
| `01_产品需求规格说明书SRS_PRD_V2.md` | 产品定位、user/admin 角色、家具业务闭环、功能与非功能需求、验收标准 |
| `02_FurniScope产品数据字典V3.md` | 数据实体、字段、枚举、来源、约束、Agent State 和 API 衍生字段 |
| `03_FurniScope_PostgreSQL数据库设计V3.md` | 数据库分层、表关系、主外键、CHECK、JSONB 和索引设计 |
| `04_FurniScope_Agent工作流设计V2.md` | LangGraph State、内部节点、Map/Reduce、重试、降级、中断和恢复 |
| `05_FurniScope_RESTful_API接口设计V3.md` | API 编号、路径、权限、请求响应、错误码及 Model Router 内部协议 |
| `06_FurniScope_Mermaid图V3.md` | 业务流程、确认恢复时序、整体架构、ER、页面流与节点映射 |
| `07_FurniScope页面交互原型说明V3.md` | 6 个 user 页面、1 个 admin 页面、状态和页面到 API 绑定 |
| `09_FurniScope测试用例V2.md` | P0 功能、异常、安全、恢复及数据库断言测试口径 |

## 5. `Docs/开发文档/`

该目录记录“设计是否已经转化为代码”，便于评委区分已实现能力与后续计划。

| 文件类别 | 内容 |
|---|---|
| Agent 实现说明 | LangGraph 节点、Checkpoint、Outbox、幂等和合成工具箱实现 |
| FastAPI 准入与基础设施说明 | 应用工厂、Envelope、鉴权、租户上下文、日志脱敏和健康检查 |
| API 追踪表 | 接口到字段、数据表和 Agent 调用的追踪关系 |
| 认证与幂等说明 | RS256、Refresh Token 轮换、重放检测和持久化幂等 |
| 产品与数据集 API 说明 | 产品画像、解析任务、市场数据集和租户隔离实现 |
| 分析任务与 Agent API 说明 | 任务创建、异步启动、五阶段投影与结果聚合实现 |
| 前端技术栈决策 | 从早期方案统一为 React 19 + Vite 的决策记录 |

## 6. `backend/furniscope_agent/`

LangGraph 超级 AI 员工业务引擎。

| 文件/类别 | 内容 |
|---|---|
| `state.py`、`contracts.py` | Agent State、节点输入输出与业务契约 |
| `graph.py` | LangGraph 图构建、路由、执行与恢复入口 |
| `nodes.py` | 产品理解、市场、评论、机会、建议和报告等内部节点 |
| `repository.py` | 任务、Stage、失败、Checkpoint 投影、确认和结果持久化 |
| `model_router.py` | 阿里云百炼 Model Router 调用封装与输出校验 |
| `synthetic_toolbox.py`、`demo_support.py` | 明确标记的 Demo 合成数据与测试支持，不冒充真实数据 |
| `config.py`、`errors.py` | Agent 配置和领域错误 |

## 7. `backend/furniscope_api/`

FastAPI 后端，按照职责分层。

| 子目录/文件 | 内容 |
|---|---|
| `app.py`、`config.py`、`database.py` | 应用工厂、环境配置和 PostgreSQL 连接池 |
| `routes/` | 认证、产品、数据集、解析任务、分析任务和健康检查路由 |
| `schemas/` | API 请求与响应 Pydantic Schema |
| `services/` | 业务编排、事务边界、Agent Adapter 与幂等服务 |
| `repositories/` | 所有带 `tenant_id` 范围的数据库访问 |
| `security/`、`auth.py` | 密码哈希、RS256 Token、Refresh 轮换和角色依赖 |
| `middleware.py`、`logging.py`、`errors.py` | 请求 ID、日志脱敏、统一异常和业务错误码 |

## 8. `tests/`

| 文件 | 覆盖内容 |
|---|---|
| `test_agent_graph.py` | Agent 正常链路、条件分支和状态投影 |
| `test_agent_postgres_integration.py` | PostgreSQL 持久化、确认事务、Outbox、Checkpoint 恢复和报告提交 |
| `test_api_infrastructure.py` | Envelope、请求 ID、401/403、租户上下文、健康检查和 OpenAPI |
| `test_auth_postgres_api.py` | 登录、RS256、Refresh 轮换、重放检测和当前用户 |
| `test_idempotency_postgres.py` | 同 Key 复用、冲突和并发唯一约束 |

开发记录中的最近一次真实 PostgreSQL 全量验证结果为 **30 passed、0 skipped**。运行完整集成测试需要提供隔离的 `FURNISCOPE_TEST_DATABASE_URL`。

## 9. `frontend/`

当前 React 19 + Vite UI 实现的精简提交内容。

| 文件/目录 | 内容 |
|---|---|
| `src/App.jsx` | user 侧工作台、新建分析、执行中、洞察和报告体验 |
| `src/Admin.jsx` | admin 用户、模型、Prompt、配置和诊断界面 |
| `src/*.css` | 完整视觉样式、响应式布局和页面细节修订 |
| `package.json`、`package-lock.json` | 前端依赖与构建脚本 |
| `vite.config.mjs`、`index.html` | Vite 构建配置和入口 |
| `implementation-1440x1024.png` | 当前 UI 实现截图 |

附件未包含 `node_modules`、构建缓存和 `dist`，可通过 `npm install && npm run dev` 重新生成运行环境。

## 10. UI 设计 PDF

| 文件 | 内容 |
|---|---|
| `archive/ui-design-v2/Docs/FurniScope_完整UI页面设计稿_V1.pdf` | 完整 UI 页面设计稿原文件 |
| `Docs/参赛提交/FurniScope_Full_UI_Design_V1.pdf` | 同一设计稿的英文文件名兼容副本，防止部分解压工具显示中文乱码 |

两份 PDF 内容相同，并非两套不同方案。

## 11. 未包含内容与原因

- `.env`、API Key、JWT 私钥、Token、密码：敏感环境信息，禁止提交。
- `.venv`、`node_modules`、`dist`、缓存和日志：可再生成的本地环境或构建产物。
- 企业未授权原始数据、评论全文和数据库备份：遵循数据安全和最小披露原则。
- 真实平台经营结论：当前 Demo 数据均应标记为 `demo_synthetic` 或 `demo_only`。

## 12. 当前范围说明

附件证明的是当前设计与开发基线，不表示全部路线图均已完成。数据库、Agent 主链、FastAPI 基础设施、认证、幂等、产品、数据集和分析任务 API 已完成；统一确认 HTTP API、洞察下钻、报告详情和完整前后端联调仍属于后续增量开发范围。
