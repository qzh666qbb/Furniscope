# FurniScope 企业决策与标准数据 API 实现说明 V1

日期：2026-10-05。上位设计：[企业决策与数据闭环总体设计](../项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md)。本文描述本地实现契约，不代表已部署或真实企业精度验收。

## 1. 身份与版本

所有接口要求普通企业 `user` 的有效 JWT，租户从服务端复核的身份解析；不接收客户端 `tenant_id`。认证后 SQLAlchemy 事务绑定 `furniscope_tenant` 角色，提交后的新事务自动重新绑定。后台训练同样绑定租户。管理员业务入口与普通用户分离。

成功响应沿用 `SuccessEnvelope.data`；唯一例外是 `/audit` 返回可下载的原始 JSON 文档。HTTP 4xx/5xx 不作为空集合显示。

本域显式使用 `expected_version`（策略）、`expected_revision`（反馈）及 `preview_sha256`（数据确认）进行并发校验，是原通用 `If-Match` 约定的新增特例。训练创建还要求 `Idempotency-Key`。相同键相同输入返回原任务；键已用于不同请求返回409。

## 2. 企业策略与处理反馈

| 方法与路径（前缀 `/api/v1/enterprise`） | 输入与结果 |
|---|---|
| `GET /profile` | 企业画像、已确认能力、版本与确认信息 |
| `PUT /profile` | `{profile, capabilities}`；保留完整未修改字段，保存即表示企业用户确认事实 |
| `GET /fact-vocabulary` | 版本化共用词表`{version,groups}`；每组含产品属性代码、能力类型、中文选项 |
| `GET /opportunity-policy` | `{current, templates, calibration}`；默认策略版本0，`calibration=uncalibrated_heuristic` |
| `PUT /opportunity-policy` | 新增不可变版本；`expected_version,name,objective,weights,fit_strength,required_capabilities` |
| `GET /opportunities/{id}/feedback` | `{current, history}`；最新版本在前 |
| `PUT /opportunities/{id}/feedback` | `{expected_revision,status,reason}`；写入不可变事件 |
| `GET /opportunity-feedback/export?after_id=0&limit=500` | `{format_version,label_semantics,items,next_after_id}`；limit最大1000，游标为空表示结束 |
| `GET /opportunities/{id}/outcomes` | `{current,history,semantics}`；实施修订最新在前 |
| `PUT /opportunities/{id}/outcomes` | 新增不可变实施和观察修订，见下方契约 |
| `GET /opportunity-ranking/export?after_task_id=0&limit=50&as_of=…` | `opportunity-ranking-v2`；按任务整组分页，limit最大100；首批截点必须用于所有后续页 |

五个权重键：`demand_heat,demand_growth,unmet_need,competition_space,profit_space`；非负有限数、合计1。`fit_strength`范围0—1。`objective`为`balanced/growth/profit/custom`。必要能力条目使用精确`capability_type/capability_code`，可选`taxonomy_code`限定需求主题；并非执行脚本。

反馈状态：`accepted/rejected/pending_validation`；原因1—2000字。采纳表示用户决策，不表示已实施或产生收益。旧事件保留评分、策略及企业事实快照；跨企业机会返回404，版本冲突返回409。页面可完整分页导出反馈用于未来排序评测，本轮没有训练机会排序模型。

实施输入：`expected_revision,accepted_feedback_id,status,evidence`，可选`implementation_start,implementation_end,observation_start,observation_end,result_label,financials,data_version_uuid,source_sku`。状态`planned/in_progress/completed/abandoned`，结果`achieved/not_achieved/inconclusive`。须关联该机会最新且状态为accepted的反馈；过期采纳或实施版本返回409。计划不填实际日期，实施中须有开始但不能有结束；完成须有起止和观察区间，观察不得早于实施，日期不得在未来。结果标签仅用于已完成，依据1—4000字，所有字段严格校验。

