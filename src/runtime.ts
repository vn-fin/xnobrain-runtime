export type XNOBrainEdition = 'opensource' | 'pro' | 'cloud' | 'enterprise';
export type AuthMode = 'required';
export type AuthProvider = 'gateway' | 'xno-firebase';

export const CONTROL_API_PREFIX = '/xnobrain/api/control/v1';
export const AUTH_API_PREFIX = '/auth/v1';

export type RuntimeConfig = {
  edition: XNOBrainEdition;
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
    controlMePath: string;
  };
  features: {
    login: boolean;
  };
};

const DEFAULT_CONFIG: RuntimeConfig = {
  edition: 'enterprise',
  api: {
    remoteBaseUrl: '',
    authBaseUrl: '',
    controlBaseUrl: '',
  },
  auth: {
    mode: 'required',
    provider: 'xno-firebase',
    bootstrapPath: `${CONTROL_API_PREFIX}/account/bootstrap`,
    loginPath: `${CONTROL_API_PREFIX}/auth/login`,
    logoutPath: `${CONTROL_API_PREFIX}/auth/logout`,
    firebaseApiKey: '',
    tokenPath: `${AUTH_API_PREFIX}/auth/token`,
    refreshPath: `${AUTH_API_PREFIX}/auth/refresh`,
    mePath: `${AUTH_API_PREFIX}/me`,
    controlMePath: `${CONTROL_API_PREFIX}/auth/me`,
  },
  features: {
    login: true,
  },
};

type RuntimeConfigSource = Partial<RuntimeConfig> & {
  api?: Partial<RuntimeConfig['api']>;
  auth?: Partial<RuntimeConfig['auth']>;
  features?: Partial<RuntimeConfig['features']>;
};

const editions = new Set<XNOBrainEdition>(['opensource', 'pro', 'cloud', 'enterprise']);
const authModes = new Set<AuthMode>(['required']);
const authProviders = new Set<AuthProvider>(['gateway', 'xno-firebase']);

export function runtimeConfig(
  source?: RuntimeConfigSource,
  environment: Partial<ImportMetaEnv> = import.meta.env,
): RuntimeConfig {
  const buildEdition = environment.VITE_APP_EDITION?.trim();
  const buildAuthMode = environment.VITE_AUTH_MODE?.trim();
  const buildAuthProvider = environment.VITE_AUTH_PROVIDER?.trim();
  const requestedEdition = buildEdition || source?.edition;
  const requestedAuthMode = buildAuthMode || source?.auth?.mode;
  const requestedAuthProvider = buildAuthProvider || source?.auth?.provider;
  const edition = editions.has(requestedEdition as XNOBrainEdition)
    ? requestedEdition as XNOBrainEdition
    : DEFAULT_CONFIG.edition;
  const mode = authModes.has(requestedAuthMode as AuthMode)
    ? requestedAuthMode as AuthMode
    : DEFAULT_CONFIG.auth.mode;
  const provider = authProviders.has(requestedAuthProvider as AuthProvider)
    ? requestedAuthProvider as AuthProvider
    : DEFAULT_CONFIG.auth.provider;

  return {
    edition,
    api: {
      remoteBaseUrl: environment.VITE_API_BASE_URL?.trim()
        || source?.api?.remoteBaseUrl?.trim()
        || DEFAULT_CONFIG.api.remoteBaseUrl,
      authBaseUrl: environment.VITE_AUTH_API_URL?.trim()
        || source?.api?.authBaseUrl?.trim()
        || DEFAULT_CONFIG.api.authBaseUrl,
      controlBaseUrl: environment.VITE_CONTROL_API_BASE_URL?.trim()
        || source?.api?.controlBaseUrl?.trim()
        || DEFAULT_CONFIG.api.controlBaseUrl,
    },
    auth: {
      mode,
      provider,
      bootstrapPath: source?.auth?.bootstrapPath || DEFAULT_CONFIG.auth.bootstrapPath,
      loginPath: source?.auth?.loginPath || DEFAULT_CONFIG.auth.loginPath,
      logoutPath: source?.auth?.logoutPath || DEFAULT_CONFIG.auth.logoutPath,
      firebaseApiKey: environment.VITE_FIREBASE_API_KEY?.trim()
        || source?.auth?.firebaseApiKey
        || DEFAULT_CONFIG.auth.firebaseApiKey,
      tokenPath: source?.auth?.tokenPath || DEFAULT_CONFIG.auth.tokenPath,
      refreshPath: source?.auth?.refreshPath || DEFAULT_CONFIG.auth.refreshPath,
      mePath: source?.auth?.mePath || DEFAULT_CONFIG.auth.mePath,
      controlMePath: source?.auth?.controlMePath || DEFAULT_CONFIG.auth.controlMePath,
    },
    features: {
      login: true,
    },
  };
}

export const xnobrainRuntime = runtimeConfig();
