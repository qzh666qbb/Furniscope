# FurniScope认证与通用幂等迭代准入报告 V1

## 1. 结论

**本轮结论：PASS。认证接口与通用持久化幂等均已完成。**

- 通用持久化幂等Repository与Service：PASS，已完成并通过真实PostgreSQL测试。
- API-AUTH-01/02/03：PASS；服务端以全局唯一email定位user并读取tenant_id。

## 2. P0认证冲突

| 契约 | 当前定义 |
|---|---|
| API-AUTH-01 Body | 只有`email/password` |
| `users.email`约束 | 全系统唯一；现有重复数据检查为0 |
| 租户隔离要求 | 服务端必须确定唯一`tenant_id`，客户端不得伪造或覆盖认证上下文 |

产品已明确：用户不输入tenant_code。系统通过全局唯一email找到user，并由user.tenant_id自动建立租户上下文。

## 3. 推荐修订

原建议增加`tenant_code`已被产品决策否决。最终登录请求保持：

```json
{"email":"user@example.com","password":"***"}
```

最终规则：`users.email`全系统唯一；登录服务按规范化email查找唯一用户，从该用户记录读取tenant_id。前端不展示企业编码输入框，客户端也不得提交tenant_id。

### 关闭证据

DDL、迁移SQL、数据字典、数据库设计、API说明和现库`uk_users_email`约束均已同步；现有数据重复检查为0。

## 4. 已完成的认证能力

1. API-AUTH-01/02/03完整路由与OpenAPI编号；
2. Argon2id自描述密码哈希与恒定工作量未知账号校验；
3. RS256签发，私钥自动匹配唯一公钥kid；
4. 固定issuer/audience/claims、900秒Access TTL和60秒偏差；
5. opaque Refresh Token仅存SHA-256摘要，30天有效；
6. 每次刷新原子轮换；旧Token重放撤销整个family；
7. Access Token不落库；
8. user/tenant状态每次请求查库复核；
9. user/admin两角色与服务端tenant上下文；
10. 登录和刷新不写api_idempotency_records。

## 5. 已完成的通用幂等能力

实现文件：

- `backend/furniscope_api/repositories/idempotency_repository.py`
- `backend/furniscope_api/services/idempotency_service.py`
- `tests/test_idempotency_postgres.py`

已实现：

1. `tenant_id+route_code+idempotency_key`唯一作用域；
2. 规范化、排序、脱敏后生成SHA-256请求哈希；
3. 首次请求取得execute资格；
4. processing返回`IDEMPOTENCY_IN_PROGRESS`；
5. 相同Key不同hash返回`IDEMPOTENCY_CONFLICT`；
6. completed/failed复用原HTTP状态和脱敏Envelope；
7. PostgreSQL唯一约束裁决并发；
8. 24小时过期；
9. 所有查询和更新强制tenant范围；
10. 认证路由显式禁止写入幂等表；
11. 密码、Authorization、Cookie、Token、API Key、Prompt、评论全文和文件字节不持久化。

## 6. 实际测试

```text
25 passed
```

覆盖登录成功/失败一致性、disabled/suspended、RS256/kid/claims、错误issuer/audience/过期Token、Refresh轮换/过期/重放、family撤销、当前user/admin、自动tenant解析、Token不落库、OpenAPI，以及幂等复用/冲突/并发/脱敏。

## 7. 下一步

可以进入“产品与数据集API迭代”。后续业务写接口接入本轮IdempotencyService；所有Repository继续强制tenant范围。

## 8. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-10 | 完成通用持久化幂等；记录登录租户唯一定位P0阻断与推荐修订 |
| V1.1 | 2026-08-10 | 产品决定邮箱全局唯一并自动定位tenant；完成三个认证接口、Refresh安全和25项全量测试，结论PASS |
