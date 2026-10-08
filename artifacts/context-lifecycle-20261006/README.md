# FurniScope 上下文生命周期验收

日期：2026-10-06

范围：统一工作台/任务Turn、版本化客户记忆、工作台Context、真实Citation、企业文档
知识库、标准SSE及前端确认交互。本目录仅记录本地工程验收，不代表生产部署或真实企业
效果。

## 结果

| 验证 | 结果 |
|---|---|
| Python全量，真实PostgreSQL | 179 passed，10 skipped，0 failed |
| 生命周期与客户记忆专项 | 8项纳入全量并通过 |
| 固定记忆Agent评测 | 10/10场景通过；误写率0 |
| Playwright全套合同 | 16 passed，8 skipped |
| 工作台生命周期场景 | 4 passed |
| 前端生产构建 | 通过；保留既有主包大于500 kB提示 |
| Sites托管合同 | 5 passed |
| 数据库迁移 | v3.15—v3.29 fresh、upgrade、repeat、启动RLS、RBAC、审计链及cell placement探针全部通过 |
| RLS表数量 | 80 |

Playwright跳过的8项要求单独启动真实企业夹具后端；Python跳过项要求Redis或旧V4制品等
显式外部条件。它们不是本轮失败项。

## 迁移

关键迁移 SHA-256：

```text
v3_23  4f695267ba77b02732539a43372b6247a0ad29e1bce490ddfcffe3416d0ec357
v3_24  7a89f534637d2049383b90af8e13ac7e8b0d47cd22ff5bdf2f6df3c9df05843f
v3_25  3f0a9f72bc86513c9ee94a1f5af247b7f43c78dea6e0d44e7d6975ed3d13fa80
v3_26  413589b98943db885a30c26ea1aa3d3c101e1de46284a546d0c0ea2d9078520a
v3_27  5ce3a360e1f584346585c219e76359ace38fbe4017a89712275918f4d89adc15
v3_28  a12892672e98982b651ba787ea0c19112dad527593b0484ee78b47dc3da2659c
v3_29  4c11e345d80f0d81b110b59e9d8a166a00220e09f8c72d53a13c71c206c24ed6
```

验收数据库均为本机新建隔离库：

- `furniscope_enterprise_test_mem1006k_fresh`
- `furniscope_enterprise_test_mem1006k_upgrade`

升级库中的存量租户哨兵保持不变。验收脚本不会重置已有数据库。

## 文件

- `migration-summary.json`：迁移SHA、版本、RLS表和存量哨兵摘要。
- `migration-fresh.log`：空库安装及重复执行原始日志。
- `migration-upgrade.log`：存量升级及重复执行原始日志。
- `pytest.xml`：真实PostgreSQL全量JUnit结果。
- `memory-agent-eval.json`：指代、纠错、市场切换、暂停/恢复、记忆误写及Prompt Injection固定评测。
- `chat-memory-citation-desktop.png`：1280×720工作台生命周期界面。
- `chat-memory-citation-mobile.png`：390×2939响应式工作台全页截图。
- `memory-drawer-desktop.png`：1280×720记忆管理与Context Preview。
- `memory-drawer-mobile.png`：390×844固定视口记忆抽屉。

## 边界

已完成P0/P1工程闭环；尚未完成真实企业数据试点、生产对象存储迁移、独立知识库文档
管理页面、检索效果评测及无依据回答监控。知识文档当前以PostgreSQL `BYTEA`保存源版本，
只适合作为可复现实验实现。
