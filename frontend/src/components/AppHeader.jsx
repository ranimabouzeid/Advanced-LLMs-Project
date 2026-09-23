import { IconCube, IconDashboard, IconOrders, IconRobot, IconState } from './icons';

export const TABS = [
  { key: 'dashboard', label: 'Dashboard', Icon: IconDashboard },
  { key: 'orders', label: 'Orders', Icon: IconOrders },
  { key: 'robots', label: 'Robots', Icon: IconRobot },
  { key: 'state', label: 'Live State', Icon: IconState },
];

export default function AppHeader({ activeTab, onTabChange, status, session, busy, onRefresh }) {
  return <header className="topbar">
    <div className="brand">
      <span className="brand-mark"><IconCube /></span>
      <div>
        <h1>SWARM<span>DOCK</span></h1>
        <small>AUTONOMOUS WAREHOUSE LAB</small>
      </div>
    </div>
    <nav className="tabs" role="tablist" aria-label="Dashboard sections">
      {TABS.map(({ key, label, Icon }) => <button key={key} type="button" role="tab" id={`tab-${key}`}
        aria-selected={activeTab === key} aria-controls={`panel-${key}`} onClick={() => onTabChange(key)}>
        <Icon /> {label}
      </button>)}
    </nav>
    <div className="status-cluster">
      <span className={`status-indicator ${status.state}`}><span className="status-dot" />
        {status.label}{status.state === 'running' && typeof status.activeRobots === 'number' &&
          ` · ${status.activeRobots} robot${status.activeRobots === 1 ? '' : 's'} active`}</span>
      <div className="session-meta">
        <span className="pill">{session ? `Revision ${session.state.warehouse.revision}` : 'Connecting'}</span>
        <small>Session {session?.session_id || 'not created'}</small>
        {session && <button disabled={busy} onClick={onRefresh}>Refresh state</button>}
      </div>
    </div>
  </header>;
}
