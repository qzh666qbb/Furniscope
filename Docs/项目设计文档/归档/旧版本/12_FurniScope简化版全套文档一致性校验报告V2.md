# FurniScope 简化版全套文档一致性校验报告 V2.0

> 状态说明（2026-08-10）：本文是编码修复前的设计阶段校验快照，其中P0问题已由数据库/Agent实现修复及《FurniScope FastAPI编码准入复核报告 V1.1》复核关闭。对应阶段性准入报告已归档至[`../../../../archive/docs/development-milestones/FurniScope_FastAPI编码准入复核报告V1.md`](../../../../archive/docs/development-milestones/FurniScope_FastAPI编码准入复核报告V1.md)；当前研发状态以代码、自动化测试和根目录 README 为准。本文仅用于追溯，不再作为当前阻断判定。

## 1. 校验结论

本轮对 FurniScope 简化版文档进行了角色、页面/API、字段/数据库、Agent恢复、五阶段、范围和测试覆盖的最终一致性校验。

**最终判定：当前存在P0阻断问题，不能判定“简化版文档闭环完成”。**

当前产品定位、角色模型、页面主流程、五阶段和移出范围已经基本统一；但生效文档索引仍指向旧版、数据字典与DDL存在字段级差异、API字段字典不完整、统一确认恢复事务语义不一致、数据库设计摘要与DDL约束不一致，且测试V2缺少若干P0接口的显式契约用例。上述问题会直接导致代码生成、迁移、接口Schema或验收口径分叉。

### 1.1 问题统计

| 严重级别 | 数量 | 闭环影响 |
|---|---:|---|
| P0 阻断 | 7 | 任一未关闭均不得判定文档闭环完成 |
| P1 重要 | 2 | 不阻止统一字段修订，但上线前必须关闭 |
| P2 建议 | 1 | 可在实现阶段补强 |

### 1.2 本轮生效基线

校验采用以下当前新版，而不是同目录中的旧版：

| 设计域 | 应采用文件 |
|---|---|
| 简化决策 | `11_FurniScope产品简化决策基线V1.md` |
| PRD | `01_产品需求规格说明书SRS_PRD_V2.md` |
| 数据字典 | `02_FurniScope产品数据字典V3.md` |
| 数据库 | `03_FurniScope_PostgreSQL数据库设计V3.md`、`furniscope_postgresql_v3.sql` |
| Agent | `04_FurniScope_Agent工作流设计V2.md` |
| API | `05_FurniScope_RESTful_API接口设计V3.md` |
| Mermaid | `06_FurniScope_Mermaid图V3.md` |
| 页面 | `07_FurniScope页面交互原型说明V3.md` |
| 测试 | `09_FurniScope测试用例V2.md` |

用户本轮粘贴路径仍引用 `09_FurniScope测试用例V1.md`，而且项目索引仍把整套旧版标为“当前有效”，因此当前有效基线在文件层面尚未锁定。

## 2. 十二项校验结果

| # | 校验项 | 结果 | 结论摘要 |
|---:|---|---|---|
| 1 | 系统角色仅user/admin | 条件通过 | 新版业务、Agent、API、页面和DDL均只授权user/admin；旧索引仍指向含五岗位的旧文档 |
| 2 | 无旧岗位鉴权枚举 | 条件通过 | 新版中的owner等仅用于禁止说明、画像文本和拒绝测试；V1活跃引用会重新引入旧枚举 |
| 3 | 页面API存在且编号/方法/路径一致 | 通过 | 页面V3共51处带方法路径引用，与API V3的39个定义无不一致 |
| 4 | API全部字段在数据字典V3定义 | 不通过 P0 | 认证、分页、筛选、诊断和部分聚合字段未定义 |
| 5 | 字典全部实体字段进入PostgreSQL V3 | 不通过 P0 | 33实体数一致，但字段名、公共字段和额外DDL字段不一致 |
| 6 | user_confirmation全链一致 | 不通过 P0 | 九字段和状态一致；Command载荷及回答事务状态更新顺序不一致 |
| 7 | 五阶段与内部节点映射一致 | 通过 | I00—I19完整映射到五阶段，API/页面只使用五阶段 |
| 8 | Checkpoint/重试/部分失败/恢复匹配 | 条件不通过 P0 | 表和主要状态存在；回答恢复的事务边界与API时序不统一 |
| 9 | 非核心能力彻底移出 | 通过 | 核心DDL/API/页面无相关实体与写路径；仅在删除、兼容和非核心附录中出现 |
| 10 | 一个user独立完成核心任务 | 通过 | PRD、页面、API和TC-E2E-001形成完整单user闭环 |
| 11 | 测试覆盖全部P0 | 不通过 P0 | FR-01—FR-12有矩阵，但5个P0 API缺少显式独立契约覆盖；输入清单还引用测试V1 |
| 12 | 占位符和旧版本引用 | 不通过 P0 | 无真实待补充/TODO/TBD；但索引和根目录旧文件仍构成生效版本歧义 |

