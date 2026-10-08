import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CaretRight,
  ChartLineUp,
  Check,
  CheckCircle,
  CircleNotch,
  Clock,
  Database,
  FileText,
  House,
  ListChecks,
  Package,
  PaperPlaneTilt,
  Pause,
  Play,
  PlugsConnected,
  Robot,
  ShieldCheck,
  Target,
  WarningCircle,
  Wrench,
  X,
} from "@phosphor-icons/react";
import { api } from "./api.js";
import "./ai-employee.css";

const STATUS = {
  draft: "草稿",
  planning: "规划中",
  plan_ready: "计划就绪",
  queued: "排队中",
  running: "执行中",
  waiting_human: "待我处理",
  verifying: "核验中",
  delivering: "交付中",
  succeeded: "已完成",
  partial_succeeded: "部分完成",
  failed: "执行失败",
  cancelled: "已取消",
  paused: "已暂停",
  pending: "待执行",
  ready: "待执行",
};

const EVENT_LABELS = {
  "goal.created": "目标已建立",
  "run.queued": "执行已入队",
  "run.started": "开始执行",
  "run.paused": "执行已暂停",
  "run.cancelled": "目标已取消",
  "run.succeeded": "目标已完成",
  "run.failed": "执行失败",
  "step.started": "步骤开始",
  "step.succeeded": "步骤完成",
  "approval.required": "需要你的处理",
  "approval.responded": "处理意见已提交",
};

const SKILL_ICON = {
  market_entry_assessment: ChartLineUp,
  weekly_sales_review: FileText,
  data_readiness_check: Database,
};

const formatTime = (value) => {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const routeState = () => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  return { goal: params.get("goal"), view: params.get("view") || "home" };
};

function StatusBadge({ status }) {
  return <span className={`employee-status is-${status}`}>{STATUS[status] || status}</span>;
}

function EmptyState({ Icon = Target, title, action, actionLabel }) {
  return (
    <div className="employee-empty">
      <Icon />
      <strong>{title}</strong>
      {action && <button type="button" onClick={action}>{actionLabel}</button>}
    </div>
  );
}

function EmployeeSidebar({ view, onView, counts = {} }) {
  const items = [
    ["home", House, "驾驶舱", null],
    ["goals", Target, "目标", counts.active],
    ["approvals", ListChecks, "待我处理", counts.waiting_human],
    ["artifacts", Package, "交付物", counts.artifacts],
    ["capabilities", PlugsConnected, "能力", null],
  ];
  return (
    <aside className="employee-sidebar">
      <button
        type="button"
        className="employee-brand"
        onClick={() => onView("home")}
        aria-label="返回 AI 员工驾驶舱"
      >
        <img src="./assets/furniscope-mark.png" alt="" />
        <span><strong>FurniScope</strong><small>AI EMPLOYEE</small></span>
      </button>
      <nav aria-label="AI 员工导航">
        {items.map(([key, Icon, label, count]) => (
          <button
            type="button"
            key={key}
            className={view === key ? "active" : ""}
            onClick={() => onView(key)}
          >
            <Icon />
            <span>{label}</span>
            {count > 0 && <b>{count}</b>}
          </button>
        ))}
      </nav>
      <div className="employee-online">
        <i />
        <span><strong>执行面在线</strong><small>租户边界已启用</small></span>
      </div>
    </aside>
  );
}

function GoalRow({ goal, onOpen }) {
  const SkillIcon = SKILL_ICON[goal.selected_skill_id] || Target;
  return (
    <button type="button" className="employee-goal-row" onClick={() => onOpen(goal.goal_uuid)}>
      <span className="employee-goal-icon"><SkillIcon /></span>
      <span className="employee-goal-copy">
        <strong>{goal.objective}</strong>
        <small>{goal.skill_name || goal.selected_skill_id} · {formatTime(goal.updated_at)}</small>
      </span>
      <span className="employee-goal-next">
        <small>{goal.next_step_title || (goal.status === "succeeded" ? "交付完成" : "等待启动")}</small>
        <i><b style={{ width: `${goal.progress_percent || 0}%` }} /></i>
      </span>
      <StatusBadge status={goal.status} />
      <CaretRight />
    </button>
  );
}

