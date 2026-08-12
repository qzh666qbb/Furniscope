# FurniScope 页面-API 对齐变更明细表

> 配套主文档：《FurniScope 页面交互原型说明 V2》  
> 修订基线：RESTful API 接口设计 V2、产品数据字典 V2、Agent 工作流设计 V1

| 序号 | 修改位置 | 旧内容 | 新内容 |
|---:|---|---|---|
| 1 | 文档基线 | 仅笼统说明基于 PRD/API | 明确 API V2、数据字典 V2、Agent N00—N19 三项正式基线 |
| 2 | 全局 P1 规则 | P1 使用“V2，P1”“P1 扩展”等不同写法 | 统一为“【P1迭代功能，本次Demo暂不实现】”，Demo 隐藏入口且不模拟成功 |
| 3 | 页面清单 P04 | 核心接口 API-PRD-03～06 | 增加 API-PRD-07 产品解析任务查询 |
| 4 | 页面清单 P08 | 核心接口 API-CMP-04/05 | 增加 API-CMP-06/07 任务竞品查询和确认恢复 |
| 5 | 页面清单 P12 | 未清晰标明 Demo 范围 | 页面名称增加统一 P1 标识 |
| 6 | P02 工作台 | 工作台字段未逐项对齐数据字典 | 明确 `metrics/recent_tasks/human_todos/recent_reports` 均来自 API-DSH-01 |
| 7 | P02/P03/P05 跳转 | 使用“无请求” | 改为完整前端路由并明确“不调用 API” |
| 8 | P04 解析进度 | 仅说明查询任务 | 明确 API-PRD-07 完整路径及 `parse_job_id/status/current_stage/progress_percent/summary/file_results/retryable` |
| 9 | P05 数据集接口 | 路径参数使用 `{id}` 或“同 API” | 统一为 `{dataset_id}` 并分别写明 API-CMP-02/03/04 完整路径 |
| 10 | P06 创建并启动 | “先 API-INS-01，再调用启动接口” | 明确两次完整 POST 路径和 `task_uuid` 串联关系 |
| 11 | P07 展示字段 | 直接写数据库表和模糊状态 | 全部改为 API-INS-03 返回字段及数据字典名称 |
| 12 | P07 阶段列表 | 使用笼统“阶段、尝试次数、耗时” | 改为 `stage_runs[].stage_code/attempt_no/status/duration_ms/retryable/error_*` |
| 13 | P07 部分失败 | 仅写 Graph State | 改为 API-INS-03 `partial_failures`，明确失败单元、影响和局部可重试性 |
| 14 | P07 恢复与报告 | 未明确字段来源 | 增加 API-INS-03 `checkpoint_stage/retryable/report_uuid` 及 null 处理 |
| 15 | P07 轮询 | 仅写“2—5 秒轮询” | 增加立即请求、2 秒轮询、终态停止、后台降频、指数退避、401 刷新和 attempt 合并规则 |
| 16 | P07 重试/取消 | 路径使用 `{uuid}/{stage}`，P1 标识不统一 | 改为 API-INS-07/08 正式路径 `{task_uuid}/{stage_code}` 和统一 P1 标识 |
| 17 | P08 竞品字段 | 未显示集合版本和恢复阶段来源 | 增加 API-CMP-06 `competitor_set_version/checkpoint_stage/summary/scores` 映射 |
| 18 | P08 竞品确认 | 仅说明提交后恢复 Graph | 明确 API-CMP-07 Header、Body、202 语义、Outbox、`resume_command_id` 和冲突刷新规则 |
| 19 | P09 评论接口 | 路径使用 `{uuid}`，查看机会只写路由 | 统一 `{task_uuid}`；通过 API-INS-03 `report_uuid` 判断是否可进入 P10 |
| 20 | P10 报告与建议 | 部分操作仅写“无请求” | 改为完整前端路由，并明确进入页面后的 API-RPT-01/API-INS-05 |
| 21 | P10 Listing 入口 | 未统一 P1 文案 | API-LST-01 操作增加统一 P1 标识 |
| 22 | P11 专家复核 | 证据接口和恢复参数不完整 | 明确 API-REV-03 完整路径；API-INS-06 传 `expected_checkpoint_stage=expert_review` |
| 23 | P12 Listing | 路径使用 `{id}`、本地编辑口径模糊 | 改为 `{generation_id}`，全部操作统一 P1 标识，本地编辑通过 API-LST-03 提交 |
| 24 | P13 下载 | 只写“使用短效 URL” | 明确先调用 API-RPT-04，再访问签名 HTTPS 地址 |
| 25 | P13 导出历史 | P1 标识不统一，字段来源不明 | API-RPT-05 使用统一 P1 标识，并列出 `report_exports` 全部展示字段 |
| 26 | API 一致性章节 | 仍带“建议/缺口”语义 | 改为 V2 正式接口映射；P1 仅表示实施优先级，不表示接口未定义 |

