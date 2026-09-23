import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useRouteReplay } from './useRouteReplay';

function Probe({ legs, onReady }) {
  const replay = useRouteReplay();
  onReady(replay);
  return <div>
    <span data-testid="playing">{String(replay.playing)}</span>
    <span data-testid="positions">{JSON.stringify(replay.positions)}</span>
    <button onClick={() => replay.play(legs)}>play</button>
    <button onClick={replay.skip}>skip</button>
  </div>;
}

// Advances fake timers, then lets the resulting promise continuations
// (the awaited setTimeout inside useRouteReplay's play loop) actually run.
async function advance(ms) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const legs = [{ robotId: 'robot-1', route: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 2, y: 0 }] }];

it('never starts when the viewer prefers reduced motion (the default test stub)', async () => {
  let replay;
  render(<Probe legs={legs} onReady={r => { replay = r; }} />);
  await act(async () => screen.getByText('play').click());
  expect(replay.playing).toBe(false);
  expect(replay.positions).toBeNull();
});

it('steps a robot through its route and finishes when motion is not reduced', async () => {
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  let replay;
  render(<Probe legs={legs} onReady={r => { replay = r; }} />);
  await act(async () => screen.getByText('play').click());
  expect(replay.playing).toBe(true);
  expect(JSON.parse(screen.getByTestId('positions').textContent)).toEqual({ 'robot-1': { x: 0, y: 0 } });
  await advance(220);
  expect(JSON.parse(screen.getByTestId('positions').textContent)).toEqual({ 'robot-1': { x: 1, y: 0 } });
  await advance(220);
  expect(JSON.parse(screen.getByTestId('positions').textContent)).toEqual({ 'robot-1': { x: 2, y: 0 } });
  await advance(220);
  expect(replay.playing).toBe(false);
  expect(replay.positions).toBeNull();
});

it('skip cancels mid-playback and clears the overlay', async () => {
  window.matchMedia = vi.fn().mockImplementation(query => ({ matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  let replay;
  render(<Probe legs={legs} onReady={r => { replay = r; }} />);
  await act(async () => screen.getByText('play').click());
  await advance(220);
  await act(async () => screen.getByText('skip').click());
  expect(replay.playing).toBe(false);
  expect(replay.positions).toBeNull();
  // Advancing further must not resurrect the cancelled playback.
  await advance(1000);
  expect(replay.playing).toBe(false);
});
