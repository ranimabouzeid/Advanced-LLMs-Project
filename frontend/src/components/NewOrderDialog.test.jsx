import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import NewOrderDialog from './NewOrderDialog';
import { ready } from '../test/fixtures';

it('submits the exact create-order payload and closes on success', async () => {
  const user = userEvent.setup();
  const run = vi.fn().mockResolvedValue(true);
  const onClose = vi.fn();
  render(<NewOrderDialog state={ready} disabled={false} run={run} onClose={onClose} />);
  await user.type(screen.getByLabelText('Order ID'), 'o2');
  await user.type(screen.getByLabelText('Package ID'), 'p2');
  await user.clear(screen.getByLabelText('Pickup x'));
  await user.type(screen.getByLabelText('Pickup x'), '3');
  await user.selectOptions(screen.getByLabelText('Drop-off'), '1');
  await user.click(screen.getByRole('button', { name: 'Create Order', exact: true }));
  expect(run).toHaveBeenCalledWith('createOrder', {
    order_id: 'o2', package_id: 'p2', pickup: { x: 3, y: 0 }, dropoff: ready.warehouse.dropoff_locations[1],
  });
  expect(onClose).toHaveBeenCalled();
});

it('keeps the dialog open and the draft intact when the command fails', async () => {
  const user = userEvent.setup();
  const run = vi.fn().mockResolvedValue(false);
  const onClose = vi.fn();
  render(<NewOrderDialog state={ready} disabled={false} run={run} onClose={onClose} />);
  await user.type(screen.getByLabelText('Order ID'), 'dup');
  await user.click(screen.getByRole('button', { name: 'Create Order', exact: true }));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Order ID')).toHaveValue('dup');
});

it('Cancel closes without submitting', async () => {
  const user = userEvent.setup();
  const run = vi.fn();
  const onClose = vi.fn();
  render(<NewOrderDialog state={ready} disabled={false} run={run} onClose={onClose} />);
  await user.click(screen.getByRole('button', { name: 'Cancel', exact: true }));
  expect(run).not.toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

it('disables the form fieldset while a command is in flight', () => {
  render(<NewOrderDialog state={ready} disabled run={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByLabelText('Order ID')).toBeDisabled();
});
