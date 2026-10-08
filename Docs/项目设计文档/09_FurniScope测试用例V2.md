# FurniScope 测试用例 V2.0

## 企业闭环增量验收（2026-10-05复核）

上位依据：[15总体设计与验收清单](./15_FurniScope_企业决策与数据闭环总体设计V1.md)。使用独立PostgreSQL、Python3.12锁定依赖及明确标注的合成工程样本，不能以合成指标证明商业精度。

| 测试 | 关键断言 | 自动化位置 |
|---|---|---|
| 字段与质量 | CSV/XLSX/JSON、重复表头、非有限数量、歧义日期、负净销量、原始行追溯、补零确认 | `tests/test_enterprise_training.py` |
| 首次/追加/重建 | 同名SKU两企业预测不同；不读取共享历史；历史改写确认；合并缺口阻止训练 | 同上 |
| 独立评测与发布 | 时间留出不进入拟合；未达标/文件篡改/磁盘失败/CAS变化保留现用模型 | 同上 |
| 数据库租户边界 | 漏tenant过滤仍受RLS保护；跨租户读写/关联/私有部署被拒；事务提交及连接重用重新绑定 | `tests/test_tenant_boundaries.py` |
| 企业策略和反馈 | 任务冻结策略与事实；精确能力冲突导致暂缓；未知不加能力分；版本冲突和跨租户反馈拒绝 | `tests/test_enterprise_policy.py` |
| 完整HTTP链路 | 真实登录→上传→预检→确认→训练→预测；幂等、标准审计、跨租户和反馈历史 | `tests/test_enterprise_http.py` |
| 页面与接口 | 规则改变失效旧预检；训练发布刷新模型/SKU；未达标保留旧部署；策略/事实/反馈刷新持久化 | `frontend/tests/e2e/enterprise-live.spec.js` |
| 训练恢复 | 实际子进程取消、数据库/Redis续租、过期令牌拦截、尝试耗尽、孤儿文件锁与发布制品保留 | `tests/test_training_recovery.py`、`test_job_queue.py`；`scripts/drill_training_sigkill.py`真实进程演练 |
| 目录和回滚 | 一一映射、歧义阻断、入队冻结、SHA篡改拒绝回滚、原子恢复目录 | `tests/test_forecast_catalog.py` |
| 产品事实 | 原文候选不自动确认、多文件冲突、人工优先、ETag、历史画像保留 | `tests/test_product_facts.py` |
| 订单与对账 | 完全重复去重、取消/实际退货扣减、跨版本冲突、仓库、币种、独立销量/库存来源、模板修订 | `tests/test_import_contract_v2.py` |
| 分层与滚动 | 每SKU独立时间范围、三窗口无泄漏、小SKU失败、新品/全零未验证、空区间 | `tests/test_forecast_strata.py` |
| 实施与排序数据 | 同租户完整销量血缘、原始SHA、不可变事件、采纳更新、回溯排除、固定截点完整分页 | `tests/test_opportunity_outcomes.py` |
| 离线验收 | 手算NDCG、完整任务、缺标签不记0、晚到特征/标签排除、重复/跨租户/非法时间拒绝、未知日期No-Go | `tests/test_offline_acceptance.py` |
| 迁移 | 新建空库、旧基线存量升级、重复迁移、启动RLS；已有数据库绝不重置 | `scripts/verify_enterprise_migrations.py` |

执行：

```bash
PYTHONPATH=.:backend FURNISCOPE_TEST_DATABASE_URL=postgresql://<account>@localhost/<isolated_db> python -m pytest tests -q --tb=short
```

页面实测必须显式设置`FURNISCOPE_ENTERPRISE_FIXTURE`（合成账户文件）和可选`FURNISCOPE_ENTERPRISE_URL`；不设置时自动跳过，禁止指向真实企业服务器。`scripts/serve_enterprise_e2e.py`启动合成夹具API，仅接受本地`furniscope_enterprise_test_*`数据库；种子函数为`test_enterprise_http.seed_enterprise`。用Vite代理完成真实浏览器测试；构建运行`npm run build`，托管兼容检查运行`npm run test:sites`。完整命令、日志、截图和SHA见[本轮验收证据](../../artifacts/enterprise-20261005/README.md)，最终计数和边界由15总体设计集中记录。10月4日证据作为历史里程碑保留。

包含冻结机会的认证测试夹具以关闭租户、禁用用户和撤销会话退出，不绕过触发器删除历史；管理员列表测试按夹具唯一标识搜索，不假定第一页包含所有租户。SIGKILL演练暂停实际训练子进程后杀死worker，等待真实租约过期并启动新worker；验证成功恢复、令牌更换、旧子进程不发布、孤儿清理及队列无待确认消息。

## 1. 文档目标

本文用于验证 FurniScope“跨境家具超级 AI 员工”V3 产品闭环。测试对象包括 React 19 + Vite 七个企业一级入口及子页、独立Admin、FastAPI V3、LangGraph Agent V2、Model Router封装和PostgreSQL V3。

登录身份仅为`user/admin`：user进入企业工作台，admin管理企业、模型、Prompt、运行配置和诊断，但不得代替user回答业务确认。v3.27租户内角色只映射业务权限，不进入`users.role_code`或JWT身份枚举；内部Agent不作为角色或权限主体。

## 2. 测试范围与基线

| 设计域 | 测试基线 |
|---|---|
| 产品需求 | 产品需求规格说明书SRS_PRD_V2，FR-01—FR-12 |
| 数据 | 产品数据字典V3、PostgreSQL数据库设计V3、V3 DDL |
| 工作流 | Agent工作流设计V2，I00—I19、五阶段、统一确认、Checkpoint |
| API | RESTful API接口设计V3 |
| 页面 | 页面交互原型说明V3，E01—E07/A01及关联子页 |

### 2.1 测试优先级

- P0：Demo和核心产品闭环发布阻断；必须全部通过。
- P1：admin配置与诊断、扩展性能和恢复演练；失败不得破坏P0数据正确性。
- P2：长期容量、兼容和体验优化。

### 2.2 明确不在P0范围

以下能力不得出现在P0测试通过率分母、Demo主链路或P0页面断言中：Listing生成、Excel业务执行包、validation_task、多人报告评审、发布审批、product_event、旧岗位Token/审批RBAC、岗位分配和跨部门审批。决策报告只读PDF属于当前交付，但必须与在线冻结值一致。旧写接口若部署兼容层，只验证410/禁止双写，不验证旧业务功能。

## 3. 环境与通用前置条件

1. PostgreSQL 16执行 `furniscope_postgresql_v3.sql`及其引用增量；35仅为历史核心表数，现行按迁移、约束及RLS校验；LangGraph官方Checkpointer使用独立Schema。
2. Redis仅用于锁、缓存、短状态和限流；对象存储使用私有桶与短效访问。
3. Model Router测试桩可按请求返回成功、超时、非法Schema、限流和固定Embedding/Rerank结果。
4. 按接口契约设置幂等和版本字段；企业策略/反馈/标准确认显式使用`expected_version/expected_revision/preview_sha256`，训练创建使用`Idempotency-Key`；其余沿用通用约定。
5. 数据库断言以测试tenant为范围；除专门隔离测试外，不得无tenant条件查询业务表。
6. 日志和失败响应均记录request_id，不记录密码、API Key、完整Prompt、未脱敏企业敏感正文。

### 3.1 标准测试数据

| 编码 | 数据 |
|---|---|
| T-A | 租户A，家具制造企业，default_currency=USD |
| U-A | T-A下active user，role_code=user |
| ADM | 平台管理租户下active admin，role_code=admin |
| T-B/U-B | 独立租户B及其active user，用于隔离测试 |
| P-SOFA | 模块化沙发SOFA-001；图片、PDF、尺寸表；画像含尺寸、材质、结构、包装等来源和置信度 |
| EP-A | 企业能力与constraints；含MOQ、交期、材料/结构能力，部分unknown |
| DS-US | Amazon US沙发授权/合成数据；120商品、8600评论、时间范围和质量报告完整 |
| DS-BAD | 字段缺失、重复、评论关联失败的数据集 |
| REV-EN | 原文 `Seat cushion sinks after use`，用于Span测试 |
| TASK-A | P-SOFA confirmed画像 + DS-US ready数据集创建的任务 |

