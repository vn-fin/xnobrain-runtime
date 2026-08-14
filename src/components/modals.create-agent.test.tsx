import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '../i18n';
import { CreateAgentModal } from './modals';

describe('CreateAgentModal', () => {
  it('explains the required display name without exposing an API route', () => {
    const onCreate = vi.fn();
    render(<CreateAgentModal onCreate={onCreate} onImported={vi.fn()} onClose={vi.fn()} />);

    expect(screen.queryByText(/Maps to POST/i)).not.toBeInTheDocument();
    const name = screen.getByRole('textbox', { name: /Display name/ });
    expect(name).toBeRequired();
    expect(name).toHaveAttribute('aria-required', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Display name is required.');
    expect(name).toHaveAttribute('aria-invalid', 'true');
    expect(onCreate).not.toHaveBeenCalled();

    fireEvent.change(name, { target: { value: 'Research agent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
    expect(onCreate).toHaveBeenCalledWith('Research agent', '');
  });
});