`financials={revenue,cost,currency,basis}`由企业报告，收入/费用非负有限数、三位币种、口径与凭据说明必填；不计算因果收益。`data_version_uuid`和`source_sku`须同时提供，并有完整观察区间。销量版本须本企业已确认sales类型，SHA真实匹配；来源SKU唯一映射到机会产品，站点等于机会国家，观察逐日覆盖完整。`sales_snapshot`返回汇总件数、日期、毛/净口径、补零/非正常在售天数及版本和映射SHA血缘；不返回文件系统路径。跨企业404，覆盖/映射/口径错误422，文件篡改409。

新机会由数据库生成不可变`decision_snapshot`，含`opportunity-features-v2`、记录时点、任务与产品/企业事实，以及完整因子、权重和策略。同任务评分重试复用原结果；新分析才重新评分。新反馈的`score_snapshot.feature_capture`引用该快照，并保留原顶层评分字段兼容旧调用；旧机会新反馈标注`legacy_not_reconstructable`，历史反馈不回填。

排序导出返回`as_of,label_semantics,split_contract,groups,next_after_task_id`。`as_of`必须带时区且不能在未来；每组`task_id,task_uuid,group_time,candidates`，每个candidate包含`features,decision,outcome,labels,issues`。`features=null`表示旧特征不可重建。`labels`给出决策/结果各自的二元标签、`*_eligible`和`*_available_at`；待验证/未实施/未完成/暂不能判断不生成结果标签。回溯实施、已被更新的采纳关联及历史缺特征会明确排除。导出包含未标注候选，不能将缺标签视为0；按任务时间划分后还须排除在验证开始后才记录的训练标签。

任务创建由`AnalysisTaskRepository.create`冻结策略、企业画像和产品事实。`market_opportunities`保存`market_score/adjusted_score/policy_snapshot`；`base_score`兼容存放修正分。`manufacturing_fit`仍是数组，新记录的首项含`status,signals,fit_score,policy_version`。`signals`的`pass/blocked/unknown`逐项解释事实依据；硬条件blocked对应`capability_gap`，建议优先级low。没有具体制造要求时保留unknown。

成本约束仅比较已确认的`unit_cost`；`factory_price`是出厂报价，不能替代成本。缺少成本事实时返回unknown，同币种也不例外。

产品中心使用既有产品接口完成确认：

- `GET /api/v1/products/{id}`增加`profile_version_id,profile_status,fact_suggestions`；候选包含标准代码、源属性、原文和来源定位；候选状态固定`suggested`，不产生事实。
- `PATCH /api/v1/products/{id}`要求`If-Match`；四类`*_codes`须为词表内不重复的字符串数组。人工保存附`source_locator.review_note`，确认人和时间由服务端赋值；文档/图像/推断不能在PATCH中直接声明已确认。修改属性后进入`profile_pending`，已确认画像复制为新草稿，保留其他属性。
- `POST /api/v1/products/{id}/profile:confirm`要求`If-Match`和`Idempotency-Key`，输入`profile_version_id,confirmed_attribute_codes`。冲突须先逐项修正，不允许通过该接口掩盖冲突；只允许确认当前画像，禁止把历史版本设为当前，所有属性均确认后启用分析。
- 旧版模型自动确认、没有`source_locator.confirmed_by`的文档/图像/推断事实不能用于新企业适配判断。新任务快照同时记录来源定位；已有任务保持原快照。

## 3. 原文件、标准数据与清洗

| 方法与路径（前缀 `/api/v1/forecast`） | 契约 |
|---|---|
| `POST /data-imports` | multipart字段`file`；CSV/XLSX/JSON；返回版本UUID、原文件SHA、列名及规则映射建议 |
| `GET /data-imports?limit=20` | 本企业最近版本，最大100 |
| `GET /data-imports/{uuid}` | 元数据、质量与前20条标准样本，不返回服务器存储路径 |
| `POST /data-imports/{uuid}/mapping-suggestion` | 仅发送列名给已配置模型；返回建议和`model_status`，不自动应用规则 |
| `POST /data-imports/{uuid}/preflight` | `{rules,auxiliary_versions?:{control:UUID,inventory:UUID}}`；确定性清洗、跨表对账、质量报告、标准文件SHA、样本 |
| `POST /data-imports/{uuid}/confirm` | `{preview_sha256}`；确认不可变版本，旧预检摘要返回409 |
| `POST /data-imports/{uuid}/revisions` | 复用原文件创建新清洗版本，保存父版本引用 |
| `GET /data-imports/{uuid}/audit` | 下载完整规则、标准记录、`source_rows`、质量和错误；鉴权+租户保护，`Cache-Control: private, no-store` |
| `GET /import-templates` | 本企业每个名称的最新模板修订，含映射和规则 |
| `POST /import-templates` | `{name,data_version_uuid,expected_revision}`；从已确认版本保存；首次修订传0，相同名称并发冲突409 |
| `POST /data-imports/{uuid}/apply-template` | `{template_uuid}`；检查列契约、保存模板快照、清除旧预检；确认版本409，跨租户404 |

