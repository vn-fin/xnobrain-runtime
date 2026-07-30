import { useEffect, useState } from 'react';

export type ThemePreference = 'light' | 'dark' | 'auto';
export type ResolvedTheme = 'light' | 'dark';

const STORAGE_KEY = 'app.theme';

export function getStoredTheme(): ThemePreference {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === 'light' || stored === 'dark' || stored === 'auto' ? stored : 'auto';
}

function systemTheme(): ResolvedTheme {
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function resolveTheme(pref: ThemePreference): ResolvedTheme {
  return pref === 'auto' ? systemTheme() : pref;
}

function applyTheme(resolved: ResolvedTheme) {
  document.documentElement.setAttribute('data-theme', resolved);
}

// Apply the persisted/auto theme as early as possible to avoid a flash
export function initTheme() {
  applyTheme(resolveTheme(getStoredTheme()));
}

// React hook to read and change the theme preference
export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(getStoredTheme);

  useEffect(() => {
    applyTheme(resolveTheme(preference));
    localStorage.setItem(STORAGE_KEY, preference);

    if (preference !== 'auto') return;
    // Follow the system when in auto mode
    const mql = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => applyTheme(resolveTheme('auto'));
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [preference]);

  return { preference, setPreference, resolved: resolveTheme(preference) };
}