> `confirmation_todos` 中的 `todo` 是正式字段名，不是TODO占位符。

## 3. 问题清单

### FSC-P0-001 生效文档索引与实际新版不一致

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | `00_项目设计文档索引.md`“当前有效文档”；用户本轮输入中的`09_FurniScope测试用例V1.md`；项目设计文档根目录旧V1/V2文件 |
| 现象 | 索引仍声明PRD V1、数据字典V2、数据库V2、Agent V1、API V2、旧Mermaid、页面V2和测试V1有效，与当前V2/V3简化基线完全相反。旧文件也未全部移入归档。 |
| 影响 | 开发、评审或AI读取索引后会重新引入五岗位、旧确认、Listing/导出及旧字段，无法建立唯一事实源。 |
| 修复建议 | 更新索引为本报告1.2的版本；测试入口改为V2；将被替代文件移到`归档/旧版本`；保留10/11决策文档V1作为当前决策基线并明确其类别。 |
| 关闭条件 | 索引仅指向当前新版；根目录每类设计仅保留当前有效版；全项目引用扫描不再把旧API/旧测试当当前入口。 |

### FSC-P0-002 数据字典V3与PostgreSQL V3字段不一致

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | 数据字典V3第3—7章；`furniscope_postgresql_v3.sql`对应CREATE TABLE |
| 实体数量 | 字典33、DDL 33，实体级无缺失 |
| 字段差异 | 见下表 |

| 实体 | 数据字典V3 | DDL V3 | 类型 |
|---|---|---|---|
| `enterprise_profiles` | `business_model` | `business_models` | 命名不一致 |
| `competitor_matches` | `analysis_job_id` | `task_id` | 外键命名不一致 |
| `review_aspects` | `analysis_job_id` | `task_id` | 外键命名不一致 |
| `insight_clusters` | `analysis_job_id` | `task_id` | 外键命名不一致 |
| `market_metrics` | `analysis_job_id` | `task_id` | 外键命名不一致 |
| `price_bands` | `analysis_job_id` | `task_id` | 外键命名不一致 |
| `users` | 未定义`password_hash` | 存在`password_hash` | DDL额外安全字段 |
| `ai_model_runs` | 未定义`error_message` | 存在`error_message` | DDL额外诊断字段 |

此外，数据字典第2章声明除纯关联表和LangGraph官方内部表外默认包含 `created_at/updated_at`，但DDL中 `market_listings/reviews/task_stage_runs/workflow_checkpoints/workflow_partial_failures/workflow_control_events/ai_model_runs/review_aspects/insight_clusters/market_metrics/price_bands/evidence_links` 等缺少 `updated_at`，部分表用其他时间字段替代但字典未声明例外。

| 修复建议 | 先锁定统一任务外键名。建议沿用数据字典的`analysis_job_id`，或反向把字典全部统一为`task_id`，不得混用；随后同步DDL、迁移SQL、数据库文档、ER图、ORM和测试。补齐`password_hash/error_message`字典定义，并逐表明确公共字段例外。 |
| 关闭条件 | 自动字段比较结果为：33实体相同、业务字段差异0、未声明公共字段差异0；V3 DDL重新从零执行和迁移验证通过。 |

### FSC-P0-003 API V3字段未被数据字典V3完整覆盖

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | API V3第1、3—10章；数据字典V3第8—9章 |
| 未覆盖示例 | 认证：`password/access_token/refresh_token/token_type/expires_in/user_id`；通用Envelope：`success/data/error.code/timestamp/items/total/page/page_size/has_next`；筛选/控制：`keyword/sort_by/opportunity_limit/recommendation_limit`；产品投影：`attributes/profile_version/version`；诊断/内部调用：`model_runs/model_hint` |
| 影响 | Pydantic Schema、前端类型、测试契约和数据分类无法从数据字典生成；敏感字段password/token也缺少明确“不落库/不记录”口径。 |
| 修复建议 | 在数据字典V3新增“API公共协议字段”“认证输入输出字段”“分页筛选字段”“管理诊断投影”和“内部Model Router请求字段”章节；逐接口生成请求/响应字段清单并做机器比对。数据库不需要保存的字段明确来源为API输入/衍生并标注不落库。 |
| 关闭条件 | API V3所有Header、Path、Query、Body、成功和失败响应字段均能在字典中定位；字段差异扫描为0。 |

