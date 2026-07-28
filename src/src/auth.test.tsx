import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import './i18n';
import LoginScreen from './LoginScreen';
import { AuthProvider, useAuth } from './auth';
import { AccountView } from './components/AccountView';

function LocalAuthTrial() {
  const { user } = useAuth();
  return user ? <AccountView onClose={() => undefined} /> : <LoginScreen optional />;
}

describe('optional local profile', () => {
  afterEach(cleanup);

  beforeEach(() => {
    localStorage.clear();
  });

  it('creates a browser-local active user and exposes the Account view', async () => {
    render(<AuthProvider><LocalAuthTrial /></AuthProvider>);

    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Kim Example' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'kim@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save local profile' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument());
    expect(screen.getByText('Kim Example')).toBeInTheDocument();
    expect(screen.getByText('kim@example.com')).toBeInTheDocument();
    expect(screen.getByText('Browser-local profile; not an access-control boundary.')).toBeInTheDocument();
  });

  it('allows the optional form to close without creating a user', () => {
    render(<AuthProvider><LoginScreen optional /></AuthProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'Continue without signing in' }));
    expect(localStorage.getItem('brain4all.local-profile')).toBeNull();
  });
});
