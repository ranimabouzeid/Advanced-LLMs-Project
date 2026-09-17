export default function OrdersPanel({ orders }) {
  return <section className="panel"><h2>Orders <span className="count">{orders.length}</span></h2>
    {!orders.length ? <p className="empty">No orders yet. Create an order to begin.</p> : <div className="table-scroll"><table>
      <thead><tr>{['Order', 'Package', 'Pickup', 'Drop-off', 'Status'].map(label => <th key={label}>{label}</th>)}</tr></thead>
      <tbody>{orders.map(o => <tr key={o.id}><td>{o.id}</td><td>{o.package.id}</td><td>({o.package.pickup.x}, {o.package.pickup.y})</td><td>({o.dropoff.x}, {o.dropoff.y})</td><td><span className="pill">{o.status}</span></td></tr>)}</tbody>
    </table></div>}
  </section>;
}
