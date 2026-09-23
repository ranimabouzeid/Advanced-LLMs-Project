import { robotColorVar } from '../lib/derive';
import robotIcon from '../assets/robot-icon.png';

export default function RobotPanel({ state, detailed = false }) {
  const robots = state.warehouse.robots;
  return <section className="panel" aria-labelledby="robot-panel-title">
    <h2 id="robot-panel-title">Robot Status <span className="count">{robots.length}</span></h2>
    <div className={detailed ? 'robot-detail-grid' : 'robot-cards'}>
      {robots.map((r, index) => {
        const schedule = state.robot_schedules?.find(item => item.robot_id === r.id);
        const target = schedule?.parking.plan.route.at(-1);
        const chosen = state.selected_robot_id === r.id;
        return <article key={r.id} className={`robot-card ${chosen ? 'chosen' : ''}`}
          style={{ '--robot-color': robotColorVar(r.id, index) }}>
          <span className="robot-avatar" aria-hidden="true">
            <img src={robotIcon} alt="" />
            <span className="robot-number">{r.id.replace(/\D/g, '')}</span>
          </span>
          <div className="robot-card-body">
            <div className="spread"><strong>{r.id}</strong><span className="pill">{r.status}</span></div>
            <p>Position ({r.position.x}, {r.position.y}){chosen && ' - Selected'}</p>
            <label>Battery {r.battery}%<meter min="0" max="100" value={r.battery} aria-label={`${r.id} battery`} /></label>
            <p className="muted">Carrying: {r.carried_package_id || 'None'}</p>
            {detailed && schedule && <>
              <p>Assigned orders: {schedule.order_ids.join(' → ') || 'None'}</p>
              <p>Final parking: ({target.x}, {target.y}) — {schedule.parking.status}</p>
              <p className="muted">Projected battery after parking: {schedule.projected_robot.battery}%</p>
              {schedule.parking.reason && <p className="error-text">{schedule.parking.reason}</p>}
            </>}
          </div>
        </article>;
      })}
    </div>
  </section>;
}