## 4. user/admin权限边界测试

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-AUTH-001 | P0 | FR-01 | API-AUTH-01 `POST /api/v1/auth/login`；API-AUTH-03 `GET /api/v1/users/me` | U-A有效 | 正确邮箱密码 | 1.登录；2.校验RS256签名、kid、iss、aud、sub/user_id/tenant_id/role_code/iat/nbf/exp/jti；3.查询me | 200；仅接受允许算法、`furniscope-api/furniscope-web`和15分钟TTL；进入S02 | users.last_login_at更新；password_hash不变化；Access Token不落库；无明文密码行 |
| TC-AUTH-002 | P0 | FR-01 | API-AUTH-01 | U-A存在 | 错误密码连续提交 | 返回统一401 `AUTH_INVALID_CREDENTIALS`，不泄露账号存在性；达到阈值429 | users不新增/修改密码；audit_logs不含密码 |
| TC-AUTH-003 | P0 | FR-01 | API-AUTH-02 `POST /api/v1/auth/refresh` | 有效/过期refresh token | 两类Token | 分别刷新并重放旧Token | 有效Token轮换；过期Token401；旧Token重用409 `AUTH_TOKEN_REUSE_DETECTED`并撤销同family | auth_sessions只存SHA-256摘要；family会话全部revoked；业务表无变化 |
| TC-AUTH-009 | P0 | FR-01 | API-AUTH-03及全部鉴权API | 双kid公钥集、disabled user、suspended tenant | 旧/新kid Token及状态变更 | 轮换验签密钥后分别请求；再禁用用户/暂停租户 | 轮换窗口内旧/新kid均可验签；未知kid或错误iss/aud/算法401；用户或租户失效立即403 | 每次请求查库复核users/tenants状态；Token、私钥和公钥集不落业务表/日志 |
| TC-AUTH-004 | P0 | FR-01 | 所有P0业务API | U-A已登录 | user Token | 依次访问产品、数据集、任务、洞察和报告API | user可执行全部业务闭环；不存在岗位权限拒绝 | audit_logs.actor_user_id关联U-A；users.role_code=user；无roles/user_roles表 |
| TC-AUTH-005 | P0 | FR-01、FR-10 | API-CFM-02 `POST /api/v1/user-confirmations/{confirmation_id}:respond` | T-A存在pending确认；ADM登录 | admin Token | admin提交回答 | 403 `CONFIRMATION_USER_REQUIRED`；admin不能代答 | user_confirmations保持pending；无resume_confirmation控制事件 |
| TC-AUTH-006 | P0 | FR-01 | API-ADM-01 `GET /api/v1/admin/users` | U-A登录 | user Token | 访问admin路径 | 403 `ADMIN_REQUIRED`；React路由返回S02 | users、model_route_configs、prompt_templates均无变更 |
| TC-AUTH-007 | P1 | FR-01、FR-12 | API-ADM-02 `PATCH /api/v1/admin/users/{user_id}` | ADM登录 | role_code=owner/product_rd/market_ops/sales各一次 | 更新角色 | 422 `USER_ROLE_INVALID`；仅user/admin可接受 | users.role_code CHECK拦截非法值；原值不变 |
| TC-AUTH-008 | P0 | FR-01 | API-PRD-03、API-INS-03、API-RPT-01 | T-A/T-B资源均存在；U-B登录 | T-A的product_id/task_uuid/report_uuid | U-B逐个读取 | 403或404，不泄露资源存在性和正文 | 查询结果无T-A行；audit_logs记录拒绝且不含正文 |

## 5. 单user端到端与新建分析向导

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-E2E-001 | P0 | FR-01—FR-11 | API-AUTH-01、API-PRD-01—07、API-DAT-01—04、API-INS-01—04、API-CFM-01/02、API-CMP-01、API-REV-01/02、API-OPP-01、API-REC-01、API-EVD-01、API-RPT-01 | U-A；空产品/任务 | P-SOFA、EP-A、DS-US | 1.user登录；2.S03创建产品并上传资料；3.确认画像；4.创建/导入数据集；5.创建并启动任务；6.必要时回答确认；7.等待completed；8.查看S05/S06 | 一个user无需其他角色完成全链路；得到竞品、评论洞察、机会、建议、证据和在线报告 | 各核心实体tenant_id=T-A；任务冻结版本一致；仅一个报告当前版本；证据、模型运行和范围快照可回溯 |
| TC-WIZ-001 | P0 | FR-03 | API-PRD-01 `POST /api/v1/products` | U-A登录 | SOFA-001合法字段 | S03步骤二创建产品 | 201，保存product_id并留在向导；SKU冲突可定位 | products新增1行；tenant+sku唯一；created_by=U-A |
| TC-WIZ-002 | P0 | FR-03 | API-PRD-05 `POST /api/v1/products/{product_id}/assets:parse`；API-PRD-06 `GET /api/v1/product-parse-jobs/{parse_job_id}` | P-SOFA已建 | 图片+PDF+参数表 | 上传并轮询 | 202；queued→running→succeeded/partial_succeeded；进度不回退；文件结果可见 | file_assets、product_parse_jobs、product_parse_job_files关联完整；completed_at终态必填 |
| TC-WIZ-003 | P0 | FR-03 | API-PRD-03/04/07 | 解析完成 | 冲突材质值、已知尺寸、unknown承重 | 查看画像、修正合法属性、确认 | 冲突未处理时确认422；处理后200 confirmed；unknown不转为0 | 新product_profile_versions不可变；product_attributes保留source_locator/confidence/confirmation_status；confirmed_by/at完整 |
| TC-WIZ-004 | P0 | FR-02、FR-03 | API-PRD-03 | EP-A存在 | 制造能力部分unknown | 查看步骤二摘要 | 显示constraints与能力证据；unknown明确显示，不编造能力 | enterprise_profiles.constraints为array；制造能力置信度合法；无未知值写0 |
| TC-WIZ-005 | P0 | FR-04 | API-DAT-01 `POST /api/v1/market-datasets` | U-A登录 | DS-US范围和授权依据 | S03步骤三创建数据集 | 201 status=uploaded；市场、平台、品类和时间口径正确 | market_datasets tenant正确；field_mapping为array；授权字段按来源条件存在 |
| TC-WIZ-006 | P0 | FR-04 | API-DAT-02 `POST /api/v1/market-datasets/{dataset_id}/imports`；API-DAT-04 `GET /api/v1/market-datasets/{dataset_id}` | 数据集uploaded | DS-US文件 | 导入并轮询质量 | validating→ready；显示商品/评论/有效评论数、质量分和limitations | listing/review去重键有效；统计与实际行数一致；原始评论保留 |
| TC-WIZ-007 | P0 | FR-04 | API-DAT-02/04 | 数据集uploaded | DS-BAD | 导入错误数据 | rejected或保持不可启动；错误定位field_mapping/质量问题；不把处理中当空数据 | 无跨数据集脏关联；quality_report记录缺失/重复/关联问题；status非ready |
| TC-WIZ-008 | P0 | FR-10 | API-INS-01 `POST /api/v1/analysis-tasks` | confirmed画像、ready数据集 | job_type=product_market_fit及analysis_config | 创建任务 | 201 draft；返回task_uuid；输入范围一致 | analysis_tasks冻结profile/dataset/analysis_config/ontology/scoring/prompt/model_route版本；idempotency_key唯一 |
| TC-WIZ-009 | P0 | FR-10 | API-INS-02 `POST /api/v1/analysis-tasks/{task_uuid}:start` | draft任务 | 有效Idempotency-Key | 启动两次相同请求 | 首次202 queued；第二次返回原受理结果，不重复计费 | 任务仅一行；I00/I01阶段attempt不重复；控制/队列事件不重复 |
| TC-WIZ-010 | P0 | FR-03、FR-04、FR-10 | API-INS-01/02 | 未确认画像或非ready数据集 | 两种非法组合 | 创建/启动 | 422 `PRODUCT_PROFILE_NOT_CONFIRMED`或`DATASET_NOT_READY`；S03定位对应步骤 | analysis_tasks不进入queued/running；无ai_model_runs |

