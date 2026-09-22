import { ordersCompletedCount } from '../lib/derive';

export default function StatePanel({ state, lastCommand, onViewFullState }) {
  const safety = state.safety;
  const collisionRisk = safety == null ? 'Unchecked' : safety.conflicts.length > 0 ? `Yes (${safety.conflicts.length})` : 'No';
  return <section className="panel" aria-labelledby="live-state-title">
    <h2 id="live-state-title">Live State</h2>
    <dl className="state-list">
      <dt>Current order</dt><dd>{state.order_selection?.order_id || 'None'}</dd>
      <dt>Selected robot</dt><dd>{state.selected_robot_id || 'None'}</dd>
      <dt>Blocked cells</dt><dd>{state.warehouse.blocked_cells.length}</dd>
      <dt>Route valid</dt><dd>{safety == null ? 'Unchecked' : safety.approved ? 'Approved' : 'Rejected'}</dd>
      <dt>Collision risk</dt><dd>{collisionRisk}</dd>
      <dt>Replan count</dt><dd>{state.replan_count}/{state.max_replans}</dd>
      <dt>Orders completed</dt><dd>{ordersCompletedCount(state.warehouse.orders)}</dd>
    </dl>
    {state.order_selection && <p className="muted">{state.order_selection.explanation}</p>}
    {state.error_message && <p className="error-text">{state.error_message}</p>}
    {safety && <p className="muted">{safety.explanation}</p>}
    {safety?.conflicts?.length > 0 && <ul>{safety.conflicts.map((r, i) => <li key={i}>{r}</li>)}</ul>}
    {onViewFullState && <button type="button" className="view-full-state" onClick={onViewFullState}>View Full State</button>}
  </section>;
}
