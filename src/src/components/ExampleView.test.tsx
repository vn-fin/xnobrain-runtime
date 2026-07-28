import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ExampleView } from './ExampleView';

vi.mock('../runtime', () => ({
  brain4AllRuntime: {
    api: { remoteBaseUrl: 'https://api.xno.vn' },
  },
}));

describe('ExampleView', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads the configured remote feature without sending credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: true,
        data: [{
          code: 'VNIndex',
          exchange: 'HOSE',
          price: 1680.62,
          dayChange: 11.61,
          dayChangePercent: 0.7,
          advances: 195,
          noChanges: 53,
          declines: 128,
          tradingDate: '28/07/2026',
        }],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ExampleView onClose={() => undefined} />);

    expect(await screen.findByText('VNIndex')).toBeInTheDocument();
    expect(screen.getByText('+0.70%')).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      'https://api.xno.vn/v2/indexoverview',
      expect.objectContaining({ credentials: 'omit' }),
    ));
  });
});