## 6. 五阶段、综合洞察与决策报告

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-STG-001 | P0 | FR-10 | API-INS-03 `GET /api/v1/analysis-tasks/{task_uuid}` | TASK-A运行 | 正常工作流 | 连续轮询 | stage只按五枚举返回；progress_percent单调；内部I节点不成为外部stage | analysis_tasks.external_stage仅五值；internal_stage保留I节点；进度0—100 |
| TC-STG-002 | P0 | FR-10 | API-INS-03 | 运行任务 | include_stage_runs=false/true | 分别查询 | 主视图不暴露运维按钮；技术详情返回脱敏stage_runs、partial_failures、checkpoint_stage、retryable及request_id | task_stage_runs按时间/attempt排序；error_message无密钥/正文 |
| TC-STG-003 | P0 | FR-05—FR-09 | API-INS-04 `GET /api/v1/analysis-tasks/{task_uuid}/result` | 已有阶段性结果 | TASK-A | 查询综合结果 | 一次返回报告摘要、data_scope、竞品、聚类、机会、建议和下钻入口所需标识 | 聚合值均来自同task/tenant；base_score与confidence分离；partial_failures不丢失 |
| TC-STG-004 | P0 | FR-05 | API-CMP-01 `GET /api/v1/analysis-tasks/{task_uuid}/competitors` | I06完成 | TASK-A | 按competitor_type筛选 | 返回direct/benchmark/substitute/excluded、六维分、overall与rerank分、理由和set_version | competitor_matches同task+listing+version唯一；rerank不覆盖业务overall_score |
| TC-STG-005 | P0 | FR-06 | API-REV-01/02 | I10/I11完成 | DS-US评论 | 查询观点和聚类 | 观点含原文Span与置信度；聚类含明确分母、跨商品率和代表证据 | review_aspects关联reviews/model_run；cluster_members引用存在；聚类task一致 |
| TC-STG-006 | P0 | FR-07 | API-INS-04、API-RPT-01 | 报告完成 | 有/无时序两组数据 | 查看S05价格Tab | 有数据返回price_summary及币种/时间口径；无时序显示截面限制，不伪造趋势 | market_metrics/price_bands算法版本、样本量合法；报告limitations记录跳过原因 |
| TC-STG-007 | P0 | FR-09 | API-OPP-01 `GET /api/v1/analysis-tasks/{task_uuid}/opportunities` | I14完成 | 五市场因子完整/缺增长利润、企业条件满足/不满足/未知 | 查询机会 | 按冻结企业权重及适配强度计算；缺项归一；硬冲突不得强推荐 | market_score、adjusted_score、confidence独立；policy_snapshot与任务一致；旧任务不受策略更新影响 |
| TC-STG-008 | P0 | FR-02、FR-08、FR-09 | API-OPP-01、API-REC-01 | I15完成 | matched/gap/unknown能力 | 查看制造适配与建议 | manufacturing_fit区分匹配、缺口和未知；建议含问题—根因假设—动作—影响—风险—验证方法 | market_opportunities.manufacturing_fit为array；建议至少1个evidence_cluster_id；未知fit_score为空 |
| TC-STG-009 | P0 | FR-11 | API-RPT-01 `GET /api/v1/reports/{report_uuid}` | I19成功 | TASK-A report_uuid | 打开S06 | 在线报告包含结论、机会分、置信度、快照、建议、风险、待验证、sections、model_trace与limitations；无发布/导出按钮 | analysis_reports report_uuid唯一；sections为有序array；version_bundle和partial_failures_snapshot冻结 |
| TC-STG-010 | P0 | FR-10、FR-11 | API-INS-03、API-RPT-01 | I19前 | report_uuid=null | 尝试进S06 | 页面停留S04；REPORT_NOT_READY或禁用入口；不出现空报告 | 不存在孤立analysis_reports；analysis_tasks未标completed |

## 7. user_confirmation生成、回答与恢复

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-CFM-001 | P0 | FR-10 | API-INS-03、API-CFM-01 `GET /api/v1/user-confirmations` | 分别构造四类阻断 | fact_conflict/insufficient_data/low_confidence/high_risk_recommendation | 运行至门禁并查询 | 仅四类条件生成确认；任务waiting_human；确认只含九个State字段 | 同task最多一条pending；options/evidence_refs非空；checkpoint_stage关联safe checkpoint |
| TC-CFM-002 | P0 | FR-05、FR-10 | API-CFM-01 | I06竞品重大歧义 | 低可比性样本 | 查询pending事项 | 显示单一问题、推荐项、样本证据和选择影响；没有独立竞品审批页 | confirmation_type=low_confidence或insufficient_data；competitor_matches不写review字段 |
| TC-CFM-003 | P0 | FR-08、FR-10 | API-CFM-01 | I15高风险建议 | 承重/阻燃等高风险建议 | 查询确认 | 使用同一确认协议；没有专家角色/复核字段 | confirmation_type=high_risk_recommendation；product_recommendations无expert_review字段 |
| TC-CFM-004 | P0 | FR-10 | API-CFM-02 `POST /api/v1/user-confirmations/{confirmation_id}:respond` | U-A、pending、safe checkpoint | 合法selected_option和user_input | 提交回答 | 202 accepted/resumed_from_checkpoint=true；仅表示事务和Outbox受理；S04恢复轮询 | confirmation变responded且responded_by=U-A；同事务新增resume_confirmation pending事件 |
| TC-CFM-005 | P0 | FR-10 | API-CFM-02 | pending确认 | options之外的code | 提交 | 422 `CONFIRMATION_OPTION_INVALID`；保留页面输入 | confirmation仍pending；无workflow_control_events新增 |
| TC-CFM-006 | P0 | FR-10 | API-CFM-02 | 选项要求补充结构化事实 | 不符合选项Schema的user_input | 提交 | 422 `CONFIRMATION_INPUT_INVALID`并定位字段 | user_input不落库；状态不变 |
| TC-CFM-007 | P0 | FR-10 | API-CFM-02 | pending确认 | 同Idempotency-Key+同Body两次 | 连续提交 | 两次返回同一受理结果，不重复Command恢复 | user_confirmations一条responded；resume_confirmation事件一条；新attempt仅一次 |
| TC-CFM-008 | P0 | FR-10 | API-CFM-02 | 已有幂等受理 | 同Key但不同selected_option | 再提交 | 409 `IDEMPOTENCY_CONFLICT`；客户端保留新输入并提示冲突 | 原回答、控制事件和checkpoint引用均不变 |
| TC-CFM-009 | P0 | FR-10 | API-CFM-02 | expires_at已过 | 合法选项 | 提交 | 409 `CONFIRMATION_EXPIRED`；系统不自动采用recommended_option | status变expired或由过期任务处理；无resume事件；任务保持可诊断状态 |
| TC-CFM-010 | P0 | FR-10 | API-CFM-02 | confirmation指向非safe/其他task checkpoint | 伪造/过期checkpoint | 提交 | 409 `WORKFLOW_CHECKPOINT_CONFLICT`；不恢复Graph | 回答事务回滚；checkpoint未consumed；无控制事件 |
| TC-CFM-011 | P0 | FR-10 | API-CFM-02、API-INS-03 | 正确safe checkpoint | 合法答案 | 回答后观察Worker | Worker消费Outbox并执行Command(resume=...)；从合法后继边继续；已完成节点不重跑 | control event consumed_at存在；任务running；stage_run attempt按规则递增；模型成功调用不重复 |
| TC-CFM-012 | P0 | FR-01、FR-10 | API-CFM-02 | U-B登录；T-A确认 | T-A confirmation_id | 提交 | 403/404；不泄露问题、选项或任务 | T-A确认不变；audit记录拒绝；无跨租户事件 |

