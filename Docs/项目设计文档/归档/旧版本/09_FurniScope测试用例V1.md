# FurniScope 测试用例 V1.0

## 1. 测试基线

| 项目 | 基线 |
|---|---|
| 需求 | PRD/SRS V1 |
| 数据 | 产品数据字典 V2 |
| 数据库 | PostgreSQL 数据库设计 V2、`furniscope_postgresql_v2.sql` |
| 工作流 | Agent 工作流 V1 |
| 接口 | RESTful API V2 |
| 页面 | 页面交互原型 V2 |
| P1 规则 | 【P1迭代功能，本次Demo暂不实现】，仅做契约审查，不纳入 Demo 通过门槛 |

## 2. 通用前置条件

- PostgreSQL 16 已执行 V2 DDL，`furniscope` Schema 和官方 LangGraph Checkpointer 已初始化。
- 已创建脱敏租户、管理员、产品经理和市场运营账号。
- 已准备一个沙发 SKU、授权产品资料、固定 Amazon US 竞品与评论数据集。
- 所有请求携带 `Authorization: Bearer <token>`、`X-Tenant-Id` 和 `X-Request-Id`；写请求按 API 要求携带 `Idempotency-Key` 或 `If-Match`。
- 数据库断言必须带 `tenant_id`，不得跨租户读取。

## 3. P0 主链路测试

| 用例ID | 场景/API | 前置与输入 | 操作 | 预期结果 | 数据库断言 |
|---|---|---|---|---|---|
| TC-P0-001 | 登录 API-USR-01 | 有效账号密码 | POST `/api/v1/auth/login` | 200；返回访问令牌、刷新令牌和有效期 | `users.last_login_at` 更新；不存明文密码 |
| TC-P0-002 | 工作台 API-DSH-01 | 已登录 | GET `/api/v1/dashboard/summary` | 200；`metrics/recent_tasks/human_todos/recent_reports` 类型正确 | 聚合结果仅包含当前租户 |
| TC-P0-003 | 创建产品 API-PRD-01 | 合法沙发基础字段 | POST `/api/v1/products` | 201；返回产品 ID，状态为草稿 | `products` 新增一行，SKU 租户内唯一 |
| TC-P0-004 | 上传解析 API-PRD-05 | 图片、PDF、参数表均通过安全检查 | 上传产品资料 | 202；返回 `parse_job_id` | `file_assets/product_parse_jobs/product_parse_job_files` 已提交且幂等键唯一 |
| TC-P0-005 | 解析轮询 API-PRD-07 | TC-P0-004 | 轮询解析任务 | 状态依次为 queued/processing/终态；进度不回退 | 计数满足成功数+失败数≤总数；终态写 `completed_at` |
| TC-P0-006 | 解析部分成功 | 一份合法文件、一份可重试失败文件 | 执行解析 | 返回 `partial`、文件级错误和 `retryable=true` | 任务、文件结果均持久化，不丢失成功文件输出 |
| TC-P0-007 | 确认画像 API-PRD-06 | 必填属性完整、关键冲突为0 | 确认产品画像 | 200；画像变为 confirmed，产品可分析 | 产品画像版本锁定，确认人和时间存在 |
| TC-P0-008 | 创建数据集 API-CMP-01 | 合法市场、平台和日期范围 | 创建数据集 | 201；返回数据集 ID | `market_datasets` 租户隔离、版本固定 |
| TC-P0-009 | 数据导入 API-CMP-02 | 合法 CSV/XLSX | dry-run 后正式导入 | 预校验不写业务数据；正式导入返回任务 ID | 商品和评论去重键有效，原文不被翻译覆盖 |
| TC-P0-010 | 数据质量 API-CMP-03 | 导入完成 | 查询质量结果 | 返回样本量、重复率、无效率和限制 | 数据集质量状态与统计一致 |
| TC-P0-011 | 创建并启动任务 API-INS-01/02 | 已确认画像、ready 数据集 | 创建任务后启动 | 创建返回 `task_uuid`；启动返回 queued | 同一事务冻结产品版本、数据集、算法版本和任务配置 |
| TC-P0-012 | 任务轮询 API-INS-03 | 任务运行中 | 每2秒查询 | 返回 `stage_runs/partial_failures/checkpoint_stage/retryable/report_uuid` | `stage_runs` 按开始时间排序，同阶段 attempt 递增 |
| TC-P0-013 | 竞品待确认 API-CMP-06 | 任务到 competitor_review | 查询竞品集合 | 返回 waiting_human、集合版本、恢复阶段和匹配解释 | 活跃阶段状态为 waiting_human；存在安全 Checkpoint 投影 |
| TC-P0-014 | 竞品复核 API-CMP-05 | TC-P0-013 | 纳入、排除和改类 | 返回更新后的匹配状态 | `competitor_matches` 记录复核人、时间和备注 |
| TC-P0-015 | 确认并恢复 API-CMP-07 | 正确 `competitor_set_version`、`checkpoint_stage`、If-Match | 提交确认 | 202；返回 `resume_command_id` 和 `resumed_from_checkpoint=true` | 确认快照与 Outbox 事件同事务提交；任务先 queued |
| TC-P0-016 | 评论观点 API-REV-01 | 评论抽取完成 | 查询观点 | 返回情感、严重度、证据 Span 和置信度 | 每条观点关联原评论和模型运行；证据范围合法 |
| TC-P0-017 | 需求聚类 API-REV-02/03 | 聚类完成 | 查询聚类并下钻证据 | 指标带明确分母和样本数 | 聚类成员引用存在，代表证据可回溯 |
| TC-P0-018 | 市场机会 API-INS-04 | 评分完成 | 查询机会 | 六维分、总分、置信度和等级完整 | 数值范围合法，评分版本与任务冻结版本一致 |
| TC-P0-019 | 产品建议 API-INS-05/06 | 已生成高风险建议 | 专家标记并提交 | 复核结果保存；需要时从 expert_review 恢复 | 建议版本、复核人、恢复控制事件均可审计 |
| TC-P0-020 | 报告查询发布 API-RPT-01/02 | 证据审计通过 | 查询并发布报告 | 返回稳定 `report_uuid`；发布成功 | 报告状态、发布人、时间和审计日志同事务更新 |

