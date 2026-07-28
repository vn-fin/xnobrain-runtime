export type Brain4AllEdition = 'opensource' | 'pro' | 'cloud' | 'enterprise';
export type AuthMode = 'disabled' | 'optional' | 'required';
export type AuthProvider = 'local-profile' | 'gateway';

export type RuntimeConfig = {
  edition: Brain4AllEdition;
  api: {
    remoteBaseUrl: string;
  };
  auth: {
    mode: AuthMode;
    provider: AuthProvider;
    bootstrapPath: string;
    loginPath: string;
    logoutPath: string;
  };
  features: {
    account: boolean;
    example: boolean;
  };
};

const DEFAULT_CONFIG: RuntimeConfig = {
  edition: 'opensource',
  api: {
    remoteBaseUrl: '',
  },
  auth: {
    mode: 'optional',
    provider: 'local-profile',
    bootstrapPath: '/control/v1/bootstrap',
    loginPath: '/control/v1/auth/login',
    logoutPath: '/control/v1/auth/logout',
  },
  features: {
    account: true,
    example: false,
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
const authProviders = new Set<AuthProvider>(['local-profile', 'gateway']);

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
    },
    auth: {
      mode,
      provider,
      bootstrapPath: source?.auth?.bootstrapPath || DEFAULT_CONFIG.auth.bootstrapPath,
      loginPath: source?.auth?.loginPath || DEFAULT_CONFIG.auth.loginPath,
      logoutPath: source?.auth?.logoutPath || DEFAULT_CONFIG.auth.logoutPath,
    },
    features: {
      account: source?.features?.account ?? DEFAULT_CONFIG.features.account,
      example: source?.features?.example ?? DEFAULT_CONFIG.features.example,
    },
  };
}

export const brain4AllRuntime = runtimeConfig();
