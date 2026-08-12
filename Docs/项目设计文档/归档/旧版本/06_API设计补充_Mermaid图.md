# FurniScope 项目 Mermaid 图 V2.0

## 0. 文档基线与图例

本文件按“有 V2 使用 V2、无 V2 保持 V1”的规则更新：

| 设计域 | 使用版本 |
|---|---|
| 产品需求规格说明书 | PRD V1 |
| 产品数据字典 | 数据字典 V2 |
| 数据库表结构 | PostgreSQL 数据库设计 V2 |
| Agent 工作流 | Agent 工作流 V1 |
| RESTful API | API V2 |
| 页面交互原型 | 交互原型 V2 |

图中 `P0` 表示本次 Demo 主链路；`P1` 表示【P1迭代功能，本次Demo暂不实现】。业务数据、工作流投影与 LangGraph 官方 Checkpoint 均采用 PostgreSQL；Redis 仅承担缓存、运行锁和短期队列状态。

---

## 1. 业务流程图

```mermaid
flowchart TD
    A([开始]) --> B[P01 用户登录<br/>API-USR-01]
    B --> C[P02 加载工作台摘要<br/>API-DSH-01 P0]
    C --> D[P03 创建或选择家具产品<br/>API-PRD-01/02]
    D --> E[P04 上传图片、参数表、PDF与报价<br/>API-PRD-05]
    E --> F[轮询产品解析任务<br/>API-PRD-07 P0]
    F --> G{解析状态}
    G -- queued/running --> F
    G -- failed且retryable --> E
    G -- succeeded/partial_succeeded --> H[查看并修正标准化产品画像<br/>API-PRD-03/04]
    H --> I{画像是否完整且冲突已处理}
    I -- 否 --> H
    I -- 是 --> J[确认产品画像版本<br/>API-PRD-06]

    J --> K[P05 创建市场数据集<br/>API-CMP-01]
    K --> L[预校验并导入竞品与评论文件<br/>API-CMP-02]
    L --> M[查看数据质量结果<br/>API-CMP-03]
    M --> N{达到分析门槛}
    N -- 否 --> O[修复数据或确认探索性分析限制]
    O --> L
    N -- 是 --> P[P06 创建并启动分析任务<br/>API-INS-01/02]

    P --> Q[P07 轮询任务状态<br/>API-INS-03 P0]
    Q --> R[Agent预检、上下文加载和数据质量检查]
    R --> S[竞品硬过滤、Embedding召回、Rerank]
    S --> T{status=waiting_human<br/>checkpoint_stage=competitor_review}
    T -- 否 --> Q
    T -- 是 --> U[P08 查询竞品匹配集合<br/>API-CMP-06 P0]
    U --> V[人工纳入、排除或改类<br/>API-CMP-05]
    V --> W[确认竞品集合<br/>API-CMP-07 P0]
    W --> X{版本和Checkpoint是否一致}
    X -- 冲突409 --> U
    X -- 一致202 --> Y[持久化确认与控制事件<br/>LangGraph Command resume]
    Y --> Q

    Q --> Z[评论清洗与并行观点抽取]
    Z --> AA[需求聚类、市场统计并行计算]
    AA --> AB[机会评分、企业适配与产品建议]
    AB --> AC{需要专家复核}
    AC -- 是 --> AD[P11 专家复核建议<br/>API-INS-06]
    AD --> AE[从expert_review Checkpoint恢复]
    AC -- 否 --> AF[证据审计与报告生成]
    AE --> AF
    AF --> AG[最终结果与report_uuid入库]
    AG --> AH{API-INS-03任务状态}
    AH -- running/partial_succeeded --> Q
    AH -- failed --> AI[显示stage_runs与partial_failures]
    AI -. 手动阶段重试 API-INS-07 P1 .-> Q
    AH -- succeeded且report_uuid非空 --> AJ[P10 查看机会报告<br/>API-RPT-01]
    AJ --> AK[发布报告<br/>API-RPT-02]

    AK -. 创建并查询报告导出<br/>API-RPT-03/04 P1 .-> AL[P13 下载短效文件]
    AL -. 查询导出历史<br/>API-RPT-05 P1 .-> AM([结束])
    AJ -. 生成、查询、审核Listing<br/>API-LST-01/02/03 P1 .-> AN[P12 Listing编辑预览]
    AN --> AM
    AI -. 协作式取消 API-INS-08 P1 .-> AM
    AK --> AM
```

### 1.1 任务进度轮询与恢复时序图