规则示例：

```json
{
  "rules": {
    "mapping": {"date":"订单日期","sku":"商品编码","site":"站点","sales":"销量"},
    "kind":"sales",
    "grain":"daily",
    "sales_basis":"gross_units",
    "date_format":"%Y-%m-%d",
    "default_site":null,
    "missing_dates":"unknown",
    "complete_export_confirmed":false
  }
}
```

`kind=sales/inventory`；最小映射`date,sku,sales或inventory`，没有站点列须明确`default_site`。状态可映射`active/out_of_stock/discontinued/unknown`。每日汇总重复报错，订单明细`transactions`才按日累加；库存必须是每日快照。`sales_basis`区分`gross_units/net_units`，负净销量保留但阻止预测训练。支持年-月-日、年/月/日、日/月/年、月/日/年四种明确日期格式。补零必须同时指定`missing_dates=zero`及完整导出确认，不默认推断缺失日为零。

质量包含原始/标准行数、原始行号错误样本、异常提示、SKU与站点组合、数量对账、时间范围、补零数量、`can_confirm/trainable/training_blockers`。`can_confirm=true`不等于可训练，例如负净销量和库存文件可以确认，但不能输入当前销量训练。原文件和标准文件读回均验证真实SHA256。重传相同原文件在本租户复用已有版本；已确认规则修改必须建立revision。

`sales-contract-v2`扩展可选映射`warehouse,order_id,line_id,order_status,refunded_units,unit_price,discount,currency`（库存只支持warehouse）。规则增加：

- `warehouse_sites`：原始仓库名→唯一站点，须完整映射且不与站点列冲突；不分摊共享库存。
- `order_statuses`：原状态→`completed/cancelled/refunded`。订单字段要求`grain=transactions`、订单号＋行号及`order_semantics_confirmed=true`，确认整日快照、非负退货前件数、按原订单日期回写退货。取消排除，净件数减实际退货；没有实际件数的退款订单报错。
- `duplicate_orders=error/drop_identical`：默认拒绝重复；后者仅去除完全相同订单行，内容冲突仍拒绝。跨版本合并后同一订单行在不同日期/SKU出现返回`DATA_ORDER_CONFLICT`。
- `price_semantics_confirmed=true`确认折后成交单价及0—1折扣比例；`default_currency`可指定三位币种。混合币种报错，缺失保留空，不同价格保留`price_facts`逐行记录，不重复扣折扣。

预检`quality.adjustments`给出去重、取消、退货扣减数量，扣减后须与标准总量一致。完整`row_audit`在审计中返回；记录归属原始行号和订单行键。辅助版本须同租户、已确认、类型正确；销量对账口径须一致，不能引用自身或同一原文件修订。对账差额或覆盖不一致阻止确认；库存精确关联，匹配零库存记录阻止训练，未匹配项单独列出。`quality.reconciliation`为摘要，`audit.reconciliation`为完整差额和未匹配键；`auxiliary_sources`冻结来源UUID/SHA。无辅助版本时传空对象，模板应用不复制旧辅助版本。

## 4. 训练与部署

