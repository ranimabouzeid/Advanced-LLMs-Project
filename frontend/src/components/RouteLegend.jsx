export default function RouteLegend() {
  return <div className="legend-column" aria-label="Warehouse legend">
    <span className="legend-item"><i className="solid-line" />Pickup route</span>
    <span className="legend-item"><i className="dashed-line" />Delivery route</span>
    <span className="legend-item"><i className="parking-line" />Final parking route</span>
    <span className="legend-item">P Staging cell</span>
    <span className="legend-item">&#9638; Shelf</span>
    <span className="legend-item">&times; Blocked</span>
    <span className="legend-item">&#9671; Package</span>
    <span className="legend-item">&darr; Drop-off</span>
    <span className="legend-item"><i className="swatch" style={{ background: 'var(--robot-1)' }} />R1</span>
    <span className="legend-item"><i className="swatch" style={{ background: 'var(--robot-2)' }} />R2</span>
    <span className="legend-item"><i className="swatch" style={{ background: 'var(--robot-3)' }} />R3</span>
  </div>;
}