```mermaid
sequenceDiagram
    autonumber
    participant UI as P07/P08 前端
    participant API as API V2
    participant DB as PostgreSQL业务事务
    participant LG as LangGraph工作流
    participant WK as 异步Worker

    UI->>API: GET /api/v1/analysis-tasks/{task_uuid}
    API->>DB: 查询任务、stage_runs、部分失败与报告
    DB-->>API: status/current_stage/checkpoint_stage/report_uuid
    API-->>UI: API-INS-03响应

    alt queued/running/partial_succeeded
        UI->>UI: 前台每2秒轮询，后台降频至10秒
    else waiting_human且competitor_review
        UI->>API: GET .../{task_uuid}/competitors (API-CMP-06)
        API-->>UI: competitor_set_version + checkpoint_stage
        UI->>API: POST .../{task_uuid}/competitors:confirm (API-CMP-07)
        API->>DB: 同一事务写确认快照与Outbox控制事件
        DB-->>API: 提交成功
        API->>LG: Command(resume=人工确认)
        LG->>WK: 从安全Checkpoint重新入队
        API-->>UI: 202 + resume_command_id
        UI->>API: 立即恢复API-INS-03轮询
    else succeeded/failed/cancelled
        UI->>UI: 停止自动轮询
    end
```

---

## 2. 系统整体架构图

```mermaid
flowchart TB
    subgraph EXT[外部数据源层]
        E1[企业产品资料<br/>图片、PDF、参数表、报价]
        E2[授权竞品商品数据<br/>CSV/XLSX/JSON]
        E3[授权评论数据<br/>CSV/XLSX/JSON]
        E4[企业制造能力<br/>材料、工艺、MOQ、交期、认证]
        E5[阿里云百炼<br/>Model Router与模型服务]
    end

    subgraph FE[前端应用层 Web]
        F1[P01 登录]
        F2[P02 工作台]
        F3[P03-P04 产品库与画像]
        F4[P05-P06 数据集与任务创建]
        F5[P07 任务运行详情]
        F6[P08 竞品监控看板]
        F7[P09 评论分析]
        F8[P10-P11 报告与建议复核]
        F9[P12 Listing P1]
        F10[P13 报告导出 P1]
    end

    subgraph ACCESS[接入与安全层]
        G1[RESTful API v1]
        G2[JWT与刷新令牌]
        G3[RBAC与租户隔离]
        G4[幂等键、If-Match、限流与Request ID]
    end

    subgraph BE[后端服务层]
        B1[用户与工作台服务<br/>API-USR/API-DSH]
        B2[产品与解析任务服务<br/>API-PRD]
        B3[数据集与竞品服务<br/>API-CMP]
        B4[评论分析服务<br/>API-REV]
        B5[市场洞察与建议服务<br/>API-INS]
        B6[报告服务<br/>API-RPT]
        B7[Listing服务 P1<br/>API-LST]
        B8[证据链与审计服务]
        B9[Workflow Supervisor]
        B10[Outbox控制事件<br/>确认、恢复、重试、取消]
    end

    subgraph AGENT[Agent编排层 LangGraph]
        A1[任务预检与上下文Agent]
        A2[竞品匹配Agent]
        A3[评论洞察Agent]
        A4[市场分析Agent]
        A5[机会评分Agent]
        A6[产品建议Agent]
        A7[证据审计与报告Agent]
        A8[人工中断 interrupt]
        A9[Checkpoint与Command resume]
        A10[并行Map Reduce与局部失败汇聚]
    end

    subgraph MODEL[AI模型调用层]
        M1[FurniScope Model Router封装<br/>API-MDL内部接口]
        M2[Prompt与Schema版本]
        M3[路由、超时、有界重试与降级]
        M4[多模态理解]
        M5[结构化抽取与推理]
        M6[Embedding]
        M7[Rerank]
        M8[内容安全、成本与延迟统计]
    end

    subgraph DATA[数据存储层]
        D1[(PostgreSQL 16<br/>V2业务与分析表)]
        D2[(PostgreSQL工作流持久化<br/>官方Checkpoint + 业务投影<br/>部分失败与控制事件)]
        D3[(Redis<br/>缓存、运行锁、短期进度)]
        D4[(对象存储<br/>原始文件与短效导出文件)]
        D5[(向量存储<br/>产品、竞品、观点向量)]
        D6[异步任务队列]
        D7[日志、指标与告警]
    end

    E1 --> F3
    E2 --> F4
    E3 --> F4
    E4 --> B5
    F1 & F2 & F3 & F4 & F5 & F6 & F7 & F8 & F9 & F10 --> G1
    G1 --> G2 --> G3 --> G4
    G4 --> B1 & B2 & B3 & B4 & B5 & B6 & B7
    B2 & B3 & B4 & B5 & B6 --> B9
    B3 & B5 --> B10
    B9 --> A1
    A1 --> A2 --> A3
    A3 --> A10
    A10 --> A4 --> A5 --> A6 --> A7
    A2 --> A8
    A6 --> A8
    A8 --> A9 --> B10
    A1 & A2 & A3 & A4 & A5 & A6 & A7 --> M1
    M1 --> M2 --> M3
    M3 --> M4 & M5 & M6 & M7
    M1 --> M8
    M3 --> E5
    B1 & B2 & B3 & B4 & B5 & B6 & B7 & B8 --> D1
    B9 & B10 & A9 & A10 --> D2
    B9 --> D3
    B9 --> D6
    B2 & B6 --> D4
    A2 & A3 & M6 --> D5
    G4 & B9 & M8 --> D7
```

