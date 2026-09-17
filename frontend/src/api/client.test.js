import { beforeEach, expect, it, vi } from 'vitest';
import { api } from './client';
import { initial, envelope, response } from '../test/fixtures';
beforeEach(() => vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(envelope(initial)))));
it.each(['createSession', 'plan', 'execute', 'reset'])('%s sends a bodyless POST', async action => {
  await api[action]('session-a');
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe(action === 'createSession' ? '/api/sessions' : `/api/sessions/session-a/${action}`);
  expect(options).toEqual({ method: 'POST' });
});
it('encodes session identifiers and performs a read', async () => {
  await api.refresh('a/b'); expect(fetch).toHaveBeenCalledWith('/api/sessions/a%2Fb/state', { method: 'GET' });
});
it('sends exact order body', async () => {
  const body = { order_id: 'o', package_id: 'p', pickup: { x: 2, y: 0 }, dropoff: { x: 9, y: 0 } };
  await api.createOrder('session-a', body);
  expect(fetch).toHaveBeenCalledWith('/api/sessions/session-a/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
});
it('adds and removes a cell', async () => {
  await api.block('session-a', { x: 5, y: 0 }); await api.unblock('session-a', { x: 5, y: 0 });
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ x: 5, y: 0 });
  expect(fetch.mock.calls[1]).toEqual(['/api/sessions/session-a/blocked-cells/5/0', { method: 'DELETE' }]);
});
it.each([404,409,422,500])('retains typed HTTP %s errors without retry', async status => {
  fetch.mockResolvedValue(response({ error: { code: 'example', message: 'Domain message' } }, status));
  await expect(api.plan('session-a')).rejects.toMatchObject({ kind: 'http', status, code: 'example' });
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('distinguishes network failure and does not retry', async () => {
  fetch.mockRejectedValue(new TypeError('offline'));
  await expect(api.execute('session-a')).rejects.toMatchObject({ kind: 'network', status: null });
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('accepts expected failed command data', async () => {
  fetch.mockResolvedValue(response({ ...envelope(initial), outcome: 'failed', error: { code: 'missing_proposal', message: 'Plan first' } }));
  await expect(api.execute('session-a')).resolves.toMatchObject({ outcome: 'failed' });
});
it('handles unreadable success responses without retry', async () => {
  fetch.mockResolvedValue({ ok: true, status: 200, json: async () => { throw new Error(); } });
  await expect(api.plan('session-a')).rejects.toMatchObject({ kind: 'protocol' });
});
