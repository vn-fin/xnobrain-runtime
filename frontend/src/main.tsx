import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { initTheme } from './theme';
import './i18n';
import './styles.css';

// Apply the persisted / system theme before first paint
initTheme();

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
