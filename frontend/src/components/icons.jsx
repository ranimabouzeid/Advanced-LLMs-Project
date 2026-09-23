// Small inline SVG icons. Kept dependency-free; each accepts standard SVG props.
const base = { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' };

export const IconCube = props => <svg {...base} width={22} height={22} {...props}>
  <path d="M12 2 21 7v10l-9 5-9-5V7z" /><path d="M3 7l9 5 9-5" /><path d="M12 12v9" />
</svg>;

export const IconDashboard = props => <svg {...base} {...props}>
  <rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" />
  <rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" />
</svg>;

export const IconOrders = props => <svg {...base} {...props}>
  <path d="M4 7h16M4 12h16M4 17h10" />
</svg>;

export const IconRobot = props => <svg {...base} {...props}>
  <rect x="4" y="9" width="16" height="11" rx="2" /><circle cx="9" cy="14.5" r="1.4" /><circle cx="15" cy="14.5" r="1.4" />
  <path d="M12 9V5M9 5h6" />
</svg>;

export const IconState = props => <svg {...base} {...props}>
  <path d="M3 12h4l2-7 4 14 2-7h6" />
</svg>;

export const IconClose = props => <svg {...base} {...props}>
  <path d="M6 6l12 12M18 6L6 18" />
</svg>;

export const IconPlus = props => <svg {...base} {...props}>
  <path d="M12 5v14M5 12h14" />
</svg>;
