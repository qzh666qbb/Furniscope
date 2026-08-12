# FurniScope 前端技术栈变更决策 V1

## 1. 决策

自 2026-08-11 起，FurniScope 当前开发基线的前端技术栈由 Svelte 调整为：

```text
React 19 + Vite
```

现有高保真 UI 实现作为前端视觉与组件迁移起点：

```text
UI_Desgin/UI_V2/product-design-plugin-product-design-openai/furniscope-app
```

## 2. 决策原因

1. UI_V2 已使用 React 19 + Vite 完成主要页面、组件、样式和视觉资产；
2. 继续使用 React 可避免将成熟原型整体重写为 Svelte；
3. FastAPI 通过 RESTful API 与前端通信，切换前端框架不影响后端业务契约；
4. React 生态、图表、测试、招聘和长期维护资源更充分。

## 3. 不变项

- 产品角色仍只有 `user/admin`；
- 产品范围仍以 PRD V2、页面原型 V3 和 API V3 为准；
- 外部五阶段、`user_confirmation`、Checkpoint恢复和租户隔离语义不变；
- API路径、字段、错误码和数据库结构不因前端框架变更而改变；
- Listing、报告文件导出、验证任务、多人评审和产品事件仍不属于核心P0。

## 4. UI_V2使用边界

UI_V2 是视觉和交互实现起点，不是新的业务契约。以下原型扩展不能自动进入当前P0：

- 独立销量预测一级模块；
- 报告文件下载；
- 企业user自定义模型路由和Prompt；
- 持久化项目/会话/消息体系；
- user手工重试内部阶段；
- sofa以外品类的后端写入。

这些能力如需正式纳入，必须先完成PRD、数据字典、DDL、API、页面和测试的版本化变更。

## 5. 前端实现规则

1. 在现有 React 应用内增量接入，不另建重复前端工程；
2. 建立统一 API Client，统一处理 `/api/v1`、Envelope、Request ID、401刷新和业务错误码；
3. 认证身份和租户上下文只信任服务端Token与 `/users/me`；
4. 删除任意账号登录成功、`sessionStorage`伪鉴权和固定业务结果；
5. 保留图片、CSS和组件视觉，但页面行为严格绑定API V3；
6. 正式业务代码逐步从JSX迁移到TypeScript/TSX，迁移不阻断当前增量联调；
7. 前端测试采用React组件测试与浏览器E2E，覆盖S01—S06/A01。

## 6. 版本记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-08-11 | V1 | 前端技术栈由Svelte切换为React 19 + Vite，登记UI_V2复用与业务边界。 |
