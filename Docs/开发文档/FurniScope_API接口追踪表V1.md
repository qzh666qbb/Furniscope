# FurniScope API接口追踪表 V1

## 1. 编码前结论

**FastAPI基础设施准入：PASS。**

数据库、Agent与API契约均已通过准入。原四项P0缺口已分别在API V3.2、数据字典V3.2、PostgreSQL V3 DDL、页面V3及运行配置中关闭。本结论只授权进入FastAPI基础设施迭代，不代表业务路由已实现。

## 2. 全量接口追踪表

Base URL为`/api/v1`；Internal接口使用文档给定的`/internal/v1`。所有租户业务查询由认证上下文注入`tenant_id`，客户端不得提交或覆盖。

| 接口编号 | 优先级 | 方法、路径 | 权限 | 请求字段 | 响应字段 | 主要错误码 | 数据表 | Agent调用 |
|---|---|---|---|---|---|---|---|---|
| API-AUTH-01 | P0 | POST `/api/v1/auth/login` | Public | email,password | access_token,refresh_token,token_type,expires_in,user | AUTH_INVALID_CREDENTIALS,USER_DISABLED,TENANT_SUSPENDED,AUTH_RATE_LIMITED | users,tenants,auth_sessions | 无 |
| API-AUTH-02 | P0 | POST `/api/v1/auth/refresh` | Public | refresh_token | access_token,refresh_token,token_type,expires_in | AUTH_REFRESH_TOKEN_INVALID,AUTH_REFRESH_TOKEN_EXPIRED,AUTH_TOKEN_REUSE_DETECTED | auth_sessions,users,tenants | 无 |
| API-AUTH-03 | P0 | GET `/api/v1/users/me` | user/admin | Authorization | user_id,email,name,role_code,status,tenant | AUTH_TOKEN_INVALID,TENANT_CONTEXT_MISMATCH | users,tenants | 无 |
| API-DSH-01 | P0 | GET `/api/v1/dashboard/summary` | user/admin | recent_limit | metrics,recent_tasks,confirmation_todos,recent_reports | DASHBOARD_FILTER_INVALID,TENANT_CONTEXT_MISMATCH,DASHBOARD_QUERY_FAILED | products,analysis_tasks,user_confirmations,analysis_reports | 无 |
| API-PRD-01 | P0 | POST `/api/v1/products` | user | Idempotency-Key; sku,name,category_code,description | product_id,sku,name,category_code,analysis_status,current_profile_version_id | PRODUCT_SKU_CONFLICT,IDEMPOTENCY_CONFLICT,IDEMPOTENCY_IN_PROGRESS,PRODUCT_CATEGORY_INVALID | products,api_idempotency_records | 无 |
| API-PRD-02 | P0 | GET `/api/v1/products` | user | page,page_size,status,category_code,keyword | items,total,page,page_size,has_next | PAGINATION_INVALID,TENANT_CONTEXT_MISMATCH | products | 无 |
| API-PRD-03 | P0 | GET `/api/v1/products/{product_id}` | user | product_id,profile_version_id | product,profile_version,completeness_score,source_summary,attributes | PRODUCT_NOT_FOUND,PRODUCT_PROFILE_NOT_FOUND,TENANT_CONTEXT_MISMATCH | products,product_profile_versions,product_attributes | 无 |
| API-PRD-04 | P0 | PATCH `/api/v1/products/{product_id}` | user | If-Match; name,description,analysis_status,attributes | product,profile_version_id,resource_version | PRODUCT_NOT_FOUND,RESOURCE_VERSION_CONFLICT,PRODUCT_PROFILE_IMMUTABLE,PRODUCT_ATTRIBUTE_INVALID | products,product_profile_versions,product_attributes | 无 |
| API-PRD-05 | P0 | POST `/api/v1/products/{product_id}/assets:parse` | user | Idempotency-Key; files,source_type,parse_config | parse_job_id,product_id,status,progress_percent,current_stage | FILE_EMPTY,FILE_SIZE_EXCEEDED,FILE_TYPE_UNSUPPORTED,PARSE_CONFIG_INVALID,IDEMPOTENCY_CONFLICT | products,file_assets,product_parse_jobs,product_parse_job_files | 解析Worker；非市场Agent主图 |
| API-PRD-06 | P0 | GET `/api/v1/product-parse-jobs/{parse_job_id}` | user | parse_job_id,include_files | parse_job字段,summary,file_results,retryable,failure | PARSE_JOB_NOT_FOUND,TENANT_CONTEXT_MISMATCH | product_parse_jobs,product_parse_job_files,file_assets | 无 |
| API-PRD-07 | P0 | POST `/api/v1/products/{product_id}/profile:confirm` | user | Idempotency-Key,If-Match; profile_version_id,confirmed_attribute_codes | product_id,profile_version_id,status,completeness_score,confirmed_at | RESOURCE_VERSION_CONFLICT,PRODUCT_PROFILE_CONFLICTED,PRODUCT_PROFILE_INCOMPLETE,PRODUCT_PROFILE_NOT_FOUND | products,product_profile_versions,product_attributes | 无；冲突时由统一confirmation处理 |
| API-DAT-01 | P0 | POST `/api/v1/market-datasets` | user | Idempotency-Key; scope/source/field_mapping | dataset_id,name,status,platform,market_country,category_code,version_no | DATASET_SCOPE_INVALID,DATASET_AUTHORIZATION_REQUIRED,FIELD_MAPPING_INVALID,IDEMPOTENCY_CONFLICT | market_datasets,api_idempotency_records | 无 |
| API-DAT-02 | P0 | POST `/api/v1/market-datasets/{dataset_id}/imports` | user | Idempotency-Key; files,field_mapping,deduplication_strategy | dataset_id,status=validating,accepted_at | DATASET_NOT_FOUND,DATASET_IMPORT_IN_PROGRESS,FIELD_MAPPING_INVALID,DATASET_FILE_INVALID | market_datasets,file_assets,market_listings,reviews,api_idempotency_records | 导入Worker；非市场Agent主图 |
| API-DAT-03 | P0 | GET `/api/v1/market-datasets` | user | 分页及platform/country/category/status | 数据集分页字段 | DATASET_FILTER_INVALID,TENANT_CONTEXT_MISMATCH | market_datasets | 无 |
| API-DAT-04 | P0 | GET `/api/v1/market-datasets/{dataset_id}` | user | dataset_id | 数据集、field_mapping、质量和范围 | DATASET_NOT_FOUND,DATASET_NOT_READY,TENANT_CONTEXT_MISMATCH | market_datasets | 无 |
| API-INS-01 | P0 | POST `/api/v1/analysis-tasks` | user | Idempotency-Key; job/product/profile/dataset/target/config | task_uuid,job字段,status,stage,progress_percent,report_uuid | PRODUCT_NOT_FOUND,DATASET_NOT_FOUND,IDEMPOTENCY_CONFLICT,PRODUCT_PROFILE_NOT_CONFIRMED,DATASET_NOT_READY,TASK_SCOPE_MISMATCH | analysis_tasks及输入实体 | 不启动图；创建draft |
| API-INS-02 | P0 | POST `/api/v1/analysis-tasks/{task_uuid}:start` | user | Idempotency-Key,task_uuid | task_uuid,status,stage,progress_percent,checkpoint_stage,retryable | TASK_NOT_FOUND,TASK_ALREADY_STARTED,IDEMPOTENCY_CONFLICT,IDEMPOTENCY_IN_PROGRESS,TASK_PREFLIGHT_FAILED,COMPUTE_QUOTA_EXCEEDED | analysis_tasks,task_stage_runs,workflow_checkpoints,api_idempotency_records | `FurniScopeAgentEngine.run` |
| API-INS-03 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}` | user/admin | include_stage_runs,stage_run_limit | task状态、五阶段、stage_runs、partial_failures、confirmation、report_uuid | TASK_NOT_FOUND,TENANT_CONTEXT_MISMATCH,WORKFLOW_STATE_INCONSISTENT | analysis_tasks,task_stage_runs,workflow_partial_failures,user_confirmations,workflow_checkpoints,analysis_reports | 无 |
| API-INS-04 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/result` | user | limits | report_summary,data_scope,competitor_summary,clusters,opportunities,recommendations,partial_failures | TASK_NOT_FOUND,TASK_RESULT_NOT_READY,TASK_RESULT_INCOMPLETE | analysis_reports及全部结果表 | 无 |
| API-CFM-01 | P0 | GET `/api/v1/user-confirmations` | user | status,task_uuid,page,page_size | confirmation九字段分页 | CONFIRMATION_FILTER_INVALID,CONFIRMATION_USER_REQUIRED | user_confirmations,analysis_tasks | 无 |
| API-CFM-02 | P0 | POST `/api/v1/user-confirmations/{confirmation_id}:respond` | user | Idempotency-Key; selected_option,user_input | confirmation_id,accepted,resumed_from_checkpoint,task_uuid,status,checkpoint_stage | CONFIRMATION_NOT_FOUND,CONFIRMATION_USER_REQUIRED,TENANT_CONTEXT_MISMATCH,CONFIRMATION_ALREADY_RESPONDED,CONFIRMATION_EXPIRED,WORKFLOW_CHECKPOINT_CONFLICT,IDEMPOTENCY_CONFLICT,CONFIRMATION_OPTION_INVALID,CONFIRMATION_INPUT_INVALID,WORKFLOW_RESUME_UNAVAILABLE | user_confirmations,workflow_checkpoints,task_stage_runs,analysis_tasks,workflow_control_events | Repository原子受理；Worker `Command(resume=...)` |
| API-CMP-01 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/competitors` | user | 类型/版本/分页/排序 | 集合版本、摘要、items及匹配分 | TASK_NOT_FOUND,COMPETITOR_RESULT_NOT_READY,COMPETITOR_FILTER_INVALID | analysis_tasks,competitor_matches,market_listings,reviews | 无 |
| API-REV-01 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/review-aspects` | user | taxonomy/sentiment/listing/confidence/分页 | 观点、原文Span、置信度、model_run_id | REVIEW_ANALYSIS_NOT_READY,TASK_NOT_FOUND,REVIEW_FILTER_INVALID | review_aspects,reviews,ai_model_runs | 无 |
| API-REV-02 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/insight-clusters` | user | sentiment,taxonomy,min_confidence,分页 | cluster字段、频率、置信度、代表观点 | INSIGHT_RESULT_NOT_READY,TASK_NOT_FOUND | insight_clusters,cluster_members,review_aspects | 无 |
| API-EVD-01 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/evidence` | user | claim_type,claim_id,claim_path,分页 | claim字段,evidence_drawer | EVIDENCE_NOT_FOUND,EVIDENCE_CLAIM_INVALID,EVIDENCE_AUDIT_INCOMPLETE | evidence_links及证据源表 | 无 |
| API-OPP-01 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/opportunities` | user | level,confidence,分页 | 机会、六项分、置信度、manufacturing_fit、证据 | OPPORTUNITY_RESULT_NOT_READY,TASK_NOT_FOUND | market_opportunities,evidence_links | 无 |
| API-REC-01 | P0 | GET `/api/v1/analysis-tasks/{task_uuid}/recommendations` | user | opportunity,type,priority,分页 | 工程建议、验证方法、证据、model_run_id | RECOMMENDATION_RESULT_NOT_READY,TASK_NOT_FOUND | product_recommendations,evidence_links,ai_model_runs | 无 |
| API-RPT-01 | P0 | GET `/api/v1/reports/{report_uuid}` | user | report_uuid,include_model_trace | 报告完整在线投影 | REPORT_NOT_FOUND,REPORT_NOT_READY,REPORT_EVIDENCE_AUDIT_FAILED,TENANT_CONTEXT_MISMATCH | analysis_reports,analysis_tasks,ai_model_runs | 无 |
| API-ADM-01 | P1 | GET `/api/v1/admin/users` | admin | tenant_id,status,keyword,分页 | user分页 | ADMIN_REQUIRED,USER_FILTER_INVALID | users,tenants | 无 |
| API-ADM-02 | P1 | PATCH `/api/v1/admin/users/{user_id}` | admin | If-Match; name,status,role_code | user,resource_version | ADMIN_REQUIRED,USER_NOT_FOUND,RESOURCE_VERSION_CONFLICT,USER_ROLE_INVALID | users,audit_logs | 无 |
| API-ADM-03 | P1 | GET `/api/v1/admin/model-routes/{task_type}` | admin | task_type | 非敏感模型路由配置 | ADMIN_REQUIRED,MODEL_ROUTE_NOT_FOUND,MODEL_ROUTE_CONFIG_INVALID | model_route_configs | 无 |
| API-ADM-04 | P1 | PUT `/api/v1/admin/model-routes/{task_type}` | admin | Idempotency-Key,If-Match;模型与算力配置 | 更新配置,updated_at | ADMIN_REQUIRED,MODEL_ROUTE_NOT_FOUND,MODEL_ROUTE_CONFIG_INVALID,RESOURCE_VERSION_CONFLICT,IDEMPOTENCY_CONFLICT | model_route_configs,audit_logs | 无 |
| API-ADM-05 | P1 | GET `/api/v1/admin/prompt-templates` | admin | 筛选、分页 | Prompt版本分页 | ADMIN_REQUIRED,PROMPT_FILTER_INVALID | prompt_templates | 无 |
| API-ADM-06 | P1 | POST `/api/v1/admin/prompt-templates` | admin | Idempotency-Key;模板字段 | 新模板版本 | ADMIN_REQUIRED,PROMPT_VERSION_CONFLICT,IDEMPOTENCY_CONFLICT,PROMPT_SCHEMA_INVALID | prompt_templates,audit_logs | 无 |
| API-ADM-07 | P1 | GET `/api/v1/admin/analysis-tasks/{task_uuid}/diagnostics` | admin | 诊断include开关 | 脱敏任务、Stage、Checkpoint、模型和控制事件 | ADMIN_REQUIRED,ADMIN_DIAGNOSTIC_SCOPE_DENIED,TASK_NOT_FOUND | 工作流控制表、ai_model_runs | 无 |
| API-ADM-08 | P1 | POST `/api/v1/admin/analysis-tasks/{task_uuid}:recover` | admin | Idempotency-Key; event_type,checkpoint_id,stage_code,payload | event_uuid及控制事件状态 | WORKFLOW_CHECKPOINT_CONFLICT,WORKFLOW_CONFIRMATION_PENDING,WORKFLOW_NOT_RECOVERABLE,ADMIN_REQUIRED,WORKFLOW_RECOVERY_INPUT_INVALID | workflow_control_events,workflow_checkpoints,analysis_tasks | Supervisor控制事件 |
| API-MDL-01 | Internal | POST `/internal/v1/model-router/invoke` | service | 服务Token;任务/Stage/Prompt/input/schema | model_run及结构输出、tokens、耗时 | MODEL_OUTPUT_SCHEMA_INVALID,MODEL_RATE_LIMITED,MODEL_UPSTREAM_ERROR,MODEL_UPSTREAM_TIMEOUT | prompt_templates,model_route_configs,ai_model_runs | `ModelRouterClient.structured_generate` |
| API-MDL-02 | Internal | POST `/internal/v1/model-router/embeddings` | service | 服务Token;任务/Stage/input_hash/texts | model_run_id,model_id,dimension,vectors,status,token_count,latency_ms | EMBEDDING_INPUT_INVALID,MODEL_UPSTREAM_ERROR,MODEL_UPSTREAM_TIMEOUT | model_route_configs,ai_model_runs；向量由调用节点按业务用途持久化 | Model Router封装；阿里云OpenAI兼容`/embeddings` |
| API-MDL-03 | Internal | POST `/internal/v1/model-router/rerank` | service | 服务Token;任务/Stage/query/candidates/hash/top_k | model_run_id,model_id,results,status,latency_ms | RERANK_INPUT_INVALID,MODEL_UPSTREAM_ERROR,MODEL_UPSTREAM_TIMEOUT | model_route_configs,ai_model_runs,competitor_matches | Model Router封装；阿里云`/reranks` |

## 3. 六项编码前检查

| 检查项 | 结果 | 证据与影响 |
|---|---|---|
| API请求/响应字段均在数据字典 | PASS | API V3.2与数据字典V3.2已统一`resource_version`、`ended_at`、`failed_count/total_count`、`cluster_code`、`clean`、`validating`及Internal模型投影字段 |
| If-Match有数据库承载 | PASS | 使用各资源`updated_at`生成强ETag；必须在同一SQL事务比较并更新，不需要新增version列 |
| Idempotency-Key均有持久化与冲突检测 | PASS | 新增`api_idempotency_records`；唯一键、请求哈希、处理中冲突、响应复用与过期清理语义已锁定并通过DDL验证 |
| 密码、Access/Refresh Token协议锁定 | PASS | RS256、claims、issuer/audience、kid轮换、TTL/偏差、密钥环境变量、逐请求用户/租户状态复核和统一复用错误码均已锁定 |
| user_confirmation API与恢复事务 | PASS | Body不含tenant/checkpoint字段；服务端推导Stage；原子回答与Outbox、租约重领已通过真实PostgreSQL测试 |
| user/admin Stage与诊断可见范围 | PASS | user白名单六字段；admin增加脱敏错误；均禁止input_ref/output_ref、Prompt、密钥与评论全文 |
| Internal Model Router协议 | PASS | API V3.2锁定阿里云官方OpenAI兼容embedding与qwen3-rerank路径、Envelope、维度、评分与错误映射；环境配置分离三类base URL |

## 4. P0问题关闭记录

### API-GATE-P0-01 字段命名和枚举冲突（已关闭）

已完成数据字典V3.2、API V3.2和页面V3的同口径修订；不改变业务能力。

1. API-PRD-04成功字段统一为`resource_version`，删除泛化`version`。
2. API-INS-03阶段结束时间统一为`ended_at`；部分失败统一返回`failed_count/total_count`。
3. API-REV-02聚类编码统一为`cluster_code`。
4. API-PRD-06安全枚举统一为数据库`pending/clean/rejected/quarantined`，删除`safe`。
5. API-DAT-02受理状态统一为数据库合法状态，例如`validating`，不得返回不存在的`importing`。
6. `user_id`已定义为受控BIGINT API投影，仅本人信息与admin租户范围内用户管理接口可返回。
7. 修复API-AUTH-01示例中未正确闭合的JSON代码块。

关闭证据：旧字段扫描无残留；API示例枚举已与数据字典和DDL CHECK统一。

### API-GATE-P0-02 写接口幂等承载不足（已关闭）

已新增统一持久化实体`api_idempotency_records`，覆盖tenant、actor、method、route、key、request hash、处理状态、资源定位、脱敏响应和过期时间；禁止以内存缓存作为一致性依据。

覆盖API-PRD-01、API-PRD-05、API-DAT-01、API-DAT-02、API-INS-01、API-INS-02和API-CFM-02等要求Header幂等键的写接口；业务实体唯一键继续作为第二层防线。

关闭证据：同tenant+接口+key+同request_hash复用原响应，不同hash返回`IDEMPOTENCY_CONFLICT`，并发中的未完成请求返回`IDEMPOTENCY_IN_PROGRESS`；数据库唯一约束已通过从零建库验证。

### API-GATE-P0-03 Access Token安全协议不完整（已关闭）

API V3.2已锁定：

- 算法与允许列表；issuer、audience、`sub/user_id/tenant_id/role_code/iat/nbf/exp/jti`；
- 签名/验签密钥环境变量名称、密钥轮换与`kid`规则；
- Access TTL、允许时钟偏差；
- user disabled/locked、tenant suspended时是否每次查库并立即拒绝；
- 日志和异常脱敏规则；
- Refresh Token复用错误码统一为一个值。

关闭证据：实现者无需再选择安全协议即可完成验签、用户状态复核、401/403映射和轮换测试；`.env.example`已给出非秘密配置名且不包含密钥。

### API-GATE-P0-04 Internal embeddings/rerank协议缺失（已关闭）

API V3.2已锁定阿里云官方embedding与qwen3-rerank协议、请求/响应映射、向量维度、相对评分语义和错误映射；数据字典V3.2已定义`dimension/vectors/token_count/results[].candidate_id/rerank_score/rank`，运行配置已拆分三类base URL和path。

## 5. 本轮执行决定

- 已完成接口追踪表复核，全部编码前检查为PASS。
- 本轮只完成准入契约修订，尚未创建FastAPI应用工厂、鉴权或业务路由代码。
- 已完成且通过的PostgreSQL与LangGraph代码不回退、不重做。
- 可以直接进入“FastAPI基础设施迭代”，该迭代仍不实现业务路由。

## 6. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-10 | 建立39个外部/Internal接口追踪，完成字段、ETag、幂等、认证、确认、诊断和Model Router准入检查，结论BLOCKED |
| V1.1 | 2026-08-10 | 关闭字段、持久化幂等、Access JWT与Internal模型协议四项P0问题；FastAPI基础设施准入结论更新为PASS |
