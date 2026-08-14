import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import './i18n';
import LoginScreen from './LoginScreen';

const signIn = vi.fn();

vi.mock('./auth', () => ({
  useAuth: () => ({ signIn }),
}));

afterEach(() => {
  cleanup();
  signIn.mockReset();
});

describe('LoginScreen', () => {
  it('maps an invalid credential code to user-facing guidance', async () => {
    signIn.mockRejectedValueOnce(new Error('INVALID_LOGIN_CREDENTIALS'));
    render(<LoginScreen />);

    const email = screen.getByRole('textbox', { name: 'Email' });
    const password = screen.getByLabelText('Password');
    expect(email).toHaveAttribute('autocomplete', 'username');
    expect(password).toHaveAttribute('autocomplete', 'current-password');
    fireEvent.change(email, { target: { value: 'nobody@example.invalid' } });
    fireEvent.change(password, { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Email or password is incorrect. Try again.',
    ));
    expect(screen.getByRole('alert')).not.toHaveTextContent('INVALID_LOGIN_CREDENTIALS');
  });

  it('uses a safe fallback instead of exposing an unknown server error', async () => {
    signIn.mockRejectedValueOnce(new Error('INTERNAL_AUTH_DATABASE_FAILURE'));
    render(<LoginScreen />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Email' }), { target: { value: 'kim@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not sign in.'));
    expect(screen.getByRole('alert')).not.toHaveTextContent('INTERNAL_AUTH_DATABASE_FAILURE');
  });
});
