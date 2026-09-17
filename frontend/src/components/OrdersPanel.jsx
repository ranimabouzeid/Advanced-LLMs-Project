export default function OrdersPanel({ orders, plannedDeliveries = [] }) {
  const assignments = new Map(plannedDeliveries.map(item => [item.order_id, item]));
  return <section className="panel"><h2>Orders <span className="count">{orders.length}</span></h2>
    {!orders.length ? <p className="empty">No orders yet. Create an order to begin.</p> : <div className="table-scroll"><table>
      <thead><tr>{['Order', 'Package', 'Pickup', 'Drop-off', 'Status', 'Assigned robot', 'Batch result'].map(label => <th key={label}>{label}</th>)}</tr></thead>
      <tbody>{orders.map(o => {
        const assignment = assignments.get(o.id);
        return <tr key={o.id}><td>{o.id}</td><td>{o.package.id}</td><td>({o.package.pickup.x}, {o.package.pickup.y})</td>
          <td>({o.dropoff.x}, {o.dropoff.y})</td><td><span className="pill">{o.status}</span></td>
          <td>{assignment?.robot_id || o.assigned_robot_id || '—'}</td>
          <td>{assignment?.status || 'Not planned'}{assignment?.reason && <small> — {assignment.reason}</small>}</td></tr>;
      })}</tbody>
    </table></div>}
  </section>;
}
