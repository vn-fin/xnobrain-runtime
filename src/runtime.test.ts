import { describe, expect, it } from 'vitest';
import { runtimeConfig } from './runtime';

describe('runtimeConfig', () => {
  it('defaults to required authenticated XNOBrain mode', () => {
    expect(runtimeConfig(undefined, {})).toMatchObject({
      edition: 'enterprise',
      auth: { mode: 'required', provider: 'xno-firebase' },
      api: { remoteBaseUrl: '', authBaseUrl: '', controlBaseUrl: '' },
      features: { login: true },
    });
  });

  it('accepts the cloud gateway contract without changing the frontend build', () => {
    expect(runtimeConfig({
      edition: 'enterprise',
      api: {
        remoteBaseUrl: 'https://runtime.xno.vn',
        authBaseUrl: 'https://auth.xno.vn',
        controlBaseUrl: 'https://control.xno.vn',
      },
      auth: {
        mode: 'required',
        provider: 'gateway',
        bootstrapPath: '/xnobrain/api/control/v1/account/bootstrap',
      },
    }, {})).toMatchObject({
      edition: 'enterprise',
      api: {
        remoteBaseUrl: 'https://runtime.xno.vn',
        authBaseUrl: 'https://auth.xno.vn',
        controlBaseUrl: 'https://control.xno.vn',
      },
      auth: {
        mode: 'required',
        provider: 'gateway',
        bootstrapPath: '/xnobrain/api/control/v1/account/bootstrap',
      },
    });
  });

  it('uses compile-time values for a managed cloud image', () => {
    expect(runtimeConfig(undefined, {
      VITE_API_BASE_URL: 'https://public.dev.xno.vn',
      VITE_AUTH_API_URL: 'https://api.dev.xnoquant.io',
      VITE_CONTROL_API_BASE_URL: 'https://public.dev.xno.vn',
      VITE_APP_EDITION: 'cloud',
      VITE_AUTH_MODE: 'required',
      VITE_AUTH_PROVIDER: 'xno-firebase',
      VITE_FIREBASE_API_KEY: 'firebase-public-key',
    })).toMatchObject({
      edition: 'cloud',
      api: {
        remoteBaseUrl: 'https://public.dev.xno.vn',
        authBaseUrl: 'https://api.dev.xnoquant.io',
        controlBaseUrl: 'https://public.dev.xno.vn',
      },
      auth: {
        mode: 'required',
        provider: 'xno-firebase',
        firebaseApiKey: 'firebase-public-key',
      },
    });
  });

  it('rejects unknown edition and auth values', () => {
    expect(runtimeConfig({
      edition: 'unknown' as never,
      auth: { mode: 'sometimes' as never, provider: 'unsafe' as never },
    }, {})).toMatchObject({
      edition: 'enterprise',
      auth: { mode: 'required', provider: 'xno-firebase' },
    });
  });
});
