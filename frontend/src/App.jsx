import { useEffect, useState } from "react";
import {
  ArrowClockwise,
  ArrowLeft,
  ArrowRight,
  Bell,
  CaretDoubleLeft,
  CaretDoubleRight,
  CaretDown,
  CaretUp,
  ChartBar,
  ChartPie,
  Check,
  Clock,
  CloudArrowUp,
  Code,
  Database,
  FileImage,
  FilePdf,
  FileText,
  Factory,
  FileXls,
  Flask,
  FloppyDisk,
  Globe,
  Hourglass,
  Info,
  List,
  LockKey,
  Plus,
  Pulse,
  ShieldCheck,
  SquaresFour,
  Trash,
  TrendUp,
  WarningCircle,
  Wrench,
  X,
} from "@phosphor-icons/react";
import { AdminControlCenter } from "./Admin.jsx";
import { ForecastWorkspace as IntegratedForecastWorkspace } from "./ForecastWorkspace.jsx";
import { AnalysisCenter, WorkflowCanvas } from "./AnalysisCenter.jsx";
import { MarketDatasetCenter, MarketDatasetDetail } from "./MarketDatasets.jsx";
import { CompetitorTrackingBoard } from "./CompetitorTracking.jsx";
import { ProductCatalog } from "./ProductCatalog.jsx";
import { ReportLibrary } from "./ReportLibrary.jsx";
import { ReportDetailPage } from "./ReportDetailPage.jsx";
import { WorkDiaryPage } from "./WorkDiaryPage.jsx";
import {
  api,
  currentUser,
  hasSession,
  adminLogin as apiAdminLogin,
  login as apiLogin,
  logout as apiLogout,
  requestPasswordReset,
  confirmPasswordReset,
  submitRegistration,
} from "./api.js";
import "./admin.css";
import "./admin-crud.css";
import "./admin-crud-actions.css";
import "./admin-model-catalog.css";
import "./admin-model-route-select.css";
import "./admin-brand-alignment.css";
import "./admin-separation.css";
import "./admin-control-center.css";
import "./feature-detail-polish.css";
import "./wizard-completion.css";
import "./report-score-alignment.css";
import "./score-label-nowrap.css";
import "./forecast-workspace.css";
import "./forecast-layout-fix.css";
import "./topbar-badge-alignment.css";
import "./analysis-organization.css";
import "./live-wizard.css";
import { Landing } from "./Landing.jsx";
import "./landing.css";

const productImage = (sku) =>
  sku
    ? `/assets/hf-products/${encodeURIComponent(sku)}.webp`
    : "/assets/furniscope-mark.png";
const productImageFallback = (event) => {
  event.currentTarget.onerror = null;
  event.currentTarget.src = "/assets/furniscope-mark.png";
};
const stages = ["理解产品", "研究市场", "评估机会", "生成建议", "完成"];
const taskStageIndex = {
  understanding_product: 0,
  researching_market: 1,
  evaluating_opportunity: 2,
  generating_recommendation: 3,
  completed: 4,
};
const taskStatusLabel = {
  draft: "草稿",
  queued: "排队中",
  running: "执行中",
  waiting_human: "待确认",
  partial_succeeded: "部分完成",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
};

const primaryNavigation = [
  ["workspace", Pulse, "首页"],
  ["products", SquaresFour, "产品中心"],
  ["analysis", Plus, "AI 工作台"],
  ["forecast", TrendUp, "销量预测"],
  ["insights", ChartBar, "市场洞察"],
  ["report", FileText, "决策报告"],
];

