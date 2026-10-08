# FurniScope 生产部署指南

更新日期：2026-10-08

> 本指南区分“仓库配置可用”和“目标环境验收完成”。当前仓库已提供 S3 兼容对象存储、
> Caddy HTTPS、WAL 归档/PITR、监控和常驻 Worker 配置；真实域名、云凭据、异地归档及
> 恢复演练必须在目标基础设施另行完成。

## 环境要求

- Linux 主机、Docker Engine 24+、Compose v2；建议 8 vCPU、16 GiB 内存、100 GiB 可用磁盘。
- PostgreSQL 16、Redis 7、Python 3.12、Node 22 已由镜像固定。
- 生产准备真实域名和 ACME 邮箱；可使用仓库 Caddy Profile，也可接入等价的外部 TLS 网关。
- 只对公网开放 80/443。数据库、Redis、Backend、Worker 指标和监控管理端口保持私网可达。

## 必填配置

从 `.env.example` 复制 `.env`。至少替换全部 `POSTGRES_*_PASSWORD`、JWT RS256
私钥/公钥集、`INTERNAL_SERVICE_TOKEN`，并按需注入模型密钥。生产必须设置：

```dotenv
APP_ENV=production
CORS_ALLOWED_ORIGINS=["https://furniscope.example.com"]
JOB_QUEUE_MODE=redis
ANALYSIS_WORKER_MODE=external
ANALYSIS_TOOL_MODE=external
PRODUCT_PARSE_MODE=model
DATABASE_ALLOW_RUNTIME_DDL=false
DATABASE_REQUIRE_RUNTIME_ROLE_SEPARATION=true
STORAGE_BACKEND=s3
OBJECT_STORAGE_ENDPOINT=s3.example.com
OBJECT_STORAGE_BUCKET=furniscope-prod
OBJECT_STORAGE_REGION=cn-beijing
OBJECT_STORAGE_SECURE=true
OBJECT_STORAGE_AUTO_CREATE_BUCKET=false
FURNISCOPE_DOMAIN=furniscope.example.com
ACME_EMAIL=ops@example.com
```

`OBJECT_STORAGE_ENDPOINT` 只填 `host[:port]`，不带 URL scheme。生产存储桶须预创建且
默认私有；Access Key、Secret Key 和其他密钥不得写入镜像、Compose 文件或 Git。
生产配置若使用本地文件存储、明文对象存储或自动建桶，应用会拒绝启动。

## 首次部署

```bash
docker compose config --quiet
docker compose build --pull
docker compose --profile tls up -d postgres redis backend worker frontend caddy
docker compose ps
curl --fail https://furniscope.example.com/health/ready
```

新空卷只执行 `furniscope_postgresql_v3.sql`。已有数据库先备份，再按版本顺序执行适用迁移；不得把全量基线重复执行到已有库。

## 对象存储联调

MinIO Profile 仅用于本地 S3 兼容性联调，不等于生产对象存储验收：

```bash
docker compose --profile object-storage up -d minio minio-init
# 本地联调环境再设置：
# STORAGE_BACKEND=s3
# OBJECT_STORAGE_ENDPOINT=minio:9000
# OBJECT_STORAGE_SECURE=false
```

生产应连接启用 TLS、版本控制、服务端加密、生命周期和访问审计的私有 S3 兼容服务。
应用按租户前缀写入随机对象键，数据库只保存对象键与 SHA-256。

## 监控、备份与 PITR

```bash
# Grafana 密码必须是独立高熵值
docker compose --profile monitoring up -d
docker compose --profile operations up -d backup
docker compose --profile disaster-recovery up -d pitr-backup
```

Grafana 默认端口 3000，Prometheus 9090，Alertmanager 9093。看板由
`deploy/monitoring/grafana` 自动配置。Prometheus 抓取 API、Worker 心跳、PostgreSQL、
Redis、主机与容器指标；告警覆盖 API 不可用、5xx 比例、Worker 心跳、调度维护失败、
失败任务积压和磁盘不足。正式上线前必须在 `deploy/monitoring/alertmanager.yml`
增加企业邮件、Webhook 或 Pager 接收器并做触发演练。

备份写入命名卷 `postgres-backups`，默认每日执行并保留 14 天。生产还应把备份异步复制到启用版本控制/不可变策略的异地对象存储。

PostgreSQL Compose 已设置 `wal_level=replica`、`archive_mode=on`、最长 300 秒归档切段，
并将 WAL 写入独立卷。`pitr-backup` 使用复制账号周期执行 `pg_basebackup` 和
`pg_verifybackup`。可在容器中验证：

```bash
docker compose --profile disaster-recovery exec \
  -e POSTGRES_VERIFY_URL='postgresql://furniscope_monitor_login:***@postgres:5432/furniscope' \
  pitr-backup /scripts/verify_pitr_readiness.sh
```

恢复必须在隔离主机和新数据目录执行
`scripts/prepare_pitr_restore.sh BASE_BACKUP TARGET_DATA_DIRECTORY`，设置明确的
`RECOVERY_TARGET_TIME` 后启动 PostgreSQL，复核迁移、关键行数和审计链头。Compose
命名卷只用于本机验收；生产 WAL 必须连续复制到异地耐久介质，并完成实际恢复时间与
恢复点目标演练。仓库脚本不替代托管数据库高可用、跨故障域副本或跨区域复制。

## 扩容

- Backend 无状态，可在负载均衡后水平扩容；JWT 密钥集和内部令牌必须一致。
- Worker 可增加副本和 `WORKER_CONCURRENCY`，但需同时观察数据库连接、Redis lag 和模型配额。
- PostgreSQL 优先采用托管高可用与只读副本；Redis 生产应启用认证、TLS/私网和持久化。
- Backend 与 Worker 必须连接同一私有对象存储桶；不得依赖本地 `upload-data` 卷共享文件。

## 发布验收

执行 `scripts/security_audit.sh`、完整 Pytest、前端构建/Sites 测试、无 Mock Playwright
金路径、HTTPS `/health/ready`、API/Worker `/metrics` 抓取、对象上传读取、通知投递、
`scripts/verify_backup_restore.sh` 以及 PITR 恢复演练。任何测试被 skip、对象存储或
恢复未验证、告警未送达、生产密钥未轮换，都不能视为生产验收完成。
