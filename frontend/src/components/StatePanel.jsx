export default function StatePanel({ state, lastCommand }) {
  const safety = state.safety;
  return <section className="panel"><h2>State & result</h2><dl className="state-list">
    <dt>Attempted command</dt><dd>{lastCommand ? `${lastCommand.action}: ${lastCommand.outcome}` : 'None'}</dd>
    <dt>Committed outcome</dt><dd>{state.run_outcome}</dd>
    <dt>Revision</dt><dd>{state.warehouse.revision}</dd>
    <dt>Selected order</dt><dd>{state.order_selection?.order_id || 'None'}</dd>
    <dt>Selected robot</dt><dd>{state.selected_robot_id || 'None'}</dd>
    <dt>Planning</dt><dd>{state.planning_outcome}</dd>
    <dt>Safety</dt><dd>{safety === null ? 'Unchecked' : safety.route_valid ? 'Valid' : 'Rejected'}</dd>
    <dt>Collision risk</dt><dd>{safety === null ? 'Unchecked' : safety.collision_risk ? 'Detected' : 'Not detected'}</dd>
  </dl>{state.order_selection && <p className="muted">{state.order_selection.explanation}</p>}
  {state.error_message && <p className="error-text">{state.error_message}</p>}
  {safety?.reasons?.length > 0 && <ul>{safety.reasons.map((r, i) => <li key={i}>{r.code}: {r.message}{r.cell && ` (${r.cell.x}, ${r.cell.y})`}</li>)}</ul>}
  {safety?.conflicts?.length > 0 && <ul>{safety.conflicts.map((r, i) => <li key={i}>{r.robot_id}: conflict at ({r.cell.x}, {r.cell.y})</li>)}</ul>}
  </section>;
}