function ApprovalCard({ item, onRespond, busy }) {
  const href = item.impact?.href;
  return (
    <article className="employee-approval-card">
      <header>
        <span><WarningCircle /></span>
        <div><small>待我处理</small><strong>{item.title}</strong></div>
      </header>
      <p>{item.question}</p>
      <em>{item.reason}</em>
      <footer>
        {href && <button type="button" onClick={() => { location.hash = href; }}>打开处理页</button>}
        {(item.options || []).map((option) => (
          <button
            type="button"
            key={option.value}
            className={option.value === item.recommended_option ? "primary" : ""}
            disabled={busy}
            onClick={() => onRespond(item.approval_uuid, option.value)}
          >
            {option.label}
          </button>
        ))}
      </footer>
    </article>
  );
}

function ArtifactCard({ artifact }) {
  const open = () => {
    if (artifact.open_href) location.hash = artifact.open_href;
  };
  return (
    <article className="employee-artifact-card">
      <span><FileText /></span>
      <div>
        <small>{artifact.artifact_type}</small>
        <strong>{artifact.title}</strong>
        <p>{artifact.summary || "交付物已通过校验并登记。"}</p>
        <code title={artifact.sha256}>SHA256 {artifact.sha256?.slice(0, 12)}</code>
      </div>
      <button type="button" disabled={!artifact.open_href} onClick={open}>
        打开 <ArrowRight />
      </button>
    </article>
  );
}

