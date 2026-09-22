import { StrictMode } from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { initial, ordered, ready, blocked, replacement, delivered, envelope, response, deferred } from './test/fixtures';
beforeEach(()=>vi.stubGlobal('fetch',vi.fn()));
async function start(snapshot=initial) {
  fetch.mockResolvedValueOnce(response(envelope(snapshot)));
  render(<StrictMode><App /></StrictMode>);
  await screen.findByText('Session session-a');
  await waitFor(()=>expect(screen.getByRole('button',{name:'Plan',exact:true})).toBeEnabled());
  return userEvent.setup();
}
// The create-order form now lives in a dialog opened from the Order Queue panel.
async function openNewOrder(user) {
  await user.click(screen.getByRole('button', { name: /New Order/ }));
  await screen.findByLabelText('Order ID');
}
// The attempted-command summary (e.g. "plan: ready") moved out of the always-
// visible dashboard and into the Full State dialog.
async function attemptedCommandText(user) {
  await user.click(screen.getByRole('button', { name: 'View Full State' }));
  const dt = await screen.findByText('Attempted command');
  return dt.nextElementSibling.textContent;
}
it('startup renders server state once and unchecked safety',async()=>{
  await start();expect(fetch).toHaveBeenCalledTimes(1);expect(screen.getAllByText('Unchecked')).toHaveLength(2);
  expect(screen.getByText('No orders yet. Create an order to begin.')).toBeVisible();
});
it('create order sends exact integers and does not plan automatically',async()=>{
  const user=await start();fetch.mockResolvedValueOnce(response(envelope(ordered)));
  await openNewOrder(user);
  await user.type(screen.getByLabelText('Order ID'),'o');await user.type(screen.getByLabelText('Package ID'),'p');
  await user.selectOptions(screen.getByLabelText('Drop-off'),'0');
  await user.click(screen.getByRole('button',{name:'Create Order',exact:true}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({order_id:'o',package_id:'p',pickup:{x:2,y:0},dropoff:{x:9,y:0}});
  expect(fetch.mock.calls[1][0]).toBe('/api/sessions/session-a/orders');
  await waitFor(()=>expect(screen.queryByLabelText('Order ID')).not.toBeInTheDocument());
  expect(within(screen.getByRole('list')).getByText('pending')).toBeVisible();
});
it('PLAN updates proposal without moving robots',async()=>{
  const user=await start(ordered);fetch.mockResolvedValueOnce(response(envelope(ready,'session-a','ready')));
  await user.click(screen.getByRole('button',{name:'Plan',exact:true}));
  await screen.findByTestId('pickup-route');
  expect(screen.getAllByText('Battery 100%')).toHaveLength(3);
  expect(await attemptedCommandText(user)).toBe('plan: ready');
});
it('EXECUTE delivery consumes route and updates battery and order',async()=>{
  const user=await start(ready);fetch.mockResolvedValueOnce(response(envelope(delivered,'session-a','delivered')));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText('Battery 91%');
  expect(screen.queryByTestId('pickup-route')).not.toBeInTheDocument();
  expect(await attemptedCommandText(user)).toBe('execute: delivered');
});
it('replacement READY never triggers a second execute without a user click',async()=>{
  const user=await start(blocked);fetch.mockResolvedValueOnce(response(envelope(replacement,'session-a','ready')));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));
  await screen.findByText('Replacement route - review required');
  expect(screen.getByText(/has not been executed/)).toBeVisible();expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.getByTestId('delivery-route').querySelector('polyline')).toHaveAttribute('points',replacement.delivery_plan.delivery_route.map(p=>`${p.x+.5},${p.y+.5}`).join(' '));
  expect(screen.getAllByText('Battery 100%')).toHaveLength(3);
  fetch.mockResolvedValueOnce(response(envelope(delivered,'session-a','delivered')));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText('Battery 91%');
  expect(fetch).toHaveBeenCalledTimes(3);expect(screen.queryByText('Replacement route - review required')).not.toBeInTheDocument();
  expect(await attemptedCommandText(user)).toBe('execute: delivered');
});
it('selecting a cell only fills controls; block and unblock wait for server',async()=>{
  const user=await start(ready);await user.click(screen.getByRole('button',{name:/Cell \(5, 0\)/}));
  expect(screen.getByLabelText('Cell x')).toHaveValue(5);expect(fetch).toHaveBeenCalledTimes(1);
  const waiting=deferred();fetch.mockReturnValueOnce(waiting.promise);
  await user.click(screen.getByRole('button',{name:'Block Cell',exact:true}));
  expect(screen.getByRole('button',{name:/Cell \(5, 0\)/})).not.toHaveClass('blocked');
  await act(async()=>waiting.resolve(response(envelope(blocked))));
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({x:5,y:0});
  expect(screen.getByRole('button',{name:/Cell \(5, 0\)/})).toHaveClass('blocked');
  fetch.mockResolvedValueOnce(response(envelope(ready)));
  await user.click(screen.getByRole('button',{name:'Unblock Cell',exact:true}));
  expect(fetch.mock.calls[2]).toEqual(['/api/sessions/session-a/blocked-cells/5/0',{method:'DELETE'}]);
});
it('reset replaces ID, clears drafts and sends later commands to the new session',async()=>{
  const user=await start(ready);
  await openNewOrder(user);
  await user.type(screen.getByLabelText('Order ID'),'draft');
  await user.click(screen.getByRole('button',{name:'Cancel',exact:true}));
  await waitFor(()=>expect(screen.queryByLabelText('Order ID')).not.toBeInTheDocument());
  fetch.mockResolvedValueOnce(response(envelope(initial,'session-b')));
  await user.click(screen.getByRole('button',{name:'Reset',exact:true}));await screen.findByText('Session session-b');
  expect(screen.queryByTestId('pickup-route')).not.toBeInTheDocument();
  await openNewOrder(user);
  expect(screen.getByLabelText('Order ID')).toHaveValue('');
  await user.click(screen.getByRole('button',{name:'Cancel',exact:true}));
  fetch.mockResolvedValueOnce(response(envelope({...initial,run_outcome:'no_work'},'session-b','no_work')));
  await user.click(screen.getByRole('button',{name:'Plan',exact:true}));
  expect(fetch.mock.calls[2][0]).toBe('/api/sessions/session-b/plan');
});
it('expected failed command accepts delivered committed state separately',async()=>{
  const user=await start(ready);fetch.mockResolvedValueOnce(response({...envelope(delivered,'session-a','failed'),error:{code:'consumed_proposal',message:'Already consumed'}}));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText('Already consumed');
  expect(screen.getAllByText('delivered').length).toBeGreaterThan(0);
  expect(screen.queryByText('Request could not be completed')).not.toBeInTheDocument();
  expect(await attemptedCommandText(user)).toBe('execute: failed');
});
it.each([404,409,422,500])('handles HTTP %s while retaining state',async status=>{
  const user=await start(ready);fetch.mockResolvedValueOnce(response({error:{code:'example',message:'Backend message'}},status));
  await user.click(screen.getByRole('button',{name:'Plan',exact:true}));await screen.findByRole('alert');
  expect(screen.getByTestId('pickup-route')).toBeInTheDocument();
  expect(await attemptedCommandText(user)).toBe('None');
  if(status===404) expect(screen.getByRole('button',{name:'Create fresh session'})).toBeVisible();
  if(status===409) expect(screen.getByText('Session busy')).toBeVisible();
  if(status===500) expect(screen.getByText('The server could not complete the operation.')).toBeVisible();
});
it('422 preserves order form values',async()=>{
  const user=await start();
  await openNewOrder(user);
  await user.type(screen.getByLabelText('Order ID'),'duplicate');await user.type(screen.getByLabelText('Package ID'),'p');
  fetch.mockResolvedValueOnce(response({error:{code:'invalid_mutation',message:'Warehouse mutation rejected'}},422));
  await user.click(screen.getByRole('button',{name:'Create Order',exact:true}));await screen.findByText('Warehouse mutation rejected');
  expect(screen.getByLabelText('Order ID')).toHaveValue('duplicate');expect(screen.getByLabelText('Package ID')).toHaveValue('p');
});
it('network uncertainty permits refresh but never automatically retries mutation',async()=>{
  const user=await start(ready);fetch.mockRejectedValueOnce(new TypeError('offline'));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText(/Request completion is uncertain/);
  expect(fetch).toHaveBeenCalledTimes(2);expect(screen.getByRole('button',{name:'Execute',exact:true})).toBeDisabled();
  fetch.mockResolvedValueOnce(response(envelope(delivered)));
  await user.click(screen.getByRole('button',{name:'Refresh state'}));
  await screen.findByText('Battery 91%');expect(fetch.mock.calls[2][1].method).toBe('GET');
});
it('while pending disables duplicate commands and appends no fabricated activity',async()=>{
  const user=await start(ready);const waiting=deferred();fetch.mockReturnValueOnce(waiting.promise);
  await user.dblClick(screen.getByRole('button',{name:'Plan',exact:true}));expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Planning...')).toBeVisible();
  const cards=within(screen.getByRole('region',{name:'Agent activity'})).getAllByRole('article');
  expect(cards).toHaveLength(4);
  cards.forEach(card=>expect(card).toHaveTextContent('Working...'));
  expect(screen.getByRole('button',{name:'Reset',exact:true})).toBeDisabled();
  await act(async()=>waiting.resolve(response(envelope(ready,'session-a','ready'))));
});

