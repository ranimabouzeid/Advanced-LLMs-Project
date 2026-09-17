import { StrictMode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useWarehouseSession } from './useWarehouseSession';
import { initial, ready, replacement, envelope, response, deferred } from '../test/fixtures';
beforeEach(() => vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(envelope(initial)))));
it('shares startup across Strict Mode effects', async () => {
  const { result } = renderHook(useWarehouseSession, { wrapper: StrictMode });
  await waitFor(() => expect(result.current.session?.session_id).toBe('session-a'));
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('guards rapid concurrent submission before React rerenders', async () => {
  const { result } = renderHook(useWarehouseSession); await waitFor(() => expect(result.current.pendingAction).toBe(null));
  const pending = deferred(); fetch.mockReturnValue(pending.promise);
  let first;
  act(() => { first = result.current.run('plan'); result.current.run('plan'); result.current.run('reset'); });
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => { pending.resolve(response(envelope(ready, 'session-a', 'ready'))); await first; });
  expect(result.current.session.state.run_outcome).toBe('ready');
});
it('reset replaces ID and rejects a delayed response carrying the retired ID', async () => {
  const { result } = renderHook(useWarehouseSession); await waitFor(() => expect(result.current.pendingAction).toBe(null));
  fetch.mockResolvedValueOnce(response(envelope(initial, 'session-b')));
  await act(async () => { await result.current.run('reset'); });
  const pending = deferred(); fetch.mockReturnValueOnce(pending.promise);
  let refresh; act(() => { refresh = result.current.run('refresh'); });
  await act(async () => { pending.resolve(response(envelope(ready, 'session-a'))); await refresh; });
  expect(result.current.session.session_id).toBe('session-b');
  expect(result.current.session.state).toEqual(initial);
  expect(fetch.mock.calls.at(-1)[0]).toBe('/api/sessions/session-b/state');
});
it('an old unmounted request cannot overwrite a new session', async () => {
  const oldRequest = deferred(); fetch.mockReturnValueOnce(oldRequest.promise);
  const old = renderHook(useWarehouseSession); old.unmount();
  fetch.mockResolvedValueOnce(response(envelope(initial, 'session-b')));
  const fresh = renderHook(useWarehouseSession);
  await waitFor(() => expect(fresh.result.current.session?.session_id).toBe('session-b'));
  await act(async () => { oldRequest.resolve(response(envelope(ready, 'session-a'))); });
  expect(fresh.result.current.session.session_id).toBe('session-b');
});
it('replacement notice persists on read but resets with session', async () => {
  const { result } = renderHook(useWarehouseSession); await waitFor(() => expect(result.current.pendingAction).toBe(null));
  fetch.mockResolvedValueOnce(response(envelope(replacement, 'session-a', 'ready')));
  await act(async () => { await result.current.run('execute'); });
  expect(result.current.replacementNotice).toBe(2);
  fetch.mockResolvedValueOnce(response(envelope(replacement)));
  await act(async () => { await result.current.run('refresh'); });
  expect(result.current.replacementNotice).toBe(2);
  fetch.mockResolvedValueOnce(response(envelope(initial, 'session-b')));
  await act(async () => { await result.current.run('reset'); });
  expect(result.current.replacementNotice).toBe(null); expect(result.current.lastCommand).toBe(null);
});
