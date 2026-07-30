import { afterEach, describe, expect, it, vi } from 'vitest';
import { randomId } from './id';

describe('randomId', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses random bytes when randomUUID is unavailable on an HTTP origin', () => {
    vi.stubGlobal('crypto', {
      getRandomValues(bytes: Uint8Array) {
        bytes.fill(0x2a);
        return bytes;
      },
    });

    expect(randomId()).toBe('2a'.repeat(16));
  });
});