## 8. Agent韧性、自动重试与安全恢复

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-WF-001 | P0 | FR-10、FR-12 | API-INS-03、API-MDL-01 | 模型路由max_retries=2 | 首次超时、第二次成功 | 执行节点 | Supervisor自动重试，user无需操作；阶段最终成功 | task_stage_runs/ai_model_runs记录attempt和retry_count；只保留一次业务结果 |
| TC-WF-002 | P0 | FR-05、FR-10 | API-MDL-02/03、API-INS-03 | Embedding/Rerank不可用 | 连续上游超时 | 执行竞品召回 | 有规则降级时继续并披露限制、降低置信度；无安全降级才失败 | ai_model_runs记录错误和模型；partial_failures/limitations记录降级；无API Key |
| TC-WF-003 | P0 | FR-06、FR-10 | API-INS-03、API-INS-04 | 评论Map多批次 | 2/20批次超时 | 执行I09/I10 | 成功批次保留；任务继续或partial_succeeded；综合结果披露受影响范围 | workflow_partial_failures记录unit_type/count/impact/retryable；成功review_aspects不回滚 |
| TC-WF-004 | P0 | FR-07、FR-10 | API-INS-04、API-RPT-01 | 无两个可比时间点 | 单一截面数据 | 执行I12 | 趋势分支skipped，价格/竞争/适配继续；报告明确截面限制 | 对应stage_run status=skipped；无虚构growth指标；limitations持久化 |
| TC-WF-005 | P0 | FR-10 | API-INS-03 | safe checkpoint后 | 强制终止Worker | 重启Worker | 从官方Checkpointer恢复；业务投影校验一致；不重复执行已完成节点 | task幂等键不变；成功结果唯一；新attempt可追踪；锁无永久遗留 |
| TC-WF-006 | P0 | FR-10 | API-INS-03 | 最新投影is_safe_resume=false、前一版本safe | Worker重启 | 恢复 | 不从不安全点恢复；回退最近safe点或进入admin诊断 | workflow_checkpoints安全标记不被篡改；控制事件引用safe checkpoint |
| TC-WF-007 | P0 | FR-10 | API-INS-03、API-CFM-02 | 业务投影存在、官方State缺失 | 删除测试Checkpointer State | 尝试恢复 | 按Agent规则重建可验证最小State；不可验证则失败并交admin诊断，不盲目继续 | failure_code和审计完整；无伪造completed；confirmation不重复消费 |
| TC-WF-008 | P0 | FR-10、FR-11 | API-INS-03、API-RPT-01 | I19最终事务 | 模拟报告插入失败 | 提交最终结果 | 任务不得返回completed/succeeded；自动事务重试或安全失败 | analysis_tasks、analysis_reports、evidence_links保持原子一致；无孤立report_uuid |
| TC-WF-009 | P0 | FR-10 | API-INS-03 | Outbox事件已提交未消费 | Worker在消费前重启 | 重启并消费 | 事件只消费一次；Command恢复幂等 | workflow_control_events唯一键生效；status最终consumed；无重复stage attempt业务结果 |
| TC-WF-010 | P1 | FR-12 | API-ADM-07、API-ADM-08 | ADM、可恢复失败任务 | event_type=auto_retry及safe_stop | 诊断后分别提交 | 仅safe checkpoint可auto_retry；safe_stop安全终止；pending确认时admin不能绕过 | 控制事件含requested_by、reason payload和审计；confirmation不被admin回答 |

## 9. 可信AI、文件与数据安全

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-SEC-001 | P0 | FR-03 | API-PRD-05/06 | P-SOFA存在 | EICAR测试文件/伪装扩展名/超限文件 | 上传 | 拒绝或标记unsafe，不进入文本/多模态解析 | file_assets保留安全审计元数据；product_parse_job_files不产生成功output_ref |
| TC-SEC-002 | P0 | FR-03 | API-PRD-05/06 | 私有对象存储 | 合法PDF | 上传并查看 | API不返回永久公网URL；仅受控访问；日志不含文件正文 | 数据库只存object_key/hash/MIME/size；无文件二进制列写入 |
| TC-SEC-003 | P0 | FR-12 | API-MDL-01 | Model Router桩 | 非法JSON/缺必填字段/错误类型 | 调用结构化推理 | Schema校验失败，有界重试；非法结构不进入业务结果表 | ai_model_runs.schema_valid=false、error_code完整；目标业务表无脏行 |
| TC-SEC-004 | P0 | FR-12 | API-MDL-01/02/03 | 已配置环境变量Key | 哨兵Key `sk-test-secret-marker` | 完成模型调用后扫描DB/日志/API响应 | 哨兵Key在三处均不存在；前端不可见 | 对所有text/json/jsonb列搜索0命中；model_route_configs只存模型ID和策略 |
| TC-SEC-005 | P0 | FR-06、FR-11 | API-REV-01、API-EVD-01 | REV-EN存在 | start=5,end=18,quote=`cushion sinks` | 写入并查询证据 | 正确Span成功；API高亮原文；翻译不替代原文 | review_aspects触发器校验quote等于content_original子串；关联review/model_run存在 |
| TC-SEC-006 | P0 | FR-06、FR-11 | API-REV-01 | REV-EN存在 | 越界end或错误quote | 尝试写入 | 数据库拒绝；Agent阶段记录Schema/证据错误，不产生可发布结论 | review_aspects无非法行；stage_run/error_code与partial failure按影响记录 |
| TC-SEC-007 | P0 | FR-11 | API-EVD-01 | 机会/建议/报告结论存在 | supports/contradicts/context/limitation证据 | 查询证据 | 每个核心结论至少一条主证据；反驳和限制不被隐藏 | evidence_links同task；claim/evidence多态引用有效；is_primary满足门禁 |
| TC-SEC-008 | P0 | FR-04、FR-11 | API-DAT-04、API-RPT-01 | DS-US | 国家、平台、时间、商品/评论数、代理指标口径 | 查看质量和报告 | 数据范围在S05/S06一致；评论数不表述为销量 | report.data_scope_snapshot冻结数据集版本与limitations；数值与数据集统计一致 |
| TC-SEC-009 | P0 | FR-08、FR-09 | API-REC-01 | 无BOM/报价 | 成本相关建议 | 查询 | cost_impact_min/max为空，不生成伪精确金额；validation_method仍可提供 | 建议成本列为NULL；证据链不引用不存在报价 |
| TC-SEC-010 | P0 | FR-01、FR-12 | 全部API | 正常/失败请求 | 密码、评论全文、企业约束 | 检查响应与日志 | 错误只含脱敏message/details/request_id；admin诊断不默认返回全文 | audit_logs快照脱敏；无密码/API Key/完整Prompt；tenant和request_id存在 |

