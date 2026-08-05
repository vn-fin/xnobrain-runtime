import { describe, expect, it } from 'vitest';
import { mapConnectionProvider } from './providers';

describe('mapConnectionProvider preset brands', () => {
  it.each([
    ['xai', 'xai'],
    ['openrouter', 'openrouter'],
    ['groq', 'groq'],
  ] as const)('maps %s to its connector brand icon', (id, brand) => {
    expect(mapConnectionProvider({ id, provider_type: id }).brand).toBe(brand);
  });
});
