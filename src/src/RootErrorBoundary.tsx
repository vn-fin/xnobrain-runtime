import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode };
type State = { error: Error | null };

export default class RootErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Open Lumora UI render failed', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="async-state error" role="alert">
        <strong>Open Lumora could not render this page.</strong>
        <span>{this.state.error.message || 'An unexpected interface error occurred.'}</span>
        <button type="button" onClick={() => window.location.reload()}>Reload</button>
      </main>
    );
  }
}
