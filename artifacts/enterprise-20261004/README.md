# 企业决策与数据闭环：本地验收证据

日期：2026-10-04。范围以[15总体设计](../../Docs/项目设计文档/15_FurniScope_企业决策与数据闭环总体设计V1.md)为准。

所有销售、机会和账户为隔离数据库中的合成工程样本。截图中的0% WAPE来自固定日销量样本，只验证训练、预测和发布流程，不能证明真实预测精度。未部署、提交或推送；公网演示与本次工作区不同。

## 结果与文件

| 验证 | 结果 | 原始记录 |
|---|---|---|
| Python全`tests`，真实PG16 | 123通过、6跳过 | [python-tests.log](python-tests.log) |
| 独立Redis6396补测 | 4通过 | [redis-tests.log](redis-tests.log) |
| Python合并覆盖 | 127项通过，2项仍跳过 | 缺少旧V4的daily.pkl/state，未计为通过 |
| 相关前端契约 | 7通过 | [frontend-contract.log](frontend-contract.log) |
| 企业真实API浏览器链路 | 3通过 | [enterprise-browser.log](enterprise-browser.log) |
| 生产构建 | 通过，有bundle体积提示 | [frontend-build.log](frontend-build.log) |
| Sites打包/路由契约 | 5通过 | [sites-tests.log](sites-tests.log) |

本次全`tests`运行时未配置Redis，4项队列测试随后在单独启动的临时Redis中运行成功；不把两个批次叙述为单次“127 passed”。新企业引擎已实际训练、留出评测并调用HTTP预测，不依赖旧V4制品。

页面截图：

- [桌面训练与评测结果](enterprise-training-desktop.png)
- [390px移动端训练页](enterprise-training-mobile.png)
- [企业策略与已确认能力](enterprise-strategy-desktop.png)
- [硬条件明细与保存后重新读取的反馈](enterprise-decision-desktop.png)

浏览器验证覆盖文件→字段口径→预检→确认→首次发布→真实预测，以及短历史重建未达标保留旧模型、策略/事实/反馈刷新后持久化。页面不拦截或模拟API；市场机会本身是预置的合成样本，不声称该项浏览器测试重新运行了完整市场研究。

## 内容完整性

- 测试时基线提交：`38db917e39ac0de26c9f6290b10a3c1692356ffb`。
- [source-sha256.json](source-sha256.json)记录工作区代码、测试、SQL、相关文档和依赖文件的真实内容SHA。含任务开始前已存在的未提交改动，不代表所有差异均由本轮产生，也不是已提交版本。
- [SHA256SUMS](SHA256SUMS)记录本目录证据文件及源文件清单的校验值（不包含它自身）。
- 不保存登录密码、Token、私钥、浏览器认证存储或原始业务数据；临时测试身份文件留在`/tmp`且权限600。

## 复现

Python3.12环境按根目录`requirements.lock`安装，前端按`package-lock.json`安装。以下仅适用于独立本地测试库，先执行根目录DDL及其引用迁移；不要指向业务数据库。数据库账户需具备建库和创建/授予`furniscope_tenant`角色的权限。

```bash
createdb furniscope_enterprise_test_local
psql -d furniscope_enterprise_test_local -v ON_ERROR_STOP=1 -f furniscope_postgresql_v3.sql
```

本次实际Python环境：`/tmp/furniscope-enterprise-venv/bin/python`。可在自己的Python3.12环境中执行（连接串替换为本地账户）：

```bash
PYTHONPATH=.:backend:tests \
FURNISCOPE_TEST_DATABASE_URL=postgresql://<account>@localhost/furniscope_enterprise_test_local \
python -m pytest tests -q --tb=short -rs
```

队列测试会清空目标Redis数据库，应使用独立临时实例：

```bash
redis-server --bind 127.0.0.1 --port 6396 --save '' --appendonly no
```

在另一终端执行：

```bash
PYTHONPATH=.:backend:tests \
FURNISCOPE_TEST_DATABASE_URL=postgresql://<account>@localhost/furniscope_enterprise_test_local \
FURNISCOPE_TEST_REDIS_URL=redis://127.0.0.1:6396/0 \
python -m pytest tests/test_job_queue.py -q --tb=short
```

浏览器使用独立合成账户。启动夹具API，每次启动创建新账户并覆盖临时身份文件；运行测试前重新启动以验证首次建模：

```bash
PYTHONPATH=.:backend:tests python scripts/serve_enterprise_e2e.py \
  --database-url postgresql://<account>@localhost/furniscope_enterprise_test_local \
  --work-dir /tmp/furniscope-enterprise-preview --port 8016
```

在`frontend/`启动代理：

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8016 npm run dev -- --host 127.0.0.1 --port 4186
```

另一个终端在`frontend/`执行：

```bash
FURNISCOPE_ENTERPRISE_FIXTURE=/tmp/furniscope-enterprise-preview/identities.json \
npx playwright test tests/e2e/enterprise-live.spec.js --workers=1
```

相关前端契约与构建：

```bash
npx playwright test tests/e2e/forecast-append-tab.spec.js tests/e2e/admin-model-actions.spec.js tests/e2e/forecast-history.spec.js tests/e2e/market-insights-contract.spec.js tests/e2e/session-expiry-contract.spec.js
npm run build
npm run test:sites
```

Playwright配置会复用或启动4173的开发前端，企业实测另外使用4186代理8016。测试产物位于`frontend/test-results/`，后续运行会清理，因此最终截图复制到本目录。
