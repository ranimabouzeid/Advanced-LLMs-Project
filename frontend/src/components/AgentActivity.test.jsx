import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import AgentActivity from './AgentActivity';
it('preserves backend order including repeated Route and Safety records', () => {
  const records=['order','fleet','route','safety','route','safety','execution'].map((node,i)=>({node,status:i===3?'rejected':'completed',message:`record ${i}`}));
  render(<AgentActivity activity={records} />);
  const items=screen.getAllByRole('listitem'); expect(items).toHaveLength(7);
  records.forEach((r,i)=>{expect(items[i]).toHaveTextContent(r.message);expect(items[i]).toHaveTextContent(r.status);});
});
it('renders no fabricated activity for an empty history',()=>{render(<AgentActivity activity={[]} />);expect(screen.queryByRole('listitem')).not.toBeInTheDocument();});
