# FurniScope 产品与市场数据集 API 实现说明 V1

## 1. 实现结论

本轮已实现 API V3 的 API-PRD-01—07 与 API-DAT-01—04，共 11 个 P0 接口。全部业务查询均由服务端认证上下文注入 `tenant_id`，业务接口只允许 `user` 调用。

## 2. 已实现能力

- 产品创建、分页查询、画像详情、基于强 ETag 的更新；
- 产品画像草稿版本、属性证据字段、完整确认和幂等确认；
- 私有文件元数据、上传安全边界、解析任务受理、文件级结果和任务查询；
- 授权市场数据集创建、分页、质量与范围详情；
- 明确标记的合成/授权 JSON 竞品和评论导入、去重、质量状态更新；
- API-PRD-01/05/07、API-DAT-01/02 使用持久化 HTTP 幂等；
- `If-Match` 使用 `updated_at` 规范化后的强 ETag，不新增业务 version 列。

## 3. 数据库对齐

新增事务迁移 `migrations/v3_api_contract_alignment.sql`，修复 V2.1 迁移库与 V3 从零 DDL 的以下漂移：

- 产品画像 `needs_confirmation` 映射为 `parsed`；
- 产品属性统一为 `profile_version_id + attribute_code + value`；
- 解析任务字段、状态、计数列和 UUID 类型对齐；
- 解析文件结果补充 `tenant_id`；
- `file_assets.created_by` 与 `market_datasets.import_asset_id` 对齐。

迁移在事务中执行，先转换与校验数据，后删除遗留列，异常时整体回滚。

## 4. Demo 与生产边界

`DemoStorage`、产品资料后台处理器和 JSON 市场导入器均标记 `demo_only`，仅可在 development/test 环境运行：

- 文件以租户目录隔离，权限为私有文件；
- 文件内容不写入 PostgreSQL；
- 合成数据必须使用 `source_type=demo_synthetic` 或在来源名称中明确标识；
- production 环境强制使用S3兼容私有对象存储与TLS；缺少端点/凭据、桶不存在或服务不可用时返回 `STORAGE_UNAVAILABLE`，不会伪装成功；
- 当前 Demo 解析器完成安全入库和文件级任务闭环，不把空结果伪装成模型抽取属性；真实语义抽取应由后续 Model Router Worker 接管。

P0 JSON 市场导入固定使用 `platform_id_latest` 去重策略和数据集已确认字段映射，避免执行未经登记的转换表达式。

## 5. 验证结果

- Python `compileall`：通过；
- 全量测试：27 passed；
- OpenAPI 接口编号：11/11 存在，无缺失；
- 覆盖：RS256、user/admin、租户隔离、ETag、幂等成功/失败重放、产品画像确认、解析任务、合成市场数据导入和质量状态。

已知非阻断警告：Starlette `TestClient` 提示未来迁移到 `httpx2`，不影响当前运行。

## 6. 下一迭代

现行实现已接入S3兼容对象存储抽象和独立Redis Worker。生产部署前仍需在目标云桶完成
权限、版本、加密和故障演练，并接入恶意文件扫描；这些是基础设施验收，不改变当前
API V3业务契约。
