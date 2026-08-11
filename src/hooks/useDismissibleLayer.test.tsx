import { useState } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDismissibleLayer } from './useDismissibleLayer';

function TestLayer({ onDismiss }: { onDismiss: () => void }) {
  const [open, setOpen] = useState(false);
  const root = useDismissibleLayer<HTMLDivElement>(open, () => {
    setOpen(false);
    onDismiss();
  });
  return (
    <>
      <div ref={root}>
        <button onClick={() => setOpen((value) => !value)}>Models</button>
        {open && <div role="menu">Model choices</div>}
      </div>
      <button>Outside</button>
    </>
  );
}

describe('useDismissibleLayer', () => {
  afterEach(cleanup);

  it('keeps inside interactions open and closes on an outside click', async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    render(<TestLayer onDismiss={onDismiss} />);

    await user.click(screen.getByRole('button', { name: 'Models' }));
    await user.click(screen.getByRole('menu'));
    expect(screen.getByRole('menu')).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('menu')).toBeNull();
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape', async () => {
    const user = userEvent.setup();
    render(<TestLayer onDismiss={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Models' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
