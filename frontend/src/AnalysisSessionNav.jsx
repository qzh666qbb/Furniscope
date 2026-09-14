import { useEffect, useMemo, useState } from "react";
import {
  Archive, CaretDoubleLeft, CaretDoubleRight, CaretDown, Folder, FolderPlus,
  List, Plus, Pulse, Tray,
} from "@phosphor-icons/react";

const STORAGE_KEY = "furniscope-analysis-library-v1";

const initialLibrary = {
  ordinary: [
    { id: "conv-product-launch", title: "新品销量预测讨论", meta: "今天 10:18", status: "草稿" },
    { id: "conv-price-check", title: "德国站点价格带验证", meta: "昨天", status: "已暂停" },
  ],
  projects: [
    { id: "FS-320", name: "北欧布艺沙发研究", conversations: [
      { id: "conv-fs320-forecast", title: "销量预测与市场偏好", meta: "进行中 · 48%", status: "running" },
      { id: "conv-fs320-competitor", title: "竞品卖点对比", meta: "昨天", status: "done" },
      { id: "conv-fs320-improve", title: "产品优化建议", meta: "2026-08-09", status: "done" },
    ] },
    { id: "FS-876", name: "可扩展餐桌进入决策", conversations: [
      { id: "conv-fs876-market", title: "北欧市场机会", meta: "2026-08-09", status: "done" },
      { id: "conv-fs876-price", title: "价格带验证", meta: "昨天", status: "done" },
    ] },
  ],
};

function loadLibrary() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return stored?.ordinary && stored?.projects ? stored : initialLibrary;
  } catch {
    return initialLibrary;
  }
}

function SessionItem({ session, active, actionLabel, onAction, onOpen }) {
  return <div className={`session-item ${active ? "active" : ""}`}>
    <button className="session-open" onClick={() => onOpen(session)}>
      <Pulse/><span><strong>{session.title}</strong><small>{session.meta}</small></span>
    </button>
    <button className="session-organize" title={actionLabel} aria-label={`${actionLabel}：${session.title}`} onClick={() => onAction(session)}>
      <Archive/>
    </button>
  </div>;
}

