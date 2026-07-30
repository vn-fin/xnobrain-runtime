export type Brain4AllEdition = 'opensource' | 'pro' | 'cloud' | 'enterprise';
export type AuthMode = 'disabled' | 'optional' | 'required';
export type AuthProvider = 'local-profile' | 'gateway' | 'xno-firebase';

export type RuntimeConfig = {
  edition: Brain4AllEdition;
  api: {
    remoteBaseUrl: string;
    authBaseUrl: string;
    controlBaseUrl: string;
  };
  auth: {
    mode: AuthMode;
    provider: AuthProvider;
    bootstrapPath: string;
    loginPath: string;
    logoutPath: string;
    firebaseApiKey: string;
    tokenPath: string;
    refreshPath: string;
    mePath: string;
  };
  features: {
    login: boolean;
  };
};

const DEFAULT_CONFIG: RuntimeConfig = {
  edition: 'opensource',
  api: {
    remoteBaseUrl: '',
    authBaseUrl: '',
    controlBaseUrl: '',
  },
  auth: {
    mode: 'optional',
    provider: 'local-profile',
    bootstrapPath: '/api/brain-control/v1/bootstrap',
    loginPath: '/api/brain-control/v1/auth/login',
    logoutPath: '/api/brain-control/v1/auth/logout',
    firebaseApiKey: '',
    tokenPath: '/api/brain-control/v1/auth/token',
    refreshPath: '/api/brain-control/v1/auth/refresh',
    mePath: '/api/brain-control/v1/auth/me',
  },
  features: {
    login: true,
  },
};

declare global {
  interface Window {
    __BRAIN4ALL_CONFIG__?: Partial<RuntimeConfig> & {
      api?: Partial<RuntimeConfig['api']>;
      auth?: Partial<RuntimeConfig['auth']>;
      features?: Partial<RuntimeConfig['features']>;
    };
  }
}

const editions = new Set<Brain4AllEdition>(['opensource', 'pro', 'cloud', 'enterprise']);
const authModes = new Set<AuthMode>(['disabled', 'optional', 'required']);
const authProviders = new Set<AuthProvider>(['local-profile', 'gateway', 'xno-firebase']);

export function runtimeConfig(source = window.__BRAIN4ALL_CONFIG__): RuntimeConfig {
  const edition = editions.has(source?.edition as Brain4AllEdition)
    ? source?.edition as Brain4AllEdition
    : DEFAULT_CONFIG.edition;
  const mode = authModes.has(source?.auth?.mode as AuthMode)
    ? source?.auth?.mode as AuthMode
    : DEFAULT_CONFIG.auth.mode;
  const provider = authProviders.has(source?.auth?.provider as AuthProvider)
    ? source?.auth?.provider as AuthProvider
    : DEFAULT_CONFIG.auth.provider;

  return {
    edition,
    api: {
      remoteBaseUrl: source?.api?.remoteBaseUrl?.trim() || DEFAULT_CONFIG.api.remoteBaseUrl,
      authBaseUrl: source?.api?.authBaseUrl?.trim() || DEFAULT_CONFIG.api.authBaseUrl,
      controlBaseUrl: source?.api?.controlBaseUrl?.trim() || DEFAULT_CONFIG.api.controlBaseUrl,
    },
    auth: {
      mode,
      provider,
      bootstrapPath: source?.auth?.bootstrapPath || DEFAULT_CONFIG.auth.bootstrapPath,
      loginPath: source?.auth?.loginPath || DEFAULT_CONFIG.auth.loginPath,
      logoutPath: source?.auth?.logoutPath || DEFAULT_CONFIG.auth.logoutPath,
      firebaseApiKey: source?.auth?.firebaseApiKey || DEFAULT_CONFIG.auth.firebaseApiKey,
      tokenPath: source?.auth?.tokenPath || DEFAULT_CONFIG.auth.tokenPath,
      refreshPath: source?.auth?.refreshPath || DEFAULT_CONFIG.auth.refreshPath,
      mePath: source?.auth?.mePath || DEFAULT_CONFIG.auth.mePath,
    },
    features: {
      login: source?.features?.login ?? DEFAULT_CONFIG.features.login,
    },
  };
}

export const brain4AllRuntime = runtimeConfig();