---

## 3. 数据库 ER 关系图（PostgreSQL V2 业务基线）

```mermaid
erDiagram
    TENANTS {
        bigint id PK
        varchar tenant_code UK
        varchar tenant_name
        varchar status
    }
    USERS {
        bigint id PK
        bigint tenant_id FK
        varchar email
        varchar user_name
        varchar role_code
        varchar status
    }
    ENTERPRISE_PROFILES {
        bigint id PK
        bigint tenant_id FK
        int profile_version
        varchar status
    }
    MANUFACTURING_CAPABILITIES {
        bigint id PK
        bigint tenant_id FK
        bigint enterprise_profile_id FK
        varchar capability_code
        json capability_value
    }
    PRODUCTS {
        bigint id PK
        bigint tenant_id FK
        varchar sku_code UK
        varchar product_name
        int profile_version
        varchar profile_status
        bigint created_by FK
    }
    PRODUCT_ATTRIBUTES {
        bigint id PK
        bigint product_id FK
        int profile_version
        varchar attribute_code
        json attribute_value
        decimal confidence
    }
    MARKET_DATASETS {
        bigint id PK
        bigint tenant_id FK
        varchar dataset_name
        int dataset_version
        varchar platform
        varchar status
        varchar quality_status
    }
    COMPETITOR_LISTINGS {
        bigint id PK
        bigint dataset_id FK
        varchar platform_listing_id
        decimal price
        decimal rating
        int review_count
    }
    COMPETITOR_REVIEWS {
        bigint id PK
        bigint dataset_id FK
        bigint listing_id FK
        varchar platform_review_id
        decimal rating
        mediumtext body_original
        boolean is_valid
    }
    ANALYSIS_TASKS {
        bigint id PK
        char task_uuid UK
        bigint tenant_id FK
        bigint product_id FK
        bigint dataset_id FK
        varchar status
        varchar current_stage
        decimal progress_percent
        varchar failure_code
    }
    TASK_STAGE_RUNS {
        bigint id PK
        bigint task_id FK
        varchar stage_code
        int attempt_no
        varchar idempotency_key UK
        varchar status
        varchar error_code
        boolean retryable
    }
    AI_MODEL_RUNS {
        bigint id PK
        bigint task_id FK
        bigint stage_run_id FK
        varchar provider
        varchar model_id
        varchar task_type
        int input_tokens
        int output_tokens
        varchar status
    }
    COMPETITOR_MATCHES {
        bigint id PK
        bigint task_id FK
        bigint listing_id FK
        varchar competitor_type
        decimal overall_score
        varchar review_status
        bigint reviewed_by FK
    }
    REVIEW_ASPECTS {
        bigint id PK
        bigint task_id FK
        bigint review_id FK
        varchar taxonomy_code
        varchar sentiment
        varchar evidence_quote
        decimal extraction_confidence
    }
    INSIGHT_CLUSTERS {
        bigint id PK
        bigint task_id FK
        int cluster_no
        varchar cluster_name
        decimal mention_rate
        decimal cluster_confidence
    }
    CLUSTER_MEMBERS {
        bigint cluster_id PK,FK
        bigint aspect_id PK,FK
        decimal distance
    }
    MARKET_OPPORTUNITIES {
        bigint id PK
        bigint task_id FK
        varchar opportunity_code
        decimal overall_score
        decimal confidence
        varchar recommendation_level
    }
    PRODUCT_RECOMMENDATIONS {
        bigint id PK
        bigint task_id FK
        bigint opportunity_id FK
        text recommended_action
        varchar priority
        varchar expert_review_status
    }
    EVIDENCE_LINKS {
        bigint id PK
        bigint task_id FK
        varchar claim_type
        bigint claim_id
        varchar evidence_type
        bigint evidence_id
    }
    ANALYSIS_REPORTS {
        bigint id PK
        char report_uuid UK
        bigint task_id FK
        int report_version
        varchar status
        decimal overall_opportunity_score
        decimal confidence
    }
    LISTING_GENERATION_RECORDS {
        bigint id PK
        bigint product_id FK
        bigint source_task_id FK
        varchar target_platform
        text generated_title
        varchar status
    }
    REPORT_EXPORTS {
        bigint id PK
        bigint report_id FK
        varchar export_format
        varchar file_url
        varchar status
        datetime expires_at
    }
    AUDIT_LOGS {
        bigint id PK
        bigint tenant_id FK
        bigint actor_id FK
        varchar action_code
        varchar resource_type
        bigint resource_id
    }

    TENANTS ||--o{ USERS : contains
    TENANTS ||--o{ ENTERPRISE_PROFILES : owns
    ENTERPRISE_PROFILES ||--o{ MANUFACTURING_CAPABILITIES : defines
    TENANTS ||--o{ PRODUCTS : owns
    USERS ||--o{ PRODUCTS : creates
    PRODUCTS ||--o{ PRODUCT_ATTRIBUTES : has
    TENANTS ||--o{ MARKET_DATASETS : owns
    MARKET_DATASETS ||--o{ COMPETITOR_LISTINGS : contains
    MARKET_DATASETS ||--o{ COMPETITOR_REVIEWS : contains
    COMPETITOR_LISTINGS ||--o{ COMPETITOR_REVIEWS : receives
    PRODUCTS ||--o{ ANALYSIS_TASKS : analyzed_by
    MARKET_DATASETS ||--o{ ANALYSIS_TASKS : supports
    ANALYSIS_TASKS ||--o{ TASK_STAGE_RUNS : contains
    ANALYSIS_TASKS ||--o{ AI_MODEL_RUNS : invokes
    TASK_STAGE_RUNS ||--o{ AI_MODEL_RUNS : records
    ANALYSIS_TASKS ||--o{ COMPETITOR_MATCHES : selects
    COMPETITOR_LISTINGS ||--o{ COMPETITOR_MATCHES : matched_as
    ANALYSIS_TASKS ||--o{ REVIEW_ASPECTS : extracts
    COMPETITOR_REVIEWS ||--o{ REVIEW_ASPECTS : produces
    ANALYSIS_TASKS ||--o{ INSIGHT_CLUSTERS : generates
    INSIGHT_CLUSTERS ||--o{ CLUSTER_MEMBERS : groups
    REVIEW_ASPECTS ||--o{ CLUSTER_MEMBERS : belongs_to
    ANALYSIS_TASKS ||--o{ MARKET_OPPORTUNITIES : identifies
    MARKET_OPPORTUNITIES ||--o{ PRODUCT_RECOMMENDATIONS : produces
    ANALYSIS_TASKS ||--o{ EVIDENCE_LINKS : traces
    ANALYSIS_TASKS ||--o{ ANALYSIS_REPORTS : generates
    ANALYSIS_TASKS ||--o{ LISTING_GENERATION_RECORDS : informs
    ANALYSIS_REPORTS ||--o{ REPORT_EXPORTS : exports
    USERS ||--o{ AUDIT_LOGS : performs
```