## 10. PostgreSQL V3结构与事务测试

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-DB-001 | P0 | FR-01 | API-ADM-02 | V3库 | role_code=user/admin/owner | 依次写入 | user/admin成功；owner失败 | `chk_users_role`生效；roles/user_roles表不存在 |
| TC-DB-002 | P0 | FR-01 | API-PRD-01 | 两租户存在 | T-A与T-B同SKU；T-A重复SKU | 写入 | 跨租户同SKU允许；同租户重复409/唯一键失败 | products租户+SKU唯一约束生效 |
| TC-DB-003 | P0 | FR-03 | API-PRD-07 | 草稿/确认画像 | confirmed但confirmed_by或confirmed_at为空 | 写入 | 拒绝不完整确认状态 | product_profile_versions CHECK与product+version唯一约束生效 |
| TC-DB-004 | P0 | FR-04 | API-DAT-01/02 | 数据集存在 | field_mapping对象而非数组；normalized_attributes数组而非对象 | 写入 | 422或数据库CHECK拒绝 | market_datasets.field_mapping、market_listings.normalized_attributes jsonb_typeof约束生效 |
| TC-DB-005 | P0 | FR-09、FR-11 | API-OPP-01、API-RPT-01 | 任务存在 | manufacturing_fit对象；sections对象 | 写入 | 拒绝错误顶层结构 | market_opportunities.manufacturing_fit和analysis_reports.sections必须为array |
| TC-DB-006 | P0 | FR-10 | 所有要求Idempotency-Key的写API | 同tenant | 同key同Body、同key不同Body、并发同key | 提交 | completed/failed同hash复用原状态与脱敏Envelope；不同hash 409 `IDEMPOTENCY_CONFLICT`；processing 409 `IDEMPOTENCY_IN_PROGRESS` | api_idempotency_records tenant+route+key唯一且request_hash稳定；认证接口无记录；领域实体、计费、恢复事件不重复 |
| TC-DB-007 | P0 | FR-10 | API-CFM-02 | 同task | 创建两条pending确认 | 并发插入 | 仅一条成功；另一条冲突 | user_confirmations pending partial unique生效 |
| TC-DB-008 | P0 | FR-10 | API-CFM-02 | confirmation和checkpoint不同task/tenant | 交叉引用 | 提交 | API拒绝；数据库/服务一致性检查阻断 | 无跨任务确认恢复；外键存在且业务同tenant断言通过 |
| TC-DB-009 | P0 | FR-05—FR-11 | 各结果查询API | 删除父任务/引用不存在对象 | 非法FK | 直接事务测试 | 非法外键写入失败；保留策略符合DDL | 35表FK无孤儿；cluster_members复合引用、model_run引用均有效 |
| TC-DB-010 | P0 | FR-06 | API-REV-01 | REV-EN | 重复review+aspect_index | 并发写 | 仅一条成功；结果稳定 | review_aspects唯一约束生效；无重复抽取业务行 |
| TC-DB-011 | P0 | FR-09 | API-OPP-01 | 任务存在 | score=-1/101、confidence=-0.1/1.1、price下界>上界 | 写入 | API422或数据库CHECK拒绝 | 市场机会评分、置信度、price_bands边界CHECK生效 |
| TC-DB-012 | P0 | FR-10、FR-11 | API-CFM-02、最终持久化 | 注入事务故障 | 回答已写但Outbox失败；报告已写但任务更新失败 | 分别执行 | 两组操作均整体回滚，不出现半提交 | confirmation+control event原子；task+report+evidence原子；行数与提交前一致 |
| TC-DB-013 | P0 | FR-12 | 无直接前端API | V3空库 | 执行DDL验证SQL | 建库成功 | furniscope核心表=35；所有表有注释；无默认业务用户/密码/API Key |
| TC-DB-014 | P1 | FR-01—FR-12 | 无 | V2.1带样例数据副本 | 官方迁移脚本 | 执行V2.1→V3 | 事务成功或整体回滚；先归档后删表 | 角色映射、JSONB合并、统一确认、归档计数、旧表残留均符合迁移说明 |

## 11. 页面交互测试

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-UI-001 | P0 | FR-01 | API-AUTH-01/03 | user/admin账号 | 两种角色 | 登录并检查导航 | user看到S01—S06；admin额外A01；无岗位选择与权限矩阵 | 只读操作仅产生必要审计；角色值仅两种 |
| TC-UI-002 | P0 | FR-03、FR-04、FR-10 | API-PRD-01—07、API-DAT-01—04、API-INS-01/02 | U-A | P-SOFA、DS-US | 完成S03四步向导 | 产品、资料、画像、数据和启动在单页完成；每步错误定位正确；冻结后跳S04 | 创建对象与页面显示ID一致；无重复提交 |
| TC-UI-003 | P0 | FR-10 | API-INS-03 | TASK-A运行 | 状态连续变化 | 前台观察轮询 | 立即查询；3秒轮询；5次无变化退避5秒；后台暂停；终态停止 | 轮询不产生业务写入；request_id可追踪 |
| TC-UI-004 | P0 | FR-10 | API-INS-03 | TASK-A | I00—I19运行记录 | 查看S04主界面和技术详情 | 主界面只显示五阶段；技术抽屉才显示stage_runs/partial_failures/checkpoint/request_id；无user重试按钮 | 数据库不因打开抽屉发生控制事件 |
| TC-UI-005 | P0 | FR-10 | API-CFM-01/02 | pending确认 | 推荐项和多个互斥选项 | 打开、查看证据、提交 | 不预选并自动提交；202只提示已受理；随后继续轮询 | 回答和Outbox各一条；前端重复点击不重复写 |
| TC-UI-006 | P0 | FR-05—FR-09 | API-INS-04、API-CMP-01、API-REV-01/02、API-OPP-01、API-EVD-01、API-RPT-01 | 结果已生成 | TASK-A | 切换S05六个Tab | 总览/竞品/评论需求/价格/机会/证据按需加载；局部失败不清空其他Tab；空态区分处理中/不足/筛选无结果 | 只读；筛选结果均限定同task/tenant |
| TC-UI-007 | P0 | FR-11 | API-RPT-01、API-REC-01、API-OPP-01、API-EVD-01 | 报告完成 | TASK-A | 查看S06并下钻 | 首屏同时展示结论、分数和置信度；建议、制造适配、风险、待验证、证据完整；无发布/导出/Listing | 报告快照不被页面操作修改；证据读取有审计 |
| TC-UI-008 | P0 | FR-01、FR-11 | API-RPT-01 | report_uuid=null/跨租户/不存在 | 三种路由 | 直接访问S06 | 未就绪回S04；无权或不存在回S02；不泄露报告正文 | 无报告写入；拒绝审计正确 |
| TC-UI-009 | P0 | FR-10 | API-INS-03 | partial_failures存在 | 两批评论失败 | 查看S04/S05/S06 | 三页均披露影响和限制；不把partial_succeeded渲染为完整成功 | 报告partial_failures_snapshot与任务失败记录一致 |
| TC-UI-010 | P1 | FR-12 | API-ADM-01—08 | ADM | user、路由、Prompt、失败任务 | 使用A01各Tab | admin可管理配置和诊断；不能代答；写操作二次确认和审计 | user/config/control事件按API写入；不出现旧岗位Token或Agent角色 |

## 12. Admin与Model Router P1契约测试

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-ADM-001 | P1 | FR-12 | API-ADM-03/04 | ADM | 合法/非法timeout、retry、compute_config | 查询并更新路由 | 合法配置保存；非法422；响应不含Key | model_route_configs.task_type唯一、JSON对象、数值CHECK；audit_logs存在 |
| TC-ADM-002 | P1 | FR-12 | API-ADM-05/06 | ADM | Prompt code/version和JSON Schema | 创建同版本两次 | 首次201；重复409；active版本不可原地改写 | prompt_templates code+version唯一；历史版本保留 |
| TC-ADM-003 | P1 | FR-12 | API-ADM-07 | ADM | TASK-A | 查询诊断 | 返回脱敏stage/model/control事件和版本束；无完整评论/Prompt/Key | 只读；audit记录诊断访问范围 |
| TC-ADM-004 | P1 | FR-10、FR-12 | API-ADM-08 | ADM、失败任务 | auto_retry+safe checkpoint | 提交 | 202控制事件pending；Supervisor消费；不能指定任意不存在Stage | workflow_control_events事件类型/checkpoint/stage合法且幂等 |
| TC-ADM-005 | P1 | FR-10、FR-12 | API-ADM-08 | pending user_confirmation | safe_stop/auto_retry | 提交 | auto_retry拒绝绕过确认；safe_stop按安全策略处理且不代答 | confirmation仍pending或按安全停止变cancelled；responded_by不为admin |

## 13. 非功能、性能与兼容测试

| 编号 | 优先级 | 关联需求 | 关联API | 前置条件 | 测试数据 | 步骤 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|---|---|---|
| TC-NFR-001 | P0 | FR-01—FR-11 | 普通GET API | 标准Demo数据 | 100并发读 | 压测列表/详情/状态 | 普通查询P95≤500ms；INS-03 P95≤300ms；错误率符合Demo门槛 | 无锁等待异常、连接泄漏或慢查询失控 |
| TC-NFR-002 | P0 | FR-03、FR-04 | API-PRD-05、API-DAT-02 | 对象存储/队列正常 | 合法最大文件集 | 提交异步任务 | ≤2秒返回202；HTTP不等待模型完成 | 异步任务和文件引用已提交；无半写 |
| TC-NFR-003 | P1 | FR-06、FR-10 | API-INS-02/03 | 1000条有效评论 | 固定模型桩 | 执行完整分析 | 目标≤5分钟；Map/Reduce并发受配置限制；结果确定可复算 | stage耗时、Token、失败率、批次和成本记录完整 |
| TC-NFR-004 | P0 | FR-10 | 全部写API | 网络重放 | 相同Idempotency-Key | 重放请求 | 不重复创建、计费、恢复或报告 | 所有业务唯一键与行数保持预期 |
| TC-NFR-005 | P1兼容 | FR-01—FR-12 | V2废弃写路径 | 兼容路由开启 | 旧竞品确认、专家复核、Listing、导出路径 | 调用 | 410 `API_DEPRECATED`或按V3策略拒绝；不双写旧实体 | 不存在旧核心表；V3业务表无旧语义字段写入 |