export function AnalysisSessionNav({ collapsed, onToggle }) {
  const [library, setLibrary] = useState(loadLibrary);
  const [view, setView] = useState("ordinary");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(() => localStorage.getItem("furniscope-active-conversation") || "conv-fs320-forecast");
  const [closedProjects, setClosedProjects] = useState(new Set());
  const [archiveSession, setArchiveSession] = useState(null);
  const [archiveProject, setArchiveProject] = useState("FS-320");
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectName, setProjectName] = useState("");

  useEffect(() => { localStorage.setItem(STORAGE_KEY, JSON.stringify(library)); }, [library]);

  const normalized = query.trim().toLowerCase();
  const ordinary = useMemo(() => library.ordinary.filter(item => item.title.toLowerCase().includes(normalized)), [library.ordinary, normalized]);
  const projects = useMemo(() => library.projects.map(project => ({
    ...project,
    conversations: project.conversations.filter(item => `${project.id}${project.name}${item.title}`.toLowerCase().includes(normalized)),
  })).filter(project => !normalized || project.conversations.length || `${project.id}${project.name}`.toLowerCase().includes(normalized)), [library.projects, normalized]);

  const openSession = session => {
    setActive(session.id);
    localStorage.setItem("furniscope-active-conversation", session.id);
    window.dispatchEvent(new CustomEvent("open-analysis", { detail: session }));
  };
  const toggleProject = id => setClosedProjects(current => {
    const next = new Set(current);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const createProject = () => {
    const name = projectName.trim();
    if (!name) return;
    const id = `PRJ-${Date.now().toString().slice(-6)}`;
    setLibrary(current => ({ ...current, projects: [...current.projects, { id, name, conversations: [] }] }));
    setArchiveProject(id); setProjectName(""); setCreatingProject(false); setView("projects");
  };
  const archive = () => {
    if (!archiveSession || !archiveProject) return;
    setLibrary(current => ({
      ordinary: current.ordinary.filter(item => item.id !== archiveSession.id),
      projects: current.projects.map(project => project.id === archiveProject
        ? { ...project, conversations: [archiveSession, ...project.conversations] }
        : project),
    }));
    setArchiveSession(null); setView("projects");
  };
  const restore = (session, projectId) => {
    setLibrary(current => ({
      ordinary: [session, ...current.ordinary],
      projects: current.projects.map(project => project.id === projectId
        ? { ...project, conversations: project.conversations.filter(item => item.id !== session.id) }
        : project),
    }));
    setView("ordinary");
  };
  const newConversation = () => {
    setActive(""); setView("ordinary");
    window.dispatchEvent(new Event("new-analysis"));
  };

  return <aside className={`analysis-session-nav ${collapsed ? "collapsed" : ""}`}>
    <header><button onClick={onToggle} title={collapsed ? "展开会话列表" : "折叠会话列表"}>{collapsed ? <CaretDoubleRight/> : <CaretDoubleLeft/>}</button><strong>分析项目</strong></header>
    <button className="session-new" onClick={newConversation}><Plus/><span>新建对话</span></button>
    {!collapsed && <>
      <div className="session-library-tabs" role="tablist">
        <button className={view === "ordinary" ? "active" : ""} onClick={() => setView("ordinary")}><Tray/>普通 <b>{library.ordinary.length}</b></button>
        <button className={view === "projects" ? "active" : ""} onClick={() => setView("projects")}><Folder/>项目归档 <b>{library.projects.length}</b></button>
      </div>
      <label className="session-search"><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索项目或会话…"/></label>
      <div className="session-projects">
        {view === "ordinary" ? <section className="ordinary-sessions">
          <header><span><b>普通会话</b>尚未归入项目</span></header>
          {ordinary.map(session => <SessionItem key={session.id} session={session} active={active === session.id} actionLabel="归档到项目" onAction={setArchiveSession} onOpen={openSession}/>)}
          {!ordinary.length && <div className="session-empty">没有未归档会话</div>}
          {archiveSession && <div className="archive-panel">
            <strong>归档“{archiveSession.title}”</strong>
            <select value={archiveProject} onChange={event => setArchiveProject(event.target.value)}>{library.projects.map(project => <option value={project.id} key={project.id}>{project.id} · {project.name}</option>)}</select>
            <div><button onClick={() => setArchiveSession(null)}>取消</button><button onClick={archive}>确认归档</button></div>
          </div>}
        </section> : <>
          <button className="project-create-trigger" onClick={() => setCreatingProject(value => !value)}><FolderPlus/>新建项目分类</button>
          {creatingProject && <div className="project-create"><input autoFocus value={projectName} onChange={event => setProjectName(event.target.value)} onKeyDown={event => event.key === "Enter" && createProject()} placeholder="输入项目名称"/><button onClick={createProject}>创建</button></div>}
          {projects.map(project => <section className="project-group" key={project.id}>
            <button className="project-header" onClick={() => toggleProject(project.id)}><span><b>{project.id}</b>{project.name}<small>{project.conversations.length} 个会话</small></span><CaretDown className={closedProjects.has(project.id) ? "closed" : ""}/></button>
            {!closedProjects.has(project.id) && project.conversations.map(session => <SessionItem key={session.id} session={session} active={active === session.id} actionLabel="移回普通会话" onAction={() => restore(session, project.id)} onOpen={openSession}/>)}
          </section>)}
          {!projects.length && <div className="session-empty">没有匹配的项目</div>}
        </>}
      </div>
      <footer><button onClick={() => location.hash = "workflow"}><List/>进入工作流画布</button></footer>
    </>}
  </aside>;
}
