# HeFeng 企业知识库生成与验收

> 日期：2026-10-07  
> 用户：`hefeng@furniscope.local`  
> 目标库：`furniscope_enterprise_test_agentui`

## 结果

执行前HeFeng知识库、文档和检索块均为0。完成后：

| 知识库 | 文档 | 状态 |
|---|---:|---|
| HeFeng 产品与制造知识库 | 2 | 2/2可检索 |
| HeFeng 北美市场证据库 | 2 | 2/2可检索 |
| HeFeng 业务SOP知识库 | 2 | 2/2可检索 |

共3个企业共享知识库、6份XLSX文档、37个检索块。资料来自当前76个已确认产品画像、
HeFeng授权市场数据集和仓库现行SOP，不生成未经确认的经营事实。

## 文档

- `HeFeng产品主档与目录.xlsx`
- `HeFeng制造能力与事实边界.xlsx`
- `HeFeng北美沙发授权市场数据说明.xlsx`
- `HeFeng客户评论证据摘录.xlsx`
- `HeFeng销量预测数据接入SOP.xlsx`
- `FurniScope企业操作与知识使用SOP.xlsx`

`seed-summary.json`保存知识库和文档UUID。脚本按规范化来源签名幂等；第二次执行六份文档
均为`unchanged`且仍为v1。

## 索引与检索

当前环境未配置阿里云Embedding密钥，因此索引采用PostgreSQL全文检索降级，文档仍为
`ready/succeeded`。服务已增加中文词组与SKU匹配评分；验证查询：

| 查询 | 命中文档 |
|---|---|
| `HF-A0393` | HeFeng产品主档与目录 |
| `Amazon 美国 沙发` | 北美市场数据说明、客户评论证据 |
| `销量 预测 SKU` | 销量预测SOP、企业操作SOP |
| `销量预测流程是什么` | 销量预测SOP、企业操作SOP |

后续配置Embedding密钥后可在页面执行重新索引，补充1024维向量和混合检索。

## 验证

- 数据库：3个active/tenant知识库，6份ready文档，6个succeeded索引任务。
- 幂等：重复执行不增加知识库、文档或版本。
- 测试：`tests/test_context_lifecycle.py`为`10 passed`。
- API：HeFeng登录后列表返回`total=3`，各知识库`document_count=2`、
  `ready_document_count=2`。
- 页面：`#knowledge`展示3个知识库和6份可检索文档；1440px无整页溢出、无运行错误。

页面证据：

- `knowledge-page-desktop.png`
- `browser-verification.json`
- `SHA256SUMS`（生成资料、证据及关键实现文件校验）

## 重建命令

```bash
PYTHONPATH=.:backend python scripts/seed_hefeng_knowledge.py
```

生产或其他数据库通过`DATABASE_URL`指定目标。执行前数据库必须升级至v3.34，并确保目标
用户拥有`knowledge.read`和`knowledge.write`权限。