| 方法与路径（前缀 `/api/v1/forecast`） | 契约 |
|---|---|
| `GET /sku-mappings` | 本企业训练映射`items`、可选产品`products`及内容摘要`revision` |
| `PUT /sku-mappings` | `{expected_revision,items:[{source_sku,product_sku}]}`，整体替换下一次训练映射；保存审计；修订冲突409 |
| `POST /training-preview` | `{data_version_uuid,mode,allow_history_overwrite}`；返回输入/合并/重叠/改写行数、最多50条改写样本和日期缺口，以及`catalog.items/unmapped/conflicts/can_publish` |
| `POST /training-runs` | 同上+`Idempotency-Key`；202创建，200幂等重放 |
| `GET /training-runs?limit=20` | 最近训练与评测记录，最大100 |
| `GET /training-runs/{uuid}` | 任务状态、数据血缘、评测指标、错误原因 |
| `POST /append` | 旧Excel兼容接口；复用相同质量与血缘逻辑，不绕过标准确认条件 |

模式：

- `initial`：仅当前确认版本独立训练；无本企业血缘时可替代共享参考模型。
- `append`：合并现用私有模型所关联的同企业标准版本；无可信历史时返回`FORECAST_TRUSTED_HISTORY_REQUIRED`。
- `rebuild`：仅使用当前完整版本重建，保留旧部署到验证通过。

改变历史销量需明确`allow_history_overwrite=true`。独立文件预检通过仍需检查合并历史日期连续性。一个企业同一时间仅允许一个queued/running训练；发布前比较开始时部署，变化时终止发布。

状态为`queued/running/succeeded/rejected/failed`。`rejected`表示数据长度或独立评测未达标，`failed`表示执行异常；两者均保留旧模型。成功时写入租户制品、真实内容SHA、模型归属和标准血缘，在事务中切换部署并刷新产品SKU目录。

企业训练要求全部SKU关联本企业产品：显式映射优先，其次同名匹配；大小写歧义、多个源指向同一产品/站点、空目录均拦截。创建任务时冻结产品ID与源SKU，后续修改映射只影响新任务。发布时复核产品仍存在且身份未变，不允许用另一个同名产品替换。

训练、平台登记与管理员回滚复用`ForecastPublication.activate`，在同一租户锁/事务内切换模型及目录。回滚前校验旧制品真实SHA和目录覆盖，使用历史`route_policy.catalog_snapshot`；无快照的旧部署返回409 `FORECAST_CATALOG_SNAPSHOT_REQUIRED`，须标准数据重建。预测任务启动时同锁绑定部署及`routing_snapshot`（含参考产品的源SKU），执行时不再读取现用目录。迁移`v3_20_forecast_routing.sql`使已保存的路由及模型绑定不可改写；缺少快照的历史排队任务须重建。

2026-10-05训练详情新增`execution_attempts,heartbeat_at,lease_expires_at`。领取、续租、恢复由服务端控制，不提供客户端修改执行令牌的接口。训练子进程在超时或取消时终止；发布/拒绝前检查数据库租约和执行令牌。worker每30秒恢复过期任务，按数据库执行次数限制重试（`WORKER_MAX_ATTEMPTS`默认3）；达到上限后失败，`FORECAST_LEASE_EXPIRED`表示进程中断后租约过期。优雅退出且未耗尽次数时回到queued，标记`FORECAST_INTERRUPTED`。原模型在整个恢复过程中保留。

迁移`v3_19_training_leases.sql`可重复执行，已经接入根目录初始化SQL。升级时先停旧worker再迁移并启动新worker；旧版本进程不支持令牌校验。独立训练目录以任务UUID和执行令牌组成，实际路径不通过业务API暴露。后台清理只删除无模型引用、无活跃执行且未被子进程文件锁占用的残留目录。

新训练使用`tenant-xgb-v2`、门槛`sku-rolling-v2`；旧v1制品继续加载自身引擎。每SKU/站点独立拟合，日有140天、周有20个完整自然周时参加三个滚动窗口评测，各窗口28天/4周。分层仅根据第一窗口前历史：非稀疏为XGBoost，零销量占比≥50%用28日/4周均值，仅验证整个窗口总量。新品用近期均值、全零用零值基线，两者未验证。日/周各至少一个SKU通过且所有参评SKU各窗口均通过才发布；短历史不阻断其他SKU，只有短历史/全零仍拒绝发布。完整门槛见总体设计§6.4。

