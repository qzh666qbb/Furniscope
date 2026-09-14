# FurniScope 生产部署指南

更新日期：2026-09-10

## 环境要求

- Linux 主机、Docker Engine 24+、Compose v2；建议 8 vCPU、16 GiB 内存、100 GiB 可用磁盘。
- PostgreSQL 16、Redis 7、Python 3.12、Node 22 已由镜像固定。
- 域名入口应由外部 TLS 反向代理提供；只暴露前端 8080，数据库、Redis、Backend 与指标端点保持内网可达。

## 必填配置

从 `.env.example` 复制 `.env`。至少替换 `POSTGRES_PASSWORD`、JWT RS256 私钥/公钥集、`INTERNAL_SERVICE_TOKEN`，并按需注入模型密钥。生产必须设置 `APP_ENV=production`、精确的 `CORS_ALLOWED_ORIGINS`、`JOB_QUEUE_MODE=redis`、`ANALYSIS_WORKER_MODE=external` 与 `ANALYSIS_TOOL_MODE=external`。密钥不得写入镜像、Compose 文件或 Git。

## 首次部署

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d postgres redis backend worker frontend
docker compose ps
curl --fail http://127.0.0.1:8080/health/ready
```

新空卷只执行 `furniscope_postgresql_v3.sql`。已有数据库先备份，再按版本顺序执行适用迁移；不得把全量基线重复执行到已有库。

## 监控与每日备份

```bash
# Grafana 密码必须是独立高熵值
docker compose --profile monitoring up -d
docker compose --profile operations up -d backup
```

Grafana 默认端口 3000，Prometheus 9090，Alertmanager 9093。看板由 `deploy/monitoring/grafana` 自动配置。告警规则覆盖 API 不可用、5xx 比例、失败任务积压和磁盘不足；正式上线前必须在 `deploy/monitoring/alertmanager.yml` 增加企业邮件、Webhook 或 Pager 接收器并做触发演练。

备份写入命名卷 `postgres-backups`，默认每日执行并保留 14 天。生产还应把备份异步复制到启用版本控制/不可变策略的异地对象存储。

## 扩容

- Backend 无状态，可在负载均衡后水平扩容；JWT 密钥集和内部令牌必须一致。
- Worker 可增加副本和 `WORKER_CONCURRENCY`，但需同时观察数据库连接、Redis lag 和模型配额。
- PostgreSQL 优先采用托管高可用与只读副本；Redis 生产应启用认证、TLS/私网和持久化。
- 上传文件使用共享对象存储时，替换本地 `upload-data` 卷并保持租户前缀隔离。

## 发布验收

执行 `scripts/security_audit.sh`、完整 Pytest、前端构建/Sites 测试、无 Mock Playwright 金路径、`/health/ready`、`/metrics` 抓取以及 `scripts/verify_backup_restore.sh`。任何测试被 skip、恢复未验证或生产密钥未轮换，都不能视为生产验收完成。
