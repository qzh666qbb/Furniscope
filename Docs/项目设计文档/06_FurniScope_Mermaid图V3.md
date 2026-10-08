# FurniScope Mermaid 图 V3.0

> 2026-10-05企业增量的完整结构图见[15总体设计§3](./15_FurniScope_企业决策与数据闭环总体设计V1.md#3-总体结构)：标准数据与模板/对账、独立初训/追加/重建、租约恢复与逐SKU滚动发布、企业策略与硬条件、处理反馈→实施/经营观察→完整任务数据→离线验收及RLS。以下图保留核心分析编排，机会评分为五因子市场分及独立企业修正，排序学习仍待真实数据实验。

## 1. 文档说明

本文提供 FurniScope“跨境家具超级 AI 员工”V3 的六张可直接复制渲染的 Mermaid 图。图中系统角色只包含 `user/admin`；多 Agent 是超级 AI 员工的内部能力，不是用户岗位。核心范围不包含 Listing 生成、文件导出、验证任务、多人报告评审和产品运营事件。

图示基线：PRD V2、Agent工作流 V2、产品数据字典 V3、PostgreSQL设计 V3、RESTful API V3、页面交互原型 V3。

## 2. 超级 AI 员工业务流程图

```mermaid
flowchart TD
    START([开始]) --> LOGIN[S01 登录<br/>user或admin]
    LOGIN --> ROLE{role_code}
    ROLE -->|admin| ADMIN[A01 管理user、模型、Prompt<br/>系统配置与运行诊断]
    ROLE -->|user| WORKSPACE[S02 AI工作台]
    ADMIN --> WORKSPACE

    WORKSPACE --> NEW[S03 新建分析向导]
    NEW --> GOAL[输入分析目标<br/>product_market_fit或product_improvement]
    GOAL --> PRODUCT[选择或创建家具产品<br/>上传图片、PDF与参数资料]
    PRODUCT --> PARSE[AI解析资料并生成产品画像]
    PARSE --> PROFILE{画像事实完整且冲突已处理}
    PROFILE -->|否| PRODUCT
    PROFILE -->|是| CAPABILITY[加载企业约束与制造能力<br/>未知项保持unknown]
    CAPABILITY --> DATA[选择或导入授权市场数据<br/>商品、评论与字段映射]
    DATA --> QUALITY{数据质量与范围可支撑分析}
    QUALITY -->|否| REPAIR[修正字段映射、授权或数据范围]
    REPAIR --> DATA
    QUALITY -->|是| CREATE[创建并启动分析任务<br/>冻结画像、数据、算法、Prompt与模型版本]

    CREATE --> RUN[S04 超级AI员工自主执行]
    RUN --> STEP1[理解产品<br/>understanding_product]
    STEP1 --> STEP2[研究市场<br/>researching_market]
    STEP2 --> STEP3[评估机会<br/>evaluating_opportunity]
    STEP3 --> STEP4[生成建议<br/>generating_recommendation]

    STEP1 --> GATE{是否满足统一确认触发条件}
    STEP2 --> GATE
    STEP3 --> GATE
    STEP4 --> GATE
    GATE -->|事实冲突、样本不足<br/>低置信度或高风险| CONFIRM[user_confirmation卡片<br/>问题、选项、证据与影响]
    CONFIRM --> ANSWER[user提交选择和必要事实]
    ANSWER --> RESUME[校验安全Checkpoint<br/>Command resume恢复]
    RESUME --> RUN
    GATE -->|否| AUTO[Supervisor自动重试、降级<br/>并行汇聚与部分失败处理]
    AUTO --> AUDIT[证据审计<br/>无证据结论删除或降级]
    AUDIT --> REPORT[生成在线综合决策报告]
    REPORT --> PERSIST[最终事务入库]
    PERSIST --> DONE[completed]

    DONE --> INSIGHT[S05 市场洞察<br/>竞品、评论、价格、机会、证据]
    DONE --> DECISION[S06 决策报告<br/>结论、置信度、建议、制造适配与风险]
    INSIGHT --> DECISION
    DECISION --> END([user获得可追溯决策依据])

    RUN -. 技术详情 .-> TECH[stage_runs、partial_failures<br/>checkpoint_stage与request_id]
    TECH -. admin脱敏诊断 .-> ADMIN
```

## 3. user_confirmation 与 LangGraph Checkpoint 恢复时序图

```mermaid
sequenceDiagram
    autonumber
    actor U as user
    participant FE as S04前端
    participant API as FastAPI
    participant DB as PostgreSQL业务库
    participant CP as LangGraph官方Checkpointer
    participant LG as LangGraph Supervisor
    participant OB as workflow_control_events Outbox
    participant WK as Worker

    LG->>LG: 检测事实冲突、样本不足、低置信度或高风险
    LG->>CP: 保存官方Graph State Checkpoint
    CP-->>LG: checkpoint_id和thread_id
    LG->>DB: 同一业务边界写safe workflow_checkpoints投影
    LG->>DB: 写user_confirmations status=pending
    LG->>DB: 更新analysis_tasks status=waiting_human
    LG->>DB: 写task_stage_runs status=waiting_human
    LG->>LG: interrupt(user_confirmation)

    FE->>API: GET /api/v1/analysis-tasks/{task_uuid}
    API->>DB: 查询五阶段、pending确认和安全恢复投影
    DB-->>API: user_confirmation九字段和checkpoint_stage
    API-->>FE: API-INS-03 status=waiting_human
    FE-->>U: 展示单一确认问题、推荐项、证据与影响

    U->>FE: 选择selected_option并填写必要user_input
    FE->>API: POST /api/v1/user-confirmations/{confirmation_id}:respond
    Note over FE,API: Authorization和Idempotency-Key
    API->>DB: 锁定同租户pending confirmation
    API->>DB: 校验未过期、选项合法、回答者为user
    API->>DB: 校验同任务safe checkpoint_id与checkpoint_stage

    alt 校验失败
        DB-->>API: 回滚事务
        API-->>FE: 409或422稳定业务错误码
        FE-->>U: 保留输入并刷新确认，不静默重放
    else 校验成功
        API->>DB: 写selected_option、user_input、responded_by和responded_at
        API->>OB: 同事务写resume_confirmation事件
        DB-->>API: 提交成功
        API-->>FE: 202 accepted=true和resumed_from_checkpoint=true
        FE-->>U: 回答已接收，AI正从安全进度继续

        WK->>OB: 获取pending控制事件并加幂等锁
        OB-->>WK: confirmation_id和checkpoint_id
        WK->>CP: 按thread_id读取官方Graph State
        CP-->>WK: 已持久化State
        WK->>LG: Command(resume={confirmation_id,selected_option,user_input})
        LG->>LG: 从checkpoint_stage后的合法边继续
        LG->>DB: analysis_tasks status=running
        LG->>DB: 新建task_stage_runs attempt
        WK->>OB: 标记事件consumed
        FE->>API: 继续轮询API-INS-03
        API-->>FE: 五阶段进度或completed
    end

    Note over DB,CP: workflow_checkpoints是业务安全投影<br/>官方Checkpointer保存完整Graph State，职责不可互换
    Note over U,LG: admin不能代替user提交业务确认
```

## 4. 系统整体架构图

```mermaid
flowchart TB
    subgraph USERS[使用者]
        U[user<br/>完成全部业务操作]
        A[admin<br/>用户、配置与诊断]
    end

    subgraph FE[前端应用层 React 19 + Vite]
        S01[S01 登录]
        S02[S02 AI工作台]
        S03[S03 新建分析]
        S04[S04 AI执行中]
        S05[S05 市场洞察]
        S06[S06 决策报告]
        A01[A01 管理后台]
        UIX[五阶段Stepper<br/>Confirmation卡片<br/>证据与技术详情抽屉]
    end

    subgraph API[FastAPI服务层]
        GW[REST API v1<br/>JWT、Request ID、限流]
        AUTH[认证与user/admin静态鉴权]
        TENANT[tenant_id、受限角色、RLS与审计]
        PRODUCT[产品、文件与画像服务]
        DATASET[市场数据集与质量服务]
        TASK[分析任务与综合结果服务]
        CONFIRM[统一user_confirmation服务]
        INSIGHT[竞品、评论、机会与证据服务]
        REPORT[在线报告服务]
        ADMIN[admin配置与脱敏诊断服务]
        OUTBOX[事务Outbox服务]
    end

    subgraph AGENT[LangGraph Agent层 超级AI员工内部能力]
        SUP[Workflow Supervisor<br/>路由、幂等、自动重试与降级]
        CONTEXT[产品与企业能力理解]
        COMP[竞品过滤、Embedding与Rerank]
        REVIEW[评论预处理与观点Map Reduce]
        ANALYTICS[价格、趋势与企业适配并行分析]
        SCORE[五因子市场分、企业修正<br/>硬条件与独立置信度]
        RECOMMEND[家具产品与制造建议]
        EVIDENCE[证据审计与报告生成]
        INTERRUPT[统一interrupt和Command resume]
        CHECKPOINT[LangGraph官方Checkpointer适配]
    end

    subgraph MODEL[Model Router层]
        MR[FurniScope Model Router封装]
        ROUTE[admin配置的主备模型路由]
        PROMPT[Prompt、Schema与版本束]
        VISION[多模态理解]
        REASON[结构化抽取与复杂推理]
        EMBED[Embedding]
        RERANK[Rerank]
        TRACE[Token、成本、延迟、重试与Schema追踪]
        BAILIAN[阿里云百炼Model Router API]
    end

    subgraph STORAGE[数据存储层]
        PG[(PostgreSQL 16<br/>V3业务表、证据、模型追溯<br/>Checkpoint业务投影与Outbox)]
        PGCP[(PostgreSQL独立Schema<br/>LangGraph官方Checkpointer)]
        REDIS[(Redis<br/>运行锁、缓存、短期状态与限流)]
        OBJ[(对象存储<br/>产品图片、PDF和授权导入文件)]
        VECTOR[(向量索引<br/>产品、竞品与评论观点)]
        OBS[(日志、指标与告警<br/>脱敏request_id链路)]
    end

    subgraph EXT[外部数据源层]
        E1[企业产品资料<br/>图片、PDF、参数表]
        E2[企业档案与制造能力<br/>约束、工艺、MOQ、交期、认证]
        E3[授权市场商品数据<br/>企业导出、持牌供应商或Demo合成]
        E4[授权评论数据]
        E5[家具本体、规则与可信知识库]
    end

    U --> S01
    A --> S01
    U --> S02
    U --> S03
    U --> S04
    U --> S05
    U --> S06
    A --> A01
    S01 --> UIX
    S02 --> UIX
    S03 --> UIX
    S04 --> UIX
    S05 --> UIX
    S06 --> UIX
    A01 --> UIX
    UIX --> GW
    GW --> AUTH --> TENANT
    TENANT --> PRODUCT
    TENANT --> DATASET
    TENANT --> TASK
    TENANT --> CONFIRM
    TENANT --> INSIGHT
    TENANT --> REPORT
    TENANT --> ADMIN
    TASK --> OUTBOX
    CONFIRM --> OUTBOX

    PRODUCT --> SUP
    DATASET --> SUP
    TASK --> SUP
    OUTBOX --> SUP
    SUP --> CONTEXT --> COMP --> REVIEW --> ANALYTICS --> SCORE --> RECOMMEND --> EVIDENCE
    SUP --> INTERRUPT
    INTERRUPT --> CHECKPOINT

    CONTEXT --> MR
    COMP --> MR
    REVIEW --> MR
    ANALYTICS --> MR
    SCORE --> MR
    RECOMMEND --> MR
    EVIDENCE --> MR
    MR --> ROUTE --> PROMPT
    PROMPT --> VISION
    PROMPT --> REASON
    PROMPT --> EMBED
    PROMPT --> RERANK
    VISION --> BAILIAN
    REASON --> BAILIAN
    EMBED --> BAILIAN
    RERANK --> BAILIAN
    MR --> TRACE

    PRODUCT --> PG
    DATASET --> PG
    TASK --> PG
    CONFIRM --> PG
    INSIGHT --> PG
    REPORT --> PG
    ADMIN --> PG
    OUTBOX --> PG
    CHECKPOINT --> PGCP
    SUP --> REDIS
    PRODUCT --> OBJ
    DATASET --> OBJ
    COMP --> VECTOR
    REVIEW --> VECTOR
    GW --> OBS
    SUP --> OBS
    MR --> OBS

    E1 --> OBJ
    E2 --> PG
    E3 --> DATASET
    E4 --> DATASET
    E5 --> CONTEXT
    E5 --> SCORE
    E5 --> RECOMMEND
```

## 5. PostgreSQL V3 ER 图

```mermaid
erDiagram
    tenants {
        bigint id PK
        uuid tenant_uuid UK
        string tenant_code UK
        string name
        string status
    }
    users {
        bigint id PK
        bigint tenant_id FK
        string email
        string role_code
        string status
    }
    auth_sessions {
        bigint id PK
        uuid session_uuid UK
        bigint tenant_id FK
        bigint user_id FK
        string refresh_token_hash UK
        uuid token_family_uuid
        datetime expires_at
        datetime revoked_at
    }
    api_idempotency_records {
        bigint id PK
        bigint tenant_id FK
        bigint actor_user_id FK
        string route_code
        string idempotency_key
        string request_hash
        string status
        datetime expires_at
    }
    enterprise_profiles {
        bigint id PK
        bigint tenant_id FK
        json constraints
    }
    manufacturing_capabilities {
        bigint id PK
        bigint tenant_id FK
        string capability_code
        decimal confidence
    }
    products {
        bigint id PK
        bigint tenant_id FK
        string sku
        string name
        bigint current_profile_version_id FK
    }
    file_assets {
        bigint id PK
        bigint tenant_id FK
        string object_key
        string sha256
    }
    product_parse_jobs {
        bigint id PK
        uuid parse_job_id UK
        bigint tenant_id FK
        bigint product_id FK
        string status
    }
    product_parse_job_files {
        bigint id PK
        bigint parse_job_id FK
        bigint file_asset_id FK
        string parse_status
    }
    product_profile_versions {
        bigint id PK
        bigint tenant_id FK
        bigint product_id FK
        int version_no
        string status
    }
    product_attributes {
        bigint id PK
        bigint tenant_id FK
        bigint profile_version_id FK
        string attribute_code
        decimal confidence
    }
    market_datasets {
        bigint id PK
        bigint tenant_id FK
        string name
        string platform
        string market_country
        json field_mapping
    }
    market_listings {
        bigint id PK
        bigint tenant_id FK
        bigint dataset_id FK
        string platform_listing_id
        json normalized_attributes
    }
    reviews {
        bigint id PK
        bigint tenant_id FK
        bigint dataset_id FK
        bigint listing_id FK
        text content_original
    }
    prompt_templates {
        bigint id PK
        string code
        string version
        string task_type
        string status
    }
    model_route_configs {
        bigint id PK
        string task_type UK
        string primary_model_id
        json compute_config
    }
    analysis_tasks {
        bigint id PK
        uuid task_uuid UK
        bigint tenant_id FK
        bigint product_id FK
        bigint product_profile_version_id FK
        bigint dataset_id FK
        string status
        string external_stage
    }
    task_stage_runs {
        bigint id PK
        bigint task_id FK
        string stage_code
        int attempt_no
        string status
    }
    workflow_checkpoints {
        string checkpoint_id PK
        bigint tenant_id FK
        bigint task_id FK
        string stage_code
        boolean is_safe_resume
    }
    workflow_partial_failures {
        bigint id PK
        bigint tenant_id FK
        bigint task_id FK
        string unit_type
        boolean retryable
    }
    user_confirmations {
        bigint id PK
        uuid confirmation_id UK
        bigint tenant_id FK
        bigint task_id FK
        string checkpoint_id FK
        string confirmation_type
        string status
    }
    workflow_control_events {
        bigint id PK
        uuid event_uuid UK
        bigint tenant_id FK
        bigint task_id FK
        string checkpoint_id FK
        string event_type
        string status
    }
    ai_model_runs {
        bigint id PK
        bigint task_id FK
        bigint stage_run_id FK
        string model_id
        string prompt_version
        string status
    }
    competitor_matches {
        bigint id PK
        bigint task_id FK
        bigint listing_id FK
        bigint product_profile_version_id FK
        string competitor_type
        decimal overall_score
    }
    review_aspects {
        bigint id PK
        bigint task_id FK
        bigint review_id FK
        bigint model_run_id FK
        string taxonomy_code
        string evidence_quote
    }
    insight_clusters {
        bigint id PK
        bigint task_id FK
        int cluster_no
        decimal cluster_confidence
    }
    cluster_members {
        bigint cluster_id PK,FK
        bigint aspect_id PK,FK
        decimal distance
    }
    market_metrics {
        bigint id PK
        bigint task_id FK
        string metric_type
        decimal confidence
    }
    price_bands {
        bigint id PK
        bigint task_id FK
        string band_code
        string currency
    }
    market_opportunities {
        bigint id PK
        bigint task_id FK
        bigint price_band_id FK
        string opportunity_code
        decimal base_score
        decimal confidence
        json manufacturing_fit
    }
    product_recommendations {
        bigint id PK
        bigint task_id FK
        bigint opportunity_id FK
        bigint model_run_id FK
        string recommendation_type
        decimal confidence
    }
    evidence_links {
        bigint id PK
        bigint task_id FK
        string claim_type
        bigint claim_id
        string evidence_type
        bigint evidence_id
    }
    analysis_reports {
        bigint id PK
        uuid report_uuid UK
        bigint tenant_id FK
        bigint task_id FK
        bigint generated_model_run_id FK
        json sections
    }
    audit_logs {
        bigint id PK
        bigint tenant_id FK
        bigint user_id FK
        string action
        string request_id
    }

    tenants ||--o{ users : contains
    tenants ||--o{ auth_sessions : isolates
    users ||--o{ auth_sessions : authenticates
    tenants ||--o{ api_idempotency_records : isolates
    users ||--o{ api_idempotency_records : submits
    tenants ||--|| enterprise_profiles : owns
    tenants ||--o{ manufacturing_capabilities : owns
    tenants ||--o{ products : owns
    tenants ||--o{ file_assets : owns
    tenants ||--o{ market_datasets : owns
    tenants ||--o{ analysis_tasks : runs
    tenants ||--o{ audit_logs : records
    users ||--o{ audit_logs : performs

    products ||--o{ product_parse_jobs : parses
    products ||--o{ product_profile_versions : versions
    product_parse_jobs ||--o{ product_parse_job_files : contains
    file_assets ||--o{ product_parse_job_files : supplies
    product_profile_versions ||--o{ product_attributes : contains
    products o|--o| product_profile_versions : current_profile

    market_datasets ||--o{ market_listings : contains
    market_datasets ||--o{ reviews : scopes
    market_listings ||--o{ reviews : receives

    products ||--o{ analysis_tasks : analyzes
    product_profile_versions ||--o{ analysis_tasks : freezes
    market_datasets ||--o{ analysis_tasks : freezes
    analysis_tasks ||--o{ task_stage_runs : attempts
    analysis_tasks ||--o{ workflow_checkpoints : projects
    analysis_tasks ||--o{ workflow_partial_failures : records
    analysis_tasks ||--o{ user_confirmations : interrupts
    analysis_tasks ||--o{ workflow_control_events : controls
    workflow_checkpoints ||--o{ user_confirmations : resumes
    workflow_checkpoints ||--o{ workflow_control_events : targets
    analysis_tasks ||--o{ ai_model_runs : traces
    task_stage_runs o|--o{ ai_model_runs : invokes

    analysis_tasks ||--o{ competitor_matches : selects
    market_listings ||--o{ competitor_matches : matches
    product_profile_versions ||--o{ competitor_matches : compares
    analysis_tasks ||--o{ review_aspects : extracts
    reviews ||--o{ review_aspects : yields
    ai_model_runs ||--o{ review_aspects : generates
    analysis_tasks ||--o{ insight_clusters : clusters
    insight_clusters ||--o{ cluster_members : contains
    review_aspects ||--o{ cluster_members : joins
    analysis_tasks ||--o{ market_metrics : calculates
    analysis_tasks ||--o{ price_bands : segments
    analysis_tasks ||--o{ market_opportunities : scores
    price_bands o|--o{ market_opportunities : positions
    analysis_tasks ||--o{ product_recommendations : generates
    market_opportunities ||--o{ product_recommendations : supports
    ai_model_runs ||--o{ product_recommendations : generates
    analysis_tasks ||--o{ evidence_links : proves
    analysis_tasks ||--o{ analysis_reports : produces
    ai_model_runs ||--o{ analysis_reports : composes
```

> `evidence_links` 使用受约束的多态证据关联，因此 `claim_id/evidence_id` 不能用单一数据库外键表达；应用层按 `claim_type/evidence_type` 校验同任务、同租户和来源存在性。`workflow_checkpoints` 仅为业务安全投影，LangGraph官方Checkpointer表不纳入本业务ER图。

## 6. 6+1 页面跳转图

```mermaid
flowchart TD
    S01[S01 登录<br/>/login] --> AUTH{认证后的role_code}
    AUTH -->|user| S02[S02 AI工作台<br/>/workspace]
    AUTH -->|admin| S02
    AUTH -->|admin| A01[A01 管理后台<br/>/admin]

    S02 -->|新建分析| S03[S03 新建分析<br/>/analyses/new]
    S03 -->|创建并启动任务| S04[S04 AI执行中<br/>/analyses/:taskUuid/run]
    S02 -->|最近任务或待确认| S04

    S04 -->|pending user_confirmation| CARD[执行页确认卡片]
    CARD -->|API-CFM-02回答并恢复| S04
    S04 -->|已有综合结果| S05[S05 市场洞察<br/>/analyses/:taskUuid/insights]
    S04 -->|completed且report_uuid有效| S06[S06 决策报告<br/>/reports/:reportUuid]
    S02 -->|最近报告| S06
    S05 -->|查看完整报告| S06
    S06 -->|返回市场证据| S05

    S02 -->|admin导航| A01
    A01 -->|查看脱敏任务诊断| S04

    S03 -->|返回| S02
    S04 -->|返回| S02
    S05 -->|返回| S02
    S06 -->|返回| S02

    USERGUARD{user访问admin路由} -->|拒绝| S02
    A01 -. 不能代user回答确认 .-> CARD
```

## 7. 外部五阶段与内部 Agent 节点映射图

```mermaid
flowchart LR
    subgraph E1[understanding_product 理解产品]
        I00[I00 创建任务并冻结版本]
        I01[I01 Preflight Gate]
        I02[I02 加载产品与企业能力上下文]
        I00 --> I01 --> I02
    end

    subgraph E2[researching_market 研究市场]
        I03[I03 数据质量门禁]
        I04[I04 竞品硬过滤]
        I05[I05 Embedding召回]
        I06[I06 Rerank与解释]
        I07[I07 统一确认门禁]
        I08[I08 评论预处理与批次计划]
        I09[I09 评论观点抽取Map]
        I10[I10 Reduce与质量检查]
        I11[I11 需求Embedding与聚类]
        I12[I12 价格、趋势、企业适配并行分析]
        I03 --> I04 --> I05 --> I06 --> I08 --> I09 --> I10 --> I11 --> I12
        I03 -. 数据不足或低置信 .-> I07
        I06 -. 重大竞品歧义 .-> I07
        I10 -. 主结论低置信 .-> I07
        I07 -. user回答后恢复 .-> I08
    end

    subgraph E3[evaluating_opportunity 评估机会]
        I13[I13 并行分析Join]
        I14[I14 市场分、企业修正<br/>硬条件与独立置信度]
        I13 --> I14
    end

    subgraph E4[generating_recommendation 生成建议]
        I15[I15 Top机会产品策略Map]
        I16[I16 统一确认门禁]
        I17[I17 证据审计]
        I18[I18 在线报告生成]
        I15 --> I17 --> I18
        I15 -. 高风险建议 .-> I16
        I16 -. user回答后恢复 .-> I17
    end

    subgraph E5[completed 完成]
        I19[I19 最终事务入库并完成]
        DONE([在线综合决策报告])
        I19 --> DONE
    end

    I02 --> I03
    I12 --> I13
    I14 --> I15
    I18 --> I19

    SUP[Workflow Supervisor] -. 自动重试、降级、幂等与部分失败 .-> I03
    SUP -. Map Reduce与Checkpoint .-> I09
    SUP -. 报告模板兜底与事务重试 .-> I18
    CONF[user_confirmation<br/>唯一人工中断协议] -. interrupt .-> I07
    CONF -. interrupt .-> I16
    CMD[Checkpoint校验与Command resume] -. 恢复 .-> I07
    CMD -. 恢复 .-> I16
```

映射规则：外部API和页面只能使用五阶段枚举；I00—I19仅出现在技术详情、admin诊断、日志和工作流实现中。I07与I16不是两个审批流程，而是同一 `user_confirmation` 协议在不同安全Checkpoint的调用。

## 8. 后续跨文档依赖

1. 测试用例需覆盖六图中的主路径、四类统一确认触发、Checkpoint冲突、Outbox幂等、部分失败和admin不可代答。
2. FastAPI与LangGraph实现需保持业务Checkpoint投影和官方Checkpointer职责分离，并按时序图保证事务提交后再消费恢复事件。
3. React路由和页面导航需严格使用S01—S06/A01，不恢复V2独立竞品复核、建议复核、Listing或导出路由。
4. 部署架构需确定LangGraph官方Checkpointer独立Schema、Redis用途、对象存储访问控制及向量索引实现。
5. 数据字典或数据库表若后续变化，必须同步ER图；不得仅修改图而不更新结构源文件。

## 9. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V2.0 | 2026-08-08 | 基于多人岗位与V2接口的旧图集 |
| V3.0 | 2026-08-09 | 按超级AI员工定位重绘业务、恢复、架构、ER、页面与节点映射六张图 |

## 10. 本次变更摘要

- 图中角色收敛为user/admin，删除传统岗位权限节点、动态RBAC和多人审批链。
- 业务流程改为user输入目标后由超级AI员工自主完成研究与建议。
- 将竞品确认和专家复核统一为 `user_confirmation`，新增Checkpoint与`Command(resume=...)`完整时序。
- 系统架构明确React 19 + Vite、FastAPI、LangGraph、Model Router、PostgreSQL/Redis/对象存储及外部数据源分层。
- ER图更新为PostgreSQL V3.2全部35张核心表（含认证会话与API幂等账本），标明业务Checkpoint与官方Checkpointer边界。
- 页面图收敛为6个user页面和1个admin页面。
- 内部I00—I19节点统一投影到五个外部阶段，内部节点不进入user主导航。
