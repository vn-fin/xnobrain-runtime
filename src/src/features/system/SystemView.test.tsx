import { StrictMode } from 'react';
import { render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SystemView } from './SystemView';

const mocks = vi.hoisted(() => ({
  deployment: vi.fn(async () => ({ mode: 'local', runtime_transport: 'in-process' })),
}));

vi.mock('./api', () => ({
  systemApi: {
    deployment: mocks.deployment,
  },
}));
vi.mock('../../components/ConnectionsView', () => ({ ConnectionsView: () => null }));
vi.mock('../../components/SandboxView', () => ({ SandboxView: () => null }));
vi.mock('./BlendsSection', () => ({ BlendsSection: () => null }));

const noop = () => undefined;
const accounts = {
  connectionsByProvider: {},
  usageByConnection: {},
  rowPendingId: null,
  onLoadConnections: noop,
  onAddAccount: noop,
  onSetAccountActive: noop,
  onReorderAccount: noop,
  onTestAccount: noop,
  onRemoveAccount: noop,
  onLoadAccountUsage: noop,
};
const sandbox = {
  data: null,
  provisioned: false,
  status: 'ready' as const,
  error: '',
  setupRunning: false,
  setupProgress: 0,
  onCreate: noop,
  onRefresh: noop,
};

describe('SystemView deployment summary', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads only on Profiles and only once under StrictMode', async () => {
    const props = {
      agents: [],
      providers: [],
      keyProviderId: '',
      providerPendingId: null,
      onSelectKeyProvider: noop,
      onConnect: noop,
      onDisconnect: noop,
      onTestProvider: vi.fn(),
      onSaveKey: noop,
      accounts,
      sandbox,
      onImported: async () => undefined,
      onClose: noop,
      onSectionChange: noop,
    };
    const { rerender } = render(
      <StrictMode><SystemView {...props} section="blends" /></StrictMode>,
    );

    expect(mocks.deployment).not.toHaveBeenCalled();

    rerender(<StrictMode><SystemView {...props} section="profiles" /></StrictMode>);
    await waitFor(() => expect(mocks.deployment).toHaveBeenCalledTimes(1));

    rerender(<StrictMode><SystemView {...props} section="blends" /></StrictMode>);
    rerender(<StrictMode><SystemView {...props} section="profiles" /></StrictMode>);
    expect(mocks.deployment).toHaveBeenCalledTimes(1);
  });
});