function AppSidebar({ page = "workspace" }) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("furniscope-sidebar") === "collapsed",
  );
  const toggle = () =>
    setCollapsed((value) => {
      const next = !value;
      localStorage.setItem(
        "furniscope-sidebar",
        next ? "collapsed" : "expanded",
      );
      return next;
    });
  return (
    <aside
      className={`sidebar ${collapsed ? "collapsed" : ""}`}
    >
      <div className="sidebar-brand">
        <button
          className="sidebar-brand-toggle"
          onClick={toggle}
          aria-label={collapsed ? "展开导航栏" : "折叠导航栏"}
          aria-expanded={!collapsed}
          title={collapsed ? "展开导航栏" : "折叠导航栏"}
        >
          <img
            className="brand-mark"
            src="./assets/furniscope-mark.png"
            alt=""
          />
        </button>
        <span className="side-brand">FurniScope</span>
      </div>
      <nav aria-label="主导航">
        {primaryNavigation.map(([route, Icon, label]) => (
          <button
            key={route}
            className={page === route ? "active" : ""}
            onClick={() => (location.hash = route)}
            title={collapsed ? label : undefined}
          >
            <Icon />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <img
        className="side-sofa"
        src="./assets/furniscope-sofa-intelligence.png"
        alt=""
      />
      <div className="sidebar-connection" title="已连接真实业务数据">
        <ShieldCheck />
        <span>真实数据已连接</span>
      </div>
    </aside>
  );
}

function Login({ onEnter }) {
  const rememberedEmail = localStorage.getItem("furniscope-remembered-email") || "";
  const [email, setEmail] = useState(rememberedEmail);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(Boolean(rememberedEmail));
  const [showPassword, setShowPassword] = useState(false);
  const [message, setMessage] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    if (!email || !password) return setMessage("请输入企业邮箱和密码");
    try {
      setMessage("正在验证企业账号…");
      const data = await apiLogin(email, password);
      if (data.user.role_code === "admin") {
        await apiLogout();
        throw new Error("平台管理员请从独立管理入口登录");
      }
      if (remember) localStorage.setItem("furniscope-remembered-email", email.trim());
      else localStorage.removeItem("furniscope-remembered-email");
      setMessage("登录成功，正在进入首页…");
      setTimeout(() => onEnter(data.user), 300);
    } catch (error) {
      setMessage(error.message);
    }
  };
  return (
    <main className="login-page">
      <section className="brand-panel">
        <div className="brand-copy">
          <div className="brand-lockup">
            <img src="./assets/furniscope-mark.png" alt="" />
            <div className="wordmark">FurniScope</div>
          </div>
          <p className="descriptor">
            跨境家具超级 <strong>AI</strong> 员工
          </p>
          <div className="accent-line" />
          <p className="brand-statement">
            将产品材料与授权市场数据转化为可验证的证据，
            <br />
            驱动更智能的跨境市场决策。
          </p>
        </div>
        <img
          className="sofa-visual"
          src="./assets/furniscope-sofa-intelligence.png"
          alt="家具市场数据分析示意"
        />
      </section>
      <section className="form-panel">
        <form className="login-form" onSubmit={submit}>
          <header>
            <button type="button" className="login-back-home" onClick={() => { location.hash = "landing"; }}>
              <ArrowLeft /> 返回首页
            </button>
            <h1>登录 FurniScope</h1>
            <p>使用企业账号继续</p>
          </header>
          <label>
            企业邮箱
            <input
              id="enterprise-login-email"
              type="email"
              autoComplete="email"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <div className="login-field">
            <label htmlFor="enterprise-login-password">密码</label>
            <span className="password-field">
              <input
                id="enterprise-login-password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                placeholder="请输入密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button type="button" onClick={() => setShowPassword((value) => !value)}>
                {showPassword ? "隐藏" : "显示"}
              </button>
            </span>
          </div>
          <div className="login-assists">
            <label className="remember-me">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              <span><b>记住我</b><small>仅保存邮箱，不保存密码</small></span>
            </label>
            <button type="button" onClick={() => (location.hash = "forgot-password")}>忘记密码？</button>
          </div>
          <button className="login-button">登录</button>
          <p className="form-message">{message}</p>
          <div className="register-prompt">
            <span>还没有企业账号？</span>
            <button type="button" onClick={() => (location.hash = "register")}>申请注册</button>
          </div>
          <div className="login-divider"><span>或使用管理身份</span></div>
          <button
            type="button"
            className="admin-entry-link"
            onClick={() => (location.hash = "admin-login")}
          >
            <ShieldCheck />
            平台管理员入口
          </button>
        </form>
      </section>
    </main>
  );
}

function Register() {
  const [form, setForm] = useState({ company: "", name: "", email: "", password: "", confirm: "" });
  const [agreed, setAgreed] = useState(false);
  const [message, setMessage] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const update = (key) => (event) => setForm((value) => ({ ...value, [key]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault();
    if (Object.values(form).some((value) => !value.trim())) return setMessage("请完整填写注册信息");
    if (form.password.length < 12) return setMessage("密码至少需要 12 位");
    if (form.password !== form.confirm) return setMessage("两次输入的密码不一致");
    if (!agreed) return setMessage("请先阅读并同意服务条款与隐私政策");
    try {
      setMessage("正在提交注册申请…");
      await submitRegistration({
        enterprise_name: form.company.trim(),
        contact_name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
        agreed: true,
      });
      localStorage.setItem("furniscope-remembered-email", form.email.trim());
      setSubmitted(true);
      setMessage("申请已提交，平台管理员审批通过后即可使用该邮箱登录。");
    } catch (error) {
      setMessage(error.message);
    }
  };
  return (
    <main className="register-page">
      <section className="register-intro">
        <button onClick={() => (location.hash = "login")}><ArrowLeft /> 返回登录</button>
        <div>
          <img src="./assets/furniscope-mark.png" alt="" />
          <span>FurniScope</span>
        </div>
        <h1>让 AI 成为你的<br />跨境市场研究团队</h1>
        <p>创建企业工作空间，统一沉淀产品画像、市场洞察与可执行的出海决策。</p>
        <ul>
          <li><Check weight="bold" /> 产品与市场数据按企业隔离</li>
          <li><Check weight="bold" /> 五步 AI 工作流全程可追踪</li>
          <li><Check weight="bold" /> 分析成果自动沉淀为决策报告</li>
        </ul>
      </section>
      <section className="register-form-panel">
        <form className="register-form" onSubmit={submit}>
          <header><span>CREATE WORKSPACE</span><h2>申请企业账号</h2><p>填写信息以创建 FurniScope 企业工作空间</p></header>
          <div className="register-field-pair">
            <label>企业名称<input value={form.company} onChange={update("company")} placeholder="例如：和风家居" /></label>
            <label>联系人<input value={form.name} onChange={update("name")} placeholder="你的姓名" /></label>
          </div>
          <label>工作邮箱<input type="email" autoComplete="email" value={form.email} onChange={update("email")} placeholder="name@company.com" /></label>
          <div className="register-field-pair">
            <label>设置密码<input type="password" autoComplete="new-password" value={form.password} onChange={update("password")} placeholder="至少 12 位" minLength="12" /></label>
            <label>确认密码<input type="password" autoComplete="new-password" value={form.confirm} onChange={update("confirm")} placeholder="再次输入" minLength="12" /></label>
          </div>
          <label className="register-agreement"><input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} /><span>我已阅读并同意《服务条款》和《隐私政策》</span></label>
          <button className="register-submit" disabled={submitted}>{submitted ? "申请已提交" : "提交注册申请"}</button>
          <p className="register-message" role="status">{message}</p>
          <footer>已有账号？<button type="button" onClick={() => (location.hash = "login")}>直接登录</button></footer>
        </form>
      </section>
    </main>
  );
}

function ForgotPassword() {
  const rememberedEmail = localStorage.getItem("furniscope-remembered-email") || "";
  const [step, setStep] = useState("request");
  const [email, setEmail] = useState(rememberedEmail);
  const [resetCode, setResetCode] = useState("");
  const [issuedCode, setIssuedCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState("");
  const [done, setDone] = useState(false);
  const requestCode = async (event) => {
    event.preventDefault();
    if (!email.trim()) return setMessage("请输入企业邮箱");
    try {
      setMessage("正在核对企业账号…");
      const data = await requestPasswordReset(email.trim());
      setEmail(data.email);
      setIssuedCode(data.reset_code || "");
      setResetCode(data.reset_code || "");
      setStep("confirm");
      setMessage("");
    } catch (error) {
      setMessage(error.message);
    }
  };
  const submitPassword = async (event) => {
    event.preventDefault();
    if (!resetCode.trim()) return setMessage("请输入 6 位校验码");
    if (password.length < 12) return setMessage("新密码至少需要 12 位");
    if (password !== confirm) return setMessage("两次输入的密码不一致");
    try {
      setMessage("正在设置新密码…");
      await confirmPasswordReset({
        email: email.trim(),
        reset_code: resetCode.trim(),
        new_password: password,
      });
      localStorage.setItem("furniscope-remembered-email", email.trim());
      setDone(true);
      setMessage("密码已更新，请使用新密码登录。");
    } catch (error) {
      setMessage(error.message);
    }
  };
  return (
    <main className="register-page">
      <section className="register-intro">
        <button onClick={() => (location.hash = "login")}><ArrowLeft /> 返回登录</button>
        <div>
          <img src="./assets/furniscope-mark.png" alt="" />
          <span>FurniScope</span>
        </div>
        <h1>重置企业账号密码</h1>
        <p>核对企业邮箱后设置新密码。当前演示环境会在本页显示一次性校验码；接入企业邮箱后将改为发送到邮箱。</p>
        <ul>
          <li><Check weight="bold" /> 校验码 30 分钟内有效，使用后立即失效</li>
          <li><Check weight="bold" /> 新密码至少 12 位</li>
          <li><Check weight="bold" /> 重置成功后已登录会话会全部退出</li>
        </ul>
      </section>
      <section className="register-form-panel">
        {step === "request" ? (
          <form className="register-form" onSubmit={requestCode}>
            <header><span>PASSWORD RESET</span><h2>忘记密码</h2><p>输入已开通的企业邮箱，获取重置校验码</p></header>
            <label>企业邮箱<input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" /></label>
            <button className="register-submit">获取校验码</button>
            <p className="register-message" role="status">{message}</p>
            <footer>想起密码了？<button type="button" onClick={() => (location.hash = "login")}>返回登录</button></footer>
          </form>
        ) : (
          <form className="register-form" onSubmit={submitPassword}>
            <header><span>SET NEW PASSWORD</span><h2>设置新密码</h2><p>校验码已生成。请立即设置新密码，不要把校验码发给他人。</p></header>
            {issuedCode && (
              <div className="reset-code-card">
                <small>演示环境校验码</small>
                <strong>{issuedCode}</strong>
                <span>生产环境将发送到企业邮箱，不会显示在页面上。</span>
              </div>
            )}
            <label>企业邮箱<input type="email" value={email} readOnly /></label>
            <label>6 位校验码<input inputMode="numeric" autoComplete="one-time-code" value={resetCode} onChange={(e) => setResetCode(e.target.value)} placeholder="6 位数字" maxLength="6" /></label>
            <div className="register-field-pair">
              <label>新密码<input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="至少 12 位" minLength="12" /></label>
              <label>确认密码<input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="再次输入" minLength="12" /></label>
            </div>
            <button className="register-submit" disabled={done}>{done ? "密码已更新" : "确认重置"}</button>
            <p className="register-message" role="status">{message}</p>
            <footer>
              {done ? (
                <button type="button" onClick={() => (location.hash = "login")}>去登录</button>
              ) : (
                <button type="button" onClick={() => { setStep("request"); setMessage(""); }}>重新获取校验码</button>
              )}
            </footer>
          </form>
        )}
      </section>
    </main>
  );
}

function AdminLogin({ onEnter }) {
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    if (!account || !password) return setMessage("请输入管理员账号和密码");
    try {
      setMessage("正在校验管理员权限…");
      const data = await apiAdminLogin(account, password);
      if (data.user.role_code !== "admin") {
        await apiLogout();
        throw new Error("当前账号没有管理员权限");
      }
      setMessage("身份验证通过，正在进入平台控制中心…");
      setTimeout(() => onEnter(data.user), 350);
    } catch (error) {
      setMessage(error.message);
    }
  };
  return (
    <main className="admin-login-page">
      <section>
        <img src="./assets/furniscope-mark.png" alt="" />
        <h1>FurniScope</h1>
        <strong>平台控制中心</strong>
        <p>供平台管理员开通企业用户、配置功能授权，并维护各企业的预测模型。</p>
        <ul>
          <li>
            <ShieldCheck />
            独立管理员身份认证
          </li>
          <li>
            <LockKey />
            操作写入安全审计
          </li>
          <li>
            <Database />
            不进入企业业务数据
          </li>
        </ul>
      </section>
      <form onSubmit={submit}>
        <header>
          <span>ADMIN PORTAL</span>
          <h2>管理员登录</h2>
          <p>此入口与 FurniScope 企业工作台相互独立</p>
        </header>
        <label>
          管理员账号
          <input
            type="email"
            autoComplete="email"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            placeholder="admin@furniscope.com"
          />
        </label>
        <label>
          密码
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="请输入管理员密码"
          />
        </label>
        <button>安全登录</button>
        <small>{message}</small>
        <button
          type="button"
          className="back-user-login"
          onClick={() => (location.hash = "login")}
        >
          <ArrowLeft />
          返回企业用户登录
        </button>
      </form>
    </main>
  );
}

function Progress({ active }) {
  return (
    <div className="progress" aria-label={`当前阶段：${stages[active]}`}>
      {stages.map((s, i) => (
        <div
          className={`step ${i < active ? "done" : ""} ${i === active ? "active" : ""}`}
          key={s}
        >
          <span>{s}</span>
          <i>{i < active ? <Check size={10} weight="bold" /> : ""}</i>
        </div>
      ))}
    </div>
  );
}

function taskProductLabel(name, sku) {
  const rawName = String(name || "").trim();
  const rawSku = String(sku || "").trim();
  if (!rawName && !rawSku) return { title: "未命名产品", sku: "" };
  if (!rawSku) return { title: rawName, sku: "" };
  const escaped = rawSku.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const stripped = rawName
    .replace(new RegExp(`(?:^|[\\s·\\-_/])${escaped}(?=$|[\\s·\\-_/])`, "gi"), " ")
    .replace(new RegExp(`${escaped}$`, "i"), "")
    .replace(/\s+/g, " ")
    .trim();
  return { title: stripped || rawName, sku: rawSku };
}

function ProductRow({ product, onOpen }) {
  const failed = product.rawStatus === "failed";
  const { title, sku } = taskProductLabel(product.name, product.id);
  return (
    <article className={`product-row ${failed ? "is-failed" : ""}`}>
      <img
        src={product.image}
        alt={`${title} 产品图`}
        onError={productImageFallback}
      />
      <div className="product-meta">
        <div className="product-heading">
          <strong title={title}>{title}</strong>
          {sku ? <em className="product-sku">{sku}</em> : null}
        </div>
        <span>{product.detail}</span>
        <small>
          <b /> {product.status}
        </small>
        <time>{product.updated} 更新</time>
      </div>
      <div className="product-progress">
        <Progress active={product.stage} />
        <p>
          <b /> {product.activity}
        </p>
      </div>
      <button className="task-action" onClick={() => onOpen(product)}>
        {failed ? "查看异常" : product.reportUuid ? "查看报告" : "查看任务"}
      </button>
    </article>
  );
}

function Dashboard({ user, onLogout }) {
  const [notice, setNotice] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [selected, setSelected] = useState(null);
  const [menu, setMenu] = useState(false);
  const [confirmations, setConfirmations] = useState([]);
  const [selectedOption, setSelectedOption] = useState("");
  const [summary, setSummary] = useState(null);
  const [liveTasks, setLiveTasks] = useState([]);
  const [liveReports, setLiveReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState("");
  const pending = confirmations.length;
  const loadDashboard = (silent = false) => {
    if (!silent) setLoading(true);
    if (!silent) setDashboardError("");
    Promise.all([
      api("/api/v1/dashboard/summary"),
      api("/api/v1/user-confirmations?page_size=20"),
      api("/api/v1/analysis-workspaces?page_size=10"),
      api("/api/v1/reports?page_size=5"),
    ])
      .then(([nextSummary, nextConfirmations, nextTasks, nextReports]) => {
        setDashboardError("");
        setSummary(nextSummary);
        setConfirmations(nextConfirmations.items);
        setLiveTasks(
          nextTasks.items.filter((item) => item.report_uuid || ["queued", "running", "waiting_human", "failed", "partial_succeeded", "draft"].includes(item.status)).map((item) => ({
            id: item.product_sku,
            name: item.product_name,
            detail: `${item.target_country} · ${item.target_platform}`,
            image: productImage(item.product_sku),
            stage: taskStageIndex[item.stage] ?? 0,
            status: taskStatusLabel[item.status] || item.status,
            rawStatus: item.status,
            activity: item.job_name,
            updated: new Date(item.updated_at).toLocaleString("zh-CN"),
            taskUuid: item.task_uuid,
            workspaceUuid: item.workspace_uuid,
            reportUuid: item.report_uuid,
          })),
        );
        setLiveReports(nextReports.items);
      })
      .catch((error) => {
        if (!silent) setDashboardError(error.message);
      })
      .finally(() => {
        if (!silent) setLoading(false);
      });
  };
  useEffect(() => {
    loadDashboard();
    const poller = window.setInterval(() => loadDashboard(true), 15000);
    return () => window.clearInterval(poller);
  }, []);
  const toast = (text) => {
    setNotice(text);
    setTimeout(() => setNotice(""), 2200);
  };
  const openConfirmation = () => {
    const item = confirmations[0];
    if (!item) return toast("当前没有待确认事项");
    setSelectedOption(item.recommended_option);
    setConfirming(true);
  };
  const confirmItem = confirmations[0];
  const confirm = async () => {
    if (!confirmItem || !selectedOption) return;
    try {
      await api(
        `/api/v1/user-confirmations/${confirmItem.confirmation_id}:respond`,
        {
          method: "POST",
          body: JSON.stringify({
            selected_option: selectedOption,
            user_input: null,
          }),
        },
      );
      setConfirmations((items) =>
        items.filter(
          (item) => item.confirmation_id !== confirmItem.confirmation_id,
        ),
      );
      setSummary((value) =>
        value
          ? {
              ...value,
              pending_confirmations: Math.max(
                0,
                value.pending_confirmations - 1,
              ),
            }
          : value,
      );
      setConfirming(false);
      toast("确认已提交，任务已进入恢复队列");
    } catch (error) {
      toast(error.message);
    }
  };
  return (
    <main className="workspace">
      <AppSidebar page="workspace" />
      <section className="workspace-main">
        <header className="topbar">
          <div className="company tenant-label">
            {user?.tenant?.name || "FurniScope 企业"}
          </div>
          <div className="top-actions">
            <button onClick={openConfirmation}>
              <Bell />
              待确认 {pending > 0 && <b>{pending}</b>}
            </button>
            <span />
            <button className="user" onClick={() => setMenu(!menu)}>
              <i>{(user?.name || "用").slice(0, 1)}</i>
              {user?.name || "当前用户"}
              <CaretDown />
            </button>
            {menu && (
              <div className="user-menu">
                <button onClick={onLogout}>退出登录</button>
              </div>
            )}
          </div>
        </header>
        <div className="canvas">
          <div className="hero">
            <div>
              <span className="eyebrow">FURNISCOPE INTELLIGENCE</span>
              <h1>今天要研究哪个产品？</h1>
              <p>从产品资料和授权市场数据出发，获得可追溯的洞察与决策报告。</p>
            </div>
            <div className="analysis-mode-actions">
              <button onClick={() => (location.hash = "products")}>
                <SquaresFour />选择产品
              </button>
              <button className="primary" onClick={() => (location.hash = "analysis")}>
                <Pulse />进入 AI 工作台
              </button>
            </div>
          </div>
          {dashboardError && (
            <div className="dashboard-status error" role="alert">
              <WarningCircle />
              <span><strong>工作台数据加载失败</strong>{dashboardError}</span>
              <button onClick={() => loadDashboard()}>重新加载</button>
            </div>
          )}
          <section className="metrics">
            {[
              [SquaresFour, "产品总数", summary?.products ?? "—", "企业产品资产持续更新", () => (location.hash = "products")],
              [Pulse, "运行中任务", summary?.running_tasks ?? "—", "正在解析产品、挖掘竞品评论", () => toast(`运行中任务：${summary?.running_tasks ?? 0}`)],
              [
                WarningCircle,
                "待确认异常",
                summary?.pending_confirmations ?? pending,
                "参数冲突、数据待人工校验",
                openConfirmation,
              ],
              [FileText, "已生成报告", summary?.reports ?? "—", "已沉淀可落地出海策略", () => (location.hash = "report")],
            ].map(([Icon, label, value, subtitle, action], i) => (
              <button key={label} onClick={action} disabled={loading}>
                <Icon className={i === 2 ? "amber" : ""} />
                <span>
                  <small>{label}</small>
                  <strong>{value}</strong>
                  <em>{subtitle}</em>
                </span>
              </button>
            ))}
          </section>
          <section className="workspace-dashboard-grid">
            <div className="workspace-primary-column">
              <div className="workflow card">
                <header className="section-heading">
                  <div><h2>AI 工作流任务</h2></div>
                  <button type="button" className="section-more" onClick={() => (location.hash = "work-diary?from=workspace")}>更多 <ArrowRight /></button>
                </header>
              {liveTasks.length ? (
                liveTasks.slice(0, 5).map((product) => (
                  <ProductRow
                    key={product.taskUuid}
                    product={product}
                    onOpen={(task) => {
                      if (task.reportUuid && task.rawStatus !== "failed") {
                        location.hash = `report-detail?id=${task.reportUuid}&from=workspace`;
                        return;
                      }
                      const workspace = task.workspaceUuid || task.taskUuid;
                      const params = new URLSearchParams({
                        from: "workspace",
                        task: String(task.taskUuid),
                        workspace: String(workspace),
                      });
                      location.hash = `workflow?${params.toString()}`;
                    }}
                  />
                ))
              ) : (
                <div className="dashboard-empty-state">
                  <span><Pulse /></span>
                  <strong>暂无分析任务</strong>
                  <p>产品已进入产品中心，但只有实际启动分析后，任务进度才会显示在这里。</p>
                  <button onClick={() => (location.hash = "products")}>选择产品开始分析</button>
                </div>
              )}
              {confirmItem && (
                <button className="confirm-strip" onClick={openConfirmation}>
                  <WarningCircle weight="fill" />
                  <span>
                    <strong>需要你的确认</strong>
                    <small>{confirmItem.question}</small>
                  </span>
                  <span className="evidence">
                    <FileText />
                    证据 {confirmItem.evidence_refs.length} 项
                  </span>
                  <b>查看并确认</b>
                </button>
              )}
              </div>
            </div>
            <aside className="reports card">
              <header className="reports-heading"><div><h2>最近报告</h2><p>近期生成的产品机会报告</p></div><FileText /></header>
              {liveReports.length ? (
                liveReports.map((report) => (
                  <button
                    className="report"
                    key={report.report_uuid}
                    onClick={() =>
                      (location.hash = `report-detail?id=${report.report_uuid}&from=workspace`)
                    }
                  >
                    <img
                      src={productImage(report.product_sku)}
                      alt={`${report.product_name} 产品图`}
                      onError={productImageFallback}
                    />
                    <span>
                      <strong>{report.title}</strong>
                      <small>{report.decision_recommendation}</small>
                      <em>
                        机会评分 <b>{report.overall_opportunity_score}</b>
                        　置信度 {Math.round(report.overall_confidence * 100)}%
                      </em>
                      <time>
                        {new Date(report.created_at).toLocaleString("zh-CN")}
                      </time>
                    </span>
                  </button>
                ))
              ) : (
                <div className="dashboard-empty-state compact">
                  <span><FileText /></span>
                  <strong>还没有决策报告</strong>
                  <p>分析任务完成后，报告会自动出现在这里。</p>
                </div>
              )}
              <button
                className="all-reports"
                onClick={() => (location.hash = "report")}
              >
                查看全部报告 ›
              </button>
            </aside>
          </section>
          <footer className="data-source-note"><ShieldCheck /> 当前页面使用企业授权数据，并按租户隔离</footer>
        </div>
      </section>
      {notice && <div className="toast">{notice}</div>}
      {(confirming || selected) && (
        <div
          className="modal-backdrop"
          onClick={() => {
            setConfirming(false);
            setSelected(null);
          }}
        >
          <section className="modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="close"
              onClick={() => {
                setConfirming(false);
                setSelected(null);
              }}
            >
              <X />
            </button>
            {confirming && confirmItem ? (
              <>
                <WarningCircle className="modal-icon" />
                <h3>任务需要确认</h3>
                <p>{confirmItem.question}</p>
                <div className="confirmation-options">
                  {confirmItem.options.map((option) => (
                    <label key={option.code}>
                      <input
                        type="radio"
                        name="confirmation-option"
                        value={option.code}
                        checked={selectedOption === option.code}
                        onChange={() => setSelectedOption(option.code)}
                      />
                      <span>
                        <strong>{option.label || option.code}</strong>
                        {option.description && (
                          <small>{option.description}</small>
                        )}
                      </span>
                    </label>
                  ))}
                </div>
                <div className="modal-actions">
                  <button onClick={() => setConfirming(false)}>稍后处理</button>
                  <button onClick={confirm} disabled={!selectedOption}>
                    提交并继续任务
                  </button>
                </div>
              </>
            ) : selected ? (
              <>
                <img
                  className="modal-product"
                  src={selected.image}
                  alt={`${selected.name} 产品图`}
                  onError={productImageFallback}
                />
                <h3>
                  {selected.name} {selected.id}
                </h3>
                <p>
                  {selected.activity}。任务更新时间：{selected.updated}。
                </p>
                <div className="modal-actions">
                  <button onClick={() => setSelected(null)}>关闭</button>
                </div>
              </>
            ) : null}
          </section>
        </div>
      )}
    </main>
  );
}

const wizardSteps = ["分析目标", "产品与文件", "市场数据", "确认并启动"];
const fileSeed = [
  {
    name: "FS-320 产品规格书.pdf",
    size: "1.8 MB",
    time: "09:15",
    Icon: FilePdf,
  },
  {
    name: "FS-320 物料清单.xlsx",
    size: "280 KB",
    time: "09:14",
    Icon: FileXls,
  },
  { name: "FS-320 主图.jpg", size: "1.2 MB", time: "09:12", Icon: FileImage },
  {
    name: "FS-320 细节图 1.jpg",
    size: "1.4 MB",
    time: "09:12",
    Icon: FileImage,
  },
];

function WizardSidebar({ page = "workspace", showSessions }) {
  return <AppSidebar page={page} showSessions={showSessions} />;
}

function WizardTop() {
  const [user, setUser] = useState(null);
  const [pending, setPending] = useState(null);
  const [menu, setMenu] = useState(false);
  useEffect(() => {
    if (!hasSession()) return undefined;
    Promise.all([currentUser(), api("/api/v1/user-confirmations?page_size=1")])
      .then(([identity, confirmations]) => {
        setUser(identity);
        setPending(confirmations.total);
      })
      .catch(() => {});
  }, []);
  const leave = async () => {
    await apiLogout();
    location.hash = "login";
    location.reload();
  };
  return (
    <header className="topbar">
      <div className="company tenant-label">
        {user?.tenant?.name || "当前企业"}
      </div>
      <div className="top-actions">
        <button onClick={() => (location.hash = "workspace")} title="回到工作台处理待确认事项">
          <Bell />
          待确认 {pending > 0 && <b>{pending}</b>}
        </button>
        <span />
        <button className="user" onClick={() => setMenu((value) => !value)} aria-expanded={menu}>
          <i>{(user?.name || "用").slice(0, 1)}</i>
          {user?.name || "当前用户"}
          <CaretDown />
        </button>
        {menu && (
          <div className="user-menu">
            <button onClick={leave}>退出登录</button>
          </div>
        )}
      </div>
    </header>
  );
}

function Stepper({ step, onStep }) {
  return (
    <div className="wizard-stepper">
      {wizardSteps.map((name, i) => (
        <button
          key={name}
          className={`${i === step ? "current" : ""} ${i < step ? "complete" : ""}`}
          onClick={() => onStep(i)}
        >
          <i>{i < step ? <Check weight="bold" /> : i + 1}</i>
          <span>{name}</span>
        </button>
      ))}
    </div>
  );
}

function GoalStep({ goal, setGoal }) {
  return (
    <section className="wizard-form">
      <h2>分析目标</h2>
      <p>告诉 AI 你希望解决的业务问题，它会据此规划分析路径。</p>
      <label>
        分析类型
        <select>
          <option>新品上市机会评估</option>
          <option>现有产品增长诊断</option>
          <option>竞品对标研究</option>
        </select>
      </label>
      <label>
        核心问题
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows="5"
        />
      </label>
      <div className="suggestions">
        <span>常用目标</span>
        {[
          "判断北欧市场是否值得进入",
          "识别最具潜力的目标人群",
          "比较核心竞品与价格带",
        ].map((x) => (
          <button key={x} onClick={() => setGoal(x)}>
            {x}
          </button>
        ))}
      </div>
    </section>
  );
}

function ProductStep({ files, setFiles }) {
  const remove = (name) => setFiles(files.filter((f) => f.name !== name));
  const upload = (list) =>
    setFiles([
      ...files,
      ...Array.from(list).map((f) => ({
        name: f.name,
        size: `${Math.max(0.1, f.size / 1048576).toFixed(1)} MB`,
        time: "刚刚",
        Icon: FileText,
        status: f.size > 20 * 1048576 ? "解析失败" : "已解析",
      })),
    ]);
  const retry = (name) =>
    setFiles(
      files.map((f) => (f.name === name ? { ...f, status: "已解析" } : f)),
    );
  return (
    <section className="wizard-form product-files">
      <div className="field-pair">
        <label>
          产品名称
          <input defaultValue="可拆洗沙发套 FS-320" />
        </label>
        <label>
          产品类别
          <select defaultValue="sofa">
            <option value="sofa">客厅家具 / 沙发配件 / 沙发套</option>
            <option>卧室家具</option>
          </select>
        </label>
      </div>
      <h2>产品文件</h2>
      <p>上传产品资料、规格书、图片等，AI 将基于文件内容进行分析。</p>
      <label className="upload">
        <CloudArrowUp />
        <strong>拖拽文件到此处，或点击上传</strong>
        <span>支持 PDF、XLSX、JPG、PNG，单个文件不超过 20MB</span>
        <input type="file" multiple onChange={(e) => upload(e.target.files)} />
      </label>
      <div className="file-list">
        {files.map(({ name, size, time, Icon, status }) => {
          const failed = status === "解析失败";
          return (
            <div className={`file-row ${failed ? "failed" : ""}`} key={name}>
              <Icon />
              <strong>{name}</strong>
              <span>{size}</span>
              <time>2026-08-10 {time}</time>
              <em>
                {failed ? <WarningCircle /> : <Check />}
                {status || "已解析"}
              </em>
              <span className="file-actions">
                {failed && <button onClick={() => retry(name)}>重试</button>}
                <button
                  onClick={() => remove(name)}
                  aria-label={`删除 ${name}`}
                >
                  <Trash />
                </button>
              </span>
            </div>
          );
        })}
      </div>
      <small>已上传 {files.length} 个文件；失败文件不会进入分析</small>
      <h2 className="attributes-title">
        产品属性识别 <span>（AI 识别）</span>
      </h2>
      <p>点击低置信度属性可在启动前人工修正。</p>
      <div className="confidence-chips">
        {[
          ["材质", 94],
          ["尺寸", 88],
          ["风格", 76],
          ["可拆洗", 62],
        ].map(([x, n]) => (
          <button className={n < 70 ? "low" : ""} key={x}>
            <span>{x}</span>
            <b>{n}%</b>
          </button>
        ))}
      </div>
    </section>
  );
}

function ProductValidation() {
  const [resolved, setResolved] = useState(false);
  return (
    <section className="product-validation" aria-label="产品资料校验">
      <header>
        <div>
          <h2>资料一致性校验</h2>
          <p>AI 对上传文件中的字段来源、冲突与缺失项进行了交叉核验。</p>
        </div>
        <span className={resolved ? "validation-ok" : "validation-warn"}>
          {resolved ? "校验已完成" : "2 项待确认"}
        </span>
      </header>
      <div className="source-mapping">
        <strong>字段来源</strong>
        {[
          ["规格说明书.pdf", "14 个字段"],
          ["竞品对比数据.xlsx", "7 个字段"],
          ["AI 智能推断", "3 个字段"],
        ].map((x) => (
          <span key={x[0]}>
            <FileText />
            {x[0]}
            <b>{x[1]}</b>
          </span>
        ))}
      </div>
      {!resolved ? (
        <div className="conflict-list">
          <article>
            <WarningCircle />
            <span>
              <strong>产品尺寸存在冲突</strong>
              <small>
                规格说明书：230 × 90 × 85 cm　·　竞品数据：225 × 88 × 82 cm
              </small>
            </span>
            <button onClick={() => setResolved(true)}>采用规格书</button>
          </article>
          <article>
            <WarningCircle />
            <span>
              <strong>面料克重缺失</strong>
              <small>
                当前 3 份资料均未明确标注，建议在启动前补充检测报告。
              </small>
            </span>
            <button onClick={() => setResolved(true)}>标记待验证</button>
          </article>
        </div>
      ) : (
        <div className="validation-complete">
          <Check />
          <span>
            <strong>冲突项已处理</strong>
            <small>处理记录将随分析任务保存，并显示在最终证据链中。</small>
          </span>
        </div>
      )}
    </section>
  );
}

function DatasetStep({ forecast, setForecast }) {
  const [dataset, setDataset] = useState(
    "北欧家居市场洞察数据集（2026 年 7 月）",
  );
  const [fields, setFields] = useState([
    "销量",
    "价格",
    "库存",
    "促销",
    "产品属性",
  ]);
  const toggle = (x) =>
    setFields(
      fields.includes(x) ? fields.filter((f) => f !== x) : [...fields, x],
    );
  return (
    <section className="wizard-form dataset-step">
      <h2>市场数据</h2>
      <p>选择经过授权的数据集，并配置 XGBoost 销量预测参数。</p>
      <label>
        搜索与选择数据集
        <input placeholder="搜索数据集名称、区域或品类…" defaultValue="北欧" />
      </label>
      <div className="dataset-selected">
        <Database weight="fill" />
        <span>
          <strong>{dataset}</strong>
          <small>
            覆盖丹麦、瑞典、挪威、芬兰、冰岛的销售、库存、渠道与价格数据
          </small>
        </span>
        <b>已授权</b>
      </div>
      <label>
        选择数据集
        <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
          <option>北欧家居市场洞察数据集（2026 年 7 月）</option>
          <option>欧洲软体家具消费趋势（2026 H1）</option>
        </select>
      </label>
      <h2>预测字段</h2>
      <div className="dataset-field-picker">
        {[
          "销量",
          "价格",
          "库存",
          "促销",
          "产品属性",
          "评价情绪",
          "季节因素",
        ].map((x) => (
          <label key={x}>
            <input
              type="checkbox"
              checked={fields.includes(x)}
              onChange={() => toggle(x)}
            />
            <span>{x}</span>
          </label>
        ))}
      </div>
      <section className="xgb-wizard-config">
        <header>
          <div>
            <span>XGBoost · v4</span>
            <h3>销量预测配置</h3>
          </div>
          <b>已通过回测 · MAPE 8.7%</b>
        </header>
        <div>
          <label>
            预测粒度
            <select
              value={forecast.granularity}
              onChange={(e) =>
                setForecast({ ...forecast, granularity: e.target.value })
              }
            >
              <option>周</option>
              <option>月</option>
            </select>
          </label>
          <label>
            预测周期
            <select
              value={forecast.horizon}
              onChange={(e) =>
                setForecast({ ...forecast, horizon: e.target.value })
              }
            >
              <option value="8">未来 8 周</option>
              <option value="12">未来 12 个月</option>
              <option value="18">未来 18 个月</option>
            </select>
          </label>
          <label>
            预测场景
            <select
              value={forecast.scenario}
              onChange={(e) =>
                setForecast({ ...forecast, scenario: e.target.value })
              }
            >
              <option>基准</option>
              <option>保守</option>
              <option>乐观</option>
            </select>
          </label>
          <label>
            置信区间
            <select
              value={forecast.interval}
              onChange={(e) =>
                setForecast({ ...forecast, interval: e.target.value })
              }
            >
              <option>80%</option>
              <option>90%</option>
              <option>95%</option>
            </select>
          </label>
        </div>
      </section>
      <h2>数据集质量评估</h2>
      <div className="quality-grid">
        {[
          ["覆盖度", "92%", "覆盖目标市场"],
          ["数据时效性", "2026-07", "最新月份"],
          ["完整性", "88%", "关键字段完整"],
          ["品类匹配度", "高", "与分析品类匹配"],
        ].map((x) => (
          <div key={x[0]}>
            <span>{x[0]}</span>
            <strong>{x[1]}</strong>
            <small>
              <i /> {x[2]}
            </small>
          </div>
        ))}
      </div>
      <div className="dataset-warning">
        <WarningCircle />
        <span>
          <strong>1 项数据缺口已标记</strong>
          <small>
            冰岛线上渠道销量缺失，将降低该区域预测权重，不影响整体分析。
          </small>
        </span>
      </div>
    </section>
  );
}

function ReviewStep({ goal, files, forecast }) {
  return (
    <section className="wizard-form review">
      <h2>确认分析设置</h2>
      <p>启动前确认目标、数据、模型、提示词和预计成本。</p>
      <div>
        <h3>分析目标</h3>
        <span>新品上市机会评估</span>
        <strong>{goal}</strong>
      </div>
      <div>
        <h3>产品与文件</h3>
        <img src="./assets/sofa-fs320.png" alt="北欧布艺沙发" />
        <span>可拆洗沙发套 FS-320</span>
        <strong>{files.length} 个文件已上传，关键属性识别完成</strong>
      </div>
      <div>
        <h3>市场数据</h3>
        <span>北欧家居市场洞察数据集（2026 年 7 月）</span>
        <strong>已授权 · 质量良好 · 覆盖 5 个目标市场</strong>
      </div>
      <div>
        <h3>销量预测</h3>
        <span>XGBoost v4 · {forecast.scenario}场景</span>
        <strong>
          {forecast.granularity}粒度 · {forecast.horizon} 个周期 ·{" "}
          {forecast.interval} 置信区间
        </strong>
      </div>
      <div>
        <h3>模型与提示词</h3>
        <span>市场机会评估提示词 · 按已授权数据启动</span>
        <strong>预计调用 18–24 次 · Token 约 38K</strong>
      </div>
      <section className="launch-cost-summary">
        <span>
          预计完成时间<strong>6–9 分钟</strong>
        </span>
        <span>
          预计成本<strong>¥1.20–1.80</strong>
        </span>
        <span>
          成本上限<strong>¥2.00</strong>
        </span>
      </section>
      <label className="launch-confirm">
        <input type="checkbox" defaultChecked />
        我确认使用已授权数据，并同意按上述模型和预算启动分析
      </label>
    </section>
  );
}

function AISummary({ step }) {
  return (
    <aside className="ai-summary">
      <h2>AI 分析摘要</h2>
      {step === 0 ? (
        <>
          <div className="summary-block ok">
            <Check />
            <strong>目标清晰</strong>
            <p>AI 将围绕市场吸引力、竞争格局、价格带和进入风险展开研究。</p>
          </div>
          <div className="summary-block">
            <Info />
            <strong>预计输出</strong>
            <p>机会评分、市场洞察、关键证据与行动建议。</p>
          </div>
        </>
      ) : step === 1 ? (
        <>
          <div className="summary-block ok">
            <Check />
            <strong>识别到的关键属性</strong>
            <dl>
              <dt>产品名称</dt>
              <dd>可拆洗沙发套 FS-320</dd>
              <dt>主要材质</dt>
              <dd>聚酯纤维（涤纶）</dd>
              <dt>适用尺寸</dt>
              <dd>三人位沙发</dd>
              <dt>产品风格</dt>
              <dd>现代简约</dd>
            </dl>
          </div>
          <div className="summary-block warn">
            <WarningCircle />
            <strong>证据不足或不确定</strong>
            <p>面料克重未在文件中明确标注，建议补充面料检测报告。</p>
          </div>
          <div className="summary-block">
            <Info />
            <strong>缺失信息</strong>
            <p>产品使用说明书、包装外箱标签或箱唛信息。</p>
          </div>
        </>
      ) : step === 2 ? (
        <>
          <div className="summary-block ok">
            <Check />
            <strong>数据就绪，可支持本次分析</strong>
            <p>市场覆盖、时效性与品类匹配度均满足要求。</p>
          </div>
          <div className="summary-block warn">
            <WarningCircle />
            <strong>1 项潜在数据缺口</strong>
            <p>冰岛线上渠道数据缺失，不影响整体机会判断。</p>
          </div>
        </>
      ) : (
        <>
          <div className="summary-block ok">
            <Check />
            <strong>已就绪</strong>
            <p>所有必填信息完整，满足分析要求。</p>
          </div>
          <div className="summary-score">
            <span>当前置信度</span>
            <strong>82%</strong>
          </div>
          <div className="summary-block warn">
            <WarningCircle />
            <strong>1 项需注意</strong>
            <p>产品文件为样品规格书，请关注最终合规细节。</p>
          </div>
          <div className="summary-block">
            <Info />
            <strong>预计完成时间</strong>
            <p>今天 17:30 前 · 约 7 小时 45 分钟</p>
          </div>
        </>
      )}
    </aside>
  );
}

function LegacyAnalysisWizard() {
  const saved = (() => {
    try {
      return JSON.parse(localStorage.getItem("furniscope-wizard-draft")) || {};
    } catch {
      return {};
    }
  })();
  const [step, setStep] = useState(saved.step || 0);
  const [goal, setGoal] = useState(
    saved.goal || "评估 FS-320 可拆洗沙发套在北欧市场的上市机会与成功概率",
  );
  const [files, setFiles] = useState(fileSeed);
  const [forecast, setForecast] = useState(
    saved.forecast || {
      granularity: "周",
      horizon: "8",
      scenario: "基准",
      interval: "90%",
    },
  );
  const [launched, setLaunched] = useState(false);
  const [error, setError] = useState("");
  const [savedAt, setSavedAt] = useState("刚刚");
  const validFiles = files.filter((f) => f.status !== "解析失败");
  const canContinue =
    step === 0
      ? goal.trim().length >= 10
      : step === 1
        ? validFiles.length > 0
        : true;
  useEffect(() => {
    const timer = setTimeout(() => {
      localStorage.setItem(
        "furniscope-wizard-draft",
        JSON.stringify({ step, goal, forecast }),
      );
      setSavedAt(
        new Date().toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
        }),
      );
    }, 350);
    return () => clearTimeout(timer);
  }, [step, goal, forecast]);
  useEffect(() => {
    const leave = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    addEventListener("beforeunload", leave);
    return () => removeEventListener("beforeunload", leave);
  }, []);
  const next = () => {
    if (!canContinue) {
      setError(
        step === 0
          ? "请至少输入 10 个字的分析目标"
          : "请至少上传并成功解析 1 个产品文件",
      );
      return;
    }
    setError("");
    if (step < 3) setStep(step + 1);
    else {
      localStorage.removeItem("furniscope-wizard-draft");
      setLaunched(true);
    }
  };
  return (
    <main className="workspace wizard-page">
      <WizardSidebar page="analysis" showSessions={false} />
      <section className="workspace-main">
        <WizardTop />
        <div className="wizard-canvas">
          <header className="wizard-title-row">
            <div>
              <h1>AI 分析 · 向导模式</h1>
              <p>通过结构化步骤配置分析目标、产品资料与市场数据</p>
            </div>
            <span>
              <FloppyDisk />
              草稿已保存 {savedAt}
            </span>
          </header>
          <Stepper
            step={step}
            onStep={(i) => {
              if (i <= step || (i === step + 1 && canContinue)) {
                setError("");
                setStep(i);
              } else setError("请先完成当前步骤的必填内容");
            }}
          />
          <div className="wizard-grid">
            {step === 0 ? (
              <GoalStep goal={goal} setGoal={setGoal} />
            ) : step === 1 ? (
              <div>
                <ProductStep files={files} setFiles={setFiles} />
                <ProductValidation />
              </div>
            ) : step === 2 ? (
              <DatasetStep forecast={forecast} setForecast={setForecast} />
            ) : (
              <ReviewStep goal={goal} files={validFiles} forecast={forecast} />
            )}
            <AISummary step={step} />
          </div>
        </div>
        <footer className="wizard-footer">
          <button
            onClick={() => {
              if (confirm("当前进度已保存为草稿，确定返回对话模式吗？"))
                location.hash = "analysis";
            }}
          >
            返回对话模式
          </button>
          {error && (
            <em className="wizard-error">
              <WarningCircle />
              {error}
            </em>
          )}
          <span />
          <button
            disabled={step === 0}
            onClick={() => {
              setError("");
              setStep(Math.max(0, step - 1));
            }}
          >
            上一步
          </button>
          <button className="continue" onClick={next}>
            {step < 3 ? `继续：${wizardSteps[step + 1]}` : "启动 AI 分析"}
          </button>
        </footer>
      </section>
      {launched && (
        <div className="modal-backdrop">
          <section className="modal launch-success">
            <Check />
            <h3>AI 分析已启动</h3>
            <p>
              任务 FS-320 已进入“理解产品”阶段；XGBoost
              销量预测将在市场研究完成后运行。
            </p>
            <div className="modal-actions">
              <button onClick={() => setLaunched(false)}>留在此页</button>
              <button onClick={() => (location.hash = "analysis")}>
                返回新版分析
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

const conversationStages = [
  "明确研究目标与范围",
  "解析资料与数据质检",
  "市场与竞品匹配",
  "需求建模与销量预测",
  "关键驱动因素分析",
  "产品优化建议输出",
];
function ContextPanel({ mode, setMode }) {
  return (
    <div className="context-panel">
      <div className="context-tabs">
        <button
          className={mode === "context" ? "active" : ""}
          onClick={() => setMode("context")}
        >
          执行上下文
        </button>
        <button
          className={mode === "result" ? "active" : ""}
          onClick={() => setMode("result")}
        >
          分析结果
        </button>
      </div>
      {mode === "context" ? (
        <>
          <section className="plan-card">
            <h2>分析计划</h2>
            {conversationStages.map((s, i) => (
              <div
                className={`${i < 2 ? "done" : ""} ${i === 2 ? "active" : ""}`}
                key={s}
              >
                <i>{i < 2 ? <Check /> : i + 1}</i>
                <span>
                  <strong>{s}</strong>
                  {i === 2 && <small>正在匹配 SKU 与市场销售数据</small>}
                </span>
              </div>
            ))}
          </section>
          <section>
            <h2>
              已上传资料 <b>3</b>
            </h2>
            {[
              [FilePdf, "FS-320 产品规格书.pdf", "1.2 MB"],
              [FileImage, "FS-320 产品图片.zip", "8 个文件"],
              [FileXls, "历史市场销售数据.xlsx", "2.4 MB"],
            ].map(([Icon, n, m]) => (
              <div className="context-file" key={n}>
                <Icon />
                <span>
                  <strong>{n}</strong>
                  <small>{m}</small>
                </span>
                <Check />
              </div>
            ))}
          </section>
          <section className="context-evidence">
            <h2>证据与参考</h2>
            <strong>42</strong>
            <span>条已引用证据</span>
            <button onClick={() => setMode("result")}>查看详情</button>
          </section>
          <section className="context-progress">
            <h2>整体进度</h2>
            <div>
              <b>48%</b>
              <span>
                <strong>分析进行中</strong>
                <small>预计剩余 2–3 分钟</small>
              </span>
            </div>
          </section>
          <small className="conversation-id">会话 ID：conv-20260811-0942</small>
        </>
      ) : (
        <ResultCanvas />
      )}
    </div>
  );
}
function ResultCanvas() {
  return (
    <div className="result-canvas">
      <header>
        <img src="./assets/sofa-fs320.png" alt="北欧布艺沙发" />
        <div>
          <h2>布艺沙发 FS-320</h2>
          <p>北欧五国 · 2026-08 至 2027-07 · 已完成</p>
        </div>
      </header>
      <section className="result-forecast">
        <h3>销量预测（未来 12 个月）</h3>
        <div className="result-bars">
          {[28, 37, 44, 39, 52, 56, 49, 64, 68, 61, 72, 66].map((x, i) => (
            <span
              className={i > 5 ? "future" : ""}
              style={{ height: `${x}%` }}
              key={i}
            />
          ))}
        </div>
        <div className="result-kpis">
          <span>
            预测总销量<b>23,680 件</b>
          </span>
          <span>
            同比增长<b>+18.7%</b>
          </span>
          <span>
            模型置信度<b>82%</b>
          </span>
        </div>
      </section>
      <section>
        <h3>市场属性偏好</h3>
        <div className="preference-grid">
          {[
            ["材质", "布艺", "68%"],
            ["风格", "北欧", "62%"],
            ["尺寸", "三人位", "56%"],
            ["价格带", "€1,500–2,500", "48%"],
          ].map((x) => (
            <span key={x[0]}>
              <small>{x[0]}</small>
              <strong>{x[1]}</strong>
              <b>{x[2]}</b>
            </span>
          ))}
        </div>
      </section>
      <section className="result-production">
        <h3>生产建议</h3>
        <div>
          <span>
            建议生产量<strong>24,500 件</strong>
          </span>
          <span>
            安全库存<strong>2,900 件</strong>
          </span>
          <span>
            排产节奏<strong>分 3 批次</strong>
          </span>
        </div>
        <p>
          优先生产可拆洗、中等座深和中性色款式；首批 8,500 件，间隔 45
          天滚动复核。
        </p>
      </section>
      <button
        className="open-insights"
        onClick={() => (location.hash = "insights")}
      >
        打开完整市场洞察
      </button>
    </div>
  );
}
function AnalysisWizard() {
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(
    () => Number(localStorage.getItem("furniscope-context-width")) || 560,
  );
  const [resizing, setResizing] = useState(false);
  const [mode, setMode] = useState("context");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [files, setFiles] = useState([
    "FS-320 产品规格书.pdf",
    "FS-320 产品图片.zip",
    "历史市场销售数据.xlsx",
  ]);
  useEffect(() => {
    if (!resizing) return;
    const move = (e) =>
      setPanelWidth(
        Math.min(720, Math.max(380, window.innerWidth - e.clientX)),
      );
    const stop = () => {
      setResizing(false);
      localStorage.setItem("furniscope-context-width", String(panelWidth));
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", stop, { once: true });
    return () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", stop);
    };
  }, [resizing, panelWidth]);
  const resizeBy = (delta) =>
    setPanelWidth((w) => {
      const next = Math.min(720, Math.max(380, w + delta));
      localStorage.setItem("furniscope-context-width", String(next));
      return next;
    });
  const resetWidth = () => {
    setPanelWidth(560);
    localStorage.setItem("furniscope-context-width", "560");
  };
  const send = () => {
    if (!input.trim()) return;
    setMessages((x) => [
      ...x,
      {
        q: input,
        a: "已收到。我会在当前分析范围内补充研究，并同步更新右侧的证据与结果。",
      },
    ]);
    setInput("");
  };
  return (
    <main
      className={`workspace conversation-page ${resizing ? "is-resizing" : ""}`}
    >
      <AppSidebar page="analysis" />
      <section className="workspace-main">
        <WizardTop />
        <div
          className={`conversation-shell ${panelOpen ? "panel-open" : "panel-closed"}`}
          style={
            panelOpen ? { "--context-width": `${panelWidth}px` } : undefined
          }
        >
          <section className="conversation-main">
            <header className="conversation-heading">
              <div>
                <h1>
                  FurniScope AI 研究助手 <em>Beta</em>
                </h1>
                <p>像聊天一样研究，快速获得可验证的市场洞察与建议。</p>
              </div>
              <span>
                <i />
                分析进行中
              </span>
            </header>
            <div className="conversation-feed">
              <article className="user-turn">
                <i>李</i>
                <div>
                  <header>
                    <strong>李思远</strong>
                    <time>09:42</time>
                  </header>
                  <p>
                    请分析这款可拆洗沙发 FS-320 在北欧市场的销售潜力，给出 12
                    个月销量预测、关键驱动因素与产品优化建议。
                  </p>
                  <div className="chat-files">
                    {files.map((f, i) => (
                      <span key={f}>
                        {i === 0 ? (
                          <FilePdf />
                        ) : i === 1 ? (
                          <FileImage />
                        ) : (
                          <FileXls />
                        )}
                        <strong>{f}</strong>
                        <small>{["1.2 MB", "8 个文件", "2.4 MB"][i]}</small>
                      </span>
                    ))}
                  </div>
                </div>
              </article>
              <article className="ai-turn">
                <i>
                  <Pulse />
                </i>
                <div>
                  <header>
                    <strong>FurniScope Copilot</strong>
                    <time>09:43</time>
                  </header>
                  <div className="work-summary">
                    {[
                      [
                        "已理解目标",
                        "围绕销量预测、需求偏好和生产建议展开研究",
                      ],
                      [
                        "文件解析完成",
                        "3 个文件 · 36,512 行销售记录 · 24 个字段",
                      ],
                      [
                        "数据质量检查",
                        "关键字段完整率 98.7%，已标准化缺失值与单位",
                      ],
                      ["市场范围确认", "北欧五国 · 零售出货量口径"],
                    ].map((x) => (
                      <span key={x[0]}>
                        <Check />
                        <strong>{x[0]}</strong>
                        <small>{x[1]}</small>
                      </span>
                    ))}
                  </div>
                </div>
              </article>
              <article className="ai-turn live">
                <i>
                  <Pulse />
                </i>
                <div>
                  <header>
                    <strong>FurniScope Copilot</strong>
                    <time>09:45</time>
                  </header>
                  <div className="live-step">
                    <Pulse />
                    <span>
                      <strong>正在匹配 SKU 与市场销售数据</strong>
                      <small>
                        关联产品规格特征与历史销量，识别可比竞品与需求区间。
                      </small>
                    </span>
                    <b>48%</b>
                  </div>
                </div>
              </article>
              <article className="ai-turn">
                <i>
                  <Pulse />
                </i>
                <div>
                  <header>
                    <strong>FurniScope Copilot</strong>
                    <time>09:48</time>
                  </header>
                  <p>
                    已生成第一版预测。预计未来 12 个月销量为 <b>23,680 件</b>
                    ，同比增长 <b>18.7%</b>。
                  </p>
                  <button
                    className="chat-result"
                    onClick={() => {
                      setMode("result");
                      setPanelOpen(true);
                    }}
                  >
                    <img src="./assets/sofa-fs320.png" alt="" />
                    <span>
                      <strong>FS-320 北欧市场销量预测</strong>
                      <small>
                        可拆洗设计 · 中等座深 · 中性色是主要增长驱动
                      </small>
                    </span>
                    <b>查看分析画布</b>
                  </button>
                </div>
              </article>
              {messages.map((m, i) => (
                <div className="followup" key={i}>
                  <article className="user-turn">
                    <i>李</i>
                    <div>
                      <p>{m.q}</p>
                    </div>
                  </article>
                  <article className="ai-turn">
                    <i>
                      <Pulse />
                    </i>
                    <div>
                      <p>{m.a}</p>
                    </div>
                  </article>
                </div>
              ))}
            </div>
            <footer className="conversation-composer">
              <textarea
                placeholder="继续提问、上传更多资料，或调整市场范围…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <div>
                <label>
                  <Plus />
                  添加资料
                  <input
                    type="file"
                    onChange={(e) =>
                      e.target.files[0] &&
                      setFiles((x) => [...x, e.target.files[0].name])
                    }
                  />
                </label>
                <button>
                  市场范围：北欧五国 <CaretDown />
                </button>
                <button>
                  分析周期：12 个月 <CaretDown />
                </button>
                <span />
                <button
                  className="send-message"
                  onClick={send}
                  aria-label="发送"
                >
                  <ArrowLeft />
                </button>
              </div>
            </footer>
          </section>
          {panelOpen ? (
            <aside className="conversation-context">
              <div
                className="context-resizer"
                role="separator"
                aria-label="调整右侧栏宽度"
                aria-orientation="vertical"
                tabIndex="0"
                title="拖动调整宽度，双击恢复默认"
                onPointerDown={(e) => {
                  e.currentTarget.setPointerCapture?.(e.pointerId);
                  setResizing(true);
                }}
                onDoubleClick={resetWidth}
                onKeyDown={(e) => {
                  if (e.key === "ArrowLeft") resizeBy(20);
                  if (e.key === "ArrowRight") resizeBy(-20);
                }}
              >
                <i />
                <span>{panelWidth}px</span>
              </div>
              <button
                className="collapse-context"
                onClick={() => setPanelOpen(false)}
                title="折叠右侧栏"
              >
                <CaretDoubleRight />
              </button>
              <ContextPanel mode={mode} setMode={setMode} />
            </aside>
          ) : (
            <aside className="context-rail">
              <button onClick={() => setPanelOpen(true)} title="展开右侧栏">
                <CaretDoubleLeft />
              </button>
              <span>FS-320</span>
              <img src="./assets/sofa-fs320.png" alt="" />
              <b>48%</b>
            </aside>
          )}
        </div>
      </section>
    </main>
  );
}

function AIAnalysisHome() {
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const start = () => {
    if (prompt.trim() || files.length) location.hash = "analysis";
  };
  useEffect(() => {
    const reset = () => {
      setPrompt("");
      setFiles([]);
    };
    const open = (event) => {
      setPrompt(event.detail?.title || "");
      location.hash = "analysis";
    };
    addEventListener("new-analysis", reset);
    addEventListener("open-analysis", open);
    return () => {
      removeEventListener("new-analysis", reset);
      removeEventListener("open-analysis", open);
    };
  }, []);
  return (
    <main className="workspace analysis-home-page">
      <AppSidebar page="analysis" />
      <section className="workspace-main">
        <WizardTop />
        <div className="analysis-home">
          <section className="analysis-welcome">
            <img src="./assets/furniscope-mark.png" alt="" />
            <h1>今天想分析什么？</h1>
            <p>
              描述你的家具产品和业务问题，FurniScope
              会与你协作完成市场研究、销量预测与决策建议。
            </p>
            <div className="analysis-starter">
              <textarea
                autoFocus
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    start();
                  }
                }}
                placeholder="例如：分析 FS-320 布艺沙发在北欧市场未来 12 个月的销量和产品机会…"
              />
              {files.length > 0 && (
                <div className="starter-files">
                  {files.map((x) => (
                    <span key={x}>
                      <FileText />
                      {x}
                      <button
                        onClick={() => setFiles(files.filter((f) => f !== x))}
                      >
                        <X />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <footer>
                <label>
                  <Plus />
                  上传产品资料
                  <input
                    type="file"
                    multiple
                    onChange={(e) =>
                      setFiles([
                        ...files,
                        ...Array.from(e.target.files).map((f) => f.name),
                      ])
                    }
                  />
                </label>
                <button>
                  市场范围：智能识别 <CaretDown />
                </button>
                <span />
                <button
                  className="starter-send"
                  onClick={start}
                  disabled={!prompt.trim() && !files.length}
                  aria-label="开始分析"
                >
                  <ArrowLeft />
                </button>
              </footer>
            </div>
            <div className="starter-suggestions">
              {[
                "预测新品未来销量",
                "寻找目标市场机会",
                "分析竞品与用户需求",
              ].map((x, i) => (
                <button
                  key={x}
                  onClick={() =>
                    setPrompt(
                      [
                        "预测 FS-320 在北欧市场未来 12 个月的销量，并结合库存与生产周期给出备货和排产建议",
                        "判断可扩展餐桌进入德国和北欧市场的机会与风险",
                        "分析目标市场竞品卖点、价格带和用户评价需求",
                      ][i],
                    )
                  }
                >
                  {i === 0 ? <TrendUp /> : i === 1 ? <Globe /> : <ChartBar />}
                  <span>
                    <strong>{x}</strong>
                    <small>
                      {
                        [
                          "结合历史销售数据和产品属性",
                          "评估需求、供给缺口和进入条件",
                          "提炼可验证的产品优化方向",
                        ][i]
                      }
                    </small>
                  </span>
                </button>
              ))}
            </div>
            <button
              className="switch-wizard"
              onClick={() => (location.hash = "analysis")}
            >
              <List />
              进入工作流画布
            </button>
          </section>
        </div>
      </section>
    </main>
  );
}

const runStages = ["理解产品", "研究市场", "评估机会", "生成建议", "完成"];
const runStageNotes = [
  "已完成产品资料解析、属性识别和一致性校验。",
  "正在收集目标市场、竞品、评价与价格证据。",
  "将在市场研究完成后计算机会得分与风险。",
  "将在机会评估后生成产品、生产和渠道建议。",
  "全部阶段完成后生成可下载报告与证据链。",
];
function RunProgress() {
  const [selected, setSelected] = useState(1);
  return (
    <>
      <section className="run-progress">
        {runStages.map((x, i) => (
          <button
            className={`${i === 0 ? "done" : ""} ${i === 1 ? "active" : ""} ${i === selected ? "selected" : ""}`}
            key={x}
            onClick={() => setSelected(i)}
          >
            <i>{i === 0 ? <Check /> : i + 1}</i>
            <span>
              <strong>{x}</strong>
              <small>
                {i === 0 ? "已完成" : i === 1 ? "进行中" : "待开始"}
              </small>
            </span>
          </button>
        ))}
      </section>
      <section
        className={`run-stage-inspector ${selected > 1 ? "pending" : ""}`}
      >
        <div>
          <b>{selected + 1}</b>
          <span>
            <strong>{runStages[selected]}</strong>
            <small>
              {selected === 0
                ? "已完成"
                : selected === 1
                  ? "当前执行阶段"
                  : "等待前序阶段完成"}
            </small>
          </span>
        </div>
        <p>{runStageNotes[selected]}</p>
        {selected === 0 && (
          <button onClick={() => (location.hash = "analysis")}>
            查看解析摘要
          </button>
        )}
        {selected === 1 && (
          <button
            onClick={() =>
              document
                .querySelector(".activity-panel")
                ?.scrollIntoView({ behavior: "smooth" })
            }
          >
            查看实时活动
          </button>
        )}
      </section>
    </>
  );
}
function ExecutionDashboard() {
  const [choice, setChoice] = useState("include");
  const [confirmed, setConfirmed] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [details, setDetails] = useState(false);
  const [allEvidence, setAllEvidence] = useState(false);
  const retry = () => {
    setRetrying(true);
    setTimeout(() => setRetrying(false), 1200);
  };
  return (
    <main className="workspace run-page">
      <WizardSidebar />
      <section className="workspace-main">
        <WizardTop />
        <div className="run-canvas">
          <button
            className="back-workspace"
            onClick={() => (location.hash = "workspace")}
          >
            <ArrowLeft />
            返回工作台
          </button>
          <header className="run-heading">
            <img src="./assets/sofa-fs320.png" alt="北欧布艺沙发" />
            <div>
              <h1>北欧布艺沙发 FS-320</h1>
              <p>
                <b /> 研究市场　·　今天 09:42 启动
              </p>
            </div>
            <button>
              <FileText />
              查看任务详情
            </button>
          </header>
          <RunProgress />
          <div className="run-grid">
            <section className="run-main">
              <div className="activity-panel">
                <h2>
                  当前阶段：<span>研究市场</span>
                </h2>
                <p>
                  正在系统性收集和分析市场数据，识别目标市场需求与竞争格局。
                </p>
                <div className="run-stats">
                  {[
                    [Clock, "已用时", "18 分 47 秒"],
                    [Hourglass, "预计剩余", "27 分钟"],
                    [ChartPie, "已收集证据", "42 / 120 项"],
                    [Pulse, "当前进度", "46%"],
                  ].map(([Icon, l, v]) => (
                    <div key={l}>
                      <Icon />
                      <span>
                        {l}
                        <strong>{v}</strong>
                      </span>
                    </div>
                  ))}
                </div>
                <h3>活动日志</h3>
                <ol className="activity-log">
                  {[
                    [
                      "09:42:10",
                      "任务已启动",
                      "开始研究市场：北欧布艺沙发 FS-320",
                    ],
                    [
                      "09:42:18",
                      "市场范围确认",
                      "目标市场：德国、法国、荷兰、瑞典、丹麦",
                    ],
                    [
                      "09:44:03",
                      "数据源连接",
                      "连接 5 个数据源，4 个可用，1 个部分可用",
                    ],
                    [
                      "09:47:21",
                      "证据收集进行中",
                      "正在抓取 Wayfair.de 的商品数据（第 3/10 页）",
                    ],
                  ].map((r, i) => (
                    <li className={i === 3 ? "live" : ""} key={r[0]}>
                      <time>{r[0]}</time>
                      <Check />
                      <strong>{r[1]}</strong>
                      <span>{r[2]}</span>
                    </li>
                  ))}
                  <li className="upcoming">
                    <time>—:—:—</time>
                    <i />
                    <strong>初步洞察生成</strong>
                    <span>待收集足够证据后生成初步市场洞察</span>
                  </li>
                </ol>
                <div className="failure-panel">
                  <WarningCircle weight="fill" />
                  <span>
                    <strong>
                      部分数据源获取失败 <em>部分失败</em>
                    </strong>
                    <p>
                      1
                      个市场数据源连接失败，将基于可用数据继续分析，不影响整体进度。
                    </p>
                  </span>
                  <small>失败源：Statista 欧盟家具市场报告 API</small>
                  <button onClick={retry}>
                    <ArrowClockwise className={retrying ? "spinning" : ""} />
                    {retrying ? "重试中" : "重试连接"}
                  </button>
                </div>
              </div>
              <button
                className="tech-toggle"
                onClick={() => setDetails(!details)}
              >
                <Code />
                技术详情（点击展开）{details ? <CaretUp /> : <CaretDown />}
              </button>
              {details && (
                <div className="technical-details">
                  <dl>
                    <dt>任务 ID</dt>
                    <dd>TSK-20260810-0321</dd>
                    <dt>执行版本</dt>
                    <dd>FurniScope Runtime 2.4</dd>
                    <dt>最后检查点</dt>
                    <dd>市场数据收集 46%</dd>
                    <dt>重试策略</dt>
                    <dd>指数退避，最多 3 次</dd>
                  </dl>
                  <p>
                    此处仅展示面向支持与排障的运行信息，不提供内部节点控制。
                  </p>
                </div>
              )}
            </section>
            <aside className={`decision-panel ${confirmed ? "confirmed" : ""}`}>
              <header>
                <h2>{confirmed ? "已完成确认" : "需要你的确认"}</h2>
                <b>{confirmed ? "已处理" : "重要"}</b>
              </header>
              {confirmed ? (
                <div className="confirmed-state">
                  <Check />
                  <strong>
                    {choice === "include"
                      ? "已纳入机会评估"
                      : "暂不纳入机会评估"}
                  </strong>
                  <p>AI 已收到你的选择，并继续执行后续研究。</p>
                  <button onClick={() => setConfirmed(false)}>修改选择</button>
                </div>
              ) : (
                <>
                  <p>
                    我们识别到“可拆洗沙发套”是核心卖点，该属性在目标市场的重要性存在不确定性，请确认是否纳入评估。
                  </p>
                  <div className="evidence-box">
                    <header>
                      <strong>关键证据（共 {allEvidence ? 8 : 3} 项）</strong>
                      <button onClick={() => setAllEvidence(!allEvidence)}>
                        {allEvidence ? "收起" : "查看全部"}
                      </button>
                    </header>
                    {[
                      "Wayfair.de 商品评论：可拆洗相关提及占 18.7%",
                      "Reddit 家具讨论：清洁便利性是重要购买因素",
                      "Google Trends：相关搜索热度环比上升 12%",
                      ...(allEvidence
                        ? [
                            "竞品标题卖点中出现率 61%",
                            "相似关键词搜索量增长 24%",
                            "退货原因中清洁相关占比 18%",
                            "北欧用户偏好可维护面料",
                            "环保认证与可洗属性正相关",
                          ]
                        : []),
                    ].map((x, i) => (
                      <article key={x}>
                        <strong>
                          {
                            [
                              "Wayfair.de 商品评论分析",
                              "Reddit r/furniture 讨论",
                              "Google Trends（德国）",
                              "竞品信息汇总",
                              "关键词趋势",
                              "退货数据",
                              "用户访谈摘要",
                              "材料报告",
                            ][i]
                          }
                        </strong>
                        <p>{x}</p>
                        <time>2026-08-{i < 1 ? "10" : "09"}</time>
                      </article>
                    ))}
                  </div>
                  <fieldset>
                    <legend>请选择</legend>
                    <label className={choice === "include" ? "selected" : ""}>
                      <input
                        type="radio"
                        name="decision"
                        checked={choice === "include"}
                        onChange={() => setChoice("include")}
                      />
                      <span>
                        <strong>纳入评估</strong>
                        <small>
                          将“可拆洗沙发套”作为核心卖点纳入后续评估。
                        </small>
                      </span>
                    </label>
                    <label className={choice === "exclude" ? "selected" : ""}>
                      <input
                        type="radio"
                        name="decision"
                        checked={choice === "exclude"}
                        onChange={() => setChoice("exclude")}
                      />
                      <span>
                        <strong>不纳入评估</strong>
                        <small>将忽略该卖点，仅基于其他属性进行评估。</small>
                      </span>
                    </label>
                  </fieldset>
                  <button
                    className="confirm-choice"
                    onClick={() => setConfirmed(true)}
                  >
                    确认选择
                  </button>
                </>
              )}
            </aside>
          </div>
          <footer>数据更新于 2026-08-10 10:01（上海时间）</footer>
        </div>
      </section>
    </main>
  );
}

const insightTabs = [
  "总览",
  "销量预测",
  "竞品",
  "评价需求",
  "价格",
  "机会",
  "证据",
];
const forecastExampleRows = [
  ["2026-08-12", "de", "FS-320", 128, 112, 145],
  ["2026-08-12", "fr", "FS-320", 94, 81, 108],
  ["2026-08-12", "de", "FS-876", 76, 65, 88],
  ["2026-08-13", "de", "FS-320", 134, 116, 151],
  ["2026-08-13", "fr", "FS-320", 101, 87, 116],
  ["2026-08-13", "de", "FS-876", 82, 70, 95],
  ["2026-08-14", "de", "FS-320", 141, 122, 159],
  ["2026-08-14", "fr", "FS-320", 108, 93, 124],
];

function downloadForecast(rows, type) {
  const headers = [
    "日期",
    "Site",
    "SKU",
    "预测销量",
    "预测下限",
    "预测上限",
    "销量预测区间",
    "可靠度值",
  ];
  const exportRows = rows.map((row) => [
    ...row,
    `${row[4]} ~ ${row[5]}`,
    row[6] ?? "—",
  ]);
  const csvCell = (value) => {
    const text = String(value ?? "");
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const content =
    type === "json"
      ? JSON.stringify(
          exportRows.map((r) => Object.fromEntries(headers.map((h, i) => [h, r[i]]))),
          null,
          2,
        )
      : `\uFEFF${[headers, ...exportRows].map((r) => r.map(csvCell).join(",")).join("\n")}`;
  const blob = new Blob([content], {
    type: type === "json" ? "application/json" : "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `forecast_all-sku_all-site.${type}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 800);
}

function ForecastWorkspace() {
  const [sku, setSku] = useState("");
  const [site, setSite] = useState("");
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState("day");
  const [period, setPeriod] = useState("7");
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [rows, setRows] = useState([]);
  const scope =
    !sku && !site
      ? "全部 SKU × 全部站点"
      : sku && !site
        ? `${sku.split(",").length} 个 SKU × 全部站点`
        : !sku && site
          ? `全部 SKU × ${site.split(",").length} 个站点`
          : `${sku.split(",").length} 个 SKU × ${site.split(",").length} 个站点`;
  const choose = (f) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".xlsx")) {
      setError("仅支持 .xlsx 格式的历史数据文件");
      return;
    }
    setFile(f);
    setError("");
    setStatus("validated");
  };
  const useSample = () => {
    setFile({ name: "家具历史销售数据_2023-2026.xlsx", size: 2480000 });
    setStatus("validated");
    setError("");
  };
  const run = () => {
    if (!file) return setError("请先上传历史销售数据文件");
    if (!/^\d+$/.test(period) || Number(period) <= 0)
      return setError(
        `预测${mode === "day" ? "天数" : "周数"}必须为大于 0 的整数`,
      );
    setError("");
    setStatus("running");
    setTimeout(() => {
      setRows(forecastExampleRows);
      setStatus("done");
    }, 900);
  };
  return (
    <main className="workspace forecast-page">
      <AppSidebar page="forecast" />
      <section className="workspace-main">
        <WizardTop />
        <div className="forecast-workspace">
          <header>
            <div>
              <span className="forecast-model-label">XGBoost · Sales v4</span>
              <h1>商品销量预测</h1>
              <p>
                上传日度历史销售数据，按 SKU 和站点预测未来销量并生成备货建议。
              </p>
            </div>
            <button onClick={() => (location.hash = "analysis")}>
              <Pulse />
              通过 AI 对话预测
            </button>
          </header>
          <div className="forecast-workspace-grid">
            <section className="forecast-setup-card">
              <header>
                <b>1</b>
                <div>
                  <h2>上传历史数据</h2>
                  <p>Excel 列名需与模板完全一致，周预测会自动聚合日度数据。</p>
                </div>
                <button onClick={useSample}>载入模板</button>
              </header>
              <label className={`forecast-upload ${file ? "has-file" : ""}`}>
                <FileXls />
                <span>
                  {file ? (
                    <>
                      <strong>{file.name}</strong>
                      <small>
                        {(file.size / 1048576).toFixed(1)} MB · 数据校验通过
                      </small>
                    </>
                  ) : (
                    <>
                      <strong>拖拽或点击上传 Excel</strong>
                      <small>仅支持 .xlsx，建议至少包含 8 周历史数据</small>
                    </>
                  )}
                </span>
                {file && <Check />}
                <input
                  type="file"
                  accept=".xlsx"
                  onChange={(e) => choose(e.target.files[0])}
                />
              </label>
              <div className="required-columns">
                <span>必填字段</span>
                {[
                  "时间",
                  "销量",
                  "站点",
                  "SKU",
                  "性别",
                  "运营熟练度",
                  "主要促销活动",
                  "每日库存",
                ].map((x) => (
                  <code key={x}>{x}</code>
                ))}
              </div>
            </section>
            <section className="forecast-setup-card">
              <header>
                <b>2</b>
                <div>
                  <h2>筛选预测范围</h2>
                  <p>两个条件均可留空；多值请使用英文逗号分隔。</p>
                </div>
              </header>
              <div className="forecast-filter-fields">
                <label>
                  SKU（选填）
                  <input
                    value={sku}
                    onChange={(e) => setSku(e.target.value)}
                    placeholder="例如：FS-320, FS-876"
                  />
                  <small>留空时预测文件中的全部 SKU</small>
                </label>
                <label>
                  站点 Site（选填）
                  <input
                    value={site}
                    onChange={(e) => setSite(e.target.value)}
                    placeholder="例如：de, fr, us"
                  />
                  <small>留空时预测文件中的全部站点</small>
                </label>
              </div>
              <div className="forecast-scope">
                <Database />
                <span>
                  本次范围<strong>{scope}</strong>
                </span>
              </div>
            </section>
            <section className="forecast-setup-card forecast-parameters">
              <header>
                <b>3</b>
                <div>
                  <h2>设置预测参数</h2>
                  <p>选择时间粒度和需要向未来预测的周期。</p>
                </div>
              </header>
              <div>
                <label>
                  预测类型
                  <span className="forecast-mode-switch">
                    <button
                      className={mode === "day" ? "active" : ""}
                      onClick={() => {
                        setMode("day");
                        setPeriod("7");
                      }}
                    >
                      天预测
                    </button>
                    <button
                      className={mode === "week" ? "active" : ""}
                      onClick={() => {
                        setMode("week");
                        setPeriod("1");
                      }}
                    >
                      周预测
                    </button>
                  </span>
                </label>
                <label>
                  预测{mode === "day" ? "天数" : "周数"}
                  <input
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                    inputMode="numeric"
                  />
                  <small>默认 {mode === "day" ? "7 天" : "1 周"}</small>
                </label>
                <label>
                  预测区间
                  <select defaultValue="90">
                    <option value="80">80%</option>
                    <option value="90">90%</option>
                    <option value="95">95%</option>
                  </select>
                </label>
              </div>
            </section>
          </div>
          {error && (
            <div className="forecast-form-error">
              <WarningCircle />
              {error}
            </div>
          )}
          <button
            className="forecast-run"
            onClick={run}
            disabled={status === "running"}
          >
            <Pulse className={status === "running" ? "spinning" : ""} />
            {status === "running" ? "正在清洗数据并运行预测…" : "开始预测"}
          </button>
          {status === "done" && (
            <section className="forecast-output">
              <header>
                <div>
                  <Check />
                  <span>
                    <h2>预测已完成</h2>
                    <p>
                      {scope} ·{" "}
                      {mode === "day" ? `${period} 天预测` : `${period} 周预测`}{" "}
                      · MAPE 8.7%
                    </p>
                  </span>
                </div>
                <div>
                  <button onClick={() => downloadForecast(rows, "csv")}>
                    <DownloadSimple />
                    导出 CSV
                  </button>
                  <button onClick={() => downloadForecast(rows, "json")}>
                    <DownloadSimple />
                    导出 JSON
                  </button>
                </div>
              </header>
              <div className="forecast-output-kpis">
                {[
                  ["预测组合", "6"],
                  ["预测总销量", "864 件"],
                  ["同比变化", "+18.6%"],
                  ["模型置信度", "90%"],
                ].map((x) => (
                  <span key={x[0]}>
                    <small>{x[0]}</small>
                    <strong>{x[1]}</strong>
                  </span>
                ))}
              </div>
              <div className="forecast-output-body">
                <div className="forecast-result-table">
                  <table>
                    <thead>
                      <tr>
                        {[
                          "日期",
                          "Site",
                          "SKU",
                          "预测销量",
                          "预测下限",
                          "预测上限",
                          "销量预测区间",
                          "可靠度值",
                        ].map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={i}>
                          {[...r, `${r[4]} ~ ${r[5]}`, r[6] ?? "—"].map((x) => (
                            <td key={x}>{x}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <aside>
                  <h3>生产与备货建议</h3>
                  <strong>建议首批备货 950 件</strong>
                  <p>
                    FS-320
                    在德国站点未来需求最强，建议优先安排中性色与可拆洗款；分两批投产以降低预测波动风险。
                  </p>
                  <button
                    onClick={() =>
                      (location.hash = "insight-detail?tab=forecast")
                    }
                  >
                    同步到市场洞察
                  </button>
                </aside>
              </div>
            </section>
          )}
        </div>
      </section>
    </main>
  );
}

const opportunities = [
  {
    rank: 1,
    title: "紧凑空间多功能可扩展餐桌",
    score: 82,
    confidence: 78,
    gap: "高",
    evidence: 136,
    position: "为城市小户型提供灵活扩展与空间优化方案",
  },
  {
    rank: 2,
    title: "高品质橡木 / 实木可扩展餐桌",
    score: 79,
    confidence: 74,
    gap: "高",
    evidence: 112,
    position: "突出天然材料工艺与长期价值",
  },
  {
    rank: 3,
    title: "易组装与稳定结构",
    score: 75,
    confidence: 72,
    gap: "中高",
    evidence: 98,
    position: "降低购买决策阻力，提升使用信心",
  },
  {
    rank: 3,
    title: "北欧极简设计与配色",
    score: 70,
    confidence: 70,
    gap: "中",
    evidence: 88,
    position: "强化北欧家居美学与搭配需求",
  },
  {
    rank: 4,
    title: "中高端价位段性价比提升",
    score: 68,
    confidence: 68,
    gap: "中",
    evidence: 74,
    position: "提升中高端价位段价值感知",
  },
];
function InsightsSidebar() {
  return <WizardSidebar page="insights" />;
}
function EvidenceDrawer({ selected, onClose }) {
  return (
    <aside className="insight-evidence">
      <header>
        <h2>证据（机会 #{selected.rank}）</h2>
        <button onClick={onClose}>
          <X />
        </button>
      </header>
      <h3>{selected.title}</h3>
      <div className="evidence-badges">
        <span>
          机会得分 <b>{selected.score}</b>
        </span>
        <span>
          置信度 <b>{selected.confidence}%</b>
        </span>
      </div>
      <h4>核心论断</h4>
      <p>
        北欧城市家庭对节省空间、灵活扩展且易维护的家具需求强烈，当前市场在产品组合与价格带上存在明显供给缺口。
      </p>
      <h4>客户评价摘录</h4>
      {[
        [
          "展开后座位够用，收起来不占地方，公寓救星。",
          "Maria L.",
          "瑞典",
          "2026-06-18",
        ],
        [
          "桌子不错，但扩展时简板需要两个人一起操作。",
          "Jonas P.",
          "挪威",
          "2026-05-27",
        ],
        [
          "很适合小户型，不过希望收纳时更轻松一些。",
          "Sofie K.",
          "丹麦",
          "2026-07-02",
        ],
      ].map((x) => (
        <article key={x[1]}>
          <p>“{x[0]}”</p>
          <span>
            {x[1]}　{x[2]} <time>{x[3]}</time>
          </span>
        </article>
      ))}
      <h4>市场报告引用</h4>
      <ol>
        <li>Statista — Scandinavian Furniture Market Outlook 2026</li>
        <li>Euromonitor — Home Furniture in Nordic Countries</li>
        <li>DNB Markets — Nordic Housing Trend Report 2026</li>
      </ol>
      <div className="evidence-summary">
        <span>
          证据数<strong>{selected.evidence}</strong>
        </span>
        <span>
          正向占比<strong>81%</strong>
        </span>
        <span>
          来源类型<strong>5 类</strong>
        </span>
      </div>
    </aside>
  );
}
function DemandForecast() {
  const [period, setPeriod] = useState("周");
  const [horizon, setHorizon] = useState("8");
  const [scenario, setScenario] = useState("基准");
  const [running, setRunning] = useState(false);
  const multipliers = { 保守: 0.86, 基准: 1, 乐观: 1.14 };
  const base = Math.round(1280 * multipliers[scenario]);
  const run = () => {
    setRunning(true);
    setTimeout(() => setRunning(false), 900);
  };
  return (
    <section className="forecast-panel">
      <header>
        <div>
          <span className="model-badge">XGBoost · v4</span>
          <h2>需求预测与产销建议</h2>
          <p>
            根据市场销售、库存、促销和家具属性，预测 FS-876
            在北欧市场的未来需求。
          </p>
        </div>
        <button onClick={run}>
          <Pulse className={running ? "spinning" : ""} />
          {running ? "重新计算中" : "更新预测"}
        </button>
      </header>
      <div className="forecast-controls">
        <label>
          产品
          <select defaultValue="FS-876">
            <option>FS-876 可扩展餐桌</option>
            <option>FS-320 北欧布艺沙发</option>
          </select>
        </label>
        <label>
          市场站点
          <select defaultValue="nordic">
            <option value="nordic">北欧四国 · 全部站点</option>
            <option>瑞典 · Amazon.se</option>
            <option>丹麦 · Amazon.dk</option>
          </select>
        </label>
        <label>
          预测粒度
          <select value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option>日</option>
            <option>周</option>
            <option>月</option>
          </select>
        </label>
        <label>
          预测周期
          <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
            <option value="4">未来 4 {period}</option>
            <option value="8">未来 8 {period}</option>
            <option value="12">未来 12 {period}</option>
          </select>
        </label>
      </div>
      <div className="forecast-body">
        <div className="forecast-chart">
          <div className="chart-head">
            <strong>历史销量与未来预测</strong>
            <span>
              <i />
              历史实际　
              <i />
              预测销量　
              <em />
              预测区间
            </span>
          </div>
          <div className="bar-chart">
            {[42, 48, 39, 56, 61, 58, 70, 74, 68, 79, 84, 88].map((n, i) => (
              <div key={i} className={i > 6 ? "predicted" : ""}>
                <span style={{ height: `${n}%` }} />
                <small>{i > 6 ? `${i - 6}${period}` : `W${28 + i}`}</small>
              </div>
            ))}
          </div>
          <footer>
            模型回测：MAPE 8.7%　·　训练数据 104 周　·　最近更新 2026-08-10
          </footer>
        </div>
        <aside className="forecast-result">
          <div className="scenario-switch">
            {["保守", "基准", "乐观"].map((x) => (
              <button
                className={x === scenario ? "active" : ""}
                onClick={() => setScenario(x)}
                key={x}
              >
                {x}
              </button>
            ))}
          </div>
          <span>
            未来 {horizon} {period}预测销量
            <strong>{base.toLocaleString()} 件</strong>
            <small>较上一周期 +18.6%</small>
          </span>
          <div>
            <span>
              建议生产量<b>{Math.round(base * 1.11).toLocaleString()} 件</b>
            </span>
            <span>
              安全库存<b>{Math.round(base * 0.11)} 件</b>
            </span>
            <span>
              建议排产<b>第 32–35 周</b>
            </span>
            <span>
              缺货风险<b className="medium">中</b>
            </span>
          </div>
          <p>
            <strong>生产建议</strong>{" "}
            优先安排橡木色、紧凑尺寸和易扩展结构；结合在途库存后分两批投产，降低需求波动风险。
          </p>
        </aside>
      </div>
      <div className="preference-strip">
        <strong>增长偏好</strong>
        {[
          ["紧凑尺寸", "+24%"],
          ["天然橡木", "+21%"],
          ["单人易扩展", "+19%"],
          ["€429–499", "+17%"],
        ].map((x) => (
          <span key={x[0]}>
            {x[0]}
            <b>{x[1]}</b>
          </span>
        ))}
      </div>
    </section>
  );
}
function InsightTabPanel({ tab, onEvidence }) {
  if (tab === "销量预测") return <DemandForecast />;
  if (tab === "总览")
    return (
      <div className="overview-stack">
        <div className="merged-overview">
          <section>
            <h2>市场信号摘要</h2>
            <div className="signal-grid">
              {[
                ["需求增长", "+18.6%", "近 12 个月"],
                ["活跃竞品", "247", "北欧四国"],
                ["中位价格", "€429", "核心价格带"],
                ["正向评价", "67.4%", "94,600 条样本"],
              ].map((x) => (
                <span key={x[0]}>
                  {x[0]}
                  <b>{x[1]}</b>
                  <small>{x[2]}</small>
                </span>
              ))}
            </div>
          </section>
          <section>
            <h2>AI 结论</h2>
            <p>
              空间效率、稳定结构和易维护表面是增长最明确的三类需求；中端价格带仍存在供给缺口。
            </p>
            <button onClick={onEvidence}>查看结论证据</button>
          </section>
        </div>
      </div>
    );
  if (tab === "竞品")
    return (
      <div className="competitor-panel">
        <div className="competitor-metrics">
          {[
            ["监控竞品", "247"],
            ["平均价格", "€529"],
            ["平均评分", "4.23"],
            ["高危竞品", "38"],
          ].map((x) => (
            <span key={x[0]}>
              {x[0]}
              <b>{x[1]}</b>
            </span>
          ))}
        </div>
        <ReportTable
          headers={["竞品", "品牌", "价格", "评分", "月销量", "相似度", "类型"]}
          rows={[
            [
              "Extendable Oak Dining Table",
              "Nordform",
              "€489",
              "4.6",
              "3,200+",
              "94%",
              "直接竞品",
            ],
            [
              "Nordic Drop-leaf Table",
              "IKEA",
              "€399",
              "4.4",
              "2,180+",
              "87%",
              "直接竞品",
            ],
            [
              "Solid Wood Extension Table",
              "Rowico",
              "€649",
              "4.8",
              "1,420+",
              "78%",
              "标杆竞品",
            ],
            [
              "Compact Dining Set",
              "JYSK",
              "€329",
              "4.1",
              "1,860+",
              "64%",
              "替代竞品",
            ],
          ]}
        />
      </div>
    );
  if (tab === "评价需求")
    return (
      <div className="review-needs">
        <aside>
          {[
            ["舒适与空间", 847],
            ["结构稳定", 634],
            ["耐用性", 521],
            ["组装便捷", 398],
            ["物流包装", 312],
          ].map((x, i) => (
            <button className={i === 0 ? "active" : ""} key={x[0]}>
              {x[0]}
              <b>{x[1]}</b>
            </button>
          ))}
        </aside>
        <section>
          <h2>需求簇：空间效率</h2>
          <div className="need-bubbles">
            {[
              ["展开空间充足", "28%", "positive"],
              ["收纳占地小", "21%", "positive"],
              ["展开操作费力", "14%", "negative"],
              ["尺寸描述不清", "9%", "negative"],
            ].map((x) => (
              <span className={x[2]} key={x[0]}>
                {x[0]}
                <b>{x[1]}</b>
              </span>
            ))}
          </div>
          <div className="review-proof">
            <strong>代表性评论证据</strong>
            <p>“展开后可以坐下六个人，收起后又很适合公寓餐厅。”</p>
            <small>Amazon.se · 5.0 · 2026-06-18 · 已验证购买</small>
            <button onClick={onEvidence}>打开完整证据链</button>
          </div>
        </section>
      </div>
    );
  if (tab === "价格")
    return (
      <div className="pricing-panel">
        <h2>价格带机会</h2>
        {[
          ["€200 以下", 12, 8],
          ["€200–349", 31, 24],
          ["€350–499", 47, 29],
          ["€500–699", 28, 36],
          ["€700 以上", 14, 22],
        ].map((x) => (
          <div key={x[0]}>
            <span>{x[0]}</span>
            <i>
              <b style={{ width: `${x[1] * 2}%` }} />
            </i>
            <em>
              需求 {x[1]}% · 供给 {x[2]}%
            </em>
          </div>
        ))}
        <p>
          <strong>建议零售价 €429–499</strong>：需求占比最高，且供给密度比需求低
          18 个百分点。
        </p>
      </div>
    );
  return (
    <div className="evidence-library">
      <header>
        <div>
          <h2>证据库</h2>
          <p>所有洞察均可追溯至授权数据与原始文本。</p>
        </div>
        <button onClick={onEvidence}>打开证据抽屉</button>
      </header>
      <ReportTable
        headers={["证据类型", "来源", "样本量", "时间范围", "质量", "支持结论"]}
        rows={[
          [
            "电商评价",
            "Amazon / Wayfair",
            "94,600",
            "2025.08–2026.07",
            "高",
            "空间效率需求",
          ],
          [
            "竞品商品",
            "4 个北欧站点",
            "38,240 SKU",
            "2026.07",
            "高",
            "价格带缺口",
          ],
          [
            "市场报告",
            "Statista / Euromonitor",
            "328 份",
            "2024–2026",
            "中高",
            "市场增长",
          ],
          [
            "搜索趋势",
            "Google Trends",
            "42 组词",
            "近 24 个月",
            "中",
            "需求上升",
          ],
        ]}
      />
    </div>
  );
}
function InsightsDashboard({ showBack = false }) {
  const requestedTab = new URLSearchParams(
    location.hash.split("?")[1] || "",
  ).get("tab");
  const [tab, setTab] = useState(
    requestedTab === "forecast" ? "销量预测" : "机会",
  );
  const [selected, setSelected] = useState(opportunities[0]);
  const [drawer, setDrawer] = useState(requestedTab !== "forecast");
  const rows = [
    ["瑞典", "紧凑空间多功能可扩展餐桌", "250–450", 88, "中", 82, 78, 136],
    ["挪威", "紧凑空间多功能可扩展餐桌", "250–450", 83, "中", 81, 76, 122],
    ["丹麦", "高品质橡木/实木可扩展餐桌", "450–800", 81, "中高", 79, 74, 112],
    ["芬兰", "易组装与稳定结构", "200–350", 76, "中高", 75, 72, 98],
    ["瑞典", "北欧极简设计与配色", "300–550", 72, "中", 70, 70, 86],
    ["丹麦", "中高端价位段性价比提升", "350–600", 69, "中高", 68, 68, 74],
    ["挪威", "易清洁耐用桌面", "200–400", 64, "高", 61, 64, 62],
  ];
  return (
    <main className="workspace insights-page">
      <InsightsSidebar />
      <section className="workspace-main">
        <WizardTop />
        <div className="scope-banner">
          <ShieldCheck />
          数据范围：已授权数据集（跨境家具·北欧市场）
          <span />
          覆盖国家：瑞典、挪威、丹麦、芬兰
          <span />
          数据新鲜度：2026 年 07 月<span />
          覆盖来源：电商评价 1,248,763 条、市场报告 328 份
        </div>
        <div className={`insights-canvas ${drawer ? "drawer-open" : ""}`}>
          <section className="insights-content">
            <header className="insights-heading">
              <div>
                {showBack && (
                  <button
                    className="insights-back"
                    onClick={() => (location.hash = "insights")}
                  >
                    <ArrowLeft />
                    返回洞察列表
                  </button>
                )}
                <h1>证据优先机会地图</h1>
                <p>
                  分析对象：可扩展餐桌　｜　目标市场：北欧四国　｜　更新时间：2026-08-10
                </p>
              </div>
              <div className="insight-score">
                <span>
                  机会得分 <b>82</b>
                </span>
                <span>
                  置信度 <b>78%</b>
                </span>
              </div>
            </header>
            <nav className="insight-tabs">
              {insightTabs.map((x) => (
                <button
                  className={x === tab ? "active" : ""}
                  onClick={() => setTab(x)}
                  key={x}
                >
                  {x}
                </button>
              ))}
            </nav>
            {tab !== "机会" ? (
              <InsightTabPanel tab={tab} onEvidence={() => setDrawer(true)} />
            ) : (
              <>
                <div className="insight-top">
                  <section>
                    <h2>市场契合度雷达图</h2>
                    <img
                      src="./assets/market-radar.png"
                      alt="六维市场契合度雷达图"
                    />
                  </section>
                  <section className="need-ranking">
                    <h2>需求簇（按机会得分排序）</h2>
                    {opportunities.map((o) => (
                      <button
                        className={o.title === selected.title ? "selected" : ""}
                        key={o.title}
                        onClick={() => {
                          setSelected(o);
                          setDrawer(true);
                        }}
                      >
                        <b>{o.rank}</b>
                        <span>{o.title}</span>
                        <em>{o.score}</em>
                        <small>{o.evidence} 项证据</small>
                      </button>
                    ))}
                  </section>
                </div>
                <h2 className="section-title">机会优先级（依据证据）</h2>
                <div className="opportunity-cards">
                  {opportunities.map((o) => (
                    <button
                      className={o.title === selected.title ? "selected" : ""}
                      onClick={() => {
                        setSelected(o);
                        setDrawer(true);
                      }}
                      key={o.title}
                    >
                      <header>
                        <b>{o.rank}</b>
                        <strong>{o.title}</strong>
                      </header>
                      <div>
                        <span>
                          <b>{o.score}</b>机会得分
                        </span>
                        <span>
                          <b>{o.confidence}%</b>置信度
                        </span>
                      </div>
                      <p>
                        市场缺口 <strong>{o.gap}</strong>　证据数{" "}
                        <strong>{o.evidence}</strong>
                      </p>
                      <small>建议定位：{o.position}</small>
                    </button>
                  ))}
                </div>
                <h2 className="section-title">机会明细</h2>
                <div className="insight-table">
                  <table>
                    <thead>
                      <tr>
                        {[
                          "排名",
                          "市场",
                          "需求簇",
                          "价格带（欧元）",
                          "需求指数",
                          "竞品饱和度",
                          "机会得分",
                          "置信度",
                          "证据数",
                        ].map((x) => (
                          <th key={x}>{x}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={i}>
                          <td>{i + 1}</td>
                          {r.map((c, j) => (
                            <td
                              className={j === 5 ? "score-cell" : ""}
                              key={`${c}-${j}`}
                            >
                              {c}
                              {j === 4 || j === 5 ? "%" : ""}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
          {drawer && (
            <EvidenceDrawer
              selected={selected}
              onClose={() => setDrawer(false)}
            />
          )}
        </div>
      </section>
    </main>
  );
}

const reportSections = [
  "结论",
  "机会",
  "工程建议",
  "制造适配",
  "风险",
  "验证清单",
];
const reportEvidence = {
  结论: [
    "北欧小户型家庭家具需求趋势 2026",
    "可扩展餐桌品类机会评分（北欧五国）",
    "竞品结构与定价对标分析",
  ],
  机会: [
    "瑞典小户型家庭占比 67%",
    "丹麦可扩展餐桌搜索增长 21%",
    "中端价格带供给密度偏低",
  ],
  工程建议: [
    "10,000 次开合循环测试规范",
    "EN 12521 家用桌安全标准",
    "用户评论中的稳定性诉求",
  ],
  制造适配: ["供应链成熟度评估", "BOM 与包装成本模型", "40HQ 装柜量测算"],
  风险: ["结构耐久性风险", "运输包装破损风险", "渠道价格接受度风险"],
  验证清单: ["结构耐久验证方案", "ISTA 3A 跌落测试", "价格敏感度 A/B 测试"],
};
function ReportEvidence({ section, onClose }) {
  return (
    <aside className="report-evidence">
      <header>
        <h2>结论证据（{section}）</h2>
        <button onClick={onClose}>
          <X />
        </button>
      </header>
      <section>
        <h3>证据摘要</h3>
        <p>
          北欧市场对可扩展餐桌存在明确需求与价值空间，但应在完成关键工程、物流与需求验证后，以小批量试销方式进入。
        </p>
        <div className="report-evidence-scores">
          <span>
            机会得分 <b>82</b>
          </span>
          <span>
            置信度 <b>78%</b>
          </span>
        </div>
      </section>
      <h3>引用证据</h3>
      {reportEvidence[section].map((x, i) => (
        <article key={x}>
          <strong>
            {i + 1}. {x}
          </strong>
          <p>
            {
              [
                "基于市场规模、增速、竞争强度和产品匹配度的综合证据。",
                "来源覆盖电商评价、市场报告与行业数据，结论方向一致。",
                "证据质量良好，仍需通过试销验证最终转化表现。",
              ][i]
            }
          </p>
          <span>
            来源：FurniScope 数据库　2026-07-{18 + i * 4}　置信度 {80 - i * 2}%
          </span>
        </article>
      ))}
      <p className="evidence-note">
        置信度基于数据质量、样本规模、来源一致性与时效性综合计算。
      </p>
    </aside>
  );
}
function ReportTable({ headers, rows }) {
  return (
    <div className="report-table">
      <table>
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c) => (
                <td key={c}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function DecisionReport() {
  const [section, setSection] = useState("结论");
  const [drawer, setDrawer] = useState(true);
  const go = (s) => {
    setSection(s);
    setDrawer(true);
    document
      .getElementById(`report-${s}`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <main className="workspace decision-report">
      <WizardSidebar page="report" />
      <section className="workspace-main">
        <WizardTop />
        <div className={`report-layout ${drawer ? "with-evidence" : ""}`}>
          <aside className="report-toc">
            <h2>目录</h2>
            {reportSections.map((s, i) => (
              <button
                className={s === section ? "active" : ""}
                onClick={() => go(s)}
                key={s}
              >
                <span>{i + 1}</span>
                {s}
              </button>
            ))}
            <div>
              <h3>报告信息</h3>
              <p>产品：可扩展餐桌 FS-876</p>
              <p>目标市场：北欧五国</p>
              <p>报告日期：2026-08-10</p>
            </div>
          </aside>
          <article className="report-document">
            <header className="report-title">
              <div>
                <h1>可扩展餐桌 FS-876 北欧市场进入决策</h1>
                <p>执行决策备忘录 · 基于授权市场数据与产品资料生成</p>
              </div>
            </header>
            <section
              id="report-结论"
              className="conclusion-hero"
              onClick={() => {
                setSection("结论");
                setDrawer(true);
              }}
            >
              <div className="conclusion-copy">
                <Check />
                <div>
                  <small>结论摘要</small>
                  <h2>有条件进入：先完成 3 项验证再启动小批量试销</h2>
                  <p>
                    北欧市场对可扩展餐桌需求明确，价格与价值空间匹配；建议在关键工程性能、运输包装与渠道价格验证通过后，以小批量试销控制风险。
                  </p>
                </div>
              </div>
              <div className="report-scores">
                <span>
                  机会得分 <b>82</b>
                  <small>/100</small>
                </span>
                <span>
                  置信度 <b>78%</b>
                </span>
              </div>
              <div className="decision-conditions">
                {[
                  [
                    "1",
                    "结构耐久验证通过",
                    "完成 10,000 次开合循环，稳定性与安全性达标",
                  ],
                  [
                    "2",
                    "扁平包装与运输验证通过",
                    "ISTA 3A 通过，破损率 ≤ 1.0%",
                  ],
                  [
                    "3",
                    "渠道价格与需求验证通过",
                    "建议零售价 €429–499，购买意愿 ≥ 60%",
                  ],
                ].map((x) => (
                  <div key={x[0]}>
                    <b>{x[0]}</b>
                    <strong>{x[1]}</strong>
                    <p>{x[2]}</p>
                  </div>
                ))}
              </div>
            </section>
            <section
              id="report-机会"
              className="report-section"
              onClick={() => {
                setSection("机会");
                setDrawer(true);
              }}
            >
              <h2>1. 北欧市场机会</h2>
              <ReportTable
                headers={[
                  "排名",
                  "市场",
                  "机会吸引力",
                  "市场规模（2026E）",
                  "CAGR",
                  "可实现 ASP",
                  "核心驱动因素",
                ]}
                rows={[
                  [
                    "1",
                    "瑞典",
                    "86",
                    "€2.48 亿",
                    "7.6%",
                    "€429–499",
                    "小户型占比高、线上渠道成熟",
                  ],
                  [
                    "2",
                    "挪威",
                    "81",
                    "€1.67 亿",
                    "6.9%",
                    "€449–519",
                    "高购买力、偏好北欧设计",
                  ],
                  [
                    "3",
                    "丹麦",
                    "79",
                    "€1.21 亿",
                    "6.3%",
                    "€429–499",
                    "空间效率需求持续提升",
                  ],
                  [
                    "4",
                    "芬兰",
                    "75",
                    "€0.98 亿",
                    "5.8%",
                    "€409–479",
                    "极简风格与功能性偏好",
                  ],
                ]}
              />
            </section>
            <section
              id="report-工程建议"
              className="report-section"
              onClick={() => {
                setSection("工程建议");
                setDrawer(true);
              }}
            >
              <h2>2. 产品工程建议</h2>
              <ReportTable
                headers={["优先级", "建议项", "关键要求", "预期收益"]}
                rows={[
                  [
                    "P1",
                    "开合机构耐久与安全",
                    "≥10,000 次开合；间隙 ≤0.5mm",
                    "降低质保与安全风险",
                  ],
                  [
                    "P2",
                    "桌面耐刮与耐热",
                    "EN 438 耐磨 AC4+；耐热 180℃",
                    "提升长期使用感与口碑",
                  ],
                  [
                    "P3",
                    "扁平包装优化",
                    "体积 ≤0.135m³；单箱 ≤45kg",
                    "降低物流成本与破损率",
                  ],
                  [
                    "P4",
                    "快速装配体验",
                    "单人 25 分钟内完成",
                    "提升转化与评论表现",
                  ],
                ]}
              />
            </section>
            <section
              id="report-制造适配"
              className="report-section manufacturing-section"
              onClick={() => {
                setSection("制造适配");
                setDrawer(true);
              }}
            >
              <h2>3. 制造适配与排产建议</h2>
              <div className="fit-scores">
                {[
                  ["工艺可行性", 82],
                  ["供应链可靠性", 78],
                  ["成本竞争力", 76],
                  ["交付能力", 83],
                  ["质量稳定性", 80],
                ].map((x) => (
                  <span key={x[0]}>
                    {x[0]}
                    <b>{x[1]}</b>
                  </span>
                ))}
              </div>
              <div className="production-plan">
                {[
                  ["未来 8 周预测", "1,280 件"],
                  ["建议生产量", "1,420 件"],
                  ["安全库存", "140 件"],
                  ["建议排产", "第 32–35 周"],
                ].map((x) => (
                  <span key={x[0]}>
                    {x[0]}
                    <strong>{x[1]}</strong>
                  </span>
                ))}
                <p>
                  <b>XGBoost · MAPE 8.7%</b>
                  　建议优先生产橡木色紧凑款，并分两批投产以控制库存风险。
                </p>
              </div>
            </section>
            <section
              id="report-风险"
              className="report-section"
              onClick={() => {
                setSection("风险");
                setDrawer(true);
              }}
            >
              <h2>4. 关键风险评估</h2>
              <ReportTable
                headers={["风险", "概率", "影响", "应对策略"]}
                rows={[
                  ["运输包装破损", "中", "高", "ISTA 3A 与多轮样件运输"],
                  ["结构耐久不足", "中", "高", "加强件与寿命测试"],
                  ["销量预测偏差", "中", "中", "滚动回测并分批排产"],
                  ["价格接受度偏低", "中", "中", "小批量 A/B 定价"],
                ]}
              />
            </section>
            <section
              id="report-验证清单"
              className="report-section"
              onClick={() => {
                setSection("验证清单");
                setDrawer(true);
              }}
            >
              <h2>5. 验证清单（进入前必须完成）</h2>
              <ReportTable
                headers={["验证项", "验证方法", "成功阈值", "建议周期"]}
                rows={[
                  [
                    "结构耐久验证",
                    "实验室循环测试",
                    "≥10,000 次开合且无功能异常",
                    "4–6 周",
                  ],
                  [
                    "包装与运输验证",
                    "ISTA 3A + 跌落测试",
                    "破损率 ≤ 1.0%",
                    "3 周",
                  ],
                  ["预测模型回测", "滚动窗口验证", "MAPE ≤ 12%", "每周更新"],
                  [
                    "价格与需求验证",
                    "线上预售与问卷",
                    "购买意愿 ≥ 60%",
                    "3–4 周",
                  ],
                ]}
              />
            </section>
          </article>
          {drawer && (
            <ReportEvidence
              section={section}
              onClose={() => setDrawer(false)}
            />
          )}
        </div>
      </section>
    </main>
  );
}

export function App() {
  const readRoute = () => {
    const route = location.hash.replace(/^#\/?/, "").split("?")[0];
    const known = [
      "workspace",
      "products",
      "analysis",
      "workflow",
      "work-diary",
      "forecast",
      "wizard",
      "insights",
      "dataset-detail",
      "competitor-tracking",
      "insight-detail",
      "report",
      "report-detail",
      "admin",
      "admin-login",
      "register",
      "forgot-password",
      "login",
      "landing",
    ];
    if (!route) return hasSession() ? "workspace" : "landing";
    if (route === "settings") return "workspace";
    return known.includes(route) ? route : "workspace";
  };
  const [route, setRoute] = useState(readRoute());
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(hasSession());
  const [restoreError, setRestoreError] = useState("");
  const [restoreNonce, setRestoreNonce] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const f = () => {
      setRoute(readRoute());
    };
    const unauthorized = () => {
      setUser(null);
      setChecking(false);
      setRestoreError("");
      location.hash = "login";
    };
    addEventListener("hashchange", f);
    addEventListener("furniscope:unauthorized", unauthorized);
    const restore = async (attempt = 0) => {
      if (!hasSession()) {
        if (!cancelled) setChecking(false);
        return;
      }
      try {
        const identity = await currentUser();
        if (!cancelled) {
          setUser(identity);
          setRestoreError("");
          setChecking(false);
        }
      } catch (error) {
        if (cancelled) return;
        if (!hasSession()) {
          unauthorized();
          return;
        }
        if (attempt >= 6) {
          setRestoreError(error instanceof Error ? error.message : "服务暂时不可用");
          setChecking(false);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, Math.min(2500, 400 * 2 ** attempt)));
        return restore(attempt + 1);
      }
    };
    restore();
    return () => {
      cancelled = true;
      removeEventListener("hashchange", f);
      removeEventListener("furniscope:unauthorized", unauthorized);
    };
  }, [restoreNonce]);
  const enter = (nextUser, target = "workspace") => {
    setUser(nextUser);
    location.hash = target;
    setRoute(target);
  };
  const leave = async () => {
    await apiLogout();
    setUser(null);
    location.hash = "landing";
  };
  if (checking)
    return (
      <main className="login-page">
        <section className="form-panel">
          <div className="login-form">
            <h1>正在恢复安全会话…</h1>
            <p>正在恢复登录状态，请稍候。</p>
          </div>
        </section>
      </main>
    );
  if (!user && hasSession())
    return (
      <main className="login-page">
        <section className="form-panel">
          <div className="login-form">
            <h1>无法连接分析服务</h1>
            <p>{restoreError || "后端暂时无响应，当前登录状态已保留。"}</p>
            <button
              type="button"
              className="login-button"
              onClick={() => {
                setChecking(true);
                setRestoreError("");
                setRestoreNonce((value) => value + 1);
              }}
            >
              重试
            </button>
          </div>
        </section>
      </main>
    );
  if (route === "admin-login")
    return <AdminLogin onEnter={(next) => enter(next, "admin")} />;
  if (route === "register") return <Register />;
  if (route === "forgot-password") return <ForgotPassword />;
  if (route === "landing") {
    if (user) return <Dashboard user={user} onLogout={leave} />;
    return <Landing />;
  }
  if (route === "admin")
    return user?.role_code === "admin" ? (
      <AdminControlCenter />
    ) : (
      <AdminLogin onEnter={(next) => enter(next, "admin")} />
    );
  if (user?.role_code === "admin") return <AdminControlCenter />;
  if (!user && route !== "login") return <Login onEnter={enter} />;
  return route === "workspace" ? (
    <Dashboard user={user} onLogout={leave} />
  ) : route === "products" ? (
    <ProductCatalog Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : route === "analysis" ? (
    <AnalysisCenter Sidebar={WizardSidebar} Topbar={WizardTop} />
  ) : route === "workflow" ? (
    <WorkflowCanvas Sidebar={WizardSidebar} Topbar={WizardTop} />
  ) : route === "work-diary" ? (
    <WorkDiaryPage Sidebar={WizardSidebar} Topbar={WizardTop} />
  ) : route === "forecast" ? (
    <IntegratedForecastWorkspace Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : route === "wizard" ? (
    <WorkflowCanvas Sidebar={WizardSidebar} Topbar={WizardTop} />
  ) : route === "insights" || route === "insight-detail" ? (
    <MarketDatasetCenter Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : route === "dataset-detail" ? (
    <MarketDatasetDetail Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : route === "competitor-tracking" ? (
    <CompetitorTrackingBoard Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : route === "report" ? (
    <ReportLibrary Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : route === "report-detail" ? (
    <ReportDetailPage Sidebar={AppSidebar} Topbar={WizardTop} />
  ) : (
    <Login onEnter={enter} />
  );
}
