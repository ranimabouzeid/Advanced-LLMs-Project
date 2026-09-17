import { useWarehouseSession } from './hooks/useWarehouseSession';
import WarehouseGrid from './components/WarehouseGrid';
import ControlsPanel from './components/ControlsPanel';
import RobotPanel from './components/RobotPanel';
import OrdersPanel from './components/OrdersPanel';
import StatePanel from './components/StatePanel';
import AgentActivity from './components/AgentActivity';
const pendingLabels = { createSession: 'Creating session...', createOrder: 'Creating order...', plan: 'Planning...', execute: 'Executing...', block: 'Blocking cell...', unblock: 'Unblocking cell...', reset: 'Resetting...', refresh: 'Refreshing...' };
export default function App() {
  const model = useWarehouseSession();
  const { session, pendingAction, lastCommand, httpError, replacementNotice, selectedCell, setSelectedCell, run } = model;
  const busy = Boolean(pendingAction);
  const unavailable = httpError?.status === 404;
  const uncertain = httpError?.kind === 'network' || httpError?.kind === 'protocol';
  return <main className="dashboard"><header className="topbar"><div><span className="eyebrow">AUTONOMOUS WAREHOUSE LAB</span><h1>SWARM<span>DOCK</span></h1><p>Plan. Review. Deliver.</p></div>
    <div className="session-meta"><span className="pill">{session ? `Revision ${session.state.warehouse.revision}` : 'Connecting'}</span><small>Session {session?.session_id || 'not created'}</small>
    {session && <button disabled={busy} onClick={() => run('refresh')}>Refresh state</button>}</div></header>
    <div className="request-status" role="status">{busy ? pendingLabels[pendingAction] : 'Ready for your next action'}</div>
    {httpError && <div className="notice error" role="alert"><strong>{unavailable ? 'Session unavailable' : httpError.status === 409 ? 'Session busy' : 'Request could not be completed'}</strong><p>{httpError.message}</p>
      {(unavailable || !session) && <button disabled={busy} onClick={() => run('createSession')}>Create fresh session</button>}
      {uncertain && session && <p>Use Refresh state to reconcile the committed result. No automatic retry was made.</p>}</div>}
    {lastCommand?.outcome === 'failed' && <div className="notice error" role="alert"><strong>Command rejected</strong><p>{lastCommand.error?.message || 'The attempted command failed. The committed state is shown below.'}</p></div>}
    {replacementNotice !== null && <div className="notice replacement" role="status"><strong>Replacement route - review required</strong><p>Warehouse conditions changed. A replacement route was generated and has not been executed. Review it, then press Execute again.</p></div>}
    {session && <><div className="workspace"><div className="main-column"><WarehouseGrid state={session.state} selectedCell={selectedCell} onSelect={setSelectedCell} /><RobotPanel state={session.state} /><OrdersPanel orders={session.state.warehouse.orders} /></div>
      <aside><ControlsPanel key={session.session_id} state={session.state} selectedCell={selectedCell} busy={busy} unavailable={unavailable || uncertain} run={run} />
        <StatePanel state={session.state} lastCommand={lastCommand} /><AgentActivity activity={session.state.node_activity} /></aside></div></>}
    <footer>Sequential simulation | Server-authoritative state | No live motion or agent stream</footer>
  </main>;
}
