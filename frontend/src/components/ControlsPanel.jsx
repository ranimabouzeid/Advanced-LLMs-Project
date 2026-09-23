import { useEffect, useState } from 'react';
import { generateRushOrders } from '../lib/derive';

const key = p => `${p.x},${p.y}`;

// Convenience only, not a simulation rule: pick a cell to block with one
// click. Prefers the selected map cell; otherwise picks a cell from the
// middle of the current proposed route so Block Aisle has an obvious effect
// to demonstrate replanning.
function pickAisleCell(state, selectedCell) {
  if (selectedCell) return selectedCell;
  const occupied = new Set([
    ...state.warehouse.obstacles.map(key), ...state.warehouse.blocked_cells.map(key),
    ...state.warehouse.dropoff_locations.map(key), ...state.warehouse.robots.map(r => key(r.position)),
  ]);
  const plans = state.planned_deliveries?.length ? state.planned_deliveries.map(d => d.delivery_plan).filter(Boolean)
    : state.delivery_plan ? [state.delivery_plan] : [];
  for (const plan of plans) {
    const route = plan.delivery_route.length > 2 ? plan.delivery_route : plan.pickup_route;
    const mid = route[Math.floor(route.length / 2)];
    if (mid && !occupied.has(key(mid))) return mid;
  }
  return null;
}

export default function ControlsPanel({ state, selectedCell, busy, unavailable, run }) {
  const [cell, setCell] = useState({ x: '0', y: '0' });
  const [rushing, setRushing] = useState(false);
  useEffect(() => { if (selectedCell) setCell({ x: String(selectedCell.x), y: String(selectedCell.y) }); }, [selectedCell]);
  const disabled = busy || unavailable;
  const aisleCell = pickAisleCell(state, selectedCell);

  async function rushMode() {
    if (disabled || rushing) return;
    setRushing(true);
    try {
      for (const draft of generateRushOrders(state.warehouse, 3)) {
        // eslint-disable-next-line no-await-in-loop -- orders must be created one at a time; the session allows one in-flight command.
        const ok = await run('createOrder', draft);
        if (!ok) break;
      }
    } finally { setRushing(false); }
  }

  return <section className="panel" aria-labelledby="controls-title">
    <h2 id="controls-title">Controls</h2>
    <div className="command-buttons">
      <button className="danger-btn" disabled={disabled || !aisleCell} title={aisleCell ? `Block (${aisleCell.x}, ${aisleCell.y})` : 'Select a cell first'}
        onClick={() => aisleCell && run('block', aisleCell)}>Block Aisle</button>
      <button className="rush-btn" disabled={disabled || rushing} onClick={rushMode}>{rushing ? 'Adding orders...' : 'Rush Mode'}</button>
    </div>
    <p className="muted">Rush Mode adds three orders at once. Block Aisle blocks the selected cell, or a cell on the current route.</p>
    <div className="command-buttons"><button disabled={disabled} onClick={() => run('plan')}>Plan</button>
      <button className="primary" disabled={disabled || (!state.delivery_plan && !state.robot_schedules?.some(s => ['approved', 'stale'].includes(s.parking.status)))} onClick={() => run('execute')}>Execute</button>
      <button disabled={disabled} onClick={() => run('reset')}>Reset</button></div>
    <p className="muted">Plan proposes. Execute revalidates. Reset starts a fresh session.</p>
    <form onSubmit={event => { event.preventDefault(); run(event.nativeEvent.submitter?.value || 'block', { x: Number(cell.x), y: Number(cell.y) }); }}>
      <fieldset disabled={disabled}><legend>Cell access</legend><div className="field-pair">{['x', 'y'].map(axis => <label key={axis}>Cell {axis}<input type="number" min="0" max="9" step="1" required value={cell[axis]} onChange={e => setCell(c => ({ ...c, [axis]: e.target.value }))} /></label>)}</div>
      <div className="command-buttons"><button type="submit" value="block">Block Cell</button><button type="submit" value="unblock">Unblock Cell</button></div>
    </fieldset></form>
  </section>;
}