### FSC-P0-004 user_confirmation恢复载荷与事务顺序不一致

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | Agent V2第9.2—9.3；API V3 API-CFM-02；Mermaid V3第3章；页面V3 S04；数据库`user_confirmations/workflow_control_events` |
| 一致部分 | 九字段完全一致；类型为四种；状态均为pending/responded/expired/cancelled；同任务最多一个pending；admin不能代答；恢复必须使用safe Checkpoint和Outbox幂等。 |
| 冲突1 | Agent的`Command(resume=...)`载荷包含`checkpoint_stage`；API与Mermaid示例只传`confirmation_id/selected_option/user_input`。 |
| 冲突2 | Agent要求回答事务内同时：确认responded、Stage成功、任务queued、写恢复Outbox；API只明确确认回答+Outbox，Mermaid将任务改running和新Stage attempt放到Worker消费之后，没有明确queued与Stage成功的提交时点。 |
| 影响 | 重启或消息延迟时可能出现“确认已回答但任务仍waiting_human”、重复恢复或前端轮询状态不确定。 |
| 修复建议 | 统一一种协议。推荐API不信任客户端checkpoint_stage，而由服务端从确认记录读取并写入Command；文档明确Command最终载荷是否包含服务端checkpoint_stage。统一事务状态机：responded + 当前等待Stage终结 + task=queued + Outbox=pending同事务，Worker消费后task=running并创建后继attempt。同步Agent、API、Mermaid、页面反馈和测试断言。 |
| 关闭条件 | 六份文档对Command字段、事务内写入、Worker消费后写入和重复消息行为逐字段一致；恢复故障注入测试通过。 |

### FSC-P0-005 数据库设计V3摘要与DDL约束/关系不一致

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | PostgreSQL数据库设计V3第5章表清单、第4章ER图；数据字典V3 7.4；DDL `analysis_reports`等 |
| 典型差异 | 数据库设计写`analysis_reports task唯一`且ER图为一任务一报告；字典和DDL支持`analysis_job_id + report_version`唯一的一任务多版本。数据库设计写market_datasets存在tenant+UUID唯一，但DDL和字典无dataset UUID。数据库设计写insight_clusters使用cluster_no，DDL使用cluster_code。数据库设计描述market_metrics复合唯一，但DDL未建立对应UNIQUE。 |
| 影响 | 开发人员根据设计文档或DDL会得到不同的表关系、唯一性和查询键。 |
| 修复建议 | 以锁定后的数据字典为源重新生成数据库设计表清单和ER；逐条对比实际pg_constraint/pg_indexes；禁止在摘要中写未落DDL的UUID、唯一键或字段。 |
| 关闭条件 | 数据库设计中的字段、基数、PK/FK/UNIQUE/CHECK/索引与DDL目录查询逐项相同；ER和Mermaid ER一致。 |

### FSC-P0-006 S04技术详情权限在页面与API间存在歧义

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | 页面V3 S04“技术详情抽屉/操作与API”；API V3 API-INS-03 Query说明；数据字典V3 9.1 `stage_runs` |
| 现象 | 页面明确user可用`include_stage_runs=true`打开受控技术详情；API写“user默认false、admin可true”，没有明确user是否允许true；字典写“admin诊断/受控详情”。 |
| 影响 | 前端按页面实现可能收到403，或后端误把admin级错误细节暴露给user。 |
| 修复建议 | 定义双层投影：user可请求脱敏stage_runs基本字段；admin诊断通过API-ADM-07获取model_runs/control events和更完整错误。API-INS-03明确角色、字段白名单和错误脱敏。 |
| 关闭条件 | API、数据字典、页面和测试对user/admin可见字段完全一致；敏感字段泄漏测试通过。 |

### FSC-P0-007 P0 API测试覆盖不够显式且测试入口仍引用V1

