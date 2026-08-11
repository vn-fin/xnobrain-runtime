import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import LoginScreen from './LoginScreen';
import RootErrorBoundary from './RootErrorBoundary';
import { AuthProvider, useAuth } from './auth';
import { productDescription, productName } from './config/product';
import { initTheme } from './theme';
import './i18n';
import './styles.css';

// Apply the persisted / system theme before first paint
initTheme();
document.title = productName;
const descriptionMeta = document.querySelector<HTMLMetaElement>('meta[name="description"]')
  ?? document.createElement('meta');
descriptionMeta.name = 'description';
descriptionMeta.content = productDescription;
if (!descriptionMeta.isConnected) document.head.append(descriptionMeta);

function AuthGate() {
  const { loading, sessionActive } = useAuth();
  if (loading) return <div className="app-loading" aria-busy="true"><span className="login-logo">{productName.charAt(0)}</span><span>Loading {productName}…</span></div>;
  if (!sessionActive) return <LoginScreen />;
  return <App />;
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <AuthProvider><AuthGate /></AuthProvider>
    </RootErrorBoundary>
  </React.StrictMode>,
);
