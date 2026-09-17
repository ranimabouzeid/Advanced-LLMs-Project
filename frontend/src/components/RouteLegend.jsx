export default function RouteLegend() {
  return <div className="legend" aria-label="Warehouse legend">
    <span><i className="solid-line" />Pickup route</span><span><i className="dashed-line" />Delivery route</span>
    <span><i className="parking-line" />Final parking route</span><span>P Staging cell</span>
    <span>&#9638; Shelf</span><span>&times; Blocked</span><span>&#9671; Package</span><span>&darr; Drop-off</span><span>R Robot</span>
  </div>;
}
