# 企业闭环复核补全：本地验收完成

本目录记录2026-10-05“逐一补全”的增量证据。前一轮结果保留于`../enterprise-20261004/`，不能视作本次工作区的回归结果。所有业务数据仍为隔离环境中的合成工程样本，不证明真实企业预测精度。

## 最终结果

七项复核全部完成；权威范围见[总体设计§11](../../Docs/项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md#11-复核补全2026-10-05)。以下结果对应本次收尾代码，不将前期分批测试相加充当最终回归。

| 验证 | 结果 | 证据 |
|---|---|---|
| Python全tests，含真实PG16与独立Redis | 172通过、2跳过，无失败/错误 | [最终日志](final-python-clean.log) |
| Playwright全tests/e2e | 23通过、1跳过 | [最终日志](final-browser.log)；含7条本轮真实API链路、16条既有契约 |
| 前端生产构建 | 通过，保留原bundle>500kB提示 | [build](final-build.log) |
| Sites契约 | 5通过 | [Sites](final-sites.log) |
| 空库安装/旧基线存量升级/重复迁移/启动RLS | 两条路径全部通过 | [汇总](migration-final/migration-summary.json)、[日志](final-migrations-recheck.log)、[空库](migration-final/migration-fresh.log)、[升级](migration-final/migration-upgrade.log) |
| 独立worker硬中断恢复 | 第二次执行成功发布；旧执行未覆盖；孤儿清理；Redis pending=0 | [事件日志](final-sigkill-recheck.log)、[结构化结果](sigkill-summary.json)、[重启worker日志](sigkill-worker-after-restart.log) |
| 真实数据离线验收工具 | 13项专项纳入全回归；CLI通过 | [预测样例报告](offline-examples/forecast-report.json)、[排序小样本No-Go](offline-examples/ranking-report.json)、[真实API合成导出No-Go](offline-examples/live-ranking-report.json) |
| 服务收尾 | 本轮API8016/UI4186/Redis6396已停止；原4173仍可用 | [收尾记录](service-shutdown.json) |

Python跳过项是旧V4的`daily.pkl`未下载和`forecast_assets/state`缺失，不能计为真实V4推理已验证；第三方SWIG有5条弃用提示。浏览器跳过的是另一个需要指定旧企业环境的`full-stack-no-mock.spec.js`，本轮`enterprise-live.spec.js`七条均实际运行。桌面与390px移动端新截图以`final-enterprise-*.png`命名；已检查[训练桌面](final-enterprise-training-desktop.png)及[实施移动端](final-enterprise-outcomes-mobile.png)，移动端自动断言无横向溢出。

首轮全回归暴露旧认证测试的第一页假设及不可变历史清理冲突。测试改为唯一标识搜索、关闭夹具租户/禁用用户/完整撤销会话；数据库保护未放宽。第二轮发现清理会话漏填`revoke_reason`，补齐后全套172通过。失败日志[首轮](final-python.log)、[第二轮](final-python-recheck.log)保留。迁移首轮发现v3_18未登记版本，补齐后两条路径通过，见[首轮](final-migrations.log)。SIGKILL首轮演练脚本误用`model_id`，更正为`artifact_model_id`后通过，见[首轮](final-sigkill.log)。

硬中断使用真实worker、训练子进程、Redis与数据库：先暂停子进程以固定故障时点，SIGKILL父worker，等待10秒租约自然过期，再启动新worker；成功后恢复孤儿子进程，确认它只完成文件、不覆盖模型。仅在子进程完成后加速目录清理年龄；没有人工修改租约或mock恢复逻辑。数据库使用新建`furniscope_enterprise_test_final2_20261005_upgrade`，Redis使用独立6396/15。

## 最终复现

Python3.12环境按仓库`requirements.lock`安装。以下命令只面向独立本地环境；Redis队列测试会清空所指定逻辑库。

```bash
PYTHONPATH=.:backend:tests \
FURNISCOPE_TEST_DATABASE_URL=postgresql://bytedance@localhost/furniscope_enterprise_test_20261004 \
FURNISCOPE_TEST_REDIS_URL=redis://127.0.0.1:6396/0 \
/tmp/furniscope-enterprise-venv/bin/python -m pytest tests -q -rs --tb=short
```

前端先以`VITE_API_PROXY_TARGET=http://127.0.0.1:8016`运行4186端口；通过`scripts/serve_enterprise_e2e.py`重新播种独立API。测试夹具文件只留临时目录，不纳入证据和SHA。

```bash
PYTHONPATH=.:backend:tests /tmp/furniscope-enterprise-venv/bin/python \
  scripts/serve_enterprise_e2e.py \
  --database-url postgresql://bytedance@localhost/furniscope_enterprise_test_20261004 \
  --work-dir /tmp/furniscope-followup-e2e --port 8016

# 在frontend目录执行；保留另一个终端运行API/UI
FURNISCOPE_ENTERPRISE_FIXTURE=/tmp/furniscope-followup-e2e/identities.json \
FURNISCOPE_ENTERPRISE_URL=http://127.0.0.1:4186 \
npx playwright test --workers=1
npm run build
npm run test:sites
```

迁移复现须使用新的唯一`suffix`（脚本遇到已有库会拒绝，不覆盖）。SIGKILL复现使用刚初始化且没有其他排队任务的测试库、新临时工作目录和空Redis6396/14或15：

```bash
PYTHONPATH=.:backend:tests python scripts/verify_enterprise_migrations.py \
  --admin-url postgresql://bytedance@localhost/postgres \
  --psql /opt/homebrew/opt/postgresql@16/bin/psql --suffix replay_unique \
  --output /tmp/furniscope-migration-replay

PYTHONPATH=.:backend:tests \
FURNISCOPE_TEST_DATABASE_URL=postgresql://bytedance@localhost/furniscope_enterprise_test_replay_unique_fresh \
python scripts/drill_training_sigkill.py --redis-url redis://127.0.0.1:6396/15 \
  --work-dir /tmp/furniscope-sigkill-replay-unique
```

销量/排序CLI及输入格式见[离线验收指南](../../Docs/开发文档/FurniScope_真实数据离线验收指南.md)。`offline-examples/`包含明确合成的输入和结果：稳定销量可通过工程门槛，只有两个任务的排序样例及真实API导出的单候选任务均得到No-Go。这些报告不能证明真实企业精度。

`SHA256SUMS`覆盖本轮相关源码、测试、迁移、依赖、文档与此目录证据；路径相对仓库根目录，可在根目录执行`shasum -a 256 -c artifacts/enterprise-20261005/SHA256SUMS`。它是工作区内容快照，包含部分本轮之前已存在的修改，不宣称每个文件的全部diff都由本轮产生；清单自身不纳入自身哈希。

## 已验证的增量

| 项目 | 结果 | 证据 |
|---|---|---|
| 训练生命周期、原训练回归及Redis队列 | 22通过 | [step1-training-recovery.log](step1-training-recovery.log) |
| v3_19租约迁移 | 已在既有独立PG16测试库执行 | 最终验收还需重复迁移与空库验证 |
| 成本/策略、训练恢复、映射/回滚、预测、HTTP汇总回归 | 31通过、1跳过（旧V4制品缺失） | [step2-catalog-regression.log](step2-catalog-regression.log) |
| 真实API浏览器链路 | 3通过，包含新SKU映射预检与保存 | [furniscope-step2-browser.log](furniscope-step2-browser.log) |
| 前端构建、v3_20路由迁移 | 通过 | [构建日志](furniscope-step2-build.log)；最终再验完整迁移 |
| 产品事实、策略、文档解析与输入守卫回归 | 31通过 | [step3回归](furniscope-step3-tests.log) |
| 既有产品创建/幂等/确认/解析接口回归 | 1通过 | [产品原契约](furniscope-step3-product-legacy.log) |
| 产品核验与原有企业浏览器链路 | 4通过 | [step3浏览器](furniscope-step3-browser.log)；[桌面](enterprise-product-facts-desktop.png)、[移动端](enterprise-product-facts-mobile.png) |
| 产品事实页面构建 | 通过 | [构建日志](furniscope-step3-build.log)，保留原bundle体积提示 |
| 订单契约、模板、多表对账及原训练/HTTP流程 | 24通过；补充跨版本订单与同源对账拒绝后12项专项再通过 | [回归](step4-tests.log)、[最终专项](step4-contract-final.log) |
| 原4条浏览器链路及新订单模板流程 | 5条通过；新测试修正选择器后单独重跑通过 | [首轮](step4-browser.log)、[重跑](step4-browser-recheck.log)；[桌面](enterprise-import-reconciliation-desktop.png)、[移动端](enterprise-import-reconciliation-mobile.png) |
| 导入页面构建、v3_21迁移 | 通过 | [构建](step4-build.log)、[迁移](step4-migration.log)；最终仍需完整空库/升级验收 |
| 分层预测、原训练及预测适配回归 | 23通过、1跳过（旧V4制品缺失）；总量误差带细化后4项专项再通过 | [回归](step5-tests.log)、[专项](step5-strata-final.log) |
| 分层评测页面构建 | 通过 | [构建](step5-build.log)，保留原bundle体积提示 |
| 分层评测与真实预测页面 | 6条流程分批通过；最终阶段再跑全套 | [初训通过与定位问题](step5-browser-recheck.log)、[其余5条通过](step5-browser-strata.log)；[评测桌面](enterprise-training-desktop.png)、[新品桌面](enterprise-strata-desktop.png)、[新品移动端](enterprise-strata-mobile.png) |
| 实施/经营观察、排序导出及原评分回归 | 13通过；增加整任务分页、撤销采纳、回溯排除和删除保护后再通过 | [最终专项](step6-tests-final.log)；[迁移](step6-migration.log) |
| 实施与经营结果页面 | 原6条通过；新增链路修正测试定位后单独通过 | [首轮](furniscope-step6-browser.log)、[新流程](furniscope-step6-browser-recheck.log)；[桌面](enterprise-outcomes-desktop.png)、[移动端](enterprise-outcomes-mobile.png) |
| 实施页面构建 | 通过；后续小幅布局调整在最终阶段重新构建 | [构建](furniscope-step6-build.log)，保留原bundle提示 |

以下为阶段性记录，“最终阶段待验证”指当时状态；收尾结果以上方最终表为准。第一项覆盖实际子进程取消、旧执行在恢复发布后的令牌拦截、执行次数耗尽、数据库与Redis心跳、优雅退出重投、锁定孤儿目录保留、已发布制品保留，以及原有训练隔离、部署变化和磁盘故障测试。独立worker的SIGKILL进程恢复演练已在最终阶段补齐。

第二项修复成本与出厂价混用；企业训练拒绝未映射/歧义/空目录。映射修改有摘要并发检查和审计，训练使用入队时冻结映射，预测同锁冻结部署及路由。数据库测试实际训练两个部署，验证映射变更不影响排队任务，旧制品被修改时拒绝回滚，恢复原内容后原子回滚目录。工作区既有`customer_memories`迁移接入根SQL并先于统一RLS迁移，修复API启动隔离检查失败。所有测试仍使用合成工程数据。

第三项统一产品/企业能力词表，以明确别名产生带证据的候选，默认不勾选。HTTP测试使用真实XLSX与受控模型替身，验证高置信度仍待确认、原文不存在的证据被过滤、多文件分歧和人工值冲突可见、未涉及属性保留、ETag及跨租户拒绝、历史画像只读、显式确认后进入企业适配拦截。浏览器验证创建→候选选择→记录依据→保存事实→确认画像→启用分析，桌面和390px移动端无横向溢出。未调用外部模型，未验证真实企业事实或认证有效性。

第四项将导入规则升级为`sales-contract-v2`，订单快照明确取消、实际退货、完全相同订单行去重及整日覆盖。示例29件减重复10件、取消4件、退货2件得到13件，并与独立销售表对账。冲突订单、跨版本移动订单、未知仓库、混合币种、错误折扣与退款均拒绝；价格保留逐行事实。库存精确关联，零库存阻止训练，不回填未来库存。模板不可变修订、列契约、并发冲突、同租户来源及RLS漏过滤读取均通过真实PG/HTTP。浏览器新测试初次因`getByLabel`精确选择器不匹配选项文本超时，改为语义combobox后通过，非业务故障；最终阶段再跑完整套件。

第五项升级`tenant-xgb-v2`/`sku-rolling-v2`。每SKU独立模型与三个扩展训练窗口；方法只由首个验证窗口之前历史决定。成熟模型检验逐期误差，稀疏基线只检验完整窗口总量，短历史与全零明确未验证。日/周各至少一个SKU通过且全部参评窗口通过才发布；加权误差很好也不能掩盖小SKU失败。新品目录来自日历史，3天新品即使没有完整周也保留日均×7周基线。新品、手动基线、参考SKU及超范围预测不再借用成熟模型误差带，无依据的库存和生产建议为空；稀疏只给整窗总量MAE带。专项验证不同SKU截止日期完全独立、验证标签不进入拟合、未达标不写制品及真实适配器的空区间契约。浏览器验收中修正了重复表头选择器和新任务尚未刷新时误选旧任务的问题；初训链路与剩余5条分批通过，混合重建真实发布，三天新品正常预测21件且显示未验证、空误差带/库存建议。桌面与390px移动端截图已检查。

第六项新增`v3_22`：机会首次评分保存不可变完整特征与任务事实快照，同任务重试复用原结果；历史记录不重建。实施事件关联最新采纳修订，记录实施/观察日期、企业核验依据、目标达成及可选收入/费用口径。销量回流校验同租户确认版本、真实SHA、产品映射和日期/站点完整覆盖，保存数量及血缘，不计算因果增量。排序导出按任务完整分页和固定时间截点，每机会一条最新标签，保留未标注候选；撤销或更新采纳、历史缺特征、回溯实施会排除。真实HTTP/PG验证并发409、跨租户404/RLS漏过滤、SHA篡改、覆盖失败、快照/事件不可修改与删除、未来/无时区截点、两任务分页及截点重放。首轮Agent连接因触发器未限定schema失败，改为显式schema后13项通过。新增浏览器流程首轮因旧文本影响精确label定位超时，改用可访问角色后通过：计划→完成→同日3件销量/收入费用→刷新→导出；桌面和390px无溢出。最终阶段仍需重启API并全套回归。

复现命令（只用于独立测试服务；队列测试会清空目标Redis库）：

```bash
PYTHONPATH=.:backend:tests \
FURNISCOPE_TEST_DATABASE_URL=postgresql://bytedance@localhost/furniscope_enterprise_test_20261004 \
FURNISCOPE_TEST_REDIS_URL=redis://127.0.0.1:6396/0 \
/tmp/furniscope-enterprise-venv/bin/python -m pytest \
  tests/test_enterprise_training.py tests/test_training_recovery.py tests/test_job_queue.py -q --tb=short
```

## 交付边界

离线预测/排序验收工具、当前代码回归、迁移、硬中断恢复、最终界面与文档/SHA已完成。真实企业试用、策略权重校准、机会排序模型训练和经营效果验证需要真实数据，未标为已完成。

尚未部署、提交或推送。本轮不会将“上新/营销/连接器整套三赛道”扩展为已交付内容。
