# FurniScope 真实数据离线验收指南

日期：2026-10-05。上位依据：[企业决策与数据闭环总体设计](../项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md)。工具在本地评测输入文件，不发布模型、不修改企业策略、不写业务数据库。仓库验收样本为合成数据，真实效果需要企业提供真实数据后运行。

## 1. 销量预测

准备原始CSV/XLSX/JSON销售文件，以及已经核验的规则文件。规则内容直接使用`ImportRules`对象，不包裹API请求外层`rules`：

```json
{
  "mapping": {"date":"日期","sku":"SKU","site":"站点","sales":"销量"},
  "kind":"sales",
  "grain":"daily",
  "sales_basis":"gross_units",
  "date_format":"%Y-%m-%d",
  "missing_dates":"unknown",
  "complete_export_confirmed":false
}
```

在仓库根目录、按`requirements.lock`安装的Python3.12环境执行：

```bash
PYTHONPATH=.:backend python scripts/evaluate_enterprise_forecast.py \
  /path/to/sales.csv --rules /path/to/rules.json --output /path/to/forecast-audit.json
```

输出含输入、规则、标准记录、引擎和清洗代码SHA，Python及依赖版本，数量对账和问题样本，以及与上线相同的逐SKU日/周三窗口指标。先清洗再评测；缺口、负需求、缺货状态等未解决时停止评测。日需140天、周需20完整自然周参加评测，短历史和全零保留未验证状态。稀疏只验证窗口总量，成熟SKU逐期评测；任何参评SKU失败都会拒绝整体门槛。

| 结果 | 退出码 | 下一步 |
|---|---|---|
| `NO_GO_DATA_QUALITY` | 2 | 按质量报告核验原文件与口径 |
| `NO_GO_EVALUATION` | 2 | 查看各SKU窗口、基线对照与历史长度 |
| `OFFLINE_GATE_PASSED` | 0 | 可进入产品映射、标准版本确认及正式训练；不代表已发布或未来精度保证 |

工具不替代页面中的企业身份、SKU映射和独立销售/库存对账。报告的`canonical_records_sha256`只校验标准记录数组，不等于API包含元数据的标准文件SHA。原文件、规则和报告应一起保留，SHA可校验文件一致性，不证明数据本身真实。

## 2. 机会排序

在“市场洞察 → AI智能选品”选择“导出排序评测数据”。页面会沿用同一`as_of`完成所有分页，包含每个任务的全部候选及最新标签。不要合并不同企业或不同截点，也不要先删掉未标注候选。

评测前选定验证开始日期，避免根据结果反复挑选。决策偏好与经营目标分别运行：

```bash
PYTHONPATH=. python scripts/evaluate_opportunity_ranking.py /path/to/opportunity-ranking.json \
  --validation-start 2026-09-01T00:00:00Z --label decision --output /path/to/decision-audit.json

PYTHONPATH=. python scripts/evaluate_opportunity_ranking.py /path/to/opportunity-ranking.json \
  --validation-start 2026-09-01T00:00:00Z --label outcome --output /path/to/outcome-audit.json
```

该日期仅为命令示例，须早于实际导出截点且根据企业数据预先确定。`decision`衡量采纳偏好，`outcome`衡量企业报告的目标达成，均不是因果收益标签。

按任务创建时间整组划分训练候选和验证任务；训练特征与标签必须严格早于验证开始。默认比较前5个位置的NDCG，原市场分与企业修正分在同一候选集上比较；并列分数按机会ID排序。NDCG越高表示正标签越靠前。报告保留逐任务指标、均值与成对差值，不声称统计显著。

只评估至少两个候选、完整有效标注、同时有正负标签的任务。缺标签不当0，部分标注任务整组排除；缺历史快照、缺分数、晚到训练标签等分别统计。重复任务/机会、不同企业、无时区/越界时间、快照归属冲突、非有限分数、分页未完成直接拒绝。导出只含截点时最新修订，晚到修订会保守排除历史任务，不尝试还原之前的标签。

| 结果 | 退出码 | 含义 |
|---|---|---|
| `NO_GO_INSUFFICIENT_GROUPS` | 2 | 有效任务不足；继续积累与核验标签 |
| `READY_FOR_MODEL_EXPERIMENT` | 0 | 满足开展模型实验的数据数量门槛；仍未训练模型或批准上线 |

默认至少30个训练任务和20个验证任务，可用`--min-train-groups`、`--min-validation-groups`预先配置；这只是工程门槛，不是统计功效结论。`--k`调整NDCG截断位置。完整标注筛选存在选择偏差，尤其经营结果通常只覆盖被采纳的机会；必须同时看覆盖率与排除原因，不能推广到全部机会。

## 3. 证据与后续实验

保留原始文件、规则、固定截点导出、报告、依赖锁定文件和报告内SHA。首次跑通只证明数据和工程链路可用；真实效果报告应另列企业、观察周期、标签核验方式、有效任务/SKU覆盖和局限。

如需训练机会排序模型，应在此数据审计之后单独立项：冻结特征及标签定义、仅用训练分区拟合、预先选定模型/调参规则，并保留额外未使用时间段做最终验证。销量预测模型和机会排序模型分别验收。

合成验证及运行日志见[本轮验收证据](../../artifacts/enterprise-20261005/README.md)。旧V4回测不能替代企业v2引擎实测。
