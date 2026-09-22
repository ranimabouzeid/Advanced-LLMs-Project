// Pure display selectors. No simulation rules live here - only formatting and
// derivation of values already present in the server-committed state.

const AGENT_NODES = ['order', 'fleet', 'route', 'safety'];
const AGENT_LABELS = { order: 'Order Agent', fleet: 'Fleet Agent', route: 'Route Agent', safety: 'Safety Agent' };
const AGENT_CAPTIONS = {
  order: state => state.order_selection?.explanation || null,
  fleet: state => state.selected_robot_id ? `Assigned ${state.selected_robot_id}` : null,
  route: state => state.delivery_plan ? `Planning route (${state.delivery_plan.total_steps} cells)` : null,
  safety: state => state.safety?.explanation || null,
};

/** Overall system status pill: running / working / error / offline. */
export function deriveSystemStatus({ pendingAction, httpError, session }) {
  if (httpError) return { state: 'error', label: httpError.status === 404 ? 'Session unavailable' : 'Connection issue' };
  if (pendingAction) return { state: 'working', label: 'Working' };
  if (!session) return { state: 'offline', label: 'Connecting' };
  const active = session.state.warehouse.robots.filter(r => r.status !== 'idle').length;
  return { state: 'running', label: `System Running`, activeRobots: active };
}

/** Latest known status per workflow node, derived from node_activity in returned order. */
export function deriveAgentStatuses(state) {
  const activity = state.node_activity || [];
  return AGENT_NODES.map(node => {
    const records = activity.filter(r => r.node === node);
    const last = records.at(-1);
    return {
      node,
      label: AGENT_LABELS[node],
      status: last?.status || 'pending',
      message: last?.message || AGENT_CAPTIONS[node](state),
    };
  });
}

/** True when Safety rejected and Route retried in the same run (a replan cycle). */
export function hasReplanCycle(state) {
  const activity = state.node_activity || [];
  return activity.some(r => r.node === 'safety' && r.status === 'rejected');
}

export function ordersCompletedCount(orders) {
  return orders.filter(o => o.status === 'delivered').length;
}

const ROBOT_COLOR_VARS = ['--robot-1', '--robot-2', '--robot-3'];
/** Stable color assignment by robot id suffix (robot-1, robot-2, ...), falling back to index. */
export function robotColorVar(robotId, index = 0) {
  const match = /(\d+)\s*$/.exec(robotId || '');
  const n = match ? Number(match[1]) - 1 : index;
  const varName = ROBOT_COLOR_VARS[((n % ROBOT_COLOR_VARS.length) + ROBOT_COLOR_VARS.length) % ROBOT_COLOR_VARS.length];
  return `var(${varName})`;
}

export function robotShortLabel(robotId) {
  return `R${(robotId || '').replace(/\D/g, '') || '?'}`;
}

const key = p => `${p.x},${p.y}`;

/**
 * Build up to `count` new order drafts from free pickup cells, cycling drop-offs.
 * Pure: takes the current warehouse snapshot and returns plain order payloads;
 * it does not call the API or mutate anything.
 */
export function generateRushOrders(warehouse, count = 3) {
  const occupied = new Set([
    ...warehouse.obstacles.map(key),
    ...warehouse.blocked_cells.map(key),
    ...(warehouse.parking_cells || []).map(key),
    ...warehouse.dropoff_locations.map(key),
    ...warehouse.robots.map(r => key(r.position)),
    ...warehouse.orders.filter(o => o.status !== 'delivered').map(o => key(o.package.pickup)),
  ]);
  const free = [];
  for (let y = 0; y < warehouse.height && free.length < count; y++) {
    for (let x = 0; x < warehouse.width && free.length < count; x++) {
      const id = `${x},${y}`;
      if (!occupied.has(id)) { free.push({ x, y }); occupied.add(id); }
    }
  }
  const usedNumbers = warehouse.orders.map(o => Number(/^rush-(\d+)$/.exec(o.id)?.[1])).filter(Number.isFinite);
  let next = (usedNumbers.length ? Math.max(...usedNumbers) : 0) + 1;
  const dropoffs = [...warehouse.dropoff_locations];
  return free.map((pickup, i) => {
    const n = next++;
    return {
      order_id: `rush-${n}`,
      package_id: `rush-pkg-${n}`,
      pickup,
      dropoff: dropoffs[i % dropoffs.length],
    };
  });
}

/**
 * Build a sequential per-robot animation path for the replay overlay.
 * `capturedState` is the state seen right before Execute was requested (still
 * carrying the proposed plan); `resultState` is the committed state Execute
 * returned. Only orders that `resultState` actually shows as delivered are
 * animated, so a partially-delivered batch only replays what really happened.
 */
export function buildReplayLegs(capturedState, resultState) {
  const deliveredIds = new Set(resultState.warehouse.orders.filter(o => o.status === 'delivered').map(o => o.id));
  const source = capturedState.planned_deliveries?.length
    ? capturedState.planned_deliveries
    : capturedState.delivery_plan
      ? [{ order_id: capturedState.delivery_plan.order_id, delivery_plan: capturedState.delivery_plan }]
      : [];
  const legs = [];
  for (const item of source) {
    if (!item.delivery_plan || !deliveredIds.has(item.order_id)) continue;
    const { pickup_route: pickup, delivery_route: delivery, robot_id: robotId } = item.delivery_plan;
    legs.push({ robotId, route: [...pickup, ...delivery.slice(1)] });
  }
  const deliveredRobots = new Set(legs.map(l => l.robotId));
  for (const schedule of capturedState.robot_schedules || []) {
    const route = schedule.parking?.plan?.route;
    if (deliveredRobots.has(schedule.robot_id) && schedule.parking?.status === 'approved' && route?.length > 1) {
      legs.push({ robotId: schedule.robot_id, route: route.slice(1) });
    }
  }
  return legs;
}
