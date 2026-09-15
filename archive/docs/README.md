# FurniScope 历史文档归档清单

归档日期：2026-09-15

本目录保存不再作为当前开发和复赛交付依据、但仍具有追溯价值的材料。归档操作只移动文件，不删除原始内容。

## 目录说明

| 目录 | 内容 | 归档原因 |
|---|---|---|
| `competition/idea-round-v1/` | 初赛提交文案、附件说明、附件清单、ZIP 和 UI PDF 兼容副本 | 已被当前复赛文档和真实运行截图取代 |
| `competition/final-preparation-2026-09/` | 复赛缺口清单和提交前代办 | 截止日前的过程管理快照，不属于正式交付物 |
| `competition/reference/` | 赛事规则与赛道方案汇总 | 外部参考资料，不属于项目当前设计文档 |
| `prompt-history/` | 分阶段实现、简化、测试提示词 | 研发过程材料，不作为当前需求或实现依据 |
| `development-planning/` | 历史迭代任务和对应实施状态 | 任务已由当前 README、代码和测试状态取代 |
| `development-milestones/` | FastAPI 编码、认证与幂等阶段性准入报告 | 固定时点的 PASS 快照，当前状态以代码和最新测试为准 |

## 重复材料说明

`competition/idea-round-v1/FurniScope_Full_UI_Design_V1.pdf` 与
`../ui-design-v2/Docs/FurniScope_完整UI页面设计稿_V1.pdf` 的 SHA-256 完全相同。前者作为初赛附件的英文兼容副本保留，后者作为 UI V2 设计源归档保留。

## 仍在原位置的历史材料

被替代的 PRD、数据字典、数据库、Agent、API、页面原型和旧 SQL 已在
[`../../Docs/项目设计文档/归档/旧版本/`](../../Docs/项目设计文档/归档/旧版本/) 中完成归档，继续沿用原有位置，避免无必要地改动大量文档内相对链接。
其中 PostgreSQL V2 历史 DDL 为 `furniscope_postgresql_v2.sql`；根目录仅保留当前 Docker 与 CI 使用的 V3 基线 DDL。

## 使用约束

- 当前正式文档请从 [`../../Docs/README.md`](../../Docs/README.md) 进入。
- 归档文件不得用于声明当前功能完成状态。
- 恢复任何文件前，应确认其内容仍与当前代码、接口和数据库契约一致。
- 凭证、个人信息和未脱敏业务数据不进入本目录。