训练`metrics.evaluation`含`gate/day/week/passed/reasons`。各粒度含加权`wape`、`macro_wape`、`worst_sku_wape`、`coverage`、参评与未验证数量，以及`series`逐SKU的`segment/method/history_periods/minimum_periods/validation_status/validation_scope/folds`。每个fold记录真实训练截止、验证范围、样本数、MAE、WAPE、偏差、对照及拒绝原因。覆盖分母包括新品和全零，不把未验证算成通过。

预测汇总新增`method,segment,validation_status,validation_scope,data_through`，随结果保存。`lower/upper`允许空；新品、手动基线、参考SKU、延迟或超出评测跨度均不给区间；稀疏仅完整窗口返回总量区间，逐期区间为空。只要一组没有区间，汇总`upper_total/safety_stock/recommended_production`为空、`planning_status=unvalidated`，页面显示“待验证”。成熟模型误差带取该SKU验证MAE；稀疏取整窗口总量MAE，均不称95%置信区间。参考产品历史精度不算目标产品精度。

当前只学习销量历史，支持新品基准销量或参考SKU；价格/折扣/促销/库存情景拒绝，页面禁用。库存独立接入用于质量审计，暂未成为训练特征。旧管理员代码替换返回409 `FORECAST_STANDARD_TRAINING_REQUIRED`，页面展示标准训练说明。

## 5. 页面和实现位置

| 页面与流程 | 代码 |
|---|---|
| 模型训练：文件→映射→质量→确认→合并→训练→评测 | `frontend/src/EnterpriseTraining.jsx`，由`ForecastWorkspace.jsx`接入 |
| AI智能选品：策略、企业事实、条件检查、反馈、实施和导出 | `frontend/src/EnterpriseDecision.jsx`、`OpportunityOutcomes.jsx`，由`MarketIntelligenceHub.jsx`接入 |
| 产品中心：标准候选、证据核验、数值事实、画像确认 | `frontend/src/ProductFacts.jsx`，由`ProductCatalog.jsx`接入；共用`furniscope_agent/product_facts.py`词表 |
| 原文件/标准文件、模板和对账 | `forecast_data_service.py`、`sales_data_cleaner.py`、`data_reconciliation.py`、`ImportSemantics.jsx` |
| 训练/评测/发布 | `forecast_training_service.py`、`training_lifecycle.py`、`furniscope_forecast/tenant_engine.py`及`training_process.py` |
| 决策规则/冻结/反馈/经营观察 | `enterprise_decision.py`、`opportunity_policy.py`、`opportunity_outcomes.py`、`analysis_task_repository.py` |
| 数据库边界 | `v3_15_enterprise_data.sql`、`v3_16_tenant_boundaries.sql`、`v3_17_opportunity_policy.sql`、`v3_19_training_leases.sql`、`v3_20_forecast_routing.sql`、`v3_21_import_templates.sql`、`v3_22_opportunity_outcomes.sql` |

自动化验证见`tests/test_enterprise_http.py`、`test_enterprise_training.py`、`test_training_recovery.py`、`test_job_queue.py`、`test_enterprise_policy.py`、`test_tenant_boundaries.py`、`test_forecast_catalog.py`、`test_product_facts.py`、`test_import_contract_v2.py`、`test_forecast_strata.py`、`test_opportunity_outcomes.py`、`test_offline_acceptance.py`及`frontend/tests/e2e/enterprise-live.spec.js`。详细验收结果由上位总体设计集中记录。

离线工具`evaluate_enterprise_forecast.py`直接读取原始销量与已确认规则，复用滚动评测；`evaluate_opportunity_ranking.py`接收页面完整ranking-v2导出，按任务和时间检查样本后比较NDCG。均输出输入/代码SHA，不写库、不训练排序模型、不发布。命令及No-Go含义见[真实数据离线验收指南](./FurniScope_真实数据离线验收指南.md)。
