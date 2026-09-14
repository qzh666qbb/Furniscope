import { CaretLeft, LockKey } from "@phosphor-icons/react";
import { LiveAdminControlCenter } from "./AdminLive.jsx";

export function UserDiagnosisDetail() {
  return <div className="diagnosis-detail-page">
    <header><button onClick={() => { location.hash = "admin"; }}><CaretLeft/>返回管理控制中心</button></header>
    <main><section className="diagnosis-detail-hero"><div>
      <h1>任务诊断尚未向普通用户开放</h1>
      <p>为避免展示模拟运行记录，该入口已暂时关闭。管理员可在管理控制中心查看真实、脱敏的任务诊断。</p>
    </div></section></main>
  </div>;
}

export function UserToolsView() {
  return <div className="user-tools">
    <header className="user-tools-heading"><div>
      <h1>AI 配置中心</h1>
      <p>模型路由和提示词属于平台运行配置，不属于租户业务权限。</p>
    </div></header>
    <section><div className="admin-empty"><LockKey/><strong>当前入口暂不开放</strong>
      <p>租户管理员可在管理中心维护用户、授权数据源与 SKU；平台模型配置由内部控制面管理。</p>
    </div></section>
  </div>;
}

export const AdminControlCenter = LiveAdminControlCenter;
