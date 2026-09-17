import { useEffect, useState } from 'react';
export default function ControlsPanel({ state, selectedCell, busy, unavailable, run }) {
  const [draft, setDraft] = useState({ order_id: '', package_id: '', x: '2', y: '0', dropoff: '0' });
  const [cell, setCell] = useState({ x: '0', y: '0' });
  useEffect(() => { if (selectedCell) setCell({ x: String(selectedCell.x), y: String(selectedCell.y) }); }, [selectedCell]);
  const update = (field, value) => setDraft(d => ({ ...d, [field]: value }));
  const disabled = busy || unavailable;
  return <section className="panel"><h2>Command center</h2>
    <div className="command-buttons"><button disabled={disabled} onClick={() => run('plan')}>Plan</button>
      <button className="primary" disabled={disabled || (!state.delivery_plan && !state.robot_schedules?.some(s => ['approved', 'stale'].includes(s.parking.status)))} onClick={() => run('execute')}>Execute</button>
      <button disabled={disabled} onClick={() => run('reset')}>Reset</button></div>
    <p className="muted">Plan proposes. Execute revalidates. Reset starts a fresh session.</p>
    <form onSubmit={async event => { event.preventDefault();
      const ok = await run('createOrder', { order_id: draft.order_id, package_id: draft.package_id,
        pickup: { x: Number(draft.x), y: Number(draft.y) }, dropoff: state.warehouse.dropoff_locations[Number(draft.dropoff)] });
      if (ok) setDraft(d => ({ ...d, order_id: '', package_id: '' }));
    }}><fieldset disabled={disabled}><legend>Create order</legend>
      <label>Order ID<input required value={draft.order_id} onChange={e => update('order_id', e.target.value)} /></label>
      <label>Package ID<input required value={draft.package_id} onChange={e => update('package_id', e.target.value)} /></label>
      <div className="field-pair">{['x', 'y'].map(axis => <label key={axis}>Pickup {axis}<input type="number" min="0" max="9" step="1" required value={draft[axis]} onChange={e => update(axis, e.target.value)} /></label>)}</div>
      <label>Drop-off<select value={draft.dropoff} onChange={e => update('dropoff', e.target.value)}>{state.warehouse.dropoff_locations.map((p, i) => <option key={`${p.x},${p.y}`} value={i}>({p.x}, {p.y})</option>)}</select></label>
      <button type="submit">Create Order</button>
    </fieldset></form>
    <form onSubmit={event => { event.preventDefault(); run(event.nativeEvent.submitter?.value || 'block', { x: Number(cell.x), y: Number(cell.y) }); }}>
      <fieldset disabled={disabled}><legend>Cell access</legend><div className="field-pair">{['x', 'y'].map(axis => <label key={axis}>Cell {axis}<input type="number" min="0" max="9" step="1" required value={cell[axis]} onChange={e => setCell(c => ({ ...c, [axis]: e.target.value }))} /></label>)}</div>
      <div className="command-buttons"><button type="submit" value="block">Block Cell</button><button type="submit" value="unblock">Unblock Cell</button></div>
    </fieldset></form>
  </section>;
}
