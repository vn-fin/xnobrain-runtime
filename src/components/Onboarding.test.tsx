import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import '../i18n';
import { Onboarding } from './Onboarding';

afterEach(cleanup);

function renderOnboarding(options?: { sandboxProvisioned?: boolean }) {
  return render(
    <Onboarding
      sandboxStatus="ready"
      sandboxProvisioned={options?.sandboxProvisioned ?? true}
      setupRunning={false}
      setupProgress={0}
      setupMessage=""
      onCreateSandbox={vi.fn()}
      providers={[]}
      providerPendingId={null}
      onStartConnect={vi.fn(async () => null)}
      onCheckConnect={vi.fn(async () => false)}
      onSubmitConnectText={vi.fn(async () => false)}
      onTestProvider={vi.fn()}
      onSaveKey={vi.fn()}
    />,
  );
}

describe('Onboarding navigation', () => {
  it('allows a ready local runtime to advance to provider setup', () => {
    renderOnboarding({ sandboxProvisioned: true });

    fireEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(screen.getByText('Step 2 of 2 · Connect a provider')).toBeInTheDocument();
  });
});
