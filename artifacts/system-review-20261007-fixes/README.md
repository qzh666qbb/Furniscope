# FurniScope 37项系统复盘整改验收证据

> 验收日期：2026-10-07  
> 结论：37/37项工程问题关闭；工程准入Go；真实企业精度、经营收益和生产容量待实证。

## 1. 结果摘要

| 范围 | 结果 | 证据 |
|---|---|---|
| Python全量回归 | 207项：205 passed、2 skipped、0 failure/error | `pytest-full-final.xml` |
| PostgreSQL迁移 | v3.33/v3.34 fresh、upgrade、repeat、启动隔离及SKU事实身份检查通过 | `migration-v34/migration-summary.json` |
| 前端生产构建 | 4663 modules；主JS 279.84 kB，gzip 84.71 kB | `frontend-build.log`、`frontend-build.exit` |
| Playwright合同 | 20 passed、8 skipped | `playwright-contract.log`、`playwright-contract.exit` |
| Sites | 5 passed、0 failed | `sites-test.log`、`sites-test.exit` |
| 浏览器验收 | 1440x1000和390x844各10个状态，共20个；无整页溢出或运行错误 | `browser-scan.json`、`screenshots/` |

三个`.exit`文件均为`0`。构建和Playwright最初的外层zsh包装误用了只读变量
`status`，因此重新按业务命令结果写入退出码；对应日志完整保留实际命令输出。

## 2. Python跳过边界

JUnit只包含两条跳过：

1. `Forecast artifact has not been downloaded: daily.pkl`
2. `forecast_assets/state is not present in CI`

两项均属于未随仓库提供的旧V4预测制品，不影响现行`tenant-xgb-v2`、数据库迁移或本轮
37项整改。`pytest-full.xml`和`pytest-full-v34.xml`保留整改过程回归，最终结论以
`pytest-full-final.xml`为准。

## 3. 数据库迁移

最终执行基线包含：

- `v3_33_tenant_data_class`：区分`business/test/demo`租户，管理员业务列表和KPI排除测试/演示数据。
- `v3_34_sku_fact_identity`：受控同步SKU改名涉及的产品、目录、别名、销量和库存事实。

`migration-v34/migration-summary.json`记录fresh与带存量哨兵upgrade两条路径、全部迁移
SHA256、87张RLS表、重复执行、启动隔离及SKU事实身份检查。`migration-v34/`为最终证据，
`migration/`保留v3.34安全权限修正前的过程日志，不作为最终结论。

## 4. 浏览器验收

扫描使用全新隔离数据库`furniscope_enterprise_test_review37_ui_final`和当前根SQL/API。
页面覆盖：

- 企业：首页、工作日记、产品中心、AI工作台、企业知识库、销量预测、市场洞察、决策报告。
- 权限：普通企业用户访问Admin的403状态。
- 管理员：Admin控制中心。

上述10个状态分别在1440x1000和390x844执行。结构化结果满足：

- 20/20：`document.width === viewport.width`
- 0个`horizontalOverflow`
- 0个console error、page error或failed request

截图用于人工核对首页单列、工作日记字段卡片、市场摘要卡片、Admin 2x2 KPI和账号卡片。

## 5. 复验入口

关闭台账见：

- `Docs/项目设计文档/16_FurniScope_系统功能与业务流程复盘V1.md`
- `Docs/项目设计文档/09_FurniScope测试用例V2.md`

`SHA256SUMS`覆盖本目录日志、XML、JSON、截图以及关键迁移、源码和权威文档。清单不包含
自身，复验时在仓库根目录执行：

```bash
shasum -a 256 -c artifacts/system-review-20261007-fixes/SHA256SUMS
```

## 6. 结论边界

本证据证明当前代码、迁移、合同和指定响应式页面状态满足本轮工程准入要求。它不证明：

- 真实企业机会排序或销量预测达到目标精度；
- 真实经营收益已经发生；
- 生产并发容量、对象存储、HTTPS、WAL/PITR或跨区域灾备已经验收。

上述事项必须使用授权企业数据和目标生产基础设施单独验收。
