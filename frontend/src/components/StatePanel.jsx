export default function StatePanel({ state, lastCommand }) {
  const safety = state.safety;
  return <section className="panel"><h2>State & result</h2><dl className="state-list">
    <dt>Attempted command</dt><dd>{lastCommand ? `${lastCommand.action}: ${lastCommand.outcome}` : 'None'}</dd>
    <dt>Committed outcome</dt><dd>{state.run_outcome}</dd>
    <dt>Revision</dt><dd>{state.warehouse.revision}</dd>
    <dt>Batch deliveries</dt><dd>{state.planned_deliveries?.length || 0}</dd>
    <dt>Selected order</dt><dd>{state.order_selection?.order_id || 'None'}</dd>
    <dt>Selected robot</dt><dd>{state.selected_robot_id || 'None'}</dd>
    <dt>Planning</dt><dd>{state.planning_outcome}</dd>
    <dt>Safety</dt><dd>{safety == null ? 'Unchecked' : safety.approved ? 'Approved' : 'Rejected'}</dd>
    <dt>Reported conflicts</dt><dd>{safety == null ? 'Unchecked' : safety.conflicts.length}</dd>
  </dl>{state.order_selection && <p className="muted">{state.order_selection.explanation}</p>}
  {state.error_message && <p className="error-text">{state.error_message}</p>}
  {safety && <p className="muted">{safety.explanation}</p>}
  {safety?.conflicts?.length > 0 && <ul>{safety.conflicts.map((r, i) => <li key={i}>{r}</li>)}</ul>}
  </section>;
}
