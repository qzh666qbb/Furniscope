# FurniScope FastAPI编码准入复核报告 V1.1

## 1. 复核结论

**编码准入结论：PASS**

数据库和Agent代码已不存在阻断FastAPI接入的结构问题。该结论表示可以进入FastAPI路由、Schema、Service、Repository与鉴权实现，不表示全部API已经完成。

## 2. 本轮关闭结果

| 原阻断 | 状态 | 关闭证据 |
|---|---|---|
| BLK-01 V2.1→V3关键字段错误 | 已关闭 | 迁移后34表；`business_model`及8张结果/报告表V3字段共9项存在；对应旧字段0；迁移事务内含结构断言 |
| BLK-02 确认事务与恢复不闭环 | 已关闭 | 中断发布原子事务、回答原子事务、safe active Checkpoint联表锁、waiting Stage闭合、事务Outbox、独立Worker及租约重领均已实现 |
| BLK-03 FastAPI基础契约缺失 | 已关闭至可编码 | 数据字典补齐密码哈希、认证会话、Token投影、Envelope、分页、ETag、模型错误；ADR锁定认证、并发、幂等、JSON和Model Router边界 |
| BLK-04 数据库摘要错误 | 已关闭 | `cluster_code`、market_metrics索引语义、报告任务内版本关系按实际DDL收口；建库统计更新为34表/34PK/89FK/104索引 |

## 3. 关键一致性检查

### 3.1 数据库

- 从零DDL：实际执行成功，34张表、34个主键、89个外键、104个索引。
- V2.1迁移：实际执行成功，34张表，关键旧物理字段为0，V3必需物理字段为9。
- 新增`auth_sessions`：仅存Refresh Token SHA-256摘要，Access/Refresh Token明文均不落库。
- V3从零DDL是新环境唯一物理基线；历史迁移库按V3代码查询兼容和数据无损验收，不再虚构“目录对象逐字同构”。历史附加列不得被新代码使用。

### 3.2 user_confirmation

发布中断时同一事务写：

1. safe active业务Checkpoint；
2. `task_stage_runs(user_confirmation,waiting_human)`；
3. pending `user_confirmations`；
4. task `waiting_human/user_confirmation/checkpoint_stage`。

回答时客户端只提交`confirmation_id/selected_option/user_input`。服务端读取恢复Stage并在同一事务完成responded、Stage succeeded、task queued和Outbox pending。Worker以服务端载荷执行`Command(resume=...)`，成功后消费事件与Checkpoint；领取后崩溃超过5分钟可重新领取。

### 3.3 Agent端到端

- 标准内存主链：I00—I19通过。
- interrupt/Outbox/Command(resume)：通过。
- 真实PostgreSQL合成家具案例：I00—I19通过，任务最终`succeeded/completed/100`，报告与模型追溯实际入库。
- 合成数据统一标记`demo_synthetic/synthetic_demo`，不得解释为真实市场事实。

## 4. 实际测试结果

执行：

```bash
PYTHONPYCACHEPREFIX=/tmp/furniscope_pycache .venv/bin/python -m compileall -q backend tests
FURNISCOPE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:<temp-port>/furniscope_test \
  .venv/bin/python -m unittest discover -s tests -v
```

结果：4/4通过。

| 测试 | 结果 |
|---|---|
| I00—I19内存完整链 | PASS |
| confirmation中断、受理、Outbox Worker恢复 | PASS |
| PostgreSQL事务回滚、幂等、跨租户、非safe点、租约重领 | PASS |
| PostgreSQL合成案例、报告、模型追溯和最终提交 | PASS |

## 5. 进入FastAPI后的强制边界

1. FastAPI不得接收客户端`tenant_id/checkpoint_stage/checkpoint_id`覆盖服务端上下文。
2. API-CFM-02返回202只表示Outbox可靠受理，不表示任务完成。
3. user/admin技术详情按数据字典白名单投影，不返回input/output引用、完整评论、Prompt正文或密钥。
4. `If-Match`使用`updated_at`强ETag；幂等使用各实体既有唯一键。
5. 合成适配器仅在非production且显式开关开启时装配。
6. 真实阿里云Model Router正式网关Envelope仍须在部署环境做契约测试；路径和密钥继续由环境变量提供。

## 6. 非阻断后续项

- FastAPI P0接口独立契约测试应随接口实现逐项补齐，不能用Agent测试替代。
- 真实家具本体、Prompt正文、评分阈值和授权市场数据接入后，需要在相同Schema下增加真实数据回归集。
- Redis队列并非确认恢复的前置条件；当前数据库Outbox Worker可先用于Demo，规模化时再增加调度层。

## 7. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| V1.0 | 2026-08-10 | 首次准入复核，结论BLOCKED |
| V1.1 | 2026-08-10 | 关闭迁移、确认事务、Outbox、认证及契约阻断；真实PostgreSQL 4/4测试通过；结论PASS |