## 14. 需求覆盖矩阵

| 需求 | 核心覆盖用例 |
|---|---|
| FR-01 身份、租户与平台管理 | TC-AUTH-001—008、TC-DB-001/002、TC-UI-001 |
| FR-02 企业能力上下文 | TC-WIZ-004、TC-STG-008 |
| FR-03 产品资料与多模态理解 | TC-WIZ-001—004、TC-SEC-001/002 |
| FR-04 市场数据与质量检查 | TC-WIZ-005—007、TC-SEC-008 |
| FR-05 竞品识别与可比性 | TC-STG-004、TC-CFM-002、TC-WF-002 |
| FR-06 家具评论需求洞察 | TC-STG-005、TC-WF-003、TC-SEC-005/006 |
| FR-07 市场、价格与竞争洞察 | TC-STG-006、TC-WF-004 |
| FR-08 需求缺口与工程建议 | TC-STG-008、TC-CFM-003、TC-SEC-009 |
| FR-09 企业适配与机会评分 | TC-STG-007/008、TC-DB-011 |
| FR-10 自主任务、确认与恢复 | TC-E2E-001、TC-STG-001/002、TC-CFM-001—012、TC-WF-001—010 |
| FR-11 综合报告与证据追溯 | TC-STG-009/010、TC-SEC-005—009、TC-UI-007 |
| FR-12 模型、Prompt与运行诊断 | TC-SEC-003/004/010、TC-ADM-001—005 |

## 15. P0验收门槛

满足以下全部条件才判定核心产品验收通过：

1. 本文所有P0用例100%通过；不得以“仅Demo”豁免数据隔离、证据、幂等和恢复失败。
2. TC-E2E-001至少连续执行3次成功，且由同一个user独立完成，无其他业务角色介入。
3. 四类user_confirmation至少各覆盖1次；回答幂等、过期、Checkpoint冲突、跨租户和Worker恢复全部通过。
4. 外部只出现五阶段；数据库和API中不存在owner/product_rd/market_ops/sales角色值。
5. 核心结论100%可下钻至少一条主证据；评论Span非法率为0；无证据数值结论为0。
6. 部分失败必须在任务、综合洞察和报告中一致披露；不得把partial结果标为完整成功。
7. API Key、明文密码、完整Prompt在数据库、日志和前端响应中的检测命中数均为0。
8. PostgreSQL基线及必需增量齐全；关键FK、UNIQUE、CHECK、JSON结构、RLS、连接重用和事务原子性测试全部通过。
9. 普通API和状态API达到NFR目标；异步受理≤2秒；无P0级安全或数据一致性缺陷。
10. S01—S06主路径可在3—5分钟答辩中完整演示；A01不作为user业务闭环依赖。

## 16. 自动化优先级

| 自动化等级 | 范围 | 推荐实现 |
|---|---|---|
| A0 每次提交 | API契约、角色/租户、幂等、确认、数据库约束、证据Span | pytest + FastAPI TestClient/httpx + Testcontainers PostgreSQL/Redis；固定Model Router桩 |
| A1 每日/合并前 | 单user E2E、五阶段、Map/Reduce部分失败、Worker重启、Outbox恢复 | Playwright + LangGraph测试图 + 可控故障注入 |
| A2 发布前 | 文件安全、Key泄漏扫描、迁移、并发、性能和恢复演练 | 对象存储测试桶、secret scanner、pgTAP/SQL、k6/Locust、Worker kill测试 |
| 人工探索 | Figma/React视觉、可信文案、空态、答辩路径 | 浏览器多分辨率检查与业务专家证据抽样 |

A0失败禁止合并；A1核心恢复用例失败禁止部署Demo；A2发现租户泄漏、密钥泄漏或迁移数据丢失时立即阻断发布。

## 17. 后续跨文档依赖

1. FastAPI实现完成后需将每个错误码、Pydantic Schema和数据库事务测试链接回本文用例编号。
2. LangGraph测试夹具需提供I00—I19节点故障注入、官方Checkpointer重启和Outbox重复消费能力。
3. React E2E需按S01—S06/A01建立稳定data-testid，并验证技术详情抽屉不暴露敏感信息。
4. 部署文档需补充测试环境密钥注入、对象存储病毒扫描、PostgreSQL备份恢复和Redis锁清理步骤。
5. 旧测试用例V1在V2验收完成前保留；完成后移入归档，不覆盖或删除原文件。
6. 若API V3新增价格专用下钻或通用系统配置接口，须先更新数据字典和交互文档，再增加对应测试。

## 18. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-08 | 五岗位、多人工复核、Listing/导出扩展形态测试集 |
| V2.0 | 2026-08-09 | 按超级AI员工V3基线重构为user/admin、单user闭环、统一确认、五阶段及PostgreSQL V3测试集 |
| V2.1 | 2026-09-29 | 增加决赛五项市场智能、成本约束定价与官方政策源契约测试 |
| V2.2 | 2026-10-04 | 增加标准数据、企业独立训练与发布、RLS、策略快照/反馈、真实HTTP及页面闭环验收；补可复现启动脚本和证据目录 |

### 18.1 决赛增量 P0 用例

| 用例 | 核心断言 |
|---|---|
| TC-MKT-001 五能力总览 | 只返回当前租户的数据集、任务、竞品、评论和政策事实；无数据时返回明确状态而非样例 |
| TC-MKT-002 机会 V3 | 五个市场因子来自独立证据；缺失因子按可用权重重归一；企业显式能力冲突降低推荐级别 |
| TC-MKT-003 评论观点 V2 | 模型输出可拆多观点；每个 `evidence_quote` 与 Unicode Span 必须逐字匹配原评论；模型失败走规则降级 |
| TC-MKT-004 成本约束定价 | 建议售价不低于目标毛利价格底线；成本高于市场上四分位时返回 `cost_above_market` |
| TC-MKT-005 弹性拒绝 | 少于 3 个有效价格/需求变化样本时不输出弹性数值 |
| TC-MKT-006 官方政策源 | 默认源安装幂等；只解析 CPSC/Federal Register 返回；重复内容不重复告警 |
| TC-MKT-007 评论语义聚类 | 同一 Taxonomy 内相近观点合并、远离观点拆簇；代表证据按抽取置信度和中心余弦相似度排序；Embedding 或聚类依赖失败时回退并留下失败模型记录 |
| TC-UI-008 五能力工作台 | 1440×1000与390×844无整页横向溢出；五个分段入口、数据范围、缺口与操作均可见；宽表有移动摘要 |

## 19. 本次变更摘要

- 删除五岗位权限组合、跨部门审批、固定竞品复核和专家复核用例。
- 增加user/admin静态权限、单user端到端和七个企业一级入口测试。
- 完整覆盖user_confirmation生成、查询、回答、幂等、过期、Checkpoint冲突与Command恢复。
- 增加自动重试、降级、部分失败、Worker重启、Outbox消费和最终事务一致性测试。
- 增加租户隔离、文件安全、模型Schema、证据Span、API Key不落库和脱敏审计测试。
- 增加PostgreSQL V3的35表、FK、UNIQUE、CHECK、JSONB结构、迁移与事务断言。
- Listing、业务执行表格、验证任务和多人评审明确退出P0范围；冻结报告PDF纳入一致性测试。
- 给出P0硬性验收门槛和A0/A1/A2自动化优先级。

## 20. 对话、记忆与知识库生命周期验收

