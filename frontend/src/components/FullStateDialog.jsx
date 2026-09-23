import { useEffect, useRef } from 'react';
import { IconClose } from './icons';

// Mounted only while open (see App.jsx); see NewOrderDialog for why.
export default function FullStateDialog({ onClose, state, lastCommand }) {
  const ref = useRef(null);
  const safety = state.safety;

  useEffect(() => { ref.current?.showModal(); }, []);

  return <dialog ref={ref} onClose={onClose} className="full-state-dialog" aria-labelledby="full-state-title">
    <div className="dialog-heading">
      <h2 id="full-state-title">Full state</h2>
      <button type="button" className="dialog-close" aria-label="Close" onClick={onClose}><IconClose /></button>
    </div>
    <div className="full-state">
      <dl className="state-list">
        <dt>Attempted command</dt><dd>{lastCommand ? `${lastCommand.action}: ${lastCommand.outcome}` : 'None'}</dd>
        <dt>Committed outcome</dt><dd>{state.run_outcome}</dd>
        <dt>Revision</dt><dd>{state.warehouse.revision}</dd>
        <dt>Batch deliveries</dt><dd>{state.planned_deliveries?.length || 0}</dd>
        <dt>Selected order</dt><dd>{state.order_selection?.order_id || 'None'}</dd>
        <dt>Selected robot</dt><dd>{state.selected_robot_id || 'None'}</dd>
        <dt>Planning</dt><dd>{state.planning_outcome}</dd>
        <dt>Safety</dt><dd>{safety == null ? 'Unchecked' : safety.approved ? 'Approved' : 'Rejected'}</dd>
        <dt>Reported conflicts</dt><dd>{safety == null ? 'Unchecked' : safety.conflicts.length}</dd>
      </dl>
      {state.order_selection && <p className="muted">{state.order_selection.explanation}</p>}
      {state.error_message && <p className="error-text">{state.error_message}</p>}
      {safety && <p className="muted">{safety.explanation}</p>}
      {safety?.conflicts?.length > 0 && <ul>{safety.conflicts.map((r, i) => <li key={i}>{r}</li>)}</ul>}
      <p className="muted" style={{ marginTop: 14 }}>Raw state (as returned by the server):</p>
      <pre>{JSON.stringify(state, null, 2)}</pre>
    </div>
  </dialog>;
}
