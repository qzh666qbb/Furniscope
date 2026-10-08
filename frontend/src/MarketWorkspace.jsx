import {
  BellRinging,
  ChartBar,
  Database,
  House,
} from "@phosphor-icons/react";
import "./market-workspace.css";

const marketNavigation = [
  ["home", "insights", House, "洞察首页", "模块总览"],
  ["decisions", "market-decisions", ChartBar, "决策中心", "五项能力"],
  ["data", "market-data", Database, "数据资产", "导入与质量"],
  ["automation", "market-automation", BellRinging, "自动化", "告警投递"],
];

export function MarketSectionNav({ active }) {
  return (
    <aside className="market-section-nav">
      <header>
        <span>MARKET INTELLIGENCE</span>
        <strong>市场洞察</strong>
      </header>
      <nav aria-label="市场洞察导航">
        {marketNavigation.map(([id, route, Icon, label, hint]) => (
          <button
            type="button"
            key={id}
            className={active === id ? "active" : ""}
            onClick={() => { location.hash = route; }}
            aria-current={active === id ? "page" : undefined}
          >
            <Icon />
            <span><strong>{label}</strong><small>{hint}</small></span>
          </button>
        ))}
      </nav>
      <footer>
        <span>数据边界</span>
        <p>仅使用企业授权数据与已配置的官方政策源。</p>
      </footer>
    </aside>
  );
}

export function MarketPageFrame({ Sidebar, Topbar, active, className = "", children }) {
  return (
    <main className="workspace market-module-page">
      <Sidebar page="insights" />
      <section className="workspace-main">
        <Topbar />
        <div className="market-module-layout">
          <MarketSectionNav active={active} />
          <section className={`market-module-content ${className}`.trim()}>
            {children}
          </section>
        </div>
      </section>
    </main>
  );
}
