# FurniScope 运维手册

更新日期：2026-09-10

## 日常检查

```bash
docker compose ps
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
docker compose logs --since=30m backend worker
```

Grafana 的 FurniScope Operations Overview 同时展示 QPS、p95 延迟、任务状态、模型调用及 CPU/内存。Prometheus 直接抓取 Backend 内网 `/metrics`；前端 Nginx 不代理该路径。

## 常见故障

| 现象 | 首查 | 处置 |
|---|---|---|
| readiness 503 | 响应错误码、PostgreSQL/Redis health | 恢复依赖，不盲目重启业务容器 |
| queued 长时间不动 | Worker 日志、Redis consumer group、失败/死信 | 确认租约与模型配额；Worker 会每 30 秒补投数据库 queued 任务 |
| 分析失败 | Admin diagnostics 的阶段、部分失败、模型运行 | 仅从安全 checkpoint 自动重试；等待用户确认时禁止管理员代答 |
| 5xx 升高 | Grafana 路由/状态码、request_id 日志 | 按 request_id 关联日志和审计，必要时回滚最近发布 |
| 磁盘不足 | node exporter、备份卷、PostgreSQL WAL | 先扩容或归档；不得直接删除数据库目录 |

## 手工备份

```bash
POSTGRES_BACKUP_URL='postgresql://USER@HOST:5432/furniscope' \
BACKUP_DIRECTORY=/srv/furniscope-backups scripts/backup_postgres.sh
```

产物为 PostgreSQL custom dump 与同名 SHA-256 文件。脚本先执行 `pg_restore --list` 再原子改名，未完成文件不会成为有效备份。

## 恢复与演练

恢复会清理目标库中同名对象，只能指向已核对的隔离目标：

```bash
RESTORE_DATABASE_URL='postgresql://USER@HOST:5432/furniscope_restore_test' \
CONFIRM_RESTORE=restore scripts/restore_postgres.sh /srv/furniscope-backups/furniscope-TIMESTAMP.dump
```

完整演练：

```bash
POSTGRES_BACKUP_URL='postgresql://USER@HOST:5432/furniscope' \
RESTORE_TEST_DATABASE_URL='postgresql://USER@HOST:5432/furniscope_restore_test' \
scripts/verify_backup_restore.sh
```

演练比较 `furniscope` schema 表数量；生产季度演练还应抽查关键租户、任务、报告和审计记录，并记录 RPO/RTO。不得把恢复目标设为源库。

## 告警接收器

仓库默认 Alertmanager 接收器仅保留告警状态，不向第三方发送数据。上线前由运维人员把批准的接收器加入 `deploy/monitoring/alertmanager.yml`，运行 `amtool check-config`，再制造测试告警确认 5 分钟内送达。接收器 URL/密码放到运行时 Secret，不提交仓库。

## 安全事件

发现疑似泄露时立即禁用旧密钥、轮换新密钥、检查 30 天调用日志/账单，并运行 `scripts/security_audit.sh`。数据库审计记录只保存动作元数据、哈希和白名单快照，不写密码、Token、模型密钥或评论全文。
