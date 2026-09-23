import { useEffect, useRef, useState } from 'react';
import { IconClose } from './icons';

// Mounted only while open (see App.jsx), so it opens itself on mount and the
// caller unmounts it on close. This keeps its fields out of the DOM - and out
// of query results - whenever the dialog is not showing.
export default function NewOrderDialog({ onClose, state, disabled, run }) {
  const ref = useRef(null);
  const [draft, setDraft] = useState({ order_id: '', package_id: '', x: '2', y: '0', dropoff: '0' });
  const update = (field, value) => setDraft(d => ({ ...d, [field]: value }));

  useEffect(() => { ref.current?.showModal(); }, []);

  return <dialog ref={ref} onClose={onClose} aria-labelledby="new-order-title">
    <div className="dialog-heading">
      <h2 id="new-order-title">Create order</h2>
      <button type="button" className="dialog-close" aria-label="Close" onClick={onClose}><IconClose /></button>
    </div>
    <form onSubmit={async event => { event.preventDefault();
      const ok = await run('createOrder', { order_id: draft.order_id, package_id: draft.package_id,
        pickup: { x: Number(draft.x), y: Number(draft.y) }, dropoff: state.warehouse.dropoff_locations[Number(draft.dropoff)] });
      if (ok) { setDraft(d => ({ ...d, order_id: '', package_id: '' })); onClose(); }
    }}>
      <fieldset disabled={disabled}>
        <label>Order ID<input required value={draft.order_id} onChange={e => update('order_id', e.target.value)} /></label>
        <label>Package ID<input required value={draft.package_id} onChange={e => update('package_id', e.target.value)} /></label>
        <div className="field-pair">{['x', 'y'].map(axis => <label key={axis}>Pickup {axis}
          <input type="number" min="0" max="9" step="1" required value={draft[axis]} onChange={e => update(axis, e.target.value)} /></label>)}</div>
        <label>Drop-off<select value={draft.dropoff} onChange={e => update('dropoff', e.target.value)}>
          {state.warehouse.dropoff_locations.map((p, i) => <option key={`${p.x},${p.y}`} value={i}>({p.x}, {p.y})</option>)}
        </select></label>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary">Create Order</button>
        </div>
      </fieldset>
    </form>
  </dialog>;
}
