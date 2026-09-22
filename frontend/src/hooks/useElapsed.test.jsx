import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useElapsed } from './useElapsed';

function Probe({ ids }) {
  const elapsedSeconds = useElapsed(ids);
  return <ul>{ids.map(id => <li key={id}>{id}: {elapsedSeconds(id)}s</li>)}</ul>;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('starts a new id at 0 seconds and ticks upward once per second', () => {
  render(<Probe ids={['a']} />);
  expect(screen.getByText('a: 0s')).toBeVisible();
  act(() => vi.advanceTimersByTime(3000));
  expect(screen.getByText('a: 3s')).toBeVisible();
});

it('keeps each id counting from when it was first seen, not from a shared start', () => {
  const { rerender } = render(<Probe ids={['a']} />);
  act(() => vi.advanceTimersByTime(5000));
  rerender(<Probe ids={['a', 'b']} />);
  expect(screen.getByText('a: 5s')).toBeVisible();
  expect(screen.getByText('b: 0s')).toBeVisible();
  act(() => vi.advanceTimersByTime(2000));
  expect(screen.getByText('a: 7s')).toBeVisible();
  expect(screen.getByText('b: 2s')).toBeVisible();
});
