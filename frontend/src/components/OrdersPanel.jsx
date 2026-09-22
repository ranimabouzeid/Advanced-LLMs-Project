import { useElapsed } from '../hooks/useElapsed';
import { IconPlus } from './icons';

export default function OrdersPanel({ orders, plannedDeliveries = [], onNewOrder, detailed = false }) {
  const assignments = new Map(plannedDeliveries.map(item => [item.order_id, item]));
  const elapsedSeconds = useElapsed(orders.map(o => o.id));

  if (detailed) {
    return <section className="panel" aria-labelledby="orders-panel-title">
      <div className="panel-heading"><h2 id="orders-panel-title">Orders <span className="count">{orders.length}</span></h2>
        {onNewOrder && <button type="button" className="primary" onClick={onNewOrder}><IconPlus /> New Order</button>}</div>
      {!orders.length ? <p className="empty">No orders yet. Create an order to begin.</p> : <div className="table-scroll"><table>
        <thead><tr>{['Order', 'Package', 'Pickup', 'Drop-off', 'Status', 'Assigned robot', 'Batch result'].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{orders.map(o => {
          const assignment = assignments.get(o.id);
          return <tr key={o.id}><td>{o.id}</td><td>{o.package.id}</td><td>({o.package.pickup.x}, {o.package.pickup.y})</td>
            <td>({o.dropoff.x}, {o.dropoff.y})</td><td><span className={`status-chip ${o.status}`}>{o.status}</span></td>
            <td>{assignment?.robot_id || o.assigned_robot_id || '—'}</td>
            <td>{assignment?.status || 'Not planned'}{assignment?.reason && <small> — {assignment.reason}</small>}</td></tr>;
        })}</tbody>
      </table></div>}
    </section>;
  }

  return <section className="panel" aria-labelledby="order-queue-title">
    <div className="panel-heading"><h2 id="order-queue-title">Order Queue <span className="count">{orders.length}</span></h2>
      {onNewOrder && <button type="button" className="primary" onClick={onNewOrder}><IconPlus /> New Order</button>}</div>
    {!orders.length ? <p className="empty">No orders yet. Create an order to begin.</p> : <ul className="order-queue" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {orders.map(o => {
        const assignment = assignments.get(o.id);
        return <li key={o.id} className="order-row">
          <span className="order-id">#{o.id}</span>
          <span className="order-meta">
            <span className={`status-chip ${o.status}`}>{o.status}</span>
            <div className="order-package">Package {o.package.id} → ({o.dropoff.x}, {o.dropoff.y})
              {(assignment?.robot_id || o.assigned_robot_id) && ` · ${assignment?.robot_id || o.assigned_robot_id}`}</div>
          </span>
          <span className="elapsed">Waiting {elapsedSeconds(o.id)}s</span>
        </li>;
      })}
    </ul>}
  </section>;
}