function DelegateModal({ skills, onClose, onCreated }) {
  const [objective, setObjective] = useState("");
  const [skillId, setSkillId] = useState("");
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const prepare = async () => {
    if (objective.trim().length < 3) return;
    setBusy(true);
    setError("");
    try {
      setDraft(await api("/api/v1/agent-runtime/goals:draft", {
        method: "POST",
        body: JSON.stringify({
          objective,
          preferred_skill_id: skillId || null,
          constraints: {},
          resource_refs: [],
          return_href: null,
        }),
      }));
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const goal = await api("/api/v1/agent-runtime/goals", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          objective: draft.objective,
          selected_skill_id: draft.selected_skill_id,
          expected_deliverables: draft.expected_deliverables,
          constraints: draft.constraints,
          acceptance_criteria: draft.acceptance_criteria,
          autonomy_envelope: draft.autonomy_envelope,
          priority: "normal",
          trigger_type: "user_delegate",
          source_mode: "employee",
          return_href: draft.return_href,
        }),
      });
      await api(`/api/v1/agent-runtime/goals/${goal.goal_uuid}/start`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      onCreated(goal.goal_uuid);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop employee-modal-backdrop" onClick={onClose}>
      <section className="employee-delegate-modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <div><small>DELEGATE A GOAL</small><h2>{draft ? "确认执行计划" : "委派新目标"}</h2></div>
          <button type="button" onClick={onClose} aria-label="关闭"><X /></button>
        </header>
        {!draft ? (
          <>
            <label className="employee-objective">
              <span>目标</span>
              <textarea
                autoFocus
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                placeholder="例如：检查最新销量数据是否已满足训练条件"
              />
            </label>
            <fieldset className="employee-skill-picker">
              <legend>指定技能（可选）</legend>
              {skills.map((skill) => {
                const Icon = SKILL_ICON[skill.skill_id] || Wrench;
                return (
                  <button
                    type="button"
                    key={skill.skill_id}
                    className={skillId === skill.skill_id ? "selected" : ""}
                    onClick={() => setSkillId((value) => value === skill.skill_id ? "" : skill.skill_id)}
                  >
                    <Icon />
                    <span><strong>{skill.display_name}</strong><small>{skill.step_count} 步</small></span>
                    {skillId === skill.skill_id && <Check />}
                  </button>
                );
              })}
            </fieldset>
          </>
        ) : (
          <div className="employee-draft">
            <section>
              <small>采用技能</small>
              <strong>{draft.skill_name}</strong>
              <span>{draft.estimated_steps} 个可审计步骤</span>
            </section>
            <section>
              <small>交付验收</small>
              <ul>{draft.acceptance_criteria.map((item) => <li key={item}><Check />{item}</li>)}</ul>
            </section>
            <section>
              <small>已解析资源</small>
              {draft.resolved_resources.length ? draft.resolved_resources.map((item) => (
                <button type="button" key={`${item.type}-${item.id}`} onClick={() => { location.hash = item.href; }}>
                  <Database /><span><strong>{item.label}</strong><small>{item.status}</small></span><ArrowRight />
                </button>
              )) : <p>尚未绑定业务资源，执行时会进入待处理状态。</p>}
            </section>
            {draft.validation_issues.length > 0 && (
              <section className="employee-draft-warnings">
                <small>前置检查</small>
                {draft.validation_issues.map((item) => (
                  <p key={item.code}><WarningCircle />{item.message}</p>
                ))}
              </section>
            )}
          </div>
        )}
        {error && <div className="employee-error" role="alert"><WarningCircle />{error}</div>}
        <footer>
          {draft && <button type="button" onClick={() => setDraft(null)}>上一步</button>}
          <span />
          <button type="button" onClick={onClose}>取消</button>
          <button
            type="button"
            className="primary"
            disabled={busy || (!draft && objective.trim().length < 3)}
            onClick={draft ? confirm : prepare}
          >
            {busy && <CircleNotch className="spinning" />}
            {draft ? "确认并启动" : "生成计划"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function DashboardView({ data, loading, error, onDelegate, onOpen, onView, onRespond, busyApproval }) {
  const commitments = data?.commitments || [];
  const approvals = data?.approvals || [];
  const artifacts = data?.artifacts || [];
  const activities = data?.activities || [];
  return (
    <div className="employee-dashboard">
      <header className="employee-page-heading">
        <div>
          <small>AI EMPLOYEE WORKBENCH</small>
          <h1>{data?.profile?.display_name || "经营分析员工"}</h1>
          <p>{data?.profile?.role_title || "跨境家具经营分析"}</p>
        </div>
        <button type="button" className="employee-primary" onClick={onDelegate}><Target />委派目标</button>
      </header>
      {error && <div className="employee-error" role="alert"><WarningCircle />{error}</div>}
      <section className="employee-kpis" aria-label="员工状态">
        {[
          [Target, "进行中目标", data?.counts?.active ?? "—", "active"],
          [WarningCircle, "待我处理", data?.counts?.waiting_human ?? "—", "warning"],
          [CheckCircle, "已完成", data?.counts?.completed ?? "—", "success"],
          [Package, "已交付", data?.counts?.artifacts ?? "—", "neutral"],
        ].map(([Icon, label, value, tone]) => (
          <button type="button" key={label} className={tone} onClick={() => onView(label === "待我处理" ? "approvals" : label === "已交付" ? "artifacts" : "goals")}>
            <Icon /><span><small>{label}</small><strong>{loading ? "—" : value}</strong></span>
          </button>
        ))}
      </section>
      <div className="employee-dashboard-grid">
        <section className="employee-panel employee-commitments">
          <header><div><small>COMMITMENTS</small><h2>当前承诺</h2></div><button type="button" onClick={() => onView("goals")}>全部目标 <ArrowRight /></button></header>
          {commitments.length ? commitments.slice(0, 6).map((goal) => <GoalRow key={goal.goal_uuid} goal={goal} onOpen={onOpen} />) : (
            <EmptyState title="还没有已委派目标" action={onDelegate} actionLabel="委派第一个目标" />
          )}
        </section>
        <aside className="employee-side-stack">
          <section className="employee-panel employee-attention">
            <header><div><small>ATTENTION</small><h2>待我处理</h2></div>{approvals.length > 0 && <b>{approvals.length}</b>}</header>
            {approvals.length ? approvals.slice(0, 2).map((item) => (
              <ApprovalCard key={item.approval_uuid} item={item} onRespond={onRespond} busy={busyApproval} />
            )) : <EmptyState Icon={ShieldCheck} title="当前没有待处理事项" />}
          </section>
          <section className="employee-panel employee-activity">
            <header><div><small>ACTIVITY</small><h2>最近动态</h2></div></header>
            {activities.length ? activities.slice(-6).reverse().map((item) => (
              <button type="button" key={item.event_uuid} onClick={() => onOpen(item.goal_uuid)}>
                <i className={item.event_type.includes("failed") || item.event_type.includes("approval") ? "warn" : ""} />
                <span><strong>{EVENT_LABELS[item.event_type] || item.event_type}</strong><small>{item.payload?.title || formatTime(item.created_at)}</small></span>
              </button>
            )) : <EmptyState Icon={Clock} title="暂无执行动态" />}
          </section>
        </aside>
      </div>
      {artifacts.length > 0 && (
        <section className="employee-panel employee-deliveries">
          <header><div><small>DELIVERABLES</small><h2>最近交付</h2></div><button type="button" onClick={() => onView("artifacts")}>全部交付物 <ArrowRight /></button></header>
          <div>{artifacts.slice(0, 3).map((item) => <ArtifactCard key={item.artifact_uuid} artifact={item} />)}</div>
        </section>
      )}
    </div>
  );
}

function CollectionView({ view, data, skills, capabilities, onDelegate, onOpen, onRespond, busyApproval }) {
  const title = {
    goals: ["目标", "持续跟踪已委派工作的执行状态"],
    approvals: ["待我处理", "需要业务判断或前置数据补充的事项"],
    artifacts: ["交付物", "已完成并登记校验摘要的经营产出"],
    capabilities: ["能力目录", "当前员工可调用的受控工具与领域技能"],
  }[view];
  return (
    <div className="employee-collection">
      <header className="employee-page-heading">
        <div><small>AI EMPLOYEE</small><h1>{title[0]}</h1><p>{title[1]}</p></div>
        {view === "goals" && <button type="button" className="employee-primary" onClick={onDelegate}><Target />委派目标</button>}
      </header>
      {view === "goals" && (
        <section className="employee-panel employee-collection-list">
          {(data?.commitments || []).length ? data.commitments.map((goal) => <GoalRow key={goal.goal_uuid} goal={goal} onOpen={onOpen} />) : <EmptyState title="还没有目标" action={onDelegate} actionLabel="委派目标" />}
        </section>
      )}
      {view === "approvals" && (
        <section className="employee-approval-grid">
          {(data?.approvals || []).length ? data.approvals.map((item) => <ApprovalCard key={item.approval_uuid} item={item} onRespond={onRespond} busy={busyApproval} />) : <EmptyState Icon={ShieldCheck} title="当前没有待处理事项" />}
        </section>
      )}
      {view === "artifacts" && (
        <section className="employee-artifact-grid">
          {(data?.artifacts || []).length ? data.artifacts.map((item) => <ArtifactCard key={item.artifact_uuid} artifact={item} />) : <EmptyState Icon={Package} title="暂无交付物" />}
        </section>
      )}
      {view === "capabilities" && (
        <div className="employee-capability-layout">
          <section className="employee-panel">
            <header><div><small>SKILLS</small><h2>已安装技能</h2></div></header>
            <div className="employee-skill-list">{skills.map((skill) => {
              const Icon = SKILL_ICON[skill.skill_id] || Wrench;
              return <article key={skill.skill_id}><span><Icon /></span><div><strong>{skill.display_name}</strong><p>{skill.description}</p><small>v{skill.version} · {skill.step_count} 步</small></div><b>启用</b></article>;
            })}</div>
          </section>
          <section className="employee-panel">
            <header><div><small>TOOLS</small><h2>受控工具</h2></div></header>
            <div className="employee-tool-list">{capabilities.map((item) => (
              <article key={item.capability_id}><span className={`risk-${item.effect_level}`}>{item.effect_level}</span><div><strong>{item.display_name}</strong><p>{item.description}</p><small>{item.provider}</small></div></article>
            ))}</div>
          </section>
        </div>
      )}
    </div>
  );
}

function GoalDetail({ goalUuid, onBack, onRefresh, onRespond, busyApproval }) {
  const [goal, setGoal] = useState(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("execution");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = (silent = false) => {
    if (!silent) setError("");
    api(`/api/v1/agent-runtime/goals/${goalUuid}`)
      .then(setGoal)
      .catch((nextError) => setError(nextError.message));
  };
  useEffect(() => {
    load();
    const poller = window.setInterval(() => load(true), 3500);
    return () => window.clearInterval(poller);
  }, [goalUuid]);

  const control = async (action) => {
    if (!goal?.run?.run_uuid) return;
    setBusy(true);
    try {
      await api(`/api/v1/agent-runtime/runs/${goal.run.run_uuid}/${action}`, {
        method: "POST",
        body: JSON.stringify({ reason: null }),
      });
      load();
      onRefresh();
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };
  const send = async () => {
    if (!message.trim()) return;
    setBusy(true);
    try {
      await api(`/api/v1/agent-runtime/goals/${goalUuid}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: message }),
      });
      setMessage("");
      load();
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusy(false);
    }
  };

  if (!goal) {
    return <div className="employee-detail-loading">{error ? <><WarningCircle /><strong>{error}</strong><button onClick={() => load()}>重试</button></> : <><CircleNotch className="spinning" /><span>正在读取目标…</span></>}</div>;
  }
  const terminal = ["succeeded", "partial_succeeded", "failed", "cancelled"].includes(goal.status);
  return (
    <div className="employee-goal-detail">
      <header className="employee-detail-header">
        <button type="button" onClick={onBack}><ArrowLeft />返回</button>
        <div><small>{goal.skill_name || goal.selected_skill_id}</small><h1>{goal.objective}</h1><span><StatusBadge status={goal.status} /> 更新于 {formatTime(goal.updated_at)}</span></div>
        <div className="employee-run-actions">
          {goal.status === "paused" && <button type="button" disabled={busy} onClick={() => control("resume")}><Play />继续</button>}
          {!terminal && goal.status !== "paused" && <button type="button" disabled={busy || goal.status === "waiting_human"} onClick={() => control("pause")}><Pause />暂停</button>}
          {!terminal && <button type="button" className="danger" disabled={busy} onClick={() => control("cancel")}><X />取消</button>}
        </div>
      </header>
      {error && <div className="employee-error"><WarningCircle />{error}</div>}
      <div className="employee-detail-grid">
        <main className="employee-execution">
          <section className="employee-progress-panel">
            <header><div><small>EXECUTION</small><h2>执行计划</h2></div><strong>{Math.round(goal.progress_percent)}%</strong></header>
            <div className="employee-progress-track"><i style={{ width: `${goal.progress_percent}%` }} /></div>
            <div className="employee-plan-steps">
              {(goal.plan?.steps || []).map((step) => (
                <article key={step.step_uuid} className={`is-${step.status}`}>
                  <span>{step.status === "succeeded" ? <Check /> : step.ordinal}</span>
                  <div><strong>{step.title}</strong><small>{step.capability_id} · {step.effect_level}</small></div>
                  <StatusBadge status={step.status} />
                </article>
              ))}
            </div>
          </section>
          {(goal.approvals || []).filter((item) => item.status === "pending").map((item) => (
            <ApprovalCard key={item.approval_uuid} item={item} onRespond={async (...args) => { await onRespond(...args); load(); }} busy={busyApproval} />
          ))}
          <section className="employee-detail-tabs">
            <header>
              {[["execution", "时间线"], ["artifacts", "交付物"], ["audit", "执行校验"]].map(([key, label]) => (
                <button type="button" key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
              ))}
            </header>
            {tab === "execution" && <div className="employee-timeline">{goal.timeline.length ? [...goal.timeline].reverse().map((item) => (
              <article key={item.event_uuid}><i /><div><strong>{EVENT_LABELS[item.event_type] || item.event_type}</strong><small>{item.payload?.title || item.payload?.message || "状态已写入审计时间线"}</small></div><time>{formatTime(item.created_at)}</time></article>
            )) : <EmptyState Icon={Clock} title="暂无执行事件" />}</div>}
            {tab === "artifacts" && <div className="employee-artifact-grid">{goal.artifacts.length ? goal.artifacts.map((item) => <ArtifactCard key={item.artifact_uuid} artifact={item} />) : <EmptyState Icon={Package} title="目标尚未产生交付物" />}</div>}
            {tab === "audit" && <div className="employee-audit">
              <dl>
                <dt>计划 SHA256</dt><dd><code>{goal.plan?.plan_sha256 || "—"}</code></dd>
                <dt>技能版本</dt><dd>{goal.plan ? `${goal.plan.skill_id}@${goal.plan.skill_version}` : "—"}</dd>
                <dt>工具调用</dt><dd>{goal.run?.tool_call_count ?? 0}</dd>
                <dt>重规划</dt><dd>{goal.run?.replan_count ?? 0}</dd>
                <dt>最高影响等级</dt><dd>{goal.plan?.risk_summary?.highest_effect || "—"}</dd>
                <dt>人工确认</dt><dd>{goal.plan?.risk_summary?.requires_approval ? "需要" : "按前置条件触发"}</dd>
              </dl>
            </div>}
          </section>
        </main>
        <aside className="employee-goal-chat">
          <header><Robot /><div><strong>目标对话</strong><small>补充要求写入当前目标</small></div></header>
          <div className="employee-message-feed">
            {(goal.messages || []).map((item) => (
              <article key={item.message_uuid} className={item.role}>
                <small>{item.role === "user" ? "你" : "AI 员工"} · {formatTime(item.created_at)}</small>
                <p>{item.content}</p>
              </article>
            ))}
          </div>
          <div className="employee-message-input">
            <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="补充限制条件或交付要求" />
            <button type="button" disabled={busy || !message.trim()} onClick={send} aria-label="发送"><PaperPlaneTilt /></button>
          </div>
        </aside>
      </div>
    </div>
  );
}

export function AIEmployeeWorkbench({ Topbar }) {
  const initial = useMemo(routeState, []);
  const [view, setView] = useState(initial.view);
  const [goalUuid, setGoalUuid] = useState(initial.goal);
  const [overview, setOverview] = useState(null);
  const [skills, setSkills] = useState([]);
  const [capabilities, setCapabilities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [delegateOpen, setDelegateOpen] = useState(false);
  const [busyApproval, setBusyApproval] = useState(false);

  const load = (silent = false) => {
    if (!silent) setLoading(true);
    Promise.all([
      api("/api/v1/agent-runtime/overview"),
      api("/api/v1/agent-runtime/skills"),
      api("/api/v1/agent-runtime/capabilities"),
    ]).then(([nextOverview, nextSkills, nextCapabilities]) => {
      setOverview(nextOverview);
      setSkills(nextSkills);
      setCapabilities(nextCapabilities);
      setError("");
    }).catch((nextError) => setError(nextError.message))
      .finally(() => { if (!silent) setLoading(false); });
  };
  useEffect(() => {
    load();
    const poller = window.setInterval(() => load(true), 7000);
    const syncRoute = () => {
      const next = routeState();
      setGoalUuid(next.goal);
      setView(next.view);
    };
    addEventListener("hashchange", syncRoute);
    return () => {
      window.clearInterval(poller);
      removeEventListener("hashchange", syncRoute);
    };
  }, []);

  const navigateView = (next) => {
    location.hash = `employee?view=${next}`;
    setView(next);
    setGoalUuid(null);
  };
  const openGoal = (uuid) => {
    location.hash = `employee?goal=${uuid}`;
    setGoalUuid(uuid);
  };
  const respond = async (uuid, selectedOption) => {
    setBusyApproval(true);
    try {
      await api(`/api/v1/agent-runtime/approvals/${uuid}:respond`, {
        method: "POST",
        body: JSON.stringify({ selected_option: selectedOption, user_input: null }),
      });
      load(true);
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setBusyApproval(false);
    }
  };

  return (
    <main className="employee-page">
      <EmployeeSidebar view={goalUuid ? "goals" : view} onView={navigateView} counts={overview?.counts} />
      <section className="employee-main">
        <Topbar />
        <div className="employee-content">
          {goalUuid ? (
            <GoalDetail
              goalUuid={goalUuid}
              onBack={() => navigateView("goals")}
              onRefresh={() => load(true)}
              onRespond={respond}
              busyApproval={busyApproval}
            />
          ) : view === "home" ? (
            <DashboardView
              data={overview}
              loading={loading}
              error={error}
              onDelegate={() => setDelegateOpen(true)}
              onOpen={openGoal}
              onView={navigateView}
              onRespond={respond}
              busyApproval={busyApproval}
            />
          ) : (
            <CollectionView
              view={view}
              data={overview}
              skills={skills}
              capabilities={capabilities}
              onDelegate={() => setDelegateOpen(true)}
              onOpen={openGoal}
              onRespond={respond}
              busyApproval={busyApproval}
            />
          )}
        </div>
      </section>
      {delegateOpen && (
        <DelegateModal
          skills={skills}
          onClose={() => setDelegateOpen(false)}
          onCreated={(uuid) => {
            setDelegateOpen(false);
            load(true);
            openGoal(uuid);
          }}
        />
      )}
    </main>
  );
}
