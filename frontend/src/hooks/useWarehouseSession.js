import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

function proposalRevision(state) {
  const parking = state.robot_schedules?.find(s => ['approved', 'stale'].includes(s.parking.status));
  return state.delivery_plan || parking
    ? state.batch_revision ?? state.delivery_plan?.warehouse_revision ?? parking.parking.plan.warehouse_revision
    : null;
}

export function useWarehouseSession() {
  const [session, setSession] = useState(null);
  const [pendingAction, setPending] = useState('createSession');
  const [lastCommand, setCommand] = useState(null);
  const [httpError, setError] = useState(null);
  const [replacementNotice, setReplacement] = useState(null);
  const [selectedCell, setSelectedCell] = useState(null);
  const [executeReplay, setExecuteReplay] = useState(null);
  const startup = useRef(null);
  const current = useRef(null);
  const active = useRef(true);
  const lock = useRef(true);
  const sequence = useRef(0);

  useEffect(() => {
    active.current = true;
    let subscribed = true;
    // The same promise survives Strict Mode's effect cleanup/setup cycle.
    startup.current ??= api.createSession();
    startup.current.then(result => {
      if (subscribed) { current.current = result; setSession(result); }
    }).catch(error => { if (subscribed) setError(error); })
      .finally(() => { if (subscribed) { lock.current = false; setPending(null); } });
    return () => { subscribed = false; active.current = false; };
  }, []);

  async function run(action, payload) {
    if (lock.current || !active.current) return false;
    const oldId = current.current?.session_id;
    if (action !== 'createSession' && !oldId) return false;
    lock.current = true;
    const ticket = ++sequence.current;
    const preExecuteState = action === 'execute' ? current.current?.state : null;
    setPending(action);
    setError(null);
    try {
      const result = await (action === 'createSession' ? api.createSession() : api[action](oldId, payload));
      if (!active.current || sequence.current !== ticket || current.current?.session_id !== oldId) return false;
      if (!['reset', 'createSession'].includes(action) && result.session_id !== oldId) {
        throw new Error('Unexpected session response. Refresh committed state.');
      }
      current.current = result;
      setSession(result);
      if (action === 'plan' || action === 'execute') {
        setCommand({ action, outcome: result.outcome, error: result.error });
        setReplacement(action === 'execute' && result.outcome === 'ready' &&
          result.state.execution_requested === false ? proposalRevision(result.state) : null);
        // Visual-only replay trigger: only when Execute actually delivered
        // something committed, never for a replan/replacement proposal.
        setExecuteReplay(action === 'execute' && preExecuteState &&
          ['delivered', 'partial'].includes(result.state.run_outcome)
          ? { before: preExecuteState, after: result.state, at: Date.now() } : null);
      } else if (action !== 'refresh') {
        setCommand(null); setReplacement(null);
      } else if (proposalRevision(result.state) !== replacementNotice) {
        setReplacement(null);
      }
      if (action === 'reset' || action === 'createSession') { setSelectedCell(null); setExecuteReplay(null); }
      return true;
    } catch (error) {
      if (active.current && sequence.current === ticket) setError(error);
      return false;
    } finally {
      if (active.current && sequence.current === ticket) { lock.current = false; setPending(null); }
    }
  }
  return { session, pendingAction, lastCommand, httpError, replacementNotice,
    selectedCell, setSelectedCell, executeReplay, run };
}