| 属性 | 内容 |
|---|---|
| 严重级别 | P0 阻断 |
| 文档位置 | 用户本轮输入的测试V1路径；测试V2第4—13章；API V3接口总览 |
| 现象 | 测试V2覆盖FR-01—FR-12和主要主链路，但自动编号扫描未找到以下P0接口的独立契约用例：API-DSH-01、API-PRD-02、API-PRD-04、API-DAT-03、API-REV-02。部分只在范围写法或组合步骤中出现，无法单独统计请求、响应、错误码和数据库断言。输入清单仍把测试V1交给校验。 |
| 影响 | 无法证明全部28个P0 API均有正向、权限、参数和异常契约覆盖；旧V1会测试已废弃岗位和接口。 |
| 修复建议 | 将最终入口改为测试V2；为5个接口各新增明确编号用例；生成“28个P0 API→至少一个P0用例”的机器可读矩阵；旧V1归档。 |
| 关闭条件 | 28/28 P0 API均至少有一个显式P0契约用例，关键写/恢复接口另有幂等和异常用例；索引只引用V2。 |

### FSC-P1-001 JSONB嵌套结构约束主要依赖应用层，文档未统一责任边界

| 属性 | 内容 |
|---|---|
| 严重级别 | P1 重要 |
| 文档位置 | 数据字典V3 JSON字段约束；数据库设计V3第6章；DDL各jsonb_typeof CHECK；API模型Schema测试 |
| 现象 | DDL多数只校验顶层array/object，字段条目必需键、类型和版本由API/Agent Schema承担；文档有结构说明但未逐字段标明“数据库CHECK”或“Pydantic/JSON Schema”。 |
| 修复建议 | 建立JSON字段责任矩阵：顶层类型/非空由DB，嵌套Schema由Pydantic/JSON Schema，关键可查询键提升普通列；为每个JSON字段关联Schema版本。 |
| 关闭条件 | 所有核心JSONB字段均有Schema标识、验证层和失败测试；开发不误认为DB已验证全部嵌套字段。 |

### FSC-P1-002 当前文档未保存自动一致性检查脚本

| 属性 | 内容 |
|---|---|
| 严重级别 | P1 重要 |
| 文档位置 | 全套文档构建与CI流程 |
| 现象 | 本轮通过临时扫描完成API路径和字段比较，但仓库没有固定的文档lint/trace检查入口。 |
| 修复建议 | 增加只读CI检查：活动版本索引、占位符、角色枚举、页面API路径、API字段字典、字典DDL、P0 API测试覆盖、Mermaid代码块。 |
| 关闭条件 | CI中稳定执行并在差异非0时失败；报告结果可追溯。 |

### FSC-P2-001 Mermaid仅完成静态结构校验

| 属性 | 内容 |
|---|---|
| 严重级别 | P2 建议 |
| 文档位置 | Mermaid图V3全部6个代码块 |
| 现象 | 已确认6个围栏、33实体、I00—I19和五阶段完整；当前环境无Mermaid CLI，未进行真实渲染截图校验。 |
| 修复建议 | 在文档CI安装固定版本Mermaid CLI，逐图渲染SVG并检查失败。 |
| 关闭条件 | 六图在固定版本渲染成功且无截断、语法错误或标签覆盖。 |

## 4. 页面→API→字段→数据库→Agent→测试追踪矩阵

| 页面 | 核心用户目标 | API V3 | 关键字段/投影 | PostgreSQL V3 | Agent节点/阶段 | 主要测试 | 状态 |
|---|---|---|---|---|---|---|---|
| S01 登录 | user/admin进入系统 | AUTH-01/02/03 | email、password、token、role_code、tenant | users、tenants、audit_logs | I01权限和租户预检 | AUTH-001—008、UI-001 | 字段字典缺认证协议，阻断 |
| S02 AI工作台 | 查看任务、确认和报告入口 | DSH-01、CFM-01、INS-03、RPT-01 | metrics、recent_tasks、confirmation_todos、recent_reports | products、analysis_tasks、user_confirmations、analysis_reports | Supervisor状态投影 | E2E-001、UI-001 | 缺DSH-01独立用例 |
| S03 新建分析 | 一次完成产品、资料、数据与启动 | PRD-01—07、DAT-01—04、INS-01/02 | 产品画像、field_mapping、quality_report、冻结版本、idempotency_key | products、file_assets、parse jobs、profiles、datasets、listings、reviews、analysis_tasks | I00— I03；understanding_product→researching_market | WIZ-001—010、UI-002 | 组合链通过；3个读/更新API需独立契约 |
| S04 AI执行中 | 五阶段进度与必要确认 | INS-03、CFM-01/02；admin ADM-07/08 | stage、stage_runs、partial_failures、checkpoint_stage、retryable、user_confirmation、report_uuid | analysis_tasks、task_stage_runs、workflow_checkpoints、partial_failures、confirmations、control_events | I00—I19、I07/I16 interrupt与Command | STG-001/002、CFM-001—012、WF-001—010、UI-003—005 | 恢复事务/技术详情权限阻断 |
| S05 市场洞察 | 竞品、评论、价格、机会和证据 | INS-04、CMP-01、REV-01/02、OPP-01、EVD-01、RPT-01 | data_scope、competitor_summary、review_aspects、clusters、price_summary、opportunities | matches、aspects、clusters、metrics、price_bands、opportunities、evidence_links | I04—I14；researching_market→evaluating_opportunity | STG-003—008、UI-006 | REV-02需独立契约；任务FK命名阻断 |
| S06 决策报告 | 查看结论、建议、制造适配和风险 | RPT-01、REC-01、OPP-01、EVD-01、INS-04 | score、confidence、manufacturing_fit、recommendations、sections、limitations、model_trace | opportunities、recommendations、evidence_links、analysis_reports、ai_model_runs | I15—I19；generating_recommendation→completed | STG-008—010、SEC-007—009、UI-007/008 | 报告基数设计冲突 |
| A01 管理后台 | 管理user、模型、Prompt和诊断 | ADM-01—08 | role_code、model route、compute_config、Prompt version、diagnostics | users、model_route_configs、prompt_templates、model_runs、stage/control/audit | Supervisor受控恢复；不代答 | AUTH-005—007、ADM-001—005、UI-010 | P1能力完整；受S04权限语义影响 |

