export default function RobotPanel({ state }) {
  return <section className="panel"><h2>Fleet status <span className="count">{state.warehouse.robots.length}</span></h2>
    <div className="robot-cards">{state.warehouse.robots.map(r => <article key={r.id} className={`robot-card ${state.selected_robot_id === r.id ? 'chosen' : ''}`}>
      <div className="spread"><strong>{r.id}</strong><span className="pill">{r.status}</span></div>
      <p>Position ({r.position.x}, {r.position.y}){state.selected_robot_id === r.id && ' - Selected'}</p>
      <label>Battery {r.battery}%<meter min="0" max="100" value={r.battery} aria-label={`${r.id} battery`} /></label>
      <p className="muted">Carrying: {r.carried_package_id || 'None'}</p>
    </article>)}</div>
  </section>;
}
