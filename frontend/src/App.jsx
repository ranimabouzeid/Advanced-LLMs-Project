import { useEffect, useState } from 'react';
import { useWarehouseSession } from './hooks/useWarehouseSession';
import { useRouteReplay } from './hooks/useRouteReplay';
import { deriveSystemStatus, buildReplayLegs } from './lib/derive';
import AppHeader from './components/AppHeader';
import WarehouseGrid from './components/WarehouseGrid';
import ControlsPanel from './components/ControlsPanel';
import RobotPanel from './components/RobotPanel';
import OrdersPanel from './components/OrdersPanel';
import StatePanel from './components/StatePanel';
import AgentActivity from './components/AgentActivity';
import AgentFlow from './components/AgentFlow';
import NewOrderDialog from './components/NewOrderDialog';
import FullStateDialog from './components/FullStateDialog';

const pendingLabels = { createSession: 'Creating session...', createOrder: 'Creating order...', plan: 'Planning...', execute: 'Executing...', block: 'Blocking cell...', unblock: 'Unblocking cell...', reset: 'Resetting...', refresh: 'Refreshing...' };

export default function App() {
  const model = useWarehouseSession();
  const { session, pendingAction, lastCommand, httpError, replacementNotice, selectedCell, setSelectedCell, executeReplay, run } = model;
  const busy = Boolean(pendingAction);
  const unavailable = httpError?.status === 404;
  const uncertain = httpError?.kind === 'network' || httpError?.kind === 'protocol';
  const [activeTab, setActiveTab] = useState('dashboard');
  const [newOrderOpen, setNewOrderOpen] = useState(false);
  const [fullStateOpen, setFullStateOpen] = useState(false);
  const replay = useRouteReplay();

  useEffect(() => {
    if (executeReplay) replay.play(buildReplayLegs(executeReplay.before, executeReplay.after));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- replay.play is stable for the hook's lifetime; only react to a new execute result.
  }, [executeReplay]);

  const status = deriveSystemStatus({ pendingAction, httpError, session });

  return <main className="dashboard">
    <AppHeader activeTab={activeTab} onTabChange={setActiveTab} status={status} session={session} busy={busy} onRefresh={() => run('refresh')} />
    <div className="request-status" role="status">{busy ? pendingLabels[pendingAction] : 'Ready for your next action'}</div>
    {httpError && <div className="notice error" role="alert"><strong>{unavailable ? 'Session unavailable' : httpError.status === 409 ? 'Session busy' : 'Request could not be completed'}</strong><p>{httpError.message}</p>
      {(unavailable || !session) && <button disabled={busy} onClick={() => run('createSession')}>Create fresh session</button>}
      {uncertain && session && <p>Use Refresh state to reconcile the committed result. No automatic retry was made.</p>}</div>}
    {lastCommand?.outcome === 'partial' && <div className="notice" role="status"><strong>Batch partially delivered</strong><p>Completed deliveries are saved. Review the per-order results, then Plan the remaining orders.</p></div>}
    {lastCommand?.outcome === 'failed' && <div className="notice error" role="alert"><strong>Command rejected</strong><p>{lastCommand.error?.message || 'The attempted command failed. The committed state is shown below.'}</p></div>}
    {replacementNotice !== null && <div className="notice replacement" role="status"><strong>Replacement route - review required</strong><p>The proposal required replanning. A replacement route was generated and has not been executed. Review it, then press Execute again.</p></div>}

    {session && <>
      {activeTab === 'dashboard' && <div id="panel-dashboard" role="tabpanel" aria-labelledby="tab-dashboard" className="dashboard-grid">
        <WarehouseGrid state={session.state} selectedCell={selectedCell} onSelect={setSelectedCell} replay={replay} />
        <div className="side-stack">
          <RobotPanel state={session.state} />
          <OrdersPanel orders={session.state.warehouse.orders} plannedDeliveries={session.state.planned_deliveries} onNewOrder={() => setNewOrderOpen(true)} />
        </div>
        <div className="row-2">
          <AgentFlow state={session.state} busy={busy} />
          <StatePanel state={session.state} lastCommand={lastCommand} onViewFullState={() => setFullStateOpen(true)} />
          <ControlsPanel key={session.session_id} state={session.state} selectedCell={selectedCell} busy={busy} unavailable={unavailable || uncertain} run={run} />
        </div>
      </div>}

      {activeTab === 'orders' && <div id="panel-orders" role="tabpanel" aria-labelledby="tab-orders" className="tab-panel">
        <OrdersPanel detailed orders={session.state.warehouse.orders} plannedDeliveries={session.state.planned_deliveries} onNewOrder={() => setNewOrderOpen(true)} />
      </div>}

      {activeTab === 'robots' && <div id="panel-robots" role="tabpanel" aria-labelledby="tab-robots" className="tab-panel">
        <RobotPanel state={session.state} detailed />
      </div>}

      {activeTab === 'state' && <div id="panel-state" role="tabpanel" aria-labelledby="tab-state" className="tab-panel">
        <StatePanel state={session.state} lastCommand={lastCommand} />
        <AgentActivity activity={session.state.node_activity} />
      </div>}

      {newOrderOpen && <NewOrderDialog state={session.state} disabled={busy || unavailable || uncertain} run={run} onClose={() => setNewOrderOpen(false)} />}
      {fullStateOpen && <FullStateDialog state={session.state} lastCommand={lastCommand} onClose={() => setFullStateOpen(false)} />}
    </>}
    <footer>Sequential simulation | Server-authoritative state | Route replay is visual only, not a live agent stream</footer>
  </main>;
}