| 用例 | 核心断言 | 自动化位置 |
|---|---|---|
| TC-CTX-001 原子Turn | 完成后恰有用户/助手消息对、上下文快照和响应UUID；答案只在提交后通过SSE释放 | `tests/test_context_lifecycle.py` |
| TC-CTX-002 幂等重放 | 相同`Idempotency-Key`不重复生成或写消息；不同请求复用键返回冲突 | 同上 |
| TC-CTX-003 记忆版本 | 新候选不覆盖confirmed；确认后旧值为superseded；画像依赖变化进入invalidated；资源操作使用`memory_uuid` | 同上 |
| TC-CTX-004 安全遗忘 | 自然语言只返回带目标UUID的确认动作；DELETE确认后才归档 | `tests/test_customer_memory.py`、Playwright |
| TC-CTX-005 Context快照 | 显式绑定画像、数据集、知识库、记忆和任务；预览返回Token、裁剪来源、服务端语义状态、冲突和解析日志 | `tests/test_context_lifecycle.py` |
| TC-CTX-006 Citation | 来源类型、ID、版本、定位器、摘录和分数真实落库并可按UUID解析 | 同上 |
| TC-CTX-007 知识索引 | XLSX解析、分页切片、Embedding、Rerank、ready状态和检索Citation完整 | 同上 |
| TC-CTX-008 SSE协议 | 仅允许标准九类事件（含`tool_result`）；稳定事件ID、自动重连去重和幂等重放；无`reasoning/thinking/token`私有协议 | `frontend/tests/e2e/plane-scoped-run.spec.js`、`frontend/src/api.js` |
| TC-CTX-009 前端确认 | 对话页只保留知识库选择器和管理入口；绑定写入Context；不存在三档记忆写入控件；Citation与已有记忆管理保持可用 | 同上 |
| TC-CTX-010 响应式 | 1440×1000与390×844逐一级入口检查页面宽度；记忆抽屉避开顶栏/底栏，消息工具不遮挡正文 | 同上及验收截图 |
| TC-CTX-011 迁移/RLS | v3.23—v3.31 fresh、upgrade、repeat、存量哨兵、启动隔离、RBAC、审计链、cell placement、16分区侧索引和受控问数表探针全部通过 | `scripts/verify_enterprise_migrations.py` |
| TC-CTX-012 优先级/冲突 | 当前表达>确认事实>确认记忆>工作台历史>任务报告>知识文档>模型推断；冲突值和选择原因入快照 | `tests/test_context_lifecycle.py` |
| TC-CTX-013 隐私边界 | restricted记忆不进Prompt；user私有知识库不被同租户其他用户绑定或检索；跨租户始终拒绝 | `tests/test_context_lifecycle.py`、`tests/test_tenant_boundaries.py` |
| TC-CTX-014 固定效果集 | 指代、SKU纠错、市场切换、多市场比较、暂停/恢复、临时条件不误写、知识指令不越权 | `scripts/evaluate_memory_agent.py`、`tests/fixtures/memory_agent_eval.json` |
| TC-CTX-015 权限与审计 | 企业成员默认角色、权限拒绝、最后一个owner保护、审计哈希链和业务身份不可篡改 | `tests/test_auth_postgres_api.py` |
| TC-CTX-016 备份恢复 | 全库Manifest/SHA/审计链头复核；单租户父表导出、对象SHA、空库恢复、IDENTITY推进和分区不重复导出 | `scripts/backup_postgres.sh`、`scripts/restore_postgres.sh`、`scripts/tenant_backup.py` |
| TC-CTX-017 Cell迁移 | 冻结写栅栏、在途写入排空、源目标行数/内容摘要一致、路由代际切换、失败回滚 | `scripts/migrate_tenant_cell.py`、`tests/test_tenant_boundaries.py` |
| TC-CTX-018 查询优化 | 16个Hash分区、FORCE RLS、侧索引触发同步、租户包恢复重算搜索向量、无pgvector降级 | `migrations/v3_30_tenant_query_optimization.sql`、`tests/test_context_lifecycle.py` |
| TC-CTX-019 知识库独立管理 | 创建/编辑/归档、tenant/user权限、上传/删除、索引刷新、版本预览、同类型版本上传、历史回溯和工作台绑定均可独立完成 | `frontend/tests/e2e/knowledge-base-center.spec.js`、`tests/test_context_lifecycle.py` |
| TC-CTX-020 自动记忆关闭 | 配置与默认策略均为false；统一Turn和旧兼容聊天入口均不创建候选记忆；已有记忆读取与管理不受影响 | `tests/test_context_lifecycle.py`、`backend/furniscope_api/routes/workspaces.py` |

### 20.1 智能问数、RAG与工具编排增量

| 用例 | 核心断言 | 自动化位置 |
|---|---|---|
| TC-AQT-001 问数意图 | 历史销量/库存进入`data_query`；未来销量进入`forecast`；知识问题进入`rag` | `tests/test_data_query.py`、`scripts/evaluate_agent_tools.py` |
| TC-AQT-002 QueryPlan白名单 | 只接受登记指标、粒度、维度和过滤器；销量/库存不能混查；`raw_sql`等未知字段拒绝 | `tests/test_data_query.py` |
| TC-AQT-003 事实投影 | 已确认canonical版本SHA复核后幂等投影；重复执行不重复事实；投影SHA稳定 | 同上（真实PostgreSQL） |
| TC-AQT-004 确定性结果 | 数值来自固定SQL表达式；相对时间基于数据最大日期；版本、过滤器、限制和结果SHA完整 | 同上 |
| TC-AQT-005 审计与Citation | 查询写入`data_query_executions`，原子Turn保存`tool_results`和可解析Citation | 同上 |
| TC-AQT-006 租户/RBAC | 无`dataset.read`拒绝；其他租户不能读取事实、投影或查询审计 | `tests/test_data_query.py`、`tests/test_tenant_boundaries.py` |
| TC-AQT-007 RAG拒答 | 自动选库只选择可访问ready资源；无匹配或低于阈值时拒答；页码和文档版本可追溯 | `tests/test_context_lifecycle.py`、`scripts/evaluate_agent_tools.py` |
| TC-AQT-008 服务端动作 | 前端先提交Turn；问数不创建任务；只有服务端`run_workflow`动作可启动工作流 | `frontend/tests/e2e/data-query-rag-orchestration.spec.js`、`plane-scoped-run.spec.js` |
| TC-AQT-009 页面恢复 | 问数表格/图表刷新后由消息`metadata.tool_results`恢复；知识库管理位于独立路由并支持版本与索引操作 | 同上、`frontend/src/planeSession.js`、`knowledge-base-center.spec.js` |
| TC-AQT-010 NL2SQL边界 | 当前接口不接受SQL；不安全SQL生成率为0；未来能力需只读AST、白名单、RLS、timeout、LIMIT和审计门禁 | `tests/test_data_query.py`、`scripts/evaluate_agent_tools.py` |

2026-10-06历史实测结果：

| 范围 | 结果 |
|---|---|
| Python全量，真实PostgreSQL与Redis | 198项收集，`196 passed, 2 skipped`；跳过仅为旧V4预测制品缺失 |
| 生命周期与客户记忆专项 | 8项纳入全量并通过 |
| 固定记忆Agent评测 | 10/10场景通过；产品/市场识别、指代、意图、记忆写入及注入防护均为1.0，记忆误写率0 |
| 固定Agent工具评测 | 意图路由、QueryPlan精确匹配和RAG阈值决策均为1.0；不安全SQL生成率0 |
| Playwright合同 | `17 passed, 8 skipped`；跳过为需显式独立后端的7条企业实测和1条旧全栈入口 |
| 工作台关键场景 | 4项纳入全量并通过 |
| 前端build / Sites | 通过 / `5 passed`；保留既有bundle大于500kB提示 |
| 页面视觉 | 当日问数/知识库验收页面通过；2026-10-07全入口复盘发现首页、工作日记和市场宽表问题，不能外推为全系统通过 |
| v3.15—v3.31迁移 | fresh、upgrade、repeat、存量哨兵、RBAC、审计链、cell placement、16分区侧索引、4张问数租户表FORCE RLS及10项指标通过 |

执行命令：

```bash
PYTHONPATH=.:backend \
FURNISCOPE_TEST_DATABASE_URL=postgresql://<account>@localhost/<isolated_db> \
FURNISCOPE_CONTEXT_TEST_DATABASE_URL=postgresql://<account>@localhost/<isolated_db> \
FURNISCOPE_TEST_REDIS_URL=redis://127.0.0.1:<isolated_port>/<isolated_db_no> \
python -m pytest -q --tb=short

PYTHONPATH=.:backend \
python scripts/evaluate_memory_agent.py \
  --fixture tests/fixtures/memory_agent_eval.json \
  --output artifacts/context-lifecycle-20261006/memory-agent-eval.json

PYTHONPATH=.:backend \
python scripts/evaluate_agent_tools.py \
  --fixture tests/fixtures/agent_tool_eval.json \
  --output artifacts/agent-tools-20261006/evaluation-final.json

cd frontend
npm run build
npm run test:sites
npm run test:e2e:contract
```

