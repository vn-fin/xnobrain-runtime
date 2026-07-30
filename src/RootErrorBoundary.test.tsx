import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RootErrorBoundary from './RootErrorBoundary';

function BrokenView(): never {
  throw new Error('render exploded');
}

afterEach(() => vi.restoreAllMocks());

describe('RootErrorBoundary', () => {
  it('shows a recoverable error instead of leaving a blank root', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);

    render(<RootErrorBoundary><BrokenView /></RootErrorBoundary>);

    expect(screen.getByRole('alert')).toHaveTextContent('Brain4All could not render this page.');
    expect(screen.getByText('render exploded')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });
});
