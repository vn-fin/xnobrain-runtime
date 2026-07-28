import { useCallback, useEffect, useState } from 'react';
import { ExternalLink, RefreshCw, X } from 'lucide-react';
import { brain4AllRuntime } from '../runtime';

type MarketIndex = {
  code?: string;
  exchange?: string;
  price?: number;
  dayChange?: number;
  dayChangePercent?: number;
  advances?: number;
  noChanges?: number;
  declines?: number;
  tradingDate?: string;
};

type RemoteResponse = {
  data?: MarketIndex[];
  message?: string;
  status?: boolean;
};

function endpoint(baseUrl: string) {
  return new URL('/v2/indexoverview', `${baseUrl.replace(/\/+$/, '')}/`).toString();
}

export function ExampleView({ onClose }: { onClose: () => void }) {
  const baseUrl = brain4AllRuntime.api.remoteBaseUrl;
  const [rows, setRows] = useState<MarketIndex[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!baseUrl) {
      setStatus('error');
      setError('No remote API base URL is configured.');
      return;
    }
    setStatus('loading');
    setError('');
    try {
      const response = await fetch(endpoint(baseUrl), {
        credentials: 'omit',
        headers: { Accept: 'application/json' },
      });
      const body = await response.json() as RemoteResponse;
      if (!response.ok || !Array.isArray(body.data)) {
        throw new Error(body.message || `Remote API returned ${response.status}.`);
      }
      setRows(body.data);
      setStatus('ready');
    } catch (cause) {
      setStatus('error');
      setError(cause instanceof Error ? cause.message : 'Could not load the remote feature.');
    }
  }, [baseUrl]);

  useEffect(() => { void load(); }, [load]);

  return (
    <section className="example-view">
      <header className="view-header">
        <div>
          <h1>Remote feature example</h1>
          <p>Market overview loaded at runtime from the configured feature API.</p>
        </div>
        <button className="icon-button" onClick={onClose} title="Close"><X size={18} /></button>
      </header>

      <div className="example-toolbar">
        <span className="example-endpoint"><ExternalLink size={15} /> {baseUrl || 'Not configured'}</span>
        <button className="secondary-button" onClick={() => void load()} disabled={status === 'loading'}>
          <RefreshCw size={15} className={status === 'loading' ? 'spin' : ''} />
          Refresh
        </button>
      </div>

      {status === 'loading' && <div className="example-state">Loading remote data…</div>}
      {status === 'error' && <div className="example-state error" role="alert">{error}</div>}
      {status === 'ready' && (
        <div className="example-grid">
          {rows.map((row) => {
            const change = row.dayChangePercent ?? 0;
            return (
              <article className="example-card" key={`${row.exchange}-${row.code}`}>
                <div className="example-card-heading">
                  <div><strong>{row.code || 'Index'}</strong><span>{row.exchange || 'Market'}</span></div>
                  <span className={change > 0 ? 'market-up' : change < 0 ? 'market-down' : ''}>
                    {change > 0 ? '+' : ''}{change.toFixed(2)}%
                  </span>
                </div>
                <div className="example-price">{(row.price ?? 0).toLocaleString()}</div>
                <dl className="example-market-detail">
                  <div><dt>Change</dt><dd>{(row.dayChange ?? 0).toLocaleString()}</dd></div>
                  <div><dt>Advance / Flat / Decline</dt><dd>{row.advances ?? 0} / {row.noChanges ?? 0} / {row.declines ?? 0}</dd></div>
                  <div><dt>Trading date</dt><dd>{row.tradingDate || '—'}</dd></div>
                </dl>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
