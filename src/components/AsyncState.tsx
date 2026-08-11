import { RefreshCw } from 'lucide-react';
import type { AsyncStatus } from '../types';

export function AsyncState({
  status,
  error = '',
  onRetry,
  empty,
}: {
  status: AsyncStatus;
  error?: string;
  onRetry?: () => void;
  empty?: React.ReactNode;
}) {
  if (status === 'loading' || status === 'idle') {
    return <div className="async-state loading" role="status" aria-busy="true"><span className="async-spinner" /></div>;
  }
  if (status === 'error') {
    return (
      <div className="async-state error" role="alert">
        <p>{error || 'Something went wrong.'}</p>
        {onRetry && <button className="conn-btn ghost" onClick={onRetry}><RefreshCw size={14} />Retry</button>}
      </div>
    );
  }
  return empty ? <div className="async-state empty">{empty}</div> : null;
}
