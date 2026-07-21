import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import LoginScreen from './LoginScreen';
import { AuthProvider, useAuth } from './auth';
import { initTheme } from './theme';
import './i18n';
import './styles.css';

// Apply the persisted / system theme before first paint
initTheme();

function AuthGate() {
  const { user, deploymentMode, loading, loginOpen } = useAuth();
  if (loading) return <div className="app-loading" aria-busy="true" />;
  if (deploymentMode === 'cloud' && !user) return <LoginScreen />;
  return <><App />{deploymentMode === 'local' && loginOpen && !user && <LoginScreen optional />}</>;
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <AuthProvider><AuthGate /></AuthProvider>
  </React.StrictMode>,
);
