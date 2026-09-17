export default function AgentActivity({ activity }) {
  return <section className="panel"><h2>Agent activity</h2><p className="muted">Completed backend records, in returned order.</p>
    {!activity.length ? <p className="empty">No activity recorded.</p> : <ol className="activity">{activity.map((record, index) =>
      <li key={index}><div className="spread"><strong>{record.node[0].toUpperCase() + record.node.slice(1)}</strong><span className={`activity-status ${record.status}`}>{record.status}</span></div>{record.message && <p>{record.message}</p>}</li>)}</ol>}
  </section>;
}
