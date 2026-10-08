# Agent 工具编排与受控问数验收

验收日期：2026-10-06

## 结论

- 受控问数、RAG阈值拒答、服务端工作流动作和原子Turn工具结果已完成本地工程验收。
- P0不开放自由NL2SQL；数值只来自白名单`DataQueryPlan`和固定SQL表达式。
- 本目录不代表真实企业数据效果、生产负载、线上模型成本或生产部署验收。

## 自动化结果

| 范围 | 结果 |
|---|---|
| Python全量，PostgreSQL 16 + 独立Redis | `196 passed, 2 skipped` |
| Playwright合同 | `17 passed, 8 skipped` |
| 前端生产构建 | 通过；保留主包大于500 kB提示 |
| Sites合同 | `5 passed` |
| v3.31迁移 | fresh、upgrade、repeat、存量哨兵、FORCE RLS和10项指标通过 |
| Agent工具固定评测 | 路由1.0、QueryPlan 1.0、RAG阈值1.0、不安全SQL生成率0 |
| 记忆Agent固定评测 | 产品/市场、指代、意图、记忆写入、注入防护均1.0；误写率0 |
| 页面视觉验收 | 1440px与390px无横向溢出；问数表格/图表及知识库抽屉可见；页面错误0 |

两项Python跳过是仓库未提供旧V4预测制品，不属于本次Agent工具变更。八项Playwright
跳过中，七项要求显式启动独立企业后端，一项为旧全栈入口；合同套件其余用例均通过。

## 结构化证据

- `evaluation-final.json`：Intent、QueryPlan、RAG阈值及不安全SQL评测。
- `memory-evaluation-final.json`：多轮状态、记忆误写及Prompt Injection评测。
- `visual-acceptance.json`：桌面/移动端尺寸、溢出、组件和页面错误检查。
- `data-query-desktop.png`、`knowledge-manager-desktop.png`、
  `knowledge-manager-mobile.png`：真实本地API页面截图。
- `../data-query-migrations-20261006-final/migration-summary.json`：v3.15-v3.31
  fresh、upgrade、repeat、RLS与迁移SHA。

## 关键文件 SHA-256

```text
0c1f02a146da90fe9cc1c9ee365eaa068a2c6192f11a1f897f4e5b0a4d4aba14  migrations/v3_31_controlled_data_query.sql
e6d1a1e050a49706d2a7deb22396db82cd9bb95265e1dc2c1ac04461db1a5dc9  backend/furniscope_api/services/data_query.py
6d024e673120c0b62399ebfdda0b5d85a67176e62a27f3aea7ea1e0a2744e713  backend/furniscope_api/services/agent_orchestrator.py
bd89604d9e2f1ed0cc00fd8e4ba04ef3abb5c2e2106a1b6ebd0640b210e0641d  frontend/src/AnalysisCenter.jsx
de20caa41b0197e08642017126428f34623aa337fb446bb862bfeeb1ea706007  frontend/src/analysis-center.css
ca482850d18da55bf513b512346517045cd0e96c49a22cc2119932d4ebd91d65  frontend/tests/e2e/data-query-rag-orchestration.spec.js
c43c584919f91aedd8b9692bfb11b8d81da98c434e995cfbdb050b968893db2c  scripts/evaluate_agent_tools.py
bcd6fb7bbc0e6ab8eedbfc995f51f1565c0cce6977cb3ac976a4abda489b59e3  tests/test_data_query.py
```
