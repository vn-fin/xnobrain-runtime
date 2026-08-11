/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_CONTROL_API_BASE_URL?: string;
  readonly VITE_AUTH_API_URL?: string;
  readonly VITE_APP_EDITION?: string;
  readonly VITE_AUTH_MODE?: string;
  readonly VITE_AUTH_PROVIDER?: string;
  readonly VITE_FIREBASE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare const __APP_NAME__: string;
declare const __APP_DESCRIPTION__: string;