---

## 4. PostgreSQL V2 工作流持久化关系图

> LangGraph 官方 Checkpointer 内部表由 `checkpointer.setup()` 管理；下图中的业务投影表由 `furniscope_postgresql_v2.sql` 创建，用于 API 查询、恢复门禁和审计。

```mermaid
flowchart LR
    T[(analysis_tasks V1)]
    S[(task_stage_runs V1)]
    R[(analysis_reports V1)]

    P[(product_parse_jobs<br/>PostgreSQL V2)]
    PF[(product_parse_job_files<br/>PostgreSQL V2)]
    C[(workflow_checkpoints<br/>业务安全恢复点投影)]
    LC[(LangGraph官方Checkpoint表<br/>依赖包管理)]
    F[(workflow_partial_failures<br/>PostgreSQL V2)]
    E[(workflow_control_events<br/>事务Outbox)]
    CC[(competitor_set_confirmations<br/>PostgreSQL V2)]

    PRD7[API-PRD-07<br/>解析任务状态]
    INS3[API-INS-03<br/>stage_runs partial_failures<br/>checkpoint_stage retryable report_uuid]
    CMP7[API-CMP-07<br/>竞品确认与Checkpoint恢复]
    INS7[API-INS-07 P1<br/>阶段重试]
    INS8[API-INS-08 P1<br/>协作式取消]

    P --> PF
    PRD7 --> P
    T --> S
    T --> C
    T --> LC
    T --> F
    T --> E
    T --> CC
    T --> R
    INS3 --> T & S & C & F & R
    CMP7 --> CC & E & C
    INS7 --> E & C & S
    INS8 --> E & T
```

## 5. 版本记录

| 版本 | 变更说明 |
|---|---|
| V1.0 | 初版业务流程、系统架构与 MySQL ER 图 |
| V2.0 | 对齐 PostgreSQL 数据库 V2、数据字典 V2、API V2 与交互原型 V2；增加 API 编号、任务轮询、竞品确认 Checkpoint 恢复、P0/P1 边界、官方 Checkpointer 与业务投影分工 |
