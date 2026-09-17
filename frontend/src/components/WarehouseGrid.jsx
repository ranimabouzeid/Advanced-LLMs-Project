import { useState } from 'react';
import RouteLegend from './RouteLegend';
const key = p => `${p.x},${p.y}`;
export default function WarehouseGrid({ state, selectedCell, onSelect }) {
  const [routeOrder, setRouteOrder] = useState(null);
  const { warehouse: w } = state;
  const routes = (state.planned_deliveries || []).filter(item => item.delivery_plan);
  const selected = routes.find(item => item.order_id === routeOrder) || routes[0];
  const plan = selected?.delivery_plan || state.delivery_plan;
  const shelves = new Set(w.obstacles.map(key)), blocks = new Set(w.blocked_cells.map(key));
  const drops = new Set(w.dropoff_locations.map(key));
  const parking = new Map((w.parking_cells || []).map((cell, index) => [key(cell), `P${index + 1}`]));
  const schedule = state.robot_schedules?.find(item => item.robot_id === (selected?.robot_id || state.selected_robot_id))
    || (!plan ? state.robot_schedules?.[0] : null);
  const departure = schedule && (schedule.order_ids.length === 0 || schedule.order_ids.at(-1) === (selected?.order_id || state.order_selection?.order_id))
    ? schedule.parking.plan : null;
  const stale = plan && (selected ? selected.status === 'stale' || (selected.status === 'approved' && state.batch_revision !== w.revision) : state.planning_outcome === 'stale' || plan.warehouse_revision !== w.revision);
  function leg(route, name) {
    return <g className={`route-${name}`} data-testid={`${name}-route`}>
      <polyline points={route.map(p => `${p.x + .5},${p.y + .5}`).join(' ')} />
      {[route[0], route.at(-1)].map((p, i) => <circle key={i} cx={p.x + .5} cy={p.y + .5} r={name === 'pickup' ? '.16' : '.1'} />)}
    </g>;
  }
  return <section className="panel warehouse-panel" aria-labelledby="warehouse-title">
    <div className="panel-heading"><div><span className="eyebrow">COMMITTED SNAPSHOT</span><h2 id="warehouse-title">Warehouse floor</h2></div><span className="pill">{w.width} x {w.height}</span></div>
    <p className="muted">Select a cell to fill block controls. Origin (0,0) is top left.</p>
    {routes.length > 0 && <label>Delivery route<select aria-label="Delivery route" value={selected.order_id} onChange={event => setRouteOrder(event.target.value)}>
      {routes.map((item, index) => <option key={item.order_id} value={item.order_id}>{index + 1}. {item.order_id} → {item.robot_id} ({item.status})</option>)}
    </select><span className="muted">Routes run sequentially. Later routes start from projected positions.</span></label>}
    {stale && <p className="stale-label">Stale route - not approved for execution. Execute will revalidate.</p>}
    <div className={`floor ${stale ? 'stale' : ''}`} style={{ '--columns': w.width, '--rows': w.height }}>
      {Array.from({ length: w.width * w.height }, (_, index) => {
        const x = index % w.width, y = Math.floor(index / w.width), id = `${x},${y}`;
        const robots = w.robots.filter(r => key(r.position) === id);
        const packages = w.orders.filter(o => ['pending', 'assigned'].includes(o.status) && key(o.package.pickup) === id);
        const labels = [shelves.has(id) && 'shelf', blocks.has(id) && 'blocked', drops.has(id) && 'drop-off',
          parking.has(id) && `parking ${parking.get(id)}`,
          ...robots.map(r => `${r.id}${r.carried_package_id ? ` carrying ${r.carried_package_id}` : ''}`),
          ...packages.map(o => `package ${o.package.id}`)].filter(Boolean);
        return <button type="button" key={id} data-cell={id} aria-label={`Cell (${x}, ${y})${labels.length ? ': ' + labels.join(', ') : ''}`}
          aria-pressed={selectedCell ? key(selectedCell) === id : false} onClick={() => onSelect({ x, y })}
          className={`cell ${shelves.has(id) ? 'shelf' : ''} ${blocks.has(id) ? 'blocked' : ''}`}>
          <span className="coordinate">{x},{y}</span><span className="objects">
            {shelves.has(id) && <span aria-hidden="true">&#9638;</span>}{blocks.has(id) && <span aria-hidden="true">&times;</span>}
            {drops.has(id) && <span className="drop-marker" title="Registered drop-off">&darr;</span>}
            {parking.has(id) && <span className="parking-marker" title="Staging cell">{parking.get(id)}</span>}
            {packages.length > 0 && <span className="package-marker" title={packages.map(o => o.package.id).join(', ')}>&#9671;{packages.length > 1 ? packages.length : ''}</span>}
            {robots.map(r => <span key={r.id} title={r.id} className={`robot-marker ${(selected?.robot_id || state.selected_robot_id) === r.id ? 'selected-robot' : ''}`}>R{r.id.replace('robot-', '')}{r.carried_package_id && <small>&#9671;</small>}</span>)}
          </span>
        </button>;
      })}
      {(plan || departure) && <svg className="routes" viewBox={`0 0 ${w.width} ${w.height}`} aria-label="Proposed delivery routes" role="img">
        {plan && <>{leg(plan.pickup_route, 'pickup')}{leg(plan.delivery_route, 'delivery')}</>}
        {departure && leg(departure.route, 'parking')}
      </svg>}
    </div><RouteLegend />
  </section>;
}