现行迁移摘要及Python/Playwright JUnit保存在
[多租户隔离验收目录](../../artifacts/tenant-isolation-20261006/)；固定记忆评测及
桌面/390px截图保存在
[生命周期验收目录](../../artifacts/context-lifecycle-20261006/)；受控问数、RAG和
工具编排结构化结果保存在
[Agent工具验收目录](../../artifacts/agent-tools-20261006/)。

## 21. 产品主档与批量导入验收（2026-10-07）

| 用例 | 核心断言 | 自动化位置 |
|---|---|---|
| TC-PRD-IMP-001 动态模板 | 五个工作表；正式区无示例；版本、文件SHA、Schema SHA一致；5组下拉字典 | `tests/test_product_imports.py` |
| TC-PRD-IMP-002 严格标准化 | 必填、文本SKU、非负数、枚举、尺寸/重量/币种联动；未知值不猜测；非sofa警告 | 同上 |
| TC-PRD-IMP-003 SKU碰撞 | 文件内大小写/空格碰撞全部阻断；库内重复在仅新增模式阻断 | 同上 |
| TC-PRD-IMP-004 错误Excel | 原行号、原值、错误码、字段、详情、修复建议、警告和标准化预览完整 | 同上 |
| TC-PRD-IMP-005 幂等与SHA | 相同文件+配置+键重放同任务；不同请求冲突；陈旧预览SHA拒绝 | 同上，真实PostgreSQL |
| TC-PRD-IMP-006 事务写入 | 主档、画像草稿、SPU关系、SKU别名同时成功；阻断任务不能留下产品 | 同上，真实PostgreSQL |
| TC-PRD-IMP-007 批量更新 | 只有`upsert`可更新；名称/品类生效；品类变化令画像回到draft | 同上，真实PostgreSQL |
| TC-PRD-IMP-008 行修正/恢复 | 修正一行后重算跨行冲突和SHA；任务从blocked变ready；取消后recent可恢复状态 | 同上，真实PostgreSQL |
| TC-PRD-IMP-009 租户与审计 | 其他租户按404拒绝；预检、提交、编辑关系和归档进入审计链 | 同上，真实PostgreSQL |
| TC-PRD-010 编辑与归档 | ETag修改SKU/品类/说明；说明可显式清空；归档后列表/详情不可见 | 同上及`test_auth_postgres_api.py` |
| TC-PRD-011 组合关系 | SPU、variant、bundle、BOM多关系保存；角色和正数数量受控 | `tests/test_product_imports.py` |
| TC-PRD-012 库存摘要 | 两站点已确认库存聚合正确；返回来源版本、日期、导入状态和`is_realtime=false` | 同上，真实PostgreSQL |
| TC-PRD-IMP-013 迁移 | v3.32在PostgreSQL 16首跑与重复执行通过；四表、RLS、复合外键和唯一比较键有效 | 隔离数据库迁移命令 |
| TC-PRD-IMP-014 性能 | 1万行解析核心小于12秒；真实服务预检小于20秒；批量每1000行落库 | `tests/test_product_imports.py` |

本轮实测：

```text
tests/test_product_imports.py
6 passed in 9.86s
10,000行解析核心 1.56s
含10,000行真实预检的完整HTTP生命周期 2.85s

tests/test_auth_postgres_api.py::
  test_product_create_idempotent_replay_list_and_admin_inheritance
1 passed
```

测试使用独立`furniscope_product_test_20261007`数据库和锁定依赖的Python 3.12临时环境。
已知警告为Starlette TestClient对`httpx`兼容层的弃用提示及第三方SWIG类型提示，不影响
业务断言；升级测试客户端依赖时应单独消除。

## 22. 系统复盘整改回归（2026-10-07）

上位关闭台账：[16_FurniScope_系统功能与业务流程复盘V1.md](./16_FurniScope_系统功能与业务流程复盘V1.md)。

| 用例 | 严重度 | 必须证明的跨层契约 |
|---|---:|---|
| TC-REV-001 报告冻结值 | P0 | 服务端未提供价格/成本时前端显示“待核算”；刷新、构建版本或PDF不能改变报告金额 |
| TC-REV-002 成本口径 | P0 | `factory_price`、`unit_cost`、`landed_cost`独立；只有显式成本口径进入毛利计算 |
| TC-REV-003 多币种 | P0 | 同一价格分布只能有一个币种；多币种返回阻断状态，不生成价格带 |
| TC-REV-004 快速追加覆盖 | P1 | 页面确认、请求参数、合并预览和后端覆盖行为一致 |
| TC-REV-005 SKU改名 | P1 | 修改SKU后产品、别名、销量目录和库存摘要仍指向同一产品身份 |
| TC-REV-006 报告归档 | P1 | UI承诺归档任务时，报告、任务、首页和工作日记在同一事务后都不可见 |
| TC-REV-007 企业机会排序 | P1 | 报告主机会来自冻结推荐级别、企业门控和adjusted_score，不按base_score重新选择 |
| TC-REV-008 管理员多账号 | P1 | 同租户多个账号逐一展示；关闭租户后全部停用；恢复时按关闭前状态恢复 |
| TC-REV-009 首页互斥指标 | P1 | pending、failed和conflicted集合互斥，同一任务不重复计数 |
| TC-REV-010 全入口响应式 | P1 | E01—E07、工作日记及A01在390×844下`documentElement.scrollWidth===innerWidth`，关键字段不被裁切 |
| TC-REV-011 弹性代理披露 | P1 | 累计评论数不得标成销量需求弹性；没有同周期销量时不输出“价格弹性” |
| TC-REV-012 合规范围 | P1 | 切换数据集时明确区分租户级政策监控和当前国家/品类匹配结果 |
| TC-REV-013 报告分页 | P2 | 超过100份报告仍可分页访问，产品和国家筛选基于全量维度 |
| TC-REV-014 通知渠道UI | P2 | API交付状态与可达页面一致；创建、启停、测试和投递记录可由企业用户完成 |

本次运行页面扫描：

| 范围 | 结果 |
|---|---|
| 1440×1000 | 企业8入口、工作日记、普通用户Admin 403及管理员页均无整页横向溢出 |
| 390×844 | 同一组页面均满足`documentWidth===innerWidth`；首页单列、工作日记/市场摘要卡片、Admin 2×2 KPI和账号卡片可见 |
| 运行错误 | 20个状态均未发现console error、page error或failed request |
| 证据 | `artifacts/system-review-20261007-fixes/browser-scan.json`及同目录20张截图 |

扫描使用全新隔离数据库和当前根SQL/API。宽度断言证明本轮覆盖页面不存在整页横向
溢出，但不替代真实设备、辅助技术和长文本/大数据量专项测试。

本次基线验证：

| 命令范围 | 结果 |
|---|---|
| 全量Python，真实PostgreSQL + 独立Redis | 207项：`205 passed, 2 skipped`，0 failure/error |
| Python跳过边界 | 缺少旧V4 `daily.pkl`和`forecast_assets/state`；不影响tenant-xgb-v2及本轮37项整改 |
| Playwright合同 | `20 passed, 8 skipped`；7项需显式企业夹具，1项需旧全栈入口 |
| 前端生产构建 / Sites | 4663模块构建通过 / `5 passed`；主JS 279.84 kB，gzip 84.71 kB，无500 kB警告 |
| 数据库迁移 | v3.33/v3.34 fresh、upgrade、repeat、启动隔离及SKU事实身份检查通过 |
| API健康 | live、ready、OpenAPI均HTTP 200 |

日志、JUnit、迁移摘要、截图、结构化扫描和SHA256清单位于
`artifacts/system-review-20261007-fixes/`。TC-REV-001—014与37项关闭台账均已转绿；
工程准入通过不代表真实企业精度、经营收益或生产容量已经得到实证。
