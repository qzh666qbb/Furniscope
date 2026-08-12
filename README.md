<div align="center">

# FurniScope

### 跨境家具超级 AI 员工

**把市场声音、竞品证据与工厂制造能力，变成可执行的产品决策。**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.1-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/Agent-LangGraph-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-V3-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=111)](https://react.dev/)
[![Alibaba Cloud](https://img.shields.io/badge/AI-阿里云百炼-FF6A00)](https://www.aliyun.com/product/bailian)

[产品概览](#-为什么是-furniscope) · [核心能力](#-核心能力) · [系统架构](#-系统架构) · [快速开始](#-快速开始) · [项目进度](#-当前实现状态) · [设计文档](#-设计与开发文档)

</div>

![FurniScope 产品界面](assets/furniscope-dashboard.png)

> FurniScope 不是一个只会生成市场摘要的聊天机器人。它是一名由 `user` 直接驾驭的跨境家具超级 AI 员工：理解产品与工厂能力，自主研究市场、筛选竞品、挖掘评论、判断机会，并给出带证据、置信度和制造适配结论的决策报告。

## ✨ 为什么是 FurniScope

传统跨境家具企业通常已经拥有图片、规格、报价、样品和客户反馈，却缺少把这些碎片持续转化为产品决策的数据团队。通用 AI 能总结文字，但很难回答制造企业真正关心的问题：

- 这个市场机会是否真实，证据来自哪里？
- 评论里的痛点是偶发现象，还是稳定需求？
- 市场想要的尺寸、材料和结构，我们能不能做？
- 即使能做，成本、包装、认证和利润是否匹配？
- 哪些结论可以行动，哪些仍需打样或补充数据？

FurniScope 将市场、产品、数据和工程分析封装成一个自主工作流。一个用户即可完成从资料输入到决策报告的闭环；仅在事实冲突、样本不足、低置信度或高风险建议时，AI 才请求用户确认。

## 🚀 核心能力

| 能力 | FurniScope 做什么 | 产出 |
|---|---|---|
| 产品与企业理解 | 解析家具图片、规格、尺寸、材料、结构、成本及包装约束 | 版本化产品画像与企业能力边界 |
| 可比竞品发现 | 按品类、价格带、尺寸、材质、场景计算可比性 | 可解释竞品集合与匹配证据 |
| 评论深度挖掘 | 提取舒适度、耐用性、安装、气味、尺寸、包装等需求与痛点 | 需求簇、情绪、原文 Evidence Span |
| 市场机会判断 | 融合需求热度、竞争缺口、利润、趋势、证据质量与制造适配 | 六维机会评分、置信度和推荐级别 |
| 工程化产品建议 | 将市场机会映射到材料、尺寸、结构、包装和认证动作 | 可执行建议、风险与待验证事项 |
| 可信决策报告 | 审计结论、反证、数据范围、模型和 Prompt 版本 | 在线综合报告与完整证据索引 |

### 五阶段用户体验

```mermaid
flowchart LR
    A[理解产品<br/>understanding_product] --> B[研究市场<br/>researching_market]
    B --> C[评估机会<br/>evaluating_opportunity]
    C --> D[生成建议<br/>generating_recommendation]
    D --> E[完成报告<br/>completed]
    B -. 仅在必要时 .-> F{user_confirmation}
    C -. 事实冲突 / 样本不足<br/>低置信度 / 高风险 .-> F
    F -->|LangGraph Command resume| B
    F -->|LangGraph Command resume| C
```

## 🧠 一个 AI 员工，多个内部专业能力

用户不会选择 Agent，也不会把任务分配给“市场岗”或“产品岗”。LangGraph 在系统内部编排产品理解、数据质量、竞品发现、评论分析、机会评分、工程建议、证据审计和报告生成能力，并提供：

- 细粒度节点幂等，成功结果可复用，避免重复调用和重复计费；
- 并行 Map/Reduce，缩短竞品和评论批处理耗时；
- 自动重试、降级与部分失败，不用把运维复杂度暴露给用户；
- `user_confirmation` 统一人工中断；
- LangGraph Checkpoint + Outbox 安全恢复；
- 模型、Prompt、Token、耗时、Schema 和证据全链路追溯。

## 🏗 系统架构

```mermaid
flowchart TB
    subgraph Client[应用层]
        Web[React 19 + Vite]
        Admin[Admin 管理后台]
    end

    subgraph API[FastAPI 服务层]
        Gateway[RESTful API / Unified Envelope]
        Auth[RS256 Auth / user & admin]
        Services[Service + Repository]
        Idem[持久化幂等 / Tenant Context]
    end

    subgraph Agent[LangGraph 超级 AI 员工]
        Supervisor[Workflow Supervisor]
        Workers[产品 · 市场 · 评论 · 机会 · 工程 · 报告]
        Confirm[user_confirmation]
        Checkpoint[Checkpoint / Outbox / Safe Resume]
    end

    subgraph Models[阿里云百炼 Model Router]
        QwenVL[Qwen VL]
        Qwen[Qwen Reasoning]
        Embed[text-embedding-v4]
        Rerank[qwen3-rerank]
    end

    subgraph Data[数据层]
        PG[(PostgreSQL V3)]
        Redis[(Redis)]
        OSS[(Object Storage)]
    end

    Sources[授权商品 / 评论 / 企业资料] --> Gateway
    Web --> Gateway
    Admin --> Gateway
    Gateway --> Auth --> Services --> Supervisor
    Services --> Idem
    Supervisor --> Workers
    Workers --> Confirm --> Checkpoint --> Supervisor
    Workers --> Models
    Services --> PG
    Checkpoint --> PG
    Supervisor --> Redis
    Services --> OSS
```

## 🧩 技术栈

- **Frontend:** React 19, Vite 6, Phosphor Icons
- **Backend:** Python 3.12, FastAPI, Pydantic, SQLAlchemy Async
- **Agent:** LangGraph, PostgreSQL Checkpointer, `Command(resume=...)`
- **Database:** PostgreSQL, asyncpg, psycopg
- **AI:** 阿里云百炼 Model Router、Qwen VL、Qwen 推理模型、`text-embedding-v4`、`qwen3-rerank`
- **Security:** RS256 JWT、Refresh Token 轮换、租户隔离、日志脱敏、持久化幂等
- **Testing:** Pytest, Pytest Asyncio, Node Test Runner

## ⚡ 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/qzh666qbb/Furniscope.git
cd Furniscope
```

### 2. 初始化 Python 环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少配置本地 PostgreSQL 连接。密钥只能放在本地环境变量或密钥管理服务中，不得提交。

### 3. 初始化 PostgreSQL V3

```bash
createdb furniscope
psql -d furniscope -v ON_ERROR_STOP=1 -f furniscope_postgresql_v3.sql
```

将 `.env` 中的连接串调整为你的本地账号：

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/furniscope
LANGGRAPH_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/furniscope
```

数据库不会创建默认密码或硬编码密钥。完整初始化和迁移说明见 [`Docs/项目设计文档`](Docs/项目设计文档)。

### 4. 启动 FastAPI

```bash
PYTHONPATH=backend uvicorn furniscope_api.app:app --host 127.0.0.1 --port 8000 --reload
```

打开：

- OpenAPI：<http://127.0.0.1:8000/docs>
- Liveness：<http://127.0.0.1:8000/health/live>
- Readiness：<http://127.0.0.1:8000/health/ready>

### 5. 启动 React 前端

```bash
cd UI_Desgin/UI_V2/product-design-plugin-product-design-openai/furniscope-app
npm install
npm run dev
```

Vite 默认地址：<http://127.0.0.1:5173>

### 6. 运行测试

```bash
source .venv/bin/activate
PYTHONPATH=.:backend pytest -q
```

数据库集成测试必须连接真实 PostgreSQL，项目不以 skipped 测试作为“已完成”证明。

如需运行完整数据库集成测试，请提供隔离的测试库连接：

```bash
FURNISCOPE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/furniscope_test \
  PYTHONPATH=.:backend pytest -q
```

## ✅ 当前实现状态

| 模块 | 状态 | 说明 |
|---|---:|---|
| PostgreSQL V3 DDL 与迁移 | ✅ | 主外键、CHECK、索引、JSONB 约束与校验 SQL |
| LangGraph 核心工作流 | ✅ | 正常链路、部分失败、降级、中断、Outbox 与恢复 |
| FastAPI 基础设施 | ✅ | Envelope、错误码、请求 ID、日志脱敏与健康检查 |
| 认证与通用幂等 | ✅ | RS256、Refresh 轮换/重放检测、持久化幂等 |
| 产品与市场数据集 API | ✅ | 租户隔离、解析任务、数据集状态与版本控制 |
| 分析任务与 Agent API | ✅ | 创建、启动、五阶段状态和结果聚合 |
| React 完整 UI 设计 | ✅ | 6 个用户页面 + 1 个 Admin 页面视觉与交互方案 |
| `user_confirmation` HTTP API | 🚧 | Agent/数据库底座已完成，业务接口待接入 |
| 洞察、报告详情与前端联调 | 🗓️ | 后续增量迭代 |

最近一次真实 PostgreSQL 全量验证记录：**30 passed，0 skipped**。详见 [`Docs/开发文档/FurniScope_分析任务与Agent_API实现说明V1.md`](Docs/开发文档/FurniScope_分析任务与Agent_API实现说明V1.md)。

> Demo 阶段允许使用结构符合业务口径的合成家具数据，但必须标记为 `demo_synthetic` 或 `demo_only`，不得伪装成真实平台统计。

## 🔐 可信 AI 与安全边界

1. 数值、比例、价格和机会评分由确定性程序计算，不允许生成模型编造。
2. 报告结论关联原始 Evidence Span、反证、数据范围和置信度。
3. 所有业务查询由服务端注入 `tenant_id`，客户端不能提交或覆盖租户范围。
4. API Key、密码、Token、私钥和评论全文不得进入日志或代码常量。
5. Checkpoint 只负责工作流恢复，不能覆盖已提交业务事实。
6. FurniScope 提供决策支持，不替代用户作出开模、认证、备料或备货决定。

## 📚 设计与开发文档

项目不是“先写 Demo、后补文档”，而是由可追踪的业务契约驱动实现：

| 文档 | 作用 |
|---|---|
| [PRD / SRS V2](Docs/项目设计文档/01_产品需求规格说明书SRS_PRD_V2.md) | 产品范围、用户角色、核心闭环与验收标准 |
| [数据字典 V3](Docs/项目设计文档/02_FurniScope产品数据字典V3.md) | 实体、字段、类型、枚举和约束 |
| [PostgreSQL V3](Docs/项目设计文档/03_FurniScope_PostgreSQL数据库设计V3.md) | 表结构、主外键、CHECK 与索引 |
| [Agent 工作流 V2](Docs/项目设计文档/04_FurniScope_Agent工作流设计V2.md) | State、节点、重试、中断和恢复语义 |
| [RESTful API V3](Docs/项目设计文档/05_FurniScope_RESTful_API接口设计V3.md) | HTTP 契约、权限和业务错误码 |
| [系统 Mermaid 图 V3](Docs/项目设计文档/06_FurniScope_Mermaid图V3.md) | 业务、架构、ER、页面与节点映射 |
| [页面原型 V3](Docs/项目设计文档/07_FurniScope页面交互原型说明V3.md) | 6+1 页面交互与 API 绑定 |
| [测试用例 V2](Docs/项目设计文档/09_FurniScope测试用例V2.md) | P0 验收、异常分支与数据库断言 |

完整索引见 [`Docs/项目设计文档/00_项目设计文档索引.md`](Docs/项目设计文档/00_项目设计文档索引.md)。

## 🗺 Roadmap

- [x] 数据库 V3 与 LangGraph Agent 核心引擎
- [x] FastAPI 基础设施、认证、幂等、产品、数据集与任务接口
- [x] React 19 完整 UI 原型
- [ ] 接入 `user_confirmation` 查询、回答与恢复 API
- [ ] 完成洞察下钻、报告详情及 Dashboard API
- [ ] 前后端真实 API 联调与端到端演示
- [ ] 接入赛事账号的阿里云百炼 Model Router 并完成效果评测

## 🤝 参与项目

欢迎通过 Issue 提交家具业务场景、数据质量问题、模型评测建议或工程改进。提交代码前请确保：

- 没有引入新的用户角色或跨部门审批语义；
- 没有提交密钥、企业原始敏感数据或未授权平台数据；
- 新增字段、状态和 API 已与当前有效设计文档对齐；
- PostgreSQL 集成测试真实执行且无 skipped。

---

<div align="center">

**FurniScope — From market signals to manufacturable decisions.**

Built for the AI Cross-border Hackathon · Track 3: AI Market Intelligence

</div>
