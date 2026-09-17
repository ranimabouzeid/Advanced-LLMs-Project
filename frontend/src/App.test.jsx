import { StrictMode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
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
it('startup renders server state once and unchecked safety',async()=>{
  await start();expect(fetch).toHaveBeenCalledTimes(1);expect(screen.getAllByText('Unchecked')).toHaveLength(2);
  expect(screen.getByText('No orders yet. Create an order to begin.')).toBeVisible();
});
it('create order sends exact integers and does not plan automatically',async()=>{
  const user=await start();fetch.mockResolvedValueOnce(response(envelope(ordered)));
  await user.type(screen.getByLabelText('Order ID'),'o');await user.type(screen.getByLabelText('Package ID'),'p');
  await user.selectOptions(screen.getByLabelText('Drop-off'),'0');
  await user.click(screen.getByRole('button',{name:'Create Order',exact:true}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(2));
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({order_id:'o',package_id:'p',pickup:{x:2,y:0},dropoff:{x:9,y:0}});
  expect(fetch.mock.calls[1][0]).toBe('/api/sessions/session-a/orders');expect(screen.getByText('pending')).toBeVisible();
});
it('PLAN updates proposal without moving robots',async()=>{
  const user=await start(ordered);fetch.mockResolvedValueOnce(response(envelope(ready,'session-a','ready')));
  await user.click(screen.getByRole('button',{name:'Plan',exact:true}));
  await screen.findByText('plan: ready');expect(screen.getByTestId('pickup-route')).toBeInTheDocument();
  expect(screen.getAllByText('Battery 100%')).toHaveLength(3);
});
it('EXECUTE delivery consumes route and updates battery and order',async()=>{
  const user=await start(ready);fetch.mockResolvedValueOnce(response(envelope(delivered,'session-a','delivered')));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText('execute: delivered');
  expect(screen.getByText('Battery 91%')).toBeVisible();expect(screen.queryByTestId('pickup-route')).not.toBeInTheDocument();
});
it('replacement READY never triggers a second execute without a user click',async()=>{
  const user=await start(blocked);fetch.mockResolvedValueOnce(response(envelope(replacement,'session-a','ready')));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));
  await screen.findByText('Replacement route - review required');
  expect(screen.getByText(/has not been executed/)).toBeVisible();expect(fetch).toHaveBeenCalledTimes(2);
  expect(screen.getByTestId('delivery-route').querySelector('polyline')).toHaveAttribute('points',replacement.delivery_plan.delivery_route.map(p=>`${p.x+.5},${p.y+.5}`).join(' '));
  expect(screen.getAllByText('Battery 100%')).toHaveLength(3);
  fetch.mockResolvedValueOnce(response(envelope(delivered,'session-a','delivered')));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText('execute: delivered');
  expect(fetch).toHaveBeenCalledTimes(3);expect(screen.queryByText('Replacement route - review required')).not.toBeInTheDocument();
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
  const user=await start(ready);await user.type(screen.getByLabelText('Order ID'),'draft');
  fetch.mockResolvedValueOnce(response(envelope(initial,'session-b')));
  await user.click(screen.getByRole('button',{name:'Reset',exact:true}));await screen.findByText('Session session-b');
  expect(screen.getByLabelText('Order ID')).toHaveValue('');expect(screen.queryByTestId('pickup-route')).not.toBeInTheDocument();
  fetch.mockResolvedValueOnce(response(envelope({...initial,run_outcome:'no_work'},'session-b','no_work')));
  await user.click(screen.getByRole('button',{name:'Plan',exact:true}));
  expect(fetch.mock.calls[2][0]).toBe('/api/sessions/session-b/plan');
});
it('expected failed command accepts delivered committed state separately',async()=>{
  const user=await start(ready);fetch.mockResolvedValueOnce(response({...envelope(delivered,'session-a','failed'),error:{code:'consumed_proposal',message:'Already consumed'}}));
  await user.click(screen.getByRole('button',{name:'Execute',exact:true}));await screen.findByText('execute: failed');
  expect(screen.getByText('Already consumed')).toBeVisible();expect(screen.getAllByText('delivered').length).toBeGreaterThan(0);
  expect(screen.queryByText('Request could not be completed')).not.toBeInTheDocument();
});
it.each([404,409,422,500])('handles HTTP %s while retaining state',async status=>{
  const user=await start(ready);fetch.mockResolvedValueOnce(response({error:{code:'example',message:'Backend message'}},status));
  await user.click(screen.getByRole('button',{name:'Plan',exact:true}));await screen.findByRole('alert');
  expect(screen.getByTestId('pickup-route')).toBeInTheDocument();expect(screen.queryByText('plan: failed')).not.toBeInTheDocument();
  if(status===404) expect(screen.getByRole('button',{name:'Create fresh session'})).toBeVisible();
  if(status===409) expect(screen.getByText('Session busy')).toBeVisible();
  if(status===500) expect(screen.getByText('The server could not complete the operation.')).toBeVisible();
});
it('422 preserves order form values',async()=>{
  const user=await start();await user.type(screen.getByLabelText('Order ID'),'duplicate');await user.type(screen.getByLabelText('Package ID'),'p');
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
  expect(screen.getByText('Planning...')).toBeVisible();expect(screen.getAllByRole('listitem')).toHaveLength(4);
  expect(screen.getByRole('button',{name:'Reset',exact:true})).toBeDisabled();
  await act(async()=>waiting.resolve(response(envelope(ready,'session-a','ready'))));
});
