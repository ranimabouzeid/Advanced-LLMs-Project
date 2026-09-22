import { deriveAgentStatuses, hasReplanCycle } from '../lib/derive';

export default function AgentFlow({ state, busy }) {
  const agents = deriveAgentStatuses(state);
  const replanned = hasReplanCycle(state);
  return <section className="panel" aria-labelledby="agent-flow-title">
    <div className="panel-heading">
      <div><span className="eyebrow">LANGGRAPH WORKFLOW</span><h2 id="agent-flow-title">Agent activity</h2></div>
      {state.replan_count > 0 && <span className="pill">Replan {state.replan_count}/{state.max_replans}</span>}
    </div>
    <p className="muted">Result of the last Plan/Execute run. There is no live stream between agent steps.</p>
    <div className="agent-flow">
      {agents.map(a => <article key={a.node} className="agent-card" style={{ '--agent-color': `var(--agent-${a.node})` }}>
        <div className="agent-name">{a.label}</div>
        <p>{busy ? 'Working...' : a.message || (a.status === 'pending' ? 'Not yet run' : null)}</p>
        <span className={`activity-status ${a.status}`}>{busy ? 'working' : a.status === 'pending' ? 'not run' : a.status}</span>
        {a.node === 'safety' && replanned && <span className="replan-badge">Rejected, Route retried</span>}
      </article>)}
    </div>
  </section>;
}