## 5. user_confirmation跨文档追踪

| 维度 | PRD V2 | Agent V2 | 字典V3 | DDL V3 | API V3 | 页面V3 | 测试V2 | 结果 |
|---|---|---|---|---|---|---|---|---|
| 触发类型 | 四类原则 | 四枚举 | 四枚举 | CHECK四枚举 | 返回四枚举 | 统一卡片 | CFM-001覆盖四类 | 一致 |
| State九字段 | 语义要求 | 明确定义九字段 | 明确定义九字段 | 全部持久化 | pending只投影九字段 | 卡片严格九字段 | CFM-001/002/003 | 一致 |
| 生命周期 | 等待user | waiting_human | pending/responded/expired/cancelled | 同枚举CHECK | 同枚举 | 对应状态反馈 | CFM-004—012 | 一致 |
| 单活动确认 | 必要时中断 | 同任务一个 | partial unique需求 | partial unique索引 | 查询当前pending | 单卡片 | DB-007 | 一致 |
| 回答权限 | user | user；admin禁代答 | responded_by为同租户user | FK users，跨租户由服务校验 | 仅user | admin无按钮 | AUTH-005/CFM-012 | 一致 |
| Command字段 | 未定细节 | 含checkpoint_stage | 回答字段与checkpoint引用 | 持久化checkpoint_stage/id | 示例不含checkpoint_stage | 不传checkpoint_stage | 期望安全恢复 | 不一致 P0 |
| 回答事务 | 确认后恢复 | responded+Stage完成+task queued+Outbox | 实体支持 | 表支持，无跨表CHECK | 只明确responded+Outbox | 202后刷新 | CFM-004/011/DB-012 | 文档顺序不一致 P0 |

## 6. 五阶段与内部节点校验

| 外部阶段 | Agent节点 | API/页面 | DDL约束 | 测试 | 结果 |
|---|---|---|---|---|---|
| understanding_product | I00—I02 | INS-03/S04 | external_stage CHECK | STG-001 | 通过 |
| researching_market | I03—I12，I07统一确认 | INS-03/S04/S05 | external_stage CHECK | STG-001、CFM-002 | 通过 |
| evaluating_opportunity | I13—I14 | INS-03/S05 | external_stage CHECK | STG-007 | 通过 |
| generating_recommendation | I15—I18，I16统一确认 | INS-03/S04/S06 | external_stage CHECK | CFM-003、STG-008/009 | 通过 |
| completed | I19 | INS-03/S06 | succeeded要求completed+100+completed_at | STG-009/010、WF-008 | 通过 |

内部 `competitor_review/expert_review` 只在删除说明、兼容映射和拒绝测试中出现，未进入V3 Stage CHECK。

## 7. 非核心范围检查