it('shows committed partial batch results and requires planning the remainder', async () => {
  const user = await start(ready);
  const partial = structuredClone(delivered);
  partial.run_outcome = 'partial';
  partial.planned_deliveries = [{ order_id: 'o', robot_id: 'robot-1', status: 'delivered', delivery_plan: ready.delivery_plan }];
  fetch.mockResolvedValueOnce(response(envelope(partial, 'session-a', 'partial')));
  await user.click(screen.getByRole('button', { name: 'Execute', exact: true }));
  expect(await screen.findByText('Batch partially delivered')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Execute', exact: true })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Plan', exact: true })).toBeEnabled();
  expect(screen.getByLabelText('Delivery route')).toBeVisible();
});

it('tabs switch which panel is shown without losing session data', async () => {
  const user = await start(ready);
  await user.click(screen.getByRole('tab', { name: /Orders/ }));
  expect(screen.getByRole('tabpanel', { name: /Orders/ })).toBeVisible();
  expect(screen.getByRole('table')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: /Robots/ }));
  expect(screen.getByText('Position (0, 0) - Selected')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: 'Live State' }));
  expect(screen.getByText('Approved')).toBeVisible();
  await user.click(screen.getByRole('tab', { name: /Dashboard/ }));
  expect(screen.getByTestId('pickup-route')).toBeInTheDocument();
});
