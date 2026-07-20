import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import en from './locales/en.json';
import vi from './locales/vi.json';
import es from './locales/es.json';
import fr from './locales/fr.json';
import de from './locales/de.json';
import ja from './locales/ja.json';
import zh from './locales/zh.json';

export const SUPPORTED_LANGUAGES = ['en', 'vi', 'es', 'fr', 'de', 'ja', 'zh'] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

const resources = {
  en: { translation: en },
  vi: { translation: vi },
  es: { translation: es },
  fr: { translation: fr },
  de: { translation: de },
  ja: { translation: ja },
  zh: { translation: zh },
};

i18n
  // Auto-detect language from localStorage cache first, then the browser
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    supportedLngs: [...SUPPORTED_LANGUAGES],
    fallbackLng: 'en',
    // Map regional codes (e.g. en-US -> en, zh-CN -> zh) to our base languages
    load: 'languageOnly',
    nonExplicitSupportedLngs: true,
    interpolation: {
      escapeValue: false, // React already escapes
    },
    detection: {
      // Try the cached choice first, then the browser/OS languages
      order: ['localStorage', 'navigator', 'htmlTag'],
      lookupLocalStorage: 'app.lang',
      caches: ['localStorage'], // persist the selected language
    },
  });

export default i18n;
