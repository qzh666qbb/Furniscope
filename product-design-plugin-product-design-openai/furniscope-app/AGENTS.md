# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

This is the unified FurniScope product prototype. Keep the seven selected experiences connected in one app: login, AI workspace, new-analysis wizard, execution dashboard, market insights, decision report, and admin control center. The only supported roles are user and admin; do not add a role selector or RBAC matrix.

The primary product sidebar is collapsible. Preserve icon-only navigation, active states, tooltips, route behavior, and the user's saved collapsed/expanded preference.

The current app is the only maintained UI. Treat the archived old UI as a business-pattern reference: reuse product-data validation, data-quality checks, competitor/review evidence drill-down, engineering validation, and admin diagnostics without restoring its dense top-level navigation, persona-specific entrances, or user-facing internal agent graph.

SKU demand forecasting belongs inside Market Insights rather than primary navigation. Present it as demand forecasting and production planning: forecast scope, period, scenario, model quality, market-preference signals, recommended production volume, safety stock, and scheduling; carry the result into the decision report's manufacturing section.

The primary analysis experience is conversational. Keep the conversation, uploaded files, concise inspectable execution summaries, and follow-up composer available throughout the task. The right side is a collapsible dual-mode panel: execution context while work is running, and analytical results after output is available, with users able to switch between both modes at any time. Never expose private chain-of-thought or internal agent controls.

Keep both analysis modes. The workspace lets users choose conversational analysis or the structured wizard. In conversational mode, organize history as analysis project (product/market objective) -> project conversation -> conversation results; expose that hierarchy in a collapsible secondary sidebar without replacing the primary product navigation.

Market Insights and Decision Reports are asset collections, not single pages. Primary navigation opens searchable list views; users then open a selected insight/report detail. Decision reports expose download actions from both the list and detail views.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
