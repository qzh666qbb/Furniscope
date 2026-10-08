# FurniScope 系统复盘证据

日期：2026-10-07

权威结论：

- `Docs/项目设计文档/16_FurniScope_系统功能与业务流程复盘V1.md`

## 运行页面

- 企业用户页面：1440x1000、390x844，共16个状态。
- 管理后台：1440x1000、390x844，共2个状态。
- 企业页面结构化结果：`ui-scan.json`
- 管理后台结构化结果：`admin-ui-scan.json`
- 截图：`desktop-*.png`、`mobile-*.png`
- 18个状态均无console error、page error或failed request。
- 已知响应式失败：移动首页872px、移动Admin 734px、工作日记字段裁切、市场宽表、AI工作台预览裁切。

## 自动化

| 文件 | 结果 |
|---|---|
| `pytest-full.log` | 迁移前环境，暴露缺少v3.32启动门禁 |
| `migration-v3_32.log` | 测试数据库增量执行成功 |
| `pytest-full-after-v32.log` | 197 passed、2 skipped；7项仅因6379无Redis |
| `pytest-full-final.log` | 独立Redis 6397：204 passed、2 skipped |
| `frontend-build.log` | 生产构建通过；主JS 816.88kB |
| `sites-test.log` | 5 passed |
| `playwright-contract.log` | 20 passed、8 skipped |
| `SHA256SUMS` | 本目录33个证据文件的SHA256清单 |

两项Python跳过为仓库未提供的旧V4预测制品。Playwright的7项企业实测需要显式夹具，
1项需要旧全栈入口；本目录另保存了对当前本地API的真实用户和管理员页面扫描。

## 环境结论

- `/health/live`：HTTP 200
- `/health/ready`：HTTP 200
- `/openapi.json`：HTTP 200
- 文档本地链接检查：8份文档，0缺失
- `git diff --check`：通过

## 边界

自动化通过只证明现有断言满足。报告前端派生金额、多币种定价、SKU改名库存关联、
报告归档反向同步、多账号租户恢复和全入口响应式等问题尚无自动化保护，不能据此判定
真实经营发布可用。
