import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import StatePanel from './StatePanel';
import { ready } from '../test/fixtures';

it('shows model approval and its explanation', () => {
  render(<StatePanel state={ready} />);
  expect(screen.getByText('Approved')).toBeVisible();
  expect(screen.getByText(ready.safety.explanation)).toBeVisible();
});

it('shows model rejection and actionable conflicts', () => {
  render(<StatePanel state={{ ...ready, safety: {
    approved: false, conflicts: ['Avoid robot-2 at (1, 0)'], explanation: 'Revise the pickup leg',
  } }} />);
  expect(screen.getByText('Rejected')).toBeVisible();
  expect(screen.getByText('Avoid robot-2 at (1, 0)')).toBeVisible();
  expect(screen.getByText('Revise the pickup leg')).toBeVisible();
});
