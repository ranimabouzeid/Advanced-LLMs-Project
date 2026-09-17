import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import WarehouseGrid from './WarehouseGrid';
import { initial, ready, blocked } from '../test/fixtures';
const draw = (state = initial) => render(<WarehouseGrid state={state} selectedCell={null} onSelect={vi.fn()} />);
it('renders 100 cells in x-right y-down order', () => {
  const { container } = draw(); const cells = container.querySelectorAll('[data-cell]');
  expect(cells).toHaveLength(100); expect(cells[0]).toHaveAttribute('data-cell','0,0');
  expect(cells[9]).toHaveAttribute('data-cell','9,0'); expect(cells[10]).toHaveAttribute('data-cell','0,1'); expect(cells[99]).toHaveAttribute('data-cell','9,9');
});
it('renders shelf, robot, dropoff and blocked objects', () => {
  draw(blocked); expect(screen.getAllByRole('button', { name: /shelf/ })).toHaveLength(initial.warehouse.obstacles.length);
  expect(screen.getByRole('button', { name: /robot-1/ })).toBeVisible();
  expect(screen.getAllByRole('button', { name: /drop-off/ })).toHaveLength(2);
  expect(screen.getByRole('button', { name: /Cell \(5, 0\).*blocked/ })).toHaveClass('blocked');
});
it.each(['pending','assigned'])('shows %s package at pickup', status => {
  const s=structuredClone(ready); s.warehouse.orders[0].status=status; draw(s);
  expect(screen.getByRole('button', { name: /Cell \(2, 0\).*package p/ })).toBeVisible();
});
it.each(['picked_up','delivered'])('does not leave %s package at pickup', status => {
  const s=structuredClone(ready); s.warehouse.orders[0].status=status; draw(s);
  expect(screen.queryByRole('button', { name: /package p/ })).not.toBeInTheDocument();
});
it('groups packages and identifies a carrying selected robot', () => {
  const s=structuredClone(ready); s.warehouse.orders.push({ ...s.warehouse.orders[0], id:'other',package:{id:'p2',pickup:{x:2,y:0}} });
  s.warehouse.robots[0].carried_package_id='carried'; const { container }=draw(s);
  expect(screen.getByRole('button',{name:/package p, package p2/})).toBeVisible();
  expect(screen.getByRole('button',{name:/robot-1 carrying carried/})).toBeVisible();
  expect(container.querySelector('.selected-robot')).toBeVisible();
});
it('draws both endpoint-inclusive route arrays at cell centers', () => {
  draw(ready); expect(screen.getByTestId('pickup-route').querySelector('polyline')).toHaveAttribute('points','0.5,0.5 1.5,0.5 2.5,0.5');
  expect(screen.getByTestId('delivery-route').querySelector('polyline')).toHaveAttribute('points',ready.delivery_plan.delivery_route.map(p=>`${p.x+.5},${p.y+.5}`).join(' '));
});
it('retains separate layers for shared segments and one-cell endpoints', () => {
  const s=structuredClone(ready); s.delivery_plan.delivery_route=s.delivery_plan.pickup_route;
  const { rerender }=draw(s); expect(screen.getAllByTestId(/-route/)).toHaveLength(2);
  s.delivery_plan.pickup_route=[{x:0,y:0}]; rerender(<WarehouseGrid state={s} onSelect={()=>{}} />);
  expect(screen.getByTestId('pickup-route').querySelector('circle')).toHaveAttribute('cx','0.5');
});
it('marks stale routes explicitly', () => { draw(blocked); expect(screen.getByText(/Stale route/)).toBeVisible(); });
it('supports keyboard cell selection without mutation', async () => {
  const select=vi.fn(); render(<WarehouseGrid state={initial} onSelect={select} />);
  screen.getByRole('button',{name:/Cell \(5, 0\)/}).focus(); await userEvent.keyboard('{Enter}');
  expect(select).toHaveBeenCalledWith({x:5,y:0});
});

it('selects each batch route in order without marking projected revisions stale', async () => {
  const s = structuredClone(ready);
  s.batch_revision = s.warehouse.revision;
  s.planned_deliveries = [
    { order_id: 'o', robot_id: 'robot-1', status: 'approved', delivery_plan: s.delivery_plan },
    { order_id: 'second', robot_id: 'robot-2', status: 'approved', delivery_plan: {
      ...s.delivery_plan, warehouse_revision: s.warehouse.revision + 1,
      pickup_route: [{ x: 0, y: 1 }, { x: 1, y: 1 }],
    } },
  ];
  draw(s);
  const selector = screen.getByRole('combobox', { name: 'Delivery route' });
  expect(selector).toHaveValue('o');
  expect(screen.getByRole('option', { name: /2. second.*robot-2/ })).toBeVisible();
  await userEvent.selectOptions(selector, 'second');
  expect(screen.getByTestId('pickup-route').querySelector('polyline')).toHaveAttribute('points', '0.5,1.5 1.5,1.5');
  expect(screen.queryByText(/Stale route/)).not.toBeInTheDocument();
  expect(s.warehouse).toEqual(ready.warehouse);
});

it('marks invalidated batch routes stale and falls back when a selected order disappears', async () => {
  const s = structuredClone(ready);
  s.batch_revision = s.warehouse.revision;
  s.planned_deliveries = ['first', 'second'].map(order_id => ({
    order_id, robot_id: 'robot-1', status: 'stale', delivery_plan: s.delivery_plan,
  }));
  const { rerender } = draw(s);
  await userEvent.selectOptions(screen.getByLabelText('Delivery route'), 'second');
  s.planned_deliveries = s.planned_deliveries.slice(0, 1);
  rerender(<WarehouseGrid state={s} onSelect={() => {}} />);
  expect(screen.getByLabelText('Delivery route')).toHaveValue('first');
  expect(screen.getByText(/Stale route/)).toBeVisible();
});

it('labels staging cells and draws parking only after the robot final assignment', async () => {
  const s = structuredClone(ready);
  s.warehouse.parking_cells = [{ x: 7, y: 9 }, { x: 8, y: 9 }, { x: 8, y: 8 }];
  s.batch_revision = s.warehouse.revision;
  s.planned_deliveries = ['first', 'last'].map(order_id => ({
    order_id, robot_id: 'robot-1', status: 'approved', delivery_plan: s.delivery_plan,
  }));
  s.robot_schedules = [{ robot_id: 'robot-1', order_ids: ['first', 'last'], parking: {
    status: 'approved', plan: { route: [{ x: 9, y: 0 }, { x: 7, y: 9 }] },
  } }];
  draw(s);
  expect(screen.getByRole('button', { name: /Cell \(7, 9\).*parking P1/ })).toBeVisible();
  expect(screen.getByRole('button', { name: /Cell \(8, 9\).*parking P2/ })).toBeVisible();
  expect(screen.getByRole('button', { name: /Cell \(8, 8\).*parking P3/ })).toBeVisible();
  expect(screen.queryByTestId('parking-route')).not.toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText('Delivery route'), 'last');
  expect(screen.getByTestId('parking-route').querySelector('polyline')).toHaveAttribute('points', '9.5,0.5 7.5,9.5');
});

it('draws a parking-only recovery proposal', () => {
  const s = structuredClone(initial);
  s.robot_schedules = [{ robot_id: 'robot-1', order_ids: [], parking: {
    plan: { route: [{ x: 9, y: 0 }, { x: 7, y: 9 }] }, status: 'approved',
  } }];
  draw(s);
  expect(screen.getByTestId('parking-route')).toBeVisible();
  expect(screen.queryByTestId('pickup-route')).not.toBeInTheDocument();
});
