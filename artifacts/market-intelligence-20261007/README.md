# 五项市场决策数据验收证据

日期：2026-10-07

## 结果

| 项目 | 结果 |
|---|---|
| HeFeng治理批次 | `f50feab9-3c84-4765-97a7-293cf12d716b`，`active` |
| 智能选品 | `ready`，5个机会 |
| 竞品动态追踪 | `ready`，6个观察目标 |
| 评论深挖 | `ready`，12个观点、7个需求簇 |
| 智能定价 | `planning_anchor`，76个同币种价格 |
| 跨境合规预警 | `ready`，2个官方来源、1条高风险品类公告 |
| 逐记录血缘 | 48条 |
| 幂等复跑 | 批次UUID与各类记录数保持不变 |
| 租户隔离 | 跨企业查询为0 |
| RLS | 两张治理表均为`ENABLE/FORCE` |

定价中的单位成本和目标毛利为经营规划参数，API分别返回
`cost_basis=planning_assumption`和
`target_margin_basis=planning_assumption`。连续价格与评论增长历史不足，代理敏感度
保持为空。

官方源实采结果：

- CPSC RSS读取40条，写入1条与躺椅品类直接相关的召回公告；
- FederalRegister.gov读取100条，当前品类命中0条；
- 两个来源状态均为`succeeded`，关键词和品类词必须同时命中。

当前决策边界共4类：实际单位成本与毛利率待企业确认、选品评分缺少连续需求增长和已确认
利润空间、企业制造能力画像未确认、单观测时点不足以计算评论增长代理敏感度。边界保留
在API中供审计与下游使用，决策中心五项能力页不再展示统一页尾边界区。

## 自动化

```text
23 passed, 1 skipped, 5 warnings in 5.32s
```

覆盖：

- 规则包结构和分类规则；
- 五项能力非空与状态；
- 同包重复导入；
- 跨企业RLS；
- 定价规划值来源；
- 经营规划约束治理字段只读；
- API基础设施与数据库安全基线。

市场洞察前端合同回归：

```text
2 passed
```

真实HeFeng页面验收覆盖1440×1000与390×844视口。五项能力数据完整，五个页尾均无
“决策边界”区域，且无横向溢出、失败请求或浏览器控制台错误：

- [`market-decisions-live-desktop.png`](./market-decisions-live-desktop.png)
- [`market-decisions-live-mobile.png`](./market-decisions-live-mobile.png)
- [`live-ui-validation.json`](./live-ui-validation.json)

## 迁移

[`migrations-v2/migration-summary.json`](./migrations-v2/migration-summary.json)记录：

- fresh安装通过；
- 带存量哨兵的upgrade通过；
- 全部增量重复执行通过；
- 启动隔离检查通过；
- 市场决策治理表检查通过。

更新后HTTP只读回归返回200，五项状态分别为：

```text
ready / ready / ready / planning_anchor / ready
```

## SHA-256

```text
7f52a726235c2066d67ae3ced1b9a58387c39ce99e17874fe105bf6800beb7d7  data/market_intelligence/hefeng_market_decision_baseline_v1.json
9caf0d9f2cef9da0f5d3842ec0e27e6c4d534146e038cb13c252b0e05eaf5cd4  data/market_intelligence/hefeng_market_decision_generated_v1.json
0d9bf03b2695f9449f7f17110663ee684b2dd2f94a9aa63b0dbe3cbfe6cff416  migrations/v3_36_market_intelligence_governance.sql
75f236cdf30771aa8f24c85a78a913a75c81088d2dba7c9b6e478e1726d2608b  scripts/seed_market_decision_baseline.py
d335e3a1a6c55bd21fedd9cfda395a67235b3fd2957ac2fb91b494b9cd5a4251  Docs/项目设计文档/19_FurniScope_五项市场决策数据与存储方案V1.md
6bf31e12409636dbd25039695052628eec3c96153aeb2e9b828730062bec3052  artifacts/market-intelligence-20261007/migrations-v2/migration-summary.json
```

## 一致性

- 完整数据断言通过；
- Python语法编译通过；
- `git diff --check`通过；
- 专项文档、规则包、完整数据、迁移、导入器和专项用例的禁用字样扫描无命中。
