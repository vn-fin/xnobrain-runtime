import { describe, expect, it } from 'vitest';
import { runtimeConfig } from './runtime';

describe('runtimeConfig', () => {
  it('defaults to optional standalone mode', () => {
    expect(runtimeConfig(undefined)).toMatchObject({
      edition: 'opensource',
      auth: { mode: 'optional', provider: 'local-profile' },
      api: { remoteBaseUrl: '' },
      features: { account: true, example: false },
    });
  });

  it('accepts the cloud gateway contract without changing the frontend build', () => {
    expect(runtimeConfig({
      edition: 'enterprise',
      api: { remoteBaseUrl: 'https://api.xno.vn' },
      auth: {
        mode: 'required',
        provider: 'gateway',
        bootstrapPath: '/control/v1/bootstrap',
      },
    })).toMatchObject({
      edition: 'enterprise',
      api: {
        remoteBaseUrl: 'https://api.xno.vn',
      },
      auth: {
        mode: 'required',
        provider: 'gateway',
        bootstrapPath: '/control/v1/bootstrap',
      },
    });
  });

  it('rejects unknown edition and auth values', () => {
    expect(runtimeConfig({
      edition: 'unknown' as never,
      auth: { mode: 'sometimes' as never, provider: 'unsafe' as never },
    })).toMatchObject({
      edition: 'opensource',
      auth: { mode: 'optional', provider: 'local-profile' },
    });
  });
});