## 4. 工作流、恢复与异常测试

| 用例ID | 场景 | 操作 | 预期结果 | 数据库断言 |
|---|---|---|---|---|
| TC-WF-001 | 竞品集合版本冲突 | 使用旧 `competitor_set_version` 调 API-CMP-07 | 409 `WORKFLOW_CHECKPOINT_CONFLICT`，前端刷新集合 | 不新增确认版本，不投递恢复事件 |
| TC-WF-002 | Checkpoint 阶段冲突 | 将 `checkpoint_stage` 伪造为非 competitor_review | 409，不恢复 Graph | 当前安全 Checkpoint 保持 active |
| TC-WF-003 | 竞品确认幂等 | 相同幂等键重复提交相同 Body | 返回同一受理结果，不重复恢复 | 确认记录和控制事件各一条 |
| TC-WF-004 | 幂等键载荷冲突 | 相同幂等键提交不同竞品集合 | 409 幂等冲突 | 原记录不变 |
| TC-WF-005 | 非阻断批次失败 | 评论 Map 阶段部分批次超时 | 任务进入 partial_succeeded 或继续降级；API 返回 `partial_failures` | 失败单元 ID、影响、置信度上限和 retryable 持久化 |
| TC-WF-006 | Worker 重启恢复 | 安全 Checkpoint 后终止 Worker并重启 | 从最近安全点恢复，不重复执行成功节点 | 幂等键不重复；模型成功调用不重复计费 |
| TC-WF-007 | 不安全恢复点 | 最新投影 `is_safe_resume=false` | 尝试恢复 | 拒绝或回退到最近安全点 | 不安全快照不被标记 consumed |
| TC-WF-008 | 报告入口门禁 | N19 前查询 API-INS-03 | `report_uuid=null`；页面不可进入报告 | 不存在孤立报告行 |
| TC-WF-009 | 最终持久化原子性 | N19 中模拟报告写入失败 | 任务不得返回 succeeded | 任务、报告、证据事务整体回滚或任务 failed |
| TC-WF-010 | 跨租户访问 | 租户B读取租户A任务 | 404 或权限错误，不泄露存在性 | 无跨租户查询结果和变更 |
| TC-WF-011 | Model Router超时 | 模拟可重试超时 | 有界重试并记录实际次数；耗尽后按节点策略失败/降级 | `ai_model_runs` 保存模型、耗时、错误码和重试次数，不存 API Key |
| TC-WF-012 | Schema校验失败 | 模型返回非法结构 | 不进入业务结果表；允许策略内重试 | `schema_valid=false`，业务结果无脏数据 |

## 5. 页面交互测试

| 用例ID | 页面 | 验证点 | 预期结果 |
|---|---|---|---|
| TC-UI-001 | P07任务详情 | 前台轮询 | queued/running/partial_succeeded 每2秒刷新 |
| TC-UI-002 | P07任务详情 | 浏览器标签页隐藏 | 轮询降频至10秒，恢复可见后立即查询 |
| TC-UI-003 | P07任务详情 | 终态 | succeeded/failed/cancelled 停止轮询 |
| TC-UI-004 | P07任务详情 | 阶段合并 | 以 `stage_code+attempt_no` 合并，不覆盖历史尝试 |
| TC-UI-005 | P08竞品看板 | 确认受理 | 202 仅显示“恢复命令已受理”，不宣称 Worker 已运行 |
| TC-UI-006 | P08竞品看板 | 409冲突 | 保留用户说明，刷新集合并要求重新确认 |
| TC-UI-007 | P10报告 | 报告 UUID 门禁 | `report_uuid=null` 时停留 P07并说明尚未生成 |
| TC-UI-008 | 全局 | P1入口 | 本次 Demo 隐藏或禁用，并统一标注【P1迭代功能，本次Demo暂不实现】 |

## 6. P1 契约测试【P1迭代功能，本次Demo暂不实现】

以下用例只做接口 Schema、权限和文档审查，不计入本次 Demo 运行通过率：

| 用例ID | 接口 | 契约检查 |
|---|---|---|
| TC-P1-001 | API-INS-07 | 仅 `retryable=true` 且有安全 Checkpoint 时允许阶段重试 |
| TC-P1-002 | API-INS-08 | 协作式取消返回 accepted，继续轮询直至 cancelled |
| TC-P1-003 | API-LST-01/02/03 | Listing 生成、查询、审核字段与数据字典一致，不自动发布平台 |
| TC-P1-004 | API-RPT-03/04/05 | 报告导出、短效 URL 和历史记录权限符合契约 |

## 7. Demo 验收门槛

- TC-P0-001～020 全部通过；
- TC-WF-001～012 全部通过；
- TC-UI-001～008 全部通过；
- 固定 Demo 主链路连续执行 30 次，成功率不低于 PRD 目标；
- 无跨租户数据泄漏、明文密码、硬编码 API Key 或未授权原始数据；
- P1 未实现不得伪造成功响应。

