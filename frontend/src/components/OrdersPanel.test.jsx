import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import OrdersPanel from './OrdersPanel';
import { ready } from '../test/fixtures';

it('detailed table shows every assignment, execution result, and unplannable reason', () => {
  const orders = ['a', 'b', 'c'].map(id => ({ ...ready.warehouse.orders[0], id }));
  render(<OrdersPanel detailed orders={orders} plannedDeliveries={[
    { order_id: 'a', robot_id: 'robot-1', status: 'delivered' },
    { order_id: 'b', robot_id: 'robot-2', status: 'not_executed', reason: 'Earlier delivery failed' },
    { order_id: 'c', robot_id: null, status: 'unplannable', reason: 'No reachable path' },
  ]} />);
  const rows = screen.getAllByRole('row');
  expect(rows).toHaveLength(4);
  expect(within(rows[1]).getByText('robot-1')).toBeVisible();
  expect(within(rows[1]).getByText('delivered')).toBeVisible();
  expect(within(rows[2]).getByText('robot-2')).toBeVisible();
  expect(within(rows[2]).getByText(/Earlier delivery failed/)).toBeVisible();
  expect(within(rows[3]).getByText(/No reachable path/)).toBeVisible();
});

it('compact queue (default) lists orders with status chips and no table', () => {
  const orders = ['a', 'b'].map(id => ({ ...ready.warehouse.orders[0], id }));
  render(<OrdersPanel orders={orders} plannedDeliveries={[{ order_id: 'a', robot_id: 'robot-1', status: 'delivered' }]} />);
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.getByText('#a')).toBeVisible();
  expect(screen.getByText('#b')).toBeVisible();
  expect(screen.getAllByText(/Waiting \d+s/)).toHaveLength(2);
});

it('shows a New Order trigger when a handler is supplied', async () => {
  const onNewOrder = () => {};
  render(<OrdersPanel orders={[]} onNewOrder={onNewOrder} />);
  expect(screen.getByRole('button', { name: /New Order/ })).toBeVisible();
});
