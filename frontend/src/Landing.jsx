import {
  ArrowRight,
  ChartBar,
  FileText,
  ShieldCheck,
  Sparkle,
  SquaresFour,
  TrendUp,
} from "@phosphor-icons/react";

const CAPABILITIES = [
  { Icon: SquaresFour, title: "产品建档", copy: "PDF、图纸与规格一键解析成可校验画像" },
  { Icon: ChartBar, title: "市场洞察", copy: "授权竞品与评论进库，清洗后才能分析" },
  { Icon: FileText, title: "证据报告", copy: "机会分、痛点与建议全部可下钻原文" },
  { Icon: TrendUp, title: "销量预测", copy: "按 SKU 与站点输出区间和备货建议" },
];

const ORBITS = [
  { label: "产品画像", delay: "0s" },
  { label: "竞品对照", delay: "2.2s" },
  { label: "评论舆情", delay: "4.4s" },
  { label: "机会评分", delay: "6.6s" },
  { label: "销量预测", delay: "8.8s" },
];

export function Landing() {
  return (
    <main className="fs-landing">
      <div className="fs-landing-aurora" aria-hidden="true" />
      <div className="fs-landing-grid" aria-hidden="true" />
      <div className="fs-landing-particles" aria-hidden="true">
        {Array.from({ length: 18 }, (_, index) => <i key={index} />)}
      </div>

      <header className="fs-landing-nav">
        <div className="fs-landing-brand">
          <img src="./assets/furniscope-mark.png" alt="" />
          <span>FurniScope</span>
        </div>
        <nav>
          <a href="#capabilities">能力</a>
          <button type="button" onClick={() => { location.hash = "register"; }}>申请账号</button>
          <button type="button" className="fs-landing-ghost" onClick={() => { location.hash = "admin-login"; }}>
            管理员
          </button>
          <button type="button" className="fs-landing-enter" onClick={() => { location.hash = "login"; }}>
            进入系统 <ArrowRight />
          </button>
        </nav>
      </header>

      <section className="fs-landing-hero">
        <div className="fs-landing-copy">
          <p className="fs-landing-kicker"><Sparkle /> 跨境家具超级 AI 员工</p>
          <h1>
            把工厂资料
            <br />
            变成可验证的
            <em>出海决策</em>
          </h1>
          <p className="fs-landing-lead">
            授权市场数据进、Token Plan 分析出。竞品、评论、机会分与销量预测全部落在证据上，
            大模型只解释事实，不编造数字。
          </p>
          <div className="fs-landing-actions">
            <button type="button" className="fs-landing-cta" onClick={() => { location.hash = "login"; }}>
              <span>进入系统</span>
              <ArrowRight />
            </button>
            <button type="button" className="fs-landing-secondary" onClick={() => { location.hash = "register"; }}>
              申请企业账号
            </button>
          </div>
          <ul className="fs-landing-stats">
            <li><strong>证据优先</strong><span>价格与评论来自授权数据</span></li>
            <li><strong>五步工作流</strong><span>产品 → 市场 → 评分 → 方案 → 报告</span></li>
            <li><strong>租户隔离</strong><span>企业数据互不可见</span></li>
          </ul>
        </div>

        <div className="fs-landing-stage" aria-hidden="true">
          <div className="fs-landing-ring" />
          <div className="fs-landing-ring fs-landing-ring-2" />
          <div className="fs-landing-core">
            <img src="./assets/furniscope-sofa-intelligence.png" alt="" />
            <div className="fs-landing-scan" />
          </div>
          {ORBITS.map((item) => (
            <span key={item.label} className="fs-landing-chip" style={{ animationDelay: item.delay }}>
              {item.label}
            </span>
          ))}
        </div>
      </section>

      <section className="fs-landing-capabilities" id="capabilities">
        {CAPABILITIES.map(({ Icon, title, copy }) => (
          <article key={title}>
            <i><Icon /></i>
            <h2>{title}</h2>
            <p>{copy}</p>
          </article>
        ))}
      </section>

      <footer className="fs-landing-foot">
        <span>FurniScope · 决策支持，不替代开模与下单</span>
        <button type="button" onClick={() => { location.hash = "login"; }}>
          <ShieldCheck /> 使用企业账号登录
        </button>
      </footer>
    </main>
  );
}
