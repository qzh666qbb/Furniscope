# FurniScope 多租户隔离与数据库生产化验收

日期：2026-10-06

范围：数据库职责分离、FORCE RLS、企业RBAC、append-only审计、租户删除、全库及
单租户恢复、Redis租户公平调度、Cell路由迁移、tenant-first索引和知识向量侧索引。
全部验证使用本地隔离PostgreSQL 16和合成工程数据，不代表生产部署、合规认证或真实
企业容量。

## 最终结果

| 验证 | 结果 |
|---|---|
| Python全量 | 190项收集，188通过、2跳过、0失败 |
| Python跳过边界 | 仅旧V4 `daily.pkl`和`forecast_assets/state`未提供 |
| Playwright全量 | 23通过、1跳过；7条企业真实API链路已运行 |
| 前端 | 生产构建通过；Sites合同5通过；保留主包大于500 kB提示 |
| 数据库迁移 | v3.15-v3.30 fresh、upgrade、repeat全部通过 |
| 数据库探针 | 81项RLS启动清单、RBAC、审计链、Cell placement和16分区侧索引通过 |
| Compose | 缺少职责账号口令时拒绝解析；提供非敏感占位值后结构检查通过 |

Python全量显式配置同一隔离PostgreSQL供普通集成测试和Context生命周期测试，并使用
独立Redis逻辑库，因此数据库和队列用例没有因环境变量缺失而跳过。Playwright除旧
`FURNISCOPE_E2E_REAL=1`入口外全部执行。

## 迁移与SHA

最终关键迁移SHA-256：

```text
v3_25  3f0a9f72bc86513c9ee94a1f5af247b7f43c78dea6e0d44e7d6975ed3d13fa80
v3_26  413589b98943db885a30c26ea1aa3d3c101e1de46284a546d0c0ea2d9078520a
v3_27  5ce3a360e1f584346585c219e76359ace38fbe4017a89712275918f4d89adc15
v3_28  a12892672e98982b651ba787ea0c19112dad527593b0484ee78b47dc3da2659c
v3_29  495e1bf08292fe8422ca0a2c8f82a217722e5fb79f0a24056ebc5b2d61f18e9e
v3_30  bf31bb3e2b306f027ed1f39b2201a133f20033ab19e2b1a725bb0463af3c66ee
```

验收库为：

- `furniscope_enterprise_test_iso30final_fresh`
- `furniscope_enterprise_test_iso30final_upgrade`

升级库中的`MIGRATION_SENTINEL`保持不变。迁移脚本不会重置已有数据库。

本机PostgreSQL没有安装pgvector。本轮验证了扩展缺失时迁移不失败、16路Hash分区、
JSONB同步、PostgreSQL FTS和Python cosine降级；没有把HNSW性能或召回率标为已验证。

## 专项校验

- 数据库登录职责拆分为App、Admin、Worker、LangGraph、Backup和Monitor；业务身份
  不能是SUPERUSER/BYPASSRLS，生产启动禁止运行期DDL。
- 带租户边界的父表同时启用`ENABLE/FORCE RLS`，策略表达式、连接池重借出、事务重绑、
  复合外键、私有模型归属、Cell不匹配和冻结写栅栏均通过。
- Redis验证单租户排队上限、原子槽位、公平延迟重投、超时清理、租约心跳、重试、
  死信和安全停止。
- 单租户包只导出分区父表一次；空库恢复验证chunk和Embedding恢复、派生搜索向量重算、
  生成列排除及IDENTITY序列推进。
- Cell迁移验证源/目标父表行数和内容摘要一致、源placement进入cutover、目标进入active，
  切换后源路由拒绝而目标路由接受。
- 全库备份Manifest记录迁移集合、关键行数和审计链头；恢复检查dump/Manifest双重SHA。
- 租户删除经过保留期和Legal Hold，业务数据清理后匿名化必要主体，并生成带证据哈希的
  不可变证明。

## 文件

- `migration-final/migration-summary.json`：迁移SHA、版本、RLS清单和探针结果。
- `migration-final/migration-fresh.log`：空库安装及重复迁移日志。
- `migration-final/migration-upgrade.log`：存量升级及重复迁移日志。
- `pytest.xml`：190项Python JUnit。
- `playwright.xml`：24项Playwright JUnit。
- `SHA256SUMS`：本轮核心实现、文档和上述证据的SHA清单。

## 边界

尚未完成真实企业负载压测、共享数据库内租户级CPU/内存/IO/连接硬配额、WAL/PITR与
跨区域灾备演练、生产对象存储、pgvector生产部署和召回率/延迟基准、自动Cell容量预测
及批量再均衡。当前验收结论是本地工程闭环通过，不是生产SLA证明。
