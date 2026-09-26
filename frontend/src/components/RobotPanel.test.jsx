import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import RobotPanel from './RobotPanel';
import { ready } from '../test/fixtures';

const withSchedule = { ...ready, robot_schedules: [{
  robot_id: 'robot-1', order_ids: ['o1', 'o3'], projected_robot: { battery: 63 },
  parking: { status: 'failed', plan: { route: [{ x: 9, y: 0 }, { x: 7, y: 9 }] },
    reason: 'Parking failed; delivery preserved' },
}] };

it('detailed view shows assignment order, final parking, projected battery and movement failure', () => {
  render(<RobotPanel state={withSchedule} detailed />);
  expect(screen.getByText('Assigned orders: o1 → o3')).toBeVisible();
  expect(screen.getByText('Final parking: (7, 9) — failed')).toBeVisible();
  expect(screen.getByText('Projected battery after parking: 63%')).toBeVisible();
  expect(screen.getByText('Parking failed; delivery preserved')).toBeVisible();
});

it('compact view (default) omits schedule detail but still shows battery and status', () => {
  render(<RobotPanel state={withSchedule} />);
  expect(screen.queryByText(/Assigned orders/)).not.toBeInTheDocument();
  expect(screen.getByText('robot-1')).toBeVisible();
  expect(screen.getByLabelText('robot-1 battery')).toBeVisible();
});

it('updates battery from committed robots even when forecasts and schedules differ', () => {
  const state = structuredClone(withSchedule);
  state.robot_forecasts = [{ robot: { ...state.warehouse.robots[0], battery: 90 }, order_ids: ['o1'] }];
  const { rerender } = render(<RobotPanel state={state} detailed />);
  expect(screen.getByLabelText('robot-1 battery')).toHaveAttribute('value', '100');

  // A partial Execute commits deliveries, but its parking forecast is not actual battery.
  const partial = structuredClone(state);
  partial.warehouse.robots[0].battery = 83;
  rerender(<RobotPanel state={partial} detailed />);
  expect(screen.getByLabelText('robot-1 battery')).toHaveAttribute('value', '83');
  expect(screen.getByText('Projected battery after parking: 63%')).toBeVisible();

  const completed = structuredClone(partial);
  completed.warehouse.robots[0].battery = 79;
  completed.robot_schedules[0].parking.status = 'completed';
  rerender(<RobotPanel state={completed} detailed />);
  expect(screen.getByLabelText('robot-1 battery')).toHaveAttribute('value', '79');
  expect(screen.getByLabelText('robot-2 battery')).toHaveAttribute('value', '100');
  expect(screen.getByLabelText('robot-3 battery')).toHaveAttribute('value', '100');
});