| 能力 | PRD | 数据字典 | DDL | API | 页面 | 测试 | 结果 |
|---|---|---|---|---|---|---|---|
| Listing生成 | 明确移出 | 仅非核心附录 | 无表 | 旧路径410说明 | 无页面 | 仅P1兼容拒绝 | 通过 |
| 文件导出 | 明确移出 | 仅非核心附录 | 无report_exports | 无核心接口 | 无页面 | 非P0 | 通过 |
| validation_task | 明确移出 | 明确删除 | 无表 | 无接口 | 仅展示validation_method | 非P0 | 通过 |
| 多人报告评审/发布 | 明确移出 | 无review实体 | 无表/发布字段 | 无发布接口 | 无按钮 | 非P0 | 通过 |
| product_event | 明确移出 | 明确删除 | 无表 | 无接口 | 无页面 | 非P0 | 通过 |

`export_markets`、数据“导出时间”等业务词不属于报告文件导出功能，不视为残留。

## 8. P0需求与测试覆盖结论

| 需求 | 测试覆盖 | 结论 |
|---|---|---|
| FR-01 身份、租户与平台管理 | AUTH、DB-001/002、UI-001 | 覆盖 |
| FR-02 企业能力上下文 | WIZ-004、STG-008 | 覆盖 |
| FR-03 产品资料与多模态理解 | WIZ-001—004、SEC-001/002 | 覆盖 |
| FR-04 市场数据与质量 | WIZ-005—007、SEC-008 | 覆盖 |
| FR-05 竞品识别与可比性 | STG-004、CFM-002、WF-002 | 覆盖 |
| FR-06 评论需求洞察 | STG-005、WF-003、SEC-005/006 | 覆盖 |
| FR-07 市场价格与竞争 | STG-006、WF-004 | 覆盖 |
| FR-08 工程建议 | STG-008、CFM-003、SEC-009 | 覆盖 |
| FR-09 企业适配与机会评分 | STG-007/008、DB-011 | 覆盖 |
| FR-10 自主任务确认恢复 | E2E、STG、CFM、WF | 深度覆盖，但语义文档需先统一 |
| FR-11 报告与证据 | STG-009/010、SEC、UI-007 | 覆盖 |
| FR-12 模型Prompt诊断 | SEC-003/004/010、ADM | 覆盖 |

需求级覆盖完整，但接口级仍有FSC-P0-007列出的5个缺口，因此不能以需求矩阵替代P0 API契约覆盖。

## 9. 修复顺序与复验方案

1. 先关闭FSC-P0-001，锁定唯一生效文档集，否则后续修订没有稳定输入。
2. 决定任务外键、企业业务模式和公共字段的唯一命名，修订数据字典。
3. 依据字典重新生成/修订DDL、迁移SQL、数据库设计和ER，执行从零及带数据迁移验证。
4. 补全API公共/认证/分页/诊断字段字典，运行API字段差异检查。
5. 统一user_confirmation恢复载荷和事务时序，同步Agent/API/Mermaid/页面/测试。
6. 明确S04 user/admin技术详情字段白名单。
7. 补齐5个P0 API独立用例，生成28/28覆盖矩阵。
8. 最后重跑本报告十二项校验、PostgreSQL验证、Mermaid渲染和P0自动化。

只有P0问题全部关闭且复验通过，才允许在下一版报告中写出“简化版文档闭环完成”。

## 10. 后续跨文档依赖

1. 数据字典V3修订会联动PostgreSQL设计/DDL、迁移SQL、API Schema、ER图和数据库测试。
2. 确认协议修订会联动Agent V2、API V3、Mermaid时序、S04交互和CFM/WF测试。
3. 生效版本索引修订会影响赛事提交包、开发读取入口和归档目录。
4. API字段补全会联动FastAPI Pydantic模型、React TypeScript类型和契约测试。前端技术栈已于2026-08-11由Svelte切换为React 19 + Vite，本报告其他结论不变。
5. 测试覆盖修订后应生成可机器检查的FR/API/页面/表/Agent追踪清单。

## 11. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-08 | 旧版多文档一致性校验 |
| V2.0 | 2026-08-09 | 对简化版V2/V3文档执行角色、API、字段、数据库、Agent、页面和测试全链校验 |

## 12. 本次变更摘要

- 确认新版角色与单user核心业务闭环已统一。
- 验证页面V3的API编号、方法和路径全部存在且一致。
- 发现活动版本索引、数据字典/DDL、API字段字典、确认恢复语义、数据库摘要和P0 API测试覆盖七类阻断问题。
- 给出页面→API→字段→数据库→Agent→测试追踪矩阵。
- 完成user_confirmation、五阶段和非核心范围专项检查。
- 因存在P0阻断问题，明确不判定简化版文档闭环完成。
