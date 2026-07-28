import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import LoginScreen from './LoginScreen';
import RootErrorBoundary from './RootErrorBoundary';
import { AuthProvider, useAuth } from './auth';
import { initTheme } from './theme';
import './i18n';
import './styles.css';

// Apply the persisted / system theme before first paint
initTheme();

function AuthGate() {
  const { config, user, loading, loginOpen, openLogin } = useAuth();
  if (loading) return <div className="app-loading" aria-busy="true"><span className="login-logo">B</span><span>Loading Brain4All…</span></div>;
  if (config.auth.mode === 'required' && !user) return <LoginScreen />;
  return (
    <>
      <App />
      {config.auth.mode === 'optional' && !user && !loginOpen && (
        <button className="optional-login-launcher" onClick={openLogin}>Sign in</button>
      )}
      {config.auth.mode === 'optional' && loginOpen && !user && <LoginScreen optional />}
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <AuthProvider><AuthGate /></AuthProvider>
    </RootErrorBoundary>
  </React.StrictMode>,
);
