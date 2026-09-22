import { describe, expect, it } from 'vitest';
import {
  deriveSystemStatus, deriveAgentStatuses, hasReplanCycle, ordersCompletedCount,
  robotColorVar, robotShortLabel, generateRushOrders, buildReplayLegs,
} from './derive';
import { initial, ready, replacement, delivered, envelope } from '../test/fixtures';

describe('deriveSystemStatus', () => {
  it('reports error when an httpError is present, regardless of session', () => {
    expect(deriveSystemStatus({ httpError: { status: 404 }, pendingAction: null, session: null }).state).toBe('error');
  });
  it('reports working while a command is pending', () => {
    expect(deriveSystemStatus({ httpError: null, pendingAction: 'plan', session: envelope(ready) }).state).toBe('working');
  });
  it('reports offline before the first session exists', () => {
    expect(deriveSystemStatus({ httpError: null, pendingAction: null, session: null }).state).toBe('offline');
  });
  it('reports running with a count of non-idle robots', () => {
    const s = structuredClone(ready);
    s.warehouse.robots[0].status = 'busy';
    const status = deriveSystemStatus({ httpError: null, pendingAction: null, session: envelope(s) });
    expect(status.state).toBe('running');
    expect(status.activeRobots).toBe(1);
  });
});

describe('deriveAgentStatuses', () => {
  it('returns the four roles in workflow order with their latest recorded status', () => {
    const statuses = deriveAgentStatuses(ready);
    expect(statuses.map(a => a.node)).toEqual(['order', 'fleet', 'route', 'safety']);
    expect(statuses.every(a => a.status === 'completed')).toBe(true);
  });
  it('falls back to pending with no message when a node never ran', () => {
    const [order] = deriveAgentStatuses(initial);
    expect(order.status).toBe('pending');
  });
  it('picks the latest record when a node ran more than once', () => {
    const statuses = deriveAgentStatuses(replacement);
    const safety = statuses.find(a => a.node === 'safety');
    expect(safety.status).toBe('completed'); // the retry's later "completed" record, not the earlier "rejected" one
  });
});

it('hasReplanCycle is true only after a safety rejection', () => {
  expect(hasReplanCycle(ready)).toBe(false);
  expect(hasReplanCycle(replacement)).toBe(true);
});

it('ordersCompletedCount counts only delivered orders', () => {
  expect(ordersCompletedCount(delivered.warehouse.orders)).toBe(1);
  expect(ordersCompletedCount(ready.warehouse.orders)).toBe(0);
});

describe('robotColorVar / robotShortLabel', () => {
  it('assigns a stable color per numeric robot suffix', () => {
    expect(robotColorVar('robot-1')).toBe('var(--robot-1)');
    expect(robotColorVar('robot-2')).toBe('var(--robot-2)');
    expect(robotColorVar('robot-4')).toBe('var(--robot-1)'); // wraps
  });
  it('shortens an id to its R-number label', () => {
    expect(robotShortLabel('robot-3')).toBe('R3');
  });
});

describe('generateRushOrders', () => {
  it('produces distinct free-cell orders that avoid shelves, blocks and existing pickups', () => {
    const orders = generateRushOrders(initial.warehouse, 3);
    expect(orders).toHaveLength(3);
    const ids = new Set(orders.map(o => o.order_id));
    expect(ids.size).toBe(3);
    const occupied = new Set([
      ...initial.warehouse.obstacles.map(p => `${p.x},${p.y}`),
      ...initial.warehouse.robots.map(r => `${r.position.x},${r.position.y}`),
    ]);
    for (const o of orders) expect(occupied.has(`${o.pickup.x},${o.pickup.y}`)).toBe(false);
  });
  it('numbers new rush orders after any existing rush orders', () => {
    const s = structuredClone(initial);
    s.warehouse.orders = [{ id: 'rush-1', package: { id: 'rush-pkg-1', pickup: { x: 0, y: 5 } }, dropoff: { x: 9, y: 0 }, status: 'pending', assigned_robot_id: null }];
    const [next] = generateRushOrders(s.warehouse, 1);
    expect(next.order_id).toBe('rush-2');
  });
});

describe('buildReplayLegs', () => {
  it('builds one continuous route per delivered order, joining pickup and delivery legs', () => {
    const legs = buildReplayLegs(ready, delivered);
    expect(legs).toHaveLength(1);
    expect(legs[0].robotId).toBe('robot-1');
    expect(legs[0].route[0]).toEqual(ready.delivery_plan.pickup_route[0]);
    expect(legs[0].route.at(-1)).toEqual(ready.delivery_plan.delivery_route.at(-1));
  });
  it('omits orders that did not actually end up delivered (partial batches stay honest)', () => {
    const before = structuredClone(ready);
    before.planned_deliveries = [
      { order_id: 'o', robot_id: 'robot-1', delivery_plan: ready.delivery_plan },
      { order_id: 'other', robot_id: 'robot-2', delivery_plan: { ...ready.delivery_plan, order_id: 'other', robot_id: 'robot-2' } },
    ];
    const legs = buildReplayLegs(before, delivered); // delivered only has order "o" delivered
    expect(legs.map(l => l.robotId)).toEqual(['robot-1']);
  });
  it('returns no legs when nothing was delivered', () => {
    expect(buildReplayLegs(ready, ready)).toEqual([]);
  });
});
