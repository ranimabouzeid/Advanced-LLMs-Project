import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import AgentFlow from './AgentFlow';
import { ready, replacement, initial } from '../test/fixtures';

it('shows all four agents completed with no replan badge on a clean plan', () => {
  render(<AgentFlow state={ready} busy={false} />);
  const region = screen.getByRole('region', { name: 'Agent activity' });
  const cards = within(region).getAllByRole('article');
  expect(cards).toHaveLength(4);
  cards.forEach(card => expect(within(card).getByText('completed')).toBeVisible());
  expect(screen.queryByText(/Rejected, Route retried/)).not.toBeInTheDocument();
});

it('shows a not-run state before any Plan has executed', () => {
  render(<AgentFlow state={initial} busy={false} />);
  const cards = within(screen.getByRole('region', { name: 'Agent activity' })).getAllByRole('article');
  cards.forEach(card => expect(within(card).getByText('not run')).toBeVisible());
});

it('shows a working placeholder on every card while a command is in flight', () => {
  render(<AgentFlow state={ready} busy />);
  const cards = within(screen.getByRole('region', { name: 'Agent activity' })).getAllByRole('article');
  cards.forEach(card => expect(within(card).getByText('working')).toBeVisible());
});

it('flags the safety card with a replan badge after a reject-then-retry cycle', () => {
  render(<AgentFlow state={replacement} busy={false} />);
  const region = screen.getByRole('region', { name: 'Agent activity' });
  expect(within(region).getByText(/Rejected, Route retried/)).toBeVisible();
});

it('shows the replan counter in the header once at least one replan happened', () => {
  render(<AgentFlow state={{ ...replacement, replan_count: 1 }} busy={false} />);
  expect(screen.getByText('Replan 1/3')).toBeVisible();
});
